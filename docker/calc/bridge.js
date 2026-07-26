'use strict';
// Reads one JSON request on stdin, runs @smogon/calc, writes one JSON line.
// Request shape:
// {
//   "gen": 9,
//   "attacker": { "name": "Garchomp", "options": { ...State.Pokemon } },
//   "defender": { "name": "Incineroar", "options": { ...State.Pokemon } },
//   "move": { "name": "Earthquake", "options": { ...State.Move } },
//   "field": { "gameType": "Doubles", "weather": "Sand", ... }
// }
// @smogon/calc's non-throwing error path (err=false) writes diagnostics via
// console.log -> stdout, which would corrupt our single-line JSON contract.
// Route every console channel to stderr so stdout is ONLY our result.
for (const k of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  console[k] = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
}

const calc = require('/app/calc/dist/index.js');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  let out;
  try {
    const r = JSON.parse(input || '{}');
    // gen 0 == @smogon/calc's native Pokémon Champions mode (Champions data
    // sets + calcStatChampions, which takes Stat Points directly). Use a
    // nullish check so an explicit 0 is honored (0 is falsy).
    const genNum = (r.gen === undefined || r.gen === null) ? 0 : r.gen;
    const gen = calc.Generations.get(genNum);
    // calc.calculate() does NOT auto-transform a held Mega Stone (the
    // Showdown UI pre-resolves the forme). Resolve it here so Champions
    // Megas use their Mega base stats / ability / typing.
    const formeName = (name, opts) => {
      try {
        const mv = opts && opts.moves && opts.moves[0];
        return calc.Pokemon.getForme(gen, name, opts && opts.item, mv) || name;
      } catch (e) { return name; }
    };
    const attacker = new calc.Pokemon(
      gen, formeName(r.attacker.name, r.attacker.options), r.attacker.options || {});
    const defender = new calc.Pokemon(
      gen, formeName(r.defender.name, r.defender.options), r.defender.options || {});
    const move = new calc.Move(gen, r.move.name, r.move.options || {});
    const field = new calc.Field(r.field || { gameType: 'Singles' });
    const result = calc.calculate(gen, attacker, defender, move, field);

    const range = result.range();
    const maxHP = defender.maxHP();
    out = {
      ok: true,
      attacker: attacker.name,
      defender: defender.name,
      move: move.name,
      gen: gen.num,
      attacker_stats: attacker.stats,
      defender_stats: defender.stats,
      defender_max_hp: maxHP,
      min_damage: range[0],
      max_damage: range[1],
      min_pct: maxHP ? +((range[0] / maxHP) * 100).toFixed(1) : null,
      max_pct: maxHP ? +((range[1] / maxHP) * 100).toFixed(1) : null,
      damage_rolls: result.damage,
    };
    // err=false so immunities / 0-damage still return a description
    // instead of throwing.
    try { out.description = result.fullDesc('%', false); }
    catch (e) { out.description = ''; }
    try {
      const ko = result.kochance(false);
      out.ko_chance = ko && ko.text ? ko.text : '';
      if (ko && typeof ko.chance === 'number') out.ko_chance_pct = +(ko.chance * 100).toFixed(1);
      if (ko && typeof ko.n === 'number') out.ko_hits = ko.n;
    } catch (e) { out.ko_chance = ''; }
    out.immune = range[0] === 0 && range[1] === 0;
  } catch (e) {
    out = { ok: false, error: String((e && e.message) || e) };
  }
  process.stdout.write(JSON.stringify(out));
});
