/**
 * Main application controller — wires the WebSocket account/position feed,
 * the polled enriched-bars endpoint (chart + core datapoints), and the
 * sidebar/training panels together.
 */

import { fetchVersion, fetchJournal, fetchBars, createWebSocket } from './api.js';
import { renderChart, setChartMode, resetChartView } from './chart.js';
import { initAutoControls, initSessionClock, renderDatapoints, renderLearningGauge, refreshLearningSparkline } from './panels.js';
import { initTrainingPanel, handleTrainingBroadcast } from './training.js';

const C = {
  long: '#3FBE8E',
  short: '#E2574C',
  textMuted: '#7C8AA5',
  textFaint: '#4C5A78',
  amber: '#E8A33D',
  cyan: '#4FD1C5',
  hairline: '#223050',
};

let chartDays = 3;
let chartTimeframe = 'M5';
let latestPrediction = null;

function renderSignals(container, signals) {
  if (!signals || !signals.length) {
    container.innerHTML = '<div style="color:var(--text-faint);font-size:0.75rem;">No signals yet</div>';
    return;
  }
  const gateColor = { approved: C.long, rejected: C.short, 'n/a': C.textFaint };
  container.innerHTML = signals.slice(0, 20).map(s => {
    const actionColor = s.action === 'BUY' ? C.long : s.action === 'SELL' ? C.short : C.textMuted;
    const arrow = s.action === 'BUY' ? '&#9650;' : s.action === 'SELL' ? '&#9660;' : '';
    return `
      <div class="signal">
        <div class="signal__header">
          <span class="signal__action" style="color:${actionColor}">${arrow} ${s.action}</span>
          <span class="signal__time">${s.time || ''}</span>
        </div>
        <div class="signal__note">${s.note || ''}</div>
        <div class="signal__gate" style="color:${gateColor[s.gate] || C.textFaint}">gate: ${s.gate}</div>
      </div>
    `;
  }).join('');
}

function renderPosition(container, positions) {
  if (!positions || !positions.length) {
    container.innerHTML = '<span style="color:var(--text-faint)">No open position</span>';
    return;
  }
  const p = positions[0];
  const pnl = p.pnl || 0;
  container.innerHTML = `
    <div class="position__row"><span>${p.symbol || 'US500'} · ${(p.side || 'BUY').toUpperCase()} · ${p.volume || 0} lots</span></div>
    <div class="position__row">
      <span style="color:var(--text-faint)">entry ${(p.entryPrice || p.entry_price || 0).toFixed(2)}</span>
      <span class="position__pnl" style="color:${pnl >= 0 ? C.long : C.short}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} pips</span>
    </div>
    <div class="position__row">
      <span style="color:var(--text-faint)">risk used</span>
      <span style="color:var(--text-muted)">—</span>
    </div>
  `;
}

function renderJournal(container, trades) {
  if (!trades || !trades.length) {
    container.innerHTML = '<tr><td colspan="5" style="color:var(--text-faint)">No trades yet</td></tr>';
    return;
  }
  container.innerHTML = trades.map(t => {
    const outcomeColor = t.outcome === 'WIN' ? C.long : t.outcome === 'LOSS' ? C.short : C.textMuted;
    const rColor = (t.r_multiple || 0) >= 0 ? C.long : C.short;
    return `
      <tr>
        <td style="color:var(--text-muted)">${t.closed_at || t.opened_at || ''}</td>
        <td style="color:var(--text-muted)">${t.setup_tag || '—'}</td>
        <td class="journal__outcome" style="color:${outcomeColor}">${t.outcome || '—'}</td>
        <td class="journal__table--right" style="color:${rColor}">${(t.r_multiple || 0) >= 0 ? '+' : ''}${(t.r_multiple || 0).toFixed(1)}R</td>
        <td style="color:var(--text-faint)">${(t.reflection && t.reflection.lesson) || '—'}</td>
      </tr>
    `;
  }).join('');
}

/** predict_next()'s Prediction.to_dict() uses entry/stop/target; chart.js's
 * overlay expects entry/sl/tp — map once here rather than in chart.js so
 * chart.js stays a generic renderer. */
function toChartPrediction(pred) {
  if (!pred || pred.entry == null || pred.stop == null || pred.target == null) return null;
  return { entry: pred.entry, sl: pred.stop, tp: pred.target };
}

async function refreshBars(chartEl, datapointsEl) {
  try {
    const { bars, session_markers: sessionMarkers } = await fetchBars(chartDays, chartTimeframe);
    if (bars && bars.length) {
      renderChart(chartEl, bars, toChartPrediction(latestPrediction), { sessionMarkers });
      renderDatapoints(datapointsEl, bars[bars.length - 1]);
    }
  } catch (e) {
    console.warn('bars fetch failed', e);
  }
}

function initChartToolbar(chartEl, datapointsEl) {
  const candlesBtn = document.getElementById('chart-mode-candles');
  const orderflowBtn = document.getElementById('chart-mode-orderflow');
  const resetBtn = document.getElementById('chart-reset-zoom');
  const daysSel = document.getElementById('chart-days');

  const setActive = (mode) => {
    candlesBtn?.classList.toggle('chart-btn--active', mode === 'candles');
    orderflowBtn?.classList.toggle('chart-btn--active', mode === 'orderflow');
  };
  candlesBtn?.addEventListener('click', () => { setChartMode(chartEl, 'candles'); setActive('candles'); });
  orderflowBtn?.addEventListener('click', () => { setChartMode(chartEl, 'orderflow'); setActive('orderflow'); });
  resetBtn?.addEventListener('click', () => resetChartView(chartEl));
  daysSel?.addEventListener('change', () => {
    chartDays = parseInt(daysSel.value, 10) || 3;
    refreshBars(chartEl, datapointsEl);
  });
}

async function init() {
  const versionEl = document.getElementById('version');
  const dailyPnlEl = document.getElementById('daily-pnl');
  const equityEl = document.getElementById('equity');
  const chartEl = document.getElementById('chart');
  const signalsEl = document.getElementById('signals');
  const positionEl = document.getElementById('position');
  const journalBody = document.getElementById('journal-body');
  const datapointsEl = document.getElementById('datapoints');

  try {
    const version = await fetchVersion();
    if (versionEl) versionEl.textContent = `v${version.version || '0.1.0'}`;
  } catch (e) {
    console.warn('Version fetch failed', e);
  }

  initChartToolbar(chartEl, datapointsEl);
  initSessionClock();
  initAutoControls();
  initTrainingPanel();
  refreshLearningSparkline();
  setInterval(refreshLearningSparkline, 60000);

  refreshBars(chartEl, datapointsEl);
  setInterval(() => refreshBars(chartEl, datapointsEl), 15000);

  createWebSocket((data) => {
    if (data.type === 'snapshot') {
      if (data.account) {
        if (equityEl) equityEl.textContent = data.account.equity?.toFixed(2) || '—';
        const pnl = data.account.daily_pnl || 0;
        dailyPnlEl.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%`;
        dailyPnlEl.style.color = pnl >= 0 ? C.long : C.short;
      }
      if (data.positions) {
        renderPosition(positionEl, data.positions);
      }
      if (data.auto) {
        latestPrediction = data.auto;
        renderLearningGauge(data.auto);
      }
    } else if (data.type === 'training') {
      handleTrainingBroadcast(data);
      if (data.status === 'completed') refreshLearningSparkline();
    }
  });

  try {
    const trades = await fetchJournal(25);
    renderJournal(journalBody, trades);
  } catch (e) {
    console.warn('Journal fetch failed', e);
  }
}

init();
