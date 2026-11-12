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
 *
 * Logs one line per calc (see ../common/log.js), tagged with the MCP
 * request id from X-Request-Id; health checks are not logged.
 */

const fs = require('fs');
const http = require('http');
let logmod;
try { logmod = require('./log'); } catch (e) { logmod = require('../common/log'); }
const log = logmod.logger('calc');
// Library chatter goes through the logger too (stderr; stdout stays clean).
logmod.captureConsole(log);

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

/** What this calc build knows, for the startup line and /health. */
function datasetInfo() {
  const info = { gen: 0 };
  try {
    const gen = calc.Generations.get(0);
    info.species = [...gen.species].length;
    info.moves = [...gen.moves].length;
    info.items = [...gen.items].length;
  } catch (e) { /* older calc builds: counts are informational only */ }
  try { info.damage_calc_commit = fs.readFileSync('/app/calc/dist/.commit', 'utf8').trim(); } catch (e) { /* not recorded */ }
  return info;
}

const INFO = datasetInfo();
let served = 0;

/** "Garchomp-Mega (Jolly, atk:32) Earthquake → Incineroar" style request label. */
function sideLabel(side) {
  const o = (side && side.options) || {};
  const extras = [o.item, o.ability, o.nature].filter(Boolean);
  return `${side && side.name}${extras.length ? ` (${extras.join(', ')})` : ''}`;
}

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, served, ...INFO }));
    return;
  }
  const rid = req.headers['x-request-id'];
  if (req.method !== 'POST') {
    log.warn('rejected request', { rid, method: req.method, url: req.url, status: 405 });
    res.writeHead(405);
    res.end();
    return;
  }

  const t0 = process.hrtime.bigint();
  let body = '';
  req.on('data', d => { body += d; });
  req.on('end', () => {
    let out, r = {};
    try {
      r = JSON.parse(body || '{}');
      out = runCalc(r);
    } catch (e) {
      out = { ok: false, error: String((e && e.message) || e) };
    }
    served++;
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    const field = r.field || {};
    if (out.ok) {
      log.info(`${out.attacker} ${out.move} → ${out.defender}`, {
        rid,
        dmg: out.immune ? 'immune' : `${out.min_pct}-${out.max_pct}%`,
        ko: out.ko_chance || undefined,
        hp: out.defender_max_hp,
        weather: field.weather, terrain: field.terrain,
        game: field.gameType,
        crit: (r.move && r.move.options && r.move.options.isCrit) || undefined,
        ms: +ms.toFixed(1),
      });
      log.debug('calc detail', { rid, attacker: sideLabel(r.attacker), defender: sideLabel(r.defender), desc: out.description });
    } else {
      log.warn('calc failed', {
        rid, error: out.error,
        attacker: r.attacker && r.attacker.name, move: r.move && r.move.name,
        defender: r.defender && r.defender.name, ms: +ms.toFixed(1),
      });
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(out));
  });
});

server.listen(PORT, '0.0.0.0', () => {
  log.info('ready — damage calc listening', { bold: true, port: PORT, node: process.version, ...INFO });
});

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    log.info('shutting down', { signal: sig, calcs_served: served });
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 2000).unref();
  });
}
