import { describe, expect, it } from 'vitest';
import { fmtCrore, fmtPct, fmtPlain, growthClass, periodLabel } from '../src/fundamentals/format';
import type { FundPage } from '../src/fundamentals/model';
import { formatterFor, growthBlocks, keyBlocks, tableFrom } from '../src/fundamentals/tables';

describe('fundamentals number format', () => {
  it('groups crores the Indian way and writes a missing figure as a dash', () => {
    expect(fmtCrore(1234567.8)).toBe('12,34,568');
    expect(fmtCrore(240893)).toBe('2,40,893');
    expect(fmtCrore(12.345)).toBe('12.35');
    expect(fmtCrore(-5400.2)).toBe('-5,400');
    expect(fmtCrore(null)).toBe('-');
    expect(fmtCrore(undefined)).toBe('-');
    expect(fmtCrore(Number.NaN)).toBe('-');
  });
  it('writes plain figures and percentages to the digits asked', () => {
    expect(fmtPlain(136.0123)).toBe('136.01');
    expect(fmtPlain(1234.5, 0)).toBe('1,235');
    expect(fmtPlain(null)).toBe('-');
    expect(fmtPct(45.89)).toBe('45.9%');
    expect(fmtPct(null)).toBe('-');
  });
  it('labels a period end by month and year and leaves odd input alone', () => {
    expect(periodLabel('2024-03-31')).toBe('Mar 2024');
    expect(periodLabel('2026-09-30')).toBe('Sep 2026');
    expect(periodLabel('soon')).toBe('soon');
  });
  it('colours a change', () => {
    expect(growthClass(-0.4)).toBe('dn');
    expect(growthClass(3)).toBe('up');
    expect(growthClass(0)).toBe('flat');
    expect(growthClass(null)).toBe('flat');
  });
});

describe('fundamentals tables', () => {
  const rows = [
    { key: 'sales', label: 'Sales', values: [146463, null, 240893], ttm: 283248 },
    { key: 'opm', label: 'OPM %', values: [27.11, 25, 26.4], ttm: null },
    { key: 'net_profit', label: 'Net Profit', values: [-12, 5, 9], ttm: 20 },
  ];
  it('keeps a missing cell as a dash, formats each row for its kind and marks negatives', () => {
    const t = tableFrom(['2019-03-31', '2020-03-31', '2021-03-31'], rows, { ttm: true });
    expect(t.head).toEqual(['Mar 2019', 'Mar 2020', 'Mar 2021', 'TTM']);
    expect(t.rows[0].cells.map((c) => c.text)).toEqual(['1,46,463', '-', '2,40,893', '2,83,248']);
    expect(t.rows[1].cells.map((c) => c.text)).toEqual(['27.1%', '25.0%', '26.4%', '-']);
    expect(t.rows[2].cells[0]).toEqual({ text: '-12.00', cls: 'dn' });
  });
  it('has no TTM column unless asked and carries the formula of a ratio row', () => {
    const t = tableFrom(['2024-03-31'], [{ key: 'roe', label: 'ROE %', values: [45.9], how: 'net profit / equity' }]);
    expect(t.head).toEqual(['Mar 2024']);
    expect(t.rows[0].how).toBe('net profit / equity');
    expect(formatterFor('debtor_days')(41.6)).toBe('42');
    expect(formatterFor('unknown_key')(1500)).toBe('1,500');
  });
  it('writes key figures for their unit and growth boxes with colours', () => {
    const page = {
      key: [
        { label: 'Market Cap', value: 1158400, unit: 'Rs Cr', how: 'price x shares' },
        { label: 'Stock P/E', value: 22.45, unit: 'x', how: 'market cap / profit' },
        { label: 'ROE %', value: 45.89, unit: '%', how: 'x' },
        { label: 'Current Price', value: 3200, unit: 'Rs', how: 'x' },
      ],
    } as unknown as FundPage;
    expect(keyBlocks(page).map((b) => b.text)).toEqual(['₹ 11,58,400 Cr', '22.45', '45.9%', '₹ 3,200.00']);
    const g = growthBlocks([{ title: 'Compounded Sales Growth', rows: [{ label: '5 Years', value: 10.2 }, { label: '10 Years', value: null }, { label: 'TTM', value: -2.1 }] }]);
    expect(g[0].rows.map((r) => [r.text, r.cls])).toEqual([['10%', 'up'], ['-', 'flat'], ['-2%', 'dn']]);
  });
});
