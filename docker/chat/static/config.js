/* Configuration view (Configure button): local models, inference settings, chat
 * settings and API providers. Talks to the chat server's /api/config,
 * /api/models, /api/local-models and /api/endpoints; refreshes the main
 * page's model picker (loadModels) after every change. */
(() => {
  const css = `
  #cfgOverlay { position: fixed; inset: 0; background: var(--overlay); display: none; z-index: 50;
    align-items: flex-start; justify-content: center; padding-top: 14px; }
  #cfgOverlay.open { display: flex; }
  #cfgPanel { border: var(--frame-w) solid transparent; border-image: var(--frame-menu) 8 fill / var(--frame-w) stretch;
    image-rendering: pixelated;
    width: min(1680px, calc(100vw - 24px)); max-height: calc(100dvh - 28px);  /* height follows the content */
    display: flex; flex-direction: column; }
  #cfgHead { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 4px 8px 10px;
    border-bottom: 4px double var(--ink); }
  #cfgHead h2 { font-family: var(--font-ui); font-size: 12px; font-weight: 400; color: var(--red);
    margin-right: 14px; text-transform: uppercase; }
  .cfgTab { border-color: transparent; background: transparent; color: var(--ink-2); }
  .cfgTab:hover { color: var(--ink); background: var(--paper-2); }
  .cfgTab.active { color: var(--ink); border-color: var(--ink); background: var(--paper-2); }
  #cfgClose { margin-left: auto; }
  #cfgBody { padding: 14px 10px 8px; overflow: auto; flex: 0 1 auto; min-height: 0; font-size: 16px; line-height: 1.45; }
  #cfgBody h3 { font-family: var(--font-ui); font-size: 10px; font-weight: 400; text-transform: uppercase;
    margin: 22px 0 10px; color: var(--blue); display: flex; align-items: center; gap: 8px; }
  #cfgBody h3::before { content: ''; width: 5px; height: 9px; background: var(--red); flex-shrink: 0;
    -webkit-mask: var(--arrow-right) no-repeat center / 5px 9px; mask: var(--arrow-right) no-repeat center / 5px 9px; }
  #cfgBody h3:first-child { margin-top: 0; }
  #cfgBody code { font-family: var(--font-mono); font-size: 1.2em; color: var(--code); background: var(--paper-2); padding: 0 .25em; }
  #cfgBody b { font-weight: 700; }
  .cfgNote { color: var(--ink-2); font-size: 15px; margin: 4px 0 10px; line-height: 1.45; max-width: 120ch; }
  .cfgErr { color: var(--red); font-size: 15px; margin: 6px 0; min-height: 1em; }
  .cfgOk { color: var(--green); font-weight: 700; }
  .cfgRow { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .cfgInput, .cfgSelect { cursor: text; padding: 7px 9px; font-family: var(--font-body); font-size: 16px; line-height: 1.2;
    text-transform: none; outline: none; }
  .cfgSelect { cursor: pointer; padding-right: 26px; }
  .cfgInput::placeholder { color: var(--muted); }
  .cfgInput:focus, .cfgSelect:focus { background-color: var(--paper-2); border-color: var(--blue); }
  .cfgBtn { background: var(--accent); color: var(--on-accent); }
  .cfgBtn:hover:not(:disabled) { filter: brightness(1.08); }
  .cfgBtn:disabled { opacity: .5; cursor: default; }
  .cfgBtn.ghost { background: var(--paper); color: var(--ink); }
  .cfgBtn.ghost:hover:not(:disabled) { background: var(--paper-2); filter: none; }
  .cfgBtn.danger { background: var(--paper); color: var(--red); border-color: var(--red); }
  .cfgBtn.danger:hover:not(:disabled) { background: var(--red); color: var(--on-accent); filter: none; }
  .cfgList { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 460px), 1fr)); gap: 8px; }
  .cfgItem { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; padding: 8px 10px;
    border: var(--px) solid var(--ink); background: var(--paper-2); align-content: start; clip-path: var(--notch); }
  .cfgItem .name { font-weight: 700; overflow-wrap: anywhere; }
  .cfgItem .meta { color: var(--ink-2); font-size: 14px; grid-column: 1; }
  .cfgItem .acts { grid-row: 1 / span 2; grid-column: 2; display: flex; gap: 6px; align-items: center; }
  .badge { display: inline-block; font-family: var(--font-ui); font-size: 7px; text-transform: uppercase;
    padding: 3px 5px 2px; margin-left: 8px; vertical-align: 2px; background: var(--ink); color: var(--paper); font-weight: 400; }
  .badge.loaded { background: var(--green); color: var(--on-accent); }
  .badge.downloading, .badge.loading { background: var(--yellow); color: #2a1f2d; }
  .badge.failed { background: var(--red); color: var(--on-accent); }
  .badge.mtp { background: var(--purple); color: var(--on-accent); }
  /* progress: an EXP bar */
  .bar { height: 8px; background: var(--hp-bg); border: var(--px) solid var(--ink); margin-top: 6px; grid-column: 1; }
  .bar > i { display: block; height: 100%;
    background: repeating-linear-gradient(90deg, var(--blue) 0 6px, color-mix(in srgb, var(--blue) 70%, #fff) 6px 8px);
    transition: width .4s steps(8); }
  .cfgGrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 520px), 1fr)); gap: 16px 40px; }
  .cfgPair, .cfgForm { display: grid; grid-template-columns: minmax(150px, 210px) 1fr; gap: 10px 14px; align-items: start; }
  .cfgPair label, .cfgForm label { padding-top: 7px; color: var(--ink); font-weight: 600; }
  .cfgPair .help, .cfgForm .help { color: var(--ink-2); font-size: 14px; margin-top: 4px; line-height: 1.4; }
  .cfgForm .cfgInput { width: 100%; max-width: 560px; }
  .cfgField { display: flex; gap: 6px; align-items: center; }
  .cfgField .cfgInput, .cfgField .cfgSelect { width: 210px; }
  /* wide screens: independent sections side by side */
  .cfgCols { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 640px), 1fr)); gap: 0 48px; align-items: start; }
  .chip { display: inline-block; background: var(--paper); border-style: dashed; text-transform: none; font-family: var(--font-body);
    font-size: 15px; padding: 8px 14px; text-align: left; white-space: normal; }
  `;
  document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`);
  document.body.insertAdjacentHTML('beforeend', `
    <div id="cfgOverlay" role="dialog" aria-modal="true" aria-label="Configuration">
      <div id="cfgPanel">
        <div id="cfgHead">
          <h2>Configuration</h2>
          <button class="cfgTab" data-tab="models">Local models</button>
          <button class="cfgTab" data-tab="inference">Inference</button>
          <button class="cfgTab" data-tab="chat">Chat</button>
          <button class="cfgTab" data-tab="providers">API providers</button>
          <button id="cfgClose" class="cfgBtn ghost" title="Close (Esc)">Close</button>
        </div>
        <div id="cfgBody"></div>
      </div>
    </div>`);

  const overlay = document.getElementById('cfgOverlay');
  const body = document.getElementById('cfgBody');
  const h = s => esc(String(s ?? ''));
  let tab = 'models';
  let models = null;   // last /api/models
  let config = null;   // last /api/config
  let orInfo = null;   // last /api/openrouter
  let orUsage = null;  // last /api/openrouter/usage
  let orSearch = { q: '', results: [], total: 0 };
  let infScope = '';   // '' = all models, else a local model id
  let poll = null;

  async function api(method, url, payload) {
    const r = await fetch(url, {
      method, headers: payload ? { 'Content-Type': 'application/json' } : undefined,
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  }

  // `fromPoll`: background refreshes only redraw the Models tab (progress
  // bars); the form tabs redraw on open/tab switch/save, never under typing.
  async function refresh(fromPoll = false) {
    [models, config, orInfo, orUsage] = await Promise.all([
      api('GET', '/api/models').catch(() => models), api('GET', '/api/config').catch(() => config),
      fromPoll ? orInfo : api('GET', '/api/openrouter').catch(() => orInfo),
      fromPoll ? orUsage : api('GET', '/api/openrouter/usage').catch(() => orUsage)]);
    if (!fromPoll || tab === 'models') render();
    const busy = (models?.models || []).some(m => ['downloading', 'loading'].includes(m.status));
    clearTimeout(poll);
    if (overlay.classList.contains('open')) poll = setTimeout(() => refresh(true), busy ? 2000 : 8000);
  }

  function open(which) {
    if (which) tab = which;
    overlay.classList.add('open');
    refresh();
  }
  function close() {
    overlay.classList.remove('open');
    clearTimeout(poll);
    loadModels();
  }
  document.getElementById('cfgBtn').addEventListener('click', () => open());
  document.getElementById('cfgClose').addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && overlay.classList.contains('open')) close(); });
  document.querySelectorAll('.cfgTab').forEach(b => b.addEventListener('click', () => { tab = b.dataset.tab; render(); }));

  // Keep the panel in step when the page's own picker polling sees changes.
  window.addEventListener('models-updated', e => {
    if (overlay.classList.contains('open') && tab === 'models') { models = e.detail; render(); }
  });

  function render() {
    document.querySelectorAll('.cfgTab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const focused = document.activeElement && body.contains(document.activeElement) ? document.activeElement.id : null;
    const keep = {};
    body.querySelectorAll('input[id]').forEach(i => { keep[i.id] = i.value; });
    body.innerHTML = { models: renderModels, inference: renderInference, chat: renderChat, providers: renderProviders }[tab]();
    // re-rendering on poll must not eat what the user is typing
    body.querySelectorAll('input[id]').forEach(i => { if (i.id in keep && i.dataset.keep !== 'no') i.value = keep[i.id]; });
    if (focused) document.getElementById(focused)?.focus();
    wire();
  }

  // ── Local models ─────────────────────────────────────────────────────────
  const fmtGB = n => n ? `${(n / 1e9).toFixed(1)} GB` : '';

  function renderModels() {
    const prov = models?.providers || {};
    const local = (models?.models || []).filter(m => m.provider === 'local');
    const state = prov.llama;
    let html = `<h3>Local models (llama.cpp)</h3>`;
    if (state === 'disabled') return html + `<p class="cfgNote">Disabled by CHAT_PROVIDER.</p>`;
    if (state !== 'ready') {
      return html + `<p class="cfgNote">The local model server isn't reachable yet. It starts with the stack
        (<code>--profile webchat</code>: CPU + RAM, <code>--profile webchat-gpu</code>: NVIDIA GPU);
        <code>docker compose logs llama-server</code> (or <code>llama-server-gpu</code>) says what it is doing.</p>`;
    }
    html += `<p class="cfgNote">Paste a Hugging Face link (a repo, or a specific .gguf file) or <code>user/repo:quant</code>.
      It downloads in the background (with its MTP draft head when the repo has one) and loads when a chat uses it.
      Files you put in <code>./models</code> are listed too.</p>
      <div class="cfgRow"><input id="hfInput" class="cfgInput" style="flex:1;min-width:320px"
        placeholder="https://huggingface.co/unsloth/Qwen3.5-9B-GGUF  or  unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL">
        <button id="hfAdd" class="cfgBtn">Download</button></div>
      <div class="cfgErr" id="hfErr"></div>`;
    // llama.cpp lists a cached quant without unsloth's "UD-" prefix (…:UD-Q4_K_XL -> …:Q4_K_XL)
    const norm = id => id.replace(/:UD-/i, ':').toLowerCase();
    const have = new Set(local.map(m => norm(m.id)));
    const sugg = (models.suggested_local || []).filter(s => !have.has(norm(s.model)));
    if (sugg.length) {
      html += `<div class="cfgRow" style="margin-bottom:12px">` + sugg.map(s =>
        `<button class="chip" data-sugg="${h(s.model)}" title="${h(s.note)}">+ ${h(s.model)} <span style="color:var(--muted)">· ${h(s.size)} · ${h(s.note)}</span></button>`).join('') + `</div>`;
    }
    if (!local.length) return html + `<p class="cfgNote">No local models yet.</p>`;
    html += `<div class="cfgList">` + local.map(m => {
      const st = m.status;
      const pct = m.progress != null ? Math.round(m.progress * 100) : null;
      const draft = (m.sidecars || []).find(k => ['mtp', 'dflash', 'eagle3', 'dspark'].includes(k));
      let meta = st === 'downloading' ? `downloading ${pct ?? 0}% · ${fmtGB(m.done)} / ${fmtGB(m.total)}`
        : st === 'loading' ? `loading${pct != null ? ` ${pct}%` : '…'}`
        : st === 'loaded' ? `loaded · context ${m.context_size?.toLocaleString?.() ?? '?'} tokens`
        : st === 'incomplete' ? 'download paused or interrupted: Resume continues it'
        : st === 'failed' ? `failed to load (exit ${h(m.exit_code)}): see docker compose logs llama-server(-gpu)`
        : 'not loaded: loads when a chat uses it';
      meta += m.source === 'file' ? ' · from ./models' : '';
      const acts = [];
      if (st === 'incomplete') acts.push(`<button class="cfgBtn ghost" data-act="resume" data-m="${h(m.id)}">Resume</button>`);
      if (['unloaded', 'failed', 'sleeping'].includes(st)) acts.push(`<button class="cfgBtn ghost" data-act="load" data-m="${h(m.id)}">Load</button>`);
      if (['loaded', 'loading', 'downloading'].includes(st)) acts.push(`<button class="cfgBtn ghost" data-act="unload" data-m="${h(m.id)}">${st === 'downloading' ? 'Pause' : 'Unload'}</button>`);
      acts.push(`<button class="cfgBtn ghost" data-act="settings" data-m="${h(m.id)}">Settings</button>`);
      if (m.removable) acts.push(`<button class="cfgBtn danger" data-act="delete" data-m="${h(m.id)}">Delete</button>`);
      return `<div class="cfgItem"><div class="name">${h(m.id)}<span class="badge ${h(st)}">${h(st)}</span>${draft ? `<span class="badge mtp" title="speculative decoding with the model's ${h(draft)} draft head">${h(draft.toUpperCase())}</span>` : ''}</div>
        <div class="acts">${acts.join('')}</div><div class="meta">${meta}</div>
        ${['downloading', 'loading'].includes(st) && pct != null ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ''}</div>`;
    }).join('') + `</div><div class="cfgErr" id="modErr"></div>`;
    return html;
  }

  // ── Knob forms (inference + chat) ────────────────────────────────────────
  function field(k, saved, placeholder, prefix) {
    const id = `${prefix}_${k.key}`;
    const v = saved ?? '';
    let input;
    if (k.type === 'choice' || k.type === 'bool') {
      const choices = k.type === 'bool' ? ['true', 'false'] : k.choices;
      input = `<select id="${id}" class="cfgSelect" data-key="${k.key}"><option value="">${h(placeholder)}</option>` +
        choices.map(c => `<option value="${h(c)}"${String(v) === String(c) ? ' selected' : ''}>${h(c === 'true' ? 'on' : c === 'false' ? 'off' : c)}</option>`).join('') + `</select>`;
    } else {
      input = `<input id="${id}" class="cfgInput" data-key="${k.key}" data-keep="no" type="number" step="any" value="${h(v)}" placeholder="${h(placeholder)}">`;
    }
    return `<div class="cfgPair"><label for="${id}">${h(k.label)}</label><div><div class="cfgField">${input}</div>${k.help ? `<div class="help">${h(k.help)}</div>` : ''}</div></div>`;
  }
  const show = (v, k) => v === null || v === undefined || v === '' ? (k.key === 'ctx' ? 'auto (fit to GPU)' : k.type === 'int' || k.type === 'float' ? 'auto' : 'default')
    : k.type === 'bool' ? (v ? 'on' : 'off') : String(v);

  function renderInference() {
    if (!config) return '<p class="cfgNote">Loading…</p>';
    const inf = config.inference;
    const ids = Object.keys(inf.models);
    if (infScope && !inf.models[infScope]) infScope = '';
    const m = infScope ? inf.models[infScope] : null;
    let html = `<h3>Local model inference (llama.cpp)</h3>
      <p class="cfgNote">Applied when a model loads: saving unloads a running model whose settings changed, and its
      next use reloads it with them (no restart). Per-model values override the defaults for all models.</p>
      <div class="cfgRow" style="margin-bottom:14px"><span>Settings for</span>
        <select id="infScope" class="cfgSelect"><option value="">All models (defaults)</option>` +
        ids.map(id => `<option value="${h(id)}"${id === infScope ? ' selected' : ''}>${h(id)}</option>`).join('') +
      `</select>${m && m.draft ? `<span class="badge mtp">${h(m.draft.toUpperCase())} draft head</span>` : ''}</div><div class="cfgGrid">`;
    for (const k of inf.knobs) {
      const saved = m ? m.saved[k.key] : inf.global[k.key];
      const inherited = m ? inf.effective[k.key] : k.default;
      const ph = m ? `inherit: ${show(inherited, k)}` : `default: ${show(inherited, k)}`;
      if (k.key === 'speculative' && m && !m.draft) {
        html += `<div class="cfgPair"><label>${h(k.label)}</label><div class="help" style="padding-top:6px">This model has no draft head (MTP/DFlash/EAGLE3): nothing to enable. Repos with an <code>-MTP</code> variant ship one.</div></div>`;
        continue;
      }
      html += field(k, saved, ph, 'inf');
    }
    html += `</div><div class="cfgRow" style="margin-top:16px"><button id="infSave" class="cfgBtn">Save</button>
      <button id="infReset" class="cfgBtn ghost">${m ? 'Reset this model to the defaults' : 'Reset all defaults'}</button></div>
      <div class="cfgErr" id="infErr"></div>`;
    return html;
  }

  function renderChat() {
    if (!config) return '<p class="cfgNote">Loading…</p>';
    const c = config.chat;
    let html = `<h3>Chat</h3><p class="cfgNote">Apply from the next message. Sampling and thinking apply to local
      models; API models keep their providers' defaults.</p><div class="cfgGrid">`;
    for (const k of c.knobs) html += field(k, c.saved[k.key], `default: ${show(k.default, k)}`, 'chat');
    html += `</div><div class="cfgRow" style="margin-top:16px"><button id="chatSave" class="cfgBtn">Save</button>
      <button id="chatReset" class="cfgBtn ghost">Reset to defaults</button></div><div class="cfgErr" id="chatErr"></div>`;
    return html;
  }

  // ── API providers ────────────────────────────────────────────────────────
  function renderProviders() {
    const prov = models?.providers || {};
    const eps = models?.endpoints || [];
    let html = `<div class="cfgCols"><section>` + renderOpenRouter(prov) + `</section><section>
      <h3>OpenAI-compatible APIs</h3>
      <p class="cfgNote">Any server speaking the OpenAI chat API with tool calling: vLLM, LM Studio, Ollama
      (<code>http://host.docker.internal:11434/v1</code>), a hosted provider… Keys stay on the chat server.
      Its models appear in the model picker under the endpoint's name.</p>`;
    if (eps.length) {
      html += `<div class="cfgList" style="margin-bottom:14px">` + eps.map(e => `<div class="cfgItem">
        <div class="name">${h(e.name)}${e.from_env ? '<span class="badge">.env</span>' : ''}</div>
        <div class="acts">${e.from_env ? '' : `<button class="cfgBtn danger" data-ep="${h(e.id)}">Remove</button>`}</div>
        <div class="meta">${h(e.base_url)} · key ${e.api_key ? h(e.api_key) : 'none'} · ${e.available.length} model(s): ${h(e.available.slice(0, 6).join(', '))}${e.available.length > 6 ? '…' : ''}</div></div>`).join('') + `</div>`;
    }
    html += `<div class="cfgForm">
      <label for="epName">Name</label><div><input id="epName" class="cfgInput" placeholder="e.g. LM Studio"></div>
      <label for="epUrl">Base URL</label><div><input id="epUrl" class="cfgInput" placeholder="http://host.docker.internal:1234/v1">
        <div class="help">The URL ending in /v1 (the chat appends /chat/completions and /models). From the containers, your host is host.docker.internal.</div></div>
      <label for="epKey">API key</label><div><input id="epKey" class="cfgInput" type="password" autocomplete="off" placeholder="optional"></div>
      <label for="epModels">Models</label><div><input id="epModels" class="cfgInput" placeholder="optional, comma-separated">
        <div class="help">Empty = every model the API's /models lists.</div></div>
      <label for="epCtx">Context size</label><div><input id="epCtx" class="cfgInput" type="number" placeholder="131072" style="max-width:160px">
        <div class="help">Used for the context meter and tool-result budgets.</div></div>
      </div><div class="cfgRow" style="margin-top:12px"><button id="epAdd" class="cfgBtn">Add endpoint</button></div>
      <div class="cfgErr" id="epErr"></div></section></div>`;
    return html;
  }

  function renderOpenRouter(prov) {
    let html = `<h3>OpenRouter</h3>`;
    if (!orInfo) return html + '<p class="cfgNote">Loading…</p>';
    if (orInfo.restricted) return html + '<p class="cfgNote">Disabled by CHAT_PROVIDER.</p>';
    const k = orInfo.key;
    html += `<p class="cfgNote">${orInfo.enabled ? '<span class="cfgOk">Active.</span>' : 'Not configured yet.'}
      Hundreds of hosted models with one key (<a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>).
      Prompts and tool results go to the model's provider and are billed to the key.</p>
      <div class="cfgForm"><label for="orKey">API key</label><div><div class="cfgField">
        <input id="orKey" class="cfgInput" type="password" autocomplete="off" style="width:min(100%, 360px)"
          placeholder="${k.source ? `set (${k.source === 'env' ? 'from .env' : 'saved here'} ${h(k.hint)}): paste to replace` : 'sk-or-…'}">
        <button id="orKeySave" class="cfgBtn">Save key</button>
        ${k.source === 'ui' ? '<button id="orKeyDel" class="cfgBtn ghost">Remove</button>' : ''}</div>
        <div class="help">Kept on the chat server, never sent back to the browser.</div></div></div>
      <div class="cfgErr" id="orErr"></div>`;
    if (!orInfo.enabled) return html;
    const u = orUsage || {};
    if (u.today != null || u.total != null) {
      const usd = v => v == null ? '—' : `$${Number(v).toFixed(v < 0.01 ? 4 : 2)}`;
      html += `<p class="cfgNote">Spend on this key: today <b>${usd(u.today)}</b> · this week <b>${usd(u.week)}</b> · this month <b>${usd(u.month)}</b> ·
        total <b>${usd(u.total)}</b>${u.remaining != null ? ` · remaining budget <b>${usd(u.remaining)}</b>` : ''}.
        The $ meter in the header shows the current chat.</p>`;
    }
    // settings
    const st = orInfo.settings;
    html += `<div class="cfgGrid" style="margin-top:10px; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr))">`;
    for (const kn of st.knobs) html += field(kn, st.saved[kn.key], `default: ${show(kn.default, kn)}`, 'or');
    html += `</div><div class="cfgRow" style="margin-top:10px"><button id="orSave" class="cfgBtn">Save settings</button></div>`;
    // models
    const price = m => m.prompt_per_m == null ? 'price unknown'
      : `$${m.prompt_per_m} in / $${m.completion_per_m} out per 1M`;
    // What a request pays once routed (pinned endpoint, price cap, cheapest
    // provider), vs. the catalogue's list price.
    const paid = m => {
      const e = m.effective || {};
      if (e.blocked) {
        const caps = [e.cap_in != null ? `$${e.cap_in} in` : null, e.cap_out != null ? `$${e.cap_out} out` : null].filter(Boolean).join(' / ');
        return `<span style="color:var(--red)">no provider within your price caps (${h(caps)} per 1M): requests would fail. ` +
          `Raise the max input/output price in the OpenRouter settings above (0 = no cap).</span> · list ${price(m)}`;
      }
      if (e.prompt_per_m == null) return price(m);
      const pays = e.max_prompt_per_m != null
        ? `$${e.prompt_per_m}–${e.max_prompt_per_m} in / $${e.completion_per_m}–${e.max_completion_per_m} out per 1M (${e.providers} providers, load-balanced)`
        : `$${e.prompt_per_m} in / $${e.completion_per_m} out per 1M via ${h(e.via)}${e.pinned ? ' (pinned)' : e.providers > 1 ? `, cheapest of ${e.providers}` : ''}`;
      const differs = e.prompt_per_m !== m.prompt_per_m || e.completion_per_m !== m.completion_per_m;
      return `<b>${pays}</b>${differs ? ` · list ${price(m)}` : ''}`;
    };
    html += `<h3>OpenRouter models in the picker</h3><p class="cfgNote">Always offered: ${orInfo.auto_tiers.map(h).join(', ')}
      (OpenRouter picks a model in that cost band). Add any other tool-capable model below${orInfo.models_from === 'env' ? '; the list currently comes from OPENROUTER_MODEL in .env' : ''}.</p>`;
    html += `<div class="cfgList" style="margin-bottom:10px">` + (orInfo.models.map(m => `<div class="cfgItem">
        <div class="name">${h(m.id)}${m.known ? '' : '<span class="badge failed" title="not in OpenRouter\'s catalogue">unknown</span>'}${m.tools === false ? '<span class="badge failed">no tool calling</span>' : ''}</div>
        <div class="acts"><button class="cfgBtn danger" data-ordel="${h(m.id)}">Remove</button></div>
        <div class="meta">${h(m.name)} · ${paid(m)}${m.context ? ` · ${Number(m.context).toLocaleString()} tokens` : ''}</div></div>`).join('')
      || '<div class="cfgItem"><div class="meta">No fixed models: only the Auto Router tiers.</div></div>') + `</div>`;
    html += `<div class="cfgRow"><input id="orQuery" class="cfgInput" style="flex:1;min-width:300px" value="${h(orSearch.q)}"
        placeholder="Search ${orInfo.catalogue_size || ''} models: name or vendor (e.g. deepseek, qwen, claude)"></div>
      <div class="cfgErr" id="orModErr"></div>`;
    if (orSearch.q) {
      const have = new Set(orInfo.models.map(m => m.id));
      html += `<div class="cfgList">` + (orSearch.results.map(m => `<div class="cfgItem">
          <div class="name">${h(m.id)}</div>
          <div class="acts">${have.has(m.id) ? '<span class="badge loaded">added</span>' : `<button class="cfgBtn ghost" data-oradd="${h(m.id)}">Add</button>`}</div>
          <div class="meta">${h(m.name)} · ${price(m)}${m.context ? ` · ${Number(m.context).toLocaleString()} tokens` : ''}</div></div>`).join('')
        || '<div class="cfgItem"><div class="meta">No tool-capable model matches.</div></div>') + `</div>
        <p class="cfgNote">${orSearch.total > orSearch.results.length ? `${orSearch.total} matches, cheapest first; refine the search to see others.` : ''}</p>`;
    }
    return html;
  }

  let searchTimer = null;
  async function runSearch(q) {
    orSearch.q = q;
    if (!q.trim()) { orSearch = { q: '', results: [], total: 0 }; render(); return; }
    const d = await api('GET', `/api/openrouter/catalogue?q=${encodeURIComponent(q)}&limit=25`).catch(() => ({ models: [], total: 0 }));
    if (orSearch.q !== q) return;  // a newer search is under way
    orSearch.results = d.models; orSearch.total = d.total;
    render();
    const el = document.getElementById('orQuery');
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  async function run(btn, errId, fn) {
    const err = $(errId);
    if (err) { err.textContent = ''; err.className = 'cfgErr'; }
    if (btn) btn.disabled = true;
    try { await fn(); await refresh(); loadModels(); return true; }
    catch (e) { const el = $(errId); if (el) el.textContent = e.message; return false; }
    finally { if (btn) btn.disabled = false; }
  }
  const collect = prefix => Object.fromEntries([...body.querySelectorAll(`[id^="${prefix}_"]`)].map(el => {
    const v = el.value.trim();
    const k = el.dataset.key;
    return [k, v === '' ? null : el.tagName === 'SELECT' ? (v === 'true' ? true : v === 'false' ? false : v) : Number(v)];
  }));

  function wire() {
    const add = async src => run($('hfAdd'), 'hfErr', async () => {
      await api('POST', '/api/local-models', { model: src });
      if ($('hfInput')) $('hfInput').value = '';
    });
    $('hfAdd')?.addEventListener('click', () => { const v = $('hfInput').value.trim(); if (v) add(v); });
    $('hfInput')?.addEventListener('keydown', e => { if (e.key === 'Enter') $('hfAdd').click(); });
    body.querySelectorAll('[data-sugg]').forEach(b => b.addEventListener('click', () => add(b.dataset.sugg)));
    body.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => {
      const { act, m } = b.dataset;
      if (act === 'settings') { infScope = m; tab = 'inference'; render(); return; }
      if (act === 'delete' && !confirmDelete(m)) return;
      const url = act === 'resume' ? '/api/local-models' : `/api/local-models/${act}`;
      run(b, 'modErr', () => api('POST', url, { model: m }));
    }));
    $('infScope')?.addEventListener('change', e => { infScope = e.target.value; render(); });
    $('infSave')?.addEventListener('click', e => run(e.target, 'infErr', () =>
      api('POST', '/api/config', { scope: 'inference', model: infScope || null, values: collect('inf') })));
    $('infReset')?.addEventListener('click', e => run(e.target, 'infErr', () =>
      api('POST', '/api/config', { scope: 'inference', model: infScope || null,
        values: Object.fromEntries(config.inference.knobs.map(k => [k.key, null])) })));
    $('chatSave')?.addEventListener('click', e => run(e.target, 'chatErr', () =>
      api('POST', '/api/config', { scope: 'chat', values: collect('chat') })));
    $('chatReset')?.addEventListener('click', e => run(e.target, 'chatErr', () =>
      api('POST', '/api/config', { scope: 'chat', values: Object.fromEntries(config.chat.knobs.map(k => [k.key, null])) })));
    $('epAdd')?.addEventListener('click', e => run(e.target, 'epErr', async () => {
      await api('POST', '/api/endpoints', {
        name: $('epName').value.trim(), base_url: $('epUrl').value.trim(), api_key: $('epKey').value.trim(),
        models: $('epModels').value.split(',').map(s => s.trim()).filter(Boolean),
        context: Number($('epCtx').value) || 0,
      });
      ['epName', 'epUrl', 'epKey', 'epModels', 'epCtx'].forEach(id => { $(id).value = ''; });
    }));
    const orModels = () => orInfo.models.map(m => m.id);
    $('orKeySave')?.addEventListener('click', e => run(e.target, 'orErr', async () => {
      const key = $('orKey').value.trim();
      if (!key) throw new Error('paste an API key first');
      await api('POST', '/api/openrouter', { api_key: key });
      $('orKey').value = '';
    }));
    $('orKeyDel')?.addEventListener('click', e => run(e.target, 'orErr', () => api('POST', '/api/openrouter', { api_key: '' })));
    $('orSave')?.addEventListener('click', e => run(e.target, 'orErr', () => api('POST', '/api/openrouter', { values: collect('or') })));
    body.querySelectorAll('[data-ordel]').forEach(b => b.addEventListener('click', () => run(b, 'orModErr', () =>
      api('POST', '/api/openrouter', { models: orModels().filter(m => m !== b.dataset.ordel) }))));
    body.querySelectorAll('[data-oradd]').forEach(b => b.addEventListener('click', () => run(b, 'orModErr', () =>
      api('POST', '/api/openrouter', { models: [...orModels(), b.dataset.oradd] }))));
    $('orQuery')?.addEventListener('input', e => {
      clearTimeout(searchTimer);
      const q = e.target.value;
      searchTimer = setTimeout(() => runSearch(q), 250);
    });
    body.querySelectorAll('[data-ep]').forEach(b => b.addEventListener('click', () =>
      run(b, 'epErr', () => api('DELETE', `/api/endpoints/${encodeURIComponent(b.dataset.ep)}`))));
  }

  // A delete re-downloads gigabytes to undo: confirm inline (no browser dialog).
  let pendingDelete = null;
  function confirmDelete(id) {
    if (pendingDelete === id) { pendingDelete = null; return true; }
    pendingDelete = id;
    const err = $('modErr');
    if (err) err.textContent = `Click Delete again to remove ${id} and its downloaded files.`;
    setTimeout(() => { if (pendingDelete === id) pendingDelete = null; }, 5000);
    return false;
  }

  // First visit with nothing to chat with: open straight on the models tab.
  window.addEventListener('models-updated', function first(e) {
    window.removeEventListener('models-updated', first);
    if (!(e.detail.models || []).length) open('models');
  });
})();
