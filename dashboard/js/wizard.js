/**
 * First-run onboarding wizard.
 *
 * Surfaces every core feature of the cockpit as a step-by-step tour with a
 * progress indicator. On the first launch it auto-opens; afterwards it can be
 * reopened any time via the header "?" button. Step 2 runs a live
 * "communication debug" that verifies the Dashboard -> API -> cTrader MCP ->
 * WebSocket chain and prints a timestamped diagnostic log.
 *
 * State is persisted in localStorage so the tour only forces itself once.
 */

import {
  fetchHealth,
  fetchRegistry,
  fetchVersion,
  fetchJournal,
  fetchDigest,
  createWebSocket,
} from './api.js';

const STORAGE_KEY = 'ctrader_wizard_done_v1';

/* ── Step definitions ───────────────────────────────────────────────────── */

const STEPS = [
  {
    title: 'Welcome to the Cockpit',
    html: `
      <p class="wizard__lead">This dashboard is the control surface for a
      <strong>100% deterministic</strong> US500 volume-profile / regime trading bot.
      No LLM sits in the live decision path — every entry, stop and target is
      computed in Python from market structure.</p>
      <ul class="wizard__list">
        <li><span class="wizard__bullet">1</span> Live market, account &amp; positions from your cTrader session</li>
        <li><span class="wizard__bullet">2</span> Trade journal + offline strategy digest</li>
        <li><span class="wizard__bullet">3</span> Training toolkit (optimize / simulate / retrain)</li>
        <li><span class="wizard__bullet">4</span> Hard safety controls (demo account, kill-switch, risk gate)</li>
      </ul>
      <p class="wizard__note">This 6-step tour walks each feature and, on step 2,
      verifies the data link with a live diagnostic. It only auto-opens once.</p>
    `,
  },
  {
    title: 'Communication Debug',
    html: `
      <p class="wizard__lead">Verifying the data chain on first launch. The
      dashboard never talks to cTrader directly — it proxies through the local
      API on <code>:8158</code>, which bridges to the cTrader MCP server.</p>
      <div class="wizard__checks" id="wizard-checks">
        <div class="wizard__check" data-check="api">
          <span class="wizard__check-dot"></span>
          <span class="wizard__check-label">Dashboard &rarr; API <code>:8158</code></span>
          <span class="wizard__check-status">running…</span>
        </div>
        <div class="wizard__check" data-check="mcp">
          <span class="wizard__check-dot"></span>
          <span class="wizard__check-label">API &rarr; cTrader MCP</span>
          <span class="wizard__check-status">running…</span>
        </div>
        <div class="wizard__check" data-check="ws">
          <span class="wizard__check-dot"></span>
          <span class="wizard__check-label">Dashboard &harr; WebSocket</span>
          <span class="wizard__check-status">running…</span>
        </div>
      </div>
      <div class="wizard__log" id="wizard-log"></div>
    `,
    onEnter: runConnectionChecks,
  },
  {
    title: 'Live Market & Account',
    html: `
      <p class="wizard__lead">The left chart shows candles, a 20-EMA and the
      volume profile (POC + value area) built from the cTrader MCP feed.</p>
      <ul class="wizard__list">
        <li><span class="wizard__bullet">&bull;</span> <strong>Equity / Daily P&amp;L</strong> update from <code>/api/state</code> via WebSocket every ~15s</li>
        <li><span class="wizard__bullet">&bull;</span> <strong>Open position</strong> panel reflects any live or dry-run trade</li>
        <li><span class="wizard__bullet">&bull;</span> Data flow: cTrader desktop app &rarr; MCP server &rarr; dashboard_api &rarr; WebSocket &rarr; this UI</li>
      </ul>
      <p class="wizard__note">The bot starts in <strong>dry-run</strong> by default — it logs
      signals and sizing without placing orders until you opt in.</p>
    `,
  },
  {
    title: 'Trade Journal & Digest',
    html: `
      <p class="wizard__lead">Every closed trade is written to the SQLite journal
      and shown in the table below the chart.</p>
      <ul class="wizard__list">
        <li><span class="wizard__bullet">&bull;</span> <strong>Journal</strong> — <code>GET /api/journal</code> (setup tag, outcome, R-multiple, lesson)</li>
        <li><span class="wizard__bullet">&bull;</span> <strong>Strategy digest</strong> — <code>GET /api/digest</code>, generated offline by
          <code>scripts/run_journal_review.py</code> (Anthropic)</li>
      </ul>
      <p class="wizard__note">The digest is the <em>only</em> place an LLM touches
      trade data, and it is fully offline / read-only.</p>
    `,
  },
  {
    title: 'Training Toolkit',
    html: `
      <p class="wizard__lead">Tuning is deterministic and advisory — it never
      places orders and never mutates <code>config.yaml</code>.</p>
      <ul class="wizard__list">
        <li><span class="wizard__bullet">&bull;</span> <code>python scripts/run_training.py optimize</code> — grid-search best params</li>
        <li><span class="wizard__bullet">&bull;</span> <code>python scripts/run_training.py simulate</code> — bar-by-bar replay + failure analysis</li>
        <li><span class="wizard__bullet">&bull;</span> <code>python scripts/run_training.py retrain</code> — narrow re-tune around current best</li>
        <li><span class="wizard__bullet">&bull;</span> Best params + live feedback persist to <code>data/reports/parameter_registry.json</code> (survives restarts)</li>
      </ul>
      <p class="wizard__note">Live trades append feedback to the registry; retrain
      blends them in. Opt into trained params at runtime with
      <code>--use-trained-params</code> (config.yaml stays the default).</p>
    `,
  },
  {
    title: 'Safety & Controls',
    html: `
      <p class="wizard__lead">Trading is gated by explicit, manual safety checks.</p>
      <ul class="wizard__warnings">
        <li><span class="wizard__warn">!</span> <strong>Demo account must be active</strong> in the cTrader desktop app. There is no programmatic switch — the bot only cross-checks the active account.</li>
        <li><span class="wizard__warn">!</span> <strong>Kill-switch:</strong> create <code>data/cache/.kill_switch</code> to stop the loop gracefully.</li>
        <li><span class="wizard__warn">!</span> <strong>Risk gate:</strong> max daily loss %, min stop ATR, max open risk %.</li>
        <li><span class="wizard__warn">!</span> <strong>Dry-run default:</strong> no orders until you explicitly run live.</li>
      </ul>
      <p class="wizard__note">You're all set. Use the "?" button any time to replay this tour.</p>
    `,
  },
];

/* ── State / DOM ────────────────────────────────────────────────────────── */

let currentStep = 0;

function buildOverlay() {
  const el = document.createElement('div');
  el.className = 'wizard';
  el.id = 'wizard';
  el.hidden = true;
  el.innerHTML = `
    <div class="wizard__backdrop" id="wizard-backdrop"></div>
    <div class="wizard__card" role="dialog" aria-modal="true" aria-label="Feature tour">
      <div class="wizard__header">
        <div class="wizard__titles">
          <h2 class="wizard__title" id="wizard-title">Setup &amp; Feature Tour</h2>
          <span class="wizard__step-count" id="wizard-step-count"></span>
        </div>
        <button class="wizard__close" id="wizard-close" aria-label="Close tour">&times;</button>
      </div>
      <div class="wizard__progress"><div class="wizard__progress-bar" id="wizard-progress-bar"></div></div>
      <div class="wizard__body" id="wizard-body"></div>
      <div class="wizard__footer">
        <button class="wizard__btn wizard__btn--ghost" id="wizard-skip">Skip tour</button>
        <div class="wizard__footer-right">
          <button class="wizard__btn wizard__btn--ghost" id="wizard-back">Back</button>
          <button class="wizard__btn wizard__btn--primary" id="wizard-next">Next</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(el);

  el.querySelector('#wizard-close').addEventListener('click', finish);
  el.querySelector('#wizard-skip').addEventListener('click', finish);
  el.querySelector('#wizard-back').addEventListener('click', () => goTo(currentStep - 1));
  el.querySelector('#wizard-next').addEventListener('click', () => {
    if (currentStep === STEPS.length - 1) finish();
    else goTo(currentStep + 1);
  });
  el.querySelector('#wizard-backdrop').addEventListener('click', finish);
}

function openWizard() {
  const wiz = document.getElementById('wizard');
  if (!wiz) buildOverlay();
  document.getElementById('wizard').hidden = false;
  goTo(0);
}

function closeWizard() {
  const wiz = document.getElementById('wizard');
  if (wiz) wiz.hidden = true;
}

function markDone() {
  try {
    localStorage.setItem(STORAGE_KEY, new Date().toISOString());
  } catch (e) {
    /* localStorage may be unavailable; non-fatal */
  }
}

function finish() {
  markDone();
  closeWizard();
}

function goTo(index) {
  if (index < 0 || index >= STEPS.length) return;
  currentStep = index;
  const step = STEPS[index];

  document.getElementById('wizard-step-count').textContent =
    `Step ${index + 1} of ${STEPS.length}`;
  document.getElementById('wizard-title').textContent = step.title;
  document.getElementById('wizard-body').innerHTML = step.html;

  const pct = ((index + 1) / STEPS.length) * 100;
  document.getElementById('wizard-progress-bar').style.width = `${pct}%`;

  const back = document.getElementById('wizard-back');
  const next = document.getElementById('wizard-next');
  back.disabled = index === 0;
  next.textContent = index === STEPS.length - 1 ? 'Finish' : 'Next';

  if (typeof step.onEnter === 'function') {
    Promise.resolve(step.onEnter()).catch((e) => logLine('[error]', String(e)));
  }
}

/* ── Communication debug (step 2) ──────────────────────────────────────── */

function logLine(prefix, msg) {
  const log = document.getElementById('wizard-log');
  if (!log) return;
  const time = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className = 'wizard__log-line';
  line.innerHTML = `<span class="wizard__log-time">${time}</span><span class="wizard__log-prefix">${prefix}</span><span>${msg || ''}</span>`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function setCheck(name, state, statusText) {
  const node = document.querySelector(`[data-check="${name}"]`);
  if (!node) return;
  const dot = node.querySelector('.wizard__check-dot');
  const status = node.querySelector('.wizard__check-status');
  dot.className = `wizard__check-dot wizard__check-dot--${state}`;
  if (statusText !== undefined) status.textContent = statusText;
}

function testWebSocket() {
  return new Promise((resolve) => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { ws && ws.close(); } catch (e) { /* noop */ }
      resolve({ ok: false });
    }, 4500);
    try {
      ws = new WebSocket(`${proto}//${location.host}/ws`);
      ws.onopen = () => logLine('[WS]', 'socket opened');
      ws.onmessage = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { ws.close(); } catch (e) { /* noop */ }
        resolve({ ok: true });
      };
      ws.onerror = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { ws.close(); } catch (e) { /* noop */ }
        resolve({ ok: false });
      };
    } catch (e) {
      settled = true;
      clearTimeout(timer);
      resolve({ ok: false });
    }
  });
}

async function runConnectionChecks() {
  logLine('[diag]', 'Starting communication diagnostics…');
  setCheck('api', 'pending');
  setCheck('mcp', 'pending');
  setCheck('ws', 'pending');

  let mcpConnected = false;
  try {
    const h = await fetchHealth();
    setCheck('api', 'ok', 'reachable');
    logLine('[API]', `health ok — mcp_connected=${h.mcp_connected}, demo_mode=${h.demo_mode}`);
    mcpConnected = !!h.mcp_connected;
    if (mcpConnected) {
      setCheck('mcp', 'ok', 'linked');
      logLine('[MCP]', 'cTrader MCP bridge is connected.');
    } else {
      setCheck('mcp', 'error', 'not linked');
      logLine('[MCP]', 'NOT linked — open the cTrader desktop app and ensure it is logged in.');
    }
  } catch (e) {
    setCheck('api', 'error', 'unreachable');
    setCheck('mcp', 'error', 'unknown');
    logLine('[API]', `ERROR ${e} — is the dashboard API running on :8158?`);
  }

  logLine('[WS]', 'opening WebSocket…');
  const wsResult = await testWebSocket();
  if (wsResult.ok) {
    setCheck('ws', 'ok', 'connected');
    logLine('[WS]', 'snapshot received — live link confirmed.');
  } else {
    setCheck('ws', 'error', 'no snapshot');
    logLine('[WS]', 'no snapshot within timeout — check the API process / firewall.');
  }

  const allOk = mcpConnected && wsResult.ok;
  logLine('[diag]', allOk
    ? 'Diagnostics complete — all links healthy.'
    : 'Diagnostics complete — see flags above; the tour still works.');
}

/* ── Entry point ────────────────────────────────────────────────────────── */

export function initWizard() {
  const helpBtn = document.getElementById('help-btn');
  if (helpBtn) helpBtn.addEventListener('click', openWizard);

  let done = false;
  try {
    done = !!localStorage.getItem(STORAGE_KEY);
  } catch (e) {
    done = false;
  }
  if (!done) openWizard();
}

initWizard();
