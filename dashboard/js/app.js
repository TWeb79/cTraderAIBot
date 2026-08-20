/**
 * Main application controller — wires the WebSocket account/position feed,
 * the polled enriched-bars endpoint (chart + core datapoints), and the
 * sidebar/training panels together.
 */

import {
  fetchVersion, fetchJournal, fetchDigest, fetchBars, fetchFootprint, fetchBarFootprints, createWebSocket,
} from './api.js';
import {
  renderChart, setChartMode, getChartMode, resetChartView, setOverlayEnabled, getOverlayEnabled,
  updateChartExtras, updateChartPrediction, setFootprints,
} from './chart.js';
import { initAutoControls, initSessionClock, renderDatapoints, renderLearningGauge, refreshLearningSparkline } from './panels.js';
import { initTrainingPanel, handleTrainingBroadcast } from './training.js';
import { renderPrediction, initManualTradePanel } from './trade.js';
import { initLiveStatusPanel } from './live-status.js';
import { initRiskControlPanel } from './risk-control.js';

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
let latestPositions = [];

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

function numOrNull(v) {
  return (v === null || v === undefined || v === '') ? null : Number(v);
}

/** Raw MCP position dicts have no documented field-casing in this codebase
 * (see mcp_client.py's get_positions()) — defensively try the common
 * variants, same spirit as renderPosition()'s entryPrice/entry_price
 * fallback, so the chart overlay degrades gracefully instead of crashing. */
function normalizePositions(positions) {
  if (!positions || !positions.length) return [];
  return positions.map(p => ({
    side: (p.side || p.tradeSide || p.direction || '').toString().toUpperCase() || null,
    volume: p.volume != null ? p.volume : (p.filledVolume != null ? p.filledVolume : null),
    entry: numOrNull(p.entryPrice ?? p.entry_price),
    sl: numOrNull(p.stopLoss ?? p.stop_loss ?? p.sl),
    tp: numOrNull(p.takeProfit ?? p.take_profit ?? p.tp),
  }));
}

/** Hover text: predicted direction/price (from the stored TradeDecision —
 * see journal/store.py's §15.5 decision_json addition) vs the actual
 * outcome (from TradeReflection) — implementationplan.md §15.5's "when I
 * hover them I would like to see the predicted price and direction and the
 * actual" request. Falls back gracefully for trades recorded before
 * decision_json/pnl existed in the journal schema. */
function journalRowTooltip(t) {
  const decision = t.decision || {};
  const reflection = t.reflection || {};
  const predicted = decision.action
    ? `Predicted: ${decision.action} @ ${fmt2(decision.entry_price)} (SL ${fmt2(decision.stop_loss)} / TP ${fmt2(decision.take_profit)})`
    : 'Predicted: —';
  const actual = `Actual: ${reflection.outcome || t.outcome || '—'}` +
    (reflection.pnl != null ? ` (${reflection.pnl >= 0 ? '+' : ''}${Number(reflection.pnl).toFixed(2)})` : '');
  return `${predicted}\n${actual}`;
}

function fmt2(v) {
  return v == null ? '—' : Number(v).toFixed(2);
}

function renderJournal(container, trades) {
  if (!trades || !trades.length) {
    container.innerHTML = '<tr><td colspan="5" style="color:var(--text-faint)">No trades yet</td></tr>';
    return;
  }
  container.innerHTML = trades.map(t => {
    const outcome = (t.reflection && t.reflection.outcome) || t.outcome;
    const outcomeColor = outcome === 'WIN' ? C.long : outcome === 'LOSS' ? C.short : C.textMuted;
    const rColor = (t.r_multiple || 0) >= 0 ? C.long : C.short;
    const tooltip = journalRowTooltip(t);
    return `
      <tr title="${tooltip.replace(/"/g, '&quot;')}">
        <td style="color:var(--text-muted)">${t.closed_at || t.opened_at || ''}</td>
        <td style="color:var(--text-muted)">${t.setup_tag || '—'}</td>
        <td class="journal__outcome" style="color:${outcomeColor}">${outcome || '—'}</td>
        <td class="journal__table--right" style="color:${rColor}">${(t.r_multiple || 0) >= 0 ? '+' : ''}${(t.r_multiple || 0).toFixed(1)}R</td>
        <td style="color:var(--text-faint)">${(t.reflection && t.reflection.lesson) || '—'}</td>
      </tr>
    `;
  }).join('');
}

/** Performance summary strip above the journal (§15.5: "overall p/l and
 * successrate ... would be good") — sourced from GET /api/digest's
 * journal.aggregate_stats() (win_rate/avg_r/total_pnl computed server-side
 * in journal/store.py). */
function renderPerformance(container, stats) {
  if (!container) return;
  if (!stats || !stats.n_trades) {
    container.innerHTML = '<div class="performance__empty">No closed trades yet</div>';
    return;
  }
  const pnl = stats.total_pnl || 0;
  const winRatePct = Math.round((stats.win_rate || 0) * 100);
  const tiles = [
    ['Trades', stats.n_trades],
    ['Win rate', `${winRatePct}%`],
    ['Total P/L', `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}`],
    ['Avg R', `${(stats.avg_r || 0) >= 0 ? '+' : ''}${(stats.avg_r || 0).toFixed(2)}R`],
  ];
  container.innerHTML = tiles.map(([label, value]) => `
    <div class="performance__tile">
      <span class="performance__label">${label}</span>
      <span class="performance__value" style="${label === 'Total P/L' ? `color:${pnl >= 0 ? C.long : C.short}` : ''}${label === 'Win rate' ? `;color:${winRatePct >= 50 ? C.long : C.short}` : ''}">${value}</span>
    </div>
  `).join('');
}

async function refreshPerformance(container) {
  try {
    const { stats } = await fetchDigest();
    renderPerformance(container, stats);
  } catch (e) {
    console.warn('digest fetch failed', e);
  }
}

/** §15.2 orderflow footprint panel: renders the buy/sell-by-price-level
 * breakdown for whichever candle the user last clicked on the chart. */
function renderFootprint(container, data, bar) {
  if (!container) return;
  if (!data || data.error) {
    container.innerHTML = `<div class="footprint__empty">${(data && data.error) || 'No data for this candle'}</div>`;
    return;
  }
  const levels = data.levels || [];
  if (!levels.length) {
    container.innerHTML = `<div class="footprint__empty">${data.note || 'No sub-bar data for this candle'}</div>`;
    return;
  }
  const maxVol = Math.max(...levels.map(l => l.buy_volume + l.sell_volume), 1);
  const rows = levels.map(l => {
    const buyPct = (l.buy_volume / maxVol) * 100;
    const sellPct = (l.sell_volume / maxVol) * 100;
    const isHighDemand = l.price === data.high_demand_price;
    return `
      <div class="footprint__row${isHighDemand ? ' footprint__row--demand' : ''}">
        <span class="footprint__price">${l.price.toFixed(5)}</span>
        <span class="footprint__bar footprint__bar--sell" style="width:${sellPct}%"></span>
        <span class="footprint__bar footprint__bar--buy" style="width:${buyPct}%"></span>
        <span class="footprint__delta" style="color:${l.delta >= 0 ? C.long : C.short}">${l.delta >= 0 ? '+' : ''}${l.delta.toFixed(1)}</span>
      </div>
    `;
  }).join('');
  const when = bar ? formatCandleTime(bar.timestamp) : '';
  container.innerHTML = `
    <div class="footprint__meta">
      <span>${when}</span>
      <span>Buy ${data.total_buy_volume.toFixed(1)} / Sell ${data.total_sell_volume.toFixed(1)}</span>
    </div>
    <div class="footprint__levels">${rows}</div>
    <p class="footprint__note">${data.note || ''}</p>
  `;
}

function formatCandleTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || '';
  return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}

async function handleCandleClick(footprintEl, bar) {
  if (!footprintEl || !bar) return;
  footprintEl.innerHTML = '<div class="footprint__empty">Loading…</div>';
  try {
    const data = await fetchFootprint(bar.timestamp, chartTimeframe);
    renderFootprint(footprintEl, data, bar);
  } catch (e) {
    console.warn('footprint fetch failed', e);
    renderFootprint(footprintEl, { error: 'Failed to load footprint' }, bar);
  }
}

/** Bulk-loads every visible candle's footprint and hands it to the chart,
 * which draws it in place of the candle body while Orderflow mode is
 * active (see chart.js's setFootprints()/draw()). Only called once the
 * user actually switches into Orderflow — candle mode never fetches this. */
async function refreshFootprints(chartEl) {
  try {
    const { footprints } = await fetchBarFootprints(chartDays, chartTimeframe);
    setFootprints(chartEl, footprints);
  } catch (e) {
    console.warn('bulk footprint fetch failed', e);
  }
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
      renderChart(chartEl, bars, toChartPrediction(latestPrediction), { sessionMarkers, positions: latestPositions });
      renderDatapoints(datapointsEl, bars[bars.length - 1]);
    }
  } catch (e) {
    console.warn('bars fetch failed', e);
  }
}

/** §15.4: keep the chart's prediction/position overlay visible by default
 * whenever auto mode is off (so there's always something to "react on"),
 * without fighting a user who has explicitly turned the overlay off/on —
 * only re-syncs at the moment auto mode itself is toggled. */
function syncOverlayWithAuto(chartEl, autoState) {
  const overlayBtn = document.getElementById('chart-overlay-toggle');
  const enabled = !autoState.enabled;
  setOverlayEnabled(chartEl, enabled);
  overlayBtn?.classList.toggle('chart-btn--active', enabled);
}

function initChartToolbar(chartEl, datapointsEl) {
  const candlesBtn = document.getElementById('chart-mode-candles');
  const orderflowBtn = document.getElementById('chart-mode-orderflow');
  const overlayBtn = document.getElementById('chart-overlay-toggle');
  const resetBtn = document.getElementById('chart-reset-zoom');
  const daysSel = document.getElementById('chart-days');

  const setActive = (mode) => {
    candlesBtn?.classList.toggle('chart-btn--active', mode === 'candles');
    orderflowBtn?.classList.toggle('chart-btn--active', mode === 'orderflow');
  };
  candlesBtn?.addEventListener('click', () => { setChartMode(chartEl, 'candles'); setActive('candles'); });
  orderflowBtn?.addEventListener('click', () => {
    setChartMode(chartEl, 'orderflow');
    setActive('orderflow');
    // Lazily load footprints only once the user actually activates this
    // view (implementationplan.md §15.2 follow-up) — candle mode never
    // pays the extra fetch.
    refreshFootprints(chartEl);
  });
  overlayBtn?.addEventListener('click', () => {
    const enabled = !getOverlayEnabled(chartEl);
    setOverlayEnabled(chartEl, enabled);
    overlayBtn.classList.toggle('chart-btn--active', enabled);
  });
  resetBtn?.addEventListener('click', () => resetChartView(chartEl));
  daysSel?.addEventListener('change', () => {
    chartDays = parseInt(daysSel.value, 10) || 3;
    refreshBars(chartEl, datapointsEl);
    if (getChartMode(chartEl) === 'orderflow') refreshFootprints(chartEl);
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
  const performanceEl = document.getElementById('performance');
  const datapointsEl = document.getElementById('datapoints');
  const footprintEl = document.getElementById('footprint');

  try {
    const version = await fetchVersion();
    if (versionEl) versionEl.textContent = `v${version.version || '0.1.0'}`;
  } catch (e) {
    console.warn('Version fetch failed', e);
  }

  initChartToolbar(chartEl, datapointsEl);
  initSessionClock();
  initAutoControls((autoState) => syncOverlayWithAuto(chartEl, autoState));
  initTrainingPanel();
  initManualTradePanel();
  initLiveStatusPanel();
  initRiskControlPanel();
  refreshLearningSparkline();
  setInterval(refreshLearningSparkline, 60000);
  refreshPerformance(performanceEl);
  setInterval(() => refreshPerformance(performanceEl), 60000);

  refreshBars(chartEl, datapointsEl);
  setInterval(() => {
    refreshBars(chartEl, datapointsEl);
    if (getChartMode(chartEl) === 'orderflow') refreshFootprints(chartEl);
  }, 15000);
  chartEl.addEventListener('candleclick', (ev) => handleCandleClick(footprintEl, ev.detail.bar));

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
        latestPositions = normalizePositions(data.positions);
        updateChartExtras(chartEl, { positions: latestPositions });
      }
      if (data.auto) {
        latestPrediction = data.auto;
        renderLearningGauge(data.auto);
        renderPrediction(data.auto);
        updateChartPrediction(chartEl, toChartPrediction(data.auto));
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
