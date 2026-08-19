/**
 * SVG chart renderer: candlesticks + EMA overlay + volume profile.
 */

const CHART_W = 620;
const AXIS_GAP = 6;
const VP_W = 120;
const TOTAL_W = CHART_W + AXIS_GAP + VP_W;
const CHART_H = 380;
const PAD_TOP = 18;
const PAD_BOTTOM = 26;

export function renderChart(svgEl, bars, prediction = null) {
  if (!bars || !bars.length) return;

  const closes = bars.map(b => b.close);

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
    const size = (hi - lo) / binCount;
    const vols = new Array(binCount).fill(0);
    bars.forEach(b => {
      const s = Math.max(0, Math.min(binCount - 1, Math.floor((b.low - lo) / size)));
      const e = Math.max(0, Math.min(binCount - 1, Math.floor((b.high - lo) / size)));
      const span = e - s + 1;
      for (let i = s; i <= e; i++) vols[i] += b.volume / span;
    });
    const max = Math.max(...vols, 1);
    const total = vols.reduce((a, b) => a + b, 0);
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

  const ema20 = ema(closes, 20);
  const vp = volumeProfile(bars, 30);

  const extra = prediction ? [prediction.entry, prediction.sl, prediction.tp] : [];
  const lows = bars.map(b => b.low);
  const highs = bars.map(b => b.high);
  let min = Math.min(...lows, ...extra);
  let max = Math.max(...highs, ...extra);
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;
  const plotH = CHART_H - PAD_TOP - PAD_BOTTOM;
  const y = (price) => PAD_TOP + ((max - price) / (max - min)) * plotH;

  const slot = CHART_W / bars.length;
  const candleW = Math.max(2, slot * 0.62);
  const x = (i) => i * slot + slot / 2;
  const emaPoints = ema20.map((v, i) => `${x(i)},${y(v)}`).join(" ");

  const last = bars[bars.length - 1];
  const priceLines = prediction
    ? [
        { label: "TP", price: prediction.tp, color: "var(--long)" },
        { label: "ENTRY", price: prediction.entry, color: "var(--cyan)" },
        { label: "SL", price: prediction.sl, color: "var(--short)" },
      ]
    : [];

  let html = '';

  // Gridlines
  [0.2, 0.4, 0.6, 0.8].forEach(f => {
    html += `<line x1="0" x2="${CHART_W}" y1="${PAD_TOP + f * plotH}" y2="${PAD_TOP + f * plotH}" stroke="var(--hairline)" stroke-width="1" />`;
  });

  // Prediction overlay
  priceLines.forEach(pl => {
    html += `<line x1="0" x2="${TOTAL_W}" y1="${y(pl.price)}" y2="${y(pl.price)}" stroke="${pl.color}" stroke-width="1" stroke-dasharray="4 3" opacity="0.85" />`;
    html += `<text x="${CHART_W + AXIS_GAP + VP_W - 2}" y="${y(pl.price) - 3}" text-anchor="end" font-size="9" font-family="'JetBrains Mono', monospace" fill="${pl.color}">${pl.label} ${pl.price.toFixed(5)}</text>`;
  });

  // Candles
  bars.forEach((b, i) => {
    const up = b.close >= b.open;
    const col = up ? 'var(--long)' : 'var(--short)';
    html += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${col}" stroke-width="1" />`;
    html += `<rect x="${x(i) - candleW / 2}" y="${Math.min(y(b.open), y(b.close))}" width="${candleW}" height="${Math.max(1, Math.abs(y(b.close) - y(b.open)))}" fill="${col}" />`;
  });

  // EMA
  html += `<polyline points="${emaPoints}" fill="none" stroke="var(--amber)" stroke-width="1.3" opacity="0.85" />`;

  // Volume profile
  vp.vols.forEach((v, i) => {
    const priceLo = vp.lo + i * vp.size;
    const priceHi = vp.lo + (i + 1) * vp.size;
    const yTop = y(priceHi);
    const yBot = y(priceLo);
    const w = (v / vp.max) * VP_W;
    const isPoc = i === vp.pocIndex;
    const inVa = vp.va.has(i);
    const fill = isPoc ? 'var(--amber)' : inVa ? 'var(--cyan-dim)' : 'var(--hairline)';
    html += `<rect x="${CHART_W + AXIS_GAP}" y="${yTop}" width="${Math.max(1, w)}" height="${Math.max(1, yBot - yTop - 0.5)}" fill="${fill}" opacity="${isPoc ? 1 : 0.9}" />`;
  });

  html += `<line x1="${CHART_W}" x2="${CHART_W}" y1="0" y2="${CHART_H}" stroke="var(--hairline-bright)" stroke-width="1" />`;

  svgEl.innerHTML = html;
}
