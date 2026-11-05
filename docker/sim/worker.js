'use strict';
// Worker thread: runs a chunk of battles and reports them from the
// perspective of "our" team (the one being evaluated), whichever side it
// actually played on.
const { parentPort } = require('worker_threads');
const engine = require('./engine');

// Load the Champions dex data now, not on this worker's first battle.
engine.warm();

const parsed = new Map(); // paste text -> sets (teams repeat across chunks)

function setsFor(paste) {
  let s = parsed.get(paste);
  if (!s) {
    if (parsed.size > 4000) parsed.clear();
    s = engine.parseTeam(paste);
    parsed.set(paste, s);
  }
  return s;
}

function runJob(task, j) {
  const ours = setsFor(task.teams[j.a]), theirs = setsFor(task.teams[j.b]);
  const r = engine.runBattle({
    formatid: task.formatid,
    setsA: j.swap ? theirs : ours,
    setsB: j.swap ? ours : theirs,
    policyA: j.swap ? task.opponentPolicy : task.policy,
    policyB: j.swap ? task.policy : task.opponentPolicy,
    seed: j.seed,
    record: !!task.record,
    view: task.view || null,
  });
  const me = j.swap ? 1 : 0;
  return {
    k: j.k,
    o: r.winner === -1 ? 0.5 : r.winner === me ? 1 : 0,
    turns: r.turns,
    us: r.sides[me],
    them: r.sides[1 - me],
    side: me === 0 ? 'p1' : 'p2',
    fallbacks: r.fallbacks,
    ...(r.log ? { log: r.log } : {}),
  };
}

parentPort.on('message', task => {
  const out = [];
  for (const j of task.jobs) {
    try {
      out.push(runJob(task, j));
    } catch (e) {
      out.push({ k: j.k, error: String((e && e.message) || e).slice(0, 300) });
    }
  }
  parentPort.postMessage({ id: task.id, out });
});
