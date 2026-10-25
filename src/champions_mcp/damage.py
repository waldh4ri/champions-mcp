"""Champions damage calculator: a bridge to @smogon/calc in Docker.

Unchanged calc contract from v1 (gen-0 native Champions mode, Stat Points passed
straight through as the per-stat ``evs`` value). The only difference is species
resolution: v2 reads the Showdown/calc species name straight from the local DB
(``pokemon.showdown_name``) instead of round-tripping through PokeAPI. Mega
Evolution is auto-applied by the bridge from the held Mega Stone.
"""

from __future__ import annotations

import asyncio
import json

from .champions_stats import (
    ALL_STATS,
    normalize_nature,
    normalize_spread,
    validate_spread,
)
from .config import Settings
from .db import Db
from .names import try_resolve
from .normalize import slugify

# Champions stat name -> @smogon/calc StatsTable key
_STAT_KEY = {
    "hp": "hp", "attack": "atk", "defense": "def",
    "special-attack": "spa", "special-defense": "spd", "speed": "spe",
}
_WEATHER = {
    "sun": "Sun", "harsh sunshine": "Harsh Sunshine", "rain": "Rain",
    "heavy rain": "Heavy Rain", "sand": "Sand", "sandstorm": "Sand",
    "snow": "Snow", "hail": "Snow",
}
_TERRAIN = {
    "electric": "Electric", "grassy": "Grassy", "grass": "Grassy",
    "psychic": "Psychic", "misty": "Misty",
}

CHAMPIONS_GEN = 0  # @smogon/calc native Pokémon Champions generation

# Roster names the calc's Champions dataset spells differently. Its gen-0 table
# has only the Eternal Flower Floette and the gendered Meowstic Megas, so the
# plain names below would come back from the sidecar as an unknown species.
_CALC_BASE_NAME = {"Floette": "Floette-Eternal", "Meowstic (Male)": "Meowstic",
                   "Aegislash": "Aegislash-Shield"}
_CALC_MEGA_NAME = {"Mega Meowstic (Male)": "Meowstic-M-Mega",
                   "Mega Meowstic (Female)": "Meowstic-F-Mega",
                   "Mega Floette": "Floette-Mega"}


class DamageCalculator:
    """Builds an @smogon/calc request from Champions-shaped input and runs it."""

    def __init__(self, settings: Settings, db: Db) -> None:
        """Store the settings/DB handles used to reach and resolve species for the calc.

        Args:
            settings: Runtime settings (calc sidecar URL, docker fallback, timeout).
            db: Open database handle, used to resolve species to their
                Showdown/calc name.
        """
        self._s = settings
        self._db = db

    def _species_name(self, species: str, item: str | None = None) -> str:
        """Resolve a species to the name @smogon/calc expects, falling back to
        the raw input if it can't be resolved locally.

        Megas are resolved here to their exact calc forme ("Lucario-Mega-Z"),
        from either a Mega name or the held stone. The sidecar's own lookup
        (``Pokemon.getForme``) does not know the Z-Mega stones and silently
        falls back to the plain Mega — Lucarionite Z calced as Mega Lucario
        (Speed 132, no Aura Guard) instead of Mega Lucario Z — and a Mega
        *name* without its stone resolved to the base form."""
        from .profiles import _entities, resolve_entity

        ent = _entities(self._db).get(resolve_entity(self._db, species) or "")
        slug = ent["species_slug"] if ent else try_resolve(self._db, species)
        if not slug:
            return species  # let the calc try the raw name
        row = self._db.get_pokemon_row(slug)
        if not row:
            return species
        mega = None
        if ent and ent["is_mega"]:
            mega = self._db.get_mega_form(ent["name"])
        elif item:
            for mf in self._db.list_mega_forms():
                if mf["species_slug"] == slug and slugify(mf["mega_stone"] or "") == slugify(item):
                    mega = mf
                    break
        if mega:
            if mega["form_name"] in _CALC_MEGA_NAME:
                return _CALC_MEGA_NAME[mega["form_name"]]
            kind = (mega.get("mega_kind") or "Mega").split()
            suffix = f"-{kind[1]}" if len(kind) > 1 else ""
            return f"{row['showdown_name']}-Mega{suffix}"
        return _CALC_BASE_NAME.get(row["showdown_name"], row["showdown_name"])

    def _side_options(
        self, nature, stat_points, boosts, ability, item, status,
    ) -> tuple[dict, list[str]]:
        """Build one side's @smogon/calc ``options`` dict from Champions-shaped input.

        Returns:
            A tuple of (options dict, list of human-readable validation warnings).
        """
        warnings: list[str] = []
        sp = normalize_spread(stat_points or {})
        warnings += validate_spread(sp).violations
        evs = {_STAT_KEY[s]: int(sp.get(s, 0)) for s in ALL_STATS if sp.get(s)}
        opts: dict = {}
        if evs:
            opts["evs"] = evs
        if nature:
            opts["nature"] = normalize_nature(nature).capitalize()
        if boosts:
            opts["boosts"] = {_STAT_KEY.get(k, k): int(v) for k, v in boosts.items()}
        if ability:
            opts["ability"] = ability
        if item:
            opts["item"] = item
        if status:
            opts["status"] = status.strip().lower()
        return opts, warnings

    async def calculate(self, req: dict) -> dict:
        """Run a full damage calculation for one attacker/move/defender request.

        Args:
            req: Raw request dict, as built by `server.calc_damage` (attacker/
                defender names, moves, natures, Stat Points, boosts, status,
                field conditions).

        Returns:
            The calc result dict (rolls, %HP, KO chance) with a `champions`
            metadata block attached, or `{"error": ...}` on failure.
        """
        atk_name = self._species_name(req["attacker"], req.get("attacker_item"))
        def_name = self._species_name(req["defender"], req.get("defender_item"))

        atk_opts, w1 = self._side_options(
            req.get("attacker_nature"), req.get("attacker_stat_points"),
            req.get("attacker_boosts"), req.get("attacker_ability"),
            req.get("attacker_item"), req.get("attacker_status"),
        )
        def_opts, w2 = self._side_options(
            req.get("defender_nature"), req.get("defender_stat_points"),
            req.get("defender_boosts"), req.get("defender_ability"),
            req.get("defender_item"), req.get("defender_status"),
        )
        warnings = [f"attacker: {m}" for m in w1] + [f"defender: {m}" for m in w2]

        move_opts: dict = {}
        if req.get("crit"):
            move_opts["isCrit"] = True
        if req.get("move_hits"):
            move_opts["hits"] = int(req["move_hits"])

        game_type = "Doubles" if str(req.get("game_type", "doubles")).lower().startswith("d") else "Singles"
        field: dict = {"gameType": game_type}
        if req.get("weather"):
            w = _WEATHER.get(str(req["weather"]).strip().lower())
            field["weather"] = w if w else field.get("weather")
            if not w:
                warnings.append(f"unknown weather {req['weather']!r} ignored")
        if req.get("terrain"):
            t = _TERRAIN.get(str(req["terrain"]).strip().lower())
            if t:
                field["terrain"] = t
            else:
                warnings.append(f"unknown terrain {req['terrain']!r} ignored")
        atk_side: dict = {}
        if req.get("helping_hand"):
            atk_side["isHelpingHand"] = True
        if req.get("attacker_tailwind"):
            atk_side["isTailwind"] = True
        if atk_side:
            field["attackerSide"] = atk_side

        payload = {
            "gen": int(req.get("gen", CHAMPIONS_GEN)),
            "attacker": {"name": atk_name, "options": atk_opts},
            "defender": {"name": def_name, "options": def_opts},
            "move": {"name": req["move"], "options": move_opts},
            "field": field,
        }
        result = await self._run(payload)
        if not result.get("ok"):
            return {"error": result.get("error", "calc failed"), "payload": payload, "warnings": warnings}
        result.pop("ok", None)
        result["champions"] = {
            "mode": "native (@smogon/calc gen 0)", "level": 50, "ivs": 31,
            "sp_budget": 66, "sp_per_stat_cap": 32,
            "note": "Champions data sets + native Stat Point formula; SP passed through directly.",
        }
        if warnings:
            result["warnings"] = warnings
        result["game_type"] = game_type
        return result

    async def _run(self, payload: dict) -> dict:
        """Dispatch the calc payload to the HTTP sidecar if configured, else a
        one-shot `docker run` of the calc image."""
        if self._s.calc_http:
            return await self._run_http(payload)
        cmd = [self._s.calc_docker, "run", "--rm", "-i", "--network", "none", self._s.calc_image]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {"ok": False, "error": f"'{self._s.calc_docker}' not found. Docker is required for the damage calculator."}
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(payload).encode()), timeout=self._s.calc_timeout
            )
        except TimeoutError:
            proc.kill()
            return {"ok": False, "error": "damage calc timed out"}
        if proc.returncode != 0 and not out:
            msg = err.decode(errors="replace").strip()[:400]
            hint = (f" (build it: docker build -f docker/calc/Dockerfile -t {self._s.calc_image} .)"
                    if "No such image" in msg or "Unable to find image" in msg else "")
            return {"ok": False, "error": f"docker run failed: {msg}{hint}"}
        try:
            return json.loads(out.decode())
        except (ValueError, UnicodeDecodeError):
            return {"ok": False, "error": "could not parse calc output: " + out.decode(errors="replace")[:400]}

    async def _run_http(self, payload: dict) -> dict:
        """POST the calc payload to the configured HTTP sidecar (`calc_http`)."""
        import httpx

        assert self._s.calc_http
        url = self._s.calc_http.rstrip("/") + "/"
        try:
            async with httpx.AsyncClient(timeout=self._s.calc_timeout) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                return r.json()
        except httpx.TimeoutException:
            return {"ok": False, "error": "damage calc timed out (HTTP)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"calc HTTP request failed: {exc}"}
