/**
 * "Live status" panel: surfaces execution/live_runner.py's own account of
 * why it did or didn't trade last cycle (GET /api/live-status), plus a
 * dashboard-driven kill switch (GET/POST /api/kill-switch...).
 *
 * Both were previously invisible/unreachable from the dashboard — the only
 * way to know "why isn't it trading" was tailing the live runner's own
 * stdout, and the kill switch could only be set by a human touching
 * data/cache/.kill_switch directly on disk.
 */

import { fetchLiveStatus, fetchKillSwitch, setKillSwitch } from './api.js';

function timeAgo(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}

export async function refreshLiveStatus() {
  const outcomeEl = document.getElementById('live-status-outcome');
  const detailEl = document.getElementById('live-status-detail');
  const timeEl = document.getElementById('live-status-time');
  if (!outcomeEl) return;

  try {
    const data = await fetchLiveStatus();
    if (!data.available) {
      outcomeEl.textContent = 'no data yet';
      outcomeEl.className = 'live-status__outcome';
      detailEl.textContent = data.reason || 'No cycle status recorded yet.';
      timeEl.textContent = '—';
      return;
    }
    outcomeEl.textContent = (data.outcome || 'unknown').replace(/_/g, ' ');
    outcomeEl.className = `live-status__outcome live-status__outcome--${data.outcome || ''}`;
    detailEl.textContent = data.detail || '';
    timeEl.textContent = timeAgo(data.timestamp);
  } catch (e) {
    console.warn('live status fetch failed', e);
  }
}

async function refreshKillSwitchButton() {
  const btn = document.getElementById('kill-switch-toggle');
  if (!btn) return;
  try {
    const { active } = await fetchKillSwitch();
    btn.textContent = active ? 'Deactivate kill switch' : 'Activate kill switch';
    btn.classList.toggle('chart-btn--danger', active);
    btn.dataset.active = active ? '1' : '0';
  } catch (e) {
    console.warn('kill switch status fetch failed', e);
  }
}

export function initLiveStatusPanel() {
  const btn = document.getElementById('kill-switch-toggle');
  if (btn) {
    btn.addEventListener('click', async () => {
      const currentlyActive = btn.dataset.active === '1';
      btn.disabled = true;
      try {
        await setKillSwitch(!currentlyActive);
      } catch (e) {
        console.warn('failed to set kill switch', e);
      } finally {
        btn.disabled = false;
        await refreshKillSwitchButton();
      }
    });
  }
  refreshKillSwitchButton();
  refreshLiveStatus();
  // Live status changes every poll cycle server-side; refresh on the same
  // cadence as the rest of the dashboard's periodic fetches.
  setInterval(refreshLiveStatus, 15000);
  setInterval(refreshKillSwitchButton, 15000);
}
