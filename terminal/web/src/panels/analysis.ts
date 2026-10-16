import { compact, pct, price } from '../format';
import { el, Panel, type AppContext } from './base';

export interface Metric {
  name: string;
  value: number | string | null;
  unit: string | null;
  window: string | null;
  note: string | null;
  group: string | null;
}

export interface Packet {
  as_of: string;
  flags: string[];
  missing: string[];
  metrics: Metric[];
}

export interface AnalysisData {
  symbol: string;
  name: string | null;
  kind: string | null;
  market_day: string | null;
  screen: Record<string, number | string | null> | null;
  technical: Packet | null;
  fundamentals: Packet | null;
  verdict: { made_at: string; data_as_of: string; close: number | null; payload: Record<string, unknown> } | null;
}

/** One fetch of the stored analysis, shared by the panels that show it. */
export async function loadAnalysis(symbol: string): Promise<AnalysisData | null> {
  try {
    const r = await fetch(`/api/analysis?symbol=${encodeURIComponent(symbol)}`);
    return r.ok ? ((await r.json()) as AnalysisData) : null;
  } catch {
    return null;
  }
}

const words = (name: string) => name.replace(/_/g, ' ');

/** A metric's value in the unit it was stored in. */
export function metricText(m: Metric): string {
  const v = m.value;
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return words(v);
  switch (m.unit) {
    case 'fraction':
      return pct(v * 100);
    case 'price':
    case 'INR':
      return price(v);
    case 'count':
    case 'days':
      return String(Math.round(v));
    default:
      return Math.abs(v) >= 1e5 ? compact(v) : v.toFixed(Math.abs(v) < 10 ? 3 : 2).replace(/\.?0+$/, '') || '0';
  }
}

/** Indian crore: 2,227 Cr, or 11.24 L Cr (lakh crore) for the largest. */
const crore = (n: unknown) => (typeof n === 'number' ? (n >= 1e5 ? `${(n / 1e5).toFixed(2)} L Cr` : `${Math.round(n).toLocaleString('en-IN')} Cr`) : '—');
const frac = (n: unknown) => (typeof n === 'number' ? pct(n * 100) : '—');
const num = (n: unknown, digits = 2) => (typeof n === 'number' ? n.toFixed(digits) : '—');
const sign = (n: unknown) => (typeof n === 'number' ? (n > 0 ? 'up' : n < 0 ? 'dn' : 'flat') : 'flat');

type Tab = 'Overview' | 'Technicals' | 'Fundamentals';

/** Stored research on the linked symbol: overview figures, the technical packet and the fundamentals packet. Needs no market data. */
export class AnalysisPanel extends Panel {
  private tab: Tab = 'Overview';
  private tabs = el('div', 'tabs');
  private content = el('div', 'content');
  private data: AnalysisData | null = null;
  private token = 0;

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.start();
  }

  protected build(): void {
    for (const t of ['Overview', 'Technicals', 'Fundamentals'] as Tab[]) {
      const b = el('button', 'tab', t);
      b.dataset.tab = t;
      b.addEventListener('click', () => {
        this.tab = t;
        this.render();
      });
      this.tabs.append(b);
    }
    this.body.append(this.tabs, this.content);
    this.render();
  }

  protected symbolChanged(symbol: string): void {
    this.setTitle(`Analysis · ${symbol}`);
    const mine = ++this.token;
    this.data = null;
    this.content.textContent = 'loading…';
    void loadAnalysis(symbol).then((d) => {
      if (mine !== this.token || this.disposed) return;
      this.data = d;
      this.render();
    });
  }

  private render(): void {
    this.tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.tab === this.tab));
    this.content.replaceChildren();
    if (!this.symbol) return void (this.content.textContent = 'pick a symbol');
    const d = this.data;
    if (!d) return void (this.content.textContent = this.token ? 'loading…' : 'no analysis available');
    if (this.tab === 'Overview') this.overview(d);
    else this.packet(this.tab === 'Technicals' ? d.technical : d.fundamentals, this.tab === 'Fundamentals');
    this.content.append(el('div', 'note', `Stored data as of ${d.market_day ?? 'unknown'} (the last loaded trading day). It does not need the market to be open.`));
  }

  private overview(d: AnalysisData): void {
    this.content.append(overviewView(d));
  }

  private packet(p: Packet | null, grouped: boolean): void {
    this.content.append(packetView(p, grouped, this.symbol, this.tab.toLowerCase()));
  }
}

function fact(table: HTMLElement, k: string, v: string, cls = ''): void {
  table.append(el('span', 'k', k), el('span', `v ${cls}`, v));
}

/** The overview figures from the stored screen row, as one block. Shared by the Analysis panel and the Research page. */
export function overviewView(d: AnalysisData): HTMLElement {
  const box = el('div', 'view');
  const s = d.screen;
  if (!s) return (box.append(el('div', 'note', `${d.symbol} has no stored screen row (it may be new, thinly traded or not in the warehouse).`)), box);
  const grid = el('div', 'facts');
  fact(grid, 'Name', String(s.name ?? d.name ?? d.symbol));
  fact(grid, 'Sector', String(s.sector ?? '—'));
  fact(grid, 'Close', typeof s.close === 'number' ? price(s.close) : '—');
  for (const [label, key] of [['1 day', 'ret_1d'], ['1 month', 'ret_1m'], ['3 months', 'ret_3m'], ['6 months', 'ret_6m'], ['1 year', 'ret_1y']] as const) {
    fact(grid, label, frac(s[key]), sign(s[key]));
  }
  fact(grid, 'From 52-week high', frac(s.from_52w_high), sign(s.from_52w_high));
  fact(grid, 'From 52-week low', frac(s.from_52w_low), sign(s.from_52w_low));
  fact(grid, 'RSI 14', num(s.rsi_14, 1));
  fact(grid, 'Trend', words(String(s.trend ?? '—')));
  fact(grid, '50 vs 200 day average', frac(s.sma50_vs_200), sign(s.sma50_vs_200));
  fact(grid, 'Traded value', crore(s.traded_value_cr));
  fact(grid, 'Market cap', crore(s.market_cap_cr));
  fact(grid, 'P/E', num(s.pe));
  fact(grid, 'P/B', num(s.pb));
  fact(grid, 'ROE', frac(s.roe));
  fact(grid, 'Net margin', frac(s.net_margin));
  fact(grid, 'Debt to equity', num(s.debt_to_equity));
  fact(grid, 'Dividend yield', frac(s.dividend_yield));
  fact(grid, 'Revenue growth', frac(s.revenue_growth), sign(s.revenue_growth));
  box.append(grid);
  return box;
}

/** A stored packet (technical or fundamentals): its warnings, its metrics (grouped when asked) and what was missing. */
export function packetView(p: Packet | null, grouped: boolean, symbol: string, label: string): HTMLElement {
  const box = el('div', 'view');
  if (!p) return (box.append(el('div', 'note', `no stored ${label} packet for ${symbol}`)), box);
  for (const f of p.flags) box.append(el('div', 'flagline', `⚠ ${f}`));
  const order: string[] = [];
  const byGroup = new Map<string, Metric[]>();
  for (const m of p.metrics) {
    const g = grouped ? (m.group ?? 'other') : '';
    if (!byGroup.has(g)) {
      byGroup.set(g, []);
      order.push(g);
    }
    byGroup.get(g)!.push(m);
  }
  for (const g of order) {
    if (g) box.append(el('div', 'group', g));
    const grid = el('div', 'facts');
    for (const m of byGroup.get(g)!) {
      const cell = el('span', 'k', words(m.name));
      if (m.window || m.note) cell.title = [m.window, m.note].filter(Boolean).join(' · ');
      grid.append(cell, el('span', 'v', metricText(m)));
    }
    box.append(grid);
  }
  if (p.missing.length) box.append(el('div', 'note', `not available: ${p.missing.map(words).join(', ')}`));
  return box;
}
