'use strict';
/**
 * Decision policies for both sides of a simulated battle.
 *
 *   random    - uniformly random legal choices (baseline / smoke tests)
 *   greedy    - highest BP x STAB x effectiveness x stat-ratio move, always
 *   heuristic - expected damage from the simulator's own damage function
 *               (with ModifyType/ModifyMove, bench ability/item activation,
 *               per-turn memoization, and in-battle Mega projection),
 *               KO/priority/speed awareness, interactive joint (both-slot)
 *               evaluation in doubles (ally KO/Fake Out/redirection rescue,
 *               Protect + spread synergy, Helping Hand), full immunity and
 *               move-precondition filtering, threat-based switching, and a
 *               synergy + single-Mega aware team preview.
 *
 * Policies read the opponent's sets through the configured information view.
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
//                 the format's most-used ones until revealed in battle
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

/**
 * Incrementally scan `battle.log` for revealed abilities and items so closed
 * sheets never overwrite an ability or item that has already activated in battle.
 */
function scanRevealed(battle) {
  let pos = battle._scannedLogPos || 0;
  const log = battle.log;
  if (!log || pos >= log.length) return;
  const findMon = ident => {
    if (!ident || ident.length < 4) return null;
    const s = ident[1] === '1' ? battle.sides[0] : ident[1] === '2' ? battle.sides[1] : null;
    if (!s) return null;
    const name = ident.slice(ident.indexOf(': ') + 2);
    return s.pokemon.find(p => p.name === name || p.set.species === name);
  };
  for (; pos < log.length; pos++) {
    const line = log[pos];
    if (line.startsWith('|-ability|')) {
      const m = findMon(line.split('|')[2]);
      if (m) m._abilityKnown = true;
    } else if (line.startsWith('|-item|') || line.startsWith('|-enditem|')) {
      const m = findMon(line.split('|')[2]);
      if (m) m._itemKnown = true;
    } else if (line.includes('[from] ability:')) {
      const ofIdx = line.indexOf('[of] ');
      const m = findMon(ofIdx >= 0 ? line.slice(ofIdx + 5).split('|')[0] : line.split('|')[2]);
      if (m) m._abilityKnown = true;
    } else if (line.includes('[from] item:')) {
      const ofIdx = line.indexOf('[of] ');
      const m = findMon(ofIdx >= 0 ? line.slice(ofIdx + 5).split('|')[0] : line.split('|')[2]);
      if (m) m._itemKnown = true;
    }
  }
  battle._scannedLogPos = pos;
}

/** Swap `side`'s view of the foe to its beliefs; returns the restore function. */
function applyView(battle, side, view) {
  if (!view || !view.mode || view.mode === 'omniscient') return () => {};
  scanRevealed(battle);
  const saved = [];
  for (const p of side.foe.pokemon) {
    const b = beliefFor(view, p);
    if (!b) continue; // no data for this species: nothing better to assume than the truth
    saved.push({ p, storedStats: p.storedStats, maxhp: p.maxhp, hp: p.hp, moveSlots: p.moveSlots, item: p.item, ability: p.ability });
    if (b.spread) {
      let cached = p._beliefStats;
      if (!cached || cached.speciesId !== p.species.id) {
        const evs = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
        for (const [k, v] of Object.entries(b.spread)) if (STAT_KEY[k]) evs[STAT_KEY[k]] = v;
        const stats = battle.spreadModify(p.species.baseStats, { ...p.set, evs, nature: b.nature || 'Serious' });
        cached = {
          speciesId: p.species.id,
          storedStats: { atk: stats.atk, def: stats.def, spa: stats.spa, spd: stats.spd, spe: stats.spe },
          maxhp: p.species.maxHP || stats.hp,
        };
        p._beliefStats = cached;
      }
      p.storedStats = { ...cached.storedStats };
      const maxhp = cached.maxhp;
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
      // An item that's gone (eaten, knocked off) or already revealed is visible; otherwise guess.
      if (p.item && b.item && !p._itemKnown) p.item = toID(b.item);
      // A Mega's ability is fixed by its form, and a triggered ability is visible.
      if (b.ability && !p.species.isMega && !p._abilityKnown) p.ability = toID(b.ability);
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
// Margin-gated selection: only explore among top-3 options that are within
// `maxDelta` score points of the best option, preventing catastrophic blunders
// when one play is clearly superior while preserving variety on close calls.
const pickTop = (rng, scored, eps, maxDelta = 8) => {
  scored.sort((a, b) => b.score - a.score);
  if (scored.length > 1 && rng() < eps) {
    const bestScore = scored[0].score;
    let k = 1;
    while (k < Math.min(3, scored.length) && bestScore - scored[k].score <= maxDelta) k++;
    if (k > 1) return scored[rint(rng, k)];
  }
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

// Abilities that make a Pokémon immune to (or absorb) a move type.
const TYPE_IMMUNE_ABILITY = {
  levitate: 'Ground', eelevate: 'Ground', eartheater: 'Ground',
  flashfire: 'Fire', wellbakedbody: 'Fire',
  waterabsorb: 'Water', stormdrain: 'Water', dryskin: 'Water',
  voltabsorb: 'Electric', lightningrod: 'Electric', motordrive: 'Electric',
  sapsipper: 'Grass',
};
const MOLD_BREAKERS = new Set(['moldbreaker', 'teravolt', 'turboblaze']);
const PRIO_BLOCK_ABILITIES = new Set(['armortail', 'queenlymajesty', 'dazzling']);
const WEATHER_BY_ABILITY = {
  drought: 'sunnyday', orichalcumpulse: 'sunnyday',
  drizzle: 'raindance',
  sandstream: 'sandstorm',
  snowwarning: 'snowscape',
};
const SAFE_MODIFY_MOVE = new Set([
  'weatherball', 'terrainpulse', 'blizzard', 'hurricane', 'thunder',
  'bleakwindstorm', 'wildboltstorm', 'sandsearstorm', 'photongeyser',
  'shellsidearm', 'expandingforce',
]);
const ATE_ABILITY_TYPE = {
  aerilate: 'Flying', pixilate: 'Fairy', refrigerate: 'Ice', galvanize: 'Electric', dragonize: 'Dragon',
};
const VAR_BP_FALLBACK = {
  lowkick: 80, grassknot: 80, heavyslam: 80, heatcrash: 80,
  gyroball: 75, electroball: 75, knockoff: 85, acrobatics: 75,
  facade: 70, hex: 65, venoshock: 65, brines: 65, avalanche: 60,
  payback: 60, assurance: 60, temperflare: 75, stompingtantrum: 75,
  firstimpression: 90, fakeout: 40,
};

const abilityId = p => (p && p.ability ? String(p.ability) : (p && p.isActive && p.getAbility ? p.getAbility().id : ''));
const itemId = p => String((p && p.item) || '');
const hasType = (p, t) => (p.hasType ? p.hasType(t) : (p.getTypes ? p.getTypes().includes(t) : (p.types || []).includes(t)));

function isGrounded(p) {
  if (!p) return true;
  if (typeof p.isGrounded === 'function') {
    const g = p.isGrounded();
    if (g !== null && g !== undefined) return !!g;
  }
  if (hasType(p, 'Flying')) return false;
  if (['levitate', 'eelevate'].includes(abilityId(p))) return false;
  if (itemId(p) === 'airballoon') return false;
  return true;
}

/** Priority of `moveOrId` when used by `att` in `battle`. */
function effectivePriority(battle, att, moveOrId) {
  const m = typeof moveOrId === 'string' ? battle.dex.moves.get(moveOrId) : moveOrId;
  if (!m || !m.exists) return 0;
  let pri = m.priority || 0;
  const ab = abilityId(att);
  if (m.category === 'Status' && ab === 'prankster') pri += 1;
  else if (ab === 'galewings' && m.type === 'Flying' && att && att.hp >= att.maxhp) pri += 1;
  else if (ab === 'triage' && (m.heal || (m.flags && m.flags.heal))) pri += 3;
  if (m.id === 'grassyglide' && battle && battle.field.terrain === 'grassyterrain' && isGrounded(att)) pri += 1;
  return pri;
}

/** True if `def` takes nothing from `move` because of ability, item, terrain, or move preconditions. */
function blockedByAbilityOrItem(battle, att, def, move) {
  const mold = att && MOLD_BREAKERS.has(abilityId(att));
  const ab = abilityId(def);
  const attAb = abilityId(att);
  const foeTarget = def && att && def.side !== att.side;

  // Turn-1-only moves fail after the user has already acted while active.
  if (att && att.isActive && att.activeMoveActions > 0 && ['fakeout', 'firstimpression', 'matblock'].includes(move.id)) {
    return true;
  }
  // Priority blockers: Psychic Terrain, Armor Tail / Queenly Majesty / Dazzling, Prankster vs Dark.
  if (battle && foeTarget) {
    const pri = effectivePriority(battle, att, move);
    if (pri > 0) {
      if (attAb === 'prankster' && move.category === 'Status' && hasType(def, 'Dark')) return true;
      if (battle.field.terrain === 'psychicterrain' && isGrounded(def)) return true;
      if (!mold && def.side && def.side.active.some(f => alive(f) && PRIO_BLOCK_ABILITIES.has(abilityId(f)))) return true;
    }
  }
  // Powder moves vs Grass-types, Overcoat, and Safety Goggles.
  if (foeTarget && move.flags && move.flags.powder) {
    if (hasType(def, 'Grass') || itemId(def) === 'safetygoggles' || (!mold && ab === 'overcoat')) return true;
  }
  if (move.category === 'Status') {
    if (!foeTarget) return false;
    if (!mold && (ab === 'goodasgold' || (ab === 'magicbounce' && move.flags && move.flags.reflectable))) return true;
    // Thunder Wave respects type immunity (Ground); absorbing abilities
    // (Volt Absorb, Lightning Rod, Sap Sipper, Flash Fire...) eat any move of their type.
    if (move.ignoreImmunity === false && battle && !battle.dex.getImmunity(move.type, def)) return true;
    if (!mold && move.type !== 'Ground' && TYPE_IMMUNE_ABILITY[ab] === move.type) return true;
    return false;
  }
  // Partner Telepathy immunity to spread moves.
  if (!foeTarget && def && att && def !== att && ab === 'telepathy') return true;
  const ground = move.type === 'Ground' && move.id !== 'thousandarrows';
  if (ground && itemId(def) === 'airballoon') return true;
  if (!mold) {
    if (TYPE_IMMUNE_ABILITY[ab] === move.type && (move.type !== 'Ground' || ground)) return true;
    if (ab === 'bulletproof' && move.flags && move.flags.bullet) return true;
    if (ab === 'soundproof' && move.flags && move.flags.sound) return true;
    if (ab === 'windrider' && move.flags && move.flags.wind) return true;
  }
  // Hard move preconditions that getDamage ignores.
  if (move.id === 'poltergeist' && !itemId(def)) return true;
  if (move.id === 'steelroller' && (!battle || !battle.field.terrain)) return true;
  if (move.id === 'dreameater' && def.status !== 'slp' && ab !== 'comatose') return true;
  if (move.id === 'belch' && att && att.isActive && !att.ateBerry) return true;
  if ((move.id === 'snore' || move.id === 'sleeptalk') && att && att.status !== 'slp' && attAb !== 'comatose') return true;
  if (move.id === 'lastresort' && att && att.moveSlots && (att.moveSlots.length < 2 || att.moveSlots.some(ms => ms.id !== 'lastresort' && !ms.used))) return true;
  if (move.id === 'synchronoise' && att && def && !(att.getTypes ? att.getTypes() : []).some(t => hasType(def, t))) return true;
  return false;
}

/**
 * Fast ability/item-aware formula estimate (no battle events): used for team
 * preview, greedy policy, and as a fallback.
 */
function formulaDamage(dex, att, def, moveId) {
  const move = dex.moves.get(moveId);
  if (!move.exists || move.category === 'Status') return 0;
  let bp = move.basePower || VAR_BP_FALLBACK[move.id] || 0;
  if (!bp) return 0;
  const attAb = abilityId(att), defAb = abilityId(def);
  let moveType = move.type;
  let ateBoost = 1;
  if (moveType === 'Normal' && ATE_ABILITY_TYPE[attAb] && move.id !== 'weatherball' && move.id !== 'terrainpulse') {
    moveType = ATE_ABILITY_TYPE[attAb];
    ateBoost = 1.2;
  }
  const scrappy = (attAb === 'scrappy' || attAb === 'mindseye') && (moveType === 'Normal' || moveType === 'Fighting');
  const defTypes = def.species && def.getTypes ? def.getTypes() : (def.types || (def.species && def.species.types) || []);
  if (!scrappy && !dex.getImmunity(moveType, defTypes)) return 0;
  if (blockedByAbilityOrItem(att.battle || null, att, def, { ...move, type: moveType })) return 0;
  const eff = scrappy && defTypes.includes('Ghost')
    ? Math.pow(2, dex.getEffectiveness(moveType, defTypes.filter(t => t !== 'Ghost')))
    : Math.pow(2, dex.getEffectiveness(moveType, defTypes));
  const attTypes = att.getTypes ? att.getTypes() : (att.types || (att.species && att.species.types) || []);
  const hasStab = attTypes.includes(moveType);
  const stab = hasStab ? (attAb === 'adaptability' ? 2.0 : 1.5) : 1;
  if (attAb === 'technician' && bp <= 60) bp *= 1.5;
  else if (attAb === 'sharpness' && move.flags && move.flags.slicing) bp *= 1.5;
  else if (attAb === 'toughclaws' && move.flags && move.flags.contact) bp *= 1.3;
  else if (attAb === 'ironfist' && move.flags && move.flags.punch) bp *= 1.2;
  else if (attAb === 'strongjaw' && move.flags && move.flags.bite) bp *= 1.5;
  else if (attAb === 'megalauncher' && move.flags && move.flags.pulse) bp *= 1.5;
  bp *= ateBoost;

  let a = move.category === 'Physical' ? att.storedStats.atk : att.storedStats.spa;
  if (move.id === 'bodypress') a = att.storedStats.def;
  else if (move.id === 'foulplay') a = def.storedStats.atk;
  if (move.category === 'Physical' && (attAb === 'hugepower' || attAb === 'purepower')) a *= 2;
  if (move.category === 'Physical' && att.status === 'brn' && attAb !== 'guts' && move.id !== 'facade') a *= 0.5;
  if (att.status && attAb === 'guts' && move.category === 'Physical') a *= 1.5;
  if (moveType === 'Fire' && attAb === 'firemane') a *= 1.5;

  let d = (move.overrideDefensiveStat ? def.storedStats[move.overrideDefensiveStat] : null) ||
    (move.category === 'Physical' ? def.storedStats.def : def.storedStats.spd);
  const defItem = itemId(def);
  if (defItem === 'assaultvest' && move.category === 'Special') d *= 1.5;
  else if (defItem === 'eviolite') d *= 1.5;
  if (move.category === 'Physical' && defAb === 'furcoat') d *= 2;
  else if (move.category === 'Special' && defAb === 'icescales') d *= 2;
  else if (move.category === 'Special' && defAb === 'sandstream' && defTypes.includes('Rock')) d *= 1.5;

  let mod = 1;
  const attItem = itemId(att);
  if (attItem === 'lifeorb') mod *= 1.3;
  else if (attItem === 'choiceband' && move.category === 'Physical') mod *= 1.5;
  else if (attItem === 'choicespecs' && move.category === 'Special') mod *= 1.5;
  else if (attItem === 'expertbelt' && eff > 1) mod *= 1.2;
  if (attAb === 'parentalbond' && !move.multihit) mod *= 1.25;
  if (moveType === 'Fire' && (attAb === 'drought' || attAb === 'orichalcumpulse' || attAb === 'megasol')) mod *= 1.5;
  else if (moveType === 'Water' && attAb === 'drizzle') mod *= 1.5;
  if ((defAb === 'multiscale' || defAb === 'shadowshield') && def.hp >= def.maxhp) mod *= 0.5;
  if (eff > 1 && (defAb === 'filter' || defAb === 'solidrock' || defAb === 'prismarmor')) mod *= 0.75;

  const base = Math.floor(Math.floor(22 * bp * a / Math.max(1, d)) / 50) + 2;
  let hits = 1;
  if (move.multihit) {
    if (Array.isArray(move.multihit)) {
      hits = attAb === 'skilllink' ? move.multihit[1] : (attItem === 'loadeddice' ? 4.5 : 3.1);
    } else hits = move.multihit;
  }
  const acc = move.accuracy === true ? 1 : move.accuracy / 100;
  return base * hits * stab * eff * mod * 0.925 * acc;
}

// Reusable scratch PRNG and noop handler so simDamage never allocates them per call.
const SCRATCH_PRNG = new PRNG([7, 7, 7, 7]);
const NOOP_FALSE = () => false;

/**
 * Expected damage of `moveId` from `att` to `def`, computed with the
 * simulator's own getDamage under a zero-allocation, per-turn memoized sandbox:
 *   - ModifyType and safe ModifyMove events run so -ate/Dragonize abilities and
 *     Weather Ball / Terrain Pulse / Sheer Force / Scrappy evaluate accurately;
 *   - att.isActive and def.isActive are temporarily set true so benched
 *     Pokémon evaluated in switchEval have their abilities and items applied;
 *   - results are cached per turn decision on `battle._dmgCache`.
 */
function simDamage(battle, att, def, moveId, spreadHit) {
  const dexMove = battle.dex.moves.get(moveId);
  if (!dexMove.exists || dexMove.category === 'Status') return 0;
  if (blockedByAbilityOrItem(battle, att, def, dexMove)) return 0;

  const cache = battle._dmgCache;
  let cacheKey = null;
  if (cache && att.side && def.side) {
    // Boosts are part of the key: setup / Baton Pass evaluation projects them.
    const ab = att.boosts || {}, db = def.boosts || {};
    cacheKey = `${att.side.n}:${att.position}:${att.species.id}:${att.ability}:${ab.atk | 0},${ab.spa | 0},${ab.def | 0},${ab.spd | 0},${ab.spe | 0}:${def.side.n}:${def.position}:${def.species.id}:${def.ability}:${db.def | 0},${db.spd | 0}:${att.status || ''}:${def.status || ''}:${def.hp}:${moveId}:${spreadHit ? 1 : 0}:${battle.field.weather}:${battle.field.terrain}`;
    const hit = cache.get(cacheKey);
    if (hit !== undefined) return hit;
  }

  const savedPrng = battle.prng;
  const savedRand = battle.randomizer;
  const savedMove = battle.activeMove;
  const savedPoke = battle.activePokemon;
  const savedTarg = battle.activeTarget;
  const savedLogLen = battle.log.length;
  const savedAttEat = att.eatItem, savedAttUse = att.useItem, savedAttAct = att.isActive;
  const savedDefEat = def.eatItem, savedDefUse = def.useItem, savedDefAct = def.isActive;

  try {
    SCRATCH_PRNG.rng.seed = [7, 7, 7, 7];
    battle.prng = SCRATCH_PRNG;
    battle.randomizer = d => battle.trunc(battle.trunc(d * 92) / 100);
    att.eatItem = NOOP_FALSE; att.useItem = NOOP_FALSE; att.isActive = true;
    def.eatItem = NOOP_FALSE; def.useItem = NOOP_FALSE; def.isActive = true;

    let m = battle.dex.getActiveMove(moveId);
    m.willCrit = false;
    if (spreadHit) m.spreadHit = true;
    battle.activeMove = m; battle.activePokemon = att; battle.activeTarget = def;

    const attAb = abilityId(att);
    if (MOLD_BREAKERS.has(attAb)) m.ignoreAbility = true;
    if (attAb === 'scrappy' || attAb === 'mindseye') {
      m.ignoreImmunity = { ...(m.ignoreImmunity || {}), Fighting: true, Normal: true };
    }
    if (attAb === 'sheerforce' && m.secondaries) {
      m.hasSheerForceBoost = true;
      delete m.secondaries;
    }

    battle.singleEvent('ModifyType', m, null, att, def, m, m);
    if (SAFE_MODIFY_MOVE.has(m.id)) {
      battle.singleEvent('ModifyMove', m, null, att, def, m, m);
    }
    m = battle.runEvent('ModifyType', att, def, m, m) || m;
    battle.activeMove = m;

    let d = battle.actions.getDamage(att, def, m, true);
    if (typeof d !== 'number' || !(d > 0)) {
      if (cache && cacheKey) cache.set(cacheKey, 0);
      return 0;
    }
    if (m.multihit) {
      if (Array.isArray(m.multihit)) {
        if (attAb === 'skilllink') d *= m.multihit[1];
        else d *= m.multihit[0] === 2 && m.multihit[1] === 5 ? (att.hasItem('loadeddice') ? 4.5 : 3.1) : (m.multihit[0] + m.multihit[1]) / 2;
      } else d *= m.multihit;
    } else if (attAb === 'parentalbond' && !spreadHit) {
      d *= 1.25;
    }
    const acc = m.accuracy === true ? 1 : Math.min(1, m.accuracy / 100);
    const out = d * acc;
    if (cache && cacheKey) cache.set(cacheKey, out);
    return out;
  } catch (e) {
    return formulaDamage(battle.dex, att, def, moveId);
  } finally {
    battle.prng = savedPrng; battle.randomizer = savedRand;
    battle.activeMove = savedMove; battle.activePokemon = savedPoke; battle.activeTarget = savedTarg;
    att.eatItem = savedAttEat; att.useItem = savedAttUse; att.isActive = savedAttAct;
    def.eatItem = savedDefEat; def.useItem = savedDefUse; def.isActive = savedDefAct;
    battle.log.length = savedLogLen;
  }
}

/**
 * Mega species `p` can evolve into if its side has not yet Mega Evolved.
 */
function megaSpeciesFor(battle, p) {
  if (!p || ! p.species || p.species.isMega) return null;
  if (p.side && p.side.pokemon.some(x => x.species && x.species.isMega)) return null;
  const item = battle.dex.items.get(p.item);
  const megaName = (item && item.megaStone && (item.megaStone[p.species.name] || item.megaStone[p.baseSpecies.name])) || p.canMegaEvo;
  const mega = megaName && battle.dex.species.get(megaName);
  return mega && mega.exists ? mega : null;
}

/**
 * Temporarily project active Mega-capable Pokémon into their Mega stats,
 * types, and abilities (plus weather if Drought/Drizzle/Sand/Snow triggers)
 * so turn-of-Mega speed and damage evaluations see the real Mega form.
 */
function applyMegaProjection(battle) {
  const saved = [];
  let savedWx = null;
  for (const s of battle.sides) {
    if (s.pokemon.some(p => p.species.isMega)) continue;
    // If two active Pokémon on the same side hold Mega Stones, pick the one
    // with the larger offensive/speed stat gain to project as the primary Mega.
    const cands = s.active.filter(alive).map(p => ({ p, mega: megaSpeciesFor(battle, p) })).filter(x => x.mega);
    if (!cands.length) continue;
    cands.sort((a, b) => {
      const gain = x => (x.mega.baseStats.atk + x.mega.baseStats.spa + x.mega.baseStats.spe) -
        (x.p.species.baseStats.atk + x.p.species.baseStats.spa + x.p.species.baseStats.spe);
      return gain(b) - gain(a);
    });
    const { p, mega } = cands[0];
    const st = battle.spreadModify(mega.baseStats, p.set);
    saved.push({
      p, species: p.species, types: p.types, ability: p.ability,
      baseAbility: p.baseAbility, storedStats: p.storedStats,
    });
    const abId = toID(mega.abilities[0]);
    p.species = mega;
    p.types = mega.types;
    p.ability = abId;
    p.baseAbility = abId;
    p.storedStats = { atk: st.atk, def: st.def, spa: st.spa, spd: st.spd, spe: st.spe };
    const wx = WEATHER_BY_ABILITY[abId];
    if (wx && !savedWx && battle.field.weather !== wx) {
      savedWx = { weather: battle.field.weather, weatherState: { ...battle.field.weatherState } };
      battle.field.weather = wx;
      battle.field.weatherState = { id: wx, target: battle.field };
    }
  }
  return () => {
    for (const s of saved) {
      s.p.species = s.species; s.p.types = s.types; s.p.ability = s.ability;
      s.p.baseAbility = s.baseAbility; s.p.storedStats = s.storedStats;
    }
    if (savedWx) {
      battle.field.weather = savedWx.weather;
      battle.field.weatherState = savedWx.weatherState;
    }
  };
}

/**
 * Effective speed for turn order, for any Pokémon (active or benched): boosts,
 * Choice Scarf, paralysis, Tailwind, weather/terrain speed abilities; negated
 * under Trick Room so "bigger acts first" holds either way.
 */
function speedOf(p) {
  const tr = p.battle && p.battle.field.pseudoWeather['trickroom'];
  let s = p.storedStats.spe;
  const boost = (p.boosts && p.boosts.spe) || 0;
  if (boost > 0) s = Math.floor(s * (2 + boost) / 2);
  else if (boost < 0) s = Math.floor(s * 2 / (2 - boost));
  const ab = abilityId(p);
  const wx = p.battle ? p.battle.field.effectiveWeather() : '';
  if ((ab === 'swiftswim' && ['raindance', 'primordialsea'].includes(wx)) ||
      (ab === 'chlorophyll' && ['sunnyday', 'desolateland'].includes(wx)) ||
      (ab === 'sandrush' && wx === 'sandstorm') ||
      (ab === 'slushrush' && ['snowscape', 'hail'].includes(wx)) ||
      (ab === 'surgesurfer' && p.battle && p.battle.field.terrain === 'electricterrain') ||
      (ab === 'unburden' && p.volatiles && p.volatiles['unburden'])) {
    s *= 2;
  }
  if (itemId(p) === 'choicescarf') s = Math.floor(s * 1.5);
  if (p.status === 'par' && ab !== 'quickfeet') s = Math.floor(s * 0.5);
  if (p.side && p.side.sideConditions['tailwind']) s *= 2;
  return tr ? -s : s;
}

/**
 * Does `att` using `moveId` act before `def` (using `defMoveId` if known, else
 * priority 0)? Compares true effective priority first, then effective speed.
 */
function movesFirst(battle, att, moveId, def, defMoveId) {
  const priA = moveId ? effectivePriority(battle, att, moveId) : 0;
  const priB = defMoveId ? effectivePriority(battle, def, defMoveId) : 0;
  if (priA !== priB) return priA > priB;
  return speedOf(att) > speedOf(def);
}

/**
 * Focus Sash / Sturdy keep `def` at 1 HP against this hit: full HP, a
 * single-hit move (multi-hit moves and Parental Bond break through), and no
 * Mold Breaker against Sturdy.
 */
function sashHolds(battle, att, def, move) {
  if (!def || def.hp < def.maxhp) return false;
  const m = typeof move === 'string' ? battle.dex.moves.get(move) : move;
  if (m.multihit || abilityId(att) === 'parentalbond') return false;
  if (itemId(def) === 'focussash') return true;
  return abilityId(def) === 'sturdy' && !MOLD_BREAKERS.has(abilityId(att));
}

/**
 * Hit-count distribution of a multi-hit move, [[hits, probability], ...]
 * (2-5 hits: 35/35/15/15%; Skill Link: always the max; Loaded Dice: 4-5),
 * or null for a single-hit move. Matches the averaging in simDamage.
 */
function hitDist(att, move) {
  const mh = move.multihit;
  if (!mh) return null;
  if (!Array.isArray(mh)) return [[mh, 1]];
  if (abilityId(att) === 'skilllink') return [[mh[1], 1]];
  if (mh[0] === 2 && mh[1] === 5) {
    return itemId(att) === 'loadeddice' ? [[4, 0.5], [5, 0.5]] : [[2, 0.35], [3, 0.35], [4, 0.15], [5, 0.15]];
  }
  const out = [];
  for (let k = mh[0]; k <= mh[1]; k++) out.push([k, 1 / (mh[1] - mh[0] + 1)]);
  return out;
}

/** Expected damage after Focus Sash / Sturdy: a would-be OHKO leaves 1 HP. */
function capSash(battle, att, def, move, d) {
  return d >= def.hp && sashHolds(battle, att, def, move) ? def.hp - 1 : d;
}

/** Best (moveId, expected damage, priority) of `att` against `def` (Sash / Sturdy aware). */
function bestMove(battle, att, def) {
  let best = { id: null, d: 0, pri: 0 };
  for (const ms of att.moveSlots) {
    if (ms.pp <= 0 || ms.disabled) continue;
    if (ms.id === 'upperhand' || ms.id === 'focuspunch') continue; // situational: not a reliable threat
    const d = capSash(battle, att, def, ms.id, simDamage(battle, att, def, ms.id, false));
    if (d > best.d) best = { id: ms.id, d, pri: effectivePriority(battle, att, ms.id) };
  }
  return best;
}

/**
 * Threat assessment for each of our active Pokémon:
 *   threat   biggest expected hit any foe has on it / its current HP
 *   koFirst  a foe KOs it *before it acts* (faster, or higher priority)
 *   bestOut  its best expected hit on any foe / that foe's HP ("walled" if small)
 * plus `predicted`: for each foe, the move it most likely uses and into which
 * of our slots (its biggest hit by %HP) — the hit a switch-in would take.
 */
function assess(battle, side) {
  const out = { slots: new Map(), predicted: [], byFoe: new Map() };
  const mine = side.active.map((p, slot) => ({ p, slot })).filter(x => alive(x.p));
  const blank = () => ({ threat: 0, koFirst: false, bestOut: 0, koBy: [], koMoves: new Map() });
  for (const foe of foesOf(side)) {
    let pick = null;
    for (const { p, slot } of mine) {
      const b = bestMove(battle, foe, p);
      const frac = p.hp ? b.d / p.hp : 0;
      if (!pick || frac > pick.frac) pick = { foe, slot, moveId: b.id, frac, pri: b.pri };
      const s = out.slots.get(slot) || blank();
      s.threat = Math.max(s.threat, frac);
      if (b.id && frac >= 1 && movesFirst(battle, foe, b.id, p)) {
        s.koFirst = true;
        s.koBy.push(foe);
        s.koMoves.set(foe, b.id);
      }
      out.slots.set(slot, s);
    }
    if (pick && pick.moveId) {
      out.predicted.push(pick);
      out.byFoe.set(foe, pick);
    }
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
  // It comes in after the entry hazards (which also break a Focus Sash).
  const savedHp = cand.hp;
  const hz = Math.floor(hazardCost(battle, side, cand) * cand.maxhp);
  if (hz > 0 && !cand.isActive) cand.hp = Math.max(1, cand.hp - hz);
  try {
    return switchEvalAt(battle, side, slot, cand, a, free);
  } finally {
    cand.hp = savedHp;
  }
}

function switchEvalAt(battle, side, slot, cand, a, free) {
  let absorb = 0, followUp = 0, offense = 0, faster = true;
  const intim = abilityId(cand) === 'intimidate';
  for (const foe of foesOf(side)) {
    const foeAb = abilityId(foe);
    const intimCut = intim && !['defiant', 'competitive', 'guarddog', 'clearbody', 'whitesmoke', 'innerfocus', 'scrappy', 'oblivious', 'hypercutter'].includes(foeAb) && itemId(foe) !== 'clearamulet';
    if (!free) {
      for (const pr of a.predicted) {
        if (pr.foe === foe && pr.slot === slot) {
          const mv = battle.dex.moves.get(pr.moveId);
          const scale = intimCut && mv.category === 'Physical' ? 2 / 3 : 1;
          absorb += scale * capSash(battle, foe, cand, pr.moveId, simDamage(battle, foe, cand, pr.moveId, false)) / Math.max(1, cand.hp);
        }
      }
    }
    const bmFoe = bestMove(battle, foe, cand);
    const mvFoe = bmFoe.id ? battle.dex.moves.get(bmFoe.id) : null;
    const scaleFollow = intimCut && mvFoe && mvFoe.category === 'Physical' ? 2 / 3 : 1;
    followUp = Math.max(followUp, scaleFollow * bmFoe.d / Math.max(1, cand.hp));
    offense = Math.max(offense, Math.min(1.2, bestMove(battle, cand, foe).d / Math.max(1, foe.hp)));
    if (speedOf(foe) >= speedOf(cand)) faster = false;
  }
  return { absorb, followUp, offense, faster };
}

/**
 * Entry-hazard HP fraction `cand` loses on switch-in (Stealth Rock, Spikes).
 */
function hazardCost(battle, side, cand) {
  if (!side || !side.sideConditions || itemId(cand) === 'heavydutyboots' || abilityId(cand) === 'magicguard') return 0;
  let frac = 0;
  if (side.sideConditions['stealthrock']) {
    const eff = Math.pow(2, battle.dex.getEffectiveness('Rock', cand.getTypes ? cand.getTypes() : cand.types));
    frac += 0.125 * eff;
  }
  if (side.sideConditions['spikes'] && isGrounded(cand)) {
    const layers = side.sideConditions['spikes'].layers || 1;
    frac += layers === 1 ? 1 / 8 : layers === 2 ? 1 / 6 : 1 / 4;
  }
  return frac;
}

/**
 * What `mon` is still worth to the rest of the battle, 0..1: its HP (full
 * credit from 40%) times what it can do to the foes left: full against those
 * it moves before, half against slower-acting ones that can't KO it, nothing
 * against those that KO it before it moves.
 */
function preserveValue(battle, side, mon) {
  const hpPart = Math.min(1, mon.hp / mon.maxhp / 0.4);
  let use = 0;
  const picked = battle.ruleTable.pickedTeamSize || side.foe.pokemon.length;
  for (const f of side.foe.pokemon.slice(0, picked)) {
    if (!alive(f)) continue;
    const b = bestMove(battle, mon, f);
    if (!b.id) continue;
    const back = bestMove(battle, f, mon);
    const first = movesFirst(battle, mon, b.id, f, back.id);
    if (!first && back.d >= mon.hp) continue; // KO'd before it can do anything to this one
    use = Math.max(use, Math.min(1, b.d / Math.max(1, f.hp)) * (first ? 1 : 0.5));
  }
  return hpPart * (0.25 + 0.75 * use);
}

/** Switch-in matchup used to break ties: hits it lands vs takes afterwards. */
function matchupTerm(e) {
  return 40 * Math.min(1, e.offense) + (e.faster ? 12 : 0) - 30 * Math.min(1, e.followUp);
}

/**
 * What the entry hazards on `side` will cost its Pokémon still to come in:
 * each bench Pokémon's entry damage (Stealth Rock, Spikes), plus Toxic Spikes
 * and Sticky Web, in move-score units. `side` = the side the hazards are on.
 */
function hazardBurden(battle, side) {
  const sc = side.sideConditions;
  if (!sc['stealthrock'] && !sc['spikes'] && !sc['toxicspikes'] && !sc['stickyweb']) return 0;
  let v = 0;
  for (const i of benchIndices(side, new Set())) {
    const p = side.pokemon[i];
    v += 90 * hazardCost(battle, side, p); // HP it loses coming in (attacks score ~100 per max HP)
    const grounded = isGrounded(p) && itemId(p) !== 'heavydutyboots';
    if (sc['toxicspikes'] && grounded && !p.status && !hasType(p, 'Poison') && !hasType(p, 'Steel')) v += 10;
    if (sc['stickyweb'] && grounded) v += 8;
  }
  return v;
}

/** Hazard control move value: removal (Defog, Rapid Spin, Tidy Up) or swap (Court Change). */
function hazardControlValue(battle, side, id) {
  const ours = hazardBurden(battle, side), theirs = hazardBurden(battle, side.foe);
  if (id === 'rapidspin' || id === 'mortalspin' || id === 'tidyup') return ours;
  if (id === 'courtchange') return ours - theirs; // our hazards go to them, theirs come to us
  if (id === 'defog') {
    const screens = ['reflect', 'lightscreen', 'auroraveil'].filter(c => side.foe.sideConditions[c]).length;
    return ours - theirs + 18 * screens; // clears both sides, and the foe's screens
  }
  return 0;
}

/**
 * Value of switching `slot`'s active into `cand`, in the same units as move
 * scores (~100 per foe-HP removed).
 */
function switchScore(battle, side, slot, cand, a, free) {
  const mon = side.active[slot];
  const s = a.slots.get(slot) || { threat: 0, koFirst: false, bestOut: 1 };
  const e = switchEval(battle, side, slot, cand, a, free);
  const hCost = hazardCost(battle, side, cand);
  let v = -80 * hCost;
  if (cand.hp <= hCost * cand.maxhp) return -200; // dies to hazards on entry
  if (!free && mon) {
    // (only needed when it's in danger; memoized per decision on the assessment)
    const keepOf = () => {
      a.keep = a.keep || new Map();
      if (!a.keep.has(mon)) a.keep.set(mon, preserveValue(battle, side, mon));
      return a.keep.get(mon);
    };
    if (s.koFirst) {
      const keep = keepOf();
      // Doomed (KO'd before it acts): the turn is lost either way, and a
      // replacement comes in regardless, now or for free after the KO. What
      // switching changes is keeping `mon` (worth what it still brings) at
      // the price of the hit the switch-in takes. A spent Pokémon is better
      // sacked. The matchup only breaks ties between switch-ins.
      v += 55 * keep;
      v -= 70 * Math.min(1, e.absorb) + (e.absorb >= 1 ? 40 : 0);
      if (e.absorb === 0 && a.predicted.some(p => p.slot === slot)) v += 18;
      if (abilityId(mon) === 'regenerator' && mon.hp < mon.maxhp * 0.75) v += 14;
      v -= 12 * posBoosts(mon.boosts) * keep;
      // Relative to the best free replacement after a KO (who comes in anyway).
      a.bestFree = a.bestFree || new Map();
      if (!a.bestFree.has(slot)) {
        let best = -Infinity;
        for (const i of benchIndices(side, new Set())) {
          best = Math.max(best, matchupTerm(switchEval(battle, side, slot, side.pokemon[i], a, true)));
        }
        a.bestFree.set(slot, best);
      }
      const bestFree = a.bestFree.get(slot);
      v += 0.3 * (matchupTerm(e) - bestFree);
      return v;
    }
    if (s.threat >= 0.6) v += 30 * Math.min(1, s.threat) * (0.5 + 0.5 * keepOf());
    if (s.bestOut < 0.15) v += 30;                // walled: nothing it does matters
    else if (s.bestOut < 0.3) v += 12;
    v -= 32;                                      // the turn spent switching
    if (abilityId(mon) === 'regenerator' && mon.hp < mon.maxhp * 0.75) v += 14;
    const boosts = Object.values(mon.boosts).reduce((x, b) => x + Math.max(0, b), 0);
    v -= 12 * boosts;                             // switching throws away boosts
    const drops = Object.values(mon.boosts).reduce((x, b) => x + Math.max(0, -b), 0);
    v += 5 * Math.min(4, drops);                  // switching clears Draco Meteor / Overheat drops
    if (mon.activeTurns <= 1) v -= 10;            // don't ping-pong
    v -= 70 * Math.min(1, e.absorb) + (e.absorb >= 1 ? 40 : 0);
    if (e.absorb === 0 && a.predicted.some(p => p.slot === slot)) v += 18; // immune / resisted to nothing
  }
  const candAb = abilityId(cand);
  if (candAb === 'intimidate') {
    const foes = foesOf(side);
    if (foes.some(f => ['defiant', 'competitive', 'guarddog'].includes(abilityId(f)))) v -= 22;
    else if (foes.some(f => f.storedStats.atk >= f.storedStats.spa)) v += 10;
  }
  const wx = WEATHER_BY_ABILITY[candAb];
  if (wx && battle.field.weather !== wx) v += battle.field.weather ? 14 : 8;
  v += 40 * Math.min(1, e.offense) + (e.faster ? 12 : 0);
  v -= 30 * Math.min(1, e.followUp) + (e.followUp >= 1 && !e.faster ? 15 : 0);
  return v;
}

// ------------------------------------------------- setup and Baton Pass
//
// Stat boosts are valued by what they change on the board: project them onto
// a Pokémon (the setter itself, or a Baton Pass recipient) and re-run the real
// damage and speed checks against every foe still standing.

const BOOST_STATS = ['atk', 'def', 'spa', 'spd', 'spe', 'accuracy', 'evasion'];
const NO_BOOSTS = Object.freeze({ atk: 0, def: 0, spa: 0, spd: 0, spe: 0, accuracy: 0, evasion: 0 });
// Moves that undo a setup turn: phazing and boost-clearing.
const ANTI_SETUP = new Set(['haze', 'clearsmog', 'roar', 'whirlwind', 'dragontail', 'circlethrow', 'spectralthief']);
const clampBoost = v => Math.max(-6, Math.min(6, v));
const posBoosts = b => BOOST_STATS.reduce((x, k) => x + Math.max(0, (b && b[k]) || 0), 0);
const negBoosts = b => BOOST_STATS.reduce((x, k) => x + Math.max(0, -((b && b[k]) || 0)), 0);

/** Self-boosts `move` gives `mon` (Swords Dance, Shell Smash, Belly Drum, non-Ghost Curse...), or null. */
function setupBoosts(mon, move) {
  let b = null;
  if (move.id === 'bellydrum') b = { atk: 12 }; // maximises Attack whatever the current stage
  else if (move.id === 'curse') b = hasType(mon, 'Ghost') ? null : { atk: 1, def: 1, spe: -1 };
  else if (move.target === 'self' && move.boosts) b = move.boosts;
  if (!b) return null;
  if (abilityId(mon) === 'contrary') b = Object.fromEntries(Object.entries(b).map(([k, v]) => [k, -v]));
  else if (abilityId(mon) === 'simple') b = Object.fromEntries(Object.entries(b).map(([k, v]) => [k, 2 * v]));
  return b;
}

function addBoosts(base, delta) {
  const out = { ...NO_BOOSTS };
  for (const k of BOOST_STATS) out[k] = clampBoost(((base && base[k]) || 0) + ((delta && delta[k]) || 0));
  return out;
}

/** Foes that matter for a sweep: the active ones fully, the brought bench partly. */
function sweepTargets(battle, side) {
  const picked = battle.ruleTable.pickedTeamSize || side.foe.pokemon.length;
  return side.foe.pokemon.slice(0, picked).filter(alive).map(f => ({ f, w: f.isActive ? 1 : 0.4 }));
}

/**
 * Board value of `cand` holding `boosts`: how hard and how fast it hits every
 * remaining foe, minus how hard they hit it back (all from the real damage
 * calc, so Stored Power, Body Press, Unaware and resistances come out right).
 */
function boostValue(battle, side, cand, boosts) {
  const saved = cand.boosts;
  cand.boosts = { ...NO_BOOSTS, ...boosts };
  try {
    let v = 0;
    for (const { f, w } of sweepTargets(battle, side)) {
      const out = bestMove(battle, cand, f).d / Math.max(1, f.hp);
      const inc = bestMove(battle, f, cand).d / Math.max(1, cand.hp);
      const fast = speedOf(cand) > speedOf(f);
      v += w * (40 * Math.min(1, out) + (out >= 1 ? (fast ? 35 : 15) : 0) + (fast && out > 0.05 ? 10 : 0));
      v -= w * (25 * Math.min(1, inc) + (inc >= 1 && !fast ? 15 : 0));
    }
    return v;
  } finally {
    cand.boosts = saved;
  }
}

/** Can `mon` Baton Pass right now (a healthy bench to pass to)? */
function canBatonPass(side, mon) {
  return !!mon.moveSlots.find(ms => ms.id === 'batonpass' && ms.pp > 0 && !ms.disabled) &&
    !(mon.volatiles && mon.volatiles['commanded']) && benchIndices(side, new Set()).length > 0;
}

/** What `boosts` are worth to `cand` compared with coming in unboosted. */
function relayGain(battle, side, cand, boosts) {
  if (!posBoosts(boosts) && !negBoosts(boosts)) return 0;
  return boostValue(battle, side, cand, boosts) - boostValue(battle, side, cand, NO_BOOSTS);
}

// Moves that force the target out (and wipe its boosts). All run at -6
// priority, so after a Baton Pass they land on the recipient.
const PHAZE_MOVES = new Set(['roar', 'whirlwind', 'dragontail', 'circlethrow']);

/** Phazing moves the active foes carry (as this side believes them). */
function foePhazes(side) {
  const out = new Set();
  for (const f of foesOf(side)) {
    for (const ms of f.moveSlots) if (PHAZE_MOVES.has(ms.id) && ms.pp > 0 && !ms.disabled) out.add(ms.id);
  }
  return [...out];
}

/**
 * Can't be forced out by `moveId`: Suction Cups / Guard Dog; Magic Bounce /
 * Good as Gold (/ Soundproof vs Roar) against the status ones; a type
 * immunity (Fairy vs Dragon Tail, Ghost vs Circle Throw) or an inherited
 * Substitute against the damaging ones.
 */
function phazeProof(battle, cand, moveId, withSub) {
  const ab = abilityId(cand);
  if (ab === 'suctioncups' || ab === 'guarddog') return true;
  const mv = battle.dex.moves.get(moveId);
  if (mv.category === 'Status') {
    return ab === 'magicbounce' || ab === 'goodasgold' || (ab === 'soundproof' && !!(mv.flags && mv.flags.sound));
  }
  const types = cand.getTypes ? cand.getTypes() : cand.types;
  return withSub || !battle.dex.getImmunity(mv.type, types);
}

/**
 * Value of Baton Passing from `passer` into bench `cand`: the switch itself
 * (hit absorbed, matchup) plus the boosts it inherits. `safe` = the recipient
 * survives coming in and isn't outsped and OHKO'd right after; with a phazer
 * on the other side the boosts only stick on a recipient it can't force out.
 * `free` = the passer moves after the foes, so the recipient takes no hit.
 */
function passScore(battle, side, slot, passer, cand, a, free) {
  // The recipient arrives already holding the boosts, so it meets the incoming
  // hit (a fast passer's case) and the follow-up with the passed Def / SpD /
  // Spe: evaluate the switch-in with them on.
  const savedBoosts = cand.boosts;
  cand.boosts = { ...NO_BOOSTS, ...passer.boosts };
  let v, e;
  try {
    v = switchScore(battle, side, slot, cand, a, free);
    e = switchEval(battle, side, slot, cand, a, free);
  } finally {
    cand.boosts = savedBoosts;
  }
  if (free) v -= 32; // the turn is still spent
  else v += 12 * posBoosts(passer.boosts) - 5 * Math.min(4, negBoosts(passer.boosts)); // switchScore assumed they're dropped
  const hazard = hazardCost(battle, side, cand) * cand.maxhp;
  const safe = cand.hp > hazard && (free || e.absorb < 1) && !(e.followUp >= 1 && !e.faster);
  let relay = relayGain(battle, side, cand, passer.boosts);
  const withSub = !!(passer.volatiles && passer.volatiles['substitute']);
  const phazes = posBoosts(passer.boosts) ? foePhazes(side) : [];
  if (phazes.length) {
    if (phazes.every(id => phazeProof(battle, cand, id, withSub))) v += 15; // the boosts can't be roared away
    else relay *= 0.15; // expect the phaze: the boosts are as good as gone
  }
  v += 0.85 * relay;
  if (withSub) v += 18;
  return { score: v, safe };
}

/** Best safe Baton Pass recipient for `slot`'s active: {i, score} (i = -1: none is safe). */
function bestPass(battle, side, slot, passer, a, free, used = new Set()) {
  let best = { i: -1, score: -Infinity };
  for (const i of benchIndices(side, used)) {
    const r = passScore(battle, side, slot, passer, side.pokemon[i], a, free);
    if (r.safe && r.score > best.score) best = { i, score: r.score };
  }
  return best;
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
 * How a Pokémon fights, for team preview: a Mega Stone holder as its Mega
 * (stats, typing, ability) when `asMega` is true, or as its base form.
 */
function previewForm(battle, p, asMega = true) {
  if (!asMega) return p;
  const item = battle.dex.items.get(p.item);
  const megaName = item.megaStone && (item.megaStone[p.species.name] || item.megaStone[p.baseSpecies.name]);
  const mega = megaName && battle.dex.species.get(megaName);
  if (!mega || !mega.exists) return p;
  const st = battle.spreadModify(mega.baseStats, p.set);
  return {
    species: mega, storedStats: st, maxhp: p.maxhp, hp: p.hp, moveSlots: p.moveSlots, item: p.item,
    ability: toID(mega.abilities[0]), isActive: false, side: p.side, battle: p.battle, status: p.status,
    boosts: p.boosts, types: mega.types, getTypes: () => mega.types,
    hasType: t => mega.types.includes(t), getAbility: () => battle.dex.abilities.get(mega.abilities[0]),
    isMegaPreview: true,
  };
}

/** Preview offense of `a` on `b`: best hit as a fraction of HP, bonus if faster. */
function previewOff(dex, a, b) {
  let best = 0;
  for (const ms of a.moveSlots) best = Math.max(best, formulaDamage(dex, a, b, ms.id) / b.maxhp);
  return Math.min(best, 1.2) * (a.storedStats.spe > b.storedStats.spe ? 1.15 : 1);
}

function memberPreviewScore(battle, p, theirs, doubles, lead) {
  const dex = battle.dex;
  const off = (a, b) => previewOff(dex, a, b);
  let o = 0, d = 0;
  for (const q of theirs) { o += off(p, q); d += off(q, p); }
  let s = o / theirs.length - 0.7 * d / theirs.length;
  const ids = new Set(p.moveSlots.map(m => m.id));
  const ability = abilityId(p);
  let role = 0;
  if (ids.has('tailwind') || ids.has('trickroom')) role += 0.25;
  if (ids.has('fakeout')) role += 0.14;
  if (ids.has('followme') || ids.has('ragepowder')) role += 0.15;
  if (ability === 'intimidate') role += 0.12;
  if (WEATHER_BY_ABILITY[ability]) role += 0.14;
  if (ability === 'prankster' && p.moveSlots.some(m => dex.moves.get(m.id).category === 'Status')) role += 0.08;
  if (ids.has('reflect') || ids.has('lightscreen') || ids.has('auroraveil')) role += 0.08;
  if (!doubles && (ids.has('stealthrock') || ids.has('spikes'))) role += lead ? 0.18 : 0.06;
  if (doubles) s += lead ? role * 1.4 : role;
  else s += role * 0.35;
  return s;
}

/**
 * Boosts a Baton Passer can realistically hand over: each of its setup moves
 * used once (plus a Speed Boost tick), at most +2 per stat.
 */
function passerBoosts(dex, p) {
  const total = {};
  for (const ms of p.moveSlots) {
    const b = setupBoosts(p, dex.moves.get(ms.id));
    if (b) for (const [k, v] of Object.entries(b)) if (v > 0) total[k] = (total[k] || 0) + v;
  }
  if (abilityId(p) === 'speedboost') total.spe = (total.spe || 0) + 1;
  for (const k of Object.keys(total)) total[k] = Math.min(2, total[k]);
  return total;
}

/** How much `boosts` raise recipient `r`'s preview offense across their team. */
function previewPassGain(dex, r, boosts, theirs) {
  const mult = k => (2 + (boosts[k] || 0)) / 2;
  const boosted = Object.create(r);
  const st = r.storedStats;
  boosted.storedStats = { ...st, atk: st.atk * mult('atk'), spa: st.spa * mult('spa'), spe: st.spe * mult('spe') };
  let g = 0;
  for (const q of theirs) g += previewOff(dex, boosted, q) - previewOff(dex, r, q);
  return g / theirs.length;
}

/** Baton Pass plus a way to build boosts (a setup move, or Speed Boost). */
function isPasserSet(dex, p) {
  const ids = p.moveSlots.map(m => m.id);
  return ids.includes('batonpass') &&
    (abilityId(p) === 'speedboost' || ids.some(id => {
      const b = setupBoosts(p, dex.moves.get(id));
      return b && Object.values(b).some(v => v > 0);
    }));
}

/** Has a real damaging move (someone worth passing boosts to). */
function isAttackerSet(dex, p) {
  return p.moveSlots.some(m => {
    const mv = dex.moves.get(m.id);
    return mv.category !== 'Status' && (mv.basePower > 0 || mv.basePowerCallback || VAR_BP_FALLBACK[mv.id]);
  });
}

function previewScores(battle, side, lead) {
  const doubles = side.active.length > 1;
  const theirs = side.foe.pokemon.map(p => previewForm(battle, p, true));
  const info = side.pokemon.map((p, i) => {
    const megaForm = previewForm(battle, p, true);
    const isMegaHolder = megaForm !== p;
    const sMega = memberPreviewScore(battle, megaForm, theirs, doubles, lead);
    const sBase = isMegaHolder ? memberPreviewScore(battle, p, theirs, doubles, lead) - 0.45 : sMega;
    const passer = isPasserSet(battle.dex, p);
    return { i, s: sMega, sMega, sBase, isMegaHolder, form: megaForm, passer, attacker: !passer && isAttackerSet(battle.dex, p) };
  });
  // A Baton Passer is worth what its boosts add to each possible recipient.
  for (const x of info) {
    if (!x.passer) continue;
    const boosts = passerBoosts(battle.dex, side.pokemon[x.i]);
    x.passGain = info.map(r => (r.attacker ? previewPassGain(battle.dex, r.form, boosts, theirs) : 0));
  }
  return info;
}

/** Each passer's gain through its best recipient in `subset`, best first. */
function subsetPassGains(subset, info) {
  return subset.filter(i => info[i].passer)
    .map(i => Math.max(0, ...subset.map(j => info[i].passGain[j])))
    .sort((a, b) => b - a);
}

function subsetSynergy(subset, info) {
  let bonus = 0;
  const forms = subset.map(i => info[i].form);
  const abils = new Set(forms.map(f => abilityId(f)));
  const moves = new Set(forms.flatMap(f => f.moveSlots.map(m => m.id)));
  if (abils.has('drought') || abils.has('orichalcumpulse')) {
    if (abils.has('chlorophyll') || abils.has('solarpower') || abils.has('protosynthesis') || moves.has('weatherball') || moves.has('solarbeam')) {
      bonus += 0.16;
    }
    // Sun halves Water damage: avoid bringing a dedicated Water attacker alongside Drought
    if (forms.some(f => hasType(f, 'Water') && !hasType(f, 'Fire') && !hasType(f, 'Dragon'))) bonus -= 0.22;
  }
  if (abils.has('drizzle') && (abils.has('swiftswim') || moves.has('electroshot') || moves.has('weatherball') || moves.has('hurricane') || moves.has('thunder'))) {
    bonus += 0.16;
  }
  if (abils.has('sandstream') && abils.has('sandrush')) bonus += 0.16;
  if (abils.has('snowwarning') && (abils.has('slushrush') || moves.has('auroraveil') || moves.has('blizzard'))) bonus += 0.16;
  // Baton Pass needs someone to receive the boosts: a passer counts for what
  // it adds to the best recipient brought along, weighted up because a
  // boosted recipient keeps sweeping (a second passer counts half).
  subsetPassGains(subset, info).forEach((g, k) => { bonus += (k ? 0.75 : 1.5) * g; });
  if (moves.has('trickroom')) {
    const slowCount = forms.filter(f => f.storedStats.spe <= 85).length;
    bonus += slowCount >= 2 ? 0.15 : -0.1;
  }
  return bonus;
}

function teamPreview(policy, battle, side, req, rng) {
  const n = req.maxChosenTeamSize || side.pokemon.length;
  const order = side.pokemon.map((_, i) => i);
  if (policy === 'random' || policy === 'greedy') {
    for (let i = order.length - 1; i > 0; i--) { const j = rint(rng, i + 1); [order[i], order[j]] = [order[j], order[i]]; }
    return 'team ' + order.slice(0, n).map(i => i + 1).join('');
  }
  const info = previewScores(battle, side, false);
  const m = side.pokemon.length;
  // Evaluate all C(m, n) bring subsets so at most one Mega Stone holder gets
  // its Mega score and weather / Trick Room synergy pairs are kept together.
  const subsets = [];
  const rec = (start, acc) => {
    if (acc.length === n) {
      let megaClaimed = false;
      let score = 0;
      // Sort Mega holders by their Mega score so the strongest Mega claims the Mega slot.
      const sorted = [...acc].sort((a, b) => info[b].sMega - info[a].sMega);
      for (const idx of sorted) {
        if (info[idx].isMegaHolder) {
          if (!megaClaimed) { score += info[idx].sMega; megaClaimed = true; }
          else score += info[idx].sBase;
        } else {
          score += info[idx].sMega;
        }
      }
      score += subsetSynergy(acc, info) + (rng() - 0.5) * 0.08;
      subsets.push({ subset: [...acc], score });
      return;
    }
    for (let i = start; i <= m - (n - acc.length); i++) {
      acc.push(i);
      rec(i + 1, acc);
      acc.pop();
    }
  };
  rec(0, []);
  subsets.sort((a, b) => b.score - a.score);
  const bring = subsets[0].subset;
  const leadInfo = previewScores(battle, side, true);
  const primaryMega = [...bring].filter(i => leadInfo[i].isMegaHolder)
    .sort((a, b) => info[b].sMega - info[a].sMega)[0];
  // A Baton Passer leads: it needs turns on the field to set up before its
  // recipient (still in the back) comes in.
  const passLead = i => (info[i].passer ? 0.6 + Math.max(0, ...bring.map(j => info[i].passGain[j])) : 0);
  const leadScore = new Map(bring.map(i => [
    i,
    (leadInfo[i].isMegaHolder && i !== primaryMega ? leadInfo[i].sBase : leadInfo[i].sMega) + passLead(i) + (rng() - 0.5) * 0.08,
  ]));
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
      // After Baton Pass the outgoing Pokémon is still active, flagged to copy
      // its boosts and Substitute over: pick who makes the most of them.
      const passer = side.active[slot];
      // (switchFlag holds the id of the move that triggered the switch.)
      const passing = passer && alive(passer) && typeof passer.switchFlag === 'string' &&
        battle.dex.moves.get(passer.switchFlag).selfSwitch === 'copyvolatile';
      // Foes still to move this turn will hit whoever comes in.
      const free = !passing || !battle.queue.list.some(q => q.choice === 'move' && q.pokemon && q.pokemon.side === side.foe);
      const value = i => {
        if (!passing) return switchScore(battle, side, slot, side.pokemon[i], a, true);
        const r = passScore(battle, side, slot, passer, side.pokemon[i], a, free);
        return r.score + (free ? 32 : 0) - (r.safe ? 0 : 60);
      };
      const scored = opts.map(i => ({ i, score: value(i) + 10 * side.pokemon[i].hp / side.pokemon[i].maxhp }));
      pick = pickTop(rng, scored, 0.05).i;
    }
    used.add(pick);
    return `switch ${pick + 1}`;
  }).join(', ');
}

// ---------------------------------------------------------- move decisions

const PROTECT = new Set(['protect', 'detect', 'spikyshield', 'kingsshield', 'banefulbunker', 'silktrap', 'burningbulwark', 'obstruct']);
const SPREAD_MOVE_IDS = new Set([
  'heatwave', 'makeitrain', 'earthquake', 'rockslide', 'hypervoice', 'dazzlinggleam',
  'icywind', 'electroweb', 'snarl', 'muddywater', 'bleakwindstorm', 'blizzard',
  'eruption', 'waterspout', 'expandingforce', 'astralbarrage', 'glacialLance', 'surf', 'discharge',
]);

/** True if `t` can be afflicted by `status` from `mon` using `move`. */
function canInflictStatus(battle, mon, t, move, status) {
  if (!t || t.status || !alive(t)) return false;
  if (!battle.dex.getImmunity(status, t)) return false;
  if (blockedByAbilityOrItem(battle, mon, t, move)) return false;
  const mold = MOLD_BREAKERS.has(abilityId(mon));
  const ab = abilityId(t);
  if (!mold && (ab === 'purifyingsalt' || ab === 'comatose' || ab === 'goodasgold')) return false;
  if (t.volatiles && t.volatiles['substitute'] && !(move.flags && move.flags.authentic) && abilityId(mon) !== 'infiltrator') return false;
  if (battle.field.terrain === 'mistyterrain' && isGrounded(t)) return false;
  if (status === 'slp') {
    if (battle.field.terrain === 'electricterrain' && isGrounded(t)) return false;
    if (!mold && ['insomnia', 'vitalspirit', 'sweetveil'].includes(ab)) return false;
    if (!mold && t.side && t.side.active.some(p => alive(p) && abilityId(p) === 'sweetveil')) return false;
  } else if (status === 'brn') {
    if (!mold && ['waterveil', 'waterbubble', 'thermalexchange', 'flashfire', 'wellbakedbody'].includes(ab)) return false;
  } else if (status === 'par') {
    if (!mold && ab === 'limber') return false;
  } else if (status === 'psn' || status === 'tox') {
    if (!mold && ['immunity', 'pastelveil', 'poisonheal'].includes(ab)) return false;
    if (!mold && t.side && t.side.active.some(p => alive(p) && abilityId(p) === 'pastelveil')) return false;
  }
  return true;
}

// ---------------------------------------------------------- end of turn

/**
 * HP `p` gains (+) or loses (-) at the end of this turn: burn, poison and
 * Toxic's growing damage, sandstorm, Leech Seed, Curse, Salt Cure, Black
 * Sludge; Leftovers, Poison Heal, Grassy Terrain. Magic Guard skips the damage.
 */
function residualDelta(battle, p) {
  if (!alive(p)) return 0;
  const max = p.maxhp, ab = abilityId(p), it = itemId(p), vol = p.volatiles || {};
  let d = 0;
  if (ab !== 'magicguard') {
    if (battle.field.effectiveWeather() === 'sandstorm' && !['Rock', 'Ground', 'Steel'].some(t => hasType(p, t)) &&
        !['sandveil', 'sandrush', 'sandforce', 'overcoat'].includes(ab) && it !== 'safetygoggles') d -= max / 16;
    if (p.status === 'brn') d -= max / 16;
    if (p.status === 'psn' && ab !== 'poisonheal') d -= max / 8;
    if (p.status === 'tox' && ab !== 'poisonheal') d -= max * Math.min(15, ((p.statusState && p.statusState.stage) || 0) + 1) / 16;
    if (vol['leechseed']) d -= max / 8;
    if (vol['curse']) d -= max / 4;
    if (vol['saltcure']) d -= max * (hasType(p, 'Water') || hasType(p, 'Steel') ? 0.25 : 0.125);
    if (it === 'blacksludge' && !hasType(p, 'Poison')) d -= max / 8;
  }
  if (!vol['healblock']) {
    if ((p.status === 'psn' || p.status === 'tox') && ab === 'poisonheal') d += max / 8;
    if (it === 'leftovers' || (it === 'blacksludge' && hasType(p, 'Poison'))) d += max / 16;
    if (battle.field.terrain === 'grassyterrain' && isGrounded(p)) d += max / 16;
  }
  return d;
}

// ---------------------------------------------------------------- healing
//
// A heal is worth the HP it actually leaves us with after this turn's
// exchange, and only if it lands: turn order (speed, priority, Triage)
// decides whether the foe's hits come before or after it, and a foe that
// KOs us first means the heal never happens.

/** HP `move` restores to `target` (negative: Liquid Ooze), before the max-HP cap. */
function healAmount(battle, user, target, move, foe) {
  const wx = battle.field.effectiveWeather();
  const max = target.maxhp;
  switch (move.id) {
    case 'moonlight': case 'synthesis': case 'morningsun':
      if (['sunnyday', 'desolateland'].includes(wx)) return max * 0.667;
      if (wx && wx !== 'deltastream') return max * 0.25;
      return max * 0.5;
    case 'shoreup': return max * (wx === 'sandstorm' ? 0.667 : 0.5);
    case 'rest': return max;
    case 'lifedew': case 'junglehealing': return max * 0.25;
    case 'healpulse': return max * (abilityId(user) === 'megalauncher' ? 0.75 : 0.5);
    case 'floralhealing': return max * (battle.field.terrain === 'grassyterrain' ? 0.667 : 0.5);
    case 'strengthsap': {
      if (!foe || (foe.boosts.atk || 0) <= -6) return 0;
      const atk = foe.getStat ? foe.getStat('atk', false, true) : foe.storedStats.atk;
      const amt = itemId(user) === 'bigroot' ? atk * 1.3 : atk;
      return abilityId(foe) === 'liquidooze' ? -amt : amt;
    }
    default:
      if (move.heal) return max * move.heal[0] / move.heal[1];
      return max * 0.5;
  }
}

/**
 * Foe hits on `target` (in `slot`) this turn, split by whether they land
 * before or after `user`'s `moveId`; speed ties go to `tie`. `atkCut` scales
 * physical hits that land after the move (Strength Sap's Attack drop).
 */
function incomingSplit(battle, side, slot, target, user, moveId, a, atkCut = 1) {
  let before = 0, after = 0, tie = 0, tieCut = 0;
  for (const pr of a.predicted) {
    if (pr.slot !== slot || !pr.moveId) continue;
    const d = capSash(battle, pr.foe, target, pr.moveId, simDamage(battle, pr.foe, target, pr.moveId, false));
    const cut = atkCut !== 1 && battle.dex.moves.get(pr.moveId).category === 'Physical' ? atkCut : 1;
    const tied = effectivePriority(battle, user, moveId) === effectivePriority(battle, pr.foe, pr.moveId) &&
      speedOf(user) === speedOf(pr.foe);
    if (tied) { tie += d; tieCut += d * cut; } else if (movesFirst(battle, user, moveId, pr.foe, pr.moveId)) after += d * cut;
    else before += d;
  }
  return { before, after, tie, tieCut, res: residualDelta(battle, target) };
}

/** healExchange averaged over a speed tie (half the time we move first). */
function healExchangeTied(target, amount, inc, delayed) {
  if (!inc.tie) return healExchange(target, amount, inc, delayed);
  return 0.5 * healExchange(target, amount, { before: inc.before + inc.tie, after: inc.after, res: inc.res }, delayed) +
    0.5 * healExchange(target, amount, { before: inc.before, after: inc.after + inc.tieCut, res: inc.res }, delayed);
}

/**
 * Value of healing `target` by `amount` this turn, in move-score units
 * (~100 per max-HP): the HP we end the turn with versus not healing, a big
 * bonus when that's the difference between fainting and surviving, a bonus
 * for out-healing the foe's damage, and nothing (or worse) if the heal
 * would come too late or not be enough.
 */
function healExchange(target, amount, inc, delayed) {
  const max = target.maxhp, hp = target.hp, res = inc.res || 0;
  if (hp - inc.before <= 0) return -50; // KO'd before the heal goes off
  // End of turn (burn, Toxic, sand, Leftovers...) lands after all the hits.
  const endOfTurn = x => (x <= 0 ? x : Math.min(max, x + res));
  const noHeal = endOfTurn(hp - inc.before - inc.after);
  // Healing after the early hits wastes nothing; the late hits land on the healed HP.
  const withHeal = delayed ? noHeal : endOfTurn(Math.min(max, hp - inc.before + amount) - inc.after);
  if (withHeal <= 0) return delayed ? -40 : -35; // healing doesn't save it
  const gained = (delayed ? Math.min(amount, max - Math.max(0, noHeal)) * 0.6 : withHeal - Math.max(0, noHeal));
  let v = 85 * gained / max;
  const perTurn = inc.before + inc.after + Math.max(0, -res);
  // The heal is what keeps it alive: for good if it out-heals the hits,
  // otherwise it only buys a turn.
  if (noHeal <= 0) v += amount >= perTurn ? 45 : 15;
  if (amount > perTurn * 1.15 && hp < max * 0.55) v += 15; // out-heals what the foe can do
  if (hp >= max * 0.85) v -= 30;
  return v;
}

function healValue(battle, side, mon, opt, ctx) {
  const move = opt.move, a = ctx.a;
  if (mon.volatiles && mon.volatiles['healblock']) return -60;
  const slot = opt.slot;
  const foeTarget = opt.target && opt.target.side !== side ? opt.target : null;

  // Heal an ally: Heal Pulse / Floral Healing aimed at the partner.
  if (opt.target && opt.target.side === side && opt.target !== mon) {
    const ally = opt.target;
    const inc = incomingSplit(battle, side, ally.position, ally, mon, move.id, a);
    return healExchangeTied(ally, healAmount(battle, mon, ally, move, null), inc, false);
  }

  if (move.id === 'wish') {
    if (side.slotConditions && side.slotConditions[slot] && side.slotConditions[slot]['wish']) return -40;
    const inc = incomingSplit(battle, side, slot, mon, mon, move.id, a);
    return healExchangeTied(mon, mon.maxhp * 0.5, inc, true);
  }

  if (move.id === 'rest') {
    if (mon.hp >= mon.maxhp || mon.status === 'slp' || ['insomnia', 'vitalspirit', 'comatose', 'purifyingsalt'].includes(abilityId(mon))) return -60;
    if (isGrounded(mon) && ['electricterrain', 'mistyterrain'].includes(battle.field.terrain)) return -60;
  }

  const atkCut = move.id === 'strengthsap' && foeTarget && (foeTarget.boosts.atk || 0) > -6 ? 2 / 3 : 1;
  const amount = healAmount(battle, mon, mon, move, foeTarget);
  if (amount <= 0) return amount < 0 ? -60 : -40; // Liquid Ooze, or Strength Sap into -6 Attack
  const inc = incomingSplit(battle, side, slot, mon, mon, move.id, a, atkCut);
  let v = healExchangeTied(mon, amount, inc, false);
  if (v <= -35) return v;

  if (move.id === 'strengthsap' && foeTarget && atkCut !== 1 && foeTarget.storedStats.atk >= foeTarget.storedStats.spa) {
    v += 12; // the Attack drop keeps paying off
  }
  if (move.id === 'rest') {
    const wakes = itemId(mon) === 'chestoberry' || itemId(mon) === 'lumberry' || abilityId(mon) === 'shedskin';
    const sleepTalk = mon.moveSlots.some(ms => ms.id === 'sleeptalk');
    v -= wakes ? 0 : sleepTalk ? 10 : 35; // two turns asleep
    if (mon.status && mon.status !== 'slp') v += 12; // also cures it
  }
  if (move.id === 'lifedew' || move.id === 'junglehealing') {
    const ally = side.active.find(p => alive(p) && p !== mon);
    if (ally) v += 0.6 * Math.max(0, Math.min(ally.maxhp - ally.hp, healAmount(battle, mon, ally, move, null))) / ally.maxhp * 85;
  }
  return v;
}

// ------------------------------------------------------- inflicting status
//
// Burn / paralysis / poison are valued by what they change on the board:
// give the foe the status for a moment and re-run its damage against our
// team (burn halving, Guts, Facade all come from the real damage calc) and
// the speed checks (paralysis, Quick Feet, Trick Room), plus residual chip.

const STATUS_BOOSTED = {
  brn: ['guts', 'quickfeet', 'marvelscale', 'flareboost'],
  par: ['guts', 'quickfeet', 'marvelscale'],
  psn: ['guts', 'quickfeet', 'marvelscale', 'toxicboost', 'poisonheal', 'magicguard'],
  tox: ['guts', 'quickfeet', 'marvelscale', 'toxicboost', 'poisonheal', 'magicguard'],
};

/** Value of `t` getting `status` (brn / par / psn / tox), for `side`. */
function statusSwing(battle, side, t, status) {
  if (!STATUS_BOOSTED[status]) return 0;
  if (STATUS_BOOSTED[status].includes(abilityId(t))) return -40; // it wants the status
  const ours = [
    ...side.active.filter(alive).map(p => ({ p, w: 1 })),
    ...benchIndices(side, new Set()).map(i => ({ p: side.pokemon[i], w: 0.4 })),
  ];
  const look = () => ours.map(({ p }) => ({
    d: Math.min(1, bestMove(battle, t, p).d / Math.max(1, p.hp)),
    fast: speedOf(p) > speedOf(t),
  }));
  const before = look();
  const saved = t.status;
  t.status = status;
  let after;
  try { after = look(); } finally { t.status = saved; }
  let v = 0, worst = 0;
  ours.forEach(({ w }, k) => {
    v += w * 60 * (before[k].d - after[k].d); // it hits us softer (burn) / harder (Guts, Facade)
    if (after[k].fast !== before[k].fast) v += w * (after[k].fast ? 22 : -22); // speed order flips (paralysis)
    worst = Math.max(worst, before[k].d);
  });
  const chip = abilityId(t) === 'magicguard' ? 0 : status === 'brn' || status === 'psn' ? 12 : status === 'tox' ? 20 : 0;
  v += chip;
  if (status === 'par') v += 6 + 15 * worst; // a 25% chance it loses its turn
  return v;
}

/**
 * How likely the active foes stop `move` (a status move of ours) from paying
 * off: a faster Taunt makes it fail this turn; Encore locks us into it.
 */
function foeDisruption(battle, side, mon, move) {
  if (move.category !== 'Status' || PROTECT.has(move.id)) return 1;
  if (itemId(mon) === 'mentalherb' || side.active.some(p => alive(p) && abilityId(p) === 'aromaveil')) return 1;
  let f = 1;
  for (const foe of foesOf(side)) {
    const pranksterVsDark = abilityId(foe) === 'prankster' && hasType(mon, 'Dark');
    for (const ms of foe.moveSlots) {
      if (ms.pp <= 0 || ms.disabled || pranksterVsDark) continue;
      if (ms.id === 'taunt' && !(mon.volatiles && mon.volatiles['taunt']) && abilityId(mon) !== 'oblivious' &&
          movesFirst(battle, foe, 'taunt', mon, move.id)) {
        f = Math.min(f, 0.5);
      }
      if (ms.id === 'encore' && move.id !== 'batonpass' && !(mon.volatiles && mon.volatiles['encore'])) {
        // Locked into a heal while being chipped is a smaller loss than into setup.
        f = Math.min(f, move.heal || (move.flags && move.flags.heal) ? 0.9 : 0.75);
      }
    }
  }
  return f;
}

/** Non-damage value of a status (or utility) move for this slot. */
function statusValue(battle, side, mon, opt, st, ctx) {
  const id = opt.id, move = opt.move, field = battle.field, foes = foesOf(side);
  const doubles = side.active.length > 1;
  const threat = st.threat;

  if (PROTECT.has(id)) {
    const stallCounter = mon.volatiles['stall'] ? (mon.volatiles['stall'].counter || 1) : 0;
    if (stallCounter >= 1) return -40; // never attempt double-Protect
    if (!doubles) {
      // In singles, Protect only makes sense to gain Speed Boost, Leftovers/Wish/Leech Seed/status tick, or stall out field turns.
      const hasPassive = abilityId(mon) === 'speedboost' || itemId(mon) === 'leftovers' ||
        foes.some(f => f.status === 'tox' || f.status === 'brn' || (f.volatiles && f.volatiles['leechseed']));
      return hasPassive && threat >= 0.5 ? 20 : 3;
    }
    // In doubles, if we already outspeed and OHKO a foe without dying first, attack instead of Protecting.
    if (!st.koFirst && st.bestOut >= 1) return 8;
    if (st.koFirst) return 48;
    return threat >= 0.85 ? 42 : threat >= 0.5 ? 18 : 2;
  }
  if (id === 'wideguard') {
    if (!doubles) return -40;
    const stallCounter = mon.volatiles['stall'] ? (mon.volatiles['stall'].counter || 1) : 0;
    if (stallCounter >= 1) return -30;
    const foeHasSpread = foes.some(f => f.moveSlots.some(ms => SPREAD_MOVE_IDS.has(ms.id)));
    return foeHasSpread ? 34 : -20;
  }
  if (opt.target && opt.target.side !== side && blockedByAbilityOrItem(battle, mon, opt.target, move)) {
    return -60;
  }
  if (id === 'tailwind') {
    if (side.sideConditions['tailwind'] || side.pokemon.filter(alive).length <= 1) return -60;
    return ctx.slower ? 55 : 22;
  }
  if (id === 'trickroom') {
    // Allow reversing Trick Room when our active team is naturally faster!
    if (field.pseudoWeather['trickroom']) return ctx.naturallyFaster ? 52 : -80;
    if (side.sideConditions['tailwind'] || side.pokemon.filter(alive).length <= 1) return -50;
    return ctx.slower ? 62 : -40;
  }
  if (id === 'followme' || id === 'ragepowder') {
    if (!doubles || st.koFirst) return -30;
    if (id === 'ragepowder' && foes.every(f => hasType(f, 'Grass') || itemId(f) === 'safetygoggles' || abilityId(f) === 'overcoat' || abilityId(f) === 'goodasgold')) return -40;
    return ctx.allyThreat >= 0.6 ? 38 : 5;
  }
  if (id === 'helpinghand' || id === 'coaching' || id === 'decorate') {
    return doubles ? 14 : -40; // main value comes from the partner's boosted hit in the joint combo step
  }
  if (id === 'reflect' || id === 'lightscreen' || id === 'auroraveil') {
    if (side.sideConditions[id] || side.pokemon.filter(alive).length <= 1 || mon.hp <= mon.maxhp * 0.45) return -60;
    if (id === 'auroraveil' && !['snowscape', 'hail'].includes(field.effectiveWeather())) return -60;
    if (id === 'reflect' && !foes.some(f => f.storedStats.atk >= f.storedStats.spa)) return -20;
    if (id === 'lightscreen' && !foes.some(f => f.storedStats.spa >= f.storedStats.atk)) return -20;
    return 26;
  }
  if (move.weather) {
    if (field.weather === move.weather) return -60;
    return 22;
  }
  if (move.terrain) {
    if (field.terrain === move.terrain) return -60;
    return 20;
  }
  // Setup moves (Swords Dance, Nasty Plot, Dragon Dance, Calm Mind, Bulk Up, Quiver Dance, Iron Defense, Shell Smash, Belly Drum...)
  const selfBoosts = setupBoosts(mon, move);
  if (selfBoosts) {
    if (id === 'bellydrum' && mon.hp <= mon.maxhp * 0.6) return -60;
    const next = addBoosts(mon.boosts, selfBoosts);
    // Nothing left to gain (every raised stat already at +6): the move fails.
    if (!Object.keys(selfBoosts).some(k => selfBoosts[k] > 0 && next[k] > (mon.boosts[k] || 0))) return -60;
    if (st.koFirst) return -10; // fainting before the boost lands
    // This turn's hit leaves it in KO range: unless the boost lets it move
    // first next turn, the boosts die with it (a passer should pass now).
    if (threat >= 0.5) {
      const saved = mon.boosts;
      mon.boosts = next;
      const outsped = foes.some(f => speedOf(f) >= speedOf(mon));
      mon.boosts = saved;
      if (outsped) return -45;
    }
    // If we already OHKO the active foes and outspeed them, take the KO instead of setting up.
    if (st.bestOut >= 1.05 && !ctx.slower) return 8;
    // Own gain: what the boosts change for this Pokémon's damage, speed and bulk.
    // A Baton Passer also sets up for whoever it will pass to.
    let gain = foes.every(f => abilityId(f) === 'unaware') ? 0 : boostValue(battle, side, mon, next) - boostValue(battle, side, mon, mon.boosts);
    if (canBatonPass(side, mon)) {
      for (const i of benchIndices(side, new Set())) {
        const cand = side.pokemon[i];
        gain = Math.max(gain, 0.85 * (relayGain(battle, side, cand, next) - relayGain(battle, side, cand, mon.boosts)));
      }
    }
    if (id === 'tidyup') gain += hazardControlValue(battle, side, id);
    if (gain <= 0) return -15;
    // Phazing / Haze / Clear Smog on the other side can erase the turn.
    const undone = foes.some(f => f.moveSlots.some(ms => ANTI_SETUP.has(ms.id) &&
      !(PHAZE_MOVES.has(ms.id) && phazeProof(battle, mon, ms.id, !!mon.volatiles['substitute']))));
    // Behind an intact Focus Sash / Sturdy the hit leaves it at 1 HP: the
    // setup still pays if the boost lets it move first next turn (checked above).
    const sashed = mon.hp === mon.maxhp && (itemId(mon) === 'focussash' || abilityId(mon) === 'sturdy') &&
      foes.every(f => { const b = bestMove(battle, f, mon); return !b.id || sashHolds(battle, f, mon, b.id); });
    const exposure = sashed && threat >= 0.5 ? 0.5 : Math.max(0, 1 - threat);
    let v = gain * exposure;
    // Last action: after this turn's hit it's in KO range of a foe that
    // outspeeds it, so attacking now is the last thing it does. A boost that
    // flips the speed order (checked above: not outsped after it) buys a
    // boosted attack next turn instead: value that attack.
    if (threat >= 0.5 && foes.some(f => speedOf(f) >= speedOf(mon))) {
      const saved = mon.boosts;
      mon.boosts = next;
      let nextTurn = 0;
      try {
        for (const f of foes) {
          const d = bestMove(battle, mon, f).d;
          nextTurn = Math.max(nextTurn, 100 * Math.min(d, f.hp) / f.maxhp + (d >= f.hp ? 75 : 0));
        }
      } finally {
        mon.boosts = saved;
      }
      v = Math.max(v, 0.85 * nextTurn);
    }
    return v * (undone ? 0.6 : 1);
  }
  if ((id === 'roar' || id === 'whirlwind') && foes.some(f => posBoosts(f.boosts) >= 2)) {
    return 30 + 8 * Math.max(...foes.map(f => posBoosts(f.boosts)));
  }
  if (move.heal || (move.flags && move.flags.heal)) return healValue(battle, side, mon, opt, ctx);
  if (id === 'substitute') {
    if (mon.volatiles['substitute'] || mon.hp <= mon.maxhp * 0.55 || threat >= 0.5) return -30;
    return 28;
  }
  if (id === 'yawn' && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if ((t.volatiles && t.volatiles['yawn']) || !canInflictStatus(battle, mon, t, move, 'slp')) return -40;
    if (foes.some(f => f.status === 'slp' || (f.volatiles && f.volatiles['yawn']))) return -15;
    return 36;
  }
  if (id === 'leechseed' && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if (hasType(t, 'Grass') || (t.volatiles && t.volatiles['leechseed']) || abilityId(t) === 'magicguard' || abilityId(t) === 'liquidooze') return -40;
    return threat < 0.65 ? 32 : 8;
  }
  if (move.status && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if (!canInflictStatus(battle, mon, t, move, move.status)) return -40;
    if (move.status === 'slp') return foes.some(f => f.status === 'slp') ? -15 : 46;
    if (STATUS_BOOSTED[move.status]) return statusSwing(battle, side, t, move.status);
    return 20;
  }
  if (id === 'encore' && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if ((t.volatiles && t.volatiles['encore']) || !t.lastMove || !movesFirst(battle, mon, 'encore', t)) return -25;
    if (itemId(t) === 'mentalherb' || abilityId(t) === 'aromaveil') return -30;
    const last = battle.dex.moves.get(t.lastMove.id || t.lastMove);
    if (last.category === 'Status' || PROTECT.has(last.id) || last.id === 'fakeout' || blockedByAbilityOrItem(battle, t, mon, last)) {
      return 54;
    }
    // Locked into an attack that barely scratches us.
    if (simDamage(battle, t, mon, last.id, false) < 0.2 * mon.hp) return 40;
    return -10;
  }
  if (id === 'taunt' && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if ((t.volatiles && t.volatiles['taunt']) || ['oblivious', 'aromaveil'].includes(abilityId(t)) || itemId(t) === 'mentalherb') return -35;
    if (!movesFirst(battle, mon, 'taunt', t)) return 4;
    const statusCount = t.moveSlots.filter(ms => {
      const mv = battle.dex.moves.get(ms.id);
      return mv.category === 'Status' && !PROTECT.has(ms.id);
    }).length;
    return statusCount >= 2 ? 42 : statusCount === 1 ? 22 : -15;
  }
  if (id === 'haze' || id === 'clearsmog') {
    const foeBoosts = foes.reduce((acc, f) => acc + Object.values(f.boosts).reduce((x, b) => x + Math.max(0, b), 0), 0);
    return foeBoosts >= 2 ? 52 : -25;
  }
  if (id === 'defog' || id === 'courtchange') {
    const v = hazardControlValue(battle, side, id);
    return v > 5 ? v : -15;
  }
  if (id === 'stealthrock' || id === 'spikes' || id === 'toxicspikes' || id === 'stickyweb') {
    if (doubles) return -25;
    const cond = side.foe.sideConditions[id];
    if (cond && (id !== 'spikes' || (cond.layers || 1) >= 3) && (id !== 'toxicspikes' || (cond.layers || 1) >= 2)) return -50;
    const totalFoes = battle.ruleTable.pickedTeamSize || 3;
    const faintedFoes = side.foe.pokemon.filter(p => p.fainted).length;
    const benchFoes = Math.max(0, totalFoes - faintedFoes - foes.length);
    return benchFoes >= 1 && threat < 0.65 ? (id === 'stealthrock' ? 34 : 26) : -20;
  }
  if (id === 'partingshot' && opt.target && opt.target.side !== side) {
    const t = opt.target;
    if (['clearbody', 'whitesmoke', 'hypercutter', 'defiant', 'competitive', 'contrary', 'goodasgold'].includes(abilityId(t)) || itemId(t) === 'clearamulet') return -30;
    return 18;
  }
  return 4;
}

/** Can `t` be flinched by `mon` using `moveId`? */
function canFlinch(battle, mon, moveId, t) {
  if (!t || !alive(t)) return false;
  if (!movesFirst(battle, mon, moveId, t)) return false;
  if (t.volatiles && t.volatiles['substitute']) return false;
  if (itemId(t) === 'covertcloak') return false;
  const mold = MOLD_BREAKERS.has(abilityId(mon));
  if (!mold && ['innerfocus', 'shielddust'].includes(abilityId(t))) return false;
  return true;
}

/**
 * Score every option of one slot. Damage options carry per-target expected
 * damage so the joint step can apply them in order and see overkill.
 */
function scoreSlot(battle, side, slot, opts, a, ctx) {
  const mon = side.active[slot];
  const st = a.slots.get(slot) || { threat: 0, koFirst: false, bestOut: 1, koBy: [], koMoves: new Map() };
  const doubles = side.active.length > 1;
  const aliveCount = side.pokemon.filter(alive).length;
  const foesAliveCount = side.foe.pokemon.filter(alive).length;

  // Best switch available (used to value pivot moves: U-turn, Volt Switch,
  // Flip Turn, Parting Shot get the switch without spending the turn on it).
  let bestSwitch = -Infinity;
  for (const o of opts) {
    if (o.kind !== 'switch') continue;
    o.base = switchScore(battle, side, slot, side.pokemon[o.idx], a, false);
    o.hits = []; o.slot = slot; o.pri = 6; o.acts = true;
    bestSwitch = Math.max(bestSwitch, o.base);
  }
  for (const o of opts) {
    if (o.kind === 'switch') continue;
    o.slot = slot;
    o.hits = [];
    o.pri = effectivePriority(battle, mon, o.move);
    // Outsped and KO'd by a foe before this move acts:
    const acts = !st.koFirst || st.koBy.every(f => movesFirst(battle, mon, o.id, f, st.koMoves.get(f)));
    o.acts = acts;
    const w = acts ? 1 : (doubles ? 0.5 : 0.15);
    let base = 0;

    if (o.id === 'batonpass') {
      // Only worth it with boosts to hand over and a recipient that's safe to bring in.
      if (!canBatonPass(side, mon) || !posBoosts(mon.boosts)) { o.base = -60; continue; }
      if (!acts) { o.base = -20; continue; }
      // Slower than every foe aiming at this slot: the passer takes those hits
      // itself and the recipient comes in free.
      const aimed = a.predicted.filter(p => p.slot === slot);
      const free = aimed.length > 0 && aimed.every(p => !movesFirst(battle, mon, 'batonpass', p.foe, p.moveId));
      const best = bestPass(battle, side, slot, mon, a, free);
      o.base = best.i < 0 ? -60 : best.score;
      continue;
    }
    if (o.move.selfSwitch && bestSwitch > -Infinity && acts) {
      base += 0.65 * Math.max(0, bestSwitch + 32); // the switch, minus the lost-turn cost
    }
    if (o.move.category !== 'Status') {
      if (['fakeout', 'firstimpression', 'matblock'].includes(o.id) && mon.activeMoveActions > 0) {
        o.base = -200;
        continue;
      }
      const spread = o.spread && o.targets.length > 1;
      let hitScale = 1;

      // Conditional priority moves: Sucker Punch / Thunderclap / Upper Hand
      if ((o.id === 'suckerpunch' || o.id === 'thunderclap') && o.target) {
        const t = o.target;
        const pred = a.byFoe.get(t);
        if (!pred || !pred.moveId || pred.frac < 0.08) {
          // Foe has no meaningful attack; likely uses Status or switches
          hitScale = 0.2;
          base -= 25;
        } else if (!movesFirst(battle, mon, o.id, t, pred.moveId)) {
          // Foe's predicted priority move acts before our Sucker Punch -> fails!
          hitScale = 0;
          base -= 60;
        } else {
          const hasStatus = t.moveSlots.some(ms => battle.dex.moves.get(ms.id).category === 'Status');
          if (hasStatus) hitScale = 0.8;
          // If we already outspeed `t` with a normal move, prefer a reliable attack when it KOs.
          if (speedOf(mon) > speedOf(t) && (pred.pri || 0) <= 0) base -= 8;
        }
        if (mon.lastMove && mon.lastMove.id === o.id) base -= 10;
      } else if (o.id === 'upperhand' && o.target) {
        const t = o.target;
        const pred = a.byFoe.get(t);
        const foeFakeOut = t.activeMoveActions === 0 && t.moveSlots.some(ms => ms.id === 'fakeout' || ms.id === 'firstimpression');
        const foePrio = foeFakeOut ? 3 : (pred ? pred.pri || 0 : 0);
        if (foePrio >= 1 && foePrio <= 3 && (foePrio < 3 || speedOf(mon) > speedOf(t))) {
          base += 42;
        } else {
          hitScale = 0;
          base -= 80;
        }
      }

      for (const t of o.targets) {
        const d = hitScale * simDamage(battle, mon, t, o.id, spread);
        if (d > 0) {
          const pred = a.byFoe.get(t);
          const sash = !o.move.multihit && abilityId(mon) !== 'parentalbond' &&
            (itemId(t) === 'focussash' || (!MOLD_BREAKERS.has(abilityId(mon)) && abilityId(t) === 'sturdy'));
          const hit = {
            t, d, w, res: residualDelta(battle, t),
            first: movesFirst(battle, mon, o.id, t, pred && pred.moveId),
            sash,
            foeThreat: pred ? pred.frac : 0,
          };
          // Multi-hit: KO odds come from the hit-count distribution, not the average.
          const dist = hitDist(mon, o.move);
          if (dist) {
            const acc = o.move.accuracy === true ? 1 : Math.min(1, o.move.accuracy / 100);
            const mean = dist.reduce((x, [k, p]) => x + k * p, 0);
            Object.assign(hit, { dist, acc, per: d / (mean * acc) });
          }
          o.hits.push(hit);
        }
      }
      // Track friendly-fire move flag so the joint combo scorer can check if the
      // partner Protects or switches into an immunity.
      o.hitsAlly = o.move.target === 'allAdjacent' && doubles;

      if (o.id === 'fakeout') {
        if (mon.activeMoveActions === 0) {
          // Only award flinch bonus if Fake Out doesn't already KO the target (KO bonus handles that)
          const kosTarget = o.hits.length === 1 && o.hits[0].d >= o.target.hp;
          base += !kosTarget && canFlinch(battle, mon, 'fakeout', o.target) ? (doubles ? 45 : 22) : 0;
        } else {
          base -= 200;
        }
      }
      // Self-KO moves (Explosion, Self-Destruct, Misty Explosion, Final Gambit)
      if (o.move.selfdestruct || o.id === 'finalgambit') {
        if (aliveCount <= 1) base -= 500;
        else base -= 95 * (mon.hp / mon.maxhp);
      }
      // Recharge moves (Hyper Beam, Giga Impact, Blast Burn, Frenzy Plant, Hydro Cannon, Meteor Assault)
      if (o.move.flags && o.move.flags.recharge) {
        const kosLastFoe = foesAliveCount <= 1 && o.hits.length === 1 && o.hits[0].d >= o.hits[0].t.hp;
        if (!kosLastFoe) base -= 42;
      }
      // Self stat drops (Draco Meteor, Overheat, Make It Rain, Close Combat)
      if (o.move.self && o.move.self.boosts) {
        const contrary = abilityId(mon) === 'contrary';
        const whiteHerb = itemId(mon) === 'whiteherb';
        for (const [stat, val] of Object.entries(o.move.self.boosts)) {
          if (val < 0) {
            if (contrary) base += 12 * Math.abs(val);
            else if (whiteHerb) base -= 1.5 * Math.abs(val);
            else base -= (stat === 'atk' || stat === 'spa' ? 6 : 3) * Math.abs(val);
          } else if (val > 0) {
            base += (contrary ? -8 : 8) * val;
          }
        }
      }
      if (o.move.recoil || o.move.mindBlownRecoil) base -= 5;
      if (o.move.flags && o.move.flags.charge && !mon.volatiles['twoturnmove']) {
        const wx = battle.field.effectiveWeather();
        const instant = mon.hasItem('powerherb') ||
          (['solarbeam', 'solarblade'].includes(o.id) && (['sunnyday', 'desolateland'].includes(wx) || abilityId(mon) === 'megasol')) ||
          (o.id === 'electroshot' && ['raindance', 'primordialsea'].includes(wx));
        if (!instant) base -= 28; // a turn of exposure before the hit lands
      }
      if (o.id === 'knockoff' && o.target && itemId(o.target)) {
        const it = battle.dex.items.get(o.target.item);
        if (!it.megaStone) base += 8;
      }
      if (o.id === 'rapidspin' || o.id === 'mortalspin') base += hazardControlValue(battle, side, o.id);
      // Status side effects (Scald, Nuzzle, Body Slam...), chance-weighted.
      const sec = o.move.secondaries || (o.move.secondary ? [o.move.secondary] : []);
      if (abilityId(mon) !== 'sheerforce') {
        for (const sc of sec) {
          if (!sc.status || !STATUS_BOOSTED[sc.status]) continue;
          const chance = Math.min(100, (sc.chance || 100) * (abilityId(mon) === 'serenegrace' ? 2 : 1)) / 100;
          for (const h of o.hits) {
            const t = h.t;
            if (h.d >= t.hp || itemId(t) === 'covertcloak' || abilityId(t) === 'shielddust') continue;
            if (!canInflictStatus(battle, mon, t, o.move, sc.status)) continue;
            base += 0.8 * chance * statusSwing(battle, side, t, sc.status);
          }
        }
      }
      if (o.move.secondary && o.move.secondary.boosts && o.move.secondary.chance === 100) {
        if (o.move.secondary.boosts.spe < 0) base += ctx.slower ? 12 : 4;
        if (o.move.secondary.boosts.spa < 0 || o.move.secondary.boosts.atk < 0) base += 6;
      }
    } else {
      let sv = statusValue(battle, side, mon, o, st, ctx);
      if (sv > 0) sv *= foeDisruption(battle, side, mon, o.move);
      base += PROTECT.has(o.id) || o.id === 'wideguard' ? sv : sv * w;
    }
    o.base = base;
  }
}

/** Value of a set of damage hits applied in order against a running HP map. */
function applyHits(hpLeft, hits, mult = 1, wOverride = null) {
  let v = 0;
  for (const h of hits) {
    const key = h.t.side.n + ':' + h.t.position;
    const left = hpLeft.has(key) ? hpLeft.get(key) : h.t.hp;
    if (left <= 0) continue;
    const w = wOverride !== null ? wOverride : (h.w === undefined ? 1 : h.w);
    let d = h.d * mult;
    // Focus Sash / Sturdy leaves a full-HP target at 1 HP on the first single-hit attack
    if (h.sash && left === h.t.maxhp && d >= left) {
      d = Math.max(0, left - 1);
    }
    const dealt = Math.min(d, left);
    v += w * 100 * dealt / h.t.maxhp;
    // Chance this hit KOs: certain/never for a single hit; for a multi-hit
    // move, the odds of enough hits landing (Focus Sash can't stop 2+ hits).
    let koP = d >= left ? 1 : 0;
    if (h.dist) {
      koP = 0;
      for (const [k, p] of h.dist) if (k * h.per * mult >= left) koP += p;
      koP *= h.acc;
    }
    if (koP > 0) {
      v += w * koP * (h.first ? 75 + 20 * Math.min(1, h.foeThreat || 0) : 40);
    } else if (h.res < 0 && left - dealt + h.res <= 0) {
      v += w * 30; // the end-of-turn damage finishes it
    }
    if (w >= 0.5) hpLeft.set(key, left - dealt);
  }
  return v;
}

function heuristicTurn(battle, side, req, rng) {
  const restoreMega = applyMegaProjection(battle);
  try {
    const used = new Set();
    const n = side.active.length;
    const a = assess(battle, side);
    const mine = side.active.filter(alive), foes = foesOf(side);
    const effAvg = arr => arr.reduce((x, p) => x + speedOf(p), 0) / Math.max(1, arr.length);
    const rawAvg = arr => arr.reduce((x, p) => x + p.storedStats.spe, 0) / Math.max(1, arr.length);
    const ctx = {
      slower: effAvg(mine) < effAvg(foes),
      naturallyFaster: rawAvg(mine) > rawAvg(foes),
      allyThreat: 0,
      a,
    };

    const perSlot = [];
    for (let slot = 0; slot < n; slot++) {
      const act = req.active[slot];
      const mon = side.active[slot];
      if (!act || !alive(mon) || act.commanding) { perSlot.push(null); continue; }
      ctx.allyThreat = (a.slots.get(1 - slot) || {}).threat || 0;
      const opts = slotOptions(battle, side, slot, act, used);
      scoreSlot(battle, side, slot, opts, a, ctx);
      // Standalone score (including default friendly-fire if partner doesn't protect) to prune per-slot candidates
      const hpTmp = new Map();
      for (const o of opts) {
        let allyPen = 0;
        if (o.hitsAlly) {
          const ally = side.active[1 - slot];
          if (alive(ally)) {
            const d = simDamage(battle, mon, ally, o.id, true);
            if (d > 0) allyPen = 110 * Math.min(d, ally.hp) / ally.maxhp + (d >= ally.hp ? 80 : 0);
          }
        }
        o.solo = o.base - allyPen + applyHits(new Map(hpTmp), o.hits);
      }
      opts.sort((x, y) => y.solo - x.solo);
      perSlot.push(opts.slice(0, 7));
    }

    // Joint selection: both slots evaluated together in true priority + speed order
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
      const order = [...c].sort((x, y) => {
        if (x.pri !== y.pri) return y.pri - x.pri;
        return speedOf(side.active[y.slot]) - speedOf(side.active[x.slot]);
      });
      let s = 0;
      const rescuedSlots = new Set();

      // Check doubles synergy between the two slots
      let boostSlot = -1, boostMult = 1;
      if (c.length === 2) {
        for (let idx = 0; idx < 2; idx++) {
          const me = c[idx], partner = c[1 - idx];
          if (me.kind === 'move' && (me.id === 'helpinghand' || me.id === 'decorate' || me.id === 'coaching')) {
            if (partner.kind === 'move' && partner.move.category !== 'Status' && partner.hits.length > 0) {
              boostSlot = partner.slot;
              boostMult = me.id === 'decorate' ? 2.0 : 1.5;
            } else {
              s -= 60; // wasted Helping Hand / Coaching into a non-attacking partner
            }
          }
          // Redirection or Fake Out flinch from `me` neutralizing threats on `partner`
          const pSt = a.slots.get(partner.slot);
          if (pSt && pSt.koFirst && !partner.acts && me.kind === 'move') {
            if (me.id === 'followme' || me.id === 'ragepowder') {
              rescuedSlots.add(partner.slot);
            } else if ((me.id === 'fakeout' || me.id === 'upperhand') && pSt.koBy.length === 1 && me.target === pSt.koBy[0] && canFlinch(battle, side.active[me.slot], me.id, me.target)) {
              rescuedSlots.add(partner.slot);
            }
          }
        }
      }

      for (const o of order) {
        const mon = side.active[o.slot];
        const partnerOpt = c.find(x => x.slot !== o.slot);
        const pSt = a.slots.get(o.slot);
        // Did our faster partner just KO every foe that was going to KO `mon` before `mon` acts?
        let wOverride = null;
        if (pSt && pSt.koFirst && !o.acts) {
          const allDead = pSt.koBy.every(f => {
            const key = f.side.n + ':' + f.position;
            return hpLeft.has(key) && hpLeft.get(key) <= 0;
          });
          if (allDead || rescuedSlots.has(o.slot)) wOverride = 0.9;
        }
        const mult = o.slot === boostSlot ? boostMult : 1;
        s += o.base + applyHits(hpLeft, o.hits, mult, wOverride);

        // Friendly-fire spread move check (`Earthquake`, `Discharge`, `Surf`...):
        // 0 damage if partner Protects / Wide Guards or switches to an immune mon!
        if (o.hitsAlly && n > 1) {
          const partnerProtected = partnerOpt && partnerOpt.kind === 'move' && (PROTECT.has(partnerOpt.id) || partnerOpt.id === 'wideguard');
          if (!partnerProtected) {
            const allyMon = partnerOpt && partnerOpt.kind === 'switch' ? side.pokemon[partnerOpt.idx] : side.active[1 - o.slot];
            if (alive(allyMon)) {
              const d = simDamage(battle, mon, allyMon, o.id, true) * mult;
              if (d > 0) s -= 110 * Math.min(d, allyMon.hp) / allyMon.maxhp + (d >= allyMon.hp ? 80 : 0);
            }
          }
        }
      }
      // Two Protects on one turn wastes the whole turn unless both are in danger
      if (c.length === 2 && c.every(o => o.kind === 'move' && PROTECT.has(o.id))) s -= 35;
      return { c, score: s };
    });

    const best = pickTop(rng, scored, 0.08).c;
    const bySlot = new Map(best.map(o => [o.slot, o]));
    // Only one Mega per battle: if both slots have `' mega'`, prioritize the slot
    // that is actively attacking (not Protecting) or has the higher Mega gain.
    const megaSlots = best.filter(o => o && o.choice && o.choice.endsWith(' mega'));
    let keepMegaSlot = megaSlots.length ? megaSlots[0].slot : -1;
    if (megaSlots.length > 1) {
      const attacker = megaSlots.find(o => o.kind === 'move' && o.move.category !== 'Status' && !PROTECT.has(o.id));
      if (attacker) keepMegaSlot = attacker.slot;
    }
    const parts = [];
    for (let slot = 0; slot < n; slot++) {
      const o = bySlot.get(slot);
      if (!o) { parts.push('pass'); continue; }
      let ch = o.choice;
      if (ch.endsWith(' mega') && slot !== keepMegaSlot) ch = ch.slice(0, -5);
      parts.push(ch);
    }
    return parts.join(', ');
  } finally {
    restoreMega();
  }
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
      })), 0.1, 1000) : opts[0];
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
  battle._dmgCache = new Map();
  try {
    if (req.teamPreview) return teamPreview(policy, battle, side, req, rng);
    if (req.forceSwitch) return forcedSwitch(policy, battle, side, req, rng);
    if (policy === 'heuristic') return heuristicTurn(battle, side, req, rng);
    return simpleTurn(policy, battle, side, req, rng);
  } finally {
    battle._dmgCache = null;
    restore();
  }
}

const POLICIES = ['random', 'greedy', 'heuristic'];

module.exports = { choose, POLICIES, INFO_MODES, applyView, simDamage, formulaDamage,
  // internals, for the self-test only
  _test: { foeDisruption, statusSwing, residualDelta, switchEval, assess, sashHolds } };
