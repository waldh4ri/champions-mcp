"""SQLite data layer for champions-mcp v2.

A single file, ``champions.sqlite``, is the entire runtime data source. It is
built by ``champions-mcp-pull`` from pokechamp.gg (+ PokeAPI for the type
chart and localized species names) and read by the MCP server with **zero**
network access. All SQL lives here so the read-model modules stay declarative.

Tables
------
pokemon            one row per playable battle entry (~268), incl. forms
pokemon_types      (slug, type)                        -> pokemons_by_type
pokemon_abilities  (slug, ability_slug, ability_name)  -> pokemons_by_ability
learnable_moves    (slug, move_slug, move_name)        -> movepool / pokemons_by_move
usage              parsed usage rows per format         -> get_pokemon_sets
usage_rank         (format, rank, slug)                 -> get_usage_stats top-N
teammates          (slug, format, rank, teammate_*)     -> suggest_cores
moves/abilities/items   pokechamp.gg reference text
mega_forms         (form_name, species_slug, mega_kind, ...)  -> Mega Evolution data
type_chart         (attacking, defending, multiplier)   -> weaknesses
build_teams        one observed team per row + provenance  -> get_observed_sets
build_members      that team's six members and their sets  -> get_team_context
smogon_sets        curated cross-game sets (Smogon/Showdown)  -> get_historical_sets
meta               key/value build markers (season, data_version, counts)

``usage`` and ``build_*`` answer different questions and are not interchangeable.
``usage`` holds *marginal* frequencies — how often a Pokémon runs each move,
computed independently per slot — so it can never say which moves accompany
which item. ``build_teams``/``build_members`` hold complete lists people
actually brought, which is what makes those joint questions answerable.
``smogon_sets`` is neither: it's curated cross-game evidence (not Champions
tournaments), kept as a fallback for when the observed corpus is thin.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .normalize import normalize_key, slugify

SCHEMA = """
CREATE TABLE IF NOT EXISTS pokemon (
    slug            TEXT PRIMARY KEY,
    battle_name     TEXT NOT NULL,
    base_name       TEXT NOT NULL,
    showdown_name   TEXT NOT NULL,
    form_kind       TEXT,
    types_json      TEXT NOT NULL,
    abilities_json  TEXT NOT NULL,
    base_stats_json TEXT NOT NULL,
    bst             INTEGER NOT NULL,
    forms_json      TEXT NOT NULL,
    sprite          TEXT,
    names_json      TEXT NOT NULL DEFAULT '{}',
    rank_doubles    INTEGER,
    rank_singles    INTEGER
);

CREATE TABLE IF NOT EXISTS pokemon_types (
    slug TEXT NOT NULL,
    type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ptypes_type ON pokemon_types(type);
CREATE INDEX IF NOT EXISTS idx_ptypes_slug ON pokemon_types(slug);

CREATE TABLE IF NOT EXISTS pokemon_abilities (
    slug         TEXT NOT NULL,
    ability_slug TEXT NOT NULL,
    ability_name TEXT NOT NULL,
    form_name    TEXT
);
CREATE INDEX IF NOT EXISTS idx_pab_ability ON pokemon_abilities(ability_slug);
CREATE INDEX IF NOT EXISTS idx_pab_slug ON pokemon_abilities(slug);

CREATE TABLE IF NOT EXISTS learnable_moves (
    slug      TEXT NOT NULL,
    move_slug TEXT NOT NULL,
    move_name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lm_slug ON learnable_moves(slug);
CREATE INDEX IF NOT EXISTS idx_lm_move ON learnable_moves(move_slug);

CREATE TABLE IF NOT EXISTS usage (
    slug             TEXT NOT NULL,
    format           TEXT NOT NULL,
    category         TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    name             TEXT,
    name_slug        TEXT,
    percentage       TEXT,
    percentage_value REAL,
    stat_up          TEXT,
    stat_down        TEXT,
    hp_points        INTEGER,
    attack_points    INTEGER,
    defense_points   INTEGER,
    sp_atk_points    INTEGER,
    sp_def_points    INTEGER,
    speed_points     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_main ON usage(slug, format, category, rank);

CREATE TABLE IF NOT EXISTS usage_rank (
    format TEXT NOT NULL,
    rank   INTEGER NOT NULL,
    slug   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_urank ON usage_rank(format, rank);

CREATE TABLE IF NOT EXISTS teammates (
    slug          TEXT NOT NULL,
    format        TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    teammate_name TEXT NOT NULL,
    teammate_slug TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tm_slug ON teammates(slug, format);
CREATE INDEX IF NOT EXISTS idx_tm_format ON teammates(format);

CREATE TABLE IF NOT EXISTS moves (
    slug         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    type         TEXT,
    damage_class TEXT,
    power        INTEGER,
    accuracy     INTEGER,
    pp           INTEGER,
    priority     INTEGER,
    target       TEXT,
    effect       TEXT
);

CREATE TABLE IF NOT EXISTS abilities (
    slug         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    effect       TEXT,
    short_effect TEXT
);

CREATE TABLE IF NOT EXISTS items (
    slug          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    effect        TEXT,
    is_mega_stone INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mega_forms (
    form_name         TEXT PRIMARY KEY,  -- "Mega Charizard X"
    species_slug      TEXT NOT NULL,     -- 'charizard' (base-form owner)
    species_name      TEXT NOT NULL,     -- 'Charizard'
    mega_kind         TEXT NOT NULL,     -- 'Mega' | 'Mega X' | 'Mega Y'
    mega_stone        TEXT,              -- NULL if no held-item usage confirms it
    types_json        TEXT NOT NULL,
    ability           TEXT,
    base_stats_json   TEXT NOT NULL,     -- the Mega form's own stats
    bst               INTEGER,
    stat_changes_json TEXT NOT NULL,     -- Mega stat - base-form stat, per stat
    bst_change        INTEGER
);

CREATE TABLE IF NOT EXISTS type_chart (
    attacking  TEXT NOT NULL,
    defending  TEXT NOT NULL,
    multiplier REAL NOT NULL,
    PRIMARY KEY (attacking, defending)
);

-- One row per *observed* team: a real six-Pokémon list somebody actually
-- brought, as opposed to the marginal frequencies in `usage`. This is what
-- makes conditional questions answerable ("what does Garchomp run *when it
-- holds a Choice Scarf*") instead of only marginal ones.
CREATE TABLE IF NOT EXISTS build_teams (
    team_id     TEXT PRIMARY KEY,  -- sha1(source|source_ref|player)
    source      TEXT NOT NULL,     -- 'limitless' | 'pokedb'
    source_ref  TEXT,              -- tournament id / ladder season
    event       TEXT,              -- human-readable event name
    format      TEXT NOT NULL,     -- 'doubles' | 'singles'
    regulation  TEXT,              -- 'M-C'
    date        TEXT,              -- ISO date, may be NULL upstream
    player      TEXT,
    placing     INTEGER,
    wins        INTEGER,
    losses      INTEGER,
    ties        INTEGER,
    rating      REAL,              -- ladder rating (pokedb only)
    field_size  INTEGER,           -- players in the event
    -- 'full_set'      : species + item + ability + nature + 4 moves (Limitless)
    -- 'team_and_item' : species + item only          (pokedb ladder export)
    evidence    TEXT NOT NULL,
    weight      REAL NOT NULL,     -- recency x field size x placement
    team_hash   TEXT NOT NULL      -- identical lists across events collapse here
);
CREATE INDEX IF NOT EXISTS idx_bt_fmt ON build_teams(format, regulation);
CREATE INDEX IF NOT EXISTS idx_bt_hash ON build_teams(team_hash);

CREATE TABLE IF NOT EXISTS build_members (
    team_id    TEXT NOT NULL,
    slot       INTEGER NOT NULL,
    slug       TEXT NOT NULL,      -- resolved against `pokemon`
    item       TEXT,
    item_slug  TEXT,
    ability    TEXT,
    nature     TEXT,
    moves_json TEXT,               -- sorted JSON list, NULL when evidence is team_and_item
    set_hash   TEXT NOT NULL       -- the joint key: slug+item+ability+nature+moves
);
CREATE INDEX IF NOT EXISTS idx_bm_slug ON build_members(slug);
CREATE INDEX IF NOT EXISTS idx_bm_team ON build_members(team_id);
CREATE INDEX IF NOT EXISTS idx_bm_set ON build_members(set_hash);

-- Curated competitive sets from Smogon/Pokémon Showdown (every generation and
-- tier they publish), kept as a historical-builds fallback for when the real
-- observed-team corpus (build_teams/build_members, above) is thin or empty —
-- which it is right after a regulation rollover, before real Champions
-- tournaments/ladder games exist. Not Champions-verified: legality against
-- the current roster/movepool/item pool is checked at read time, not here.
CREATE TABLE IF NOT EXISTS smogon_sets (
    id            TEXT PRIMARY KEY,  -- sha1(slug|format|source_format|set_name)
    slug          TEXT NOT NULL,     -- resolved against `pokemon`
    format        TEXT NOT NULL,     -- 'doubles' | 'singles'
    source_format TEXT NOT NULL,     -- raw Showdown format id, e.g. 'gen9ou'
    generation    INTEGER NOT NULL,  -- 1-9; prefer higher when deduping
    set_name      TEXT NOT NULL,     -- e.g. 'Choice Scarf'
    ability       TEXT,
    item          TEXT,
    nature        TEXT,
    moves_json    TEXT NOT NULL,     -- JSON list, as Smogon gives it
    evs_json      TEXT,              -- JSON dict, canonical champions_stats keys
    set_hash      TEXT NOT NULL      -- item+ability+nature+moves, dedup across gens/tiers
);
CREATE INDEX IF NOT EXISTS idx_smogon_slug_fmt ON smogon_sets(slug, format);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Tables wiped + repopulated on every (re)build, in this order.
_BUILD_TABLES = (
    "pokemon", "pokemon_types", "pokemon_abilities", "learnable_moves",
    "usage", "usage_rank", "teammates",
    "moves", "abilities", "items", "mega_forms", "type_chart", "meta",
    "build_teams", "build_members", "smogon_sets",
)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


class Db:
    """Thread-safe SQLite wrapper. Reads are synchronous and sub-millisecond."""

    def __init__(self, path: Path | str, *, create: bool = False) -> None:
        """Open a connection to the champions SQLite database.

        Args:
            path: Location of the SQLite file.
            create: If True, create the file and run ``SCHEMA`` against it
                (used by ``champions-mcp-pull``); if False, the file must
                already exist.

        Raises:
            FileNotFoundError: ``create`` is False and no database exists at
                ``path``.
        """
        self.path = Path(path)
        if not create and not self.path.exists():
            raise FileNotFoundError(
                f"champions database not found at {self.path}. "
                "Run `champions-mcp-pull` to build it."
            )
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def ensure_schema(self) -> None:
        """Idempotently create any table/index this version of the schema adds
        that an existing database file predates.

        Every statement in ``SCHEMA`` is ``CREATE ... IF NOT EXISTS``, so this
        is a no-op on a fully up-to-date database and additive-only on an
        older one — it never touches existing data. Needed because opening
        with ``create=False`` (the normal server/refresh path) does not run
        ``SCHEMA`` at all, so a persistent volume built by an older
        ``champions-mcp-pull`` can be missing tables a newer pull expects
        (e.g. ``build_teams``/``build_members``) even though the file exists.
        """
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # Low-level query helpers
    # ------------------------------------------------------------------ #
    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Run a SELECT and return every matching row as a dict.

        Args:
            sql: SQL text to execute.
            params: Positional parameters bound into ``sql``.

        Returns:
            One dict per row, keyed by column name.
        """
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Run a SELECT and return the first matching row, if any.

        Args:
            sql: SQL text to execute.
            params: Positional parameters bound into ``sql``.

        Returns:
            The first row as a dict, or None if the query had no results.
        """
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return _row_to_dict(row) if row is not None else None

    # ------------------------------------------------------------------ #
    # Build-time helpers (used by champions_mcp.pull)
    # ------------------------------------------------------------------ #
    def reset_build_tables(self) -> None:
        """Delete all rows from every table that a (re)build repopulates."""
        with self._lock:
            for t in _BUILD_TABLES:
                self._conn.execute(f"DELETE FROM {t}")
            self._conn.commit()

    def reset_observed_builds(self) -> None:
        """Delete all rows from ``build_teams``/``build_members`` only.

        Narrower than `reset_build_tables`: the observed-build corpus can be
        refreshed on its own — independent of pokechamp's roster and usage
        data, which come from an entirely different upstream — so
        this must not touch `pokemon`/`usage`/etc. Used both as the first step
        of a full rebuild (where it is a no-op on an empty temp database) and
        to refresh just the corpus against an already-populated database.
        """
        with self._lock:
            self._conn.execute("DELETE FROM build_teams")
            self._conn.execute("DELETE FROM build_members")
            self._conn.commit()

    def reset_smogon_sets(self) -> None:
        """Delete all rows from ``smogon_sets`` only.

        Same rationale as `reset_observed_builds`: this corpus (Smogon/
        Showdown sets) can be refreshed independently of the roster pull and
        of the observed-team corpus, since it comes from a third, unrelated
        upstream that changes on its own schedule.
        """
        with self._lock:
            self._conn.execute("DELETE FROM smogon_sets")
            self._conn.commit()

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        """Run a parameterized statement once per row and commit.

        Args:
            sql: SQL text with positional placeholders.
            rows: One parameter tuple per execution.
        """
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def set_meta(self, key: str, value: Any) -> None:
        """Upsert a key/value pair into the ``meta`` table.

        Args:
            key: Meta key.
            value: Any JSON-serializable value.
        """
        # Always JSON-encode so values round-trip with their type. Notably,
        # data_version is a long numeric *string* identifier; storing it raw
        # would make meta()'s json.loads re-parse it as a (lossy) number.
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            self._conn.commit()

    def count(self, table: str) -> int:
        """Row count of ``table``.

        Args:
            table: Table name (interpolated directly into SQL — caller-trusted).

        Returns:
            Number of rows in the table.
        """
        with self._lock:
            return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # ------------------------------------------------------------------ #
    # meta
    # ------------------------------------------------------------------ #
    def meta(self, key: str, default: Any = None) -> Any:
        """Read a value from the ``meta`` table.

        Args:
            key: Meta key.
            default: Value to return if the key is absent.

        Returns:
            The JSON-decoded value, the raw stored string if it isn't valid
            JSON, or ``default`` if the key doesn't exist.
        """
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return row["value"]

    # ------------------------------------------------------------------ #
    # Pokémon read-model
    # ------------------------------------------------------------------ #
    @staticmethod
    def _hydrate_pokemon(row: dict[str, Any]) -> dict[str, Any]:
        """Replace a pokemon row's ``*_json`` columns with decoded Python values."""
        out = dict(row)
        for col in ("types", "abilities", "base_stats", "forms", "names"):
            jc = f"{col}_json"
            if jc in out:
                out[col] = json.loads(out.pop(jc))
        return out

    def get_pokemon_row(self, slug: str) -> dict[str, Any] | None:
        """Fetch one pokemon by slug, with its JSON columns decoded.

        Args:
            slug: Pokemon slug (primary key).

        Returns:
            The hydrated row, or None if no pokemon has this slug.
        """
        row = self.query_one("SELECT * FROM pokemon WHERE slug = ?", (slug,))
        return self._hydrate_pokemon(row) if row else None

    def all_pokemon_slugs(self) -> list[str]:
        """Every pokemon slug in the roster, sorted alphabetically."""
        return [r["slug"] for r in self.query("SELECT slug FROM pokemon ORDER BY slug")]

    def random_pokemon_slug(self) -> str | None:
        """A uniformly random pokemon slug, or None if the table is empty."""
        row = self.query_one("SELECT slug FROM pokemon ORDER BY RANDOM() LIMIT 1")
        return row["slug"] if row else None

    def resolve_slug(self, name: str) -> str | None:
        """Resolve a (possibly localized / form) name to a pokemon slug.

        Order: exact slug -> exact battle/showdown name -> localized name ->
        normalized-key match.

        Args:
            name: A slug, battle name, Showdown name, or localized name.

        Returns:
            The matching slug, or None if nothing matches (caller may fuzz).
        """
        key = slugify(name)
        if self.query_one("SELECT 1 FROM pokemon WHERE slug = ?", (key,)):
            return key
        norm = normalize_key(name)
        row = self.query_one(
            "SELECT slug FROM pokemon WHERE lower(battle_name) = ? "
            "OR lower(showdown_name) = ? LIMIT 1",
            (name.strip().lower(), name.strip().lower()),
        )
        if row:
            return row["slug"]
        # normalized comparison against battle/showdown names
        for r in self.query("SELECT slug, battle_name, showdown_name, names_json FROM pokemon"):
            if normalize_key(r["battle_name"]) == norm or normalize_key(r["showdown_name"]) == norm:
                return r["slug"]
            names = json.loads(r["names_json"] or "{}")
            for loc in names.values():
                if normalize_key(loc) == norm:
                    return r["slug"]
        return None

    def search_pokemon(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        """Substring-match pokemon by battle name or slug.

        Args:
            query: Search text.
            limit: Maximum number of rows to return.

        Returns:
            Rows with ``slug`` and ``battle_name``, ordered by battle name.
        """
        like = f"%{normalize_key(query)}%"
        rows = self.query(
            "SELECT slug, battle_name FROM pokemon "
            "WHERE replace(replace(lower(battle_name),' ','-'),'.','') LIKE ? "
            "OR slug LIKE ? ORDER BY battle_name LIMIT ?",
            (like, like, limit),
        )
        return rows

    def pokemons_by_type(self, t1: str, t2: str | None = None) -> list[str]:
        """Slugs of pokemon with a given type, or exactly a given dual type.

        Args:
            t1: First type (required).
            t2: Second type; if given, only dual-typed matches of exactly
                these two types are returned.

        Returns:
            Matching slugs, sorted alphabetically.
        """
        if t2:
            rows = self.query(
                "SELECT slug FROM pokemon_types WHERE type IN (?, ?) "
                "GROUP BY slug HAVING COUNT(DISTINCT type) = 2 ORDER BY slug",
                (t1.capitalize(), t2.capitalize()),
            )
        else:
            rows = self.query(
                "SELECT slug FROM pokemon_types WHERE type = ? ORDER BY slug",
                (t1.capitalize(),),
            )
        return [r["slug"] for r in rows]

    def pokemons_by_ability(self, ability: str) -> list[dict[str, Any]]:
        """Pokemon (and, if form-specific, which form) that have an ability.

        Args:
            ability: Ability name.

        Returns:
            Rows with ``slug`` and ``form_name`` (the latter may be None).
        """
        return self.query(
            "SELECT DISTINCT slug, form_name FROM pokemon_abilities "
            "WHERE ability_slug = ? ORDER BY slug",
            (slugify(ability),),
        )

    def pokemons_by_move(self, move: str) -> list[str]:
        """Slugs of pokemon that can learn a given move.

        Args:
            move: Move name.

        Returns:
            Matching slugs, sorted alphabetically.
        """
        rows = self.query(
            "SELECT DISTINCT slug FROM learnable_moves WHERE move_slug = ? ORDER BY slug",
            (slugify(move),),
        )
        return [r["slug"] for r in rows]

    def learnable_moves(self, slug: str) -> list[dict[str, str]]:
        """A pokemon's full legal movepool (move slug + display name).

        Args:
            slug: Pokemon slug.

        Returns:
            Rows with ``move_slug`` and ``move_name``, ordered by name.
        """
        return self.query(
            "SELECT move_slug, move_name FROM learnable_moves WHERE slug = ? "
            "ORDER BY move_name",
            (slug,),
        )

    def learnable_attacking_moves(self, slug: str) -> list[dict[str, Any]]:
        """This Pokémon's legal damaging moves with their type/category/power,
        in one join — used to turn "you want Rock coverage" into the actual
        Rock moves it can run.

        Args:
            slug: Pokemon slug.

        Returns:
            Rows with ``name``, ``type``, ``damage_class`` and ``power``,
            ordered by power descending.
        """
        return self.query(
            "SELECT m.name, m.type, m.damage_class, m.power "
            "FROM learnable_moves lm JOIN moves m ON m.slug = lm.move_slug "
            "WHERE lm.slug = ? AND lower(m.damage_class) IN ('physical','special') "
            "  AND m.power IS NOT NULL AND m.power > 0 "
            "ORDER BY m.power DESC",
            (slug,),
        )

    def is_learnable(self, slug: str, move: str) -> bool:
        """Whether a pokemon can legally learn a move.

        Args:
            slug: Pokemon slug.
            move: Move name.

        Returns:
            True if the move is in the pokemon's movepool.
        """
        return bool(
            self.query_one(
                "SELECT 1 FROM learnable_moves WHERE slug = ? AND move_slug = ?",
                (slug, slugify(move)),
            )
        )

    # ------------------------------------------------------------------ #
    # Usage read-model
    # ------------------------------------------------------------------ #
    def usage_rows(self, slug: str, fmt: str, category: str) -> list[dict[str, Any]]:
        """Raw parsed usage rows for one pokemon/format/category, ranked.

        Args:
            slug: Pokemon slug.
            fmt: Internal format key (e.g. "vgc" or "bss").
            category: Usage category (e.g. "moves", "items", "abilities").

        Returns:
            Matching rows, ordered by rank.
        """
        return self.query(
            "SELECT * FROM usage WHERE slug = ? AND format = ? AND category = ? "
            "ORDER BY rank",
            (slug, fmt, category),
        )

    def usage_ranking(
        self, fmt: str, limit: int = 30, order: str = "top", megas_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Top- or bottom-ranked pokemon by usage in a format.

        Args:
            fmt: Internal format key.
            limit: Maximum number of rows to return.
            order: "top" for highest usage first, "bottom" for lowest usage first.
            megas_only: If True, restrict to species with a known Mega form.

        Returns:
            Rows with ``rank``, ``slug``, ``battle_name`` and ``bst``.
        """
        # `order` is caller-controlled but only ever "top"/"bottom" (validated
        # in usage.get_usage_stats before it reaches here), so this f-string
        # is safe — never interpolate an unvalidated value into SQL like this.
        direction = "DESC" if order == "bottom" else "ASC"
        mega_filter = (
            "AND ur.slug IN (SELECT DISTINCT species_slug FROM mega_forms) "
            if megas_only else ""
        )
        return self.query(
            "SELECT ur.rank, ur.slug, p.battle_name, p.bst "
            "FROM usage_rank ur JOIN pokemon p ON p.slug = ur.slug "
            f"WHERE ur.format = ? {mega_filter}"
            f"ORDER BY ur.rank {direction} LIMIT ?",
            (fmt, limit),
        )

    def mega_stone_usage(self, fmt: str) -> list[dict[str, Any]]:
        """Held-item usage rows that are Mega Stones, joined to the Mega form
        each stone unlocks, for the whole format in one query.

        This is what tells you a ranked Pokémon is actually played as its Mega:
        a species whose held-item usage is dominated by its stone is never on
        the field with its base stats, so speed tiers and damage calcs built on
        the base form are wrong for it. ``usage.name_slug`` joins straight to
        ``items.slug``; the Mega form is matched on the stone name (display
        names like "Charizardite X" slugify by lowercasing and hyphenating).

        Args:
            fmt: Internal format key.

        Returns:
            Rows with ``slug``, ``stone``, ``percent``, ``form_name``,
            ``base_stats_json``, ``types_json``, ``ability`` and ``bst``.
        """
        return self.query(
            "SELECT u.slug, u.name AS stone, u.percentage_value AS percent, "
            "       mf.form_name, mf.base_stats_json, mf.types_json, "
            "       mf.ability, mf.bst "
            "FROM usage u "
            "JOIN items i ON i.slug = u.name_slug AND i.is_mega_stone = 1 "
            "LEFT JOIN mega_forms mf ON mf.species_slug = u.slug "
            "     AND replace(lower(mf.mega_stone), ' ', '-') = u.name_slug "
            "WHERE u.format = ? AND u.category = 'held_item' "
            "ORDER BY u.slug, u.percentage_value DESC",
            (fmt,),
        )

    def teammates(self, slug: str, fmt: str) -> list[dict[str, Any]]:
        """Top common teammates for one pokemon in a format.

        Args:
            slug: Pokemon slug.
            fmt: Internal format key.

        Returns:
            Rows with ``rank``, ``teammate_name`` and ``teammate_slug``.
        """
        return self.query(
            "SELECT rank, teammate_name, teammate_slug FROM teammates "
            "WHERE slug = ? AND format = ? ORDER BY rank",
            (slug, fmt),
        )

    def all_teammate_pairs(self, fmt: str) -> list[dict[str, Any]]:
        """Every recorded teammate pair for a format, used to build cores.

        Args:
            fmt: Internal format key.

        Returns:
            Rows with ``slug``, ``teammate_slug``, ``teammate_name`` and ``rank``.
        """
        return self.query(
            "SELECT slug, teammate_slug, teammate_name, rank FROM teammates "
            "WHERE format = ?",
            (fmt,),
        )

    # ------------------------------------------------------------------ #
    # Observed builds read-model (build_teams / build_members)
    # ------------------------------------------------------------------ #
    def has_builds_tables(self) -> bool:
        """Whether this database was built with the observed-team tables.

        A mirror built by an older ``champions-mcp-pull`` has no ``build_teams``,
        and the server must degrade to marginal-only data rather than raising
        on every meta tool call.
        """
        return bool(self.query_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='build_teams'"
        ))

    def builds_available(self, fmt: str) -> dict[str, Any] | None:
        """Summary of the observed-team corpus for a format, or None if empty.

        Args:
            fmt: Internal format key (``doubles``/``singles``).

        Returns:
            A dict with ``teams``, ``evidence``, ``regulations``, ``sources``
            and the date range, or None when nothing has been ingested (or the
            mirror predates the observed-team tables).
        """
        if not self.has_builds_tables():
            return None
        row = self.query_one(
            "SELECT COUNT(*) AS teams, MIN(date) AS first_date, MAX(date) AS last_date "
            "FROM build_teams WHERE format = ?",
            (fmt,),
        )
        if not row or not row["teams"]:
            return None
        out = dict(row)
        out["evidence"] = [
            r["evidence"] for r in self.query(
                "SELECT DISTINCT evidence FROM build_teams WHERE format = ?", (fmt,)
            )
        ]
        out["sources"] = [
            r["source"] for r in self.query(
                "SELECT DISTINCT source FROM build_teams WHERE format = ?", (fmt,)
            )
        ]
        out["regulations"] = [
            r["regulation"] for r in self.query(
                "SELECT regulation, COUNT(*) c FROM build_teams WHERE format = ? "
                "AND regulation IS NOT NULL GROUP BY regulation ORDER BY c DESC",
                (fmt,),
            )
        ]
        return out

    def observed_members(
        self, slug: str, fmt: str, regulation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every observed appearance of one Pokémon, joined to its team.

        Args:
            slug: Pokemon slug.
            fmt: Internal format key.
            regulation: Restrict to one regulation (e.g. ``"M-B"``); None keeps all.

        Returns:
            One row per appearance, carrying the member's set fields plus the
            owning team's provenance and weight.
        """
        if not self.has_builds_tables():
            return []
        clause, params = "", [slug, fmt]
        if regulation:
            clause = "AND t.regulation = ? "
            params.append(regulation)
        return self.query(
            "SELECT m.team_id, m.item, m.item_slug, m.ability, m.nature, "
            "       m.moves_json, m.set_hash, "
            "       t.evidence, t.weight, t.event, t.player, t.placing, "
            "       t.wins, t.losses, t.ties, t.rating, t.date, t.regulation, "
            "       t.source, t.field_size "
            "FROM build_members m JOIN build_teams t ON t.team_id = m.team_id "
            f"WHERE m.slug = ? AND t.format = ? {clause}"
            "ORDER BY t.weight DESC",
            tuple(params),
        )

    def observed_rosters(
        self, fmt: str, regulation: str | None = None,
    ) -> dict[str, list[str]]:
        """team_id -> its member slugs, for every observed team in a format.

        Args:
            fmt: Internal format key.
            regulation: Restrict to one regulation; None keeps all.

        Returns:
            A mapping of team id to the list of slugs on that team.
        """
        if not self.has_builds_tables():
            return {}
        clause, params = "", [fmt]
        if regulation:
            clause = "AND t.regulation = ? "
            params.append(regulation)
        rosters: dict[str, list[str]] = {}
        for r in self.query(
            "SELECT m.team_id, m.slug FROM build_members m "
            "JOIN build_teams t ON t.team_id = m.team_id "
            f"WHERE t.format = ? {clause}",
            tuple(params),
        ):
            rosters.setdefault(r["team_id"], []).append(r["slug"])
        return rosters

    def observed_team(self, team_id: str) -> list[dict[str, Any]]:
        """The full member list of one observed team, in slot order.

        Args:
            team_id: Team primary key.

        Returns:
            Member rows ordered by slot.
        """
        return self.query(
            "SELECT * FROM build_members WHERE team_id = ? ORDER BY slot", (team_id,)
        )

    def observed_teams_for(
        self, fmt: str, slug: str | None, regulation: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        """Highest-weighted observed teams for a format, optionally containing
        a given Pokémon.

        Args:
            fmt: Internal format key.
            slug: Restrict to teams containing this slug; None for any team.
            regulation: Restrict to one regulation; None keeps all.
            limit: Maximum number of teams.

        Returns:
            build_teams rows ordered by weight, deduplicated by ``team_hash``
            so the same list entered at five events returns once.
        """
        clauses, params = ["t.format = ?"], [fmt]
        if regulation:
            clauses.append("t.regulation = ?")
            params.append(regulation)
        if slug:
            clauses.append(
                "EXISTS (SELECT 1 FROM build_members m "
                "WHERE m.team_id = t.team_id AND m.slug = ?)"
            )
            params.append(slug)
        params.append(limit)
        return self.query(
            "SELECT t.*, COUNT(*) OVER (PARTITION BY t.team_hash) AS times_seen "
            "FROM build_teams t "
            f"WHERE {' AND '.join(clauses)} "
            "GROUP BY t.team_hash "
            "ORDER BY MAX(t.weight) DESC LIMIT ?",
            tuple(params),
        )

    # ------------------------------------------------------------------ #
    # Reference read-model (PokeAPI-enriched)
    # ------------------------------------------------------------------ #
    def get_move(self, name: str) -> dict[str, Any] | None:
        """Look up a move's reference data by name.

        Args:
            name: Move name.

        Returns:
            The move row, or None if unknown.
        """
        return self.query_one("SELECT * FROM moves WHERE slug = ?", (slugify(name),))

    def search_moves(
        self, query: str | None, type_: str | None, category: str | None,
        legal_only: bool, limit: int,
    ) -> list[dict[str, Any]]:
        """Filtered move search.

        Args:
            query: Optional substring to match against the move slug.
            type_: Optional move type to filter on (e.g. "fire").
            category: Optional damage class to filter on ("physical", etc.).
            legal_only: If True, restrict to moves at least one Champions
                pokemon can actually learn.
            limit: Maximum number of rows to return.

        Returns:
            Matching move rows, ordered by name.
        """
        clauses, params = [], []
        if legal_only:
            clauses.append(
                "slug IN (SELECT DISTINCT move_slug FROM learnable_moves)"
            )
        if query:
            clauses.append("slug LIKE ?")
            params.append(f"%{slugify(query)}%")
        if type_:
            clauses.append("lower(type) = ?")
            params.append(type_.strip().lower())
        if category:
            clauses.append("lower(damage_class) = ?")
            params.append(category.strip().lower())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return self.query(f"SELECT * FROM moves {where} ORDER BY name LIMIT ?", tuple(params))

    def get_item(self, name: str) -> dict[str, Any] | None:
        """Look up an item's reference data by name.

        Args:
            name: Item name.

        Returns:
            The item row, or None if unknown.
        """
        return self.query_one("SELECT * FROM items WHERE slug = ?", (slugify(name),))

    def search_items(self, query: str | None, limit: int) -> list[dict[str, Any]]:
        """Substring-match items by slug, or list all if no query is given.

        Args:
            query: Optional substring to match against the item slug.
            limit: Maximum number of rows to return.

        Returns:
            Matching item rows, ordered by name.
        """
        if query:
            return self.query(
                "SELECT * FROM items WHERE slug LIKE ? ORDER BY name LIMIT ?",
                (f"%{slugify(query)}%", limit),
            )
        return self.query("SELECT * FROM items ORDER BY name LIMIT ?", (limit,))

    def get_ability(self, name: str) -> dict[str, Any] | None:
        """Look up an ability's reference data by name.

        Args:
            name: Ability name.

        Returns:
            The ability row, or None if unknown.
        """
        return self.query_one(
            "SELECT * FROM abilities WHERE slug = ?", (slugify(name),)
        )

    # ------------------------------------------------------------------ #
    # Mega Evolutions
    # ------------------------------------------------------------------ #
    @staticmethod
    def _hydrate_mega(row: dict[str, Any]) -> dict[str, Any]:
        """Replace a mega_forms row's ``*_json`` columns with decoded Python values."""
        out = dict(row)
        for col in ("types", "base_stats", "stat_changes"):
            jc = f"{col}_json"
            out[col] = json.loads(out.pop(jc))
        return out

    def list_mega_forms(self) -> list[dict[str, Any]]:
        """Every Mega/Primal form in the roster, hydrated.

        Returns:
            All mega_forms rows, ordered by species then form name.
        """
        rows = self.query("SELECT * FROM mega_forms ORDER BY species_name, form_name")
        return [self._hydrate_mega(r) for r in rows]

    def mega_forms_for_species(self, slug: str) -> list[dict[str, Any]]:
        """Mega/Primal forms available to one base species.

        Args:
            slug: Base species slug.

        Returns:
            Matching mega_forms rows, ordered by form name.
        """
        rows = self.query(
            "SELECT * FROM mega_forms WHERE species_slug = ? ORDER BY form_name", (slug,)
        )
        return [self._hydrate_mega(r) for r in rows]

    def get_mega_form(self, form_name: str) -> dict[str, Any] | None:
        """Look up one Mega/Primal form by its display name.

        Args:
            form_name: Full form name (e.g. "Mega Charizard X").

        Returns:
            The hydrated row, or None if unknown.
        """
        row = self.query_one("SELECT * FROM mega_forms WHERE form_name = ?", (form_name,))
        return self._hydrate_mega(row) if row else None

    # ------------------------------------------------------------------ #
    # Type chart
    # ------------------------------------------------------------------ #
    def type_multiplier(self, attacking: str, defending: str) -> float:
        """Damage multiplier for an attacking type against a defending type.

        Args:
            attacking: Attacking type name.
            defending: Defending type name.

        Returns:
            The multiplier (e.g. 2.0, 0.5, 0.0), or 1.0 if the pair isn't in
            the type chart.
        """
        row = self.query_one(
            "SELECT multiplier FROM type_chart WHERE attacking = ? AND defending = ?",
            (attacking.capitalize(), defending.capitalize()),
        )
        return float(row["multiplier"]) if row else 1.0

    def all_types(self) -> list[str]:
        """Every type name that appears in the type chart, sorted alphabetically."""
        rows = self.query("SELECT DISTINCT attacking FROM type_chart ORDER BY attacking")
        return [r["attacking"] for r in rows]
