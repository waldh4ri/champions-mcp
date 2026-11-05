'use strict';
// One-shot mode: read {"op": ..., ...body} JSON on stdin, write one JSON
// result line on stdout, exit. Used by champions-mcp when no HTTP sidecar is
// configured (`docker run --rm -i champions-sim /app/cli.js`).
for (const k of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  console[k] = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
}
const { Pool } = require('./pool');
const api = require('./api');

let input = '';
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', async () => {
  let req;
  try { req = JSON.parse(input || '{}'); } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'invalid JSON on stdin' }) + '\n');
    process.exit(1);
  }
  const pool = new Pool(parseInt(process.env.SIM_WORKERS || '0', 10) || undefined);
  const out = await api.handle(pool, req.op || 'simulate', req);
  process.stdout.write(JSON.stringify(out) + '\n');
  await pool.close();
  process.exit(0);
});
