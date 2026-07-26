'use strict';
/**
 * champions-calc HTTP sidecar
 *
 * Wraps @smogon/calc (gen 0 = native Pokémon Champions mode) as a persistent
 * HTTP service. Accepts POST / with the same JSON payload as bridge.js and
 * returns the same JSON response.
 *
 * Listens on PORT (default 3000), bound to 0.0.0.0.
 * Used by the MCP server when CHAMPIONS_MCP_CALC_HTTP is set.
 */

// Route every console channel to stderr so stdout stays clean.
for (const k of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  console[k] = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
}

const http = require('http');
const calc = require('/app/calc/dist/index.js');

const PORT = parseInt(process.env.PORT || '3000', 10);

function runCalc(r) {
  const genNum = (r.gen === undefined || r.gen === null) ? 0 : r.gen;
  const gen = calc.Generations.get(genNum);

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
  const move     = new calc.Move(gen, r.move.name, r.move.options || {});
  const field    = new calc.Field(r.field || { gameType: 'Singles' });

  const result = calc.calculate(gen, attacker, defender, move, field);
  const range  = result.range();
  const maxHP  = defender.maxHP();

  const out = {
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

  try { out.description = result.fullDesc('%', false); }
  catch (e) { out.description = ''; }

  try {
    const ko = result.kochance(false);
    out.ko_chance     = ko && ko.text ? ko.text : '';
    if (ko && typeof ko.chance === 'number') out.ko_chance_pct = +(ko.chance * 100).toFixed(1);
    if (ko && typeof ko.n      === 'number') out.ko_hits       = ko.n;
  } catch (e) { out.ko_chance = ''; }

  out.immune = range[0] === 0 && range[1] === 0;
  return out;
}

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }
  if (req.method !== 'POST') {
    res.writeHead(405);
    res.end();
    return;
  }

  let body = '';
  req.on('data', d => { body += d; });
  req.on('end', () => {
    let out;
    try {
      const r = JSON.parse(body || '{}');
      out = runCalc(r);
    } catch (e) {
      out = { ok: false, error: String((e && e.message) || e) };
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(out));
  });
});

server.listen(PORT, '0.0.0.0', () => {
  process.stderr.write(`champions-calc HTTP sidecar listening on :${PORT}\n`);
});
