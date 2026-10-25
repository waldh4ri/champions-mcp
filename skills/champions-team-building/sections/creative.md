# Creative team building

The top-6 usage list is the most predictable team in the format — your opponent
has prepared for exactly those Pokémon. A distinctive team is usually a *better*
team, provided every pick is earned. Creativity belongs in the **concept and the
picks**; it never belongs in the **facts**.

Where to be creative:
- **The gameplan.** A clear, unusual mode (a specific weather, a Trick Room
  matchup flip, a two-Mega team with different win conditions per matchup) beats
  six individually-strong Pokémon with no plan.
- **Who fills a role.** Roles are fixed by the format; the *filler* is not.
  `get_usage_stats(format, order="bottom")` ranks every legal Pokémon with no
  cutoff — intersect it with `search_pokemon_by_move("<the role's signature move>")` to
  find real off-meta candidates rather than guessing which are underrated.
- **The angle on a known threat.** An unexpected answer to a top-usage Pokémon
  is worth more than a copied one, because it isn't played around.
- **The contested slots.** `get_observed_sets` marks every decision `locked`,
  `contested` or `fringe`. The contested ones are not unsolved problems — they
  are slots where the right answer genuinely depends on the rest of the team, so
  they are the intended place to express a plan. Its `free_slots` count says how
  many move slots are actually yours once the mandatory ones are spent, and
  `effective_distinct_sets` says how settled the Pokémon is overall: a low
  number means everyone brings the same thing and a deviation will surprise
  people; a high number means the species is already a toolbox.
- **The under-explored partner.** `get_team_context` returns
  `underexplored_partners` — Pokémon with high `affinity` for your exact core
  but low overall usage. These are the strongest creative leads available,
  because they are simultaneously *attested* (they really did appear with this
  core) and *unexpected*.

Creativity has to survive contact with the numbers, though. A deviation is
earned when `calc_damage` or `compute_speed_threshold` shows it hits a benchmark
the standard build misses — not merely because it is unusual. "Nobody plays
this" is a reason to check carefully, not a reason on its own. And an empty
result from the observed corpus means **untested**, never **refuted**: sampling
covers tournaments and the top of the ladder, not everything that works.

Every unconventional pick must clear the same four gates as an obvious one
(`get_role_description` returns them as `creative_pick_gate`):

1. **Legal** — it really learns the move / has the ability *in Champions*
   (`get_pokemon_moves`, `is_legal_move`).
2. **Signature** — its base stats and typing match the role's `look_for`
   (`get_pokemon`).
3. **Benchmark** — it hits the number the role lives on: the Speed tier
   (`compute_speed_threshold`) or the KO/survival roll (`calc_damage`). Most off-meta
   picks fail here — check it before falling in love with the idea.
4. **Relevance** — it does something concrete about a real top-usage threat.

A pick that clears all four is legitimate no matter how low its usage. A pick
that fails one is not creative, it is just worse than the obvious choice — say so
and move on. **Never bend a fact to rescue a fun idea**: if the tools don't
support it, the idea is dead, and reporting that honestly is the correct answer.
