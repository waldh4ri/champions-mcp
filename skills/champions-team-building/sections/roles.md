# Role skeletons

Start from one of these, adapt to the concept, and check every slot's brief with
`get_role_description(role, format=…)`. One Pokémon can cover two roles — six
slots do not mean six distinct roles — but every role in the skeleton must be
covered by *someone*.

**VGC (doubles, bring 4)** — the format is decided by speed control and by who
gets to move at all:

1. **Speed control** — `tailwind-setter` *or* `trick-room-setter` *or*
   `speed-drop-support`. Non-negotiable. Strong teams often carry two modes.
2. **Turn denial** — `fake-out-user` and/or `redirection-support`. This is how
   your attackers actually get their click off.
3. **Primary attacker** — `spread-attacker`, `physical-wallbreaker` or
   `special-wallbreaker`, matched to your speed mode (a Trick Room team wants
   `trick-room-abuser`s, not fast breakers).
4. **Secondary attacker** — different offensive type and different defensive
   profile from the first, so one wall can't answer both.
5. **Bulky anchor** — `bulky-attacker` or `intimidate-support`; survives the
   double-target and keeps the board stable.
6. **Flex** — the answer to whatever `analyze_team` flags: an opposing speed
   mode (`disruptor` with Taunt), spread offence (`field-protection-support`
   with Wide Guard), or a specific top-usage threat.

VGC checks a professional always runs:
- **An answer to opposing Fake Out.** Fake Out is how the opponent stops your
  Tailwind or Trick Room turn. Have at least one answer: a Ghost-type setter,
  a Fake Out-blocking ability (confirm in Champions with `get_ability`),
  redirection, or your own Fake Out.
- **A plan against each opposing speed mode.** Into Tailwind: your own
  Tailwind, priority, Icy Wind/Electroweb, or Taunt. Into Trick Room: Taunt,
  Imprison, a Fake Out on the setter, or slow attackers of your own. Into
  weather: a setter that overwrites it, or Pokémon that don't care.
- **Trick Room density.** Two or three real Trick Room abusers plus a mid-speed
  piece that functions outside TR. More than three TR-dependent Pokémon leaves
  the team stranded on non-TR turns.
- **Bring-4 plans.** You bring six and pick four, and each pick is two leads and
  two in the back. Write 3–4 bring plans: a default, plus ones for anti-Tailwind,
  anti-Trick Room and anti-weather or the hardest top-usage matchup. Each needs a
  lead pair and a back pair. Every Pokémon should appear in at least one plan,
  and every plan must still contain its own speed control. If one member is in
  *every* plan and the team falls apart without it, that is a single point of
  failure. Note it, or add redundancy.

**BSS (singles, bring 6, pick 3)** — do **not** build one team of six. You only
ever field three, chosen at team preview against what you can see of their six,
so build **two or three overlapping 3-cores** and treat the six as the union of
them. A "balanced team of 6" that only works when all six are present is a team
that never actually shows up.

Each 3-core must independently satisfy:

1. **A real lead.** The Pokémon you are happy to send out blind on turn one.
2. **A win condition.** The thing that actually closes games in that core.
3. **An answer to the core's own weakness** — whatever beats slots 1 and 2.

Then check the union: overlapping members are good (they earn their slot in
several matchups), but if a Pokémon appears in no viable core, it is a wasted
slot. Aim for cores that answer *different* matchups — a fast-offence core and a
bulkier core, so team preview is a real choice rather than an illusion.

**What makes a real lead** (a strong lead is the most useful Pokémon on turn
one, not the strongest overall). It should do at least one of these on turn one,
without needing information it doesn't have yet:
- **Threaten immediately** — force a switch or a bad trade with strong neutral
  coverage against the format's common openers.
- **Generate momentum** — pivot out (`offensive-pivot`) so your real answer
  enters for free.
- **Establish the mode** — screens, weather, terrain or Trick Room, if your core
  is built on it.

It also needs to be **hard to lose to on turn one**: enough Speed to act first
(or priority instead), and either bulk or a Focus Sash so a bad matchup costs a
switch rather than a Pokémon. Note that bring-3 makes hazard leads much weaker
here than in 6v6 — with only two Pokémon behind, a turn spent on Stealth Rock is
usually better spent attacking. Never build a core whose only lead is something
you would rather not reveal.

Fill the remaining slots from the roles that fit the core's plan — typically
`physical-`/`special-wallbreaker`, a setup sweeper as the win condition,
`revenge-killer` or `priority-attacker` as insurance against opposing setup, and
a `defensive-pivot`/`tank` covering the side your core is soft to.

How a BSS core actually wins:
- **Breakers exist to clear the win condition's path.** List what walls or
  checks your sweeper (from `get_top_teammates` / `get_usage_stats` and the
  calcs). Then give the core a breaker that either *pressures* those checks
  directly or *lures* one with an unexpected coverage move. Confirm the KO with
  `calc_damage`. A sweeper whose checks are all still healthy is not a win
  condition yet.
- **"Sweeper" is a timing, not a species.** The same Pokémon can break early or
  clean late. Decide which it is *in this core*, and don't ask it to do both in
  one game.
- **Momentum is the currency.** With three Pokémon a side, a free switch or a
  forced switch is worth more than in 6v6. Pivots (`offensive-pivot`) and
  safe switch-ins are how a core turns a lost lead matchup into an even one.
- **Revenge-kill insurance.** Each core needs something that stops an opposing
  setup sweeper that got a boost: priority, a faster Scarf user, a phazer, or a
  bulky check that survives the boosted hit. Prove it with `calc_damage`, passing
  the sweeper's boosts as `attacker_boosts`.
- **Team-preview guide.** For each core, say which opposing sixes it is brought
  against (what you see at preview that makes you pick it). That is how you
  prove the cores answer *different* matchups.
