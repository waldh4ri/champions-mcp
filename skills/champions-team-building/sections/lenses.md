# Think like a professional builder

A strong player judges a team on several separate lenses. Run each one
explicitly. A team that passes legality and has good parts can still fail one of
these, and that failure is what loses games.

**1. One plan, stated in one sentence.** Before choosing slot 3, write the plan:
- VGC: *the ideal first three turns* ("Fake Out + Tailwind turn 1, double into the
  biggest threat turns 2–3, Mega closes").
- BSS: *lead → how it gains the advantage → what closes*.

If the plan doesn't fit in a sentence, the team is asking too much of each
slot. Every support piece must serve the **same** endgame as the attackers. Fake
Out and Tailwind are worthless if the attackers can't convert the turns they
buy.

**2. Synergy (degree, not yes/no).** Judge every pair on two axes:
- *Offensive synergy*: partners remove **each other's checks**. A's best answer
  is something B threatens, so stopping one costs the opponent what it needs to
  stop the other. This matters more than type complementarity. An offensive
  core without it tends to fail.
- *Defensive synergy*: **switchability**. Can one switch into the attacks aimed at
  the other without losing much? Typing is part of this, but bulk and recovery
  count too. A frail Pokémon with good resistances still can't switch in.
  Complementary bulk (physically bulky + specially bulky) is a real pairing.
- In doubles, a resistance protects only one side. The opponent can simply target
  the partner, so team-wide tools (Intimidate, screens, redirection) carry extra
  weight. Redirection and setup **enable** a partner. They are not synergy in
  themselves.

**3. Anti-synergy (check every pair).** These are common and silent:
- two weather setters, or two terrain setters, overwriting each other
- a terrain or ability that blocks your own plan: Psychic Terrain stops your
  own priority and Prankster; a status-blocking terrain kills your own
  sleep/paralysis
- partners whose spread moves hit each other without an immunity or Protect plan
- stacked weaknesses (see 4)

Verify every ability and terrain interaction with `get_ability` / `get_move`.
Champions may differ.

**4. Shared-weakness rule.** No single attacking type should hit **3 or more
of the 6** (or 2 of a BSS 3-core) super-effectively with no resist or immunity
covering it. One attacker would then threaten most of the team. `analyze_team`
reports it as `shared_weaknesses` (battle typing, Mega typing when the member
holds its stone, and listed abilities such as Levitate). Weigh each flagged type
by how much of it the top threats carry (`get_threat_profile` →
`threat_stab_types`).

**5. Consistency.** Prefer tools that work **even when the opponent knows your
team**: Fake Out, Taunt, Intimidate, reliable speed control, accurate STABs. A
*gimmick* exploits unpreparedness and fades in best-of-three and against strong
players. Keep it as an angle, never as the whole plan. Plans that hinge on a
75%-accurate move, a flinch or a crit are coin flips. Avoid making the team's
key turn depend on one.

**6. Pacing and archetype.** Know which one you are building, and make the
pieces agree:
- *Hyper offence*: fast, frail, overwhelming; ~≤8 turns. Strong in Bo1 and in
  unsettled metas, but collapses once it loses position.
- *Bulky offence*: good defensive typing and stats, several speed-control or
  disruption options, no loss of damage; ~8–12 turns. This is the most
  consistent style and the most common among top tournament teams. It is the
  default unless the user wants otherwise.
- *Defence / stall*: recovery, Will-O-Wisp/Snarl/screens; long games, passive,
  timer risk. It rarely tops events.

A frail breaker on a slow positional team, or a wall on hyper offence, is a
pacing mismatch.

**7. Breadth and relevance.** *Breadth*: is there a backup route when the main
plan is stopped (the setter is KO'd, Taunted or Faked Out)? *Relevance*: how
does the team do against what the meta actually brings? That is the
`get_usage_stats` threat list, not hypothetical opponents.

**8. Every slot earns its place.** Slots 4–6 especially need a *job*, not an
excuse. For each one, name **which phase it improves (lead, switch, or
endgame)** and **the specific matchup or board state it is for**. A sixth
Pokémon that is brought into one hard matchup with a clear purpose beats a
"flexible" one that never gets brought. Cut a slot that overlaps another
without adding a new matchup answer.

**9. Bulk and items must change a turn.** Invest bulk only when surviving the
hit changes what happens next. Examples: Tailwind survives to be set, a
double-target is denied, an attacker gets one more action. Every item likewise
needs a stated purpose (a KO it secures, a hit it survives, a Speed tier it
reaches) backed by `calc_damage` / `compute_speed_threshold`.
