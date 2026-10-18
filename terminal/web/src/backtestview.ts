import { ColorType, createChart, createSeriesMarkers, LineSeries, type IChartApi, type SeriesMarker, type Time } from 'lightweight-charts';
import type { StrategyTree } from './formula/strategy';
import { el } from './panels/base';

export interface BacktestCurve {
  days: string[];
  strategy: number[];
  buy_and_hold: number[];
  entries: string[];
  exits: string[];
}

export interface BacktestPanelData {
  rule: string;
  description: string;
  rows: [string, string, string][]; // measure, strategy, buy and hold
  facts: string[];
  blinded_identical: boolean;
  curve: BacktestCurve | null;
}

export interface BacktestData {
  identifier: string;
  assumptions: string;
  notes: string[];
  panels: BacktestPanelData[];
}

export type BacktestResult = { ok: true; data: BacktestData } | { ok: false; error: string };

/** Replay the built-in rules, or the user's own strategy when one is given, over the symbol's history (up to eight years) against buy and hold. Takes seconds
 *  to a minute. */
export async function runBacktest(symbol: string, strategy?: StrategyTree): Promise<BacktestResult> {
  try {
    const r = await fetch('/api/backtest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(strategy ? { symbol, strategy } : { symbol }) });
    if (r.status === 400) return { ok: false, error: 'That is not a symbol.' };
    if (!r.ok) {
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      return { ok: false, error: body.error ?? 'The backtest could not be run.' };
    }
    return { ok: true, data: (await r.json()) as BacktestData };
  } catch {
    return { ok: false, error: 'Could not reach the server.' };
  }
}

const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';

/** The strategy's value of 100 invested against buy and hold, with each entry and exit marked. */
function drawCurve(host: HTMLElement, curve: BacktestCurve): IChartApi {
  const chart = createChart(host, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: cssVar('--bg-panel') }, textColor: cssVar('--fg-dim'), fontFamily: 'inherit', attributionLogo: false },
    grid: { vertLines: { color: cssVar('--grid') }, horzLines: { color: cssVar('--grid') } },
    rightPriceScale: { borderColor: cssVar('--border') },
    timeScale: { borderColor: cssVar('--border'), fixLeftEdge: true, fixRightEdge: true },
  });
  const line = (values: number[], color: string, width: 1 | 2) => {
    const s = chart.addSeries(LineSeries, { color, lineWidth: width, priceLineVisible: false, lastValueVisible: true });
    s.setData(curve.days.map((d, i) => ({ time: d as Time, value: values[i] })));
    return s;
  };
  line(curve.buy_and_hold, cssVar('--fg-dim'), 1);
  const strategy = line(curve.strategy, cssVar('--ind1'), 2);
  const days = new Set(curve.days);
  const marks: SeriesMarker<Time>[] = [
    ...curve.entries.filter((d) => days.has(d)).map((d) => ({ time: d as Time, position: 'belowBar' as const, color: cssVar('--up'), shape: 'arrowUp' as const })),
    ...curve.exits.filter((d) => days.has(d)).map((d) => ({ time: d as Time, position: 'aboveBar' as const, color: cssVar('--dn'), shape: 'arrowDown' as const })),
  ].sort((a, b) => String(a.time).localeCompare(String(b.time)));
  createSeriesMarkers(strategy, marks);
  chart.timeScale().fitContent();
  return chart;
}

/** The backtest as a card: per rule a comparison table, the facts that do not fit it, the blinding check and the curve. `dispose` frees the charts. */
export function renderBacktest(data: BacktestData): { element: HTMLElement; dispose: () => void } {
  const box = el('section', 'rcard wide backtest');
  box.dataset.section = 'Backtest';
  box.append(el('h3', '', `Backtest · ${data.identifier}`));
  for (const n of data.notes) box.append(el('div', 'flagline', n));
  const charts: IChartApi[] = [];
  for (const p of data.panels) {
    const panel = el('div', 'btpanel');
    panel.dataset.rule = p.rule;
    panel.append(el('div', 'btrule', `${p.rule} rule`), el('div', 'note', p.description));
    const table = el('table', 'bttable');
    const head = el('tr');
    for (const h of ['Measure', 'Strategy', 'Buy and hold']) head.append(el('th', h === 'Measure' ? '' : 'num', h));
    table.append(head);
    for (const [measure, strategy, hold] of p.rows) {
      const tr = el('tr');
      tr.append(el('td', '', measure), el('td', 'num', strategy), el('td', 'num', hold));
      table.append(tr);
    }
    panel.append(table);
    for (const f of p.facts) panel.append(el('div', 'note', f));
    panel.append(
      el('div', p.blinded_identical ? 'note' : 'flagline', `Blinding check (ticker removed, dates shifted 28 years, prices rescaled to 100): ${p.blinded_identical ? 'same trades' : 'DIFFERENT trades, so the rule may depend on the name or the price level'}`),
    );
    if (p.curve && p.curve.days.length) {
      const host = el('div', 'curve');
      panel.append(host);
      const curve = p.curve;
      queueMicrotask(() => charts.push(drawCurve(host, curve))); // after the card is in the page, so the chart can measure its box
    }
    box.append(panel);
  }
  box.append(el('div', 'note', data.assumptions));
  box.append(el('div', 'note', 'A backtest shows what a rule would have done, not what it will do. It is not advice.'));
  return { element: box, dispose: () => charts.forEach((c) => c.remove()) };
}
