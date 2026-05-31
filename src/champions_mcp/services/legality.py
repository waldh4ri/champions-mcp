from __future__ import annotations

from ..champions_items import ItemCatalog, item_key
from ..champions_movesets import ChampionsMovesets
from ..champions_roster import ChampionsRoster
from ..champions_stats import StatError, normalize_nature, validate_spread
from ..models import Team, ValidationReport, Violation
from ..names import NameIndex
from ..normalize import base_species
from ..pokeapi import PokeAPIClient, slugify
from ..regulations import Regulation


def _dups(pairs) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for key, label in pairs:
        grouped.setdefault(key, []).append(label)
    return {k: v for k, v in grouped.items() if len(v) > 1}


class LegalityService:
    def __init__(
        self,
        pokeapi: PokeAPIClient,
        names: NameIndex,
        catalog: ItemCatalog | None = None,
        roster: ChampionsRoster | None = None,
        movesets: ChampionsMovesets | None = None,
    ) -> None:
        self._api = pokeapi
        self._names = names
        self._catalog = catalog
        self._roster = roster
        self._movesets = movesets

    def _violation(
        self,
        violations: list[Violation],
        rule: str,
        detail: str,
        member: str | None = None,
    ) -> None:
        violations.append(Violation(member=member, rule=rule, detail=detail))

    async def validate(
        self, team: Team, regulation: Regulation
    ) -> ValidationReport:
        violations: list[Violation] = []
        warnings: list[Violation] = []

        if len(team.members) == 0:
            violations.append(
                Violation(rule="team-size", detail="Team is empty.")
            )
        if len(team.members) > regulation.team_size:
            violations.append(
                Violation(
                    rule="team-size",
                    detail=f"{len(team.members)} Pokémon submitted; "
                    f"max is {regulation.team_size}.",
                )
            )

        banned_species = {slugify(s) for s in regulation.banned_species}
        restricted = {slugify(s) for s in regulation.restricted_species}
        allowlist = (
            {slugify(s) for s in regulation.allowed_species}
            if regulation.allowed_species is not None
            else None
        )
        banned_items = {item_key(s) for s in regulation.banned_items}
        banned_moves = {slugify(s) for s in regulation.banned_moves}
        mega_count = 0
        species_members: dict[str, list[str]] = {}
        item_members: list[tuple[str, str, str]] = []  # (label, key, display)

        for m in team.members:
            label = m.nickname or m.species
            try:
                slug = await self._names.resolve(m.species)
                mon = await self._api.get_pokemon(slug)
            except Exception as exc:  # noqa: BLE001
                violations.append(
                    Violation(
                        member=label,
                        rule="unknown-species",
                        detail=f"Could not resolve {m.species!r}: {exc}",
                    )
                )
                continue

            base = base_species(slug)
            species_members.setdefault(base, []).append(label)

            if (
                self._roster is not None
                and self._roster.loaded
                and self._roster.verified
                and not self._roster.contains(
                    slug, base, mon.name, mon.types
                )
            ):
                violations.append(
                    Violation(
                        member=label, rule="not-in-champions",
                        detail=f"{mon.name} is not in the Pokémon "
                        "Champions roster (use list_legal_pokemon / "
                        "is_legal_pokemon while building).",
                    )
                )

            if "legendary" in regulation.ban_categories and mon.is_legendary:
                violations.append(
                    Violation(member=label, rule="legendary",
                              detail=f"{mon.name} is a Legendary.")
                )
            if "mythical" in regulation.ban_categories and mon.is_mythical:
                violations.append(
                    Violation(member=label, rule="mythical",
                              detail=f"{mon.name} is a Mythical.")
                )
            if "restricted" in regulation.ban_categories and (
                slug in restricted or base in restricted
            ):
                violations.append(
                    Violation(member=label, rule="restricted",
                              detail=f"{mon.name} is Restricted in {regulation.id}.")
                )
            if slug in banned_species or base in banned_species:
                violations.append(
                    Violation(member=label, rule="banned-species",
                              detail=f"{mon.name} is banned in {regulation.id}.")
                )
            if allowlist is not None and base not in allowlist and slug not in allowlist:
                violations.append(
                    Violation(
                        member=label, rule="not-in-roster",
                        detail=f"{mon.name} is not in the {regulation.id} "
                        f"legal roster.",
                    )
                )

            if regulation.level_cap and m.level > regulation.level_cap:
                violations.append(
                    Violation(member=label, rule="level-cap",
                              detail=f"Level {m.level} exceeds cap "
                              f"{regulation.level_cap}.")
                )

            # --- Item handling: resolution, Champions catalog, bans ---
            is_mega = slug.endswith("-mega") or slug.endswith("-primal")
            item_is_stone = False
            if m.item:
                try:
                    resolved = await self._api.get_item(m.item)
                    item_is_stone = resolved.is_mega_stone
                except Exception:  # noqa: BLE001
                    resolved = None

                cand = {item_key(m.item)}
                if resolved is not None:
                    cand.add(item_key(resolved.slug))
                    cand.add(item_key(resolved.name))
                identity = (
                    item_key(resolved.slug)
                    if resolved is not None
                    else item_key(m.item)
                )
                display = resolved.name if resolved is not None else m.item
                item_members.append((label, identity, display))

                if any(c in banned_items for c in cand):
                    violations.append(
                        Violation(member=label, rule="banned-item",
                                  detail=f"Item {display} is banned in "
                                  f"{regulation.id}.")
                    )

                if self._catalog is not None and self._catalog.loaded:
                    if not any(c in self._catalog.keys for c in cand):
                        if self._catalog.verified:
                            violations.append(
                                Violation(
                                    member=label,
                                    rule="item-not-in-champions",
                                    detail=f"{display} is not a Pokémon "
                                    "Champions item (not in the Champions "
                                    "item catalog).",
                                )
                            )
                        else:
                            warnings.append(
                                Violation(
                                    member=label,
                                    rule="item-maybe-not-in-champions",
                                    detail=f"{display} not found in the "
                                    "(unverified) Champions item catalog; "
                                    "verify in-game.",
                                )
                            )
                elif resolved is None:
                    warnings.append(
                        Violation(member=label, rule="unknown-item",
                                  detail=f"Could not resolve item {m.item!r}.")
                    )
            if is_mega or item_is_stone:
                mega_count += 1
                if not regulation.mega.allowed:
                    violations.append(
                        Violation(member=label, rule="mega",
                                  detail="Mega Evolution is not allowed in "
                                  f"{regulation.id}.")
                    )
                elif regulation.mega.eligible_species is not None:
                    elig = {slugify(s) for s in regulation.mega.eligible_species}
                    if base not in elig:
                        violations.append(
                            Violation(member=label, rule="mega-ineligible",
                                      detail=f"{mon.name} is not Mega-eligible "
                                      f"in {regulation.id}.")
                        )

            for mv in m.moves:
                if slugify(mv) in banned_moves:
                    violations.append(
                        Violation(member=label, rule="banned-move",
                                  detail=f"Move {mv} is banned in "
                                  f"{regulation.id}.")
                    )

            if (
                self._movesets is not None
                and self._movesets.loaded
                and self._movesets.verified
                and m.moves
            ):
                cand_sp = {
                    item_key(slug), item_key(base), item_key(mon.name)
                }
                legal_set = self._movesets.legal_moves(cand_sp)
                if legal_set is not None:
                    for mv in m.moves:
                        if item_key(mv) not in legal_set:
                            violations.append(
                                Violation(
                                    member=label, rule="illegal-move",
                                    detail=f"{mon.name} cannot learn {mv} "
                                    "in Pokémon Champions.",
                                )
                            )

            if m.nature:
                try:
                    normalize_nature(m.nature)
                except StatError as exc:
                    violations.append(
                        Violation(member=label, rule="nature",
                                  detail=str(exc))
                    )
            if m.stat_points:
                chk = validate_spread(m.stat_points)
                for msg in chk.violations:
                    violations.append(
                        Violation(member=label, rule="stat-points",
                                  detail=msg)
                    )

        if regulation.item_clause:
            for key, holders in _dups(
                (k, lbl) for lbl, k, _ in item_members
            ).items():
                disp = next(d for _, k, d in item_members if k == key)
                violations.append(
                    Violation(
                        rule="item-clause",
                        detail=f"{disp} is held by {len(holders)} Pokémon "
                        f"({', '.join(holders)}); each Pokémon must hold a "
                        f"different item.",
                    )
                )

        if regulation.species_clause:
            for base, holders in species_members.items():
                if len(holders) > 1:
                    violations.append(
                        Violation(
                            rule="species-clause",
                            detail=f"{base} appears {len(holders)} times "
                            f"({', '.join(holders)}); each species may be "
                            f"used only once.",
                        )
                    )

        if regulation.mega.allowed:
            if (
                regulation.mega.max_per_team is not None
                and mega_count > regulation.mega.max_per_team
            ):
                violations.append(
                    Violation(rule="mega-count",
                              detail=f"{mega_count} Mega Pokémon; max "
                              f"{regulation.mega.max_per_team} per team.")
                )
            if mega_count > regulation.mega.max_per_battle:
                warnings.append(
                    Violation(
                        rule="mega-battle-limit",
                        detail=f"{mega_count} Mega-capable Pokémon on the team; "
                        f"only {regulation.mega.max_per_battle} may Mega "
                        f"Evolve per battle (legal to bring, manage in-battle).",
                    )
                )

        roster_verified = regulation.roster_verified or (
            self._roster is not None
            and self._roster.loaded
            and self._roster.verified
        )
        if not roster_verified:
            warnings.append(
                Violation(
                    rule="roster-unverified",
                    detail=f"{regulation.id} roster is not machine-verified; "
                    "category/ban checks applied but the exact legal roster "
                    "may differ. Verify online before tournament submission.",
                )
            )

        return ValidationReport(
            legal=len(violations) == 0,
            regulation_id=regulation.id,
            roster_verified=roster_verified,
            violations=violations,
            warnings=warnings,
        )
