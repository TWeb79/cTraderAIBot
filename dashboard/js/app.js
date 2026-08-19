/**
 * Main application controller.
 */

import { fetchVersion, fetchState, fetchJournal, createWebSocket } from './api.js';
import { renderChart } from './chart.js';

const C = {
  long: '#3FBE8E',
  short: '#E2574C',
  textMuted: '#7C8AA5',
  textFaint: '#4C5A78',
  amber: '#E8A33D',
  cyan: '#4FD1C5',
  hairline: '#223050',
};

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

async function init() {
  const versionEl = document.getElementById('version');
  const dailyPnlEl = document.getElementById('daily-pnl');
  const equityEl = document.getElementById('equity');
  const chartEl = document.getElementById('chart');
  const signalsEl = document.getElementById('signals');
  const positionEl = document.getElementById('position');
  const journalBody = document.getElementById('journal-body');

  try {
    const version = await fetchVersion();
    if (versionEl) versionEl.textContent = `v${version.version || '0.1.0'}`;
  } catch (e) {
    console.warn('Version fetch failed', e);
  }

  createWebSocket((data) => {
    if (data.type === 'snapshot') {
      if (data.bars && data.bars.length) {
        const liveBars = data.bars.map(b => ({
          ...b,
          timestamp: new Date(b.timestamp).toISOString(),
        }));
        renderChart(chartEl, liveBars, null);
      }
      if (data.account) {
        if (equityEl) equityEl.textContent = data.account.equity?.toFixed(2) || '—';
        const pnl = data.account.daily_pnl || 0;
        dailyPnlEl.textContent = `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%`;
        dailyPnlEl.style.color = pnl >= 0 ? C.long : C.short;
      }
      if (data.positions) {
        renderPosition(positionEl, data.positions);
      }
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
