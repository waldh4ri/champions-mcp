# Champions pitfalls (check, don't trust memory)

- **Moves**: SV learnsets are wrong here. Confirm via `get_pokemon_moves`.
  Notably absent: Teleport, Aromatherapy, Court Change, Spore, Tail Glow.
- **Items**: call `list_items` once — if an item is not in it, it does not exist
  here. The Champions item pool is much smaller than other games'.
  **Choice Scarf is the only Choice item** — there is no Choice Band or Specs —
  and there is no Assault Vest, Heavy-Duty Boots, Covert Cloak or Safety
  Goggles. Bulk and power must come from Stat Points, typing and abilities
  instead. Confirm with `search_items` before planning around any item.
- **Stats**: Stat Points (66/32, 1 SP = +1), not 252-EV math.
- **Protect is a doubles move.** In VGC it is near-mandatory (250 of 268 species
  run it, many above 90%). In BSS it is usually a wasted turn — only 48 of 268
  species run it at all, 15 of the top 60. With one Pokémon per side it buys no
  partner action, and a wasted turn in a 3-Pokémon game is free setup for the
  opponent. Put it on a BSS set only for a concrete reason: banking a Speed
  Boost-style accumulating turn, scaling passive damage behind reliable
  recovery, scouting a Choice lock, or triggering a turn-based item. `analyze_team`
  flags it on singles teams — justify each one or cut it.
- **Listed stats are Lv-50 values at 0 SP**, not classic base stats (roster Speed
  runs 40-162, median ~98). Final stat = listed + SP, then nature — never run a
  level formula on them; `calc_stats` / `compute_speed` do it for you. `get_pokemon` returns
  `speed_context` with the roster percentile: a species in the slowest ~10% is a
  Trick Room piece (setter or abuser) unless it is slow *on purpose* — a slow
  pivot, Baton Passer, Gyro Ball user, or a wall that never races. Slow with no
  plan just means moving second every turn; invest in bulk, not in Speed it
  cannot win with.
- **Regional forms**: share a name with the base and differ by typing — request
  them explicitly (e.g. "Alolan Ninetales") and trust the tool's typing.
- **Megas**: some are Champions-original; capped at one active per battle.
  `calc_damage` auto-Mega-evolves from the held stone.
- **Format mixing**: doubles has spread damage ×0.75 and Tailwind as a core
  mechanic; singles does not. Keep `game_type` / `format` matched to the session.
