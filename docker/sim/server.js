'use strict';
/**
 * champions-sim HTTP sidecar.
 *
 *   GET  /health
 *   POST /validate | /simulate | /compare | /replay   (JSON body, see api.js)
 *
 * Listens on PORT (default 3000). Worker count from SIM_WORKERS (default:
 * cores - 1). Only reachable on the compose-internal network.
 */
for (const k of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  console[k] = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
}

const http = require('http');
const { Pool } = require('./pool');
const api = require('./api');

const PORT = parseInt(process.env.PORT || '3000', 10);
const MAX_BODY = 8 * 1024 * 1024;
const pool = new Pool(parseInt(process.env.SIM_WORKERS || '0', 10) || undefined);

function send(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(obj));
}

const server = http.createServer((req, res) => {
  const op = (req.url || '/').replace(/^\/+|\/+$/g, '').split('?')[0] || 'health';
  if (req.method === 'GET') {
    if (op === 'health') return api.handle(pool, 'health', {}).then(r => send(res, 200, r));
    return send(res, 404, { ok: false, error: 'not found' });
  }
  if (req.method !== 'POST') return send(res, 405, { ok: false, error: 'method not allowed' });
  let body = '';
  let tooBig = false;
  req.on('data', d => {
    body += d;
    if (body.length > MAX_BODY) { tooBig = true; req.destroy(); }
  });
  req.on('end', async () => {
    if (tooBig) return;
    let parsed;
    try { parsed = JSON.parse(body || '{}'); } catch (e) { return send(res, 400, { ok: false, error: 'invalid JSON' }); }
    const out = await api.handle(pool, op, parsed);
    send(res, 200, out);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  process.stderr.write(`champions-sim listening on :${PORT} with ${pool.size} workers\n`);
});
