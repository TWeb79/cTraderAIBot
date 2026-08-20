/**
 * In-dashboard training trigger (implementationplan.md §11.6) plus the
 * "simulated trades vs prediction" analysis window that sits beside the
 * controls: for each simulated trade, the predicted direction/price (the
 * deterministic signal's side + target_price at entry) next to what
 * actually happened (exit_price/exit_reason/r_multiple).
 *
 * The backend (POST/GET /api/training, GET /api/training/trades) already
 * runs optimize/simulate jobs in the background and broadcasts progress
 * over the WebSocket — this module is just the UI: start a job, reflect
 * status/log, chain "optimize then simulate", and render the last
 * simulate run's trade-by-trade breakdown.
 */

import { fetchTrainingStatus, startTraining, fetchSimulationTrades } from './api.js';

let lastMode = 'optimize';

function fmtNum(v, digits = 2) {
  return v == null ? '—' : Number(v).toFixed(digits);
}

function renderStatus(state) {
  const badge = document.getElementById('training-status-badge');
  const progress = document.getElementById('training-progress');
  const log = document.getElementById('training-log');
  const startBtn = document.getElementById('training-start');
  const chainBtn = document.getElementById('training-run-simulate');
  if (!badge) return;

  badge.textContent = state.status || 'idle';
  badge.className = `training__status-badge training__status-badge--${state.status || 'idle'}`;
  progress.textContent = state.stage ? `— ${state.stage}` : '';

  if (log) {
    log.innerHTML = (state.log || []).map(line => `<div>${line}</div>`).join('');
    log.scrollTop = log.scrollHeight;
  }

  const running = state.status === 'running';
  if (startBtn) startBtn.disabled = running;
  if (chainBtn) {
    // Enable "then run simulated trades" once an optimize job has completed.
    chainBtn.disabled = running || !(state.result && state.result.mode === 'optimize');
  }

  // A simulate job just finished — refresh the trades-vs-prediction window.
  if (state.status === 'completed' && state.result && state.result.mode === 'simulate') {
    refreshSimulationAnalysis();
  }
}

export async function pollTrainingStatus() {
  try {
    const state = await fetchTrainingStatus();
    renderStatus(state);
    return state;
  } catch (e) {
    console.warn('training status fetch failed', e);
    return null;
  }
}

/** Handle the {"type": "training", ...} WebSocket broadcast for live updates. */
export function handleTrainingBroadcast(msg) {
  renderStatus(msg);
}

/* ── Simulated trades vs prediction analysis window ─────────────────────── */

function renderSimulationAnalysis(data) {
  const summaryEl = document.getElementById('training-analysis-summary');
  const bodyEl = document.getElementById('training-analysis-body');
  if (!summaryEl || !bodyEl) return;

  const summary = data && data.summary;
  if (!summary || !summary.n_trades) {
    summaryEl.innerHTML = '<p class="training__analysis-empty">Run a "simulate" job to see the predicted-vs-actual breakdown here.</p>';
    bodyEl.innerHTML = '';
    return;
  }

  const hitPct = Math.round((summary.direction_hit_rate || 0) * 100);
  summaryEl.innerHTML = `
    <div class="training__analysis-stat"><span>${summary.n_trades}</span><label>trades</label></div>
    <div class="training__analysis-stat"><span>${hitPct}%</span><label>hit predicted direction</label></div>
    <div class="training__analysis-stat"><span>${fmtNum(summary.avg_r_multiple)}R</span><label>avg R multiple</label></div>
    <div class="training__analysis-stat"><span>${fmtNum(summary.avg_abs_price_delta, 4)}</span><label>avg |Δ| vs predicted price</label></div>
  `;

  const trades = (data.trades || []).slice(0, 30);
  bodyEl.innerHTML = trades.length ? trades.map(t => {
    const sideClass = t.side === 'SELL' ? 'training__side--sell' : 'training__side--buy';
    const resultClass = t.direction_correct ? 'training__result--correct' : 'training__result--wrong';
    const rClass = (t.r_multiple || 0) >= 0 ? 'training__r--pos' : 'training__r--neg';
    const deltaText = t.price_delta == null ? '—' : `${t.price_delta >= 0 ? '+' : ''}${t.price_delta.toFixed(4)}`;
    return `
      <tr>
        <td>${(t.entry_time || '').replace('T', ' ').slice(0, 16)}</td>
        <td class="${sideClass}">${t.side || '—'}</td>
        <td>${fmtNum(t.predicted_price, 4)}</td>
        <td>${fmtNum(t.exit_price, 4)}</td>
        <td>${deltaText}</td>
        <td class="${rClass}">${fmtNum(t.r_multiple, 2)}R</td>
        <td class="${resultClass}">${t.direction_correct ? 'hit target' : (t.exit_reason || '—')}</td>
      </tr>
    `;
  }).join('') : '<tr><td colspan="7" class="training__analysis-empty">No trades in this run.</td></tr>';
}

export async function refreshSimulationAnalysis() {
  try {
    const data = await fetchSimulationTrades(30);
    renderSimulationAnalysis(data);
  } catch (e) {
    console.warn('simulation trades fetch failed', e);
  }
}

export function initTrainingPanel() {
  const startBtn = document.getElementById('training-start');
  const chainBtn = document.getElementById('training-run-simulate');
  const modeSel = document.getElementById('training-mode');
  const daysInput = document.getElementById('training-days');
  const includeLiveBox = document.getElementById('training-include-live');
  if (!startBtn) return;

  const runJob = async (mode) => {
    lastMode = mode;
    try {
      await startTraining({
        mode,
        days: parseInt(daysInput.value, 10) || 30,
        include_live: !!includeLiveBox.checked,
      });
    } catch (e) {
      console.warn('start training failed', e);
    }
    pollTrainingStatus();
  };

  startBtn.addEventListener('click', () => runJob(modeSel.value));

  // "initiate training on old historical data and afterwards train on
  // simulated trades" — chain optimize -> simulate with one click.
  chainBtn.addEventListener('click', () => runJob('simulate'));

  pollTrainingStatus();
  refreshSimulationAnalysis();
  // Fallback poll in case the WebSocket broadcast is missed/reconnecting.
  setInterval(pollTrainingStatus, 8000);
}
