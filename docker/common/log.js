'use strict';
/**
 * One-line, key=value, optionally colored logging for the Node sidecars —
 * the same line shape as champions_mcp/log.py, so `docker compose logs`
 * reads as one stream across services:
 *
 *   12:04:31.207 INFO  calc        [3f9c1a] Garchomp Earthquake → Incineroar dmg=48.1-57.0% ms=3
 *
 * Writes to stderr (stdout is reserved for the one-shot CLI's JSON reply).
 * LOG_LEVEL (debug|info|warn|error, default info) and LOG_COLOR (1|0|auto;
 * NO_COLOR / FORCE_COLOR honoured; auto = color on a TTY) configure it.
 * `[rid]` is the X-Request-Id the MCP server sends with each call.
 */

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const STYLE = {
  debug: ['DEBUG', '\x1b[2;37m'], info: ['INFO ', '\x1b[32m'],
  warn: ['WARN ', '\x1b[33m'], error: ['ERROR', '\x1b[31m'],
};
const RESET = '\x1b[0m', DIM = '\x1b[2m', BOLD = '\x1b[1m';
const NAME = '\x1b[36m', RID = '\x1b[35m', KEY = '\x1b[2;36m';

function colorEnabled() {
  const v = String(process.env.LOG_COLOR || 'auto').toLowerCase();
  if (['1', 'true', 'yes', 'on', 'always'].includes(v)) return true;
  if (['0', 'false', 'no', 'off', 'never'].includes(v)) return false;
  if (process.env.NO_COLOR) return false;
  if (process.env.FORCE_COLOR) return true;
  return Boolean(process.stderr.isTTY);
}

const COLOR = colorEnabled();
const THRESHOLD = LEVELS[String(process.env.LOG_LEVEL || 'info').toLowerCase().replace('warning', 'warn')] || LEVELS.info;
const c = (code, s) => (COLOR ? code + s + RESET : s);

function fmtValue(v) {
  if (typeof v === 'number' && !Number.isInteger(v)) v = +v.toPrecision(4);
  else if (Array.isArray(v)) v = v.join(',');
  else if (v && typeof v === 'object') v = Object.entries(v).map(([k, x]) => `${k}:${x}`).join(',');
  let s = String(v);
  if (s === '' || /[\s"=]/.test(s)) s = JSON.stringify(s);
  return s;
}

function fmtDuration(ms) {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
}

function timestamp() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

/**
 * A logger for one component. Each method takes (msg, fields); a `rid` field
 * is shown as [rid], a `bold` field emphasises the message, an Error in
 * `err` is appended as an indented stack.
 */
function logger(name) {
  const write = (level, msg, fields) => {
    if (LEVELS[level] < THRESHOLD) return;
    const f = { ...(fields || {}) };
    const [label, color] = STYLE[level];
    const parts = [c(DIM, timestamp()), c(color, label), c(NAME, name.padEnd(11))];
    if (f.rid) parts.push(c(RID, `[${f.rid}]`));
    const bold = f.bold;
    const err = f.err;
    delete f.rid; delete f.bold; delete f.err;
    parts.push(LEVELS[level] >= LEVELS.warn ? c(color, msg) : bold ? c(BOLD, msg) : msg);
    for (const [k, v] of Object.entries(f)) {
      if (v === undefined || v === null) continue;
      parts.push(c(KEY, k + '=') + fmtValue(v));
    }
    let line = parts.join(' ');
    if (err && err.stack) line += '\n' + err.stack.split('\n').map(l => '    ' + l).join('\n');
    process.stderr.write(line + '\n');
  };
  return {
    debug: (m, f) => write('debug', m, f),
    info: (m, f) => write('info', m, f),
    warn: (m, f) => write('warn', m, f),
    error: (m, f) => write('error', m, f),
    enabled: level => LEVELS[level] >= THRESHOLD,
  };
}

/** Route stray console.* calls (e.g. from libraries) through a logger. */
function captureConsole(log) {
  const join = a => a.map(x => (x instanceof Error ? x.stack : typeof x === 'string' ? x : JSON.stringify(x))).join(' ');
  console.log = console.info = (...a) => log.info(join(a));
  console.debug = console.trace = (...a) => log.debug(join(a));
  console.warn = (...a) => log.warn(join(a));
  console.error = (...a) => log.error(join(a));
}

module.exports = { logger, captureConsole, fmtDuration };
