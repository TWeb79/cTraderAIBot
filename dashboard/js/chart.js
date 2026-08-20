/**
 * SVG chart renderer: candlesticks + EMA overlay + volume profile, with a
 * price/time axis + legend, session-window markers, wheel-zoom + drag-pan,
 * and a tick-volume "orderflow" view toggle.
 *
 * implementationplan.md §11.2 (legend), §11.7 (zoom/pan, orderflow toggle,
 * session markers). Kept dependency-free (no charting library / build step)
 * per the dashboard's existing vanilla HTML/CSS/JS constraint.
 */

const PAD_LEFT = 46;   // price-axis label gutter
const CHART_W = 580;   // candle plot width
const AXIS_GAP = 6;
const VP_W = 110;      // volume-profile sidebar width
const TOTAL_W = PAD_LEFT + CHART_W + AXIS_GAP + VP_W;
const CHART_H = 380;
const PAD_TOP = 32;    // room for the legend row
const PAD_BOTTOM = 34; // room for the time-axis row

const MIN_VIEW_BARS = 12;

// Per-<svg> chart state (zoom/pan window + view mode), so redraws triggered
// by fresh WebSocket data don't reset the user's zoom/pan position.
const chartState = new WeakMap();

function getState(svgEl) {
  let s = chartState.get(svgEl);
  if (!s) {
    s = {
      start: 0, len: 1, mode: 'candles', lastBars: null, lastPrediction: null,
      lastExtras: null, bound: false,
      // Activatable overlay (implementationplan.md feature request): open
      // positions' entry/SL/TP + the predicted next-5min price. Off by
      // default — the user turns it on via the toolbar's "Overlay" button.
      showOverlay: false,
      // §15.2 follow-up: per-candle buy/sell-by-price footprint data, keyed
      // by bar timestamp, drawn instead of a plain tick-volume bar whenever
      // the Orderflow view is active — see setFootprints() / draw()'s
      // orderflow branch. Empty until the user switches into Orderflow mode
      // (see app.js's refreshFootprints()), so candle mode never pays for it.
      footprints: {},
    };
    chartState.set(svgEl, s);
  }
  return s;
}

function ema(values, period) {
  const k = 2 / (period + 1);
  let prev = values[0];
  const out = [prev];
  for (let i = 1; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function volumeProfile(bars, binCount) {
  const lo = Math.min(...bars.map(b => b.low));
  const hi = Math.max(...bars.map(b => b.high));
  const size = (hi - lo) / binCount || 1;
  const vols = new Array(binCount).fill(0);
  bars.forEach(b => {
    const s = Math.max(0, Math.min(binCount - 1, Math.floor((b.low - lo) / size)));
    const e = Math.max(0, Math.min(binCount - 1, Math.floor((b.high - lo) / size)));
    const span = e - s + 1;
    for (let i = s; i <= e; i++) vols[i] += b.volume / span;
  });
  const max = Math.max(...vols, 1);
  const total = vols.reduce((a, b) => a + b, 0) || 1;
  const sorted = vols.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]);
  const va = new Set();
  let cum = 0;
  for (const [v, i] of sorted) {
    cum += v;
    va.add(i);
    if (cum >= total * 0.7) break;
  }
  const pocIndex = sorted[0][1];
  return { lo, hi, size, vols, max, va, pocIndex };
}

function formatTimeTick(iso, spanMs) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const showDate = spanMs > 20 * 60 * 60 * 1000; // > ~20h visible -> include date
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  if (!showDate) return `${hh}:${mm}`;
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const da = String(d.getUTCDate()).padStart(2, '0');
  return `${mo}-${da} ${hh}:${mm}`;
}

function niceStep(range, targetTicks) {
  const raw = range / targetTicks;
  const pow10 = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const norm = raw / pow10;
  let step;
  if (norm < 1.5) step = 1;
  else if (norm < 3.5) step = 2;
  else if (norm < 7.5) step = 5;
  else step = 10;
  return step * pow10;
}

const SESSION_COLOR = {
  asia: 'var(--text-faint)',
  frankfurt: 'var(--cyan)',
  ny: 'var(--amber)',
};

function escapeXml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function draw(svgEl, bars, prediction, extras) {
  const state = getState(svgEl);
  if (!bars || !bars.length) return;

  const total = bars.length;
  const startIdx = Math.max(0, Math.min(total - MIN_VIEW_BARS, Math.floor(state.start * total)));
  const viewCount = Math.max(MIN_VIEW_BARS, Math.min(total - startIdx, Math.round(state.len * total)));
  const view = bars.slice(startIdx, startIdx + viewCount);

  const closes = view.map(b => b.close);
  const ema20 = ema(closes, Math.min(20, closes.length));
  const vp = volumeProfile(view, 30);

  // Open positions + the predicted next-5min price only affect the visible
  // Y-range while the overlay is actually switched on (see toolbar's
  // "Overlay" toggle / setOverlayEnabled) — otherwise a hidden prediction
  // shouldn't reserve vertical space on the chart.
  const positions = (state.showOverlay && extras && extras.positions) || [];
  const overlayPrices = [];
  if (state.showOverlay && prediction) {
    overlayPrices.push(prediction.entry, prediction.sl, prediction.tp);
  }
  positions.forEach(p => overlayPrices.push(p.entry, p.sl, p.tp));
  const extra = overlayPrices.filter(v => v != null);
  const lows = view.map(b => b.low);
  const highs = view.map(b => b.high);
  let min = Math.min(...lows, ...extra);
  let max = Math.max(...highs, ...extra);
  const pad = (max - min) * 0.08 || 1;
  min -= pad;
  max += pad;
  const plotH = CHART_H - PAD_TOP - PAD_BOTTOM;
  const y = (price) => PAD_TOP + ((max - price) / (max - min)) * plotH;

  const slot = CHART_W / view.length;
  const candleW = Math.max(2, slot * 0.62);
  const x = (i) => PAD_LEFT + i * slot + slot / 2;
  const emaPoints = ema20.map((v, i) => `${x(i)},${y(v)}`).join(' ');

  const priceLines = (state.showOverlay && prediction)
    ? [
        prediction.tp != null && { label: 'PRED 5m (TP)', price: prediction.tp, color: 'var(--long)' },
        prediction.entry != null && { label: 'ENTRY', price: prediction.entry, color: 'var(--cyan)' },
        prediction.sl != null && { label: 'SL', price: prediction.sl, color: 'var(--short)' },
      ].filter(Boolean)
    : [];

  let html = '';

  // ── Legend (implementationplan.md §11.2) ──────────────────────────────
  const legendItems = state.mode === 'orderflow'
    ? [
        { swatch: 'var(--long)', label: 'Buy volume (by price)' },
        { swatch: 'var(--short)', label: 'Sell volume (by price)' },
        { swatch: 'var(--amber)', label: 'High-demand level' },
      ]
    : [
        { swatch: 'var(--long)', label: 'Bull candle' },
        { swatch: 'var(--short)', label: 'Bear candle' },
        { swatch: 'var(--amber)', label: 'EMA-20 / POC' },
        { swatch: 'var(--cyan-dim)', label: 'Value area' },
      ];
  let lx = PAD_LEFT;
  legendItems.forEach(item => {
    html += `<rect x="${lx}" y="8" width="8" height="8" rx="1.5" fill="${item.swatch}" />`;
    html += `<text x="${lx + 12}" y="15" font-size="9" font-family="'JetBrains Mono', monospace" fill="var(--text-faint)">${escapeXml(item.label)}</text>`;
    lx += 14 + item.label.length * 5.4 + 14;
  });

  // ── Price-axis gridlines + labels ──────────────────────────────────────
  const priceStep = niceStep(max - min, 5);
  const firstTick = Math.ceil(min / priceStep) * priceStep;
  for (let p = firstTick; p <= max; p += priceStep) {
    const yy = y(p);
    html += `<line x1="${PAD_LEFT}" x2="${PAD_LEFT + CHART_W}" y1="${yy}" y2="${yy}" stroke="var(--hairline)" stroke-width="1" />`;
    html += `<text x="${PAD_LEFT - 6}" y="${yy + 3}" text-anchor="end" font-size="9" font-family="'JetBrains Mono', monospace" fill="var(--text-faint)">${p.toFixed(2)}</text>`;
  }

  // ── Time-axis labels ────────────────────────────────────────────────
  const spanMs = new Date(view[view.length - 1].timestamp) - new Date(view[0].timestamp);
  const tickEvery = Math.max(1, Math.round(view.length / 6));
  for (let i = 0; i < view.length; i += tickEvery) {
    html += `<text x="${x(i)}" y="${CHART_H - PAD_BOTTOM + 16}" text-anchor="middle" font-size="9" font-family="'JetBrains Mono', monospace" fill="var(--text-faint)">${formatTimeTick(view[i].timestamp, spanMs)}</text>`;
  }

  // ── Session-window markers (Asia / Frankfurt / NY open+close) ─────────
  const markers = (extras && extras.sessionMarkers) || [];
  if (markers.length) {
    const times = view.map(b => new Date(b.timestamp).getTime());
    markers.forEach(m => {
      const t = new Date(m.ts).getTime();
      if (t < times[0] || t > times[times.length - 1]) return;
      // nearest bar index
      let idx = 0, best = Infinity;
      for (let i = 0; i < times.length; i++) {
        const d = Math.abs(times[i] - t);
        if (d < best) { best = d; idx = i; }
      }
      const color = SESSION_COLOR[m.session] || 'var(--text-faint)';
      const dash = m.kind === 'open' ? '2 2' : '5 3';
      html += `<line x1="${x(idx)}" x2="${x(idx)}" y1="${PAD_TOP}" y2="${CHART_H - PAD_BOTTOM}" stroke="${color}" stroke-width="1" stroke-dasharray="${dash}" opacity="0.55" />`;
      if (m.kind === 'open') {
        html += `<text x="${x(idx) + 3}" y="${PAD_TOP + 9}" font-size="8" font-family="'JetBrains Mono', monospace" fill="${color}" opacity="0.85">${escapeXml(m.label)}</text>`;
      }
    });
  }

  // ── Activatable overlay: predicted next-5min price (TP/ENTRY/SL) ───────
  priceLines.forEach(pl => {
    html += `<line x1="${PAD_LEFT}" x2="${TOTAL_W}" y1="${y(pl.price)}" y2="${y(pl.price)}" stroke="${pl.color}" stroke-width="1" stroke-dasharray="4 3" opacity="0.85" />`;
    html += `<text x="${TOTAL_W - 2}" y="${y(pl.price) - 3}" text-anchor="end" font-size="9" font-family="'JetBrains Mono', monospace" fill="${pl.color}">${pl.label} ${pl.price.toFixed(5)}</text>`;
  });

  // ── Activatable overlay: open positions' entry / SL / TP ───────────────
  positions.forEach(p => {
    const col = p.side === 'SELL' ? 'var(--short)' : 'var(--long)';
    const label = `${p.side || 'POS'}${p.volume != null ? ' ' + p.volume + 'L' : ''}`;
    if (p.entry != null) {
      html += `<line x1="${PAD_LEFT}" x2="${TOTAL_W}" y1="${y(p.entry)}" y2="${y(p.entry)}" stroke="${col}" stroke-width="1.4" opacity="0.9" />`;
      html += `<text x="${PAD_LEFT + 3}" y="${y(p.entry) - 3}" font-size="8" font-family="'JetBrains Mono', monospace" fill="${col}">${escapeXml(label)} @ ${p.entry.toFixed(5)}</text>`;
    }
    if (p.sl != null) {
      html += `<line x1="${PAD_LEFT}" x2="${TOTAL_W}" y1="${y(p.sl)}" y2="${y(p.sl)}" stroke="var(--short)" stroke-width="1" stroke-dasharray="3 2" opacity="0.7" />`;
    }
    if (p.tp != null) {
      html += `<line x1="${PAD_LEFT}" x2="${TOTAL_W}" y1="${y(p.tp)}" y2="${y(p.tp)}" stroke="var(--long)" stroke-width="1" stroke-dasharray="3 2" opacity="0.7" />`;
    }
  });

  if (state.mode === 'orderflow') {
    // Per-candle footprint (implementationplan.md §15.2 follow-up: "the
    // orderflow footprint should be shown instead of a candle once i
    // activate this view") — buy/sell tick-volume by price level, in place
    // of the candle body itself. Still a tick-volume proxy, not real
    // bid/ask depth (see legend + chart-panel__meta caption in index.html)
    // — state.footprints is populated by app.js's refreshFootprints() only
    // once the user switches into this view (see setFootprints()).
    const halfW = Math.max(2, slot * 0.42);
    const maxVol = Math.max(...view.map(b => b.volume || 0), 1);
    const zeroY = CHART_H - PAD_BOTTOM;
    const bandH = plotH * 0.32;
    view.forEach((b, i) => {
      const fp = state.footprints[b.timestamp];
      if (fp && fp.levels && fp.levels.length) {
        const levelMax = Math.max(...fp.levels.map(l => Math.max(l.buy_volume, l.sell_volume)), 1);
        const halfBin = (fp.bin_size || (max - min) * 0.01) / 2;
        fp.levels.forEach(l => {
          const yTop = y(l.price + halfBin);
          const yBot = y(l.price - halfBin);
          const rowH = Math.max(1, yBot - yTop - 0.5);
          const isDemand = l.price === fp.high_demand_price;
          const sellW = (l.sell_volume / levelMax) * halfW;
          const buyW = (l.buy_volume / levelMax) * halfW;
          if (isDemand) {
            html += `<rect x="${x(i) - halfW}" y="${yTop}" width="${halfW * 2}" height="${rowH}" fill="var(--amber)" opacity="0.15" />`;
          }
          html += `<rect x="${x(i) - sellW}" y="${yTop}" width="${sellW}" height="${rowH}" fill="var(--short)" opacity="0.85" />`;
          html += `<rect x="${x(i)}" y="${yTop}" width="${buyW}" height="${rowH}" fill="var(--long)" opacity="0.85" />`;
        });
        html += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="var(--hairline-bright)" stroke-width="0.5" opacity="0.5" />`;
      } else {
        // Footprint not loaded yet for this candle (still fetching, or
        // outside the fetched range) — fall back to the simple tick-volume
        // bar so the view isn't empty while data streams in.
        const up = b.close >= b.open;
        const col = up ? 'var(--long)' : 'var(--short)';
        const h = ((b.volume || 0) / maxVol) * bandH;
        html += `<rect x="${x(i) - candleW / 2}" y="${zeroY - h}" width="${candleW}" height="${Math.max(1, h)}" fill="${col}" opacity="0.4" />`;
      }
    });
    html += `<line x1="${PAD_LEFT}" x2="${PAD_LEFT + CHART_W}" y1="${zeroY}" y2="${zeroY}" stroke="var(--hairline-bright)" stroke-width="1" opacity="0.3" />`;
    // Faint close-price line for context.
    const closePoints = view.map((b, i) => `${x(i)},${y(b.close)}`).join(' ');
    html += `<polyline points="${closePoints}" fill="none" stroke="var(--text-faint)" stroke-width="1" opacity="0.5" />`;
  } else {
    // Candles
    view.forEach((b, i) => {
      const up = b.close >= b.open;
      const col = up ? 'var(--long)' : 'var(--short)';
      html += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${col}" stroke-width="1" />`;
      html += `<rect x="${x(i) - candleW / 2}" y="${Math.min(y(b.open), y(b.close))}" width="${candleW}" height="${Math.max(1, Math.abs(y(b.close) - y(b.open)))}" fill="${col}" />`;
    });
    // EMA
    html += `<polyline points="${emaPoints}" fill="none" stroke="var(--amber)" stroke-width="1.3" opacity="0.85" />`;
  }

  // Volume profile sidebar
  vp.vols.forEach((v, i) => {
    const priceLo = vp.lo + i * vp.size;
    const priceHi = vp.lo + (i + 1) * vp.size;
    const yTop = y(priceHi);
    const yBot = y(priceLo);
    const w = (v / vp.max) * VP_W;
    const isPoc = i === vp.pocIndex;
    const inVa = vp.va.has(i);
    const fill = isPoc ? 'var(--amber)' : inVa ? 'var(--cyan-dim)' : 'var(--hairline)';
    html += `<rect x="${PAD_LEFT + CHART_W + AXIS_GAP}" y="${yTop}" width="${Math.max(1, w)}" height="${Math.max(1, yBot - yTop - 0.5)}" fill="${fill}" opacity="${isPoc ? 1 : 0.9}" />`;
  });

  html += `<line x1="${PAD_LEFT}" x2="${PAD_LEFT}" y1="${PAD_TOP}" y2="${CHART_H - PAD_BOTTOM}" stroke="var(--hairline-bright)" stroke-width="1" />`;
  html += `<line x1="${PAD_LEFT + CHART_W}" x2="${PAD_LEFT + CHART_W}" y1="0" y2="${CHART_H}" stroke="var(--hairline-bright)" stroke-width="1" />`;

  if (viewCount < total) {
    html += `<text x="${PAD_LEFT + CHART_W - 4}" y="${PAD_TOP - 10}" text-anchor="end" font-size="8" font-family="'JetBrains Mono', monospace" fill="var(--text-faint)">zoomed: ${viewCount}/${total} bars — scroll to zoom, drag to pan, dblclick to reset</text>`;
  }

  svgEl.innerHTML = html;
}

function bindInteraction(svgEl) {
  const state = getState(svgEl);
  if (state.bound) return;
  state.bound = true;

  const rerender = () => {
    if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
  };

  svgEl.addEventListener('wheel', (ev) => {
    if (!state.lastBars) return;
    ev.preventDefault();
    const total = state.lastBars.length;
    const rect = svgEl.getBoundingClientRect();
    const relX = (ev.clientX - rect.left) / rect.width; // 0..1 across the whole SVG viewBox
    const cursorFrac = state.start + relX * state.len;

    const factor = ev.deltaY > 0 ? 1.15 : 1 / 1.15;
    const minLen = Math.min(1, MIN_VIEW_BARS / total);
    const newLen = Math.max(minLen, Math.min(1, state.len * factor));
    let newStart = cursorFrac - relX * newLen;
    newStart = Math.max(0, Math.min(1 - newLen, newStart));

    state.len = newLen;
    state.start = newStart;
    rerender();
  }, { passive: false });

  let dragging = false;
  let dragStartX = 0;
  let dragStartFrac = 0;
  let downX = 0, downY = 0, downTime = 0;
  svgEl.addEventListener('mousedown', (ev) => {
    dragging = true;
    dragStartX = ev.clientX;
    dragStartFrac = state.start;
    downX = ev.clientX;
    downY = ev.clientY;
    downTime = Date.now();
  });
  window.addEventListener('mousemove', (ev) => {
    if (!dragging || !state.lastBars) return;
    const rect = svgEl.getBoundingClientRect();
    const deltaFrac = (ev.clientX - dragStartX) / rect.width * state.len;
    state.start = Math.max(0, Math.min(1 - state.len, dragStartFrac - deltaFrac));
    rerender();
  });
  window.addEventListener('mouseup', (ev) => {
    dragging = false;
    // A "click" (not a drag-pan): small movement, short duration — used to
    // zoom into a single candle's orderflow footprint (§15.2). Distinguished
    // from panning so the existing drag-to-pan gesture is unaffected.
    const moved = Math.hypot(ev.clientX - downX, ev.clientY - downY);
    if (moved < 4 && Date.now() - downTime < 500 && state.lastBars) {
      const rect = svgEl.getBoundingClientRect();
      const scaleX = TOTAL_W / rect.width;
      const svgX = (ev.clientX - rect.left) * scaleX;
      const total = state.lastBars.length;
      const startIdx = Math.max(0, Math.min(total - MIN_VIEW_BARS, Math.floor(state.start * total)));
      const viewCount = Math.max(MIN_VIEW_BARS, Math.min(total - startIdx, Math.round(state.len * total)));
      const slot = CHART_W / viewCount;
      const localIdx = Math.round((svgX - PAD_LEFT - slot / 2) / slot);
      const globalIdx = startIdx + localIdx;
      if (globalIdx >= 0 && globalIdx < total && svgX >= PAD_LEFT && svgX <= PAD_LEFT + CHART_W) {
        const bar = state.lastBars[globalIdx];
        svgEl.dispatchEvent(new CustomEvent('candleclick', { detail: { bar } }));
      }
    }
  });

  svgEl.addEventListener('dblclick', () => {
    state.start = 0;
    state.len = 1;
    rerender();
  });
}

/**
 * Render the chart. `extras.sessionMarkers` is the array returned by
 * GET /api/bars (or /api/sessions + client-side windowing).
 */
export function renderChart(svgEl, bars, prediction = null, extras = {}) {
  if (!bars || !bars.length) return;
  bindInteraction(svgEl);
  const state = getState(svgEl);
  state.lastBars = bars;
  state.lastPrediction = prediction;
  state.lastExtras = extras;
  draw(svgEl, bars, prediction, extras);
}

/** Switch between 'candles' and 'orderflow' (tick-volume proxy) view. */
export function setChartMode(svgEl, mode) {
  const state = getState(svgEl);
  state.mode = mode === 'orderflow' ? 'orderflow' : 'candles';
  if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
}

export function getChartMode(svgEl) {
  return getState(svgEl).mode;
}

/** Reset zoom/pan to show the full fetched range. */
export function resetChartView(svgEl) {
  const state = getState(svgEl);
  state.start = 0;
  state.len = 1;
  if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
}

/** Toggle the open-positions + predicted-price overlay on/off. */
export function setOverlayEnabled(svgEl, enabled) {
  const state = getState(svgEl);
  state.showOverlay = !!enabled;
  if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
}

export function getOverlayEnabled(svgEl) {
  return getState(svgEl).showOverlay;
}

/** Bulk per-candle footprint data (keyed by bar timestamp, from
 * GET /api/bars/footprint) for the Orderflow chart view — see
 * app.js's refreshFootprints(). Redraws immediately if Orderflow is active. */
export function setFootprints(svgEl, footprints) {
  const state = getState(svgEl);
  state.footprints = footprints || {};
  if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
}

/**
 * Merge new fields into the last-rendered extras (e.g. fresh `positions`
 * from a WebSocket update) and redraw immediately, without waiting for the
 * next /api/bars poll.
 */
export function updateChartExtras(svgEl, patch) {
  const state = getState(svgEl);
  state.lastExtras = { ...(state.lastExtras || {}), ...patch };
  if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
}

/** Update the prediction overlay (e.g. a fresh WebSocket `data.auto`) and redraw. */
export function updateChartPrediction(svgEl, prediction) {
  const state = getState(svgEl);
  state.lastPrediction = prediction;
  if (state.lastBars) draw(svgEl, state.lastBars, state.lastPrediction, state.lastExtras);
}
