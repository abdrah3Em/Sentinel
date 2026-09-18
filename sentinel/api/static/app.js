/* Sentinel dashboard.  One SSE stream feeds a small in-memory store; views render from it.
   The overview is built from the process descriptor the API sends in the snapshot, so the
   same shell serves the distribution feeder and the pipeline pump station. */
'use strict';

const $ = (id) => document.getElementById(id);
const RANK = { NORMAL: -1, LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3 };
const CONF_CLASS = { HIGH: 'ok', REDUCED: 'warn', LOW: 'crit' };
const SEV_COLOR = { LOW: 'var(--muted)', MEDIUM: 'var(--text)', HIGH: 'var(--orange)', CRITICAL: 'var(--orange)' };
const CV = { text: 'var(--text)', green: 'var(--green)', orange: 'var(--orange)', muted: 'var(--muted)' };

const S = {
  state: {}, status: {}, alerts: [], events: [], assessments: [], trend: [], scenarios: [], rules: [], thresholds: {},
  selected: null, pinned: false, view: 'overview', sev: 'all', tlFilter: 'all', tlSearch: '', running: {},
};
let D = null;                       // process descriptor

/* ================================================================ utilities */
const pad = (n, w = 2) => String(n).padStart(w, '0');
const fmtTime = (ms, withMs = false) => {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` + (withMs ? `.${pad(d.getMilliseconds(), 3)}` : '');
};
const fmtFull = (ms) => new Date(ms).toLocaleString(undefined, { hour12: false });
const num = (v, d = 1) => (v === undefined || v === null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d));
const signed = (v) => (v === undefined || v === null ? '—' : (v > 0 ? '+' : '') + Math.round(v));
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const title = (s) => (s ? s[0] + s.slice(1).toLowerCase() : '');
const badge = (level, text) => `<span class="badge ${level}">${esc(text ?? title(level))}</span>`;
// "{key:1}" -> fixed decimals, "{key:+d}" -> signed integer, "{key}" -> raw
const fmtTpl = (tpl, obj) => tpl.replace(/\{(\w+)(?::([^}]+))?\}/g, (_, k, spec) => {
  const v = obj[k];
  if (v === undefined || v === null) return '—';
  if (spec === '+d') return signed(v);
  if (spec !== undefined && /^\d+$/.test(spec)) return num(v, Number(spec));
  return String(v);
});

// Operator token: taken from ?token=… once, kept per browser, sent on every write.
let TOKEN = '';
let DIRECTOR = false;
try { const d = new URLSearchParams(location.search).get('director'); if (d !== null) localStorage.setItem('sentinel-director', d === '1' ? '1' : '0'); DIRECTOR = localStorage.getItem('sentinel-director') === '1'; } catch (e) {}
try { const q = new URLSearchParams(location.search).get('token'); if (q) localStorage.setItem('sentinel-token', q); TOKEN = localStorage.getItem('sentinel-token') || ''; } catch (e) {}
async function post(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(TOKEN ? { 'X-Sentinel-Token': TOKEN } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
  if (res.status === 401) data.error = 'Operator token required — open the console with ?token=…';
  return data;
}
function toast(msg) {
  const el = document.createElement('div'); el.textContent = msg; $('toast').appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/* ================================================================ stream */
function connect() {
  const es = new EventSource('/api/stream');
  es.onopen = () => setStream(true);
  es.onerror = () => setStream(false);
  es.onmessage = (msg) => {
    const { type, data } = JSON.parse(msg.data);
    switch (type) {
      case 'snapshot':
        Object.assign(S, { state: data.state || {}, status: data.status || {}, alerts: data.alerts || [],
          events: data.events || [], assessments: data.assessments || [], trend: data.trend || [],
          scenarios: data.scenarios || [], rules: data.rules || [], thresholds: data.thresholds || {} });
        if (data.descriptor) applyDescriptor(data.descriptor);
        $('tag-lock').hidden = !data.token_required;
        if (!S.selected && S.alerts.length) S.selected = S.alerts[0];
        renderScenarios(); renderRules(); renderAll();
        requestAnimationFrame(() => document.body.classList.add('ready'));
        break;
      case 'telemetry':
        S.state = data;
        S.trend.push({ ts: data.ts, ...Object.fromEntries((D ? D.trend.series : []).map((s) => [s.key, data[s.key]])) });
        if (S.trend.length > 300) S.trend.shift();
        renderProcess(); if (S.view === 'overview') { renderChart(); renderSparklines(); }
        break;
      case 'status': S.status = data; renderStatus(); if (S.view === 'rules') renderBaseline(); break;
      case 'alert': {
        S.alerts.unshift(data);
        const cur = S.selected, stale = !cur || Date.now() - cur.ts > 60000;
        if (!S.pinned && (stale || RANK[data.level] >= RANK[cur.level])) S.selected = data;
        renderAdvisory(); renderTables(); renderStatus();
        break;
      }
      case 'event': S.events.unshift(data); if (S.events.length > 600) S.events.pop(); renderTables(); break;
      case 'assessment': S.assessments.unshift(data); renderTables(); break;
      case 'reset':
        Object.assign(S, { alerts: [], events: [], assessments: [], selected: null, pinned: false });
        renderAll(); toast('Demo reset — plant, guard and timeline back to nominal');
        break;
    }
  };
}
function setStream(ok) {
  ['dot-stream', 'dot-live'].forEach((id) => ($(id).className = 'dot ' + (ok ? 'ok pulse' : 'bad')));
  $('txt-live').textContent = ok ? 'Live' : 'Disconnected';
  $('txt-stream').textContent = ok ? 'Stream connected' : 'Stream down · retrying';
  $('tag-live').className = 'tag' + (ok ? '' : ' bad');
}

/* ================================================================ descriptor → DOM */
function applyDescriptor(d) {
  D = d;
  document.title = `Sentinel — ${d.brand}`;
  $('brand-sub').textContent = d.brand;
  $('nav-consoles').innerHTML = (d.consoles || []).map((c) => {
    const href = c.active ? '#overview' : `${location.protocol}//${location.hostname}:${c.port}/`;
    return `<a href="${href}" class="${c.active ? 'here' : ''}" title="port ${c.port}"><svg><use href="#i-${c.id === 'grid' ? 'breaker' : 'pump'}"/></svg>${esc(c.label)}<span class="dot ${c.active ? 'ok' : ''}"></span></a>`;
  }).join('');
  $('foot-process').textContent = d.short;
  $('process-sub').textContent = d.process_sub;
  const tiles = $('tiles'); tiles.style.setProperty('--cols-desktop', d.kpis.length);
  tiles.innerHTML = d.kpis.map((k) => `<div class="tile" id="kpi-${k.key}" style="--spark:${CV[k.spark] || CV.green}">
    <div class="tile-top"><span class="eyebrow">${esc(k.label)}</span><span class="badge plain" id="kpi-${k.key}-badge">—</span></div>
    <div class="tile-val"><b id="v-${k.key}">—</b><span>${esc(k.unit)}</span></div>
    <svg class="spark" id="sp-${k.key}" viewBox="0 0 100 40" preserveAspectRatio="none"></svg>
    <div class="tile-foot" id="kpi-${k.key}-foot">${esc(fmtTpl(k.foot, {}))}</div></div>`).join('');
  const eq = $('equipment'); eq.style.setProperty('--cols-desktop', d.equipment.length);
  eq.innerHTML = d.equipment.map((e) => `<div><div class="eq-icon" id="eq-${e.key}-i"><svg><use href="#i-${esc(e.icon)}"/></svg></div>
    <div><div class="eq-name">${esc(e.label)}</div><div class="eq-state" id="eq-${e.key}">—</div></div></div>`).join('');
  $('legend').innerHTML = d.trend.series.map((s) => `<span class="${s.dash ? 'dash' : ''}" style="--c:${CV[s.color]}">${esc(s.label)}</span>`).join('');
  document.querySelectorAll('.mimic').forEach((m) => m.toggleAttribute('hidden', m.id !== 'mimic-' + d.id));
  const c = d.console;
  $('ops-buttons').innerHTML = c.buttons.map((b) => `<button class="btn" data-cmd="${esc(b.action)}">${esc(b.label)}</button>`).join('');
  const control = (i, idAttr) => i.type === 'select'
    ? `<select class="select" id="${idAttr}">${(i.options || []).map((o) => `<option value="${esc(o)}" ${o === i.default ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`
    : `<input class="input" id="${idAttr}" type="${i.type || 'text'}" value="${esc(i.default ?? '')}" ${i.min !== undefined ? `min="${i.min}"` : ''} ${i.max !== undefined ? `max="${i.max}"` : ''} ${i.step !== undefined ? `step="${i.step}"` : ''}>`;
  $('ops-inputs').innerHTML = c.inputs.map((i) => `<label><span class="eyebrow">${esc(i.label)}</span>${control(i, 'in-' + i.action)}</label>
      <button class="btn" data-cmd="${esc(i.action)}" data-input="in-${esc(i.action)}">Apply</button>`).join('')
    + `<label class="grow"><span class="eyebrow">Source</span><select class="select" id="source">${c.sources.map((s) => `<option value="${esc(s.value)}">${esc(s.label)}</option>`).join('')}</select></label>`;
  const sim = $('ops-sim'); sim.hidden = !c.sim.length;
  sim.innerHTML = c.sim.length ? `<span class="eyebrow"><i class="dot bad"></i>Simulator only</span>` + c.sim.map((h) => (h.type
    ? `<label><span class="eyebrow">${esc(h.label)}</span>${control(h, 'sim-' + h.hook)}</label><button class="btn ghost" data-sim="${esc(h.hook)}" data-input="sim-${esc(h.hook)}">Apply</button>`
    : `<button class="btn ghost" data-sim="${esc(h.hook)}">${esc(h.label)}</button>`)).join('') : '';
}

/* ================================================================ router */
const PAGES = {
  overview: ['Monitor', 'Overview'], advisories: ['Monitor', 'Advisories'], timeline: ['Monitor', 'Timeline'],
  scenarios: ['Operate', 'Scenarios'], rules: ['Operate', 'Detection rules'], about: ['System', 'About'],
};
function route() {
  const view = (location.hash || '#overview').slice(1);
  S.view = PAGES[view] ? view : 'overview';
  document.querySelectorAll('.view').forEach((el) => (el.hidden = el.id !== 'view-' + S.view));
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === S.view));
  const [eyebrow, heading] = PAGES[S.view];
  $('page-eyebrow').textContent = eyebrow; $('page-title').textContent = heading;
  $('page-dot').className = 'dot ' + (eyebrow === 'Monitor' ? '' : 'ok');
  window.scrollTo({ top: 0 });
  renderAll();
  if (S.view === 'rules') renderBaseline();
  if (S.view === 'about') renderAbout();
}
window.addEventListener('hashchange', route);

/* ================================================================ render: shared */
function renderAll() {
  renderStatus(); renderProcess();
  if (S.view === 'overview') { renderChart(); renderSparklines(); }
  renderAdvisory(); renderTables();
}

function renderStatus() {
  const st = S.status || {}, level = st.level || 'NORMAL';
  const score = st.risk_score || 0;
  $('posture').className = 'card posture ' + level;
  $('p-dot').className = 'dot ' + (RANK[level] >= 2 ? 'bad' : level === 'NORMAL' ? 'ok' : 'warn');
  const b = $('p-badge'); b.className = 'badge lg ' + level;
  b.textContent = level === 'NORMAL' ? 'Normal' : `${title(level)} risk`;
  $('p-headline').textContent = st.headline || 'No unsafe command detected';
  $('p-sub').textContent = st.alert_id ? 'Worst advisory in the last 60 s · no automatic action taken' : 'Advisory only · the engineer decides';
  $('p-score').textContent = score;
  $('p-scale').style.setProperty('--x', score + '%');
  $('p-ctx').textContent = st.context || '—';
  const tel = $('p-tel');
  tel.textContent = st.telemetry_trusted === false ? 'Untrusted' : st.telemetry_fresh ? 'Trusted' : '—';
  tel.className = st.telemetry_trusted === false ? 'bad' : 'ok';
  $('p-cmds').textContent = st.commands_seen ?? 0;
  $('p-alerts').textContent = st.alerts_total ?? S.alerts.length;

  const age = st.telemetry_age_s;
  $('tel-age').textContent = age === undefined || age === null ? '—' : num(age, 1) + ' s';
  $('tel-seq').textContent = (st.telemetry_seq ?? '—') + (st.telemetry_repeats >= 3 ? ` ×${st.telemetry_repeats}` : '');
  const telState = st.telemetry_fresh === false ? 'bad' : st.telemetry_trusted === false ? 'warn' : 'ok';
  $('tag-telemetry').className = 'tag tel' + (telState === 'ok' ? '' : ' ' + telState);
  $('dot-telemetry').className = 'dot ' + telState;
  $('txt-telemetry').textContent = st.telemetry_fresh === undefined ? 'Telemetry —' : st.telemetry_fresh === false ? 'Telemetry stale'
    : st.telemetry_trusted === false ? 'Telemetry replay suspected' : 'Telemetry healthy · 2 Hz';

  const n = $('nav-alerts'); n.textContent = S.alerts.length;
  n.className = 'count' + (S.alerts.some((a) => RANK[a.level] >= 2 && Date.now() - a.ts < 60000) ? ' alert' : '');
}

/* ================================================================ render: process (descriptor + domain renderer) */
function renderProcess() {
  const t = S.state; if (!D || !t || t.seq === undefined) return;
  const dom = DOMAINS[D.id]; if (!dom) return;
  const x = { ...t, ...dom.derive(t) };
  for (const k of D.kpis) {
    $('v-' + k.key).textContent = k.signed ? signed(t[k.key]) : num(t[k.key], k.dp);
    $('kpi-' + k.key + '-foot').textContent = fmtTpl(k.foot, x);
    const [cls, text, alarm] = dom.kpi(k.key, t, x);
    setBadge('kpi-' + k.key + '-badge', [cls, text]);
    $('kpi-' + k.key).classList.toggle('alarm', !!alarm);
  }
  for (const e of D.equipment) { const [text, cls] = dom.equip(e.key, t, x); setEquip(e.key, text, cls); }
  dom.mimic(t, x);
  const [ncls, ntext] = dom.note(t, x);
  const note = $('mimic-note'); note.className = 'mimic-note ' + ncls; note.textContent = ntext;
}
function setBadge(id, [cls, text]) { const el = $(id); el.className = 'badge ' + cls; el.textContent = text; }
function setEquip(key, text, cls) { $('eq-' + key).textContent = text; $('eq-' + key + '-i').className = 'eq-icon ' + cls; }

/* ---------------------------------------------------------------- domain: pipeline pump station */
const TANK = { top: 41, height: 198, levelMin: 20, levelMax: 90, pressureMax: 5.0, pressureWarn: 4.0 };
function expectedFlow(t) {
  if (!t.pump || !t.outlet_valve) return 0;
  const suction = t.tank_level >= 8 ? 1 : Math.pow(Math.max(0, t.tank_level / 8), 1.5);
  return 95 * (0.82 + 0.18 * t.tank_level / 100) * suction * (1 + 0.12 * Math.max(0, Math.min(100, t.dra_rate || 0)) / 100);
}
function setLine(lineId, labelId, value, text) {
  if (value === undefined || value === null) return;
  const y = TANK.top + TANK.height * (1 - value / 100);
  $(lineId).setAttribute('y1', y); $(lineId).setAttribute('y2', y);
  $(labelId).setAttribute('y', y + 3); $(labelId).textContent = text;
}
const tankDomain = {
  derive: (t) => ({ expected_flow: expectedFlow(t), ullage: 100 - (t.tank_level || 0) }),
  kpi(key, t) {
    const deadhead = t.pump && !t.outlet_valve, level = t.tank_level, p = t.pressure;
    switch (key) {
      case 'tank_level': return level > TANK.levelMax ? ['crit', 'High', true] : level < TANK.levelMin ? ['crit', 'Low', true] : ['ok', 'Nominal', false];
      case 'pressure': return p > TANK.pressureMax ? ['crit', 'Over limit', true] : p > TANK.pressureWarn ? ['warn', 'Elevated', false] : ['ok', 'Nominal', false];
      case 'flow': return t.pump && t.outlet_valve && t.flow < 5 ? ['crit', 'No flow', true] : deadhead ? ['crit', 'Dead-headed', true] : ['ok', t.pump ? 'Nominal' : 'Idle', false];
      case 'setpoint': return t.mode === 'MAINTENANCE' ? ['warn', 'Maintenance', false] : ['plain', title(t.mode || '—'), false];
    }
    return ['plain', '—', false];
  },
  equip(key, t) {
    const deadhead = t.pump && !t.outlet_valve;
    switch (key) {
      case 'pump': return [t.pump ? 'Running' : 'Stopped', deadhead || t.dry_run_s > 1 ? 'bad' : t.pump ? 'on' : 'off'];
      case 'outlet': return [t.outlet_valve ? 'Open' : 'Closed', t.outlet_valve ? 'on' : deadhead ? 'bad' : 'off'];
      case 'inlet': return [t.inlet_valve ? 'Open' : 'Closed', t.inlet_valve ? 'on' : 'off'];
      case 'mode': return [title(t.mode || ''), t.mode === 'MAINTENANCE' ? 'warn' : 'on'];
    }
    return ['—', 'off'];
  },
  mimic(t) {
    const { tank_level: level, pressure, flow } = t, deadhead = t.pump && !t.outlet_valve;
    const y = TANK.top + TANK.height * (1 - level / 100);
    $('tank-fill').setAttribute('y', y); $('tank-fill').setAttribute('height', Math.max(0, TANK.top + TANK.height - y));
    $('tank-surface').setAttribute('y', y);
    $('tank-read').textContent = num(level, 1) + '%';
    $('tank-read').setAttribute('y', Math.min(TANK.top + TANK.height - 14, Math.max(TANK.top + 28, y + 30)));
    setLine('line-hi', 'lbl-hi', TANK.levelMax, 'HI 90'); setLine('line-lo', 'lbl-lo', TANK.levelMin, 'LO 20');
    setLine('line-sp', 'lbl-sp', t.setpoint, 'SP ' + num(t.setpoint, 0));
    const flowing = flow > 5, inflow = t.inlet_valve && level < 99.5;
    $('pipe-inlet').classList.toggle('active', !!inflow);
    $('pipe-suction').classList.toggle('active', flowing); $('pipe-discharge').classList.toggle('active', flowing);
    $('arrow-out').classList.toggle('active', flowing);
    const speed = flowing ? Math.max(0.3, 1.6 - flow / 90) : 1;
    ['pipe-inlet', 'pipe-suction', 'pipe-discharge'].forEach((id) => ($(id).style.animationDuration = speed + 's'));
    $('valve-inlet').classList.toggle('closed', !t.inlet_valve);
    $('valve-outlet').classList.toggle('closed', !t.outlet_valve);
    $('deadhead-zone').classList.toggle('show', deadhead);
    $('pump').setAttribute('class', 'pump' + (t.pump ? ' on' : '') + (deadhead || t.dry_run_s > 1 ? ' fault' : ''));
    $('pt-value').textContent = num(pressure, 1); $('ft-value').textContent = num(flow, 0);
    $('gauge-pt').setAttribute('class', 'gauge' + (pressure > TANK.pressureMax ? ' alarm' : pressure > TANK.pressureWarn ? ' warn' : ''));
    $('gauge-ft').setAttribute('class', 'gauge' + (t.pump && t.outlet_valve && flow < 5 ? ' alarm' : ''));
  },
  note(t) {
    const deadhead = t.pump && !t.outlet_valve, flowing = t.flow > 5;
    if (deadhead) return ['bad', `Surge · P-101 dead-headed ${num(t.deadhead_s, 0)} s · ${num(t.flow, 0)} m³/h · ${num(t.pressure, 2)} bar rising`];
    if (t.dry_run_s > 1) return ['bad', `Cavitation · tank farm ${num(t.tank_level, 0)} % below suction minimum`];
    if (t.pump && flowing) return ['', `Nominal · ${num(t.flow, 0)} m³/h · ${num(t.pressure, 2)} bar`];
    return ['', t.pump ? 'P-101 running · no flow yet' : 'P-101 stopped · segment static'];
  },
};

/* ---------------------------------------------------------------- domain: distribution feeder */
const GRID = { vmin: 10.34, vmax: 11.66, vwarnLo: 10.5, vwarnHi: 11.5, rating: 400, warn: 360 };
let lastCloseOntoFault = null;
const BRANCHES = { S1: ['bb1', 'b1', 'cb'], S2: ['b1', 'b2', 'sw'], S3: ['b2', 'b3', null], TIE: ['b3', 'b4', 'tie'], S4: ['bb2', 'b4', 'cb2'], S5: ['b4', 'b5', null] };
function reach(root, sw) {
  const seen = new Set([root]); const q = [root];
  while (q.length) { const n = q.shift(); for (const [name, [a, b, s]] of Object.entries(BRANCHES)) { if (s && !sw[s]) continue; const o = a === n ? b : b === n ? a : null; if (o && !seen.has(o)) { seen.add(o); q.push(o); } } }
  return seen;
}
function topo(cb, sw, tie, cb2 = true) {
  const S = { cb, sw, tie, cb2 }, r1 = reach('bb1', S), r2 = reach('bb2', S), out = { t1: {}, f2: {} };
  for (const [name, [a, b, s]] of Object.entries(BRANCHES)) { if (name === 'TIE') continue; const live = !s || S[s]; out.t1[name] = live && r1.has(a) && r1.has(b); out.f2[name] = live && r2.has(a) && r2.has(b); out[name] = out.t1[name] || out.f2[name]; }
  out.parallel = cb && cb2 && [...r1].some((n) => n !== 'bb1' && n !== 'bb2' && r2.has(n));
  return out;
}
const vBand = (v) => (v > GRID.vmax || v < GRID.vmin ? 'out' : v > GRID.vwarnHi || v < GRID.vwarnLo ? 'near' : 'ok');
const gridDomain = {
  derive: (t) => ({}),
  kpi(key, t) {
    switch (key) {
      case 'v_bus_kv': { const b = vBand(t.v_bus_kv); return b === 'out' ? ['crit', 'Out of band', true] : b === 'near' ? ['warn', 'Near limit', false] : ['ok', 'In band', false]; }
      case 'v_b3_kv': { if (!t.supplied?.b3) return ['crit', 'Dead', true]; const b = vBand(t.v_b3_kv); return b === 'out' ? ['crit', 'Out of band', true] : b === 'near' ? ['warn', 'Near limit', false] : ['ok', 'Supplied', false]; }
      case 'i_feeder_a': return t.fault_current_ka ? ['crit', 'Fault current', true] : t.i_feeder_a > GRID.rating ? ['crit', 'Overload', true]
        : t.i_feeder_a > GRID.warn ? ['warn', 'Near rating', false] : !t.cb_closed ? ['plain', 'Breaker open', false] : ['ok', 'Nominal', false];
      case 'tap': return t.avc_mode === 'MANUAL' ? ['warn', 'Manual', false] : t.avc_override_s > 0 ? ['warn', 'Override', false] : ['plain', 'AVC auto', false];
    }
    return ['plain', '—', false];
  },
  equip(key, t) {
    const par = topo(t.cb_closed, t.sw_closed, t.tie_closed, t.cb2_closed).parallel;
    switch (key) {
      case 'cb': return t.protection_tripped ? [t.cb_closed ? 'Closed · latch set' : 'Tripped', 'bad'] : [t.cb_closed ? 'Closed' : 'Open', t.cb_closed ? 'on' : 'off'];
      case 'cb2': return t.protection2_tripped ? [t.cb2_closed ? 'Closed · latch set' : 'Tripped', 'bad'] : [t.cb2_closed ? 'Closed' : 'Open', t.cb2_closed ? 'on' : 'off'];
      case 'sw': return [t.sw_closed ? 'Closed' : 'Open', t.sw_closed ? 'on' : 'off'];
      case 'tie': return t.tie_closed ? [par ? 'Closed · parallel' : 'Closed · transfer', par ? 'warn' : 'on'] : ['Open · normal', 'off'];
      case 'pv': return t.supplied?.b2 ? [`${num(t.pv_kw / 1000, 2)} MW${t.pv_curtail_pct ? ` · ${num(t.pv_curtail_pct, 0)} % curtailed` : ''}`, 'on'] : ['Tripped · bus dead', 'bad'];
      case 'sp': { const ptw = (t.permits && t.permits.length) ? t.permits.join(',') : t.permit_to_work; return t.switching_program ? [t.switching_program + (t.sp_covers?.length && t.sp_covers.length < 9 ? ` · ${t.sp_covers.length} items` : '') + (ptw ? ` · PTW ${ptw}` : ''), 'on']
        : ptw ? [`PTW ${ptw} · no program`, 'warn'] : ['None', 'off']; }
    }
    return ['—', 'off'];
  },
  mimic(t) {
    const cb2 = t.cb2_closed !== false, tp = topo(t.cb_closed, t.sw_closed, t.tie_closed, cb2);
    const live = (id, on) => { const el = $(id); if (el) el.classList.toggle('dead', !on); };
    live('g-l0', t.cb_closed); live('g-s1', tp.S1); live('g-l1', tp.S1); live('g-s2', tp.S2); live('g-s3', tp.S3); live('g-l3', tp.S3);
    live('g-l4', tp.S4 || tp.S3); live('g-s4', tp.S4); live('g-l5', cb2); live('g-s5', tp.S5);
    const flows = { 'g-f1': tp.S1, 'g-f2': tp.S2, 'g-f3': tp.S3, 'g-f4': tp.S4, 'g-f5': tp.S5, 'g-ft': t.tie_closed && (tp.S3 || tp.S4) && !tp.parallel };
    for (const [id, on] of Object.entries(flows)) $(id).classList.toggle('active', !!on);
    const sw = (id, closed, tripped) => ($(id).setAttribute('class', 'sw ' + (tripped ? 'tripped' : closed ? 'closed' : 'open')));
    sw('g-cb', t.cb_closed, t.protection_tripped && !t.cb_closed); sw('g-sw', t.sw_closed, false); sw('g-tie', t.tie_closed, false); sw('g-cb2', cb2, t.protection2_tripped && !cb2);
    const faults = t.fault_sections || (t.fault_section ? [t.fault_section] : []);
    for (const s of ['S1', 'S2', 'S3', 'S4', 'S5']) { $('g-bolt-' + s).classList.toggle('show', faults.includes(s)); $('g-fi-' + s).classList.toggle('set', faults.includes(s)); }
    // flow direction: dashes run away from the source that feeds each section
    const fromF2 = { S1: !tp.t1.S1 && tp.f2.S1, S2: !tp.t1.S2 && tp.f2.S2, S3: !tp.t1.S3 && tp.f2.S3, S4: !tp.f2.S4 && tp.t1.S4, S5: !tp.f2.S5 && tp.t1.S5 };
    [['g-f1', 'S1'], ['g-f2', 'S2'], ['g-f3', 'S3'], ['g-f4', 'S4'], ['g-f5', 'S5']].forEach(([id, s]) => $(id).classList.toggle('rev', !!fromF2[s]));
    $('g-ft').classList.toggle('rev', !!(tp.t1.S3 && !tp.f2.S4));
    const node = (id, on, v, vid, lid, kw) => {
      const el = $(id); el.classList.toggle('dead', !on); el.classList.toggle('out', on && vBand(v) === 'out');
      $(vid).textContent = on ? num(v, 2) + ' kV' : 'DEAD'; $(lid).textContent = on ? `${num(kw / 1000, 2)} MW` : '0 MW';
    };
    node('g-b1', t.supplied?.b1, t.v_b1_kv, 'g-vb1', 'g-lb1', t.load_b1_kw);
    node('g-b2', t.supplied?.b2, t.v_b2_kv, 'g-vb2', 'g-lb2', t.load_b2_kw);
    node('g-b3', t.supplied?.b3, t.v_b3_kv, 'g-vb3', 'g-lb3', t.load_b3_kw);
    node('g-b4', t.supplied?.b4, t.v_b4_kv, 'g-vb4', 'g-lb4', t.load_b4_kw);
    node('g-b5', t.supplied?.b5, t.v_b5_kv, 'g-vb5', 'g-lb5', t.load_b5_kw);
    $('g-vbus').textContent = num(t.v_bus_kv, 2) + ' kV'; $('g-vbus2').textContent = num(t.v_bus2_kv, 2) + ' kV';
    $('g-tap').textContent = `TAP ${signed(t.tap)}`;
    $('g-pv').classList.toggle('off', !t.supplied?.b2 || t.pv_kw <= 0);
    $('g-pvkw').textContent = t.supplied?.b2 ? `PV ${num(t.pv_kw / 1000, 2)} MW${t.pv_curtail_pct ? ` · ${num(t.pv_curtail_pct, 0)} % curt.` : ''}` : 'PV tripped';
    const stats = $('g-stats');
    stats.textContent = `Customers off ${t.customers_off ?? 0} · CML ${num(t.cml, 1)} · close-onto-fault ${t.close_onto_fault_count ?? 0} · stress ${num(t.switchgear_stress, 0)} % · F2 ${num(t.i_f2_a, 0)} A${tp.parallel ? ` · parallel ${num(t.parallel_s, 0)} s · ${num(t.circulating_a, 0)} A circulating` : ''}`;
    stats.setAttribute('class', 'stat left' + (t.customers_off > 0 || t.fault_current_ka ? ' bad' : ''));
    const permits = (t.permits && t.permits.length) ? t.permits.join(', ') : (t.permit_to_work || '—');
    $('g-ctx').textContent = `AVC ${t.avc_mode} ${num(t.avc_target_kv, 2)} kV · program ${t.switching_program ? t.switching_program + ' (' + (t.sp_covers || []).join(', ') + ')' : '—'} · permits ${permits} · protection ${t.protection_tripped || t.protection2_tripped ? 'TRIPPED' : 'reset'}`;
    if (lastCloseOntoFault !== null && t.close_onto_fault_count > lastCloseOntoFault) {
      const f = $(t.protection2_tripped && !t.protection_tripped ? 'g-flash2' : 'g-flash'); f.classList.remove('show'); void f.getBoundingClientRect(); f.classList.add('show');
    }
    lastCloseOntoFault = t.close_onto_fault_count ?? 0;
  },
  note(t) {
    const tp = topo(t.cb_closed, t.sw_closed, t.tie_closed, t.cb2_closed !== false);
    const faults = (t.fault_sections && t.fault_sections.length) ? t.fault_sections.join(', ') : t.fault_section;
    if (t.fault_current_ka) return ['bad', `Closed onto fault ${faults} · ${num(t.fault_current_ka, 1)} kA · re-tripping`];
    if (t.fault_present && (t.protection_tripped || t.protection2_tripped)) return ['bad', `Tripped · fault on ${faults} not cleared · ${t.customers_off} customers off`];
    if (t.customers_off > 0) return ['bad', `${['b1', 'b2', 'b3', 'b4', 'b5'].filter((b) => !t.supplied?.[b]).map((b) => b.toUpperCase()).join(', ')} dead · ${t.customers_off} customers off · CML ${num(t.cml, 1)}`];
    if (vBand(t.v_bus_kv) === 'out') return ['bad', `Busbar ${num(t.v_bus_kv, 2)} kV outside ${GRID.vmin}–${GRID.vmax} kV · tap ${signed(t.tap)}`];
    if (tp.parallel) return ['', `Paralleled with F2 · ${num(t.parallel_s, 0)} s · ${num(t.circulating_a, 0)} A circulating`];
    if (t.fault_present) return ['', `Fault indicator set on ${faults} · latch ${t.protection_tripped ? 'set' : 'reset'}`];
    return ['', `Nominal · ${num(t.i_feeder_a, 0)} A · ${num(t.v_bus_kv, 2)} kV · tap ${signed(t.tap)} · PV ${num(t.pv_kw / 1000, 1)} MW`];
  },
};
const DOMAINS = { grid: gridDomain, pipeline: tankDomain };

/* ================================================================ render: charts */
function renderSparklines() {
  if (!D) return;
  const data = S.trend.slice(-80);
  for (const k of D.kpis) {
    const svg = $('sp-' + k.key); if (!svg) continue;
    if (data.length < 2) { svg.innerHTML = ''; continue; }
    const span = Math.max(1e-9, k.max - k.min);
    const pts = data.map((d, i) => [(i / (data.length - 1)) * 100, 39 - (Math.min(k.max, Math.max(k.min, d[k.key] ?? k.min)) - k.min) / span * 34]);
    const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
    svg.innerHTML = `<path class="area" d="${line} L100 40 L0 40 Z"/><path d="${line}"/>`;
  }
}

let chartGeom = null;
function renderChart() {
  if (!D) return;
  const host = $('chart-host'), svg = $('chart');
  const data = S.trend.slice(-240); if (data.length < 2) { svg.innerHTML = ''; return; }
  const W = host.clientWidth || 480, H = 260, L = 38, R = 38, T = 12, B = 26;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const t0 = data[0].ts, t1 = data[data.length - 1].ts, span = Math.max(1, t1 - t0);
  const x = (ts) => L + ((ts - t0) / span) * (W - L - R);
  const ax = D.trend;
  const yFor = (a) => (v) => T + (1 - (Math.max(a.min, Math.min(a.max, v)) - a.min) / (a.max - a.min)) * (H - T - B);
  const yL = yFor(ax.left), yR = yFor(ax.right), yA = ax.aux ? yFor(ax.aux) : yL;
  const yOf = (axis) => (axis === 'right' ? yR : axis === 'aux' ? yA : yL);
  chartGeom = { data, x, W };

  let grid = '', axis = '';
  for (const v of ax.left.ticks) { grid += `<line x1="${L}" x2="${W - R}" y1="${yL(v)}" y2="${yL(v)}"/>`; axis += `<text x="${L - 8}" y="${yL(v) + 3}" text-anchor="end">${v}</text>`; }
  for (const v of ax.right.ticks) axis += `<text x="${W - R + 8}" y="${yR(v) + 3}">${v}</text>`;
  const ticks = W < 520 ? 2 : 4;
  for (let i = 0; i <= ticks; i++) { const ts = t0 + (span * i) / ticks; axis += `<text x="${x(ts)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === ticks ? 'end' : 'middle'}">${fmtTime(ts)}</text>`; }
  const path = (key, y, step) => data.map((d, i) => (i && step ? `H${x(d.ts).toFixed(1)} V${y(d[key] ?? 0).toFixed(1)}` : `${i ? 'L' : 'M'}${x(d.ts).toFixed(1)} ${y(d[key] ?? 0).toFixed(1)}`)).join(' ');
  const limits = ax.limits.map((l) => { const y = (l.axis === 'right' ? yR : yL)(l.value); return `<line class="limit ${l.danger ? 'pressure' : ''}" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/>`; }).join('');
  const series = ax.series.map((s) => `<path class="series ${s.axis === 'aux' ? 'aux' : ''}" stroke="${CV[s.color]}" ${s.dash ? 'stroke-dasharray="3 4"' : ''} d="${path(s.key, yOf(s.axis), s.step)}"/>`).join('');
  svg.innerHTML = `<g class="grid">${grid}</g><g class="axis">${axis}</g>${limits}${series}
    <line class="crosshair" id="crosshair" y1="${T}" y2="${H - B}" x1="-10" x2="-10"/>`;
}
$('chart-host').addEventListener('mousemove', (ev) => {
  if (!chartGeom || !D) return;
  const { data, x, W } = chartGeom, host = $('chart-host'), rect = host.getBoundingClientRect();
  const px = ((ev.clientX - rect.left) / host.clientWidth) * W;
  let best = 0, bd = Infinity;
  data.forEach((d, i) => { const dd = Math.abs(x(d.ts) - px); if (dd < bd) { bd = dd; best = i; } });
  const d = data[best], cx = x(d.ts);
  const ch = $('crosshair'); ch.setAttribute('x1', cx); ch.setAttribute('x2', cx);
  const tip = $('chart-tip'); tip.hidden = false;
  tip.innerHTML = `<div class="t">${fmtTime(d.ts, true)}</div>` + D.trend.series.map((s) =>
    `<div><span><i style="background:${CV[s.color]}"></i>${esc(s.label)}</span><b>${s.axis === 'aux' ? signed(d[s.key]) : num(d[s.key], s.axis === 'right' ? 0 : 2)}</b></div>`).join('');
  const left = (cx / W) * host.clientWidth;
  tip.style.left = Math.max(0, Math.min(host.clientWidth - 180, left + 12)) + 'px'; tip.style.top = '8px';
});
$('chart-host').addEventListener('mouseleave', () => { $('chart-tip').hidden = true; const ch = $('crosshair'); if (ch) { ch.setAttribute('x1', -10); ch.setAttribute('x2', -10); } });
window.addEventListener('resize', () => { if (S.view === 'overview') renderChart(); });

/* ================================================================ render: advisory detail */
function stateRows(s) {
  if (!D) return [];
  return D.state_rows.map(([label, key, f]) => {
    const v = s[key];
    let out = '—';
    if (f.kind === 'num') out = num(v, f.dp) + (f.unit || '');
    else if (f.kind === 'bool') out = v ? f.on : f.off;
    else if (f.kind === 'tap') out = `${signed(s.tap)} / ${num(s.avc_target_kv, 2)} kV`;
    else if (f.kind === 'list') out = Array.isArray(v) && v.length ? v.join(', ') : (f.empty || '—');
    else out = v === null || v === undefined || v === '' ? (f.empty || '—') : (f.title ? title(String(v)) : String(v));
    return [label, out];
  });
}
// First sentence (or two) of a paragraph, whole sentences only.
function brief(text, max = 1) {
  const parts = String(text || '').split(/(?<=\.)\s+/);
  return parts.slice(0, max).join(' ');
}
function advisoryBrief(a) {
  if (!a) return `<div class="adv-empty"><b>No unsafe command detected</b></div>`;
  const c = a.command, findings = (a.findings || []).filter((f) => f.weight > 0).sort((x, y) => y.weight - x.weight);
  const shown = findings.slice(0, 3), more = findings.length - shown.length, max = Math.max(1, ...findings.map((f) => f.weight));
  return `<div class="adv brief">
    <div class="adv-head">${badge(a.level)}<span class="badge plain">${esc(a.rule)}</span><span class="t">${fmtTime(a.ts, true)}</span></div>
    <h2>${esc(a.summary)}</h2>
    <div class="kv row">
      <div><span class="eyebrow">Command</span><span class="v mono">${c ? esc(c.action + (c.value != null ? ` = ${c.value}` : '')) : 'process condition'}</span></div>
      <div><span class="eyebrow">Source</span><span class="v mono">${c ? esc(c.source) : '—'}</span></div>
      <div><span class="eyebrow">Equipment</span><span class="v">${esc(a.equipment || '—')}</span></div>
    </div>
    <div class="contribs">${shown.map((f) => `<div class="contrib" style="--w:${(f.weight / max) * 100}%"><span class="w">+${f.weight}</span><span class="r">${esc(f.rule)}</span><span class="d">${esc(f.detail)}</span></div>`).join('')}
      ${more > 0 ? `<a class="more" href="#advisories">+${more} more</a>` : ''}</div>
    <div class="callout"><span class="eyebrow"><i class="dot green"></i>Do this</span><p>${esc(brief(a.recommendation))}</p></div>
    <div class="adv-foot"><span>${badge(CONF_CLASS[a.confidence] || 'ok', 'Confidence ' + title(a.confidence || 'HIGH'))}</span><span>Risk <b>${a.score}</b> / 100</span></div>
  </div>`;
}
function advisoryHtml(a) {
  if (!a) return `<div class="adv-empty"><b>No unsafe command detected</b></div>`;
  const c = a.command, s = a.state || {}, integ = s.integrity || {};
  const findings = a.findings || [], max = Math.max(1, ...findings.map((f) => Math.abs(f.weight)));
  const rows = stateRows(s);
  return `<div class="adv">
    <div class="adv-head">${badge(a.level)}<span class="badge plain">${esc(a.rule)}</span>${a.context ? `<span class="badge plain">${esc(title(a.context))}</span>` : ''}<span class="t" title="${esc(fmtFull(a.ts))}">${fmtTime(a.ts, true)}</span></div>
    <h2>${esc(a.summary)}</h2>
    <div class="adv-grid">
      <div class="kv">
        <div><span class="eyebrow">Command</span><span class="v mono">${c ? esc(c.action + (c.value != null ? ` = ${c.value}` : '')) : 'process condition · no command'}</span></div>
        <div><span class="eyebrow">Source</span><span class="v mono">${c ? esc(c.source) : '—'}</span></div>
        <div><span class="eyebrow">Equipment affected</span><span class="v">${esc(a.equipment || '—')}</span></div>
        ${a.suppressed_score ? `<div><span class="eyebrow">Without context</span><span class="v">Would have scored ${a.suppressed_score}/100</span></div>` : ''}
      </div>
      <div class="adv-section"><span class="eyebrow">Process state at evaluation</span>
        <table class="state-table">${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</table></div>
    </div>
    <div class="adv-section"><span class="eyebrow">Why this matters</span><p class="why">${esc(a.why)}</p></div>
    <div class="adv-section"><span class="eyebrow">Risk contributions</span>
      <div class="contribs">${findings.map((f) => `<div class="contrib ${f.weight < 0 ? 'neg' : ''}" style="--w:${(Math.abs(f.weight) / max) * 100}%">
        <span class="w">${f.weight > 0 ? '+' : ''}${f.weight}</span><span class="r">${esc(f.rule)}</span><span class="d">${esc(f.detail)}</span></div>`).join('')}</div></div>
    <div class="callout"><span class="eyebrow"><i class="dot green"></i>Do this</span><p>${esc(a.recommendation)}</p></div>
    <div class="confidence ${a.confidence || 'HIGH'}"><span class="eyebrow">${badge(CONF_CLASS[a.confidence] || 'ok', 'Confidence ' + title(a.confidence || 'HIGH'))}</span>
      ${a.confidence && a.confidence !== 'HIGH' ? `<p>${esc(a.uncertainty)}</p>` : ''}</div>
    <div class="adv-foot"><span>Advisory only</span><span>Risk <b>${a.score}</b> / 100</span></div>
  </div>`;
}
function renderAdvisory() {
  const a = S.selected;
  $('adv-overview').innerHTML = advisoryBrief(a);
  $('adv-ov-sub').textContent = a ? `${a.rule} · ${fmtTime(a.ts)}` : '';
  if (S.view === 'advisories') $('adv-detail').innerHTML = advisoryHtml(a);
}
function selectAlert(a) { S.selected = a; S.pinned = true; renderAdvisory(); renderTables(); }

/* ================================================================ render: tables */
function timelineRows() {
  const byCmd = new Map(S.assessments.map((as) => [as.command?.id, as]));
  const rows = [];
  for (const a of S.alerts) rows.push({ ts: a.ts, kind: 'alert', type: badge(a.level), text: `${a.summary} · ${a.rule} · risk ${a.score}`, source: a.command?.source || 'guard', alert: a, q: `${a.summary} ${a.rule} ${a.level}` });
  for (const e of S.events) {
    const p = e.payload || {}, d = p.detail || '';
    if (e.type === 'SCENARIO') { if (DIRECTOR) rows.push({ ts: e.ts, kind: 'demo', type: badge('demo', p.phase === 'START' || p.phase === 'END' ? 'Scenario' : 'Narration'), text: d, source: e.source, q: d }); }
    else if (e.type === 'COMMAND_ACCEPTED') { const as = byCmd.get(p.command_id);
      rows.push({ ts: e.ts, kind: 'cmd', type: badge('accent', 'Command'), text: d, source: e.source, verdict: as ? (as.verdict === 'NORMAL' ? badge('ok', 'Consistent') : badge(as.verdict, `${title(as.verdict)} · ${as.score}`)) : '', q: d }); }
    else if (e.type === 'MODBUS') rows.push({ ts: e.ts, kind: 'modbus', type: badge(p.write ? 'warn' : 'plain', p.write ? 'Modbus write' : 'Modbus read'), text: d, source: e.source, q: d });
    else if (e.type === 'PHYSICAL' || e.type === 'PROCESS' || e.type === 'PROTECTION') rows.push({ ts: e.ts, kind: 'physical', type: badge(p.severity === 'HIGH' ? 'crit' : p.severity === 'MEDIUM' ? 'warn' : 'plain', title(e.type)), text: d, source: e.source, q: d });
    else rows.push({ ts: e.ts, kind: 'other', type: badge('plain', title(e.type.replace(/_/g, ' '))), text: d || JSON.stringify(p), source: e.source, q: d });
  }
  return rows.sort((a, b) => b.ts - a.ts);
}
function renderDirector() {
  const panel = $('director'); panel.hidden = !DIRECTOR;
  $('director-toggle').classList.toggle('on', DIRECTOR);
  $('chip-demo').hidden = !DIRECTOR;
  if (!DIRECTOR) return;
  const notes = S.events.filter((e) => e.type === 'SCENARIO').slice(0, 14);
  $('director-sub').textContent = notes.length ? `${esc(notes[0].payload?.title || '')}` : 'no scenario running';
  $('director-log').innerHTML = notes.length ? notes.map((e) => { const p = e.payload || {}; return `<li><span class="t">${fmtTime(e.ts)}</span>${badge('demo', p.phase === 'START' || p.phase === 'END' || p.phase === 'ABORT' ? 'Scenario' : p.phase === 'STEP' ? 'Step' : 'Say')}<span>${esc(p.detail || '')}</span></li>`; }).join('') : '<li class="muted">Run a scenario; its script appears here.</li>';
}
function renderTables() {
  renderDirector();
  const rows = timelineRows();
  if (S.view === 'overview') {
    const tb = $('tbl-recent').querySelector('tbody');
    const keep = rows.slice(0, 9);
    tb.innerHTML = keep.length ? keep.map((r) => `<tr class="${r.alert ? 'click' : ''}"><td class="t">${fmtTime(r.ts)}</td><td>${r.type}</td><td class="msg">${esc(r.text)}</td></tr>`).join('')
      : `<tr><td class="empty">No activity yet</td></tr>`;
    [...tb.children].forEach((tr, i) => { if (keep[i]?.alert) tr.onclick = () => selectAlert(keep[i].alert); });
  }
  if (S.view === 'timeline') {
    const q = S.tlSearch.toLowerCase();
    const keep = rows.filter((r) => (S.tlFilter === 'all' || r.kind === S.tlFilter) && (!q || (r.q + ' ' + r.source).toLowerCase().includes(q))).slice(0, 400);
    $('tl-count').textContent = `${keep.length} events`;
    const tb = $('tbl-timeline').querySelector('tbody');
    tb.innerHTML = keep.length ? keep.map((r) => `<tr class="${r.alert ? 'click' : ''}"><td class="t" title="${esc(fmtFull(r.ts))}">${fmtTime(r.ts, true)}</td><td>${r.type}</td><td class="msg">${esc(r.text)}</td><td class="src">${esc(r.source)}</td><td>${r.verdict || ''}</td></tr>`).join('')
      : `<tr><td colspan="5" class="empty">Nothing matches</td></tr>`;
    [...tb.children].forEach((tr, i) => { if (keep[i]?.alert) tr.onclick = () => { selectAlert(keep[i].alert); location.hash = '#advisories'; }; });
  }
  if (S.view === 'advisories') {
    const keep = S.alerts.filter((a) => S.sev === 'all' || a.level === S.sev);
    $('adv-count').textContent = `${keep.length} of ${S.alerts.length}`;
    const tb = $('tbl-alerts').querySelector('tbody');
    tb.innerHTML = keep.length ? keep.map((a) => `<tr class="click ${S.selected && S.selected.id === a.id ? 'selected' : ''}">
      <td class="t" title="${esc(fmtFull(a.ts))}">${fmtTime(a.ts)}</td><td>${badge(a.level)}</td><td class="mono">${esc(a.rule)}</td><td class="msg">${esc(a.summary)}</td>
      <td><span class="scorebar" style="--w:${a.score}%;--sc:${SEV_COLOR[a.level]}"><i></i><b>${a.score}</b></span></td>
      <td class="src">${esc(a.command?.source || '—')}</td><td>${esc(title(a.context || ''))}</td></tr>`).join('')
      : `<tr><td colspan="7" class="empty">No advisories${S.sev !== 'all' ? ' at this severity' : ' — the plant is behaving'}</td></tr>`;
    [...tb.children].forEach((tr, i) => { if (keep[i]) tr.onclick = () => selectAlert(keep[i]); });
    $('adv-detail').innerHTML = advisoryHtml(S.selected);
  }
}

/* ================================================================ render: scenarios & rules */
function renderScenarios() {
  $('scenario-grid').innerHTML = S.scenarios.map((s) => `<div class="card scenario" data-id="${esc(s.id)}">
    <div class="sc-head">${badge(s.kind === 'attack' ? 'crit' : 'ok', s.kind === 'attack' ? 'Attack' : 'Legitimate')}</div>
    <h4>${esc(s.title)}</h4>
    <p>${esc(s.narrative)}</p>
    <div class="sc-foot"><span class="muted">${esc(s.expect)} · ~${s.duration_hint} s</span><button class="btn" data-run="${s.id}"><svg><use href="#i-play"/></svg>Run</button></div>
    <div class="progress"><i></i></div></div>`).join('');
}
async function runScenario(id) {
  const card = document.querySelector(`.scenario[data-id="${id}"]`), bar = card.querySelector('.progress i');
  const res = await post('/api/scenario/' + id);
  if (!res.ok) { toast(res.error || 'Could not start scenario'); return; }
  const ms = (res.duration_hint || 10) * 1000;
  document.querySelectorAll('[data-run]').forEach((b) => (b.disabled = true));
  bar.style.transition = 'none'; bar.style.width = '0'; requestAnimationFrame(() => { bar.style.transition = `width ${ms}ms linear`; bar.style.width = '100%'; });
  toast(`Running: ${res.title}`);
  setTimeout(() => { document.querySelectorAll('[data-run]').forEach((b) => (b.disabled = false)); bar.style.transition = 'none'; bar.style.width = '0'; }, ms + 1500);
}
function renderBaseline() {
  const b = (S.status || {}).baseline; const tb = $('tbl-baseline').querySelector('tbody');
  if (!b) { tb.innerHTML = '<tr><td colspan="4" class="empty">Waiting for the guard</td></tr>'; return; }
  const rows = [];
  const srcs = Object.entries(b.sources || {});
  if (!srcs.length) rows.push(['Cadence per source', b.configured.cadence, 'nothing learned yet', `0 / ${b.min_samples}`]);
  for (const [src, s] of srcs) rows.push([`Cadence · ${src}`, b.configured.cadence,
    s.median_interval_s == null ? '—' : `median ${s.median_interval_s} s · floor ${s.floor_interval_s} s · EWMA ${s.ewma_interval_s} s${s.ready ? '' : ' (learning)'}`, `${s.samples} / ${b.min_samples}`]);
  const acts = Object.entries(b.actions || {});
  const configuredFor = (a) => a === 'avc_target' ? b.configured.avc_target : a === 'setpoint' ? b.configured.setpoint : '—';
  if (!acts.length) rows.push(['Value ranges', b.configured.range, 'nothing learned yet', `0 / ${b.min_samples}`]);
  for (const [a, v] of acts) rows.push([`Range · ${a}`, configuredFor(a), v.p05 == null ? '—' : `${v.p05} – ${v.p95}${v.ready ? '' : ' (learning)'}`, `${v.samples} / ${b.min_samples}`]);
  for (const [src, s] of srcs) if (Object.keys(s.mix || {}).length) rows.push([`Command mix · ${src}`, b.configured.mix, Object.entries(s.mix).map(([a, f]) => `${a} ${Math.round(f * 100)} %`).join(' · '), `${Object.values(s.mix).length} actions`]);
  tb.innerHTML = rows.map((r) => `<tr><td class="msg">${esc(r[0])}</td><td class="muted">${esc(r[1])}</td><td>${esc(r[2])}</td><td class="mono">${esc(r[3])}</td></tr>`).join('');
}
async function renderAbout() {
  const tile = (k, v) => `<div class="threshold"><span class="eyebrow">${esc(k)}</span><b>${esc(v)}</b></div>`;
  $('about-facts').innerHTML = [
    ['Process', D ? D.title : '—'], ['Transports', 'Modbus TCP (RTU + wire tap) · MQTT adapter'],
    ['Write path to the plant', 'none — advisory only'], ['Command envelopes', 'HMAC per source · sequence · nonce · freshness'],
    ['Guard state', 'SQLite WAL, survives restart'], ['Network at runtime', 'none — self-hosted fonts, CSP self-only'],
  ].map(([k, v]) => tile(k, v)).join('');
  $('about-trust').innerHTML = [
    ['Inside', 'guard process and its state · signing master · rule weights · operator token'],
    ['Outside', 'anything that reaches the broker or the RTU · the telemetry path · every scenario attacker'],
    ['Not defended', 'a valid key on a compromised workstation · a wholly consistent forged world · DoS on broker or RTU'],
  ].map(([k, v]) => `<div class="policy-item"><span class="eyebrow">${k}</span><p>${esc(v)}</p></div>`).join('');
  try {
    const r = await (await fetch('/api/results')).json();
    $('about-version').textContent = r.version || '';
    $('about-results').innerHTML = (r.headline || []).map(([k, v]) => tile(k, v)).join('') || '<div class="empty">Run make metrics</div>';
  } catch (e) { $('about-results').innerHTML = '<div class="empty">Results unavailable</div>'; }
}
function renderRules() {
  renderBaseline();
  const th = S.thresholds || {};
  const pol = th.policy || {}, env = th.envelope || {};
  $('policy').innerHTML = [['When Sentinel is unsure', pol.when_unsure], ['What it never does', pol.never_blocks], ['Who decides', pol.human_decides], ['What it cannot see', pol.limits]]
    .filter(([, v]) => v).map(([k, v]) => `<div class="policy-item"><span class="eyebrow">${k}</span><p>${esc(v)}</p></div>`).join('');
  const bands = (th.severity_bands || []).slice().sort((a, b) => a.min - b.min);
  const envelope = env.voltage_kv
    ? [['Statutory voltage', `${env.voltage_kv[0].toFixed(2)}–${env.voltage_kv[1].toFixed(2)} kV (warn ${env.voltage_warn_kv[0]}–${env.voltage_warn_kv[1]})`],
       ['Feeder rating', `${env.current_a[1].toFixed(0)} A (warn ${env.current_warn_a.toFixed(0)}) · tap ${env.tap[0]}…+${env.tap[1]} · standing parallel > ${env.parallel_s} s`]]
    : env.level_pct
      ? [['Level band', `${env.level_pct[0]}–${env.level_pct[1]} %`], ['Pressure limit', `${env.pressure_bar[1].toFixed(1)} bar (warn ${env.pressure_warn_bar.toFixed(1)})`]]
      : [];
  $('thresholds').innerHTML = [
    ['Severity bands', bands.map((b, i) => `${b.min}–${bands[i + 1] ? bands[i + 1].min - 1 : 100} ${title(b.level)}`).join(' · ')],
    ['Advisory threshold', `score ≥ ${th.alert_min_score}`],
    ...envelope,
    ['Telemetry freshness', th.telemetry ? `stale after ${th.telemetry.stale_s} s · replay at ${th.telemetry.replay_repeat_count} repeats` : '—'],
    ['Rapid sequencing', th.timing ? `${th.timing.rapid_count} in ${th.timing.rapid_window_s} s · ${th.timing.burst_count} in ${th.timing.burst_window_s} s` : '—'],
    ['Trusted sources', (th.trusted_sources || []).join(', ')],
    [D && D.id === 'grid' ? 'Program sources' : 'Maintenance sources', (th.maintenance_sources || []).join(', ')],
  ].map(([k, v]) => `<div class="threshold"><span class="eyebrow">${k}</span><b>${esc(v)}</b></div>`).join('');
  $('rules-count').textContent = `${S.rules.length} rules across 7 detection layers`;
  $('tbl-rules').querySelector('tbody').innerHTML = S.rules.map((r) => `<tr>
    <td class="mono">${esc(r.id)}</td><td>${esc(r.layer)}</td><td class="msg">${esc(r.title)}</td><td class="muted">${esc(r.trigger)}</td>
    <td><div class="weights">${r.weights.map((w) => `<span class="badge ${w.weight < 0 ? 'neg' : 'pos'}" title="${esc(w.label)}">${w.weight > 0 ? '+' : ''}${w.weight} ${esc(w.label)}</span>`).join('')}</div></td></tr>`).join('');
}

/* ================================================================ actions */
function inputValue(id) {
  const el = $(id); if (!el) return undefined;
  if (el.type === 'number') return Number(el.value);
  return el.value;
}
document.addEventListener('click', async (ev) => {
  const run = ev.target.closest('[data-run]'); if (run) return runScenario(run.dataset.run);
  const sim = ev.target.closest('[data-sim]');
  if (sim) {
    const body = { hook: sim.dataset.sim, value: sim.dataset.input ? inputValue(sim.dataset.input) : true };
    const res = await post('/api/sim', body);
    toast(res.ok ? `Simulator: ${body.hook}${body.value !== true ? ' ' + body.value : ''}` : res.error);
    return;
  }
  const cmd = ev.target.closest('[data-cmd]');
  if (cmd) {
    const body = { action: cmd.dataset.cmd, source: $('source').value };
    if (cmd.dataset.input) body.value = inputValue(cmd.dataset.input);
    const res = await post('/api/command', body);
    toast(res.ok ? `Sent ${body.action}${body.value != null ? ' = ' + body.value : ''} from ${body.source}` : res.error);
    return;
  }
  const sev = ev.target.closest('#sev-filters .chip');
  if (sev) { S.sev = sev.dataset.sev; document.querySelectorAll('#sev-filters .chip').forEach((c) => c.classList.toggle('active', c === sev)); renderTables(); return; }
  const tf = ev.target.closest('#tl-filters .chip');
  if (tf) { S.tlFilter = tf.dataset.f; document.querySelectorAll('#tl-filters .chip').forEach((c) => c.classList.toggle('active', c === tf)); renderTables(); return; }
});
$('tl-search').addEventListener('input', (ev) => { S.tlSearch = ev.target.value; renderTables(); });
function setDirector(on) { DIRECTOR = on; try { localStorage.setItem('sentinel-director', on ? '1' : '0'); } catch (e) {} renderTables(); }
$('director-toggle').addEventListener('click', () => setDirector(!DIRECTOR));
$('director-close').addEventListener('click', () => setDirector(false));
$('reset').addEventListener('click', async () => { const r = await post('/api/reset'); if (!r.ok) toast(r.error); });
$('theme').addEventListener('click', () => {
  const light = document.documentElement.dataset.theme !== 'light';
  if (light) document.documentElement.dataset.theme = 'light'; else delete document.documentElement.dataset.theme;
  try { localStorage.setItem('sentinel-theme', light ? 'light' : 'dark'); } catch (e) {}
  setThemeIcon();
});
function setThemeIcon() { $('theme').innerHTML = `<svg><use href="#${document.documentElement.dataset.theme === 'light' ? 'i-moon' : 'i-sun'}"/></svg>`; }

function tick() { $('clock').textContent = fmtTime(Date.now()); }
setInterval(tick, 1000); tick(); setThemeIcon(); route(); connect();
