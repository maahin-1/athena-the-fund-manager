import { describe, expect, it } from 'vitest';
import { formatCell } from '../src/screens/format';
import { ScreenStore } from '../src/screenrows';

describe('a screen figure as text', () => {
  it('shows a missing figure as a dash and never as zero', () => {
    expect(formatCell(null, 'number', 'pe')).toEqual(['—', 'flat']);
    expect(formatCell(undefined as never, 'pct', 'roe')).toEqual(['—', 'flat']);
  });

  it('writes a fraction as a percent, toned only for the moves', () => {
    expect(formatCell(0.152, 'pct', 'roe')[1]).toBe('');
    expect(formatCell(0.05, 'pct', 'ret_1y')[1]).toBe('up');
    expect(formatCell(-0.05, 'pct', 'ret_1y')[1]).toBe('dn');
    expect(formatCell(0, 'pct', 'revenue_growth')[1]).toBe('flat');
  });

  it('writes numbers to two places, big whole numbers in the Indian grouping, and text without underscores', () => {
    expect(formatCell(12.3456, 'number', 'pe')[0]).toBe('12.35');
    expect(formatCell(60000, 'number0', 'market_cap_cr')[0]).toBe('60,000');
    expect(formatCell('aligned_up', 'text', 'trend')).toEqual(['aligned up', 'up']);
    expect(formatCell('Banks', 'text', 'industry')).toEqual(['Banks', '']);
  });
});

describe('every row of the stored screen', () => {
  it('is handed out as a list, for peers to be chosen from', async () => {
    const body = { columns: ['symbol', 'kind', 'pe'], rows: [['AAA', 'stock', 10], ['BBB', 'stock', null]], market_day: '2026-10-16' };
    const store = new ScreenStore((async () => new Response(JSON.stringify(body))) as typeof fetch);
    expect(store.rows()).toEqual([]); // nothing yet: the load has been started, not finished
    await store.load();
    expect(store.rows().map((r) => r.symbol)).toEqual(['AAA', 'BBB']);
  });
});
