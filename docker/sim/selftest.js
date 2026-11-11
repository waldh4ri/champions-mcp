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
 *   6. Information modes.
 *   8. Healing: heals when it out-heals the chip, never when a faster foe
 *      KOs first or the heal can't cover the hit, Strength Sap vs physical.
 *   9. Status: Thunder Wave / Will-O-Wisp respect type and ability
 *      immunities and Guts-style abilities; burn the physical attacker,
 *      paralyze the fast one; Scald's burn chance counts; a faster Taunt or
 *      an Encore discounts our status moves.
 *  10. Hazards: Defog when our side's hazards cost the bench, not otherwise
 *      (and not when it would clear more of the foe's than ours).
 *  11. Focus Sash / Sturdy and multi-hit: a Sashed lead isn't "doomed" by an
 *      OHKO and can set up when the boost outspeeds; multi-hit moves break a
 *      foe's Sash (KO odds from the hit distribution); hazards break a
 *      switch-in's Sash.
 *   7. Setup + Baton Pass: a passer with only setup moves and Baton Pass sets
 *      up, then passes, into the recipient that uses the boosts; full battles
 *      with such a team actually hand the boosts over.
 *
 * Usage: node selftest.js [--quick]
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { Battle, toID } = require(process.env.PS_SIM || '/ps/dist/sim');
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
    tacticSackWhenSpent();
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

    // 7. setup moves and Baton Pass
    tacticPasserSetsUpFirst();
    tacticPasserPassesWhenBuilt();
    tacticPassRecipientUsesTheBoosts();
    tacticThreatenedPasserPasses();
    tacticNoPassWithoutBench();
    tacticNoPassIntoUnsafeRecipient();
    tacticFastPasserDefensiveBoosts();
    tacticPassAroundPhazing();
    tacticDoublesPassRecipient();
    ok('setup + baton pass tactics');
    // 8. healing
    tacticHealOutOfChip();
    tacticHealNeedsToMoveFirst();
    tacticHealNotEnough();
    tacticStrengthSap();
    ok('healing tactics');

    // 9. inflicting status, and the foe's Taunt / Encore
    tacticStatusImmunities();
    tacticBurnOrParalyze();
    tacticScaldBurnChance();
    tacticFoeDisruption();
    ok('status tactics');

    // 10. hazard control
    tacticDefogWhenItPays();
    endOfTurnEstimates();
    ok('hazard + end-of-turn tactics');

    // 11. Focus Sash / Sturdy, multi-hit
    tacticSashLeadStays();
    tacticSashSetup();
    tacticMultiHitBreaksSash();
    sashAndHazards();
    ok('sash + multi-hit tactics');
    for (const fmt of ['bss', 'vgc']) {
      const v = await api.handle(pool, 'validate', { format: fmt, teams: [{ id: 'bp', paste: PASS_TEAM }, { id: 'nofire', paste: NO_FIRE_TEAM }] });
      assert(v.ok && v.teams.every(t => t.valid), `Baton Pass test teams must be legal in ${fmt}: ${JSON.stringify(v.teams)}`);
      const r = await api.handle(pool, 'simulate', { format: fmt, team: PASS_TEAM, opponents, games_per_opponent: quick ? 4 : 10, seed: 11 });
      assert(r.ok && r.errors === 0, `${fmt} Baton Pass team sim errors: ${JSON.stringify(r).slice(0, 300)}`);
      ok(`baton pass battles ${fmt}`, batonPassBattles(fmt, quick ? 16 : 40));
    }
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

/**
 * Low-HP Garchomp, outsped and KO'd by Moonblast, but still the answer to the
 * Kingambit in the back: save it, and into the Fairy resist.
 */
function tacticSwitchWhenDoomed() {
  const b = singles(
    [set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin' }),
      set('Salamence', ['Dragon Claw', 'Earthquake', 'Fire Fang', 'Roost'], { ability: 'Intimidate' }),
      spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' })],
    [spa('Whimsicott', ['Moonblast', 'Energy Ball', 'Tailwind', 'Encore'], { ability: 'Infiltrator' }),
      set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' })]);
  const chomp = b.sides[0].active[0];
  chomp.hp = Math.floor(chomp.maxhp * 0.25);
  const counts = tally(b);
  const toGholdengo = counts['switch 3'] || 0, toSalamence = counts['switch 2'] || 0;
  assert(toGholdengo >= 14, `expected a switch to Gholdengo (resists Fairy), got ${JSON.stringify(counts)}`);
  assert(toSalamence <= 2, `switched into a Fairy weakness: ${JSON.stringify(counts)}`);
  b.destroy();
}

/**
 * Sacrifice: a doomed Pokémon that's spent (5% HP, or nothing left it can
 * touch or outspeed) stays in and takes the KO, so the replacement comes in
 * for free afterwards instead of eating the hit; the free switch-in after the
 * KO is the one that resists the attacker.
 */
function tacticSackWhenSpent() {
  const team = () => [set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin' }),
    set('Salamence', ['Dragon Claw', 'Earthquake', 'Fire Fang', 'Roost'], { ability: 'Intimidate' }),
    spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' })];
  const whim = () => spa('Whimsicott', ['Moonblast', 'Energy Ball', 'Tailwind', 'Encore'], { ability: 'Infiltrator' });
  const gambit = () => set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' });
  for (const [label, foes, hp] of [
    ['5% HP', [whim(), gambit()], 0.05],
    ['25% HP, nothing left it can touch first', [whim()], 0.25],
  ]) {
    const b = singles(team(), foes);
    const chomp = b.sides[0].active[0];
    chomp.hp = Math.max(1, Math.floor(chomp.maxhp * hp));
    const c = tally(b);
    const stays = Object.entries(c).filter(([k]) => k.startsWith('move')).reduce((x, [, n]) => x + n, 0);
    assert(stays >= 16, `${label}: expected Garchomp to take the KO (stay), got ${JSON.stringify(c)}`);
    // Play it out: Moonblast KOs, then the free replacement should be Gholdengo (resists Fairy).
    b.choose('p1', 'move 1'); b.choose('p2', 'move 1');
    assert(chomp.fainted && b.sides[0].activeRequest && b.sides[0].activeRequest.forceSwitch, `${label}: Garchomp should have fainted`);
    const r = tally(b);
    assert((r['switch 3'] || 0) >= 16, `${label}: free switch-in should be Gholdengo, got ${JSON.stringify(r)}`);
    b.destroy();
  }
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

// ------------------------------------------------------------------ healing

const slowbro = () => spa('Slowbro', ['Slack Off', 'Scald', 'Psychic', 'Protect'], { ability: 'Regenerator', nature: 'Bold' });
const chipLax = () => set('Snorlax', ['Body Slam', 'Rest', 'Curse', 'Protect'], { ability: 'Thick Fat', nature: 'Careful' });

/** Healer facing a board where `foe`'s best hit is `hitFrac`-ish: set HP / speed, tally. */
function healBoard(healer, foe, { hp, hpOfHit, foeSpe } = {}) {
  const { simDamage } = require('./policies');
  const b = singles([healer], [foe]);
  const me = b.sides[0].active[0], them = b.sides[1].active[0];
  const hit = Math.max(...them.moveSlots.map(ms => simDamage(b, them, me, ms.id, false)));
  me.hp = Math.max(1, Math.floor(hpOfHit !== undefined ? hit * hpOfHit : me.maxhp * hp));
  if (foeSpe !== undefined) them.storedStats.spe = foeSpe;
  const c = tally(b);
  b.destroy();
  return { c, hit, maxhp: me.maxhp };
}

/**
 * Low on HP against chip damage: Slack Off (it out-heals the foe), also on a
 * speed tie when the hit can't KO; near full HP, don't.
 */
function tacticHealOutOfChip() {
  const low = healBoard(slowbro(), chipLax(), { hp: 0.45, foeSpe: 1 });
  assert(low.hit < low.maxhp * 0.45, `test setup: Body Slam should be chip (${low.hit}/${low.maxhp})`);
  assert((low.c['move 1'] || 0) >= 16, `45% HP vs chip: expected Slack Off, got ${JSON.stringify(low.c)}`);
  const tie = healBoard(slowbro(), chipLax(), { hp: 0.45 }); // Slowbro and Snorlax speed-tie with these spreads
  assert((tie.c['move 1'] || 0) >= 16, `45% HP vs chip on a speed tie: expected Slack Off, got ${JSON.stringify(tie.c)}`);
  const high = healBoard(slowbro(), chipLax(), { hp: 0.9 });
  assert(!(high.c['move 1'] > 2), `90% HP: no need to heal, got ${JSON.stringify(high.c)}`);
}

/**
 * In KO range of one hit that's smaller than the heal: a slower foe lets the
 * heal land first (heal, and it keeps out-healing), a faster foe KOs before
 * it goes off (never heal: the turn would be wasted).
 */
function tacticHealNeedsToMoveFirst() {
  const whimsi2 = () => spa('Whimsicott', ['Moonblast', 'Tailwind', 'Encore', 'Protect'], { ability: 'Infiltrator' });
  const slow = healBoard(slowbro(), whimsi2(), { hpOfHit: 0.8, foeSpe: 1 });
  assert(slow.hit < slow.maxhp * 0.5, `test setup: Moonblast should do less than Slack Off heals (${slow.hit}/${slow.maxhp})`);
  assert((slow.c['move 1'] || 0) >= 16, `slower foe: the heal lands first, expected Slack Off, got ${JSON.stringify(slow.c)}`);
  const fast = healBoard(slowbro(), whimsi2(), { hpOfHit: 0.8, foeSpe: 999 });
  assert(!fast.c['move 1'], `faster foe KOs first: Slack Off would never go off, got ${JSON.stringify(fast.c)}`);
}

/** Healing first still loses to a hit bigger than HP + heal: don't bother. */
function tacticHealNotEnough() {
  const g = healBoard(slowbro(), spa('Gholdengo', ['Shadow Ball', 'Make It Rain', 'Nasty Plot', 'Protect'], { ability: 'Good as Gold', nature: 'Modest' }), { hp: 0.4, foeSpe: 1 });
  assert(g.hit > g.maxhp * 0.9, `test setup: Shadow Ball should nearly OHKO Slowbro (${g.hit}/${g.maxhp})`);
  assert(!g.c['move 1'], `heal can't cover a ${Math.round(100 * g.hit / g.maxhp)}% hit from 40%: got ${JSON.stringify(g.c)}`);
}

/** Strength Sap into a slower physical attacker: heals by its Attack and weakens the hit. */
function tacticStrengthSap() {
  const sinistcha = () => spa('Sinistcha', ['Matcha Gotcha', 'Shadow Ball', 'Strength Sap', 'Protect'], { ability: 'Hospitality', nature: 'Bold' });
  const gambit = () => set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant', nature: 'Adamant' });
  const slow = healBoard(sinistcha(), gambit(), { hp: 0.5, foeSpe: 1 });
  assert((slow.c['move 3'] || 0) >= 16, `expected Strength Sap vs slower physical Kingambit, got ${JSON.stringify(slow.c)}`);
  const fast = healBoard(sinistcha(), gambit(), { hpOfHit: 0.8, foeSpe: 999 });
  assert(!fast.c['move 3'], `faster Kingambit KOs first: no Strength Sap, got ${JSON.stringify(fast.c)}`);
}

// ------------------------------------------------------------------- status

// Rotom with only status options, so its choice is about the status itself.
const statusRotom = () => spa('Rotom-Wash', ['Thunder Wave', 'Will-O-Wisp', 'Protect', 'Rest'], { ability: 'Levitate', nature: 'Bold' });
const statusPick = foe => {
  const b = singles([statusRotom()], [foe]);
  const c = tally(b);
  b.destroy();
  return c;
};

/** Thunder Wave never into a Ground-type or Volt Absorb; no status at all into Guts. */
function tacticStatusImmunities() {
  const chomp = statusPick(set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin' }));
  assert(!chomp['move 1'] && (chomp['move 2'] || 0) >= 16, `vs Garchomp: Will-O-Wisp, never Thunder Wave (Ground), got ${JSON.stringify(chomp)}`);
  const absorb = statusPick(set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Volt Absorb' }));
  assert(!absorb['move 1'], `Thunder Wave into Volt Absorb: ${JSON.stringify(absorb)}`);
  const guts = statusPick(set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Facade'], { ability: 'Guts' }));
  assert(!guts['move 1'] && !guts['move 2'], `statusing a Guts Pokemon: ${JSON.stringify(guts)}`);
}

/** Burn the slower physical attacker; paralyze the faster special one. */
function tacticBurnOrParalyze() {
  const phys = statusPick(set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' }));
  assert((phys['move 2'] || 0) >= 16, `vs physical Kingambit: expected Will-O-Wisp, got ${JSON.stringify(phys)}`);
  const fast = statusPick(spa('Gengar', ['Shadow Ball', 'Sludge Bomb', 'Focus Blast', 'Protect'], { ability: 'Cursed Body' }));
  assert((fast['move 1'] || 0) >= 16, `vs fast special Gengar: expected Thunder Wave, got ${JSON.stringify(fast)}`);
}

/** Scald over Hydro Pump against a physical attacker (the burn chance); not against Guts. */
function tacticScaldBurnChance() {
  const rotom = () => spa('Rotom-Wash', ['Hydro Pump', 'Scald', 'Protect', 'Rest'], { ability: 'Levitate', nature: 'Bold' });
  const b1 = singles([rotom()], [set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' })]);
  const phys = tally(b1); b1.destroy();
  assert((phys['move 2'] || 0) >= 14, `vs physical Kingambit: expected Scald, got ${JSON.stringify(phys)}`);
  const b2 = singles([rotom()], [set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Facade'], { ability: 'Guts' })]);
  const guts = tally(b2); b2.destroy();
  assert((guts['move 1'] || 0) >= 14, `vs Guts: expected Hydro Pump (no burn wanted), got ${JSON.stringify(guts)}`);
}

/** A faster Taunt halves a status move's value, Encore trims it; Mental Herb / slower Taunt / Prankster-vs-Dark don't. */
function tacticFoeDisruption() {
  const { _test: { foeDisruption } } = require('./policies');
  const factor = (foeMoves, { foeSpe, item, me } = {}) => {
    const b = singles([me || set('Scizor', ['Swords Dance', 'Bullet Punch', 'Iron Head', 'Protect'], { ability: 'Technician', item })],
      [set('Whimsicott', foeMoves, { ability: 'Prankster' })]);
    if (foeSpe !== undefined) b.sides[1].active[0].storedStats.spe = foeSpe;
    const mon = b.sides[0].active[0];
    const f = foeDisruption(b, b.sides[0], mon, b.dex.moves.get(mon.moveSlots[0].id));
    b.destroy();
    return f;
  };
  assert.strictEqual(factor(['Taunt', 'Moonblast', 'Protect', 'Tailwind']), 0.5, 'Prankster Taunt beats Swords Dance');
  assert.strictEqual(factor(['Taunt', 'Moonblast', 'Protect', 'Tailwind'], { item: 'Mental Herb' }), 1, 'Mental Herb');
  assert.strictEqual(factor(['Encore', 'Moonblast', 'Protect', 'Tailwind']), 0.75, 'Encore lock risk');
  const kingambit = set('Kingambit', ['Swords Dance', 'Iron Head', 'Kowtow Cleave', 'Protect'], { ability: 'Defiant' });
  assert.strictEqual(factor(['Taunt', 'Encore', 'Protect', 'Tailwind'], { me: kingambit }), 1, 'Prankster fails into a Dark-type');
  const b = singles([set('Scizor', ['Swords Dance', 'Bullet Punch', 'Iron Head', 'Protect'], { ability: 'Technician' })],
    [set('Snorlax', ['Taunt', 'Body Slam', 'Rest', 'Protect'], { ability: 'Thick Fat' })]);
  b.sides[1].active[0].storedStats.spe = 1;
  assert.strictEqual(foeDisruption(b, b.sides[0], b.sides[0].active[0], b.dex.moves.get('swordsdance')), 1, 'a slower Taunt comes too late this turn');
  b.destroy();
}

// ------------------------------------------------------------------ hazards

/**
 * Corviknight facing a passive Snorlax with a Stealth Rock-weak bench
 * (Charizard 4x, Salamence 2x): Defog when Rocks are up on our side; never
 * with no hazards, nor when the foe has more hazards up than we do.
 */
function tacticDefogWhenItPays() {
  const run = (ours, theirs) => {
    const b = singles([set('Corviknight', ['Defog', 'Brave Bird', 'Roost', 'Body Press'], { ability: 'Pressure', nature: 'Impish' }),
      spa('Charizard', ['Flamethrower', 'Air Slash', 'Dragon Pulse', 'Protect'], { ability: 'Blaze' }),
      set('Salamence', ['Dragon Claw', 'Earthquake', 'Fire Fang', 'Roost'], { ability: 'Intimidate' })],
    [set('Snorlax', ['Rest', 'Protect', 'Curse', 'Sleep Talk'], { ability: 'Thick Fat', nature: 'Careful' }),
      set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' }),
      set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Protect'], { ability: 'Rough Skin' })]);
    for (const c of ours) b.sides[0].addSideCondition(c, b.sides[1].active[0]);
    for (const c of theirs) b.sides[1].addSideCondition(c, b.sides[0].active[0]);
    const c = tally(b);
    b.destroy();
    return c;
  };
  const rocks = run(['stealthrock'], []);
  assert((rocks['move 1'] || 0) >= 14, `Rocks on our side, SR-weak bench: expected Defog, got ${JSON.stringify(rocks)}`);
  const none = run([], []);
  assert(!none['move 1'], `no hazards: Defog is pointless, got ${JSON.stringify(none)}`);
  const theirs = run(['stealthrock'], ['stealthrock', 'spikes']);
  assert(!(theirs['move 1'] > 2), `the foe has more hazards up: Defog would clear theirs too, got ${JSON.stringify(theirs)}`);
}

/** End-of-turn HP estimates: Toxic grows, Leftovers offsets, Magic Guard and sand immunity. */
function endOfTurnEstimates() {
  const { _test: { residualDelta } } = require('./policies');
  const b = singles([spa('Slowbro', ['Slack Off', 'Scald', 'Psychic', 'Protect'], { ability: 'Regenerator', item: 'Leftovers' }),
    spa('Clefable', ['Moonblast', 'Moonlight', 'Calm Mind', 'Protect'], { ability: 'Magic Guard' }),
    set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Protect'], { ability: 'Rough Skin' })],
  [set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' })]);
  const [bro, clef, chomp] = b.sides[0].pokemon;
  const near = (x, want, label) => assert(Math.abs(x - want) <= 1, `${label}: ${x} vs ${want}`);
  bro.setStatus('tox'); bro.statusState.stage = 3; // next tick is the 4th: 4/16
  near(residualDelta(b, bro), -bro.maxhp * 4 / 16 + bro.maxhp / 16, 'Toxic 4/16 with Leftovers');
  clef.status = 'brn';
  assert.strictEqual(residualDelta(b, clef), 0, 'Magic Guard takes no burn damage');
  b.field.setWeather('sandstorm', b.sides[1].active[0]);
  near(residualDelta(b, chomp), 0, 'Ground-type ignores sandstorm');
  near(residualDelta(b, clef), 0, 'Magic Guard ignores sandstorm');
  b.destroy();
}

// --------------------------------------------------- Focus Sash / multi-hit

// Whimsicott hitting hard enough that Moonblast OHKOs Garchomp (unless Sashed).
const hardWhim = (moves = ['Moonblast', 'Energy Ball', 'Tailwind', 'Encore']) => spa('Whimsicott', moves, { ability: 'Infiltrator' });
const boostSpa = b => { const f = b.sides[1].active[0]; f.storedStats.spa = Math.floor(f.storedStats.spa * 1.6); };

/** Full-HP Garchomp facing an OHKO: with Focus Sash it stays and attacks; without, it's saved by a switch. */
function tacticSashLeadStays() {
  const run = item => {
    const b = singles([set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Swords Dance'], { ability: 'Rough Skin', item }),
      spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' })],
    [hardWhim(), set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' })]);
    boostSpa(b);
    const { simDamage } = require('./policies');
    const [me, foe] = [b.sides[0].active[0], b.sides[1].active[0]];
    assert(simDamage(b, foe, me, 'moonblast', false) > me.hp, 'test setup: Moonblast should OHKO Garchomp');
    const c = tally(b);
    b.destroy();
    return c;
  };
  const sash = run('Focus Sash');
  const moves = Object.entries(sash).filter(([k]) => k.startsWith('move')).reduce((x, [, n]) => x + n, 0);
  assert(moves >= 16, `Focus Sash holds: expected Garchomp to stay and attack, got ${JSON.stringify(sash)}`);
  const orb = run('Life Orb');
  assert((orb['switch 2'] || 0) >= 14, `no Sash: Garchomp is doomed and worth saving, expected a switch, got ${JSON.stringify(orb)}`);
}

/**
 * Sashed Dragon Dance lead: the foe would OHKO but is slower than Garchomp at
 * +1, and +1 Iron Head turns a 2HKO into a KO: set up (the Sash buys the turn).
 */
function tacticSashSetup() {
  const run = item => {
    const b = singles([set('Garchomp', ['Dragon Dance', 'Iron Head', 'Earthquake', 'Protect'], { ability: 'Rough Skin', item, nature: 'Jolly' })],
      [hardWhim(['Moonblast', 'Energy Ball', 'Protect', 'Encore']),
        set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' })]);
    boostSpa(b);
    const [me, foe] = [b.sides[0].active[0], b.sides[1].active[0]];
    foe.storedStats.spe = Math.floor(me.storedStats.spe * 1.2); // faster now, slower than +1
    const { simDamage } = require('./policies');
    const ih = simDamage(b, me, foe, 'ironhead', false);
    assert(ih < foe.hp && ih * 1.5 >= foe.hp, `test setup: Iron Head should KO only at +1 (${ih} vs ${foe.hp})`);
    const c = tally(b);
    b.destroy();
    return c;
  };
  const sash = run('Focus Sash');
  assert((sash['move 1'] || 0) >= 14, `Sashed Dragon Dance lead: expected Dragon Dance, got ${JSON.stringify(sash)}`);
  const orb = run('Leftovers');
  assert(!(orb['move 1'] > 2), `no Sash: it faints before the boost matters, got ${JSON.stringify(orb)}`);
}

/** A foe's Focus Sash: Scale Shot (multi-hit) over a Dragon Claw it would survive at 1 HP; Dragon Claw without the Sash. */
function tacticMultiHitBreaksSash() {
  const run = item => {
    const b = singles([set('Garchomp', ['Scale Shot', 'Dragon Claw', 'Earthquake', 'Protect'], { ability: 'Rough Skin', nature: 'Jolly' })],
      [set('Salamence', ['Dragon Claw', 'Earthquake', 'Fire Fang', 'Roost'], { ability: 'Moxie', item })]);
    const c = tally(b);
    b.destroy();
    return c;
  };
  const sash = run('Focus Sash');
  assert((sash['move 1'] || 0) >= 16, `vs Focus Sash: expected Scale Shot, got ${JSON.stringify(sash)}`);
  const plain = run('Leftovers');
  assert((plain['move 2'] || 0) >= 16, `no Sash: expected the sure Dragon Claw KO, got ${JSON.stringify(plain)}`);
}

/** Sash holds vs a single hit, not vs multi-hit; Stealth Rock on entry breaks a switch-in's Sash. */
function sashAndHazards() {
  const { _test: { sashHolds, switchEval, assess } } = require('./policies');
  const b = singles([spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Nasty Plot', 'Recover'], { ability: 'Good as Gold', nature: 'Modest' }),
    set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Protect'], { ability: 'Rough Skin', item: 'Focus Sash' })],
  [hardWhim(), set('Kingambit', ['Iron Head', 'Kowtow Cleave', 'Sucker Punch', 'Low Kick'], { ability: 'Defiant' })]);
  boostSpa(b);
  const [whim, chomp] = [b.sides[1].active[0], b.sides[0].pokemon[1]];
  assert(sashHolds(b, whim, chomp, 'moonblast') && !sashHolds(b, whim, chomp, 'scaleshot'), 'Sash vs single hit / multi-hit');
  b.sides[0].active[0].hp = 1; // make Gholdengo the slot the foe aims at
  const a = assess(b, b.sides[0]);
  const intact = switchEval(b, b.sides[0], 0, chomp, a, false);
  b.sides[0].addSideCondition('stealthrock', whim);
  const rocked = switchEval(b, b.sides[0], 0, chomp, a, false);
  assert(intact.followUp < 1 && rocked.followUp >= 1, `Rocks break the switch-in's Sash: ${JSON.stringify({ intact, rocked })}`);
  b.destroy();
}

// ------------------------------------------------- setup + Baton Pass

// Scizor / Hawlucha / Espathra carry nothing but setup moves and Baton Pass.
const PASS_TEAM = `Scizor @ Leftovers
Ability: Technician
Level: 50
EVs: 32 HP / 2 Atk / 32 Def
Impish Nature
- Swords Dance
- Agility
- Iron Defense
- Baton Pass

Hawlucha @ Sitrus Berry
Ability: Unburden
Level: 50
EVs: 32 HP / 2 Atk / 32 Spe
Jolly Nature
- Swords Dance
- Bulk Up
- Agility
- Baton Pass

Espathra @ Focus Sash
Ability: Speed Boost
Level: 50
EVs: 32 HP / 2 Def / 32 SpD
Calm Nature
- Calm Mind
- Protect
- Baton Pass
- Agility

Garchomp @ Life Orb
Ability: Rough Skin
Level: 50
EVs: 2 HP / 32 Atk / 32 Spe
Jolly Nature
- Earthquake
- Dragon Claw
- Rock Slide
- Protect

Gholdengo @ Metal Coat
Ability: Good as Gold
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Modest Nature
- Make It Rain
- Shadow Ball
- Focus Blast
- Protect

Kingambit @ Black Glasses
Ability: Defiant
Level: 50
EVs: 32 HP / 32 Atk / 2 Spe
Adamant Nature
- Kowtow Cleave
- Iron Head
- Sucker Punch
- Protect
`;

/** One set out of the fixture teams, by its "Species @ Item" header line. */
const fixtureSet = header => {
  for (const t of TEAMS) for (const b of t.split(/\r?\n\r?\n/)) if (b.trim().startsWith(header)) return b.trim();
  throw new Error(`no fixture set ${header}`);
};
// Every fixture team carries a Fire Mega or Mega Staraptor that a Bug/Steel
// passer must run from; this one (fixture sets, no Fire) is a field passers
// can actually set up on.
const NO_FIRE_TEAM = ['Milotic @ Sitrus Berry', 'Sinistcha @ Occa Berry', 'Grimmsnarl @ Light Clay',
  'Basculegion @ Mystic Water', 'Annihilape @ Leftovers', 'Kingambit @ Chople Berry'].map(fixtureSet).join('\n\n');

const SETUP_IDS = new Set(['swordsdance', 'agility', 'irondefense', 'bulkup', 'calmmind', 'nastyplot', 'dragondance']);
const scizorBP = () => set('Scizor', ['Swords Dance', 'Agility', 'Iron Defense', 'Baton Pass'], { ability: 'Technician', item: 'Leftovers', nature: 'Impish' });
const espathraBP = () => spa('Espathra', ['Calm Mind', 'Protect', 'Baton Pass', 'Agility'], { ability: 'Opportunist', nature: 'Calm' });
const chompRecv = () => set('Garchomp', ['Earthquake', 'Dragon Claw', 'Rock Slide', 'Protect'], { ability: 'Rough Skin', nature: 'Jolly' });
const gholdRecv = () => spa('Gholdengo', ['Make It Rain', 'Shadow Ball', 'Focus Blast', 'Protect'], { ability: 'Good as Gold', nature: 'Modest' });
// A foe that can't really hurt Scizor (both attacks resisted): free turns to set up.
const whimsi = () => spa('Whimsicott', ['Moonblast', 'Energy Ball', 'Tailwind', 'Protect'], { ability: 'Infiltrator', item: 'Leftovers' });
const sum = (counts, pred) => Object.entries(counts).filter(([c]) => pred(c)).reduce((x, [, n]) => x + n, 0);

/** Unboosted passer facing a non-threat: set up (Swords Dance / Agility), don't pass yet. */
function tacticPasserSetsUpFirst() {
  const b = singles([scizorBP(), chompRecv(), gholdRecv()], [whimsi()]);
  const c = tally(b);
  const setup = (c['move 1'] || 0) + (c['move 2'] || 0);
  assert(setup >= 16 && !c['move 4'], `expected Swords Dance / Agility at +0 (never an empty Baton Pass), got ${JSON.stringify(c)}`);
  b.destroy();
}

/** Once the passer is at +2 Atk / +2 Spe, hand the boosts over. */
function tacticPasserPassesWhenBuilt() {
  const b = singles([scizorBP(), chompRecv(), gholdRecv()], [whimsi()]);
  Object.assign(b.sides[0].active[0].boosts, { atk: 2, spe: 2 });
  const c = tally(b);
  assert((c['move 4'] || 0) >= 16, `expected Baton Pass at +2/+2, got ${JSON.stringify(c)}`);
  b.destroy();
}

/**
 * The forced switch after Baton Pass goes to whoever uses the boosts, and they
 * arrive. Same passive foe both times, so only the kind of boost decides.
 */
function tacticPassRecipientUsesTheBoosts() {
  const wall = () => set('Snorlax', ['Rest', 'Protect', 'Sleep Talk', 'Amnesia'], { ability: 'Thick Fat', nature: 'Careful' });
  for (const [label, passer, boosts, want, stat] of [
    ['physical boosts -> Garchomp', scizorBP(), { atk: 2 }, 'switch 2', 'atk'],
    ['special boosts -> Gholdengo', espathraBP(), { spa: 2, spd: 2 }, 'switch 3', 'spa'],
  ]) {
    const b = singles([passer, chompRecv(), gholdRecv()], [wall()]);
    Object.assign(b.sides[0].active[0].boosts, boosts);
    const bp = b.sides[0].active[0].moveSlots.findIndex(m => m.id === 'batonpass') + 1;
    b.choose('p1', `move ${bp}`); b.choose('p2', 'move 4'); // Snorlax: Amnesia
    assert(b.sides[0].activeRequest && b.sides[0].activeRequest.forceSwitch, `${label}: Baton Pass should ask for a recipient`);
    const c = tally(b);
    assert((c[want] || 0) >= 18, `${label}: expected ${want}, got ${JSON.stringify(c)}`);
    b.choose('p1', want);
    const recv = b.sides[0].active[0];
    assert.strictEqual(recv.boosts[stat], boosts[stat], `${label}: ${recv.name} should arrive with +${boosts[stat]} ${stat}`);
    b.destroy();
  }
}

/** Boosted but about to be KO'd next turn (outspeeds, 4x Fire weak): pass now, don't keep boosting or hard switch. */
function tacticThreatenedPasserPasses() {
  const b = singles([scizorBP(), chompRecv(), gholdRecv()],
    [spa('Charizard', ['Flamethrower', 'Air Slash', 'Dragon Pulse', 'Protect'], { ability: 'Blaze', nature: 'Modest' })]);
  Object.assign(b.sides[0].active[0].boosts, { atk: 2, spe: 2 });
  const c = tally(b);
  assert((c['move 4'] || 0) >= 16, `expected a Baton Pass out of the Fire threat, got ${JSON.stringify(c)}`);
  b.destroy();
}

/** Boosted, but the only recipient would be KO'd coming in (5% HP into Moonblast): don't pass. */
function tacticNoPassIntoUnsafeRecipient() {
  const b = singles([scizorBP(), chompRecv()], [whimsi()]);
  Object.assign(b.sides[0].active[0].boosts, { atk: 2, spe: 2 });
  b.sides[0].pokemon[1].hp = Math.max(1, Math.floor(b.sides[0].pokemon[1].maxhp * 0.05));
  const c = tally(b);
  assert(!c['move 4'], `Baton Pass into a recipient that dies on entry: ${JSON.stringify(c)}`);
  b.destroy();
}

/**
 * A fast passer hands over Defense too: the recipient takes the foe's hit
 * already at +2 Def. An injured Garchomp that a Double-Edge would KO
 * unboosted survives it at +2 Def, so passing is safe; the control without
 * the Defense boosts must not pass.
 */
function tacticFastPasserDefensiveBoosts() {
  const { simDamage } = require('./policies');
  const run = boosts => {
    const b = singles([scizorBP(), chompRecv()],
      [set('Snorlax', ['Double-Edge', 'Rest', 'Curse', 'Protect'], { ability: 'Thick Fat', nature: 'Adamant' })]);
    const scizor = b.sides[0].active[0], chomp = b.sides[0].pokemon[1], lax = b.sides[1].active[0];
    const hit = simDamage(b, lax, chomp, 'doubleedge', false);
    chomp.hp = Math.floor(0.75 * hit); // KO'd by the hit unboosted, survives it at +2 Def (half damage)
    assert(chomp.hp > 0 && chomp.hp < chomp.maxhp, `test setup: Double-Edge does ${hit} to Garchomp (${chomp.maxhp} HP)`);
    Object.assign(scizor.boosts, boosts);
    const c = tally(b);
    b.destroy();
    return c;
  };
  const tanky = run({ atk: 4, def: 2, spe: 2 });
  assert((tanky['move 4'] || 0) >= 16, `+2 Def makes the recipient safe: expected Baton Pass, got ${JSON.stringify(tanky)}`);
  const frail = run({ atk: 4, spe: 2 });
  assert(!frail['move 4'], `without the Def boosts the recipient dies coming in: got ${JSON.stringify(frail)}`);
}

/**
 * The foe carries a phazing move: pass into whoever it can't force out.
 *   Roar        -> Magic Bounce Hatterene over Gengar (otherwise the better
 *                  switch: immune to Body Slam), checked against a control
 *                  without Roar where Gengar is picked.
 *   Dragon Tail -> Fairy-type Azumarill over Garchomp.
 */
function tacticPassAroundPhazing() {
  const gengar = () => spa('Gengar', ['Shadow Ball', 'Sludge Bomb', 'Focus Blast', 'Protect'], { ability: 'Cursed Body', nature: 'Modest' });
  const hatterene = () => spa('Hatterene', ['Psychic', 'Dazzling Gleam', 'Mystical Fire', 'Protect'], { ability: 'Magic Bounce', nature: 'Modest' });
  const azu = () => set('Azumarill', ['Play Rough', 'Liquidation', 'Aqua Jet', 'Protect'], { ability: 'Huge Power', nature: 'Adamant' });
  const lax = moves => set('Snorlax', moves, { ability: 'Thick Fat', nature: 'Careful' });
  const pickAfterPass = (passer, boosts, bench, foe) => {
    const b = singles([passer, ...bench], [foe]);
    Object.assign(b.sides[0].active[0].boosts, boosts);
    const bp = b.sides[0].active[0].moveSlots.findIndex(m => m.id === 'batonpass') + 1;
    const before = tally(b);
    b.choose('p1', `move ${bp}`); b.choose('p2', 'move 4');
    const after = tally(b);
    b.destroy();
    return { before, after };
  };
  const special = { spa: 2, spd: 2, spe: 2 };
  const roar = pickAfterPass(espathraBP(), special, [gengar(), hatterene()], lax(['Body Slam', 'Rest', 'Roar', 'Protect']));
  assert((roar.before['move 3'] || 0) >= 16, `vs Roar: expected Baton Pass (Hatterene is safe), got ${JSON.stringify(roar.before)}`);
  assert((roar.after['switch 3'] || 0) >= 18, `vs Roar: expected Magic Bounce Hatterene (switch 3), got ${JSON.stringify(roar.after)}`);
  const control = pickAfterPass(espathraBP(), special, [gengar(), hatterene()], lax(['Body Slam', 'Rest', 'Curse', 'Protect']));
  assert((control.after['switch 2'] || 0) >= 18, `control (no Roar): expected Gengar (switch 2), got ${JSON.stringify(control.after)}`);
  const dtail = pickAfterPass(scizorBP(), { atk: 2, spe: 2 }, [chompRecv(), azu()], lax(['Body Slam', 'Rest', 'Dragon Tail', 'Protect']));
  assert((dtail.after['switch 3'] || 0) >= 18, `vs Dragon Tail: expected Fairy-type Azumarill (switch 3), got ${JSON.stringify(dtail.after)}`);
}

/** Last Pokémon standing: Baton Pass would fail, never pick it. */
function tacticNoPassWithoutBench() {
  const b = singles([scizorBP()], [whimsi()]);
  Object.assign(b.sides[0].active[0].boosts, { atk: 2, spe: 2 });
  const c = tally(b);
  assert(!c['move 4'], `Baton Pass with no bench: ${JSON.stringify(c)}`);
  b.destroy();
}

/**
 * Doubles: against a weak field Scizor builds to +4 (where Garchomp starts
 * OHKOing) and then passes; Garchomp, not Gholdengo, takes the physical boosts.
 */
function tacticDoublesPassRecipient() {
  const b = new Battle({ formatid: 'gen9championsdoublescustomgame', seed: [1, 2, 3, 4] });
  b.setPlayer('p1', { name: 'A', team: [scizorBP(), set('Kingambit', ['Kowtow Cleave', 'Iron Head', 'Sucker Punch', 'Protect'], { ability: 'Defiant' }), gholdRecv(), chompRecv()] });
  b.setPlayer('p2', { name: 'B', team: [whimsi(), spa('Sinistcha', ['Matcha Gotcha', 'Shadow Ball', 'Life Dew', 'Protect'], { ability: 'Hospitality', nature: 'Bold' })] });
  b.choose('p1', 'team 1234'); b.choose('p2', 'team 12');
  Object.assign(b.sides[0].active[0].boosts, { atk: 4, spe: 2 });
  const c = tally(b);
  assert(sum(c, k => k.startsWith('move 4')) >= 14, `doubles: expected Scizor to Baton Pass at +4/+2, got ${JSON.stringify(c)}`);
  b.choose('p1', 'move 4, move 4'); b.choose('p2', 'move 3, move 4');
  assert(b.sides[0].activeRequest && b.sides[0].activeRequest.forceSwitch, 'doubles: Baton Pass should ask for a recipient');
  const r = tally(b);
  assert(sum(r, k => k.startsWith('switch 4')) >= 18, `doubles: expected Garchomp (switch 4) to receive, got ${JSON.stringify(r)}`);
  b.destroy();
}

/**
 * Full heuristic-vs-heuristic battles with PASS_TEAM against the fixture
 * teams and NO_FIRE_TEAM: passers get brought, set up and pass (never empty-handed). Every hand-over is checked
 * against the battle state at that moment: the recipient must receive
 * exactly the passer's boosts.
 */
function batonPassBattles(fmt, games) {
  const formatid = engine.formatId(fmt);
  const mine = engine.parseTeam(PASS_TEAM);
  const passers = new Set(['Scizor', 'Hawlucha', 'Espathra']);
  const pool = [...TEAMS.slice(1), NO_FIRE_TEAM];
  const st = { games, broughtPasser: 0, setups: 0, passes: 0, boostedPasses: 0, landed: 0, wrong: 0, fallbacks: 0, wins: 0 };
  for (let g = 0; g < games; g++) {
    const theirs = engine.parseTeam(pool[g % pool.length]);
    const rng = engine.mulberry32(1000 + g);
    const b = new Battle({ formatid, seed: [g + 1, 2 * g + 3, 5, 7] });
    b.setPlayer('p1', { name: 'A', team: mine.map(s => ({ ...s })) });
    b.setPlayer('p2', { name: 'B', team: theirs.map(s => ({ ...s })) });
    for (const p of b.sides[0].pokemon) {
      const copy = p.copyVolatileFrom;
      p.copyVolatileFrom = function (src, cause) {
        const handed = { ...src.boosts };
        copy.call(this, src, cause);
        if (Object.values(handed).some(v => v > 0)) {
          st.boostedPasses++;
          if (Object.keys(handed).every(k => this.boosts[k] === handed[k])) st.landed++;
          else st.wrong++;
        }
      };
    }
    let pos = 0;
    for (let guard = 0; !b.ended && guard < 1200; guard++) {
      for (const side of b.sides) {
        const req = side.activeRequest;
        if (!req || req.wait || side.isChoiceDone()) continue;
        const c = choose('heuristic', b, side, rng);
        if (c === null || !b.choose(side.id, c)) { st.fallbacks++; side.clearChoice(); b.choose(side.id, 'default'); }
      }
      for (; pos < b.log.length; pos++) {
        const p = b.log[pos].split('|');
        if (p[1] !== 'move' || !p[2].startsWith('p1')) continue;
        const id = toID(p[3]);
        if (passers.has(p[2].slice(p[2].indexOf(': ') + 2)) && SETUP_IDS.has(id)) st.setups++;
        if (id === 'batonpass' && !p.includes('[still]')) st.passes++;
      }
    }
    if (b.sides[0].pokemon.slice(0, b.ruleTable.pickedTeamSize).some(p => passers.has(p.set.species))) st.broughtPasser++;
    if (b.winner === 'A') st.wins++;
    b.destroy();
  }
  assert.strictEqual(st.fallbacks, 0, `${fmt}: rejected choices with the Baton Pass team: ${JSON.stringify(st)}`);
  assert(st.broughtPasser >= 0.5 * games, `${fmt}: passers left at home: ${JSON.stringify(st)}`);
  // BSS (singles) is the target for the passing game itself; in VGC only the
  // correctness checks below apply (passers often end up last on the field).
  if (fmt === 'bss') {
    assert(st.boostedPasses >= Math.max(3, games / 10) && st.setups >= st.boostedPasses, `${fmt}: too few setups/passes: ${JSON.stringify(st)}`);
  }
  assert.strictEqual(st.passes, st.boostedPasses, `${fmt}: Baton Pass used with no boosts: ${JSON.stringify(st)}`);
  assert(st.wrong === 0 && st.landed === st.boostedPasses, `${fmt}: boosts didn't reach the recipient: ${JSON.stringify(st)}`);
  return st;
}

main().catch(e => { console.error('SELFTEST FAILED:', e.message || e); process.exit(1); });
