import { describe, expect, it } from 'vitest';
import { toCsv } from '../src/screens/csv';
import type { Row } from '../src/screenlogic';

describe('the CSV of a screen', () => {
  const rows: Row[] = [
    { symbol: 'AAA', name: 'Alpha, Beta & "Co" Limited', pe: 12.3456789, roe: 0.152, close: 100, flags: 0 },
    { symbol: 'BBB', name: 'Beta', pe: null, roe: null, close: 5.5, flags: 2 },
  ];

  it('has a header of the shown labels, percentages marked, and one row per stock', () => {
    const lines = toCsv(rows, ['symbol', 'name', 'pe', 'roe']).split('\r\n').filter(Boolean);
    expect(lines[0]).toBe('Symbol,Name,P/E,ROE (%)');
    expect(lines).toHaveLength(3);
  });

  it('writes percentages in percent, numbers as they are, missing figures empty, and quotes what needs quoting', () => {
    const lines = toCsv(rows, ['symbol', 'name', 'pe', 'roe']).split('\r\n');
    expect(lines[1]).toBe('AAA,"Alpha, Beta & ""Co"" Limited",12.3457,15.2');
    expect(lines[2]).toBe('BBB,Beta,,');
  });

  it('ends with a line break for tools that expect one, and gives only the header for no rows', () => {
    expect(toCsv(rows, ['symbol']).endsWith('\r\n')).toBe(true);
    expect(toCsv([], ['symbol', 'pe'])).toBe('Symbol,P/E\r\n');
  });

  it('keeps text that could be read as a formula from being one in a spreadsheet', () => {
    const csv = toCsv([{ symbol: '=1+1', name: '+x' }], ['symbol', 'name']);
    expect(csv).toContain("'=1+1");
    expect(csv).toContain("'+x");
  });
});
