// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { parse } from '../src/command';
import type { PriceChart } from '../src/fundamentals/chartbox';
import type { FundPage } from '../src/fundamentals/model';
import { FundamentalsView } from '../src/fundamentals/view';
import type { AppContext } from '../src/panels/base';

const page: FundPage = {
  symbol: 'TCS', name: 'Tata Consultancy Services', layout: 'indas', basis: 'consolidated', bases: ['consolidated'], as_of: '2026-09-30',
  price: 3200, price_note: 'warehouse close 2026-10-16',
  key: [{ label: 'Market Cap', value: 1158400, unit: 'Rs Cr', how: 'price x shares outstanding' }],
  quarters: { ends: ['2026-06-30', '2026-09-30'], rows: [{ key: 'sales', label: 'Sales', values: [70000, 71000] }] },
  annual: {
    ends: ['2025-03-31', '2026-03-31'],
    income: [{ key: 'sales', label: 'Sales', values: [255324, 267021], ttm: 283248 }, { key: 'eps', label: 'EPS in Rs', values: [134, null], ttm: null }],
    balance: [{ key: 'reserves', label: 'Reserves', values: [90000, 95000] }],
    cashflow: [],
    banking: [],
  },
  ratios: { ends: ['2025-03-31', '2026-03-31'], rows: [{ key: 'roe', label: 'ROE %', values: [47, 45.9], how: 'net profit / (equity capital + reserves)' }] },
  growth: [{ title: 'Compounded Sales Growth', rows: [{ label: '5 Years', value: 10.2 }, { label: '10 Years', value: null }] }],
  coverage: { periods: 50, first: '2019-03-31', last: '2026-09-30' },
  gaps: ['balance sheets start at FY2023; the earlier years are not held yet'],
};

function context(): AppContext {
  return {
    feed: { onQuotes: () => () => {}, need: vi.fn(), release: vi.fn(), quotes: new Map() },
    bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => undefined },
    watchlist: { items: ['TCS', 'SBIN'], subscribe: () => () => {} },
    showSymbol: vi.fn(), openResearch: vi.fn(), openFundamentals: vi.fn(),
  } as unknown as AppContext;
}

function view(): FundamentalsView {
  const chart: PriceChart = { element: document.createElement('div'), load: vi.fn(), dispose: vi.fn() };
  return new FundamentalsView(context(), { makeChart: () => chart });
}

describe('fundamentals page', () => {
  it('draws the header, key figures, five sections and a dash for a missing figure', () => {
    const v = view();
    v.render(page);
    const text = v.element.textContent ?? '';
    expect(v.element.querySelector('.fsym')?.textContent).toBe('TCS');
    expect(text).toContain('Tata Consultancy Services');
    expect(text).toContain('₹ 11,58,400 Cr');
    const sections = [...v.element.querySelectorAll('[data-section]')].map((s) => (s as HTMLElement).dataset.section);
    expect(sections).toEqual(['Quarterly Results', 'Profit & Loss', 'Balance Sheet', 'Cash Flows', 'Ratios']);
    const pl = v.element.querySelector('[data-section="Profit & Loss"]')!;
    expect(pl.textContent).toContain('2,67,021');
    expect(pl.textContent).toContain('2,83,248'); // the TTM column
    const eps = [...pl.querySelectorAll('tr')].find((r) => r.textContent?.startsWith('EPS'))!;
    expect([...eps.querySelectorAll('td')].map((c) => c.textContent)).toEqual(['EPS in Rs', '134.00', '-', '-']);
  });

  it('says which sections have nothing stored and lists the gaps', () => {
    const v = view();
    v.render(page);
    expect(v.element.querySelector('[data-section="Cash Flows"]')?.textContent).toContain('No cash flows are stored');
    expect(v.element.textContent).toContain('Not held yet: balance sheets start at FY2023');
    expect(v.element.textContent).toContain('50 periods held, Mar 2019 to Sep 2026');
  });

  it('marks the active basis, disables the one that is not stored and links the export', () => {
    const v = view();
    v.render(page);
    const on = v.element.querySelector('.basis-toggle .on') as HTMLElement;
    expect(on.dataset.basis).toBe('consolidated');
    expect((v.element.querySelector('[data-basis="standalone"]') as HTMLButtonElement).disabled).toBe(true);
    const link = v.element.querySelector('a.fexport') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/api/fund/TCS/export?basis=consolidated');
  });

  it('shows a ratio row with its formula as the hover text and the growth boxes', () => {
    const v = view();
    v.render(page);
    const roe = [...v.element.querySelectorAll('td.flabel')].find((c) => c.textContent === 'ROE %') as HTMLElement;
    expect(roe.title).toBe('net profit / (equity capital + reserves)');
    const box = v.element.querySelector('.fgrow')!;
    expect(box.textContent).toContain('Compounded Sales Growth');
    expect(box.textContent).toContain('5 Years:10%');
    expect(box.textContent).toContain('10 Years:-');
  });

  it('loads the price chart for the symbol it shows', () => {
    const chart: PriceChart = { element: document.createElement('div'), load: vi.fn(), dispose: vi.fn() };
    const v = new FundamentalsView(context(), { makeChart: () => chart });
    v.render(page);
    expect(chart.load).toHaveBeenCalledWith('TCS');
  });
});

describe('fundamentals commands', () => {
  const ctx = { layouts: [] };
  it('opens the page with or without a symbol, in either word order', () => {
    expect(parse('fund', ctx)).toEqual({ kind: 'fund', symbol: null });
    expect(parse('FUND sbin', ctx)).toEqual({ kind: 'fund', symbol: 'SBIN' });
    expect(parse('tcs fund', ctx)).toEqual({ kind: 'fund', symbol: 'TCS' });
    expect(parse('fund a;b', ctx).kind).toBe('error');
  });
});
