/* Sentinel dashboard.  One SSE stream feeds a small in-memory store; views render from it. */
'use strict';

const $ = (id) => document.getElementById(id);
const LIMITS = { levelMin: 20, levelMax: 90, pressureMax: 5.0, pressureWarn: 4.0, flowMax: 100 };
const TANK = { top: 41, height: 198 };
const RANK = { NORMAL: -1, LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3 };
const CONF_CLASS = { HIGH: 'ok', REDUCED: 'warn', LOW: 'crit' };
const SEV_COLOR = { LOW: 'var(--info)', MEDIUM: 'var(--warn)', HIGH: 'var(--high)', CRITICAL: 'var(--crit)' };

const S = {
  state: {}, status: {}, alerts: [], events: [], assessments: [], trend: [], scenarios: [], rules: [], thresholds: {},
  selected: null, pinned: false, view: 'overview', sev: 'all', tlFilter: 'all', tlSearch: '', running: {},
};

/* ================================================================ utilities */
const pad = (n, w = 2) => String(n).padStart(w, '0');
const fmtTime = (ms, withMs = false) => {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` + (withMs ? `.${pad(d.getMilliseconds(), 3)}` : '');
};
const fmtFull = (ms) => new Date(ms).toLocaleString(undefined, { hour12: false });
const num = (v, d = 1) => (v === undefined || v === null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d));
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const title = (s) => (s ? s[0] + s.slice(1).toLowerCase() : '');
const badge = (level, text) => `<span class="badge ${level}">${esc(text ?? title(level))}</span>`;

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
        if (!S.selected && S.alerts.length) S.selected = S.alerts[0];
        renderScenarios(); renderRules(); renderAll();
        break;
      case 'telemetry':
        S.state = data;
        S.trend.push({ ts: data.ts, tank_level: data.tank_level, pressure: data.pressure, flow: data.flow, setpoint: data.setpoint });
        if (S.trend.length > 300) S.trend.shift();
        renderProcess(); if (S.view === 'overview') { renderChart(); renderSparklines(); }
        break;
      case 'status': S.status = data; renderStatus(); break;
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
  $('dot-stream').className = 'dot ' + (ok ? 'ok pulse' : 'bad');
  $('txt-stream').textContent = ok ? 'Live stream connected' : 'Stream disconnected — retrying';
  $('pill-live').className = 'pill' + (ok ? '' : ' bad');
  $('pill-live').querySelector('.dot').className = 'dot ' + (ok ? 'ok pulse' : 'bad');
}

/* ================================================================ router */
const TITLES = { overview: 'Overview', advisories: 'Advisories', timeline: 'Timeline', scenarios: 'Scenarios', rules: 'Detection rules' };
function route() {
  const view = (location.hash || '#overview').slice(1);
  S.view = TITLES[view] ? view : 'overview';
  document.querySelectorAll('.view').forEach((el) => (el.hidden = el.id !== 'view-' + S.view));
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === S.view));
  $('page-title').textContent = TITLES[S.view];
  renderAll();
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
  const posture = $('posture'); posture.className = 'card posture ' + level;
  const b = $('p-badge'); b.className = 'badge lg ' + level;
  b.textContent = level === 'NORMAL' ? 'Normal' : `${title(level)} risk`;
  $('p-headline').textContent = st.headline || 'No unsafe command detected';
  $('p-sub').textContent = st.alert_id
    ? `Most severe advisory in the last 60 s. Sentinel advises — the engineer decides.`
    : 'Every consequential command is checked against live process state, command history, operating context and telemetry integrity.';
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
  const pt = $('pill-telemetry');
  pt.className = 'pill' + (st.telemetry_fresh === false ? ' bad' : st.telemetry_trusted === false ? ' warn' : '');
  $('dot-telemetry').className = 'dot ' + (st.telemetry_fresh === false ? 'bad' : st.telemetry_trusted === false ? 'warn' : 'ok');
  $('txt-telemetry').textContent = st.telemetry_fresh === undefined ? 'Telemetry —' : st.telemetry_fresh === false ? 'Telemetry stale' : st.telemetry_trusted === false ? 'Telemetry replay suspected' : 'Telemetry healthy · 2 Hz';

  const n = $('nav-alerts'); n.textContent = S.alerts.length;
  n.className = 'count' + (S.alerts.some((a) => RANK[a.level] >= 2 && Date.now() - a.ts < 60000) ? ' alert' : '');
}

function renderProcess() {
  const t = S.state; if (!t || t.tank_level === undefined) return;
  const { tank_level: level, pressure, flow } = t;
  const deadhead = t.pump && !t.outlet_valve;

  $('v-level').textContent = num(level, 1);
  $('v-pressure').textContent = num(pressure, 2);
  $('v-flow').textContent = num(flow, 0);
  $('v-setpoint').textContent = num(t.setpoint, 0);
  $('kpi-level-foot').textContent = `Setpoint ${num(t.setpoint, 0)} % · band ${LIMITS.levelMin}–${LIMITS.levelMax} %`;
  const expected = expectedFlow(t);
  $('kpi-flow-foot').textContent = `Expected ${num(expected, 0)} L/min from physics`;

  setBadge('kpi-level-badge', level > LIMITS.levelMax ? ['crit', 'High'] : level < LIMITS.levelMin ? ['crit', 'Low'] : ['ok', 'Nominal']);
  setBadge('kpi-pressure-badge', pressure > LIMITS.pressureMax ? ['crit', 'Over limit'] : pressure > LIMITS.pressureWarn ? ['warn', 'Elevated'] : ['ok', 'Nominal']);
  setBadge('kpi-flow-badge', t.pump && t.outlet_valve && flow < 5 ? ['crit', 'No flow'] : deadhead ? ['crit', 'Dead-headed'] : ['ok', t.pump ? 'Nominal' : 'Idle']);
  setBadge('kpi-sp-badge', t.mode === 'MAINTENANCE' ? ['warn', 'Maintenance'] : ['', t.mode || '—']);
  $('kpi-level').classList.toggle('alarm', level > LIMITS.levelMax || level < LIMITS.levelMin);
  $('kpi-pressure').classList.toggle('alarm', pressure > LIMITS.pressureMax);

  setEquip('pump', t.pump ? 'Running' : 'Stopped', deadhead || t.dry_run_s > 1 ? 'bad' : t.pump ? 'on' : 'off');
  setEquip('outlet', t.outlet_valve ? 'Open' : 'Closed', t.outlet_valve ? 'on' : deadhead ? 'bad' : 'off');
  setEquip('inlet', t.inlet_valve ? 'Open' : 'Closed', t.inlet_valve ? 'on' : 'off');
  setEquip('mode', title(t.mode || ''), t.mode === 'MAINTENANCE' ? 'warn' : 'on');

  // mimic
  const y = TANK.top + TANK.height * (1 - level / 100);
  $('tank-fill').setAttribute('y', y); $('tank-fill').setAttribute('height', Math.max(0, TANK.top + TANK.height - y));
  $('tank-surface').setAttribute('y', y);
  $('tank-read').textContent = num(level, 1) + '%';
  $('tank-read').setAttribute('y', Math.min(TANK.top + TANK.height - 14, Math.max(TANK.top + 28, y + 30)));
  setLine('line-hi', 'lbl-hi', LIMITS.levelMax, 'HI 90'); setLine('line-lo', 'lbl-lo', LIMITS.levelMin, 'LO 20');
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
  $('gauge-pt').setAttribute('class', 'gauge' + (pressure > LIMITS.pressureMax ? ' alarm' : pressure > LIMITS.pressureWarn ? ' warn' : ''));
  $('gauge-ft').setAttribute('class', 'gauge' + (t.pump && t.outlet_valve && flow < 5 ? ' alarm' : ''));

  const note = $('mimic-note');
  if (deadhead) { note.className = 'mimic-note bad';
    note.textContent = `Dead-headed for ${num(t.deadhead_s, 0)} s — pump running into a closed outlet. Flow ${num(flow, 0)} L/min, pressure ${num(pressure, 2)} bar and rising toward shut-off head.`; }
  else if (t.dry_run_s > 1) { note.className = 'mimic-note bad'; note.textContent = `Dry running — suction level ${num(level, 0)} % is below the pump minimum. Cavitation risk.`; }
  else if (t.pump && flowing) { note.className = 'mimic-note'; note.textContent = `Nominal transfer: tank ${num(level, 0)} % → pump P-101 → discharge at ${num(flow, 0)} L/min, ${num(pressure, 2)} bar.`; }
  else { note.className = 'mimic-note'; note.textContent = t.pump ? 'Pump running, no flow established yet.' : 'Pump stopped — no transfer in progress.'; }
}
function expectedFlow(t) {
  if (!t.pump || !t.outlet_valve) return 0;
  const suction = t.tank_level >= 8 ? 1 : Math.pow(Math.max(0, t.tank_level / 8), 1.5);
  return 95 * (0.82 + 0.18 * t.tank_level / 100) * suction;
}
function setBadge(id, [cls, text]) { const el = $(id); el.className = 'badge ' + cls; el.textContent = text; }
function setEquip(key, text, cls) { $('eq-' + key).textContent = text; $('eq-' + key + '-i').className = 'eq-icon ' + cls; }
function setLine(lineId, labelId, value, text) {
  if (value === undefined || value === null) return;
  const y = TANK.top + TANK.height * (1 - value / 100);
  $(lineId).setAttribute('y1', y); $(lineId).setAttribute('y2', y);
  $(labelId).setAttribute('y', y + 3); $(labelId).textContent = text;
}

/* ================================================================ render: charts */
function renderSparklines() {
  const data = S.trend.slice(-80);
  const spark = (id, key, max) => {
    const svg = $(id); if (data.length < 2) { svg.innerHTML = ''; return; }
    const pts = data.map((d, i) => [(i / (data.length - 1)) * 100, 29 - (Math.min(max, Math.max(0, d[key] ?? 0)) / max) * 26]);
    const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
    svg.innerHTML = `<path class="area" d="${line} L100 30 L0 30 Z"/><path d="${line}" stroke="currentColor"/>`;
  };
  spark('sp-level', 'tank_level', 100); spark('sp-pressure', 'pressure', 7); spark('sp-flow', 'flow', 100); spark('sp-setpoint', 'setpoint', 100);
}

let chartGeom = null;
function renderChart() {
  const host = $('chart-host'), svg = $('chart');
  const data = S.trend.slice(-240); if (data.length < 2) { svg.innerHTML = ''; return; }
  const W = host.clientWidth || 720, H = 260, L = 38, R = 40, T = 12, B = 26;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const t0 = data[0].ts, t1 = data[data.length - 1].ts, span = Math.max(1, t1 - t0);
  const x = (ts) => L + ((ts - t0) / span) * (W - L - R);
  const yL = (v) => T + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - T - B);
  const yR = (v) => T + (1 - Math.max(0, Math.min(7, v)) / 7) * (H - T - B);
  chartGeom = { data, x, W, L, R };

  let grid = '', axis = '';
  for (const v of [0, 25, 50, 75, 100]) { grid += `<line x1="${L}" x2="${W - R}" y1="${yL(v)}" y2="${yL(v)}"/>`; axis += `<text x="${L - 8}" y="${yL(v) + 4}" text-anchor="end">${v}</text>`; }
  for (const v of [0, 2, 4, 6]) axis += `<text x="${W - R + 8}" y="${yR(v) + 4}">${v}</text>`;
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) { const ts = t0 + (span * i) / ticks; axis += `<text x="${x(ts)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === ticks ? 'end' : 'middle'}">${fmtTime(ts)}</text>`; }
  const path = (key, y) => data.map((d, i) => `${i ? 'L' : 'M'}${x(d.ts).toFixed(1)} ${y(d[key] ?? 0).toFixed(1)}`).join(' ');
  svg.innerHTML = `
    <g class="grid">${grid}</g><g class="axis">${axis}</g>
    <line class="limit" x1="${L}" x2="${W - R}" y1="${yL(LIMITS.levelMax)}" y2="${yL(LIMITS.levelMax)}"/>
    <line class="limit" x1="${L}" x2="${W - R}" y1="${yL(LIMITS.levelMin)}" y2="${yL(LIMITS.levelMin)}"/>
    <line class="limit" style="stroke:var(--crit-bd)" x1="${L}" x2="${W - R}" y1="${yR(LIMITS.pressureMax)}" y2="${yR(LIMITS.pressureMax)}"/>
    <path class="series" stroke="var(--c-setpoint)" stroke-dasharray="4 4" d="${path('setpoint', yL)}"/>
    <path class="series" stroke="var(--c-flow)" d="${path('flow', yL)}"/>
    <path class="series" stroke="var(--c-pressure)" d="${path('pressure', yR)}"/>
    <path class="series" stroke="var(--c-level)" d="${path('tank_level', yL)}"/>
    <line class="crosshair" id="crosshair" y1="${T}" y2="${H - B}" x1="-10" x2="-10"/>`;
}
$('chart-host').addEventListener('mousemove', (ev) => {
  if (!chartGeom) return;
  const { data, x, W, L, R } = chartGeom, host = $('chart-host'), rect = host.getBoundingClientRect();
  const px = ((ev.clientX - rect.left - 18) / (host.clientWidth)) * W;       // 18 = card-body padding
  let best = 0, bd = Infinity;
  data.forEach((d, i) => { const dd = Math.abs(x(d.ts) - px); if (dd < bd) { bd = dd; best = i; } });
  const d = data[best], cx = x(d.ts);
  const ch = $('crosshair'); ch.setAttribute('x1', cx); ch.setAttribute('x2', cx);
  const tip = $('chart-tip'); tip.hidden = false;
  tip.innerHTML = `<div class="t">${fmtTime(d.ts, true)}</div>
    <div><span><i style="background:var(--c-level)"></i>Level</span><b>${num(d.tank_level, 1)} %</b></div>
    <div><span><i style="background:var(--c-flow)"></i>Flow</span><b>${num(d.flow, 0)} L/min</b></div>
    <div><span><i style="background:var(--c-pressure)"></i>Pressure</span><b>${num(d.pressure, 2)} bar</b></div>
    <div><span><i style="background:var(--c-setpoint)"></i>Setpoint</span><b>${num(d.setpoint, 0)} %</b></div>`;
  const left = (cx / W) * host.clientWidth + 18;
  tip.style.left = Math.min(host.clientWidth - 180, left + 12) + 'px'; tip.style.top = '14px';
});
$('chart-host').addEventListener('mouseleave', () => { $('chart-tip').hidden = true; const ch = $('crosshair'); if (ch) { ch.setAttribute('x1', -10); ch.setAttribute('x2', -10); } });
window.addEventListener('resize', () => { if (S.view === 'overview') renderChart(); });

/* ================================================================ render: advisory detail */
function advisoryHtml(a) {
  if (!a) return `<div class="adv-empty"><b>No unsafe command detected</b>Advisories appear here with the command, the state it was judged against, the reasoning and what to verify.</div>`;
  const c = a.command, s = a.state || {}, integ = s.integrity || {};
  const findings = a.findings || [], max = Math.max(1, ...findings.map((f) => Math.abs(f.weight)));
  const rows = [['Tank level', num(s.tank_level, 1) + ' %'], ['Pressure', num(s.pressure, 2) + ' bar'], ['Flow', num(s.flow, 0) + ' L/min'],
    ['Pump', s.pump ? 'Running' : 'Stopped'], ['Outlet valve', s.outlet_valve ? 'Open' : 'Closed'], ['Inlet valve', s.inlet_valve ? 'Open' : 'Closed'],
    ['Mode', title(s.mode || '—')], ['Telemetry age', integ.age_s == null ? '—' : num(integ.age_s, 1) + ' s'], ['Telemetry seq', integ.seq ?? '—']];
  return `<div class="adv">
    <div class="adv-head">${badge(a.level)}<span class="badge mono">${esc(a.rule)}</span>${a.context ? `<span class="badge">${esc(title(a.context))}</span>` : ''}<span class="t" title="${esc(fmtFull(a.ts))}">${fmtTime(a.ts, true)}</span></div>
    <h2>${esc(a.summary)}</h2>
    <div class="adv-grid">
      <div class="kv">
        <div><span class="eyebrow">Command</span><span class="v mono">${c ? esc(c.action + (c.value != null ? ` = ${c.value}` : '')) : 'process condition · no command'}</span></div>
        <div><span class="eyebrow">Source</span><span class="v mono">${c ? esc(c.source) : '—'}</span></div>
        <div><span class="eyebrow">Equipment affected</span><span class="v">${esc(a.equipment || '—')}</span></div>
        ${a.suppressed_score ? `<div><span class="eyebrow">Without context</span><span class="v">Would have scored ${a.suppressed_score}/100</span></div>` : ''}
      </div>
      <div><div class="eyebrow" style="margin-bottom:6px">Process state at evaluation</div>
        <table class="state-table">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('')}</table></div>
    </div>
    <div><div class="eyebrow" style="margin-bottom:4px">Why this matters</div><p class="why">${esc(a.why)}</p></div>
    <div><div class="eyebrow" style="margin-bottom:6px">Risk contributions</div>
      <div class="contribs">${findings.map((f) => `<div class="contrib ${f.weight < 0 ? 'neg' : ''}" style="--w:${(Math.abs(f.weight) / max) * 100}%">
        <span class="w">${f.weight > 0 ? '+' : ''}${f.weight}</span><span class="r">${esc(f.rule)}</span><span class="d">${esc(f.detail)}</span></div>`).join('')}</div></div>
    <div class="callout"><div class="eyebrow">Recommended action — engineer decides</div><p>${esc(a.recommendation)}</p></div>
    <div class="confidence ${a.confidence || 'HIGH'}"><div class="eyebrow">${badge(CONF_CLASS[a.confidence] || 'ok', 'Confidence ' + title(a.confidence || 'HIGH'))}<span>what Sentinel could verify</span></div>
      <p>${esc(a.uncertainty || 'Sentinel advises only; it has not acted on the plant.')}</p></div>
    <div class="adv-foot"><span>No automatic action has been taken on the plant.</span><span>Risk <b>${a.score}</b> / 100</span></div>
  </div>`;
}
function renderAdvisory() {
  const a = S.selected;
  $('adv-overview').innerHTML = advisoryHtml(a);
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
    if (e.type === 'SCENARIO') rows.push({ ts: e.ts, kind: 'demo', type: badge('demo', p.phase === 'START' || p.phase === 'END' ? 'Scenario' : 'Narration'), text: d, source: e.source, q: d });
    else if (e.type === 'COMMAND_ACCEPTED') { const as = byCmd.get(p.command_id);
      rows.push({ ts: e.ts, kind: 'cmd', type: badge('accent', 'Command'), text: d, source: e.source, verdict: as ? (as.verdict === 'NORMAL' ? badge('ok', 'Consistent') : badge(as.verdict, `${title(as.verdict)} · ${as.score}`)) : '', q: d }); }
    else if (e.type === 'PHYSICAL' || e.type === 'PROCESS') rows.push({ ts: e.ts, kind: 'physical', type: badge(p.severity === 'HIGH' ? 'crit' : p.severity === 'MEDIUM' ? 'warn' : '', e.type === 'PHYSICAL' ? 'Physical' : 'Process'), text: d, source: e.source, q: d });
    else rows.push({ ts: e.ts, kind: 'other', type: badge('', title(e.type.replace(/_/g, ' '))), text: d || JSON.stringify(p), source: e.source, q: d });
  }
  return rows.sort((a, b) => b.ts - a.ts);
}
function renderTables() {
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
  $('scenario-grid').innerHTML = S.scenarios.map((s) => `<div class="card scenario" data-id="${s.id}">
    <div class="sc-head">${badge(s.kind === 'attack' ? 'crit' : 'ok', s.kind === 'attack' ? 'Attack' : 'Legitimate')}<h4>${esc(s.title)}</h4></div>
    <p>${esc(s.narrative)}</p>
    <div class="expect"><b>Expected:</b> ${esc(s.expect)}</div>
    <div class="sc-foot"><span class="muted">${s.steps} steps · ~${s.duration_hint}s</span><button class="btn primary sm" data-run="${s.id}"><svg><use href="#i-play"/></svg>Run</button></div>
    <div class="progress"><i></i></div></div>`).join('');
}
async function runScenario(id) {
  const card = document.querySelector(`.scenario[data-id="${id}"]`), btn = card.querySelector('button'), bar = card.querySelector('.progress i');
  const res = await fetch('/api/scenario/' + id, { method: 'POST' }).then((r) => r.json());
  if (!res.ok) { toast(res.error || 'Could not start scenario'); return; }
  const ms = (res.duration_hint || 10) * 1000;
  document.querySelectorAll('[data-run]').forEach((b) => (b.disabled = true));
  bar.style.transition = 'none'; bar.style.width = '0'; requestAnimationFrame(() => { bar.style.transition = `width ${ms}ms linear`; bar.style.width = '100%'; });
  toast(`Running: ${res.title}`);
  setTimeout(() => { document.querySelectorAll('[data-run]').forEach((b) => (b.disabled = false)); bar.style.transition = 'none'; bar.style.width = '0'; }, ms + 1500);
}
function renderRules() {
  const th = S.thresholds || {};
  const pol = th.policy || {};
  $('policy').innerHTML = [['When Sentinel is unsure', pol.when_unsure], ['What it never does', pol.never_blocks], ['Who decides', pol.human_decides]]
    .filter(([, v]) => v).map(([k, v]) => `<div class="policy-item"><div class="eyebrow">${k}</div><p>${esc(v)}</p></div>`).join('');
  const bands = (th.severity_bands || []).slice().sort((a, b) => a.min - b.min);
  $('thresholds').innerHTML = [
    ['Severity bands', bands.map((b, i) => `${b.min}–${bands[i + 1] ? bands[i + 1].min - 1 : 100} ${title(b.level)}`).join(' · ')],
    ['Advisory threshold', `score ≥ ${th.alert_min_score}`],
    ['Level band', th.envelope ? `${th.envelope.level_pct[0]}–${th.envelope.level_pct[1]} %` : '—'],
    ['Pressure limit', th.envelope ? `${th.envelope.pressure_bar[1].toFixed(1)} bar (warn ${th.envelope.pressure_warn_bar.toFixed(1)})` : '—'],
    ['Telemetry freshness', th.telemetry ? `stale after ${th.telemetry.stale_s} s · replay at ${th.telemetry.replay_repeat_count} repeats` : '—'],
    ['Rapid sequencing', th.timing ? `${th.timing.rapid_count} in ${th.timing.rapid_window_s} s · ${th.timing.burst_count} in ${th.timing.burst_window_s} s` : '—'],
    ['Trusted sources', (th.trusted_sources || []).join(', ')],
    ['Maintenance sources', (th.maintenance_sources || []).join(', ')],
  ].map(([k, v]) => `<div class="threshold"><span class="eyebrow">${k}</span><b>${esc(v)}</b></div>`).join('');
  $('rules-count').textContent = `${S.rules.length} rules across 7 detection layers`;
  $('tbl-rules').querySelector('tbody').innerHTML = S.rules.map((r) => `<tr>
    <td class="mono">${esc(r.id)}</td><td>${esc(r.layer)}</td><td class="msg">${esc(r.title)}</td><td class="muted">${esc(r.trigger)}</td>
    <td><div class="weights">${r.weights.map((w) => `<span class="badge ${w.weight < 0 ? 'ok' : 'high'}" title="${esc(w.label)}">${w.weight > 0 ? '+' : ''}${w.weight} ${esc(w.label)}</span>`).join('')}</div></td></tr>`).join('');
}

/* ================================================================ actions */
document.addEventListener('click', async (ev) => {
  const run = ev.target.closest('[data-run]'); if (run) return runScenario(run.dataset.run);
  const cmd = ev.target.closest('[data-cmd]');
  if (cmd) {
    const body = { action: cmd.dataset.cmd, source: $('source').value };
    if (body.action === 'setpoint') body.value = Number($('sp').value);
    const res = await fetch('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => r.json());
    toast(res.ok ? `Sent ${body.action}${body.value != null ? ' = ' + body.value : ''} from ${body.source}` : res.error);
    return;
  }
  const sev = ev.target.closest('#sev-filters .chip');
  if (sev) { S.sev = sev.dataset.sev; document.querySelectorAll('#sev-filters .chip').forEach((c) => c.classList.toggle('active', c === sev)); renderTables(); return; }
  const tf = ev.target.closest('#tl-filters .chip');
  if (tf) { S.tlFilter = tf.dataset.f; document.querySelectorAll('#tl-filters .chip').forEach((c) => c.classList.toggle('active', c === tf)); renderTables(); return; }
});
$('tl-search').addEventListener('input', (ev) => { S.tlSearch = ev.target.value; renderTables(); });
$('reset').addEventListener('click', async () => { await fetch('/api/reset', { method: 'POST' }); });
$('theme').addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : '';
  try { localStorage.setItem('sentinel-theme', dark ? 'dark' : 'light'); } catch (e) {}
  setThemeIcon(); if (S.view === 'overview') { renderChart(); }
});
function setThemeIcon() { $('theme').innerHTML = `<svg><use href="#${document.documentElement.dataset.theme === 'dark' ? 'i-sun' : 'i-moon'}"/></svg>`; }

function tick() { $('clock').textContent = fmtTime(Date.now()); }
setInterval(tick, 1000); tick(); setThemeIcon(); route(); connect();
