/**
 * "Position & trailing" panel: dashboard control over the trailing-stop
 * trigger/distance (§15.8) and margin-% position sizing (§15.6), which
 * were previously config.yaml-only and required restarting the live
 * runner to change. Backed by GET/POST /api/risk-control..., which writes
 * data/cache/.risk_control.json — execution/live_runner.py reads it fresh
 * every cycle (see load_risk_control()/_apply_risk_control_overrides()),
 * so a change here takes effect on the next cycle, no restart needed.
 */

import { fetchRiskControl, setRiskControl } from './api.js';

function els() {
  return {
    trailingEnabled: document.getElementById('risk-trailing-enabled'),
    trigger: document.getElementById('risk-trailing-trigger'),
    distance: document.getElementById('risk-trailing-distance'),
    marginSizing: document.getElementById('risk-margin-sizing'),
    marginPct: document.getElementById('risk-margin-pct'),
    saveBtn: document.getElementById('risk-controls-save'),
    hint: document.getElementById('risk-controls-hint'),
  };
}

function applyToForm(e, riskControl) {
  const trailing = riskControl.trailing_stop || {};
  e.trailingEnabled.checked = !!trailing.enabled;
  e.trigger.value = trailing.trigger_pips ?? '';
  e.distance.value = trailing.lock_pips ?? '';
  e.marginSizing.checked = riskControl.position_sizing_mode === 'margin_pct';
  e.marginPct.value = riskControl.margin_pct_of_free_margin ?? '';
}

export async function refreshRiskControl() {
  const e = els();
  if (!e.saveBtn) return;
  try {
    const { risk_control: riskControl } = await fetchRiskControl();
    applyToForm(e, riskControl);
    e.hint.textContent = 'Applies on the live runner’s next cycle — no restart needed.';
  } catch (err) {
    console.warn('risk control fetch failed', err);
    e.hint.textContent = 'Could not load current values.';
  }
}

export function initRiskControlPanel() {
  const e = els();
  if (!e.saveBtn) return;

  e.saveBtn.addEventListener('click', async () => {
    e.saveBtn.disabled = true;
    e.hint.textContent = 'Saving…';
    try {
      const payload = {
        trailing_stop: {
          enabled: e.trailingEnabled.checked,
          trigger_pips: parseFloat(e.trigger.value) || 0,
          lock_pips: parseFloat(e.distance.value) || 0,
        },
        position_sizing_mode: e.marginSizing.checked ? 'margin_pct' : 'risk_pct',
        margin_pct_of_free_margin: parseFloat(e.marginPct.value) || 0,
      };
      const { risk_control: riskControl } = await setRiskControl(payload);
      applyToForm(e, riskControl);
      e.hint.textContent = 'Saved — applies on the live runner’s next cycle.';
    } catch (err) {
      console.warn('failed to save risk control', err);
      e.hint.textContent = 'Save failed — see console.';
    } finally {
      e.saveBtn.disabled = false;
    }
  });

  refreshRiskControl();
}
