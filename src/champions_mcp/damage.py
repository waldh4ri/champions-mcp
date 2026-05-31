"""Champions damage calculator: a bridge to @smogon/calc running in Docker.

@smogon/calc is JS; Node is not assumed on the host, so the calc is compiled
into the ``champions-calc`` Docker image (see docker/calc) and
invoked one-shot per call (`docker run --rm -i`).

@smogon/calc has a **native Champions mode (generation 0)**: it uses the
Champions data sets (Champions species/abilities/items + patched moves, e.g.
Freeze-Dry has no secondary) and `calcStatChampions`, which takes Stat Points
directly (`floor(nature * (base + SP + 20))`; HP `base + SP + 75`). Level 50
and 31 IVs are forced by the library for gen 0. So we pass the SP spread
straight through as the calc's per-stat `evs` value — no EV conversion.
"""

from __future__ import annotations

import asyncio
import json

from .champions_stats import ALL_STATS, normalize_nature, validate_spread
from .config import Settings
from .names import NameIndex
from .normalize import MEGA_SUFFIXES, REGION_MAP
from .pokeapi import PokeAPIClient

# Champions stat name -> @smogon/calc StatsTable key
_STAT_KEY = {
    "hp": "hp",
    "attack": "atk",
    "defense": "def",
    "special-attack": "spa",
    "special-defense": "spd",
    "speed": "spe",
}
# Derived from REGION_MAP: slug suffix -> @smogon/calc region display name
_REGION_SUFFIX = {f"-{v}": v.capitalize() for v in REGION_MAP.values()}
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


class DamageError(RuntimeError):
    pass


class DamageCalculator:
    def __init__(
        self, settings: Settings, names: NameIndex, pokeapi: PokeAPIClient
    ) -> None:
        self._s = settings
        self._names = names
        self._api = pokeapi

    async def _calc_species_name(self, species: str) -> str:
        """Resolve a (possibly localized) name to a @smogon/calc species name."""
        slug = await self._names.resolve(species)
        mon = await self._api.get_pokemon(slug)
        for suffix, region in _REGION_SUFFIX.items():
            if slug.endswith(suffix):
                return f"{mon.name}-{region}"
        if slug.endswith(MEGA_SUFFIXES):
            # calc auto-applies Mega/Primal from the held stone; use base.
            return mon.name
        return mon.name

    def _side_options(
        self,
        nature: str | None,
        stat_points: dict[str, int] | None,
        boosts: dict[str, int] | None,
        ability: str | None,
        item: str | None,
        status: str | None,
    ) -> tuple[dict, list[str]]:
        warnings: list[str] = []
        sp = stat_points or {}
        chk = validate_spread(sp)
        warnings += chk.violations
        # Champions gen-0: the calc's per-stat `evs` value IS the Stat Point
        # count (calcStatChampions uses it directly). No EV conversion, no
        # 252 cap. Lv 50 and 31 IVs are forced by the library for gen 0.
        evs = {
            _STAT_KEY[s]: int(sp.get(s, 0))
            for s in ALL_STATS
            if sp.get(s)
        }
        opts: dict = {}
        if evs:
            opts["evs"] = evs
        if nature:
            opts["nature"] = normalize_nature(nature).capitalize()
        if boosts:
            opts["boosts"] = {
                _STAT_KEY.get(k, k): int(v) for k, v in boosts.items()
            }
        if ability:
            opts["ability"] = ability
        if item:
            opts["item"] = item
        if status:
            opts["status"] = status.strip().lower()
        return opts, warnings

    async def calculate(self, req: dict) -> dict:
        """Run one damage calculation. `req` uses Champions-native fields."""
        warnings: list[str] = []
        try:
            atk_name = await self._calc_species_name(req["attacker"])
            def_name = await self._calc_species_name(req["defender"])
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Could not resolve a Pokémon: {exc}"}

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
        warnings += [f"attacker: {m}" for m in w1]
        warnings += [f"defender: {m}" for m in w2]

        move_opts: dict = {}
        if req.get("crit"):
            move_opts["isCrit"] = True
        if req.get("move_hits"):
            move_opts["hits"] = int(req["move_hits"])

        game_type = (
            "Doubles"
            if str(req.get("game_type", "doubles")).lower().startswith("d")
            else "Singles"
        )
        field: dict = {"gameType": game_type}
        if req.get("weather"):
            w = _WEATHER.get(str(req["weather"]).strip().lower())
            if w:
                field["weather"] = w
            else:
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
            return {
                "error": result.get("error", "calc failed"),
                "payload": payload,
                "warnings": warnings,
            }
        result.pop("ok", None)
        result["champions"] = {
            "mode": "native (@smogon/calc gen 0)",
            "level": 50,
            "ivs": 31,
            "sp_budget": 66,
            "sp_per_stat_cap": 32,
            "note": "Champions data sets (incl. patched moves) and the "
            "native Stat Point formula are used; SP passed through directly.",
        }
        if warnings:
            result["warnings"] = warnings
        result["game_type"] = game_type
        return result

    async def _run(self, payload: dict) -> dict:
        if self._s.calc_http:
            return await self._run_http(payload)
        cmd = [
            self._s.calc_docker, "run", "--rm", "-i",
            "--network", "none", self._s.calc_image,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "error": f"'{self._s.calc_docker}' not found. Docker is "
                "required for the damage calculator.",
            }
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(payload).encode()),
                timeout=self._s.calc_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "damage calc timed out"}
        if proc.returncode != 0 and not out:
            msg = err.decode(errors="replace").strip()[:400]
            hint = (
                f" (build it: docker build -f docker/calc/"
                f"Dockerfile -t {self._s.calc_image} .)"
                if "No such image" in msg or "Unable to find image" in msg
                else ""
            )
            return {"ok": False, "error": f"docker run failed: {msg}{hint}"}
        try:
            return json.loads(out.decode())
        except (ValueError, UnicodeDecodeError):
            return {
                "ok": False,
                "error": "could not parse calc output: "
                + out.decode(errors="replace")[:400],
            }

    async def _run_http(self, payload: dict) -> dict:
        import httpx

        assert self._s.calc_http  # guarded by caller
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
