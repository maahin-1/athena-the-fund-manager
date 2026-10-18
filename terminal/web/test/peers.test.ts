import { describe, expect, it } from 'vitest';
import { COMPARE, indicesOf, peersOf, standing } from '../src/peers';
import type { Row } from '../src/screenlogic';

const stock = (symbol: string, extra: Row = {}): Row => ({ symbol, kind: 'stock', name: symbol, sector: 'Tech', industry: null, indices: null, market_cap_cr: 1000, pe: 20, ...extra });

describe('which stocks are peers', () => {
  const rows: Row[] = [
    stock('SUBJECT', { industry: 'Software', market_cap_cr: 1000 }),
    stock('NEAR', { industry: 'Software', market_cap_cr: 900 }),
    stock('FAR', { industry: 'Software', market_cap_cr: 90_000 }),
    stock('MID', { industry: 'Software', market_cap_cr: 3000 }),
    stock('SMALL', { industry: 'Software', market_cap_cr: 300 }),
    stock('OTHERIND', { industry: 'Hardware', market_cap_cr: 1000 }),
    stock('OTHERSECTOR', { sector: 'Banks', industry: 'Banking', market_cap_cr: 1000 }),
    stock('NOCAP', { industry: 'Software', market_cap_cr: null }),
    { symbol: 'AN_ETF', kind: 'etf', name: 'etf', sector: 'Tech', industry: 'Software', market_cap_cr: 1000 },
  ];

  it('are the stocks of the same industry, closest in market cap first, the subject and unsized or non-stock rows left out', () => {
    const got = peersOf(rows, 'subject')!;
    expect(got.basis).toBe('industry');
    expect(got.group).toBe('Software');
    expect(got.peers.map((r) => r.symbol)).toEqual(['NEAR', 'MID', 'SMALL', 'FAR']); // nearness is in ratio: 3,000 is 3 times 1,000, 300 is a third of it, and a third is the farther by a little
    expect(got.subject.symbol).toBe('SUBJECT');
  });

  it('fall back to the sector when too few share the industry, and say so', () => {
    const few: Row[] = [stock('S', { industry: 'Rare', sector: 'Tech' }), stock('A', { industry: 'Rare' }), stock('B', { sector: 'Tech' }), stock('C', { sector: 'Tech' }), stock('D', { sector: 'Tech' }), stock('E', { sector: 'Tech' })];
    const got = peersOf(few, 'S')!;
    expect(got.basis).toBe('sector');
    expect(got.group).toBe('Tech');
    expect(got.peers.map((r) => r.symbol).sort()).toEqual(['A', 'B', 'C', 'D', 'E']);
  });

  it('use the sector when the industry is not known', () => {
    const got = peersOf([stock('S'), stock('A'), stock('B', { sector: 'Banks' })], 'S')!;
    expect(got.basis).toBe('sector');
    expect(got.peers.map((r) => r.symbol)).toEqual(['A']);
  });

  it('honour the limit, at least one and at most thirty, and find the subject whatever the case', () => {
    const many = [stock('S', { industry: 'X' }), ...Array.from({ length: 40 }, (_, i) => stock(`P${i}`, { industry: 'X', market_cap_cr: 1000 + i }))];
    expect(peersOf(many, 's', 5)!.peers).toHaveLength(5);
    expect(peersOf(many, 'S', 500)!.peers).toHaveLength(30);
    expect(peersOf(many, 'S', 0)!.peers).toHaveLength(1);
  });

  it('give nothing for an unknown symbol or an ETF, and no peers when nobody shares the sector', () => {
    expect(peersOf(rows, 'NOPE')).toBeNull();
    expect(peersOf(rows, 'AN_ETF')).toBeNull();
    const alone = peersOf([stock('S', { sector: 'Unique' }), stock('A', { sector: 'Other' })], 'S')!;
    expect(alone.basis).toBe('none');
    expect(alone.peers).toEqual([]);
  });

  it('take the largest first when the subject has no market cap to be near', () => {
    const got = peersOf([stock('S', { industry: 'X', market_cap_cr: null }), ...[5, 50, 500, 5000].map((c) => stock(`P${c}`, { industry: 'X', market_cap_cr: c }))], 'S')!;
    expect(got.peers.map((r) => r.symbol)).toEqual(['P5000', 'P500', 'P50', 'P5']);
  });
});

describe('the median and where the subject stands', () => {
  const rows: Row[] = [
    stock('S', { industry: 'X', pe: 10, roe: 0.2, debt_to_equity: 2 }),
    ...[
      [20, 0.1, 1],
      [30, 0.3, null],
      [-5, 0.15, 0.5],
      [40, null, 3],
      [50, 0.25, 4],
    ].map(([pe, roe, de], i) => stock(`P${i}`, { industry: 'X', pe, roe, debt_to_equity: de })),
  ];
  const got = peersOf(rows, 'S')!;

  it('the median of the peers skips missing figures', () => {
    expect(got.medians.pe).toBe(30); // -5 is a figure; 20, 30, -5, 40, 50 gives 30
    expect(got.medians.roe).toBe(0.2);
    expect(got.medians.debt_to_equity).toBe(2);
    expect(got.medians.nothing).toBeUndefined();
  });

  it('rank runs from the better end of each figure, ignoring a loss-making P/E and missing figures', () => {
    const by = Object.fromEntries(standing(got, ['pe', 'roe', 'debt_to_equity', 'market_cap_cr']).map((s) => [s.column, s]));
    expect(by.pe).toMatchObject({ value: 10, rank: 1, of: 5, better: 'low' }); // lowest of 10, 20, 30, 40, 50 (the negative one is not comparable)
    expect(by.roe).toMatchObject({ rank: 3, of: 5, better: 'high' }); // 0.3 and 0.25 are above 0.2, 0.15 and 0.1 below
    expect(by.debt_to_equity).toMatchObject({ rank: 3, of: 5, better: 'low' }); // 0.5 and 1 are lower, 3 and 4 higher, the missing one not counted
    expect(by.market_cap_cr).toMatchObject({ rank: null, better: 'none' }); // size has no better end
  });

  it('percentile is the share of peers the subject beats', () => {
    const roe = standing(got, ['roe']).find((s) => s.column === 'roe')!;
    expect(roe.beats).toBe(2);
    expect(roe.percentile).toBeCloseTo(2 / 4);
  });

  it('a subject with no figure has no rank', () => {
    const none = peersOf([stock('S', { industry: 'X', pe: null }), stock('A', { industry: 'X' }), stock('B', { industry: 'X' }), stock('C', { industry: 'X' }), stock('D', { industry: 'X' })], 'S')!;
    expect(standing(none, ['pe'])[0]).toMatchObject({ value: null, rank: null, percentile: null });
  });

  it('the figures worth comparing each say which end is better', () => {
    expect(Object.fromEntries(COMPARE.map((c) => [c.column, c.better]))).toMatchObject({ pe: 'low', roe: 'high', debt_to_equity: 'low', market_cap_cr: 'none' });
  });
});

describe('indices', () => {
  it('are the names the stock is listed in, split from the stored text', () => {
    expect(indicesOf(stock('S', { indices: 'NIFTY 50|NIFTY BANK' }))).toEqual(['NIFTY 50', 'NIFTY BANK']);
    expect(indicesOf(stock('S'))).toEqual([]);
  });
});
