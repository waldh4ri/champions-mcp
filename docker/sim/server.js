'use strict';
/**
 * champions-sim HTTP sidecar.
 *
 *   GET  /health
 *   POST /validate | /simulate | /compare | /replay   (JSON body, see api.js)
 *
 * Listens on PORT (default 3000). Worker count from SIM_WORKERS (default:
 * cores - 1). Only reachable on the compose-internal network.
 *
 * Logs (../common/log.js) one line when a batch starts, a progress line
 * every few seconds while it runs, and its result — tagged with the MCP
 * request id from X-Request-Id. Health checks are not logged.
 */
let logmod;
try { logmod = require('./log'); } catch (e) { logmod = require('../common/log'); }
const log = logmod.logger('sim');
logmod.captureConsole(log);

const http = require('http');
const { Pool } = require('./pool');
const api = require('./api');

const PORT = parseInt(process.env.PORT || '3000', 10);
const MAX_BODY = 8 * 1024 * 1024;
const PROGRESS_EVERY_MS = 5000;
const pool = new Pool(parseInt(process.env.SIM_WORKERS || '0', 10) || undefined);
let inFlight = 0;
let served = 0;

function send(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(obj));
}

const pct = x => (typeof x === 'number' ? `${(x * 100).toFixed(1)}%` : undefined);
const ci = c => (Array.isArray(c) ? `${pct(c[0])}-${pct(c[1])}` : undefined);

/** Battles a request will play (for progress lines), from its body. */
function plannedBattles(op, body) {
  const gpo = parseInt(body.games_per_opponent || 100, 10);
  const opps = (body.opponents || []).length;
  if (op === 'simulate') return gpo * opps;
  if (op === 'compare') return gpo * opps * ((body.variants || []).length + 1);
  if (op === 'replay') return 1;
  return 0;
}

/** The request as log fields: what is being asked. */
function describe(op, body) {
  const f = {
    format: body.format,
    policy: body.policy || body.policy_a, opponent_policy: body.opponent_policy || body.policy_b,
    information: body.information,
  };
  if (op === 'simulate' || op === 'compare') {
    Object.assign(f, {
      opponents: (body.opponents || []).length, games_per_opponent: body.games_per_opponent,
      battles: plannedBattles(op, body), seed: body.seed,
    });
    if (op === 'compare') f.variants = (body.variants || []).length + 1;
  }
  if (op === 'validate') f.teams = (body.teams || []).length;
  if (op === 'replay') f.seed = body.seed;
  return f;
}

/** The result as log fields: what came out. */
function outcome(op, out) {
  if (op === 'simulate') {
    const worst = (out.worst_matchups || [])[0];
    return {
      win_rate: pct(out.win_rate), ci95: ci(out.win_rate_ci95), games: out.games,
      avg_turns: out.avg_turns,
      worst: worst ? `${String(worst.label || worst.id).slice(0, 60).replace(/[\s/]+$/, '')} (${pct(worst.win_rate)})` : undefined,
      dropped_opponents: (out.opponents_dropped || []).length || undefined,
      battle_errors: out.errors || undefined, policy_fallbacks: out.policy_fallbacks || undefined,
      battles_per_sec: out.battles_per_sec,
    };
  }
  if (op === 'compare') {
    const parts = (out.variants || []).map(v => {
      const d = v.delta_vs_base;
      return `${v.id}=${pct(v.win_rate)}${d ? ` (${d.win_rate >= 0 ? '+' : ''}${(d.win_rate * 100).toFixed(1)}${d.significant ? '*' : ''})` : ''}`;
    });
    return { variants: parts.join(' '), opponents: out.opponents_used, dropped_opponents: (out.opponents_dropped || []).length || undefined };
  }
  if (op === 'replay') return { winner: out.winner, turns: out.turns, log_lines: (out.log || []).length };
  if (op === 'validate') {
    const teams = out.teams || [];
    return { valid: `${teams.filter(t => t.valid).length}/${teams.length}`, problems: teams.flatMap(t => t.problems).slice(0, 2).join(' | ') || undefined };
  }
  return {};
}

const server = http.createServer((req, res) => {
  const op = (req.url || '/').replace(/^\/+|\/+$/g, '').split('?')[0] || 'health';
  const rid = req.headers['x-request-id'];
  if (req.method === 'GET') {
    if (op === 'health') {
      return api.handle(pool, 'health', {}).then(r => send(res, 200, { ...r, in_flight: inFlight, served, battles_played: pool.battlesDone }));
    }
    log.warn('unknown path', { rid, method: 'GET', path: req.url, status: 404 });
    return send(res, 404, { ok: false, error: 'not found' });
  }
  if (req.method !== 'POST') {
    log.warn('rejected request', { rid, method: req.method, path: req.url, status: 405 });
    return send(res, 405, { ok: false, error: 'method not allowed' });
  }
  let body = '';
  let tooBig = false;
  req.on('data', d => {
    body += d;
    if (body.length > MAX_BODY) {
      tooBig = true;
      log.warn('request body too large; dropped', { rid, op, limit_mb: MAX_BODY / 1024 / 1024 });
      req.destroy();
    }
  });
  req.on('end', async () => {
    if (tooBig) return;
    let parsed;
    try { parsed = JSON.parse(body || '{}'); } catch (e) {
      log.warn('invalid JSON body', { rid, op, bytes: body.length });
      return send(res, 400, { ok: false, error: 'invalid JSON' });
    }
    const t0 = Date.now();
    const planned = plannedBattles(op, parsed);
    const startDone = pool.battlesDone;
    inFlight++;
    if (planned > 1) log.info(`▶ ${op}`, { rid, ...describe(op, parsed), workers: pool.size, in_flight: inFlight });
    const timer = planned > 1 ? setInterval(() => {
      const done = pool.battlesDone - startDone;
      const s = (Date.now() - t0) / 1000;
      log.info(`… ${op} running`, {
        rid, battles: `${Math.min(done, planned)}/${planned}`, elapsed: logmod.fmtDuration(Date.now() - t0),
        rate: `${Math.round(done / Math.max(s, 0.001))}/s`, in_flight: inFlight > 1 ? inFlight : undefined,
      });
    }, PROGRESS_EVERY_MS) : null;
    let out;
    try {
      out = await api.handle(pool, op, parsed);
    } finally {
      if (timer) clearInterval(timer);
      inFlight--;
      served++;
    }
    const took = logmod.fmtDuration(Date.now() - t0);
    if (out.ok) {
      log.info(`✓ ${op}`, { rid, took, ...(planned > 1 ? {} : describe(op, parsed)), ...outcome(op, out) });
    } else {
      log.warn(`✗ ${op}`, {
        rid, took, error: out.error,
        problems: Array.isArray(out.problems) ? out.problems.slice(0, 3).join(' | ') : undefined,
        dropped: Array.isArray(out.dropped) ? out.dropped.length : undefined,
      });
    }
    send(res, 200, out);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  const p = pool.plan;
  log.info('ready — battle simulator listening', {
    bold: true, port: PORT, workers: pool.size, limited_by: p.limitedBy, cpus: p.cpus,
    memory_mb: p.memMb, worker_heap_mb: p.heapMb, showdown_sha: (process.env.SHOWDOWN_SHA || 'unknown').slice(0, 10),
    max_battles: process.env.SIM_MAX_BATTLES || 50000, node: process.version,
  });
});

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    log.info('shutting down', { signal: sig, requests_served: served, battles_played: pool.battlesDone, in_flight: inFlight });
    server.close();
    pool.close().finally(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
  });
}
