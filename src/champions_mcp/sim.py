"""Battle simulation: a bridge to the champions-sim sidecar.

The sidecar (``docker/sim/``) runs Pokémon Showdown's simulator with its
``champions`` mod: the real Champions mechanics (Stat Points, Megas/Z-Megas,
20 PP cap, no Tera, the new abilities) and the Reg M-C VGC/BSS formats and
validator. Thousands of battles run in seconds across a worker pool.

This module turns champions-mcp's team dicts into Showdown pastes, builds the
meta opponent pool from the observed-team corpus, and calls the sidecar over
HTTP (``CHAMPIONS_MCP_SIM_HTTP``) or a one-shot ``docker run``.

Win rates are relative to the AI policy playing both sides (see
``docker/sim/policies.js``), not ladder predictions.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from . import log as logmod
from .config import Settings
from .db import Db
from .formats import game_type, resolve_format
from .names import try_resolve
from .pokepaste import team_to_showdown

_STAT_COLS = {
    "hp": "hp_points", "attack": "attack_points", "defense": "defense_points",
    "special-attack": "sp_atk_points", "special-defense": "sp_def_points",
    "speed": "speed_points",
}
INFO_MODES = ("open_sheets", "closed", "omniscient")
# "Nick (Species) (M) @ Item" / "Species @ Item" / "Species"
_SPECIES_LINE = re.compile(r"^(?:.*?\((?P<sp>[^()]{2,})\)|(?P<plain>[^@(]+?))\s*(?:\((?:M|F)\))?\s*(?:@.*)?$")

log = logmod.get("sim")

# When a species has no recorded spread at all: bulk + the better attack.
_FALLBACK_SPREAD = {"hp": 32, "attack": 32, "speed": 2}


class SimError(Exception):
    """A request the simulator can't run (bad team, nothing to fight...)."""

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = extra


class BattleSimulator:
    """Builds simulator requests from Champions-shaped input and runs them."""

    def __init__(self, settings: Settings, db: Db) -> None:
        self._s = settings
        self._db = db

    # ------------------------------------------------------------- teams

    def _usage_top(self, slug: str, fmt_key: str, category: str) -> dict[str, Any] | None:
        for key in (fmt_key, "singles" if fmt_key == "doubles" else "doubles"):
            row = self._db.query_one(
                "SELECT * FROM usage WHERE slug=? AND format=? AND category=? ORDER BY rank LIMIT 1",
                (slug, key, category))
            if row:
                return row
        return None

    def modal_spread(self, slug: str, fmt_key: str) -> dict[str, int]:
        """The species' most-used Stat Point spread (other format as fallback)."""
        row = self._usage_top(slug, fmt_key, "stat_points")
        if not row:
            return dict(_FALLBACK_SPREAD)
        return {stat: int(row[col] or 0) for stat, col in _STAT_COLS.items() if row[col]}

    def complete_member(self, m: dict[str, Any], fmt_key: str) -> tuple[dict[str, Any], list[str]]:
        """Fill a member's missing spread/nature/ability from usage data.

        Returns:
            The completed member and human-readable notes on what was filled.

        Raises:
            SimError: the species is unknown or it has no moves.
        """
        species = m.get("species") or ""
        slug = try_resolve(self._db, species)
        if not slug:
            raise SimError(f"unknown species {species!r}")
        if not m.get("moves"):
            raise SimError(f"{species} has no moves; every member needs its moveset to be simulated")
        out = dict(m)
        notes: list[str] = []
        if not any(out.get(k) for k in ("stat_points", "evs", "ev", "sp", "spread")):
            out["stat_points"] = self.modal_spread(slug, fmt_key)
            notes.append(f"{species}: no spread given, used its most common one {out['stat_points']}")
        if not out.get("nature"):
            row = self._usage_top(slug, fmt_key, "stat_alignment")
            out["nature"] = row["name"] if row else "Serious"
            notes.append(f"{species}: no nature given, used {out['nature']}")
        if not out.get("ability"):
            row = self._usage_top(slug, fmt_key, "ability")
            if row:
                out["ability"] = row["name"]
                notes.append(f"{species}: no ability given, used {out['ability']}")
        return out, notes

    def team_paste(self, team: list[dict[str, Any]] | str, fmt: str) -> tuple[str, list[str]]:
        """Showdown paste for a team given as member dicts or paste text."""
        if isinstance(team, str):
            text = team.strip()
            if not text:
                raise SimError("empty team")
            if "\n" not in text and "@" not in text:
                # one line: an opponent id from worst/best_matchups, or a mistake
                found = self.team_by_id(text, fmt)
                if found:
                    return found["paste"], [f"{text}: {found['label']} ({found['source']})"]
                raise SimError(
                    f"{text[:60]!r} is not a team: pass six member dicts, Showdown paste text, or an "
                    "opponent `id` from simulate_vs_meta's worst_matchups / best_matchups")
            return text, []
        if not team:
            raise SimError("empty team")
        fmt_key = game_type(fmt)
        members, notes = [], []
        for m in team:
            done, n = self.complete_member(m, fmt_key)
            members.append(done)
            notes += n
        return team_to_showdown(self._db, members, for_sim=True), notes

    def meta_opponents(self, fmt: str, limit: int = 30) -> tuple[list[dict[str, Any]], list[str]]:
        """Top observed teams for the format, as simulator opponents.

        Complete report teams (exact sets and spreads) come first; then the
        observed corpus by recency-weighted evidence, whose teams lack spreads
        (each member gets its species' modal spread). BSS ladder teams are
        real top-rated ladder teams (species + item) completed from BSS usage;
        the pool is topped up with VGC teams only if there aren't enough
        (noted).
        """
        fmt_key = game_type(fmt)
        notes: list[str] = []
        t0 = time.perf_counter()
        teams = self._full_teams(fmt_key, limit)
        if teams:
            notes.append(f"{len(teams)} opponents are complete teams (moves, ability, nature and "
                         "spread) from players' published team reports, best final ranks first")
        if len(teams) < limit:
            seen = {t["label"] for t in teams}
            teams += [t for t in self._corpus_teams(fmt_key, limit - len(teams) + len(seen))
                      if t["label"] not in seen][: limit - len(teams)]
        if any(t.get("sets_from_usage") for t in teams):
            notes.append("BSS ladder teams record species + held item only: their moves, ability "
                         "and nature are each species' most-used BSS ones")
        if len(teams) < limit and fmt_key == "singles":
            extra = self._corpus_teams("doubles", limit - len(teams))
            if extra:
                notes.append(f"only {len(teams)} complete BSS ladder teams in the corpus; "
                             f"added {len(extra)} VGC tournament teams (played as bring-3)")
            teams += extra
        if not teams:
            from . import refresh

            if refresh.running():
                raise SimError("the observed-team corpus is still being fetched in the background "
                               "(first start: about 15 minutes); retry later, or simulate against "
                               "a specific team with simulate_matchup meanwhile")
            raise SimError("the observed-team corpus is empty; run champions-mcp-pull to build it")
        modal = sum(1 for t in teams if not t["id"].startswith("report:"))
        log.info("meta opponents assembled", format=fmt, opponents=len(teams),
                 complete_report_teams=len(teams) - modal, corpus_teams=modal,
                 sets_from_usage=sum(1 for t in teams if t.get("sets_from_usage")) or None,
                 took=logmod.fmt_duration(time.perf_counter() - t0))
        if modal:
            notes.append(f"{modal} opponent team(s) from tournament/ladder data use each species' most "
                         "common Stat Point spread (that data records items/abilities/moves, not spreads)")
        return teams, notes

    def _full_teams(self, fmt_key: str, limit: int) -> list[dict[str, Any]]:
        """Complete teams from published team reports (``full_teams``): highest
        final ladder rating first (rating compares across seasons and events;
        a rank alone mixes the ladder with monthly challenges), then ranked
        teams without a rating, unranked showcase teams last."""
        try:
            rows = self._db.query(
                "SELECT * FROM full_teams WHERE format=? ORDER BY rating IS NULL, rating DESC, "
                "rank IS NULL, rank, season DESC", (fmt_key,))
        except Exception:  # database from before the table existed
            return []
        out = []
        for t in rows:
            if len(out) >= limit:
                break
            entry = self._full_team_entry(t)
            if entry:
                out.append(entry)
        return out

    def _full_team_entry(self, t: dict[str, Any]) -> dict[str, Any] | None:
        """One ``full_teams`` row as an opponent, or None if it can't be played."""
        members = json.loads(t["members_json"])
        if any(self.member_problem(m) for m in members):
            return None
        try:
            paste, _ = self.team_paste(members, "bss" if t["format"] == "singles" else "vgc")
        except SimError:
            return None
        names = [self._db.get_pokemon_row(m["species"]) or {} for m in members]
        source = " ".join(str(x) for x in (
            f"M-{t['season']}" if t["season"] else "", f"#{t['rank']}" if t["rank"] else "",
            f"rating {t['rating']:.0f}" if t["rating"] else "", t["trainer"] or "",
            f"({t['url']})" if t["url"] else "") if x)
        return {"id": t["team_id"], "label": " / ".join(n.get("showdown_name", "?") for n in names),
                "source": source, "paste": paste, "sets_from_usage": False, "title": t["title"]}

    def member_problem(self, m: dict[str, Any]) -> str | None:
        """Why a corpus member can't be played as recorded, or None.

        Scraped teamlists carry the odd typo or impossible entry (Archaludon
        with Levitate, "lastrespect", "lightingrod"); the simulator's validator
        would drop the whole team at request time. Checking against the
        Champions data here keeps such teams out of the opponent pool.
        """
        slug = try_resolve(self._db, m.get("species") or "")
        row = self._db.get_pokemon_row(slug) if slug else None
        if not row:
            return f"unknown species {m.get('species')!r}"
        if m.get("ability"):
            legal = {a.lower() for a in row.get("abilities") or []}  # get_pokemon_row decodes the JSON
            # a Mega's ability is legal on its holder (Megas are stored per base species)
            legal |= {r["ability"].lower() for r in self._db.query(
                "SELECT ability FROM mega_forms WHERE species_slug=? OR species_name=?",
                (slug, row.get("base_name") or "")) if r["ability"]}
            if legal and m["ability"].lower() not in legal:
                return f"{row['showdown_name']} can't have {m['ability']}"
        if m.get("item") and not self._db.get_item(m["item"]):
            return f"unknown item {m['item']!r}"
        for mv in m.get("moves") or []:
            move = self._db.get_move(mv)
            if not move or not self._db.query_one(
                    "SELECT 1 FROM learnable_moves WHERE slug=? AND move_slug=?", (slug, move["slug"])):
                return f"{row['showdown_name']} can't learn {mv}"
        return None

    def usage_moves(self, slug: str, fmt_key: str, item: str | None) -> list[str]:
        """The species' four most-used moves in this format (other format as
        fallback), skipping status moves for a Choice holder (locked moves)."""
        choice = (item or "").lower().startswith("choice")
        for key in (fmt_key, "singles" if fmt_key == "doubles" else "doubles"):
            rows = self._db.query(
                "SELECT name FROM usage WHERE slug=? AND format=? AND category='move' ORDER BY rank LIMIT 12",
                (slug, key))
            moves = []
            for r in rows:
                mv = self._db.get_move(r["name"])
                if choice and mv and mv.get("damage_class") == "status":
                    continue
                moves.append(r["name"])
                if len(moves) == 4:
                    return moves
            if moves:
                return moves
        return []

    def _corpus_teams(self, fmt_key: str, limit: int) -> list[dict[str, Any]]:
        """Observed teams as opponents. VGC teamlists are complete sets; BSS
        ladder exports record species + held item only, so their moves (and
        ability/nature/spread, via team_paste) come from that species' BSS
        usage, with the real item kept (it decides Megas and Choice locks)."""
        rows = self._db.query(
            "SELECT team_id, team_hash, event, player, placing, rating, date, weight FROM build_teams "
            "WHERE format=? AND source != 'report' ORDER BY weight DESC", (fmt_key,))
        out, seen = [], set()
        for t in rows:
            if len(out) >= limit:
                break
            if t["team_hash"] in seen:
                continue
            entry = self._corpus_team_entry(t, fmt_key)
            if entry:
                seen.add(t["team_hash"])
                out.append(entry)
        return out

    def _corpus_team_entry(self, t: dict[str, Any], fmt_key: str) -> dict[str, Any] | None:
        """One ``build_teams`` row as an opponent, or None if it can't be played."""
        members = self._db.query(
            "SELECT slug, item, item_slug, ability, nature, moves_json FROM build_members "
            "WHERE team_id=? ORDER BY slot", (t["team_id"],))
        if len(members) != 6:
            return None
        # pokedb items are Japanese display names: use the resolved slug's
        # English name; a held item that didn't resolve makes the team unusable.
        if any(m["item"] and not m["item_slug"] for m in members):
            return None
        for m in members:
            if m["item_slug"]:
                it = self._db.get_item(m["item_slug"])
                m["item"] = it["name"] if it else m["item"]
        team, filled = [], False
        for m in members:
            moves = json.loads(m["moves_json"]) if m["moves_json"] else []
            if not moves:
                moves = self.usage_moves(m["slug"], fmt_key, m["item"])
                filled = True
            team.append({"species": m["slug"], "item": m["item"] or None, "ability": m["ability"] or None,
                         "nature": m["nature"] or None, "moves": moves})
        if not all(x["moves"] for x in team) or any(self.member_problem(x) for x in team):
            return None
        try:
            paste, _ = self.team_paste(team, "vgc" if fmt_key == "doubles" else "bss")
        except SimError:
            return None
        names = [self._db.get_pokemon_row(m["slug"]) or {} for m in members]
        source = " ".join(str(x) for x in (
            t["event"], f"#{t['placing']}" if t["placing"] else "",
            f"rating {t['rating']:.0f}" if t["rating"] else "", t["player"] or "", t["date"] or "") if x)
        return {
            "id": t["team_id"],
            "label": " / ".join(n.get("showdown_name", "?") for n in names),
            "source": source.strip(),
            "paste": paste,
            "sets_from_usage": filled,
        }

    def team_by_id(self, team_id: str, fmt: str) -> dict[str, Any] | None:
        """The opponent with this id (from simulate_vs_meta's worst/best
        matchups), built exactly as the meta simulation played it."""
        team_id = team_id.strip()
        if team_id.startswith("report:"):
            try:
                row = self._db.query_one("SELECT * FROM full_teams WHERE team_id=?", (team_id,))
            except Exception:
                return None
            return self._full_team_entry(row) if row else None
        row = self._db.query_one(
            "SELECT team_id, team_hash, format, event, player, placing, rating, date, weight "
            "FROM build_teams WHERE team_id=?", (team_id,))
        return self._corpus_team_entry(row, row["format"]) if row else None

    @staticmethod
    def paste_species(paste: str) -> list[str]:
        """Showdown species names in a paste (nicknames and gender stripped)."""
        out = []
        for block in paste.replace("\r", "").split("\n\n"):
            lines = block.strip().splitlines()
            m = _SPECIES_LINE.match(lines[0].strip()) if lines else None
            if m:
                out.append((m.group("sp") or m.group("plain") or "").strip())
        return [x for x in out if x]

    def beliefs(self, pastes: list[str], fmt: str) -> dict[str, dict[str, Any]]:
        """What a player assumes about each species it might face, from usage:
        the most common spread and nature (hidden even on open team sheets),
        and the most-used moves, item and ability (for closed sheets)."""
        fmt_key = game_type(fmt)
        out: dict[str, dict[str, Any]] = {}
        for paste in pastes:
            for name in self.paste_species(paste):
                if name in out:
                    continue
                row = self._db.query_one("SELECT slug FROM pokemon WHERE showdown_name=?", (name,))
                slug = row["slug"] if row else try_resolve(self._db, name)
                if not slug:
                    continue
                b: dict[str, Any] = {"spread": self.modal_spread(slug, fmt_key)}
                nat = self._usage_top(slug, fmt_key, "stat_alignment")
                if nat:
                    b["nature"] = nat["name"]
                for key, category in (("item", "held_item"), ("ability", "ability")):
                    top = self._usage_top(slug, fmt_key, category)
                    if top:
                        b[key] = top["name"]
                moves = self.usage_moves(slug, fmt_key, b.get("item"))
                if moves:
                    b["moves"] = moves
                out[name] = b
        return out

    def _info(self, information: str, pastes: list[str], fmt: str) -> dict[str, Any]:
        mode = (information or "open_sheets").strip().lower()
        if mode not in INFO_MODES:
            raise SimError(f"unknown information mode {information!r}; expected one of {', '.join(INFO_MODES)}")
        return {"information": mode, "beliefs": {} if mode == "omniscient" else self.beliefs(pastes, fmt)}

    # ---------------------------------------------------------- transport

    async def call(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one request to the sidecar (HTTP if configured, else docker run)."""
        opponents = payload.get("opponents") or []
        variants = len(payload.get("variants") or []) + 1 if op == "compare" else 1
        gpo = payload.get("games_per_opponent")
        log.info(f"→ sidecar {op}", format=payload.get("format"),
                 battles=gpo * len(opponents) * variants if gpo else None,
                 opponents=len(opponents) or None, games_per_opponent=gpo,
                 variants=variants if op == "compare" else None,
                 policy=payload.get("policy"), information=payload.get("information"),
                 via="http" if self._s.sim_http else "docker run")
        t0 = time.perf_counter()
        if self._s.sim_http:
            res = await self._call_http(op, payload)
        else:
            res = await self._call_docker(op, payload)
        took = logmod.fmt_duration(time.perf_counter() - t0)
        if not res.get("ok"):
            log.warning(f"← sidecar {op} failed", took=took, error=res.get("error"),
                        problems=len(res["problems"]) if isinstance(res.get("problems"), list) else None,
                        dropped=len(res["dropped"]) if isinstance(res.get("dropped"), list) else None)
        else:
            log.info(f"← sidecar {op}", took=took, games=res.get("games"),
                     battles_per_sec=res.get("battles_per_sec"),
                     dropped_opponents=len(res["opponents_dropped"]) if res.get("opponents_dropped") else None,
                     policy_fallbacks=res.get("policy_fallbacks") or None)
        return res

    async def _call_http(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = self._s.sim_http.rstrip("/") + "/" + op
        try:
            rid = logmod.request_id.get()
            async with httpx.AsyncClient(timeout=self._s.sim_timeout) as client:
                resp = await client.post(url, json=payload, headers={"X-Request-Id": rid} if rid else None)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            return {"ok": False, "error": f"simulation timed out after {self._s.sim_timeout:.0f}s; ask for fewer games"}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"champions-sim unreachable at {url}: {exc}"}

    async def _call_docker(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        cmd = [self._s.calc_docker, "run", "--rm", "-i", "--network", "none",
               self._s.sim_image, "/app/cli.js"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            return {"ok": False, "error": f"'{self._s.calc_docker}' not found; Docker is required for the battle simulator."}
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps({"op": op, **payload}).encode()), timeout=self._s.sim_timeout)
        except TimeoutError:
            proc.kill()
            return {"ok": False, "error": f"simulation timed out after {self._s.sim_timeout:.0f}s; ask for fewer games"}
        if proc.returncode != 0 and not out:
            msg = err.decode(errors="replace").strip()[:400]
            hint = (f" (build it: docker build -f docker/sim/Dockerfile -t {self._s.sim_image} .)"
                    if "No such image" in msg or "Unable to find image" in msg else "")
            return {"ok": False, "error": f"docker run failed: {msg}{hint}"}
        try:
            return json.loads(out.decode().strip().splitlines()[-1])
        except (ValueError, IndexError, UnicodeDecodeError):
            return {"ok": False, "error": "could not parse simulator output: " + out.decode(errors="replace")[:400]}

    # ------------------------------------------------------------ tools

    def _cap(self, battles: int) -> None:
        if battles > self._s.sim_max_battles:
            raise SimError(f"{battles} battles requested; the per-call cap is {self._s.sim_max_battles} "
                           "(CHAMPIONS_MCP_SIM_MAX_BATTLES). Use fewer games or opponents.")

    @staticmethod
    def _finish(result: dict[str, Any], notes: list[str], **extra: Any) -> dict[str, Any]:
        if not result.pop("ok", False):
            err = {"error": result.get("error", "simulation failed")}
            if extra.get("format"):
                err["format"] = extra["format"]
                if "not legal in" in err["error"]:
                    err["error"] += (f" (checked as format={extra['format']!r}; pass "
                                     f"format='{'vgc' if extra['format'] == 'bss' else 'bss'}' if you meant the other one)")
            for k in ("problems", "dropped"):
                if result.get(k):
                    err[k] = result[k]
            if notes:
                err["notes"] = notes
            return err
        result.update(extra)
        if notes:
            result["assumptions"] = notes
        return result

    async def matchup(self, team_a: Any, team_b: Any, fmt: str, games: int, policy: str,
                      opponent_policy: str | None, seed: int,
                      information: str = "open_sheets") -> dict[str, Any]:
        fmt = resolve_format(fmt)
        self._cap(games)
        a, na = self.team_paste(team_a, fmt)
        b, nb = self.team_paste(team_b, fmt)
        res = await self.call("simulate", {
            "format": fmt, "team": a, "opponents": [{"id": "team_b", "paste": b}],
            "games_per_opponent": games, "policy": policy,
            "opponent_policy": opponent_policy or policy, "seed": seed,
            **self._info(information, [a, b], fmt),
        })
        for k in ("worst_matchups", "best_matchups", "per_opponent"):
            res.pop(k, None)
        return self._finish(res, [f"team_a: {x}" for x in na] + [f"team_b: {x}" for x in nb], format=fmt)

    async def vs_meta(self, team: Any, fmt: str, opponents: int, games_per_opponent: int,
                      policy: str, opponent_policy: str | None, seed: int,
                      information: str = "open_sheets") -> dict[str, Any]:
        fmt = resolve_format(fmt)
        self._cap(opponents * games_per_opponent)
        paste, notes = self.team_paste(team, fmt)
        pool, pnotes = self.meta_opponents(fmt, opponents)
        by_id = {o["id"]: o for o in pool}
        res = await self.call("simulate", {
            "format": fmt, "team": paste,
            "opponents": [{"id": o["id"], "label": o["label"], "paste": o["paste"]} for o in pool],
            "games_per_opponent": games_per_opponent, "policy": policy,
            "opponent_policy": opponent_policy or policy, "seed": seed,
            **self._info(information, [paste] + [o["paste"] for o in pool], fmt),
        })
        for key in ("worst_matchups", "best_matchups"):
            for o in res.get(key, []):
                o["source"] = by_id.get(o["id"], {}).get("source")
        res.pop("per_opponent", None)  # worst/best carry the useful part; keep the payload small
        return self._finish(res, notes + pnotes, format=fmt)

    async def compare(self, base: Any, variants: list[Any], fmt: str, opponents: int,
                      games_per_opponent: int, policy: str, opponent_policy: str | None,
                      seed: int, information: str = "open_sheets") -> dict[str, Any]:
        fmt = resolve_format(fmt)
        if not variants:
            raise SimError("give at least one variant team to compare against the base")
        self._cap(opponents * games_per_opponent * (len(variants) + 1))
        base_paste, notes = self.team_paste(base, fmt)
        vpastes = []
        for i, v in enumerate(variants, 1):
            p, n = self.team_paste(v, fmt)
            vpastes.append({"id": f"variant_{i}", "paste": p})
            notes += [f"variant_{i}: {x}" for x in n]
        pool, pnotes = self.meta_opponents(fmt, opponents)
        res = await self.call("compare", {
            "format": fmt, "base": base_paste, "base_id": "base", "variants": vpastes,
            "opponents": [{"id": o["id"], "label": o["label"], "paste": o["paste"]} for o in pool],
            "games_per_opponent": games_per_opponent, "policy": policy,
            "opponent_policy": opponent_policy or policy, "seed": seed,
            **self._info(information, [base_paste] + [v["paste"] for v in vpastes]
                         + [o["paste"] for o in pool], fmt),
        })
        return self._finish(res, notes + pnotes, format=fmt)

    async def replay(self, team_a: Any, team_b: Any, fmt: str, policy: str, seed: int,
                     information: str = "open_sheets") -> dict[str, Any]:
        fmt = resolve_format(fmt)
        a, na = self.team_paste(team_a, fmt)
        b, nb = self.team_paste(team_b, fmt)
        res = await self.call("replay", {
            "format": fmt, "team_a": a, "team_b": b, "policy_a": policy, "policy_b": policy, "seed": seed,
            **self._info(information, [a, b], fmt),
        })
        return self._finish(res, [f"team_a: {x}" for x in na] + [f"team_b: {x}" for x in nb], format=fmt)

    async def validate(self, team: Any, fmt: str) -> dict[str, Any]:
        fmt = resolve_format(fmt)
        paste, notes = self.team_paste(team, fmt)
        res = await self.call("validate", {"format": fmt, "teams": [{"id": "team", "paste": paste}]})
        if not res.pop("ok", False):
            return {"error": res.get("error", "validation failed")}
        t = res["teams"][0]
        return {"valid": t["valid"], "problems": t["problems"], "format_id": res.get("format_id"),
                "paste": paste, **({"assumptions": notes} if notes else {})}
