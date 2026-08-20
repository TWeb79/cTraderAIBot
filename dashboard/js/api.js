/**
 * API client for the dashboard backend.
 *
 * Base URL resolution (implementationplan.md §11.9): the dashboard is always
 * served on the "80xx" port and the API on the matching "81xx" port for the
 * same project number (RULES_ports.md's service-category pattern, e.g.
 * 8058 -> 8158). Previously this only worked when accessed as
 * localhost/127.0.0.1 — any LAN/Docker/remote-hostname access silently
 * pointed fetches at the dashboard's own origin instead of the API.
 */

function resolveApiBase() {
  const { protocol, hostname, port } = location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return `${protocol}//${hostname}:8158`;
  }
  const dashboardPort = parseInt(port, 10);
  if (Number.isFinite(dashboardPort)) {
    return `${protocol}//${hostname}:${dashboardPort + 100}`;
  }
  // No explicit port (served behind a reverse proxy on 80/443) — fall back
  // to same-origin; a proxy deployment is expected to route /api and /ws
  // itself in this case.
  return '';
}

const API_BASE = resolveApiBase();

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

export async function fetchVersion() {
  return get('/api/version');
}

export async function fetchHealth() {
  return get('/api/health');
}

export async function fetchState() {
  return get('/api/state');
}

export async function fetchJournal(limit = 25) {
  return get(`/api/journal?limit=${limit}`);
}

export async function fetchDigest() {
  return get('/api/digest');
}

export async function fetchRegistry() {
  return get('/api/registry');
}

export async function fetchRegistryHistory(limit = 20) {
  return get(`/api/registry/history?limit=${limit}`);
}

export async function fetchBars(days = 3, timeframe = 'M5') {
  return get(`/api/bars?days=${days}&timeframe=${encodeURIComponent(timeframe)}`);
}

export async function fetchSessions() {
  return get('/api/sessions');
}

export async function fetchStrategies() {
  return get('/api/strategies');
}

export async function fetchAuto() {
  return get('/api/auto');
}

export async function setAuto(payload) {
  return post('/api/auto/set', payload);
}

export async function startTraining(payload) {
  return post('/api/training', payload);
}

export async function fetchTrainingStatus() {
  return get('/api/training');
}

export async function fetchSimulationTrades(limit = 50) {
  return get(`/api/training/trades?limit=${limit}`);
}

export async function submitManualTrade() {
  return post('/api/manual-trade');
}

export async function fetchManualTradeStatus() {
  return get('/api/manual-trade');
}

/** "Why isn't it trading?" diagnostics — the live runner's own account of
 * its last decision point (kill switch / no data / no signal / auto-mode
 * gating / sizing / order placed), read from data/cache/.last_cycle_status.json. */
export async function fetchLiveStatus() {
  return get('/api/live-status');
}

export async function fetchKillSwitch() {
  return get('/api/kill-switch');
}

export async function setKillSwitch(active) {
  return post('/api/kill-switch/set', { active });
}

/** Trailing-stop trigger/distance + margin-% position sizing, overriding
 * config.yaml's risk.trailing_stop / risk.position_sizing_mode /
 * risk.margin_pct_of_free_margin without restarting the live runner. */
export async function fetchRiskControl() {
  return get('/api/risk-control');
}

export async function setRiskControl(payload) {
  return post('/api/risk-control/set', payload);
}

/** §15.2 orderflow footprint — buy/sell tick-volume by price level within a
 * single candle, built from finer sub-bars (see the endpoint's own docstring
 * for why this is a proxy, not true bid/ask depth). */
export async function fetchFootprint(timestamp, timeframe = 'M5') {
  return get(`/api/bars/${encodeURIComponent(timestamp)}/footprint?timeframe=${encodeURIComponent(timeframe)}`);
}

/** Bulk per-candle footprints for every bar currently in view — powers the
 * chart's Orderflow mode itself (one request instead of one per candle). */
export async function fetchBarFootprints(days = 3, timeframe = 'M5') {
  return get(`/api/bars/footprint?days=${days}&timeframe=${encodeURIComponent(timeframe)}`);
}

export function getApiHost() {
  if (API_BASE && (API_BASE.startsWith('http://') || API_BASE.startsWith('https://'))) {
    try {
      return new URL(API_BASE).host;
    } catch (e) {
      /* fall through to location.host */
    }
  }
  return location.host;
}

export function createWebSocket(onMessage) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = getApiHost();
  const ws = new WebSocket(`${proto}//${host}/ws`);
  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      onMessage(data);
    } catch (e) {
      console.error('WS parse error', e);
    }
  };
  return ws;
}
