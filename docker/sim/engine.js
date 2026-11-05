'use strict';
/**
 * Battle engine wrapper: runs one Pokémon Showdown battle (Champions mod)
 * synchronously in-process and summarises it.
 *
 * Battles are driven directly through `Battle#choose`, not through
 * BattleStream and the text protocol, so the only per-turn cost is the
 * simulator itself plus the decision policy.
 */

const { Battle, Teams, TeamValidator, Dex } = require(process.env.PS_SIM || '/ps/dist/sim');
const { choose } = require('./policies');

// Our format names -> Showdown format ids. Reg M-C is the live regulation;
// both formats are "Flat Rules" (Lv 50, Item/Species Clause, bring 4 of 6
// for VGC and 3 of 6 for BSS) on the `champions` mod.
const FORMATS = {
  vgc: 'gen9championsvgc2026regmc',
  bss: 'gen9championsbssregmc',
};

// A battle that hasn't ended after this many turns is scored as a tie (two
// walls trading Protects forever, or a policy stuck in a loop).
const MAX_TURNS = 300;

function formatId(format) {
  const id = FORMATS[String(format || 'vgc').toLowerCase()] || format;
  if (!Dex.formats.get(id).exists) throw new Error(`unknown format ${format}`);
  return id;
}

/** Deterministic 32-bit PRNG for policies (separate from the battle's own). */
function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/** Mix a base seed and a job index into an independent 32-bit seed. */
function jobSeed(base, k) {
  let h = (base ^ Math.imul(k + 1, 0x9E3779B1)) >>> 0;
  h = Math.imul(h ^ h >>> 16, 0x85EBCA6B) >>> 0;
  h = Math.imul(h ^ h >>> 13, 0xC2B2AE35) >>> 0;
  return (h ^ h >>> 16) >>> 0;
}

function battleSeed(s) {
  return [s & 0xffff, (s >>> 16) & 0xffff, (s * 7 + 13) & 0xffff, 0x2a];
}

/** Parse Showdown team text into sets; throws on an empty/garbled paste. */
function parseTeam(paste) {
  const sets = Teams.import(String(paste || ''));
  if (!sets || !sets.length) throw new Error('could not parse team (expected Showdown paste text)');
  for (const s of sets) {
    // Name every member after its species so log identities are unambiguous
    // (Species Clause guarantees uniqueness within a team).
    s.name = s.species;
    if (!s.level) s.level = 50;
  }
  return sets;
}

const validators = {};
/** Showdown's own legality check for a format; returns problem strings or []. */
function validate(formatid, sets) {
  validators[formatid] = validators[formatid] || new TeamValidator(formatid);
  return validators[formatid].validateTeam(sets.map(s => ({ ...s }))) || [];
}

const cloneSets = sets => sets.map(s => ({ ...s, moves: [...s.moves], evs: { ...s.evs }, ivs: s.ivs && { ...s.ivs } }));

/**
 * Run one battle to completion.
 *
 * @returns {{winner:number, turns:number, sides:object[], fallbacks:number, log?:string[]}}
 *   winner: 0 = p1, 1 = p2, -1 = tie / turn cap.
 */
function runBattle({ formatid, setsA, setsB, policyA = 'heuristic', policyB = 'heuristic', seed = 1, record = false, view = null }) {
  const rng = mulberry32(seed ^ 0x5bd1e995);
  const battle = new Battle({ formatid, seed: battleSeed(seed) });
  let fallbacks = 0;
  try {
    battle.setPlayer('p1', { name: 'A', team: cloneSets(setsA) });
    battle.setPlayer('p2', { name: 'B', team: cloneSets(setsB) });
    const policies = [policyA, policyB];
    let guard = 0;
    while (!battle.ended && battle.turn <= MAX_TURNS && guard++ < 4 * MAX_TURNS) {
      for (let i = 0; i < 2; i++) {
        const side = battle.sides[i];
        const req = side.activeRequest;
        if (!req || req.wait || side.isChoiceDone()) continue;
        let choice = null;
        try {
          choice = choose(policies[i], battle, side, rng, view);
        } catch (e) {
          choice = null; // a policy bug must never kill the battle
        }
        if (choice === null || !battle.choose(side.id, choice)) {
          fallbacks++;
          side.clearChoice();
          battle.choose(side.id, 'default');
        }
      }
    }
    return {
      winner: battle.winner === 'A' ? 0 : battle.winner === 'B' ? 1 : -1,
      turns: battle.turn,
      sides: summarize(battle),
      fallbacks,
      ...(record ? { log: condenseLog(battle.log) } : {}),
    };
  } finally {
    battle.destroy();
  }
}

/** Per-side outcome: who was brought/led, who fainted, KOs, Mega usage. */
function summarize(battle) {
  const kos = [{}, {}];
  // KO attribution from the protocol log: credit the last move user of the
  // other side, unless the fainting Pokémon was last hit by indirect damage
  // (weather, poison, recoil — the `[from]` damage lines).
  // Reports are keyed by the species *brought* (set.species): Showdown names
  // an un-nicknamed regional form after its base species in battle, so
  // "Slowking-Galar" would otherwise report as "Slowking".
  const speciesOf = battle.sides.map(side => new Map(side.pokemon.map(p => [p.name, p.set.species])));
  let lastMover = null;
  const indirect = new Set();
  for (const line of battle.log) {
    if (line.startsWith('|move|')) {
      const id = line.split('|')[2];
      const s = id[1] === '1' ? 0 : 1;
      const name = id.slice(id.indexOf(': ') + 2);
      lastMover = { side: s, name: speciesOf[s].get(name) || name };
      indirect.clear();
    } else if (line.startsWith('|-damage|') && line.includes('[from]')) {
      indirect.add(line.split('|')[2]);
    } else if (line.startsWith('|turn|') || line.startsWith('|upkeep')) {
      lastMover = null;
    } else if (line.startsWith('|faint|')) {
      const id = line.split('|')[2];
      const fside = id[1] === '1' ? 0 : 1;
      if (lastMover && lastMover.side !== fside && !indirect.has(id)) {
        kos[lastMover.side][lastMover.name] = (kos[lastMover.side][lastMover.name] || 0) + 1;
      }
    }
  }
  // Leads = who switched in before turn 1. Not side.pokemon's order: Showdown
  // swaps party slots on every switch, so by the end of a battle its first
  // slots hold whoever was active last, not who led.
  const leads = [[], []];
  for (const line of battle.log) {
    if (line.startsWith('|turn|')) break;
    if (line.startsWith('|switch|') || line.startsWith('|drag|')) {
      const id = line.split('|')[2];
      const s = id[1] === '1' ? 0 : 1;
      const name = id.slice(id.indexOf(': ') + 2);
      const species = speciesOf[s].get(name) || name;
      if (!leads[s].includes(species)) leads[s].push(species);
    }
  }
  const picked = battle.ruleTable.pickedTeamSize || 6;
  return battle.sides.map((side, i) => {
    // the brought Pokémon stay in the first `picked` slots (switches only swap among them)
    const brought = side.pokemon.slice(0, picked);
    const sp = p => p.set.species;
    return {
      brought: brought.map(sp),
      leads: leads[i],
      fainted: brought.filter(p => p.fainted).map(sp),
      mega: (brought.find(p => p.species.isMega) || { set: {} }).set.species || null,
      kos: kos[i],
      hp_left: +(brought.reduce((a, p) => a + (p.fainted ? 0 : p.hp / p.maxhp), 0)).toFixed(2),
    };
  });
}

// ---------------- human-readable log ----------------

const who = id => {
  const side = id[1] === '1' ? 'A' : 'B';
  return `${side}:${id.slice(id.indexOf(': ') + 2)}`;
};
const hp = s => (s || '').split(' ')[0].replace(/\/100$/, '%');

/** Condense the Showdown protocol log into short readable lines. */
function condenseLog(log) {
  const out = [];
  let skipSecret = false;
  for (const line of log) {
    if (line.startsWith('|split|')) { skipSecret = true; continue; }
    if (skipSecret) { skipSecret = false; continue; } // keep the public (percentage) copy only
    const p = line.split('|');
    const tag = p[1];
    const from = p.find(x => x.startsWith('[from]'));
    switch (tag) {
      case 'turn': out.push(`== Turn ${p[2]} ==`); break;
      case 'switch': case 'drag': out.push(`${who(p[2])} enters (${hp(p[4])})`); break;
      case 'move': out.push(`${who(p[2])} used ${p[3]}${p[4] && p[4].includes(':') ? ' -> ' + who(p[4]) : ''}${p.includes('[miss]') ? ' (missed)' : ''}${p.includes('[still]') ? '' : ''}`); break;
      case '-damage': out.push(`  ${who(p[2])} -> ${hp(p[3])}${from ? ' ' + from : ''}`); break;
      case '-heal': out.push(`  ${who(p[2])} healed -> ${hp(p[3])}${from ? ' ' + from : ''}`); break;
      case 'faint': out.push(`  ${who(p[2])} fainted`); break;
      case '-mega': out.push(`${who(p[2])} Mega Evolved (${p[4] || ''})`); break;
      case '-supereffective': out.push('  super effective'); break;
      case '-resisted': out.push('  resisted'); break;
      case '-immune': out.push(`  ${who(p[2])} is immune`); break;
      case '-crit': out.push('  critical hit'); break;
      case '-miss': out.push(`  missed ${p[3] ? who(p[3]) : ''}`); break;
      case '-fail': out.push(`  ${who(p[2])}: failed`); break;
      case '-status': out.push(`  ${who(p[2])} is ${p[3]}`); break;
      case '-boost': case '-unboost': out.push(`  ${who(p[2])} ${p[3]} ${tag === '-boost' ? '+' : '-'}${p[4]}`); break;
      case '-weather': if (p[2] !== 'none' && !p.includes('[upkeep]')) out.push(`Weather: ${p[2]}`); break;
      case '-fieldstart': out.push(`Field: ${p[2].replace('move: ', '')} started`); break;
      case '-fieldend': out.push(`Field: ${p[2].replace('move: ', '')} ended`); break;
      case '-sidestart': out.push(`${p[2].startsWith('p1') ? 'A' : 'B'} side: ${p[3].replace('move: ', '')}`); break;
      case '-activate': if (/Protect|Detect|Spiky|King|Silk|Baneful|Burning/.test(p[3] || '')) out.push(`  ${who(p[2])} protected itself`); break;
      case 'cant': out.push(`  ${who(p[2])} can't move (${p[3]})`); break;
      case 'win': out.push(`Winner: ${p[2]}`); break;
      case 'tie': out.push('Tie'); break;
      default: break;
    }
  }
  return out;
}

/** Preload the dex data for every served format (moves the cost off the first battle). */
function warm() {
  for (const id of Object.values(FORMATS)) Dex.forFormat(Dex.formats.get(id)).includeData();
}

module.exports = { FORMATS, formatId, parseTeam, validate, runBattle, jobSeed, mulberry32, condenseLog, warm, MAX_TURNS };
