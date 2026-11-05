# Testing a team in simulation

The simulate tools play real battles with Pokémon Showdown's engine and the
Champions mechanics (Stat Points, Megas/Z-Megas, Reg M-C rules), an AI
playing both sides. Hundreds of games take seconds. Use them to *test* a
team the calcs already support, not to replace the calcs.

## Which tool

| Question | Tool |
|---|---|
| How does the team do against the current meta? Which real teams beat it? | `simulate_vs_meta(team, format)` |
| Does this change actually help? | `compare_team_variants(base_team, [variant, ...], format)` |
| How does it do into one specific team? | `simulate_matchup(team_a, team_b, format)` |
| *Why* does a matchup go that way? | `simulate_battle_log(team_a, team_b, format, seed)` |

To replay or head-to-head a matchup from `worst_matchups`, pass that entry's
`id` as `team_b` (e.g. `"report:pokesol.app/..."`). It is the exact team the
simulation played. Never rebuild an opponent from its `label`: the label has
species only, and guessed sets are a different matchup.

Pass complete teams (all six, with moves). A missing spread, nature or
ability is filled with the species' most common one and listed under
`assumptions`, so say so if you rely on it. An illegal team comes back with
`problems`: fix those, don't work around them.

## Reading the results

- **`win_rate` + `win_rate_ci95`**: quote the interval, not just the number.
  Two teams whose intervals overlap are not shown to differ.
- **`worst_matchups`**: real teams (with `label` and `source`) the team loses
  to. In BSS these are mostly top-ladder teams from players' published team
  reports, played with their exact sets. The `source` has the season, final
  rank and the article URL, so you can point the user at the real team. Look for a shared threat across them, then
  confirm it with `calc_damage` before changing anything.
- **`members`**:
  - `brought_pct` near 0: the AI never picks it. It's dead weight in most
    matchups, or a niche answer, so check it has a named job.
  - `faint_rate_when_brought` high with low `kos_per_game_brought`: it trades badly.
  - `win_rate_when_brought` shows who carries the games.
- **`opponent_threats`**: the species that score the most KOs on the team. It
  should line up with the threat map. A surprise here is worth a calc.
- **`delta_vs_base`** (compare): `significant: false` means *no measurable
  difference*, not "better". Only recommend a change that is significant, or
  that is justified by calcs alone.

## Limits

- **The AI is not a top player.** It plays well tactically: it takes KOs,
  doesn't overkill a target, uses Protect when threatened, switches into
  resistances and immunities, pivots with U-turn and friends, and picks leads
  and brings by matchup. It doesn't plan long lines. Win rates are relative to
  this AI, not ladder predictions, so compare teams, don't promise a ladder rating.
- **`information`**: by default (`open_sheets`) each side knows the other's
  moves, items and abilities, but assumes the usual spread and nature. Use
  `closed` to test a team whose strength is surprise (an unusual set). The
  usage-based guess is then wrong, which is the point.
- **Opponent spreads:** each species' most common one, since the
  tournament corpus doesn't record spreads.
- **Budget:** the defaults (600 battles vs meta, 500 head to head) are
  enough. More games only narrow the interval. Don't loop the tools to fish
  for a better number.
