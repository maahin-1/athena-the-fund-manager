// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import type { PriceChart } from '../src/fundamentals/chartbox';
import type { FundPage, ValuationDoc } from '../src/fundamentals/model';
import { barPositions, impliedLine, valuationBlock } from '../src/fundamentals/valuation';
import { FundamentalsView } from '../src/fundamentals/view';
import type { AppContext } from '../src/panels/base';

const doc: ValuationDoc = {
  symbol: 'TCS', basis: 'consolidated', ok: true, model: 'fcff', missing: [], price: 2529, industry: 'Computer Services', low: 1172.4, base: 1692.3, high: 2695.2, price_to_base: 1.49,
  scenarios: [
    { name: 'bear', value: 1172.4, growth: 0.0329, margin: 0.1674, cost: 0.142, roe: null, stable_roe: null },
    { name: 'base', value: 1692.3, growth: 0.0658, margin: 0.2066, cost: 0.1218, roe: null, stable_roe: null },
    { name: 'bull', value: 2695.2, growth: 0.1187, margin: 0.2458, cost: 0.1031, roe: null, stable_roe: null },
  ],
  implied_growth: 0.165, implied_margin: 0.3, company_growth: 0.058, industry_growth: 0.0736, justified_price_to_book: null,
  grid: { growth: [0.02, 0.04, 0.0658, 0.08, 0.1], margin: [0.16, 0.18, 0.2066, 0.22, 0.24], values: Array.from({ length: 5 }, (_, i) => Array.from({ length: 5 }, (_, j) => 1000 + i * 100 + j * 50)) },
  cost_of_capital: { firm_kind: 'large', rating: 'Aaa/AAA', unlevered_beta: 0.736, levered_beta: 0.736, riskfree_inr: 0.0698, inr: { cost_of_equity: 0.1218, pre_tax_cost_of_debt: 0.0918, cost_of_capital: 0.1218 }, low: 0.1031, high: 0.142 },
  assumptions: ['revenue growth 6.6% a year'], flags: ['the benchmarks are one industry\'s'],
};

const basePage = {
  symbol: 'TCS', name: 'Tata Consultancy Services', layout: 'indas', basis: 'consolidated', bases: ['consolidated'], as_of: '2026-09-30', price: 2529, price_note: '',
  key: [], quarters: { ends: [], rows: [] }, annual: { ends: [], income: [], balance: [], cashflow: [], banking: [] }, ratios: { ends: [], rows: [] }, growth: [],
  documents: { annual_reports: [], concalls: [], announcements: [] }, shareholding: { ends: [], rows: [] }, coverage: null, gaps: [], history: [],
} as unknown as FundPage;

function view(fetchValuation: (s: string, b: string) => Promise<ValuationDoc | null>): FundamentalsView {
  const chart: PriceChart = { element: document.createElement('div'), load: vi.fn(), dispose: vi.fn() };
  const ctx = {
    market: { subscribe: () => () => {}, loaded: false, rows: () => [], get: () => undefined },
    feed: { onQuotes: () => () => {}, need: vi.fn(), release: vi.fn(), quotes: new Map() },
    bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => undefined },
    watchlist: { items: ['TCS'], subscribe: () => () => {} },
    showSymbol: vi.fn(), openResearch: vi.fn(), openFundamentals: vi.fn(),
  } as unknown as AppContext;
  return new FundamentalsView(ctx, { makeChart: () => chart, fetchValuation });
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('the valuation section', () => {
  it('shows the range, the price against the base value, the cases, the cost of capital and every assumption', () => {
    const text = valuationBlock(doc).textContent ?? '';
    expect(text).toContain('Value per share: ₹ 1,172 to ₹ 2,695, base ₹ 1,692');
    expect(text).toContain('Price ₹ 2,529 is 1.49x the base value');
    expect(text).toContain('not a prediction');
    expect(text).toContain('bear');
    expect(text).toContain('Cost of capital 12.2% (10.3% to 14.2%)');
    expect(text).toContain('revenue growth 6.6% a year');
    expect(text).toContain("Caution: the benchmarks are one industry's");
  });

  it('says what the price is paying for, against the company and the industry', () => {
    expect(impliedLine(doc)).toBe("At the price of ₹ 2,529 the market is paying for revenue growth of 16.5% a year for five years; the company's last three years were 5.8% and its industry is expected to grow 7.4%.");
    expect(impliedLine({ ...doc, implied_growth: null })).toBeNull();
  });

  it('draws a five by five grid of values and a lender in terms of returns on equity', () => {
    const grid = valuationBlock(doc).querySelectorAll('table')[1];
    expect(grid.querySelectorAll('tbody tr')).toHaveLength(5);
    const bank = valuationBlock({ ...doc, model: 'excess_return', grid: null, justified_price_to_book: 0.63, scenarios: [{ name: 'base', value: 498.7, growth: 0.13, margin: null, cost: 0.155, roe: 0.134, stable_roe: 0.128 }] });
    const text = bank.textContent ?? '';
    expect(text).toContain('Return on equity');
    expect(text).toContain('puts the price to book at 0.63x');
    expect(text).not.toContain('Cost of capital 12.2%');
  });

  it('puts the range, the base and the price on one scale and survives a missing price', () => {
    const pos = barPositions(doc)!;
    expect(pos.low).toBeLessThan(pos.base);
    expect(pos.base).toBeLessThan(pos.high);
    expect(pos.price!).toBeGreaterThan(pos.base);
    expect(barPositions({ ...doc, price: null })!.price).toBeNull();
    expect(barPositions({ low: undefined, base: undefined, high: undefined, price: null })).toBeNull();
  });

  it('gives the reasons, and no number, for a company that cannot be valued', () => {
    const text = valuationBlock({ ...doc, ok: false, low: undefined, base: undefined, high: undefined, missing: ['company price'], flags: [] }).textContent ?? '';
    expect(text).toBe('No value range for TCS: company price.');
  });

  it('fills the section when the document arrives and says so when none can be had', async () => {
    const v = view(async () => doc);
    v.render(basePage);
    expect(v.element.querySelector('[data-section="Valuation"]')?.textContent).toContain('Working out the value range');
    await settle();
    expect(v.element.querySelector('[data-section="Valuation"]')?.textContent).toContain('Value per share: ₹ 1,172 to ₹ 2,695');
    const none = view(async () => null);
    none.render(basePage);
    await settle();
    expect(none.element.querySelector('[data-section="Valuation"]')?.textContent).toContain('could not be worked out');
    const broken = view(async () => {
      throw new Error('network');
    });
    broken.render(basePage);
    await settle();
    expect(broken.element.querySelector('[data-section="Valuation"]')?.textContent).toContain('could not be loaded');
  });

  it('does not fill a section for a company the person has moved away from', async () => {
    const releases: ((d: ValuationDoc) => void)[] = [];
    const v = view(() => new Promise<ValuationDoc | null>((r) => releases.push(r)));
    v.render(basePage);
    v.render({ ...basePage, symbol: 'SBIN', name: 'State Bank of India' });
    releases[0](doc); // the answer for the first company arrives after the person has moved on
    await settle();
    expect(v.element.querySelectorAll('[data-section="Valuation"]')).toHaveLength(1);
    expect(v.element.querySelector('[data-section="Valuation"]')?.textContent).toContain('Working out the value range');
    releases[1]({ ...doc, symbol: 'SBIN', low: 400, base: 500, high: 600 });
    await settle();
    expect(v.element.querySelector('[data-section="Valuation"]')?.textContent).toContain('Value per share: ₹ 400 to ₹ 600');
  });
});
