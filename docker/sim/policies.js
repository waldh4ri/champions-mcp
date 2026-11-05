'use strict';
/**
 * Decision policies for both sides of a simulated battle.
 *
 *   random    - uniformly random legal choices (baseline / smoke tests)
 *   greedy    - highest BP x STAB x effectiveness x stat-ratio move, always
 *   heuristic - expected damage from the simulator's own damage function,
 *               KO and speed awareness, joint (both-slot) action selection in
 *               doubles so two attackers don't overkill one target, Protect /
 *               Fake Out / speed control / redirection / setup / status rules,
 *               threat-based switching, and a matchup-scored team preview.
 *
 * Policies read the opponent's full sets. That is the Open Team Sheets
 * reality of VGC Reg M-C; in BSS it makes both sides "informed" players.
 * Every policy returns a Showdown choice string; runBattle falls back to
 * 'default' if a choice is rejected, so a policy bug can't stall a battle.
 */

const { PRNG, toID } = require(process.env.PS_SIM || '/ps/dist/sim');

// ------------------------------------------------------ information model
//
// What a side's AI may know about the opponent while it decides:
//   omniscient  - the true sets, spreads included (upper bound, unrealistic)
//   open_sheets - Open Team Sheets: true species/moves/items/abilities, but
//                 the hidden Stat Point spread and nature are assumed to be
//                 the species' most common ones, and HP is only seen as %
//   closed      - species only (team preview); moves, item and ability are
//                 the format's most-used ones until moves are revealed in
//                 battle (revealed moves replace the guesses)
// Beliefs (per species: spread, nature, moves, item, ability) come from
// champions-mcp's usage data with each request.
//
// The opponent's Pokémon are swapped to the belief view for the duration of
// one decision and restored right after, so the battle itself always runs on
// the real sets.

const INFO_MODES = ['open_sheets', 'closed', 'omniscient'];
const STAT_KEY = {
  hp: 'hp', attack: 'atk', defense: 'def', 'special-attack': 'spa', 'special-defense': 'spd', speed: 'spe',
  atk: 'atk', def: 'def', spa: 'spa', spd: 'spd', spe: 'spe',
};

function beliefFor(view, p) {
  const b = view.beliefs || {};
  return b[p.set.species] || b[p.baseSpecies.name] || b[p.baseSpecies.baseSpecies] || null;
}

/** Swap `side`'s view of the foe to its beliefs; returns the restore function. */
function applyView(battle, side, view) {
  if (!view || !view.mode || view.mode === 'omniscient') return () => {};
  const saved = [];
  for (const p of side.foe.pokemon) {
    const b = beliefFor(view, p);
    if (!b) continue; // no data for this species: nothing better to assume than the truth
    saved.push({ p, storedStats: p.storedStats, maxhp: p.maxhp, hp: p.hp, moveSlots: p.moveSlots, item: p.item, ability: p.ability });
    if (b.spread) {
      const evs = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
      for (const [k, v] of Object.entries(b.spread)) if (STAT_KEY[k]) evs[STAT_KEY[k]] = v;
      const stats = battle.spreadModify(p.species.baseStats, { ...p.set, evs, nature: b.nature || 'Serious' });
      p.storedStats = { atk: stats.atk, def: stats.def, spa: stats.spa, spd: stats.spd, spe: stats.spe };
      const maxhp = p.species.maxHP || stats.hp;
      if (!p.fainted) p.hp = Math.max(1, Math.round(p.hp / p.maxhp * maxhp)); // HP is seen as a percentage
      p.maxhp = maxhp;
    }
    if (view.mode === 'closed') {
      const revealed = p.moveSlots.filter(m => m.used);
      const guesses = (b.moves || []).map(toID).filter(id => id && !revealed.some(m => m.id === id));
      const ids = [...revealed.map(m => m.id), ...guesses].slice(0, 4);
      if (ids.length) {
        p.moveSlots = ids.map(id => revealed.find(m => m.id === id) || (() => {
          const mv = battle.dex.moves.get(id);
          return { id, move: mv.name, pp: 16, maxpp: 16, target: mv.target, disabled: false, used: false };
        })());
      }
      // An item that's gone (eaten, knocked off) is visible; otherwise guess.
      if (p.item && b.item) p.item = toID(b.item);
      // A Mega's ability is fixed by its form, so it's known once it evolves.
      if (b.ability && !p.species.isMega) p.ability = toID(b.ability);
    }
  }
  return () => {
    for (const s of saved) {
      s.p.storedStats = s.storedStats; s.p.maxhp = s.maxhp; s.p.hp = s.hp;
      s.p.moveSlots = s.moveSlots; s.p.item = s.item; s.p.ability = s.ability;
    }
  };
}

// ------------------------------------------------------------------ helpers

const rint = (rng, n) => Math.floor(rng() * n);
const pickTop = (rng, scored, eps) => {
  // argmax, but with probability eps take a random one of the top 3 so the
  // same matchup doesn't replay identically every game.
  scored.sort((a, b) => b.score - a.score);
  if (scored.length > 1 && rng() < eps) return scored[rint(rng, Math.min(3, scored.length))];
  return scored[0];
};

const alive = p => p && !p.fainted && p.hp > 0;
const foesOf = side => side.foe.active.filter(alive);
const SPREAD_TARGETS = new Set(['allAdjacentFoes', 'allAdjacent', 'all', 'foeSide']);
const FOE_TARGETED = new Set(['normal', 'any', 'adjacentFoe']);

function benchIndices(side, used) {
  const out = [];
  for (let i = side.active.length; i < side.pokemon.length; i++) {
    const p = side.pokemon[i];
    if (alive(p) && !used.has(i)) out.push(i);
  }
  return out;
}

// Abilities that make a Pokémon immune to (or absorb) a move type. getDamage
// doesn't run onTryHit, and a benched Pokémon's abilities are ignored by the
// sim, so these immunities would otherwise be invisible to the estimates —
// and they are exactly what a good switch-in exploits.
const TYPE_IMMUNE_ABILITY = {
  levitate: 'Ground', eelevate: 'Ground', eartheater: 'Ground',
  flashfire: 'Fire', wellbakedbody: 'Fire',
  waterabsorb: 'Water', stormdrain: 'Water', dryskin: 'Water',
  voltabsorb: 'Electric', lightningrod: 'Electric', motordrive: 'Electric',
  sapsipper: 'Grass',
};
const MOLD_BREAKERS = new Set(['moldbreaker', 'teravolt', 'turboblaze']);

const abilityId = p => (p.isActive ? p.getAbility().id : String(p.ability || ''));
const itemId = p => String(p.item || '');

/** True if `def` takes nothing from `move` because of its ability or item. */
function blockedByAbilityOrItem(att, def, move) {
  if (move.category === 'Status') return false;
  const ground = move.type === 'Ground' && move.id !== 'thousandarrows';
  if (ground && itemId(def) === 'airballoon') return true;
  if (att && MOLD_BREAKERS.has(abilityId(att))) return false;
  const ab = abilityId(def);
  if (TYPE_IMMUNE_ABILITY[ab] === move.type && (move.type !== 'Ground' || ground)) return true;
  if (ab === 'bulletproof' && move.flags && move.flags.bullet) return true;
  if (ab === 'soundproof' && move.flags && move.flags.sound) return true;
  if (ab === 'windrider' && move.flags && move.flags.wind) return true;
  return false;
}

/** Cheap formula estimate (no battle events): used for team preview and as a fallback. */
function formulaDamage(dex, att, def, moveId) {
  const move = dex.moves.get(moveId);
  if (!move.exists || move.category === 'Status' || !move.basePower) return 0;
  if (!dex.getImmunity(move.type, def.species ? def : { types: def.types })) return 0;
  if (blockedByAbilityOrItem(att, def, move)) return 0;
  const eff = Math.pow(2, dex.getEffectiveness(move.type, def.species ? def.getTypes() : def.types));
  const stab = att.getTypes ? (att.getTypes().includes(move.type) ? 1.5 : 1) : 1;
  const a = move.category === 'Physical' ? att.storedStats.atk : att.storedStats.spa;
  const d = move.category === 'Physical' ? def.storedStats.def : def.storedStats.spd;
  const base = Math.floor(Math.floor(22 * move.basePower * a / d) / 50) + 2;
  const acc = move.accuracy === true ? 1 : move.accuracy / 100;
  return base * stab * eff * 0.925 * acc;
}

/**
 * Expected damage of `moveId` from `att` to `def`, computed with the
 * simulator's own getDamage (abilities, items, weather, terrain, boosts,
 * screens, Mega forms all apply) under a sandbox:
 *   - a scratch PRNG, so evaluating never advances the battle's RNG stream;
 *   - the damage roll pinned to its average and crits off;
 *   - item consumption disabled (resist berries are eaten inside the damage
 *     pipeline) and log lines written during evaluation discarded.
 * Ability/item immunities (Levitate, absorb abilities, Air Balloon...) are
 * applied on top, since getDamage skips onTryHit. Multi-hit moves are scaled
 * by their expected hit count, and the result by accuracy. Works on benched
 * Pokémon too (used to judge switch-ins); falls back to the plain formula if
 * the simulator throws.
 */
function simDamage(battle, att, def, moveId, spreadHit) {
  const dexMove = battle.dex.moves.get(moveId);
  if (!dexMove.exists || dexMove.category === 'Status') return 0;
  if (blockedByAbilityOrItem(att, def, dexMove)) return 0;
  const saved = {
    prng: battle.prng, randomizer: battle.randomizer, activeMove: battle.activeMove,
    activePokemon: battle.activePokemon, activeTarget: battle.activeTarget, logLen: battle.log.length,
    attEat: att.eatItem, attUse: att.useItem, defEat: def.eatItem, defUse: def.useItem,
  };
  try {
    battle.prng = new PRNG([7, 7, 7, 7]);
    battle.randomizer = d => battle.trunc(battle.trunc(d * 92) / 100);
    const no = () => false;
    att.eatItem = no; att.useItem = no; def.eatItem = no; def.useItem = no;
    const m = battle.dex.getActiveMove(moveId);
    m.willCrit = false;
    if (spreadHit) m.spreadHit = true;
    battle.activeMove = m; battle.activePokemon = att; battle.activeTarget = def;
    let d = battle.actions.getDamage(att, def, m, true);
    if (typeof d !== 'number' || !(d > 0)) return 0;
    if (m.multihit) {
      if (Array.isArray(m.multihit)) {
        d *= m.multihit[0] === 2 && m.multihit[1] === 5 ? (att.hasItem('loadeddice') ? 4.5 : 3.1) : (m.multihit[0] + m.multihit[1]) / 2;
      } else d *= m.multihit;
    }
    const acc = m.accuracy === true ? 1 : Math.min(1, m.accuracy / 100);
    return d * acc;
  } catch (e) {
    return formulaDamage(battle.dex, att, def, moveId);
  } finally {
    battle.prng = saved.prng; battle.randomizer = saved.randomizer;
    battle.activeMove = saved.activeMove; battle.activePokemon = saved.activePokemon; battle.activeTarget = saved.activeTarget;
    att.eatItem = saved.attEat; att.useItem = saved.attUse; def.eatItem = saved.defEat; def.useItem = saved.defUse;
    battle.log.length = saved.logLen;
  }
}

/**
 * Effective speed for turn order, for any Pokémon (active or benched): boosts,
 * Choice Scarf, paralysis and Tailwind; negated under Trick Room so "bigger
 * acts first" holds either way.
 */
function speedOf(p) {
  if (p.isActive) {
    try { return p.getActionSpeed(); } catch (e) { /* fall through */ }
  }
  let s = p.storedStats.spe;
  if (itemId(p) === 'choicescarf') s *= 1.5;
  if (p.status === 'par') s *= 0.5;
  if (p.side.sideConditions['tailwind']) s *= 2;
  return p.battle.field.pseudoWeather['trickroom'] ? -s : s;
}

/** Does `att` using `moveId` act before `def` (priority, then speed; Trick Room aware)? */
function movesFirst(battle, att, moveId, def) {
  const m = battle.dex.moves.get(moveId);
  let pri = m.priority || 0;
  if (m.category === 'Status' && abilityId(att) === 'prankster') pri += 1;
  if (pri > 0) return true;
  if (pri < 0) return false;
  return speedOf(att) > speedOf(def);
}

/** Best (moveId, expected damage) of `att` against `def`. */
function bestMove(battle, att, def) {
  let best = { id: null, d: 0 };
  for (const ms of att.moveSlots) {
    if (ms.pp <= 0 || ms.disabled) continue;
    const d = simDamage(battle, att, def, ms.id, false);
    if (d > best.d) best = { id: ms.id, d };
  }
  return best;
}

/**
 * Threat assessment for each of our active Pokémon:
 *   threat   biggest expected hit any foe has on it / its current HP
 *   koFirst  a foe KOs it *before it acts* (faster, or priority)
 *   bestOut  its best expected hit on any foe / that foe's HP ("walled" if small)
 * plus `predicted`: for each foe, the move it most likely uses and into which
 * of our slots (its biggest hit by %HP) — the hit a switch-in would take.
 */
function assess(battle, side) {
  const out = { slots: new Map(), predicted: [] };
  const mine = side.active.map((p, slot) => ({ p, slot })).filter(x => alive(x.p));
  const blank = () => ({ threat: 0, koFirst: false, bestOut: 0, koBy: [] });
  for (const foe of foesOf(side)) {
    let pick = null;
    for (const { p, slot } of mine) {
      const b = bestMove(battle, foe, p);
      const frac = p.hp ? b.d / p.hp : 0;
      if (!pick || frac > pick.frac) pick = { foe, slot, moveId: b.id, frac };
      const s = out.slots.get(slot) || blank();
      s.threat = Math.max(s.threat, frac);
      if (b.id && frac >= 1 && movesFirst(battle, foe, b.id, p)) { s.koFirst = true; s.koBy.push(foe); }
      out.slots.set(slot, s);
    }
    if (pick && pick.moveId) out.predicted.push(pick);
  }
  for (const { p, slot } of mine) {
    const s = out.slots.get(slot) || blank();
    for (const foe of foesOf(side)) {
      s.bestOut = Math.max(s.bestOut, Math.min(1.5, bestMove(battle, p, foe).d / Math.max(1, foe.hp)));
    }
    out.slots.set(slot, s);
  }
  return out;
}

/**
 * How good is bench Pokémon `cand` coming in for `slot` right now?
 *   absorb   the predicted hit(s) aimed at this slot, as a fraction of cand's
 *            HP (0 = resisted into nothing / immune: the ideal pivot)
 *   followUp the biggest hit any foe then has on it next turn
 *   offense  its best hit on a foe afterwards, and whether it outspeeds them
 * `free` = replacing a fainted Pokémon (nothing to absorb this turn).
 */
function switchEval(battle, side, slot, cand, a, free) {
  let absorb = 0, followUp = 0, offense = 0, faster = true;
  for (const foe of foesOf(side)) {
    if (!free) {
      for (const pr of a.predicted) {
        if (pr.foe === foe && pr.slot === slot) absorb += simDamage(battle, foe, cand, pr.moveId, false) / Math.max(1, cand.hp);
      }
    }
    followUp = Math.max(followUp, bestMove(battle, foe, cand).d / Math.max(1, cand.hp));
    offense = Math.max(offense, Math.min(1.2, bestMove(battle, cand, foe).d / Math.max(1, foe.hp)));
    if (speedOf(foe) >= speedOf(cand)) faster = false;
  }
  return { absorb, followUp, offense, faster };
}

/**
 * Value of switching `slot`'s active into `cand`, in the same units as move
 * scores (~100 per foe-HP removed). A switch only wins when staying is bad —
 * the active dies before acting, takes a huge hit, or is walled — AND the
 * switch-in takes the predicted hit well (resist/immunity) and threatens
 * back. Losing the turn, discarding boosts and ping-ponging all cost.
 */
function switchScore(battle, side, slot, cand, a, free) {
  const mon = side.active[slot];
  const s = a.slots.get(slot) || { threat: 0, koFirst: false, bestOut: 1 };
  const e = switchEval(battle, side, slot, cand, a, free);
  let v = 0;
  if (!free && mon) {
    if (s.koFirst) v += 55;                       // would faint before acting
    else if (s.threat >= 0.6) v += 30 * Math.min(1, s.threat);
    if (s.bestOut < 0.15) v += 30;                // walled: nothing it does matters
    else if (s.bestOut < 0.3) v += 12;
    v -= 32;                                      // the turn spent switching
    const boosts = Object.values(mon.boosts).reduce((x, b) => x + Math.max(0, b), 0);
    v -= 12 * boosts;                             // switching throws away boosts
    if (mon.activeTurns <= 1) v -= 10;            // don't ping-pong
    v -= 70 * Math.min(1, e.absorb) + (e.absorb >= 1 ? 40 : 0);
    if (e.absorb === 0 && a.predicted.some(p => p.slot === slot)) v += 18; // immune / resisted to nothing
  }
  v += 40 * Math.min(1, e.offense) + (e.faster ? 12 : 0);
  v -= 30 * Math.min(1, e.followUp) + (e.followUp >= 1 && !e.faster ? 15 : 0);
  return v;
}

// ------------------------------------------------------- option generation

/** Every legal action for one active slot, as {kind, choice, ...}. */
function slotOptions(battle, side, slot, act, used) {
  const mon = side.active[slot];
  const opts = [];
  const doubles = side.active.length > 1;
  // Locked into a move (second turn of Solar Beam / Electro Shot, Outrage,
  // recharge...): the target is already fixed and naming one is rejected.
  const locked = mon.getLockedMove && mon.getLockedMove();
  if (locked) {
    const move = battle.dex.moves.get(locked);
    const foes = foesOf(side);
    return [{ kind: 'move', id: move.id, move, choice: 'move 1', target: foes[0] || null,
      targets: move.category === 'Status' ? [] : foes.slice(0, SPREAD_TARGETS.has(move.target) ? 2 : 1), spread: SPREAD_TARGETS.has(move.target) }];
  }
  act.moves.forEach((m, i) => {
    if (m.disabled || (m.pp !== undefined && m.pp <= 0 && m.id !== 'struggle')) return;
    const move = battle.dex.moves.get(m.id);
    const t = m.target || move.target;
    const base = `move ${i + 1}`;
    if (doubles && FOE_TARGETED.has(t)) {
      const foes = side.foe.active.map((p, j) => ({ p, j })).filter(x => alive(x.p));
      if (!foes.length) foes.push({ p: side.foe.active[0], j: 0 });
      for (const { p, j } of foes) opts.push({ kind: 'move', id: m.id, move, choice: `${base} ${j + 1}`, target: p, targets: [p] });
    } else if (doubles && t === 'adjacentAlly') {
      const ally = side.active[1 - slot];
      if (alive(ally)) opts.push({ kind: 'move', id: m.id, move, choice: `${base} -${2 - slot}`, target: ally, targets: [] });
    } else if (doubles && t === 'adjacentAllyOrSelf') {
      opts.push({ kind: 'move', id: m.id, move, choice: `${base} -${slot + 1}`, target: mon, targets: [] });
    } else {
      // singles, or an untargeted doubles move (spread, self, randomNormal...)
      const foes = foesOf(side);
      const spread = SPREAD_TARGETS.has(t);
      const aimed = spread || FOE_TARGETED.has(t) || t === 'randomNormal';
      const targets = move.category === 'Status' || !aimed ? [] : spread ? foes : foes.slice(0, 1);
      opts.push({ kind: 'move', id: m.id, move, choice: base, target: aimed ? foes[0] || null : null, targets, spread });
    }
  });
  if (!act.trapped && !act.maybeTrapped) {
    for (const i of benchIndices(side, used)) opts.push({ kind: 'switch', idx: i, choice: `switch ${i + 1}` });
  }
  if (act.canMegaEvo) for (const o of opts) if (o.kind === 'move') o.choice += ' mega';
  return opts;
}

// ----------------------------------------------------------- team preview

/**
 * Team-preview value of each of our six: average offensive pressure on their
 * six minus the pressure they put on it, plus a support-role bonus. Damage
 * alone never brings a Tailwind setter or a Fake Out user, which is exactly
 * what doubles teams are built around, so roles count for bring choice in
 * doubles and weigh extra for leads.
 */
/**
 * How a Pokémon fights, for team preview: a Mega Stone holder as its Mega
 * (stats, typing, ability), since that's the form that will do the work —
 * judging Lucario by its base form badly undersells Mega Lucario Z.
 */
function previewForm(battle, p) {
  const item = battle.dex.items.get(p.item);
  const megaName = item.megaStone && (item.megaStone[p.species.name] || item.megaStone[p.baseSpecies.name]);
  const mega = megaName && battle.dex.species.get(megaName);
  if (!mega || !mega.exists) return p;
  const st = battle.spreadModify(mega.baseStats, p.set);
  return {
    species: mega, storedStats: st, maxhp: p.maxhp, hp: p.hp, moveSlots: p.moveSlots, item: p.item,
    ability: toID(mega.abilities[0]), isActive: false, side: p.side, battle: p.battle, status: p.status,
    boosts: p.boosts, getTypes: () => mega.types, getAbility: () => battle.dex.abilities.get(mega.abilities[0]),
  };
}

function previewScores(battle, side, lead) {
  const dex = battle.dex;
  const doubles = side.active.length > 1;
  const mine = side.pokemon.map(p => previewForm(battle, p));
  const theirs = side.foe.pokemon.map(p => previewForm(battle, p));
  const off = (a, b) => {
    let best = 0;
    for (const ms of a.moveSlots) best = Math.max(best, formulaDamage(dex, a, b, ms.id) / b.maxhp);
    return Math.min(best, 1.2) * (a.storedStats.spe > b.storedStats.spe ? 1.15 : 1);
  };
  return mine.map((p, i) => {
    let o = 0, d = 0;
    for (const q of theirs) { o += off(p, q); d += off(q, p); }
    let s = o / theirs.length - 0.7 * d / theirs.length;
    const ids = new Set(p.moveSlots.map(m => m.id));
    const ability = p.getAbility().id;
    let role = 0;
    if (ids.has('tailwind') || ids.has('trickroom')) role += 0.25;
    if (ids.has('fakeout')) role += 0.12;
    if (ids.has('followme') || ids.has('ragepowder')) role += 0.15;
    if (ability === 'intimidate') role += 0.1;
    if (ability === 'prankster' && p.moveSlots.some(m => dex.moves.get(m.id).category === 'Status')) role += 0.08;
    if (ids.has('reflect') || ids.has('lightscreen') || ids.has('auroraveil')) role += 0.08;
    if (doubles) s += lead ? role * 1.4 : role;
    else s += role * 0.3;
    return { i, s };
  });
}

function teamPreview(policy, battle, side, req, rng) {
  const n = req.maxChosenTeamSize || side.pokemon.length;
  const order = side.pokemon.map((_, i) => i);
  if (policy === 'random' || policy === 'greedy') {
    for (let i = order.length - 1; i > 0; i--) { const j = rint(rng, i + 1); [order[i], order[j]] = [order[j], order[i]]; }
    return 'team ' + order.slice(0, n).map(i => i + 1).join('');
  }
  const jitter = () => (rng() - 0.5) * 0.12;
  const bring = previewScores(battle, side, false).map(x => ({ ...x, s: x.s + jitter() }))
    .sort((a, b) => b.s - a.s).slice(0, n).map(x => x.i);
  const leadScore = new Map(previewScores(battle, side, true).map(x => [x.i, x.s + jitter()]));
  bring.sort((a, b) => leadScore.get(b) - leadScore.get(a));
  return 'team ' + bring.map(i => i + 1).join('');
}

// ------------------------------------------------------------ forced switch

function forcedSwitch(policy, battle, side, req, rng) {
  const used = new Set();
  return req.forceSwitch.map((must, slot) => {
    if (!must) return 'pass';
    const opts = benchIndices(side, used);
    if (!opts.length) return 'pass';
    let pick = opts[rint(rng, opts.length)];
    if (policy === 'heuristic') {
      const a = assess(battle, side);
      const scored = opts.map(i => ({
        i, score: switchScore(battle, side, slot, side.pokemon[i], a, true) + 10 * side.pokemon[i].hp / side.pokemon[i].maxhp,
      }));
      pick = pickTop(rng, scored, 0.05).i;
    }
    used.add(pick);
    return `switch ${pick + 1}`;
  }).join(', ');
}

// ---------------------------------------------------------- move decisions

const PROTECT = new Set(['protect', 'detect', 'spikyshield', 'kingsshield', 'banefulbunker', 'silktrap', 'burningbulwark', 'obstruct']);
const SETUP_BOOST = 2;

/** Non-damage value of a status (or utility) move for this slot. */
function statusValue(battle, side, mon, opt, threat, ctx) {
  const id = opt.id, move = opt.move, field = battle.field, foes = foesOf(side);
  if (PROTECT.has(id)) {
    const stall = mon.volatiles['stall'] ? 1 / 3 : 1;
    return (threat >= 0.5 ? 45 : threat >= 0.3 ? 15 : 3) * stall * (side.active.length > 1 ? 1 : 0.6);
  }
  if (id === 'fakeout' || id === 'firstimpression') return mon.activeMoveActions === 0 ? 0 : -100; // damage added separately
  if (id === 'tailwind') return side.sideConditions['tailwind'] ? -50 : (ctx.slower ? 55 : 20);
  if (id === 'trickroom') return field.pseudoWeather['trickroom'] ? -80 : (ctx.slower ? 60 : -30);
  if (id === 'followme' || id === 'ragepowder') return side.active.length > 1 && ctx.allyThreat >= 0.6 ? 35 : 5;
  if (id === 'helpinghand') return side.active.length > 1 && ctx.allyAttacks ? 18 : -10;
  if (id === 'reflect' || id === 'lightscreen' || id === 'auroraveil') {
    if (side.sideConditions[id]) return -50;
    if (id === 'auroraveil' && !['snowscape', 'hail'].includes(field.weather)) return -50;
    return 28;
  }
  if (move.weather) return field.weather === move.weather ? -50 : 22;
  if (move.terrain) return field.terrain === move.terrain ? -50 : 20;
  if (move.target === 'self' && move.boosts) {
    const cur = Math.max(...Object.keys(move.boosts).map(k => mon.boosts[k] || 0));
    return threat < 0.5 && cur < SETUP_BOOST ? 30 : 2;
  }
  if (move.heal || move.flags.heal) return threat < 0.6 ? (1 - mon.hp / mon.maxhp) * 70 : 5;
  if (move.status && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if (t.status || !battle.dex.getImmunity(move.status, t)) return -30;
    if (move.status === 'slp') return foes.some(f => f.status === 'slp') ? -10 : 45;
    if (move.status === 'brn') return t.storedStats.atk > t.storedStats.spa ? 35 : 10;
    if (move.status === 'par') return speedOf(t) > speedOf(mon) ? 30 : 12;
    return 22;
  }
  if (id === 'taunt' || id === 'encore') return 12;
  return 6;
}

/**
 * Score every option of one slot. Damage options carry per-target expected
 * damage so the joint step can apply them in order and see overkill.
 */
function scoreSlot(battle, side, slot, opts, a, ctx) {
  const mon = side.active[slot];
  const st = a.slots.get(slot) || { threat: 0, koFirst: false, bestOut: 1, koBy: [] };
  const threat = st.threat;
  const doubles = side.active.length > 1;
  // Best switch available (used to value pivot moves: U-turn, Volt Switch,
  // Flip Turn, Parting Shot get the switch without spending the turn on it).
  let bestSwitch = -Infinity;
  for (const o of opts) {
    if (o.kind !== 'switch') continue;
    o.base = switchScore(battle, side, slot, side.pokemon[o.idx], a, false);
    o.hits = []; o.slot = slot;
    bestSwitch = Math.max(bestSwitch, o.base);
  }
  for (const o of opts) {
    if (o.kind === 'switch') continue;
    o.slot = slot;
    o.hits = [];
    // Outsped and KO'd by a foe: this action likely never happens (in
    // doubles that foe may target the partner instead, so only halve it).
    const acts = !st.koFirst || st.koBy.every(f => movesFirst(battle, mon, o.id, f));
    const w = acts ? 1 : (doubles ? 0.5 : 0.15);
    let base = 0;
    if (o.move.selfSwitch && bestSwitch > -Infinity && acts) {
      base += 0.6 * Math.max(0, bestSwitch + 32); // the switch, minus the lost-turn cost
    }
    if (o.move.category !== 'Status') {
      const spread = o.spread && o.targets.length > 1;
      for (const t of o.targets) {
        const d = simDamage(battle, mon, t, o.id, spread);
        if (d > 0) o.hits.push({ t, d, w, first: movesFirst(battle, mon, o.id, t) });
      }
      // spread moves that also hit the partner (Earthquake, Surf, Discharge...)
      if (o.move.target === 'allAdjacent' && side.active.length > 1) {
        const ally = side.active[1 - slot];
        if (alive(ally)) {
          const d = simDamage(battle, mon, ally, o.id, true);
          if (d > 0) base -= 110 * Math.min(d, ally.hp) / ally.maxhp + (d >= ally.hp ? 80 : 0);
        }
      }
      if (o.id === 'fakeout') base += mon.activeMoveActions === 0 ? 45 : -200;
      if (o.move.self && o.move.self.boosts && Object.values(o.move.self.boosts).some(v => v < 0)) base -= 4;
      if (o.move.recoil || o.move.mindBlownRecoil) base -= 5;
      if (o.move.flags && o.move.flags.charge && !mon.volatiles['twoturnmove']) {
        const wx = battle.field.effectiveWeather();
        const instant = mon.hasItem('powerherb') ||
          (['solarbeam', 'solarblade'].includes(o.id) && ['sunnyday', 'desolateland'].includes(wx)) ||
          (o.id === 'electroshot' && ['raindance', 'primordialsea'].includes(wx));
        if (!instant) base -= 25; // a turn of exposure before the hit lands
      }
      if (o.move.secondary && o.move.secondary.boosts && o.move.secondary.chance === 100 && o.move.secondary.boosts.spe < 0) base += ctx.slower ? 10 : 3;
    } else {
      const sv = statusValue(battle, side, mon, o, threat, ctx);
      base += PROTECT.has(o.id) ? sv : sv * w;
    }
    o.base = base;
  }
}

/** Value of a set of damage hits applied in order against a running HP map. */
function applyHits(hpLeft, hits) {
  let v = 0;
  for (const h of hits) {
    const key = h.t.side.n + ':' + h.t.position;
    const left = hpLeft.has(key) ? hpLeft.get(key) : h.t.hp;
    if (left <= 0) continue;
    const w = h.w === undefined ? 1 : h.w;
    const dealt = Math.min(h.d, left);
    v += w * 100 * dealt / h.t.maxhp;
    if (h.d >= left) v += w * (h.first ? 60 : 30);
    if (w >= 0.5) hpLeft.set(key, left - dealt);
  }
  return v;
}

function heuristicTurn(battle, side, req, rng) {
  const used = new Set();
  const n = side.active.length;
  const a = assess(battle, side);
  const mine = side.active.filter(alive), foes = foesOf(side);
  const avg = arr => arr.reduce((a, p) => a + Math.abs(p.storedStats.spe), 0) / Math.max(1, arr.length);
  const ctx = {
    slower: avg(mine) < avg(foes),
    allyThreat: 0, allyAttacks: true,
  };

  const perSlot = [];
  for (let slot = 0; slot < n; slot++) {
    const act = req.active[slot];
    const mon = side.active[slot];
    if (!act || !alive(mon) || act.commanding) { perSlot.push(null); continue; }
    ctx.allyThreat = (a.slots.get(1 - slot) || {}).threat || 0;
    const opts = slotOptions(battle, side, slot, act, used);
    scoreSlot(battle, side, slot, opts, a, ctx);
    // keep the best few per slot (by standalone value) for the joint search
    const hpTmp = new Map();
    for (const o of opts) o.solo = o.base + applyHits(new Map(hpTmp), o.hits);
    opts.sort((a, b) => b.solo - a.solo);
    perSlot.push(opts.slice(0, 6));
  }

  // joint selection: both slots evaluated together, hits applied fastest first
  const live = perSlot.map((o, i) => (o && o.length ? i : -1)).filter(i => i >= 0);
  let combos = [[]];
  for (const slot of live) {
    const next = [];
    for (const c of combos) for (const o of perSlot[slot]) {
      if (o.kind === 'switch' && c.some(x => x.kind === 'switch' && x.idx === o.idx)) continue;
      next.push([...c, o]);
    }
    combos = next;
  }
  const scored = combos.map(c => {
    const hpLeft = new Map();
    const order = [...c].sort((a, b) => speedOf(side.active[b.slot]) - speedOf(side.active[a.slot]));
    let s = 0;
    for (const o of order) s += o.base + applyHits(hpLeft, o.hits);
    // two Protects on one turn wastes the whole turn unless both are in danger
    if (c.length === 2 && c.every(o => o.kind === 'move' && PROTECT.has(o.id))) s -= 30;
    // only one Mega per battle: keep the flag on one slot only
    return { c, score: s };
  });
  const best = pickTop(rng, scored, 0.08).c;
  let megaUsed = false;
  const bySlot = new Map(best.map(o => [o.slot, o]));
  const parts = [];
  for (let slot = 0; slot < n; slot++) {
    const o = bySlot.get(slot);
    if (!o) { parts.push('pass'); continue; }
    let ch = o.choice;
    if (ch.endsWith(' mega')) {
      if (megaUsed) ch = ch.slice(0, -5);
      megaUsed = true;
    }
    parts.push(ch);
  }
  return parts.join(', ');
}

function simpleTurn(policy, battle, side, req, rng) {
  const used = new Set();
  let megaUsed = false;
  return req.active.map((act, slot) => {
    const mon = side.active[slot];
    if (!act || !alive(mon) || act.commanding) return 'pass';
    const opts = slotOptions(battle, side, slot, act, used);
    if (!opts.length) return 'pass';
    let o;
    if (policy === 'greedy') {
      const moves = opts.filter(x => x.kind === 'move');
      o = moves.length ? pickTop(rng, moves.map(x => ({
        ...x, score: x.target && x.target.side !== side ? formulaDamage(battle.dex, mon, x.target, x.id) : (x.move.category === 'Status' ? 5 : 0),
      })), 0.1) : opts[0];
    } else {
      const moves = opts.filter(x => x.kind === 'move');
      o = moves.length && rng() < 0.85 ? moves[rint(rng, moves.length)] : opts[rint(rng, opts.length)];
    }
    if (o.kind === 'switch') used.add(o.idx);
    let ch = o.choice;
    if (ch.endsWith(' mega')) { if (megaUsed) ch = ch.slice(0, -5); megaUsed = true; }
    return ch;
  }).join(', ');
}

/**
 * Entry point: the choice string for `side`'s pending request.
 * `view` = {mode, beliefs} sets what this side knows about the opponent.
 */
function choose(policy, battle, side, rng, view) {
  const req = side.activeRequest;
  if (!req || req.wait) return null;
  const restore = policy === 'random' ? () => {} : applyView(battle, side, view);
  try {
    if (req.teamPreview) return teamPreview(policy, battle, side, req, rng);
    if (req.forceSwitch) return forcedSwitch(policy, battle, side, req, rng);
    if (policy === 'heuristic') return heuristicTurn(battle, side, req, rng);
    return simpleTurn(policy, battle, side, req, rng);
  } finally {
    restore();
  }
}

const POLICIES = ['random', 'greedy', 'heuristic'];

module.exports = { choose, POLICIES, INFO_MODES, applyView, simDamage, formulaDamage };
