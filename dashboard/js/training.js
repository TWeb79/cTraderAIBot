/**
 * In-dashboard training trigger (implementationplan.md §11.6).
 *
 * The backend (POST/GET /api/training) already runs optimize/simulate jobs
 * in the background and broadcasts progress over the WebSocket — this module
 * is just the UI: start a job, reflect status/log, and chain "optimize then
 * simulate" the way the user originally asked for.
 */

import { fetchTrainingStatus, startTraining } from './api.js';

let lastMode = 'optimize';

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
  // Fallback poll in case the WebSocket broadcast is missed/reconnecting.
  setInterval(pollTrainingStatus, 8000);
}
