'use strict';
/**
 * Build-time self-test (run by the Dockerfile; a failure fails the image build).
 *
 *   1. Legality: the fixture teams pass Reg M-C; a "Past" item is rejected.
 *   2. Robustness: every policy, both formats, zero sim errors.
 *   3. Determinism: identical seeds give identical results.
 *   4. Policy ladder: heuristic beats random and greedy.
 *   5. Tactics: takes the super-effective hit; doesn't double into a foe that
 *      one attack already KOs.
 *
 * Usage: node selftest.js [--quick]
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { Battle } = require(process.env.PS_SIM || '/ps/dist/sim');
const engine = require('./engine');
const { choose } = require('./policies');
const { Pool } = require('./pool');
const api = require('./api');

const TEAMS = JSON.parse(fs.readFileSync(path.join(__dirname, 'selftest-teams.json'), 'utf8'));
const quick = process.argv.includes('--quick');
const results = [];
const ok = (name, detail) => { results.push(name); console.log(`ok - ${name}${detail ? ' ' + JSON.stringify(detail) : ''}`); };

async function main() {
  const pool = new Pool(parseInt(process.env.SIM_WORKERS || '0', 10) || Math.min(8, require('os').cpus().length));
  try {
    // 1. legality
    for (const fmt of ['vgc', 'bss']) {
      const v = await api.handle(pool, 'validate', { format: fmt, teams: TEAMS.map((p, i) => ({ id: i, paste: p })) });
      assert(v.ok && v.teams.every(t => t.valid), `fixtures should be legal in ${fmt}: ${JSON.stringify(v)}`);
    }
    const specs = TEAMS[0].replace('Kingambit @ Chople Berry', 'Kingambit @ Choice Band');
    const bad = await api.handle(pool, 'validate', { format: 'vgc', teams: [{ id: 'x', paste: specs }] });
    assert(!bad.teams[0].valid, 'Choice Band is a Past item in Champions and must be rejected');
    ok('legality');

    // 2. robustness across policies and formats
    const opponents = TEAMS.slice(1).map((p, i) => ({ id: `t${i + 1}`, paste: p }));
    for (const fmt of ['vgc', 'bss']) {
      for (const policy of ['random', 'greedy', 'heuristic']) {
        const r = await api.handle(pool, 'simulate', { format: fmt, team: TEAMS[0], opponents, games_per_opponent: quick ? 6 : 20, policy, seed: 3 });
        assert(r.ok, JSON.stringify(r));
        assert.strictEqual(r.errors, 0, `${fmt}/${policy} sim errors: ${JSON.stringify(r.error_samples)}`);
        assert(r.policy_fallbacks <= r.games * 2, `${fmt}/${policy} too many fallbacks: ${r.policy_fallbacks}`);
        ok(`robust ${fmt}/${policy}`, { games: r.games, fallbacks: r.policy_fallbacks, bps: r.battles_per_sec });
      }
    }

    // 3. determinism: same seed -> same games, whatever the pool size
    const req = { format: 'vgc', team: TEAMS[0], opponents, games_per_opponent: 10, seed: 42 };
    const a = await api.handle(pool, 'simulate', req);
    const b = await api.handle(pool, 'simulate', req);
    assert.deepStrictEqual(a.per_opponent, b.per_opponent, 'same seed must reproduce the same results');
    assert.deepStrictEqual(a.members, b.members);
    const solo = new Pool(1);
    try {
      const c = await api.handle(solo, 'simulate', req);
      assert.deepStrictEqual(c.per_opponent, a.per_opponent, 'results must not depend on how many workers ran them');
      assert.deepStrictEqual(c.members, a.members);
    } finally {
      await solo.close();
    }
    const other = await api.handle(pool, 'simulate', { ...req, seed: 43 });
    assert.notDeepStrictEqual(other.per_opponent, a.per_opponent, 'a different seed should play different games');
    ok('determinism');

    // 3b. bad input fails cleanly, never crashes the sidecar
    for (const [label, body, op] of [
      ['garbled team', { format: 'vgc', team: 'not a team', opponents }, 'simulate'],
      ['unknown policy', { format: 'vgc', team: TEAMS[0], opponents, policy: 'genius' }, 'simulate'],
      ['unknown information mode', { format: 'vgc', team: TEAMS[0], opponents, information: 'psychic' }, 'simulate'],
      ['no opponents', { format: 'vgc', team: TEAMS[0], opponents: [] }, 'simulate'],
      ['unknown format', { format: 'gen1ou', team: TEAMS[0], opponents }, 'simulate'],
      ['over the cap', { format: 'vgc', team: TEAMS[0], opponents, games_per_opponent: 10 ** 7 }, 'simulate'],
      ['unknown op', {}, 'explode'],
    ]) {
      const r = await api.handle(pool, op, body);
      assert(r && r.ok === false && typeof r.error === 'string', `${label}: expected a clean error, got ${JSON.stringify(r).slice(0, 200)}`);
    }
    const illegalOpp = await api.handle(pool, 'simulate', { format: 'vgc', team: TEAMS[0], games_per_opponent: 2,
      opponents: [...opponents, { id: 'bad', paste: TEAMS[1].replace(/@ [^\r\n]+/, '@ Choice Band') }] });
    assert(illegalOpp.ok && illegalOpp.opponents_dropped.some(d => d.id === 'bad'), 'an illegal opponent is dropped and reported');
    ok('bad input');

    // 3c. leads are who entered before turn 1 (not whoever is active at the end)
    for (const fmt of ['bss', 'vgc']) {
      for (const seed of [1, 2, 3, 4, 5]) {
        const r = await api.handle(pool, 'replay', { format: fmt, team_a: TEAMS[0], team_b: TEAMS[1], seed });
        assert(r.ok, JSON.stringify(r));
        const opening = r.log.slice(0, r.log.indexOf('== Turn 1 ==')).filter(l => / enters /.test(l));
        const enteredA = opening.filter(l => l.startsWith('A:')).map(l => l.slice(2, l.indexOf(' enters')));
        assert.deepStrictEqual(r.team_a.leads, enteredA, `${fmt} seed ${seed}: leads ${r.team_a.leads} vs log ${enteredA}`);
        assert.strictEqual(r.team_a.leads.length, fmt === 'vgc' ? 2 : 1);
      }
    }
    ok('leads');

    // 4. policy ladder (mirror-free: every team plays every other)
    const ladderN = quick ? 30 : 80;
    for (const [fmt, weak, minWr] of [['vgc', 'random', 0.75], ['bss', 'random', 0.75], ['vgc', 'greedy', 0.55], ['bss', 'greedy', 0.55]]) {
      let w = 0, n = 0;
      for (let i = 0; i < TEAMS.length; i++) {
        const opp = TEAMS.filter((_, j) => j !== i).map((p, j) => ({ id: j, paste: p }));
        const r = await api.handle(pool, 'simulate', { format: fmt, team: TEAMS[i], opponents: opp, games_per_opponent: Math.ceil(ladderN / opp.length), policy: 'heuristic', opponent_policy: weak, seed: 7 + i });
        w += r.win_rate * r.games; n += r.games;
      }
      const wr = w / n;
      assert(wr >= minWr, `heuristic vs ${weak} in ${fmt}: win rate ${wr.toFixed(3)} < ${minWr}`);
      ok(`ladder heuristic>${weak} ${fmt}`, { win_rate: +wr.toFixed(3), games: n });
    }

    // 5. tactics
    tacticSuperEffective();
    tacticNoOverkill();
    tacticSwitchWhenDoomed();
    tacticSwitchIntoImmunity();
    tacticStayWhenWinning();
    tacticNoFirstImpressionAfterTurn1();
    tacticImmunitiesAndBlockers();
    tacticMegaTurnProjection();
    tacticDoublesProtectEarthquake();
    tacticFastAllyKoSavesPartner();
    ok('tactics');

    // 6. information modes: beliefs are used while deciding, truth restored after
    informationViews();
    for (const information of ['open_sheets', 'closed']) {
      const r = await api.handle(pool, 'simulate', { format: 'vgc', team: TEAMS[0], opponents, games_per_opponent: 4, seed: 2, information, beliefs: SAMPLE_BELIEFS });
      assert(r.ok && r.errors === 0 && r.information === information, JSON.stringify(r).slice(0, 300));
    }
    ok('information modes');
  } finally {
    await pool.close();
  }
  console.log(`selftest passed (${results.length} checks)`);
}

const set = (species, moves, extra = {}) => ({
  name: species, species, moves, ability: extra.ability || '', item: extra.item || '', nature: extra.nature || 'Hardy',
  evs: { hp: 2, atk: 32, def: 0, spa: 0, spd: 0, spe: 32 }, ivs: { hp: 31, atk: 31, def: 31, spa: 31, spd: 31, spe: 31 }, level: 50, gender: '',
});

function rngConst() { let x = 0.42; return () => (x = (x * 9301 + 0.49297) % 1); }

/** Singles: Garchomp facing Gholdengo should pick Earthquake (2x), not Dragon Claw (0.5x). */
function tacticSuperEffective() {
  const b = new Battle({ formatid: 'gen9championscustomgame', seed: [1, 2, 3, 4] });
  b.setPlayer('p1', { name: 'A', team: [set('Garchomp', ['Dragon Claw', 'Earthquake', 'Rock Slide', 'Protect'], { ability: 'Rough Skin' })] });
  b.setPlayer('p2', { name: 'B', team: [set('Gholdengo', ['Shadow Ball', 'Nasty Plot', 'Protect', 'Make It Rain'], { ability: 'Good as Gold' })] });
  b.choose('p1', 'team 1'); b.choose('p2', 'team 1');
  const counts = {};
  for (let i = 0; i < 20; i++) {
    const c = choose('heuristic', b, b.sides[0], rngConst());
    counts[c] = (counts[c] || 0) + 1;
  }
  const best = Object.entries(counts).sort((x, y) => y[1] - x[1])[0][0];
  assert(best.startsWith('move 2'), `expected Earthquake (move 2), got ${JSON.stringify(counts)}`);
  b.destroy();
}

/** Doubles: a foe at 5% HP shouldn't absorb both attacks when one KOs it. */
function tacticNoOverkill() {
  const b = new Battle({ formatid: 'gen9championsdoublescustomgame', seed: [1, 2, 3, 4] });
  b.setPlayer('p1', { name: 'A', team: [set('Kingambit', ['Iron Head', 'Sucker Punch', 'Kowtow Cleave', 'Low Kick'], { ability: 'Defiant' }), set('Sneasler', ['Close Combat', 'Dire Claw', 'Rock Slide', 'Gunk Shot'], { ability: 'Poison Touch' })] });
  b.setPlayer('p2', { name: 'B', team: [set('Whimsicott', ['Moonblast', 'Tailwind', 'Encore', 'Protect'], { ability: 'Infiltrator' }), set('Kingambit', ['Iron Head', 'Sucker Punch', 'Kowtow Cleave', 'Low Kick'], { ability: 'Defiant' })] });
  b.choose('p1', 'team 12'); b.choose('p2', 'team 12');
  const weak = b.sides[1].active[0];
  weak.hp = Math.max(1, Math.floor(weak.maxhp * 0.05));
  let doubled = 0;
  for (let i = 0; i < 20; i++) {
    const c = choose('heuristic', b, b.sides[0], rngConst());
    const targets = c.split(', ').map(s => (s.match(/^move \d (\d)/) || [])[1]);
    if (targets[0] === '1' && targets[1] === '1') doubled++;
  }
  assert(doubled <= 4, `doubled into a 5% HP target ${doubled}/20 times`);
  b.destroy();
}

/** Run the heuristic 20x on side A's request and tally its choices. */
function tally(b) {
  const counts = {};
  for (let i = 0; i < 20; i++) {
    const c = choose('heuristic', b, b.sides[0], rngConst());
    counts[c] = (counts[c] || 0) + 1;
  }
  return counts;
}

function singles(teamA, teamB) {
  const b = new Battle({ formatid: 'gen9championscustomgame', seed: [1, 2, 3, 4] });
  b.setPlayer('p1', { name: 'A', team: teamA });
  b.setPlayer('p2', { name: 'B', team: teamB });
  b.choose('p1', 'team ' + teamA.map((_, i) => i + 1).join(''));
  b.choose('p2', 'team ' + teamB.map((_, i) => i + 1).join(''));
  return b;
}

const spa = (species, moves, extra) => ({ ...set(species, moves, extra), evs: { hp: 2, atk: 0, def: 0, spa: 32, spd: 0, spe: 32 }, nature: extra.nature || 'Timid' });

/** Low-HP Garchomp, outsped and KO'd by Moonblast: switch, and into the Fairy resist. */
function tacticSwitchWhenDoomed() {
  const b = singles(
    [set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin' }),
      set('Salamence', ['Dragon Claw', 'Earthquake', 'Fire Fang', 'Roost'], { ability: 'Intimidate' }),
      spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' })],
    [spa('Whimsicott', ['Moonblast', 'Energy Ball', 'Tailwind', 'Encore'], { ability: 'Infiltrator' })]);
  const chomp = b.sides[0].active[0];
  chomp.hp = Math.floor(chomp.maxhp * 0.25);
  const counts = tally(b);
  const toGholdengo = counts['switch 3'] || 0, toSalamence = counts['switch 2'] || 0;
  assert(toGholdengo >= 14, `expected a switch to Gholdengo (resists Fairy), got ${JSON.stringify(counts)}`);
  assert(toSalamence <= 2, `switched into a Fairy weakness: ${JSON.stringify(counts)}`);
  b.destroy();
}

/** Gholdengo facing a faster Garchomp's Earthquake: switch into the Levitate Rotom. */
function tacticSwitchIntoImmunity() {
  const b = singles(
    [spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' }),
      spa('Rotom-Wash', ['Hydro Pump', 'Thunderbolt', 'Will-O-Wisp', 'Protect'], { ability: 'Levitate', nature: 'Modest' }),
      set('Kingambit', ['Iron Head', 'Sucker Punch', 'Kowtow Cleave', 'Low Kick'], { ability: 'Defiant' })],
    [set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin', nature: 'Jolly', item: 'Life Orb' })]);
  b.sides[0].active[0].hp = Math.floor(b.sides[0].active[0].maxhp * 0.55);
  const counts = tally(b);
  assert((counts['switch 2'] || 0) >= 14, `expected a switch into Levitate Rotom-Wash, got ${JSON.stringify(counts)}`);
  b.destroy();
}

/** Healthy Garchomp with a super-effective hit and a bench available: attack, don't switch. */
function tacticStayWhenWinning() {
  const b = singles(
    [set('Garchomp', ['Dragon Claw', 'Earthquake', 'Rock Slide', 'Protect'], { ability: 'Rough Skin', nature: 'Jolly' }),
      spa('Rotom-Wash', ['Hydro Pump', 'Thunderbolt', 'Will-O-Wisp', 'Protect'], { ability: 'Levitate', nature: 'Modest' }),
      set('Kingambit', ['Iron Head', 'Sucker Punch', 'Kowtow Cleave', 'Low Kick'], { ability: 'Defiant' })],
    [spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' })]);
  const counts = tally(b);
  const switches = Object.entries(counts).filter(([c]) => c.startsWith('switch')).reduce((x, [, n]) => x + n, 0);
  assert(switches <= 2 && (counts['move 2'] || 0) >= 14, `expected Earthquake, not a switch: ${JSON.stringify(counts)}`);
  b.destroy();
}

/** First Impression works on entry (turn 1) and must never be clicked once activeMoveActions >= 1. */
function tacticNoFirstImpressionAfterTurn1() {
  const b = singles(
    [set('Golisopod', ['First Impression', 'Liquidation', 'Leech Life', 'Aqua Jet'], { ability: 'Emergency Exit', nature: 'Adamant' })],
    [spa('Whimsicott', ['Moonblast', 'Energy Ball', 'Tailwind', 'Protect'], { ability: 'Infiltrator', item: 'Leftovers' })]);
  const t1 = tally(b);
  assert((t1['move 1'] || 0) >= 16, `expected First Impression on turn 1, got ${JSON.stringify(t1)}`);
  b.sides[0].active[0].activeMoveActions = 1;
  const t2 = tally(b);
  assert((t2['move 1'] || 0) === 0, `must never pick First Impression after turn 1, got ${JSON.stringify(t2)}`);
  b.destroy();
}

/** Good as Gold, Prankster vs Dark, and Armor Tail must block futile status/priority moves. */
function tacticImmunitiesAndBlockers() {
  // 1. Prankster Thunder Wave / Encore / Taunt into Dark-type Kingambit -> must use Moonblast (move 1)
  const b1 = singles(
    [spa('Whimsicott', ['Moonblast', 'Thunder Wave', 'Taunt', 'Encore'], { ability: 'Prankster' })],
    [set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Swords Dance'], { ability: 'Defiant' })]);
  const c1 = tally(b1);
  assert((c1['move 1'] || 0) === 20, `Prankster vs Dark: expected only Moonblast (move 1), got ${JSON.stringify(c1)}`);
  b1.destroy();

  // 2. Spore / Will-O-Wisp into Good as Gold Gholdengo -> must use Shadow Ball (move 1)
  const b2 = singles(
    [spa('Gengar', ['Shadow Ball', 'Will-O-Wisp', 'Thunder Wave', 'Taunt'], { ability: 'Cursed Body' })],
    [spa('Gholdengo', ['Shadow Ball', 'Make It Rain', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold' })]);
  const c2 = tally(b2);
  assert((c2['move 1'] || 0) === 20, `vs Good as Gold: expected only Shadow Ball (move 1), got ${JSON.stringify(c2)}`);
  b2.destroy();

  // 3. Priority move into Armor Tail Farigiraf -> must use Kowtow Cleave (move 2), never Sucker Punch (move 1)
  const b3 = singles(
    [set('Kingambit', ['Sucker Punch', 'Kowtow Cleave', 'Iron Head', 'Protect'], { ability: 'Defiant' })],
    [spa('Farigiraf', ['Psychic', 'Hyper Voice', 'Trick Room', 'Protect'], { ability: 'Armor Tail' })]);
  const c3 = tally(b3);
  assert((c3['move 1'] || 0) === 0 && (c3['move 2'] || 0) >= 18, `vs Armor Tail: expected Kowtow Cleave (move 2), got ${JSON.stringify(c3)}`);
  b3.destroy();
}

/** Turn-of-Mega evaluation projects Drought + Weather Ball and Aerilate + Hyper Voice. */
function tacticMegaTurnProjection() {
  // Charizard @ Charizardite Y facing Gholdengo knows Weather Ball becomes a 100 BP Sun Fire move (not 0-dmg Normal)
  const b = singles(
    [spa('Charizard', ['Weather Ball', 'Dragon Pulse', 'Air Slash', 'Protect'], { ability: 'Blaze', item: 'Charizardite Y', nature: 'Modest' })],
    [spa('Gholdengo', ['Shadow Ball', 'Make It Rain', 'Recover', 'Protect'], { ability: 'Good as Gold', nature: 'Modest' })]);
  const counts = tally(b);
  assert((counts['move 1 mega'] || 0) >= 18, `expected Weather Ball mega (move 1 mega), got ${JSON.stringify(counts)}`);
  b.destroy();
}

/** Doubles: Garchomp (Earthquake) + low-HP ally (Protect) discovers the Protect + Earthquake combo. */
function tacticDoublesProtectEarthquake() {
  const b = new Battle({ formatid: 'gen9championsdoublescustomgame', seed: [1, 2, 3, 4] });
  b.setPlayer('p1', { name: 'A', team: [
    set('Garchomp', ['Earthquake', 'Rock Slide', 'Dragon Claw', 'Protect'], { ability: 'Rough Skin', item: 'Life Orb', nature: 'Jolly' }),
    spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Protect'], { ability: 'Good as Gold', nature: 'Modest' }),
  ] });
  b.setPlayer('p2', { name: 'B', team: [
    set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Protect'], { ability: 'Defiant' }),
    spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Protect'], { ability: 'Good as Gold' }),
  ] });
  b.choose('p1', 'team 12'); b.choose('p2', 'team 12');
  b.sides[0].active[1].hp = 10; // ally Gholdengo at ~5% HP, threatened and would faint to friendly Earthquake without Protect
  b.sides[1].active[0].hp = 70;
  b.sides[1].active[1].hp = 70;
  const counts = tally(b);
  assert((counts['move 1, move 4'] || 0) >= 14, `expected Earthquake + Protect (move 1, move 4), got ${JSON.stringify(counts)}`);
  b.destroy();
}

/** Doubles: when fast Slot 1 KOs the only foe threatening Slot 2, Slot 2 attacks Foe 2 instead of Protecting. */
function tacticFastAllyKoSavesPartner() {
  const b = new Battle({ formatid: 'gen9championsdoublescustomgame', seed: [1, 2, 3, 4] });
  b.setPlayer('p1', { name: 'A', team: [
    set('Sneasler', ['Close Combat', 'Dire Claw', 'Rock Slide', 'Protect'], { ability: 'Poison Touch', nature: 'Jolly' }),
    spa('Gholdengo', ['Shadow Ball', 'Make It Rain', 'Nasty Plot', 'Protect'], { ability: 'Good as Gold', nature: 'Modest' }),
  ] });
  b.setPlayer('p2', { name: 'B', team: [
    set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Protect'], { ability: 'Rough Skin', nature: 'Jolly' }),
    spa('Sinistcha', ['Matcha Gotcha', 'Shadow Ball', 'Life Dew', 'Protect'], { ability: 'Hospitality', nature: 'Bold' }),
  ] });
  b.choose('p1', 'team 12'); b.choose('p2', 'team 12');
  b.sides[1].active[0].hp = 15; // Garchomp at 8% HP: outsped and OHKO'd by Sneasler before it can Earthquake Gholdengo
  const counts = tally(b);
  const gholdengoAttacks = Object.entries(counts)
    .filter(([c]) => c.split(', ')[1].startsWith('move 1') || c.split(', ')[1].startsWith('move 2'))
    .reduce((x, [, n]) => x + n, 0);
  assert(gholdengoAttacks >= 16, `expected rescued Gholdengo to attack, got ${JSON.stringify(counts)}`);
  b.destroy();
}

const SAMPLE_BELIEFS = {
  Garchomp: { spread: { hp: 32, defense: 32, speed: 2 }, nature: 'Impish', item: 'Sitrus Berry', ability: 'Rough Skin', moves: ['Protect', 'Stomping Tantrum', 'Rock Slide', 'Dragon Claw'] },
  Kingambit: { spread: { hp: 32, attack: 32, 'special-defense': 2 }, nature: 'Adamant', item: 'Black Glasses', ability: 'Defiant', moves: ['Sucker Punch', 'Kowtow Cleave', 'Protect', 'Iron Head'] },
};

function informationViews() {
  const { applyView } = require('./policies');
  const b = singles(
    [set('Kingambit', ['Iron Head', 'Sucker Punch', 'Kowtow Cleave', 'Low Kick'], { ability: 'Defiant' })],
    [set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin', item: 'Life Orb', nature: 'Jolly' })]);
  const foe = b.sides[1].active[0];
  const real = { moves: foe.moveSlots.map(m => m.id), spe: foe.storedStats.spe, item: foe.item, hp: foe.hp };
  let restore = applyView(b, b.sides[0], { mode: 'closed', beliefs: SAMPLE_BELIEFS });
  assert.deepStrictEqual(foe.moveSlots.map(m => m.id), ['protect', 'stompingtantrum', 'rockslide', 'dragonclaw'], 'closed: unrevealed moves are the usage guesses');
  assert(foe.storedStats.spe < real.spe && foe.item === 'sitrusberry', 'closed: assumed spread and item');
  restore();
  assert.deepStrictEqual(foe.moveSlots.map(m => m.id), real.moves, 'truth restored after deciding');
  assert(foe.storedStats.spe === real.spe && foe.item === real.item && foe.hp === real.hp);
  restore = applyView(b, b.sides[0], { mode: 'open_sheets', beliefs: SAMPLE_BELIEFS });
  assert.deepStrictEqual(foe.moveSlots.map(m => m.id), real.moves, 'open sheets: real moves');
  assert(foe.item === real.item && foe.storedStats.spe < real.spe, 'open sheets: real item, assumed (hidden) spread');
  restore();
  // Once an item or ability is revealed in battle.log, closed mode must keep the truth
  b.log.push('|-ability|p2a: Garchomp|Rough Skin', '|-item|p2a: Garchomp|Life Orb');
  restore = applyView(b, b.sides[0], { mode: 'closed', beliefs: SAMPLE_BELIEFS });
  assert(foe.item === 'lifeorb' && foe.ability === 'roughskin', 'closed: revealed item/ability are preserved');
  restore();
  b.destroy();
}

main().catch(e => { console.error('SELFTEST FAILED:', e.message || e); process.exit(1); });
