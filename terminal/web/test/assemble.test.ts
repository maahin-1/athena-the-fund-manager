import { describe, expect, it } from 'vitest';
import type { ChainStructure } from '../src/options/api';
import { around, assemble, chainKeys } from '../src/options/assemble';
import { yearsToExpiry } from '../src/options/black76';
import { keyOf, smileVol, syntheticFeed, syntheticQuote } from '../src/options/synthetic';

const IST = 19_800;
const now = Date.UTC(2026, 9, 27, 11, 0) / 1000 - IST;
const expiry = '2026-11-03';
const ref = (token: string) => ({ token, symbol: `X${token}`, lot: 65 });

function structure(withFuture = true, strikes = Array.from({ length: 31 }, (_, i) => 23_500 + i * 100)): ChainStructure {
  return {
    underlying: 'NIFTY', expiry, segment: 'NFO', class: 'index', lot: 65, tick: 0.05,
    future: withFuture ? ref('F') : null,
    strikes: strikes.map((strike) => ({ strike, call: ref(`C${strike}`), put: ref(`P${strike}`) })),
  };
}

async function quotesFor(s: ChainStructure) {
  const feed = syntheticFeed(() => now);
  return { quotes: await feed.quotes(chainKeys(s)), spot: await feed.spot('NIFTY') };
}

describe('the keys a chain needs prices for', () => {
  it('are every call and put and the future, none for a side a strike lacks', () => {
    const s = structure();
    s.strikes[0].put = null;
    const keys = chainKeys(s);
    expect(keys).toHaveLength(31 * 2 - 1 + 1);
    expect(keys.filter((k) => k.kind === 'future')).toHaveLength(1);
    expect(keys.some((k) => k.kind === 'put' && k.strike === 23_500)).toBe(false);
    expect(chainKeys(structure(false)).some((k) => k.kind === 'future')).toBe(false);
  });
});

describe('a chain assembled from a synthetic market', () => {
  it('reads the forward from the future, finds the at-the-money strike beside it and marks in-the-money sides', async () => {
    const s = structure();
    const { quotes, spot } = await quotesFor(s);
    const v = assemble(s, quotes, spot, now, 0.07);
    expect(v.forwardFrom).toBe('future');
    expect(Math.abs(v.forward! - spot! * Math.exp(0.07 * v.T))).toBeLessThan(0.2);
    expect(Math.abs(v.atm! - v.forward!)).toBeLessThanOrEqual(50);
    expect(v.rows.filter((r) => r.atm)).toHaveLength(1);
    for (const r of v.rows) {
      expect(r.itmCall).toBe(r.strike < v.forward!);
      expect(r.itmPut).toBe(r.strike > v.forward!);
      expect(r.itmCall && r.itmPut).toBe(false);
    }
    expect(v.rows).toHaveLength(31);
    expect(v.T).toBeCloseTo(yearsToExpiry(expiry, now), 12);
    expect(v.lot).toBe(65);
  });

  it('works out each side\'s volatility and Greeks, and they are the smile it was made with where prices carry it', async () => {
    const s = structure();
    const { quotes, spot } = await quotesFor(s);
    const v = assemble(s, quotes, spot, now, 0.07);
    for (const r of v.rows) {
      for (const side of [r.call, r.put]) {
        if (!side?.iv) continue;
        const price = side.quote!.last!;
        if (price < 20) continue;
        expect(side.iv).toBeCloseTo(smileVol(v.forward!, r.strike, v.T), 3);
        expect(side.greeks).not.toBeNull();
      }
    }
    const atm = v.rows.find((r) => r.atm)!;
    expect(atm.call!.greeks!.delta).toBeGreaterThan(0.4);
    expect(atm.call!.greeks!.delta).toBeLessThan(0.6);
    expect(atm.put!.greeks!.delta).toBeLessThan(-0.4);
    expect(atm.call!.greeks!.gamma).toBeCloseTo(atm.put!.greeks!.gamma, 4);
  });

  it('has statistics: a put-call ratio, max pain in range, an at-the-money volatility near the smile and a smile of the out-of-the-money options', async () => {
    const s = structure();
    const { quotes, spot } = await quotesFor(s);
    const v = assemble(s, quotes, spot, now, 0.07);
    expect(v.pcr.oi).toBeGreaterThan(0.3);
    expect(v.pcr.oi).toBeLessThan(3);
    expect(v.maxPain!).toBeGreaterThanOrEqual(23_500);
    expect(v.maxPain!).toBeLessThanOrEqual(26_500);
    expect(v.atmIv!).toBeCloseTo(smileVol(v.forward!, v.atm!, v.T), 2);
    expect(v.smile.length).toBeGreaterThan(10);
    for (const p of v.smile) expect(p.side).toBe(p.strike >= v.forward! ? 'call' : 'put');
  });

  it('reads the forward from the option prices (put-call parity) when the expiry has no future, and says so', async () => {
    const s = structure(false);
    const { quotes, spot } = await quotesFor(s);
    const v = assemble(s, quotes, spot, now, 0.07);
    expect(v.forwardFrom).toBe('parity');
    expect(Math.abs(v.forward! - spot! * Math.exp(0.07 * v.T))).toBeLessThan(0.5); // the synthetic prices were made from that forward, to the tick
    expect(v.future).toBeNull();
  });

  it('follows the option prices, not a spot taken at another time, when there is no future', async () => {
    const s = structure(false);
    const { quotes, spot } = await quotesFor(s);
    const stale = assemble(s, quotes, spot! * 1.01, now, 0.07); // spot 1% off the prices: inside the 5% that parity is trusted
    const exact = assemble(s, quotes, spot, now, 0.07);
    expect(stale.forward).toBeCloseTo(exact.forward!, 6);
    expect(stale.forwardFrom).toBe('parity');
  });

  it('uses spot grown at the rate when no strike has both a call and a put price, and when the implied forward is far from it', async () => {
    const s = structure(false, [24_000, 24_500]);
    const none = assemble(s, new Map(), 24_500, now, 0.07);
    expect(none.forwardFrom).toBe('spot');
    expect(none.forward).toBeCloseTo(24_500 * Math.exp(0.07 * none.T), 10);
    const { quotes, spot } = await quotesFor(structure(false));
    const wild = assemble(structure(false), quotes, spot! * 1.2, now, 0.07); // spot 20% away from the prices: the prices are not believed
    expect(wild.forwardFrom).toBe('spot');
  });
});

describe('a chain with less than everything', () => {
  it('says why there is no volatility for a side with no price or an impossible one, and still shows the row', () => {
    const s = structure(true, [24_000, 24_500]);
    const spot = 24_500;
    const quotes = new Map([
      [keyOf({ underlying: 'NIFTY', expiry, kind: 'future', strike: 0 }), syntheticQuote({ underlying: 'NIFTY', expiry, kind: 'future', strike: 0 }, { spot, rate: 0.07, now })],
      [keyOf({ underlying: 'NIFTY', expiry, kind: 'call', strike: 24_000 }), { last: 1, bid: null, ask: null, volume: 0, oi: 5, oiChange: 0 }], // far below the intrinsic value
    ]);
    const v = assemble(s, quotes, spot, now, 0.07);
    expect(v.rows).toHaveLength(2);
    expect(v.rows[0].call!.iv).toBeNull();
    expect(v.rows[0].call!.ivNote).toMatch(/below the option's intrinsic/);
    expect(v.rows[0].put!.ivNote).toBe('no price');
    expect(v.rows[0].put!.quote).toBeNull();
    expect(v.rows[0].call!.greeks).toBeNull();
    expect(v.pcr.oi).not.toBeNull();
  });

  it('has no forward, volatilities or at-the-money strike when there is neither spot nor a future', () => {
    const s = structure(false, [24_000, 24_500]);
    const v = assemble(s, new Map(), null, now, 0.07);
    expect(v.forward).toBeNull();
    expect(v.forwardFrom).toBeNull();
    expect(v.atm).toBeNull();
    expect(v.rows[0].call!.ivNote).toBe('no forward price');
    expect(v.rows.every((r) => !r.atm && !r.itmCall && !r.itmPut)).toBe(true);
    expect(v.smile).toEqual([]);
    expect(v.maxPain).toBeNull();
  });

  it('keeps a strike that has only one side', () => {
    const s = structure();
    s.strikes[3].put = null;
    return quotesFor(s).then(({ quotes, spot }) => {
      const v = assemble(s, quotes, spot, now, 0.07);
      expect(v.rows[3].put).toBeNull();
      expect(v.rows[3].call).not.toBeNull();
    });
  });
});

describe('the strikes shown around the money', () => {
  it('are the count either side of the at-the-money strike, fewer at an end of the list, or all', async () => {
    const s = structure();
    const { quotes, spot } = await quotesFor(s);
    const v = assemble(s, quotes, spot, now, 0.07);
    const at = v.rows.findIndex((r) => r.atm);
    const ten = around(v.rows, 10);
    expect(ten).toHaveLength(21);
    expect(ten[10].atm).toBe(true);
    expect(around(v.rows, 'all')).toHaveLength(31);
    expect(around(v.rows, 1000)).toHaveLength(31);
    expect(around(v.rows, 2).map((r) => r.strike)).toEqual(v.rows.slice(at - 2, at + 3).map((r) => r.strike));
    const edge = around(v.rows.slice(0, 3), 10);
    expect(edge).toHaveLength(3);
  });

  it('are the first few when there is no at-the-money strike', () => {
    const v = assemble(structure(false), new Map(), null, now, 0.07);
    expect(around(v.rows, 3)).toHaveLength(7);
  });
});
