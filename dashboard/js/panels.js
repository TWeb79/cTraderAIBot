/**
 * Sidebar panels: session/core-datapoints readout, auto-trading controls,
 * and the "model learning" visualization (implementationplan.md §11.3, §11.4).
 *
 * Kept separate from app.js (one concern per file, per RULES_coding.md) —
 * app.js owns the WebSocket loop and calls into these render functions.
 */

import { fetchAuto, fetchRegistryHistory, fetchSessions, fetchStrategies, setAuto } from './api.js';

const C = { long: '#3FBE8E', short: '#E2574C', amber: '#E8A33D', cyan: '#4FD1C5' };

function fmt(v, digits = 2) {
  return v == null ? '—' : Number(v).toFixed(digits);
}

/* ── Session clock + active-session indicator ───────────────────────────── */

let SESSION_WINDOWS = [];

function activeSession(now) {
  const hhmm = now.getUTCHours() * 60 + now.getUTCMinutes();
  for (const w of SESSION_WINDOWS) {
    const [oh, om] = w.open.split(':').map(Number);
    const [ch, cm] = w.close.split(':').map(Number);
    const openMin = oh * 60 + om;
    const closeMin = ch * 60 + cm;
    if (hhmm >= openMin && hhmm < closeMin) return w.label;
  }
  return 'closed (Asia/Frankfurt/NY hours only)';
}

export async function initSessionClock() {
  const el = document.getElementById('session-clock');
  if (!el) return;
  try {
    const { sessions } = await fetchSessions();
    SESSION_WINDOWS = sessions || [];
  } catch (e) {
    console.warn('sessions fetch failed', e);
  }
  const tick = () => {
    const now = new Date();
    const hh = String(now.getUTCHours()).padStart(2, '0');
    const mm = String(now.getUTCMinutes()).padStart(2, '0');
    const ss = String(now.getUTCSeconds()).padStart(2, '0');
    el.textContent = `${hh}:${mm}:${ss} UTC · session: ${activeSession(now)}`;
  };
  tick();
  setInterval(tick, 1000);
}

/* ── Core datapoints (POC/VAH/VAL, pre-NY/NY split, day close, NY open) ─── */

export function renderDatapoints(container, bar) {
  if (!container) return;
  if (!bar) {
    container.innerHTML = '<div class="datapoints__empty">No enriched bar data yet</div>';
    return;
  }
  const rows = [
    ['Session POC / VAH / VAL', `${fmt(bar.poc_prev)} / ${fmt(bar.vah_prev)} / ${fmt(bar.val_prev)}`],
    ['Pre-NY POC / VAH / VAL', `${fmt(bar.poc_pre_ny_prev)} / ${fmt(bar.vah_pre_ny_prev)} / ${fmt(bar.val_pre_ny_prev)}`],
    ['NY POC / VAH / VAL', `${fmt(bar.poc_ny_prev)} / ${fmt(bar.vah_ny_prev)} / ${fmt(bar.val_ny_prev)}`],
    ['Prior day close', fmt(bar.day_close_price_prev)],
    ['Prior NY open', fmt(bar.ny_open_price_prev)],
    ['Regime', bar.regime || 'UNKNOWN'],
  ];
  container.innerHTML = rows.map(([label, value]) => `
    <div class="datapoints__row">
      <span class="datapoints__label">${label}</span>
      <span class="datapoints__value">${value}</span>
    </div>
  `).join('');
}

/* ── Auto-trading controls (strategy select + enable toggle) ────────────── */

let autoState = { enabled: false, strategy: null, use_trained: false };

function updateAutoUi() {
  const toggle = document.getElementById('auto-toggle');
  const hint = document.getElementById('auto-hint');
  const light = document.getElementById('light-auto');
  if (toggle) {
    toggle.textContent = autoState.enabled ? 'Disable auto mode' : 'Enable auto mode';
    toggle.classList.toggle('chart-btn--active', autoState.enabled);
  }
  if (hint) {
    hint.textContent = autoState.enabled
      ? `On — live runner only takes signals '${autoState.strategy || 'balanced'}' enables.`
      : 'Off — live runner takes every risk-approved signal (unchanged default behavior).';
  }
  if (light) {
    light.classList.toggle('light--ok', autoState.enabled);
    light.classList.toggle('light--warn', !autoState.enabled);
    light.innerHTML = `<span class="light__dot"></span>AUTOPILOT ${autoState.enabled ? 'ON' : 'OFF'}`;
  }
}

export async function initAutoControls() {
  const select = document.getElementById('auto-strategy');
  const toggle = document.getElementById('auto-toggle');
  const trainedBox = document.getElementById('auto-use-trained');
  if (!select || !toggle) return;

  try {
    const { strategies, default: def } = await fetchStrategies();
    select.innerHTML = strategies.map(s => `<option value="${s.name}">${s.label}</option>`).join('');
    select.value = def;
    autoState.strategy = def;
  } catch (e) {
    console.warn('strategies fetch failed', e);
  }

  try {
    const { auto } = await fetchAuto();
    if (auto) {
      autoState = { ...autoState, ...auto };
      if (auto.strategy) select.value = auto.strategy;
      if (trainedBox) trainedBox.checked = !!auto.use_trained;
    }
  } catch (e) {
    console.warn('auto state fetch failed', e);
  }
  updateAutoUi();

  const push = async () => {
    try {
      const { auto } = await setAuto({
        enabled: autoState.enabled,
        strategy: select.value,
        use_trained: trainedBox ? trainedBox.checked : false,
      });
      if (auto) autoState = auto;
    } catch (e) {
      console.warn('auto/set failed', e);
    }
    updateAutoUi();
  };

  toggle.addEventListener('click', () => {
    autoState.enabled = !autoState.enabled;
    push();
  });
  select.addEventListener('change', push);
  if (trainedBox) trainedBox.addEventListener('change', push);
}

/* ── Model-learning visualization ────────────────────────────────────────
 * No neural network exists in this codebase by design (see
 * training/registry.py's own docstring and implementationplan.md §11.4) —
 * this visualizes the deterministic parameter-registry's real optimization
 * history and the live confidence score, not a simulated "AI thinking".
 */

export function renderLearningGauge(pred) {
  const valueEl = document.getElementById('learning-gauge-value');
  const gaugeEl = document.getElementById('learning-gauge');
  const dirEl = document.getElementById('learning-direction');
  const noteEl = document.getElementById('learning-note');
  if (!valueEl) return;
  if (!pred) {
    valueEl.textContent = '—';
    if (dirEl) dirEl.textContent = 'FLAT';
    if (noteEl) noteEl.textContent = 'no analysis yet';
    return;
  }
  const pct = Math.round((pred.likelihood || 0.5) * 100);
  valueEl.textContent = `${pct}%`;
  const color = pred.direction === 'LONG' ? C.long : pred.direction === 'SHORT' ? C.short : C.cyan;
  if (gaugeEl) gaugeEl.style.setProperty('--gauge-color', color);
  if (gaugeEl) gaugeEl.style.setProperty('--gauge-pct', `${pct}%`);
  if (dirEl) { dirEl.textContent = pred.direction || 'FLAT'; dirEl.style.color = color; }
  if (noteEl) noteEl.textContent = pred.note || pred.reason || '';
}

export async function refreshLearningSparkline() {
  const svg = document.getElementById('learning-sparkline');
  if (!svg) return;
  let history = [];
  try {
    const data = await fetchRegistryHistory(20);
    history = (data.history || []).slice().reverse(); // oldest -> newest
  } catch (e) {
    console.warn('registry history fetch failed', e);
  }
  if (!history.length) {
    svg.innerHTML = '<text x="4" y="22" font-size="9" fill="var(--text-faint)" font-family="\'JetBrains Mono\', monospace">No optimization runs recorded yet</text>';
    return;
  }
  const scores = history.map(h => (h.metrics && h.metrics.composite_score) ?? h.metrics?.total_return_pct ?? 0);
  const min = Math.min(...scores, 0);
  const max = Math.max(...scores, 0.001);
  const w = 280, h = 40, padY = 4;
  const x = (i) => (i / Math.max(1, scores.length - 1)) * w;
  const y = (v) => h - padY - ((v - min) / (max - min || 1)) * (h - padY * 2);
  const points = scores.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  const lastColor = scores[scores.length - 1] >= scores[0] ? C.long : C.short;
  svg.innerHTML = `
    <polyline points="${points}" fill="none" stroke="${lastColor}" stroke-width="1.5" />
    <circle cx="${x(scores.length - 1)}" cy="${y(scores[scores.length - 1])}" r="2.5" fill="${lastColor}" />
  `;
}
