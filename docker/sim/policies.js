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
    if (foeTarget && !mold && (ab === 'goodasgold' || (ab === 'magicbounce' && move.flags && move.flags.reflectable))) return true;
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
    cacheKey = `${att.side.n}:${att.position}:${att.species.id}:${att.ability}:${def.side.n}:${def.position}:${def.species.id}:${def.ability}:${ def.hp}:${moveId}:${spreadHit ? 1 : 0}:${battle.field.weather}:${battle.field.terrain}`;
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

/** Best (moveId, expected damage, priority) of `att` against `def`. */
function bestMove(battle, att, def) {
  let best = { id: null, d: 0, pri: 0 };
  for (const ms of att.moveSlots) {
    if (ms.pp <= 0 || ms.disabled) continue;
    if (ms.id === 'upperhand' || ms.id === 'focuspunch') continue; // situational: not a reliable threat
    const d = simDamage(battle, att, def, ms.id, false);
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
          absorb += scale * simDamage(battle, foe, cand, pr.moveId, false) / Math.max(1, cand.hp);
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
    if (s.koFirst) v += 55;                       // would faint before acting
    else if (s.threat >= 0.6) v += 30 * Math.min(1, s.threat);
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

function memberPreviewScore(battle, p, theirs, doubles, lead) {
  const dex = battle.dex;
  const off = (a, b) => {
    let best = 0;
    for (const ms of a.moveSlots) best = Math.max(best, formulaDamage(dex, a, b, ms.id) / b.maxhp);
    return Math.min(best, 1.2) * (a.storedStats.spe > b.storedStats.spe ? 1.15 : 1);
  };
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

function previewScores(battle, side, lead) {
  const doubles = side.active.length > 1;
  const theirs = side.foe.pokemon.map(p => previewForm(battle, p, true));
  return side.pokemon.map((p, i) => {
    const megaForm = previewForm(battle, p, true);
    const isMegaHolder = megaForm !== p;
    const sMega = memberPreviewScore(battle, megaForm, theirs, doubles, lead);
    const sBase = isMegaHolder ? memberPreviewScore(battle, p, theirs, doubles, lead) - 0.45 : sMega;
    return { i, s: sMega, sMega, sBase, isMegaHolder, form: megaForm };
  });
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
  const leadScore = new Map(bring.map(i => [
    i,
    (leadInfo[i].isMegaHolder && i !== primaryMega ? leadInfo[i].sBase : leadInfo[i].sMega) + (rng() - 0.5) * 0.08,
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
  const selfBoosts = (move.target === 'self' && move.boosts) ||
    (id === 'bellydrum' ? { atk: 6 } : id === 'shellsmash' ? { atk: 2, spa: 2, spe: 2 } : id === 'curse' && !hasType(mon, 'Ghost') ? { atk: 1, def: 1 } : null);
  if (selfBoosts) {
    if (id === 'bellydrum' && mon.hp <= mon.maxhp * 0.6) return -60;
    const cur = Math.max(...Object.keys(selfBoosts).map(k => mon.boosts[k] || 0));
    if (cur >= SETUP_BOOST || st.koFirst || threat >= 0.55) return -10;
    if (foes.some(f => abilityId(f) === 'unaware')) return 2;
    // Check that we actually carry an attacking move that uses the boosted offensive stat.
    const hasPhys = mon.moveSlots.some(ms => battle.dex.moves.get(ms.id).category === 'Physical');
    const hasSpec = mon.moveSlots.some(ms => battle.dex.moves.get(ms.id).category === 'Special');
    const hasBodyPress = mon.moveSlots.some(ms => ms.id === 'bodypress');
    if (selfBoosts.atk && !selfBoosts.spa && !hasPhys) return -20;
    if (selfBoosts.spa && !selfBoosts.atk && !hasSpec) return -20;
    if (selfBoosts.def && !selfBoosts.atk && !selfBoosts.spa && !hasBodyPress) return 10;
    // If we already OHKO the active foes and outspeed them, take the KO instead of setting up.
    if (st.bestOut >= 1.05 && !ctx.slower) return 8;
    const gain = Math.max(selfBoosts.atk || 0, selfBoosts.spa || 0, selfBoosts.def || 0);
    return (gain >= 2 ? 38 : 28) * (1 - threat);
  }
  if (move.heal || (move.flags && move.flags.heal)) {
    if (mon.hp >= mon.maxhp * 0.85) return -30;
    return threat < 0.65 ? (1 - mon.hp / mon.maxhp) * 72 : 8;
  }
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
    if (move.status === 'brn') return t.storedStats.atk > t.storedStats.spa * 1.1 ? 38 : 10;
    if (move.status === 'par') return speedOf(t) > speedOf(mon) ? 34 : 12;
    if (move.status === 'tox' || move.status === 'psn') return 26;
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
          o.hits.push({
            t, d, w,
            first: movesFirst(battle, mon, o.id, t, pred && pred.moveId),
            sash,
            foeThreat: pred ? pred.frac : 0,
          });
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
      if ((o.id === 'rapidspin' || o.id === 'mortalspin') && (side.sideConditions['stealthrock'] || side.sideConditions['spikes'] || side.sideConditions['stickyweb'])) {
        base += 18;
      }
      if (o.move.secondary && o.move.secondary.boosts && o.move.secondary.chance === 100) {
        if (o.move.secondary.boosts.spe < 0) base += ctx.slower ? 12 : 4;
        if (o.move.secondary.boosts.spa < 0 || o.move.secondary.boosts.atk < 0) base += 6;
      }
    } else {
      const sv = statusValue(battle, side, mon, o, st, ctx);
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
    if (d >= left) {
      v += w * (h.first ? 75 + 20 * Math.min(1, h.foeThreat || 0) : 40);
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

module.exports = { choose, POLICIES, INFO_MODES, applyView, simDamage, formulaDamage };
