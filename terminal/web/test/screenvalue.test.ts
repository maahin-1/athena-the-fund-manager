import { describe, expect, it } from 'vitest';
import { COLUMNS, filterRows, noFilters, PRESETS, toRows, type Row } from '../src/screenlogic';
import { evaluate, parseQuery } from '../src/screenquery';

const cols = ['symbol', 'kind', 'name', 'pe', 'roe', 'pe_vs_industry', 'roe_vs_industry', 'price_vs_value', 'value_position', 'implied_return', 'industry_group'];
const rows: Row[] = toRows(cols, [
  ['CHEAPER', 'stock', 'Cheaper Ltd', 12, 0.2, 0.6, 0.03, -0.35, -1, 0.16, 'Steel'],
  ['SAME', 'stock', 'Same Ltd', 20, 0.12, 1.0, 0.0, 0.02, 0, 0.1, 'Steel'],
  ['DEARER', 'stock', 'Dearer Ltd', 40, 0.1, 1.8, -0.05, 0.4, 1, 0.04, 'Retail (General)'],
  ['NOGROUP', 'stock', 'No Industry Ltd', 15, 0.15, null, null, null, null, null, null],
]);
const preset = (label: string) => {
  const p = PRESETS.find((x) => x.label === label)!;
  return { ...noFilters(), ranges: p.ranges, kinds: p.kinds, trends: p.trends };
};
const run = (query: string, r: Row): boolean => {
  const parsed = parseQuery(query);
  if (!parsed.ok) throw new Error(`${query}: ${parsed.error.message}`);
  return evaluate(parsed.node, r);
};

describe('the industry and model-value columns', () => {
  it('each column the server sends has a label', () => {
    for (const name of ['industry_group', 'pe_vs_industry', 'pb_vs_industry', 'roe_vs_industry', 'value_base', 'price_vs_value', 'value_position', 'implied_return', 'valued_on']) {
      expect(COLUMNS[name], name).toBeDefined();
    }
  });

  it('the cheaper-than-industry preset keeps stocks well under their industry P/E with ROE no worse, and leaves out a stock with no industry', () => {
    expect(filterRows(rows, preset('Cheaper than its industry')).map((r) => r.symbol)).toEqual(['CHEAPER']);
  });

  it('the below-model-range preset keeps stocks priced under the low end of the recorded range, and never one with no recorded range', () => {
    expect(filterRows(rows, preset('Below model range')).map((r) => r.symbol)).toEqual(['CHEAPER']);
  });

  it('the query line reaches the new fields, with percentages typed as percent', () => {
    expect(run('price_vs_value < -20', rows[0])).toBe(true);
    expect(run('price_vs_value < -20', rows[1])).toBe(false);
    expect(run('implied_return > 12', rows[0])).toBe(true);
    expect(run('value_position = -1', rows[0])).toBe(true);
    expect(run('pe_vs_industry < 0.8 and roe_vs_industry > 0', rows[0])).toBe(true);
    expect(run('industry_group = "Retail (General)"', rows[2])).toBe(true);
    expect(run('pe_vs_industry < 5', rows[3])).toBe(false); // a missing figure never passes
  });
});
