'use strict';
/**
 * Fixed-size worker_threads pool that sizes itself to the machine.
 *
 * Battles are CPU-bound and independent, so throughput scales with cores
 * (~700 VGC battles/s measured on 22 threads). By default the pool uses every
 * CPU the container may use — host cores, or the Docker/cgroup quota
 * (`--cpus`, compose `cpus:`) when one is set — and as many workers as the
 * memory budget (host RAM or the cgroup memory limit) can hold, each with a
 * V8 heap limit sized to its share. SIM_WORKERS overrides the count.
 */
const { Worker } = require('worker_threads');
const fs = require('fs');
const os = require('os');
const path = require('path');

const MB = 1024 * 1024;
// Measured: a warmed worker (Showdown dex + a battle in flight) sits around
// 90-130 MB RSS. Budget a bit more so a busy pool never swaps.
const WORKER_BUDGET_MB = parseInt(process.env.SIM_WORKER_MB || '192', 10);
const RESERVED_MB = 256; // main thread, HTTP server, request/result JSON

function readCgroup(file) {
  try { return fs.readFileSync(file, 'utf8').trim(); } catch (e) { return null; }
}

/** CPUs this process may use: cores, capped by a cgroup v2/v1 CPU quota. */
function cpuBudget() {
  const cores = typeof os.availableParallelism === 'function' ? os.availableParallelism() : os.cpus().length;
  let quota = null;
  const v2 = readCgroup('/sys/fs/cgroup/cpu.max'); // "max 100000" or "200000 100000"
  if (v2) {
    const [q, p] = v2.split(/\s+/);
    if (q !== 'max' && Number(p) > 0) quota = Number(q) / Number(p);
  } else {
    const q = Number(readCgroup('/sys/fs/cgroup/cpu/cpu.cfs_quota_us'));
    const p = Number(readCgroup('/sys/fs/cgroup/cpu/cpu.cfs_period_us'));
    if (q > 0 && p > 0) quota = q / p;
  }
  return Math.max(1, Math.min(cores, quota ? Math.floor(quota) || 1 : cores));
}

/** Bytes of memory this process may use: host RAM, capped by a cgroup limit. */
function memBudget() {
  let limit = Infinity;
  const v2 = readCgroup('/sys/fs/cgroup/memory.max');
  if (v2 && v2 !== 'max') limit = Number(v2);
  const v1 = Number(readCgroup('/sys/fs/cgroup/memory/memory.limit_in_bytes'));
  if (v1 > 0 && v1 < 2 ** 60) limit = Math.min(limit, v1);
  return Math.min(os.totalmem(), limit);
}

/** Worker count + per-worker heap limit for this machine. */
function plan(requested) {
  const cpus = cpuBudget();
  const memMb = Math.floor(memBudget() / MB);
  const byMem = Math.max(1, Math.floor((memMb - RESERVED_MB) / WORKER_BUDGET_MB));
  const size = requested > 0 ? requested : Math.min(cpus, byMem);
  // Give each worker an even share of the memory left over (bounded).
  const heapMb = Math.max(128, Math.min(1024, Math.floor((memMb - RESERVED_MB) / size * 0.8)));
  return { size, cpus, memMb, heapMb, limitedBy: requested > 0 ? 'SIM_WORKERS' : (byMem < cpus ? 'memory' : 'cpu') };
}

class Pool {
  constructor(requested) {
    this.plan = plan(requested || 0);
    this.size = this.plan.size;
    this.workers = [];
    this.idle = [];
    this.queue = [];
    this.pending = new Map();
    this.nextId = 1;
    for (let i = 0; i < this.size; i++) this._spawn();
  }

  _spawn() {
    const w = new Worker(path.join(__dirname, 'worker.js'), {
      resourceLimits: { maxOldGenerationSizeMb: this.plan.heapMb },
    });
    w.on('message', msg => {
      const p = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      w.currentTask = null;
      this.idle.push(w);
      this._drain();
      if (p) p.resolve(msg.out);
    });
    w.on('error', err => {
      // A crashed worker (incl. hitting its heap limit) fails its task and is replaced.
      const task = w.currentTask;
      this.workers = this.workers.filter(x => x !== w);
      this.idle = this.idle.filter(x => x !== w);
      if (task) {
        const p = this.pending.get(task.id);
        this.pending.delete(task.id);
        if (p) p.resolve(task.jobs.map(j => ({ k: j.k, error: 'worker crashed: ' + String(err.message || err) })));
      }
      if (!this.closed) { this._spawn(); this._drain(); }
    });
    this.workers.push(w);
    this.idle.push(w);
  }

  _drain() {
    while (this.idle.length && this.queue.length) {
      const w = this.idle.pop();
      const task = this.queue.shift();
      w.currentTask = task;
      w.postMessage(task);
    }
  }

  run(task) {
    return new Promise(resolve => {
      task.id = this.nextId++;
      this.pending.set(task.id, { resolve });
      this.queue.push(task);
      this._drain();
    });
  }

  /**
   * Run a job list across the pool; results come back ordered by k.
   * ~4 chunks per worker keeps every core busy to the end (battles vary a
   * lot in length) without drowning the pool in messages.
   */
  async runJobs(base, jobs) {
    const chunk = Math.max(2, Math.ceil(jobs.length / (this.size * 4)));
    const parts = [];
    for (let i = 0; i < jobs.length; i += chunk) parts.push(this.run({ ...base, jobs: jobs.slice(i, i + chunk) }));
    const out = (await Promise.all(parts)).flat();
    out.sort((a, b) => a.k - b.k);
    return out;
  }

  close() {
    this.closed = true;
    return Promise.all(this.workers.map(w => w.terminate()));
  }
}

module.exports = { Pool, plan };
