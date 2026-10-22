// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { parse } from '../src/command';
import type { PriceChart } from '../src/fundamentals/chartbox';
import type { FundPage } from '../src/fundamentals/model';
import { FundamentalsView, safeHref } from '../src/fundamentals/view';
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
  shareholding: {
    ends: ['2026-03-31', '2026-06-30'],
    rows: [
      { key: 'promoters', label: 'Promoters', values: [71.77, 71.77] },
      { key: 'fii', label: 'FIIs', values: [9.1, 9.06] },
      { key: 'holders', label: 'No. of Shareholders', values: [2600000, 2605182] },
    ],
  },
  documents: {
    annual_reports: [{ title: 'FY2025-26', category: 'Annual report', date: '2026-05-15', url: 'https://nsearchives.nseindia.com/annual_reports/AR_1.pdf', size: '16.62 MB' }],
    concalls: [{ title: 'Transcript', category: 'Call', date: '2026-10-14', url: 'https://nsearchives.nseindia.com/corporate/b.pdf', size: '' }],
    announcements: [{ title: 'Bad link', category: 'Updates', date: '2026-10-01', url: 'https://evil.example/x.pdf', size: '' }],
  },
  growth: [{ title: 'Compounded Sales Growth', rows: [{ label: '5 Years', value: 10.2 }, { label: '10 Years', value: null }] }],
  coverage: { periods: 50, first: '2019-03-31', last: '2026-09-30' },
  gaps: ['balance sheets start at FY2023; the earlier years are not held yet'],
  history: [{ symbol: 'OLDTCS', note: 'OLDTCS was renamed TCS in 2020.', first: '2019-03-31', last: '2020-03-31' }],
};

/** A stand-in for the stored market screen holding these rows (loaded when there are any). */
function market(rows: Record<string, unknown>[] = []) {
  return { subscribe: () => () => {}, loaded: rows.length > 0, rows: () => rows, get: (s: string) => rows.find((r) => r.symbol === s) };
}

function context(rows: Record<string, unknown>[] = [], openFundamentals = vi.fn()): AppContext {
  return {
    market: market(rows),
    feed: { onQuotes: () => () => {}, need: vi.fn(), release: vi.fn(), quotes: new Map() },
    bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => undefined },
    watchlist: { items: ['TCS', 'SBIN'], subscribe: () => () => {} },
    showSymbol: vi.fn(), openResearch: vi.fn(), openFundamentals,
  } as unknown as AppContext;
}

function view(rows: Record<string, unknown>[] = [], openFundamentals = vi.fn()): FundamentalsView {
  const chart: PriceChart = { element: document.createElement('div'), load: vi.fn(), dispose: vi.fn() };
  return new FundamentalsView(context(rows, openFundamentals), { makeChart: () => chart });
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
    expect(sections).toEqual(['Peers', 'Valuation', 'Quarterly Results', 'Profit & Loss', 'Balance Sheet', 'Cash Flows', 'Ratios', 'Shareholding', 'Documents']);
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

  it('shows the shareholding pattern with percentages and the number of shareholders', () => {
    const v = view();
    v.render(page);
    const text = v.element.querySelector('[data-section="Shareholding"]')?.textContent ?? '';
    expect(text).toContain('Promoters71.77%71.77%');
    expect(text).toContain('FIIs9.10%9.06%');
    expect(text).toContain('No. of Shareholders26,00,0002,605,182'.replace('2,605,182', '26,05,182'));
  });

  it('lists documents as links on the exchange archive only, opened safely, and shows anything else as text', () => {
    const v = view();
    v.render(page);
    const box = v.element.querySelector('[data-section="Documents"]')!;
    const links = [...box.querySelectorAll('a')] as HTMLAnchorElement[];
    expect(links.map((a) => a.textContent)).toEqual(['FY2025-26', 'Transcript']);
    expect(links.every((a) => a.target === '_blank' && a.rel.includes('noopener') && a.href.startsWith('https://nsearchives.nseindia.com/'))).toBe(true);
    expect(box.textContent).toContain('Updates: Bad link'); // listed, but not a link
    expect(box.textContent).toContain('2026-05-15 · 16.62 MB');
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref('https://nsearchives.nseindia.com/a b.pdf')).toBeNull();
    expect(safeHref('https://nsearchives.nseindia.com/annual_reports/AR_1.pdf')).not.toBeNull();
  });

  it('tells which earlier symbols are included and why', () => {
    const v = view();
    v.render(page);
    const note = v.element.querySelector('.fhistory')?.textContent ?? '';
    expect(note).toContain('Includes OLDTCS (Mar 2019 to Mar 2020)');
    expect(note).toContain('renamed TCS in 2020');
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

describe('the peers section', () => {
  const stock = (symbol: string, extra: Record<string, unknown> = {}) => ({
    symbol, name: `${symbol} Ltd`, kind: 'stock', sector: 'Technology', industry: 'Information Technology', indices: null, close: 100, market_cap_cr: 1000, pe: 20, pb: 3, roe: 0.2,
    net_margin: 0.1, debt_to_equity: 0.5, dividend_yield: 0.02, revenue_growth: 0.1, ret_1y: 0.1, ...extra,
  });
  const rows = [
    stock('TCS', { market_cap_cr: 890000, pe: 18, indices: 'NIFTY 50|NIFTY IT' }),
    ...[900000, 600000, 500000, 300000, 200000, 100000, 50000, 20000].map((c, i) => stock(`PEER${i + 1}`, { market_cap_cr: c, pe: 20 + i * 5 })),
    stock('BANK1', { sector: 'Financial Services', industry: 'Banks' }),
  ];

  it('shows where the company sits, itself first, its nearest peers, and the median of them', () => {
    const v = view(rows);
    v.render(page);
    const box = v.element.querySelector('.fpeers')!;
    expect(box.getAttribute('data-section')).toBe('Peers');
    expect(box.querySelector('.fcrumbs')?.textContent).toBe('Technology › Information Technology');
    expect([...box.querySelectorAll('.fchip')].map((c) => c.textContent)).toEqual(['NIFTY 50', 'NIFTY IT']);
    expect(box.textContent).toContain('the other stocks in the exchange\'s industry "Information Technology"');
    const lines = [...box.querySelectorAll('.fpeer-table tbody tr')];
    expect(lines[0].className).toBe('subject');
    expect(lines[0].textContent).toContain('TCS');
    expect(lines.slice(1, 3).map((l) => l.querySelector('button')?.textContent)).toEqual(['PEER1', 'PEER2']); // nearest in market cap first
    expect(lines[lines.length - 1].className).toBe('median');
    expect(lines[lines.length - 1].textContent).toContain('Median of peers');
    expect(lines).toHaveLength(1 + 8 + 1);
  });

  it('writes the sector once when the industry has the same name, and leaves the median price blank', () => {
    const same = rows.map((r) => (r.symbol === 'BANK1' ? r : { ...r, sector: 'Information Technology' }));
    const v = view(same);
    v.render(page);
    expect(v.element.querySelector('.fcrumbs')?.textContent).toBe('Information Technology');
    const median = v.element.querySelector('.fpeer-table tbody tr.median')!;
    expect(median.querySelectorAll('td')[1].textContent).toBe('—'); // the Close column
  });

  it('ranks the company on each figure against its peers and says which end is better', () => {
    const v = view(rows);
    v.render(page);
    const pe = [...v.element.querySelectorAll('.fagainst-table tbody tr')].find((r) => r.textContent?.startsWith('P/E'))!;
    expect(pe.textContent).toContain('1 of 9'); // the lowest P/E of the nine
    expect(pe.textContent).toContain('lower');
    const size = [...v.element.querySelectorAll('.fagainst-table tbody tr')].find((r) => r.textContent?.startsWith('Mkt cap'))!;
    expect(size.textContent).toContain('no better end');
  });

  it('opens a peer\'s page when its name is clicked', () => {
    const open = vi.fn();
    const v = view(rows, open);
    v.render(page);
    (v.element.querySelector('.fpeer-table tbody tr:nth-child(2) button') as HTMLButtonElement).click();
    expect(open).toHaveBeenCalledWith('PEER1');
  });

  it('shows more or fewer peers on request', () => {
    const v = view(rows);
    v.render(page);
    const count = v.element.querySelector('select[aria-label="number of peers"]') as HTMLSelectElement;
    count.value = '6';
    count.dispatchEvent(new Event('change'));
    expect(v.element.querySelectorAll('.fpeer-table tbody tr')).toHaveLength(1 + 6 + 1);
  });

  it('says so while the market screen has not arrived, and when the stock is not in it', () => {
    const waiting = view();
    waiting.render(page);
    expect(waiting.element.querySelector('.fpeers')?.textContent).toContain('loading the market screen');
    const missing = view([stock('OTHER')]);
    missing.render(page);
    expect(missing.element.querySelector('.fpeers')?.textContent).toContain('TCS is not a stock in the stored market screen');
  });

  it('is in the section buttons, first', () => {
    const v = view(rows);
    v.render(page);
    expect([...v.element.querySelectorAll('.fnavbtn')].map((b) => b.textContent)[0]).toBe('Peers');
  });
});

describe('the peers command', () => {
  const ctx = { layouts: [] };
  it('opens the Fundamentals page at the peers, in either word order', () => {
    expect(parse('peers', ctx)).toEqual({ kind: 'fund', symbol: null, section: 'Peers' });
    expect(parse('PEERS sbin', ctx)).toEqual({ kind: 'fund', symbol: 'SBIN', section: 'Peers' });
    expect(parse('tcs peers', ctx)).toEqual({ kind: 'fund', symbol: 'TCS', section: 'Peers' });
    expect(parse('peers a;b', ctx).kind).toBe('error');
  });
});
