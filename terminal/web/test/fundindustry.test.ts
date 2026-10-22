// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import type { PriceChart } from '../src/fundamentals/chartbox';
import { industryNotes, industryTable, versusCell } from '../src/fundamentals/industry';
import type { FundPage, IndustryBlock } from '../src/fundamentals/model';
import { FundamentalsView } from '../src/fundamentals/view';
import type { AppContext } from '../src/panels/base';

const block: IndustryBlock = {
  name: 'Computer Services', method: 'nse_ticker', confidence: 1, n_firms: 196, data_as_of: '2026-01-05', flags: [], source: 'Damodaran Online, India industry tables',
  rows: [
    { label: 'P/E', company: 22.4, industry: 24.4, unit: 'x', relation: 'ratio', compare: 0.92, how: 'market cap / TTM net profit' },
    { label: 'ROE %', company: 45.9, industry: 27.1, unit: '%', relation: 'gap', compare: 18.7, how: 'net profit / equity' },
    { label: 'Operating margin % (before depreciation)', company: 20, industry: 25, unit: '%', relation: 'gap', compare: -5, how: 'TTM' },
  ],
};

const basePage = {
  symbol: 'TCS', name: 'Tata Consultancy Services', layout: 'indas', basis: 'consolidated', bases: ['consolidated'], as_of: '2026-09-30', price: 3200, price_note: '',
  key: [], quarters: { ends: [], rows: [] }, annual: { ends: [], income: [], balance: [], cashflow: [], banking: [] }, ratios: { ends: [], rows: [] }, growth: [],
  documents: { annual_reports: [], concalls: [], announcements: [] }, shareholding: { ends: [], rows: [] }, coverage: null, gaps: [], history: [],
} as unknown as FundPage;

function view(): FundamentalsView {
  const chart: PriceChart = { element: document.createElement('div'), load: vi.fn(), dispose: vi.fn() };
  const ctx = {
    market: { subscribe: () => () => {}, loaded: false, rows: () => [], get: () => undefined },
    feed: { onQuotes: () => () => {}, need: vi.fn(), release: vi.fn(), quotes: new Map() },
    bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => undefined },
    watchlist: { items: ['TCS'], subscribe: () => () => {} },
    showSymbol: vi.fn(), openResearch: vi.fn(), openFundamentals: vi.fn(),
  } as unknown as AppContext;
  return new FundamentalsView(ctx, { makeChart: () => chart });
}

describe('company against its industry', () => {
  it('writes a multiple as a ratio and a return or margin as a gap in points, coloured only for the gap', () => {
    expect(versusCell(block.rows[0])).toEqual({ text: '0.92x', cls: '' });
    expect(versusCell(block.rows[1])).toEqual({ text: '+18.7 pts', cls: 'up' });
    expect(versusCell(block.rows[2])).toEqual({ text: '-5.0 pts', cls: 'dn' });
  });

  it('builds the table with the company, the industry and the comparison for each measure', () => {
    const table = industryTable(block);
    expect(table.head).toEqual(['This company', 'Industry', 'Versus']);
    expect(table.rows[0].cells.map((c) => c.text)).toEqual(['22.4x', '24.4x', '0.92x']);
    expect(table.rows[1].cells.map((c) => c.text)).toEqual(['45.9%', '27.1%', '+18.7 pts']);
    expect(table.rows[0].how).toBe('market cap / TTM net profit');
  });

  it('says which industry, how many firms, which table date and how the company was matched, plus any caution', () => {
    const lines = industryNotes({ ...block, flags: ['small sample: 6 firms'] });
    expect(lines[0]).toContain('Computer Services (196 firms in the table dated 2026-01-05; matched by NSE symbol)');
    expect(lines).toContain('Caution: small sample: 6 firms.');
    expect(industryNotes({ ...block, method: 'crosswalk', confidence: 0.4 })[0]).toContain('matched by crosswalk at confidence 40%');
  });

  it('shows an Industry section and a nav button only when the page carries industry rows', () => {
    const withBlock = view();
    withBlock.render({ ...basePage, industry: block });
    expect(withBlock.element.querySelector('[data-section="Industry"]')?.textContent).toContain('This company');
    expect([...withBlock.element.querySelectorAll('.fnavbtn')].map((b) => b.textContent)).toContain('Industry');
    const without = view();
    without.render({ ...basePage, industry: null });
    expect(without.element.querySelector('[data-section="Industry"]')).toBeNull();
    expect([...without.element.querySelectorAll('.fnavbtn')].map((b) => b.textContent)).not.toContain('Industry');
  });
});
