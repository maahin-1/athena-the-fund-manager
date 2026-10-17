import { ColorType, createChart, CrosshairMode, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import type { Bar } from '../bars';
import { el } from '../panels/base';

/** Daily bars per range; the page keeps to daily closes (the full chart is the terminal's). */
export const PRICE_RANGES: { label: string; bars: number | null }[] = [
  { label: '1M', bars: 22 }, { label: '6M', bars: 126 }, { label: '1Yr', bars: 252 }, { label: '3Yr', bars: 756 },
  { label: '5Yr', bars: 1260 }, { label: '10Yr', bars: 2520 }, { label: 'Max', bars: null },
];

export interface PriceChart {
  element: HTMLElement;
  load(symbol: string): void;
  dispose(): void;
}

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}

/** The closing-price line with the range buttons the company page shows above its tables. */
export function makePriceChart(): PriceChart {
  const element = el('div', 'fchart');
  const rangeRow = el('div', 'ranges');
  const host = el('div', 'chart-host fchart-host');
  const note = el('div', 'note');
  element.append(rangeRow, host, note);
  let range = PRICE_RANGES[2];
  let symbol = '';
  let token = 0;
  let chart: IChartApi | null = null;
  let line: ISeriesApi<'Line'> | null = null;
  let disposed = false;

  const paintRanges = () => rangeRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.range === range.label));
  for (const r of PRICE_RANGES) {
    const b = el('button', 'rbtn', r.label);
    b.dataset.range = r.label;
    b.addEventListener('click', () => {
      range = r;
      paintRanges();
      load(symbol);
    });
    rangeRow.append(b);
  }
  paintRanges();

  const make = () => {
    chart = createChart(host, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css('--bg-panel') }, textColor: css('--fg-dim'), fontFamily: 'inherit', attributionLogo: false },
      grid: { vertLines: { color: css('--grid') }, horzLines: { color: css('--grid') } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: css('--border') },
      timeScale: { borderColor: css('--border'), fixLeftEdge: true, fixRightEdge: true },
    });
    line = chart.addSeries(LineSeries, { color: css('--accent'), lineWidth: 2, priceLineVisible: true });
  };

  function load(next: string): void {
    symbol = next;
    if (!symbol || disposed) return;
    if (!chart) make();
    const mine = ++token;
    const limit = range.bars ?? 5000;
    note.textContent = '';
    fetch(`/api/bars?symbol=${encodeURIComponent(symbol)}&tf=1D&limit=${limit}`)
      .then((r) => r.json() as Promise<{ bars: Bar[]; note?: string }>)
      .then((body) => {
        if (mine !== token || disposed || !line) return;
        line.setData(body.bars.map((b) => ({ time: b.t as UTCTimestamp, value: b.c })));
        chart?.timeScale().fitContent();
        note.textContent = body.bars.length ? (body.note ?? '') : `no price history for ${symbol}`;
      })
      .catch(() => {
        if (mine === token) note.textContent = 'could not reach the server';
      });
  }

  return {
    element,
    load,
    dispose: () => {
      disposed = true;
      chart?.remove();
      chart = null;
    },
  };
}
