'use strict';
/**
 * Request handlers shared by the HTTP server (server.js) and the one-shot
 * stdin/stdout CLI (cli.js).
 *
 * Operations
 *   validate  {format, teams:[{id, paste}]}
 *   simulate  {format, team, opponents:[{id, paste, label?}], games_per_opponent,
 *              policy, opponent_policy, seed, swap_sides}
 *   compare   {format, base, variants:[{id, paste}], opponents, games_per_opponent, ...}
 *   replay    {format, team_a, team_b, policy_a, policy_b, seed}
 *
 * Every simulated team is validated against the real Reg M-C format first:
 * an unvalidated battle happily applies items Champions doesn't have
 * (Choice Specs / Band, Assault Vest are "Past" there).
 */
const engine = require('./engine');
const { POLICIES, INFO_MODES } = require('./policies');

const SHOWDOWN_SHA = process.env.SHOWDOWN_SHA || 'unknown';
const MAX_BATTLES = parseInt(process.env.SIM_MAX_BATTLES || '50000', 10);

class ApiError extends Error {
  constructor(message, extra) { super(message); this.extra = extra || {}; }
}

function policyName(p, fallback) {
  const v = String(p || fallback).toLowerCase();
  if (!POLICIES.includes(v)) throw new ApiError(`unknown policy ${p}; expected one of ${POLICIES.join(', ')}`);
  return v;
}

function intArg(v, def, lo, hi) {
  const n = v === undefined || v === null ? def : parseInt(v, 10);
  if (!Number.isFinite(n)) return def;
  return Math.min(hi, Math.max(lo, n));
}

/** Parse + validate one team; throws ApiError with Showdown's problems if illegal. */
function legalTeam(formatid, paste, label) {
  let sets;
  try { sets = engine.parseTeam(paste); } catch (e) { throw new ApiError(`${label}: ${e.message}`); }
  const problems = engine.validate(formatid, sets);
  if (problems.length) throw new ApiError(`${label} is not legal in ${formatid}`, { problems });
  return sets;
}

/** Validate a list of opponents, dropping illegal ones (reported, not fatal). */
function legalOpponents(formatid, opponents) {
  const ok = [], dropped = [];
  for (const o of opponents || []) {
    try {
      const sets = engine.parseTeam(o.paste);
      const problems = engine.validate(formatid, sets);
      if (problems.length) dropped.push({ id: o.id, problem: problems[0] });
      else ok.push(o);
    } catch (e) {
      dropped.push({ id: o.id, problem: e.message });
    }
  }
  if (!ok.length) throw new ApiError('no legal opponent teams', { dropped });
  return { ok, dropped };
}

function wilson(p, n, z = 1.96) {
  if (!n) return [0, 1];
  const d = 1 + z * z / n;
  const c = (p + z * z / (2 * n)) / d;
  const h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
  return [+Math.max(0, c - h).toFixed(4), +Math.min(1, c + h).toFixed(4)];
}

const round = (x, d = 3) => +x.toFixed(d);

function buildJobs(nOpp, gpo, seed, swapSides) {
  const jobs = [];
  for (let r = 0; r < gpo; r++) {
    for (let o = 0; o < nOpp; o++) {
      const k = r * nOpp + o;
      jobs.push({ k, a: 0, b: o + 1, seed: engine.jobSeed(seed, k), swap: swapSides && r % 2 === 1 });
    }
  }
  return jobs;
}

/** Fold per-battle results into the report the MCP tools return. */
function aggregate(results, opponents, ourSets) {
  const done = results.filter(r => !r.error);
  const errors = results.filter(r => r.error);
  const n = done.length;
  const w = done.filter(r => r.o === 1).length, t = done.filter(r => r.o === 0.5).length;
  const p = n ? (w + 0.5 * t) / n : 0;

  const perOpp = opponents.map(o => ({ id: o.id, label: o.label, games: 0, wins: 0, losses: 0, ties: 0 }));
  const members = {};
  for (const s of ourSets) members[s.species] = { brought: 0, led: 0, kos: 0, fainted: 0, mega: 0, wins_brought: 0 };
  const threats = {};
  let turns = 0, fallbacks = 0, asP1 = 0, winsP1 = 0;

  for (const r of done) {
    turns += r.turns; fallbacks += r.fallbacks || 0;
    if (r.side === 'p1') { asP1++; winsP1 += r.o; }
    const po = perOpp[(r.k % opponents.length)];
    po.games++;
    if (r.o === 1) po.wins++; else if (r.o === 0) po.losses++; else po.ties++;
    for (const name of r.us.brought) {
      const m = members[name] || (members[name] = { brought: 0, led: 0, kos: 0, fainted: 0, mega: 0, wins_brought: 0 });
      m.brought++;
      m.wins_brought += r.o;
      if (r.us.leads.includes(name)) m.led++;
      if (r.us.fainted.includes(name)) m.fainted++;
      if (r.us.mega === name) m.mega++;
      m.kos += r.us.kos[name] || 0;
    }
    for (const name of r.them.brought) {
      const th = threats[name] || (threats[name] = { brought: 0, kos: 0, fainted: 0 });
      th.brought++;
      th.kos += r.them.kos[name] || 0;
      if (r.them.fainted.includes(name)) th.fainted++;
    }
  }

  const memberReport = Object.entries(members).map(([species, m]) => ({
    species,
    brought_pct: round(n ? m.brought / n : 0),
    lead_pct: round(n ? m.led / n : 0),
    win_rate_when_brought: m.brought ? round(m.wins_brought / m.brought) : null,
    kos_per_game_brought: m.brought ? round(m.kos / m.brought, 2) : null,
    faint_rate_when_brought: m.brought ? round(m.fainted / m.brought) : null,
    mega_pct_when_brought: m.brought && m.mega ? round(m.mega / m.brought) : undefined,
  }));
  const threatReport = Object.entries(threats)
    .filter(([, th]) => th.kos > 0)
    .map(([species, th]) => ({ species, games_brought: th.brought, kos_on_us: th.kos, kos_per_game_brought: round(th.kos / th.brought, 2) }))
    .sort((a, b) => b.kos_on_us - a.kos_on_us)
    .slice(0, 10);
  const opps = perOpp.filter(o => o.games).map(o => ({
    ...o, win_rate: round((o.wins + 0.5 * o.ties) / o.games),
  })).sort((a, b) => a.win_rate - b.win_rate);

  return {
    games: n,
    wins: w, losses: n - w - t, ties: t,
    win_rate: round(p),
    win_rate_ci95: wilson(p, n),
    avg_turns: n ? round(turns / n, 1) : null,
    side_balance: { games_as_p1: asP1, win_rate_as_p1: asP1 ? round(winsP1 / asP1) : null },
    members: memberReport,
    opponent_threats: threatReport,
    worst_matchups: opps.slice(0, 5),
    best_matchups: opps.slice(-5).reverse(),
    ...(opponents.length > 1 ? { per_opponent: opps } : {}),
    errors: errors.length,
    ...(errors.length ? { error_samples: [...new Set(errors.map(e => e.error))].slice(0, 3) } : {}),
    policy_fallbacks: fallbacks,
  };
}

function engineInfo(formatid, policy, oppPolicy, view) {
  return {
    information: view ? view.mode : 'omniscient',
    engine: 'Pokémon Showdown sim, champions mod',
    showdown_sha: SHOWDOWN_SHA,
    format_id: formatid,
    policy, opponent_policy: oppPolicy,
    note: 'Win rates are relative to the AI policy playing both sides, not a ladder prediction.',
  };
}

/** What each side's AI knows about the other (see policies.js). Default: open team sheets. */
function viewArg(body) {
  const mode = String(body.information || 'open_sheets').toLowerCase();
  if (!INFO_MODES.includes(mode)) throw new ApiError(`unknown information mode ${body.information}; expected one of ${INFO_MODES.join(', ')}`);
  return { mode, beliefs: body.beliefs || {} };
}

function commonArgs(body) {
  const formatid = engine.formatId(body.format);
  const policy = policyName(body.policy, 'heuristic');
  const oppPolicy = policyName(body.opponent_policy, policy);
  const seed = intArg(body.seed, 1, 0, 2 ** 31 - 1);
  const gpo = intArg(body.games_per_opponent, 100, 1, MAX_BATTLES);
  const swap = body.swap_sides !== false;
  return { formatid, policy, oppPolicy, seed, gpo, swap, view: viewArg(body) };
}

async function simulate(pool, body) {
  const { formatid, policy, oppPolicy, seed, gpo, swap, view } = commonArgs(body);
  const ours = legalTeam(formatid, body.team, 'team');
  const { ok: opponents, dropped } = legalOpponents(formatid, body.opponents);
  const total = gpo * opponents.length;
  if (total > MAX_BATTLES) throw new ApiError(`${total} battles requested; the cap is ${MAX_BATTLES}`);
  const jobs = buildJobs(opponents.length, gpo, seed, swap);
  const t0 = Date.now();
  const results = await pool.runJobs({
    formatid, policy, opponentPolicy: oppPolicy, view, teams: [body.team, ...opponents.map(o => o.paste)],
  }, jobs);
  const ms = Date.now() - t0;
  return {
    ...aggregate(results, opponents, ours),
    opponents_used: opponents.length,
    ...(dropped.length ? { opponents_dropped: dropped } : {}),
    elapsed_ms: ms, battles_per_sec: round(results.length / Math.max(1, ms) * 1000, 1),
    ...engineInfo(formatid, policy, oppPolicy, view),
  };
}

async function compare(pool, body) {
  const { formatid, policy, oppPolicy, seed, gpo, swap, view } = commonArgs(body);
  const variants = [{ id: body.base_id || 'base', paste: body.base }, ...(body.variants || [])];
  if (variants.length < 2) throw new ApiError('give at least one variant to compare against the base team');
  const parsed = variants.map(v => legalTeam(formatid, v.paste, `variant ${v.id}`));
  const { ok: opponents, dropped } = legalOpponents(formatid, body.opponents);
  const total = gpo * opponents.length * variants.length;
  if (total > MAX_BATTLES) throw new ApiError(`${total} battles requested; the cap is ${MAX_BATTLES}`);
  // Common random numbers: every variant plays the exact same opponents with
  // the exact same battle seeds, so the paired difference has far lower
  // variance than two independent win rates.
  const jobs = buildJobs(opponents.length, gpo, seed, swap);
  const t0 = Date.now();
  const runs = [];
  for (const v of variants) {
    runs.push(await pool.runJobs({
      formatid, policy, opponentPolicy: oppPolicy, view, teams: [v.paste, ...opponents.map(o => o.paste)],
    }, jobs));
  }
  const ms = Date.now() - t0;
  const base = runs[0];
  const report = variants.map((v, i) => {
    const agg = aggregate(runs[i], opponents, parsed[i]);
    const out = {
      id: v.id, games: agg.games, win_rate: agg.win_rate, win_rate_ci95: agg.win_rate_ci95,
      avg_turns: agg.avg_turns, members: agg.members, worst_matchups: agg.worst_matchups.slice(0, 3),
      errors: agg.errors,
    };
    if (i > 0) {
      const d = [];
      for (let k = 0; k < jobs.length; k++) {
        const a = runs[i][k], b = base[k];
        if (a && b && !a.error && !b.error) d.push(a.o - b.o);
      }
      const mean = d.reduce((x, y) => x + y, 0) / Math.max(1, d.length);
      const sd = Math.sqrt(d.reduce((x, y) => x + (y - mean) ** 2, 0) / Math.max(1, d.length - 1));
      const se = sd / Math.sqrt(Math.max(1, d.length));
      out.delta_vs_base = {
        win_rate: round(mean), ci95: [round(mean - 1.96 * se), round(mean + 1.96 * se)],
        significant: Math.abs(mean) > 1.96 * se, paired_games: d.length,
      };
    }
    return out;
  });
  return {
    variants: report, opponents_used: opponents.length, games_per_opponent: gpo,
    ...(dropped.length ? { opponents_dropped: dropped } : {}),
    elapsed_ms: ms, ...engineInfo(formatid, policy, oppPolicy, view),
  };
}

async function replay(pool, body) {
  const formatid = engine.formatId(body.format);
  const pa = policyName(body.policy_a, 'heuristic'), pb = policyName(body.policy_b, pa);
  legalTeam(formatid, body.team_a, 'team_a');
  legalTeam(formatid, body.team_b, 'team_b');
  const seed = intArg(body.seed, 1, 0, 2 ** 31 - 1);
  const view = viewArg(body);
  const [r] = await pool.runJobs({ formatid, policy: pa, opponentPolicy: pb, view, teams: [body.team_a, body.team_b], record: true },
    [{ k: 0, a: 0, b: 1, seed: engine.jobSeed(seed, 0), swap: false }]);
  if (r.error) throw new ApiError(r.error);
  return {
    winner: r.o === 1 ? 'A' : r.o === 0 ? 'B' : 'tie',
    turns: r.turns, team_a: r.us, team_b: r.them, log: r.log, seed,
    ...engineInfo(formatid, pa, pb, view),
  };
}

function validateTeams(body) {
  const formatid = engine.formatId(body.format);
  return {
    format_id: formatid,
    teams: (body.teams || []).map(t => {
      try {
        const problems = engine.validate(formatid, engine.parseTeam(t.paste));
        return { id: t.id, valid: !problems.length, problems };
      } catch (e) {
        return { id: t.id, valid: false, problems: [e.message] };
      }
    }),
  };
}

async function handle(pool, op, body) {
  try {
    switch (op) {
      case 'health': return {
        ok: true, showdown_sha: SHOWDOWN_SHA, formats: engine.FORMATS,
        workers: pool.size, cpus: pool.plan.cpus, memory_mb: pool.plan.memMb,
        worker_heap_mb: pool.plan.heapMb, limited_by: pool.plan.limitedBy,
      };
      case 'validate': return { ok: true, ...validateTeams(body) };
      case 'simulate': return { ok: true, ...(await simulate(pool, body)) };
      case 'compare': return { ok: true, ...(await compare(pool, body)) };
      case 'replay': return { ok: true, ...(await replay(pool, body)) };
      default: return { ok: false, error: `unknown operation ${op}` };
    }
  } catch (e) {
    return { ok: false, error: String(e.message || e), ...(e.extra || {}) };
  }
}

module.exports = { handle, aggregate, wilson, buildJobs };
