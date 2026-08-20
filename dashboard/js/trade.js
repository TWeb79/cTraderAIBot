/**
 * "Predicted trade" panel: shows the current deterministic prediction
 * (direction / entry / SL / TP, where TP is the predicted next-5min price)
 * and a single OK ("Execute trade") button.
 *
 * This module never talks to MCP and never places an order itself — clicking
 * the button POSTs /api/manual-trade, which queues a request that
 * live_runner.py (the same process that already places automated trades)
 * picks up on its next cycle and executes through its existing risk-sized,
 * kill-switch-respecting, journal-tracked pipeline. The dashboard API
 * resolves the actual entry/stop/target from its own current prediction at
 * queue time (not from anything this module sends), so a stale click can't
 * fire off outdated prices — see dashboard_api.py's POST /api/manual-trade.
 */

import { submitManualTrade, fetchManualTradeStatus } from './api.js';

let currentPrediction = null;
let pending = false;

function fmt(v, digits = 5) {
  return v == null ? '—' : Number(v).toFixed(digits);
}

function els() {
  return {
    direction: document.getElementById('predicted-direction'),
    likelihood: document.getElementById('predicted-likelihood'),
    entry: document.getElementById('predicted-entry'),
    sl: document.getElementById('predicted-sl'),
    tp: document.getElementById('predicted-tp'),
    note: document.getElementById('predicted-note'),
    execBtn: document.getElementById('predicted-execute'),
    status: document.getElementById('predicted-status'),
  };
}

function isActionable(pred) {
  return !!pred && (pred.direction === 'LONG' || pred.direction === 'SHORT')
    && pred.entry != null && pred.stop != null && pred.target != null;
}

/** Render the latest prediction (e.g. from the WebSocket's `data.auto`). */
export function renderPrediction(pred) {
  const e = els();
  if (!e.direction) return;
  currentPrediction = pred;

  const direction = (pred && pred.direction) || 'FLAT';
  e.direction.textContent = direction;
  e.direction.className = `predicted-trade__direction predicted-trade__direction--${direction.toLowerCase()}`;
  e.likelihood.textContent = pred ? `${Math.round((pred.likelihood || 0.5) * 100)}% likelihood` : '—';
  e.entry.textContent = fmt(pred && pred.entry);
  e.sl.textContent = fmt(pred && pred.stop);
  e.tp.textContent = fmt(pred && pred.target);
  e.note.textContent = (pred && (pred.note || pred.reason)) || 'no prediction yet';

  e.execBtn.disabled = pending || !isActionable(pred);
}

async function refreshPendingStatus() {
  const e = els();
  if (!e.status) return;
  try {
    const { pending: isPending, kill_switch_active } = await fetchManualTradeStatus();
    if (kill_switch_active) {
      e.status.textContent = 'Kill switch is active — execution disabled.';
      e.execBtn.disabled = true;
      pending = false;
      return;
    }
    if (isPending) {
      pending = true;
      e.status.textContent = 'Queued — the live runner will execute it on its next cycle (usually within ~15s).';
      e.execBtn.disabled = true;
    } else {
      const wasPending = pending;
      pending = false;
      if (wasPending) {
        e.status.textContent = 'Request cleared — check "Open position" or the trade journal for the result.';
      }
      e.execBtn.disabled = !isActionable(currentPrediction);
    }
  } catch (err) {
    console.warn('manual trade status fetch failed', err);
  }
}

export function initManualTradePanel() {
  const e = els();
  if (!e.execBtn) return;

  e.execBtn.addEventListener('click', async () => {
    e.execBtn.disabled = true;
    e.status.textContent = 'Submitting…';
    try {
      const res = await submitManualTrade();
      if (res.submitted) {
        pending = true;
        e.status.textContent = 'Queued — the live runner will execute it on its next cycle (usually within ~15s).';
      } else {
        e.status.textContent = res.reason || 'Could not submit the trade.';
        e.execBtn.disabled = !isActionable(currentPrediction);
      }
    } catch (err) {
      e.status.textContent = 'Submit failed — see console.';
      console.warn('manual trade submit failed', err);
      e.execBtn.disabled = !isActionable(currentPrediction);
    }
  });

  refreshPendingStatus();
  setInterval(refreshPendingStatus, 5000);
}
