import { describe, expect, it } from 'vitest';
import { impliedVol, price, yearsToExpiry } from '../src/options/black76';
import { atmStrike, forwardOf, maxPain, pcr, smile, type StrikeRow } from '../src/options/chain';
import { keyOf, smileVol, syntheticFeed, syntheticQuote, syntheticSpot, unit, type ContractKey } from '../src/options/synthetic';

const IST = 19_800;
const now = Date.UTC(2026, 9, 27, 11, 0) / 1000 - IST; // Tuesday 27 October 2026, 11:00 IST
const market = { spot: 24_500, rate: 0.07, now };
const expiry = '2026-11-03';
const T = yearsToExpiry(expiry, now);
const F = 24_500 * Math.exp(0.07 * T);
const key = (kind: ContractKey['kind'], strike: number): ContractKey => ({ underlying: 'NIFTY', expiry, kind, strike });

describe('the numbers made from a name', () => {
  it('are the same every time, and between 0 and 1', () => {
    expect(unit('abc')).toBe(unit('abc'));
    expect(unit('abc')).not.toBe(unit('abd'));
    for (const t of ['', 'a', 'NIFTY|2026-11-03|call|24500', 'x'.repeat(500)]) {
      expect(unit(t)).toBeGreaterThanOrEqual(0);
      expect(unit(t)).toBeLessThan(1);
    }
  });

  it('spread evenly enough: the middle half of many names holds about half', () => {
    let inside = 0;
    for (let i = 0; i < 4000; i++) if (unit(`name${i}`) > 0.25 && unit(`name${i}`) < 0.75) inside++;
    expect(inside / 4000).toBeGreaterThan(0.45);
    expect(inside / 4000).toBeLessThan(0.55);
  });
});

describe('the synthetic spot', () => {
  it('is the same for an underlying on a day, near the known base for an index, and the same name on another day moves a little', () => {
    const day = Math.floor(now / 86_400);
    expect(syntheticSpot('NIFTY', day)).toBe(syntheticSpot('NIFTY', day));
    expect(syntheticSpot('NIFTY', day)).toBeGreaterThan(24_500 * 0.97);
    expect(syntheticSpot('NIFTY', day)).toBeLessThan(24_500 * 1.03);
    expect(Math.abs(syntheticSpot('NIFTY', day + 1) / syntheticSpot('NIFTY', day) - 1)).toBeLessThan(0.02);
  });

  it('is a plausible price for a stock name that has no base, and differs between names', () => {
    const a = syntheticSpot('RELIANCE', 20_000);
    const b = syntheticSpot('TCS', 20_000);
    expect(a).toBeGreaterThan(90);
    expect(a).toBeLessThan(5500);
    expect(a).not.toBe(b);
  });
});

describe('a synthetic quote', () => {
  it('is the same for the same contract on the same day, and finite and non-negative in every field', () => {
    for (const kind of ['call', 'put', 'future'] as const) {
      for (const strike of [22_000, 24_000, 24_500, 25_000, 27_000]) {
        const a = syntheticQuote(key(kind, strike), market);
        expect(syntheticQuote(key(kind, strike), market)).toEqual(a);
        for (const v of [a.last, a.bid ?? 0, a.ask ?? 0, a.volume, a.oi]) {
          expect(Number.isFinite(v)).toBe(true);
          expect(v).toBeGreaterThanOrEqual(0);
        }
        expect(Number.isFinite(a.oiChange)).toBe(true);
      }
    }
  });

  it('has a bid below its ask, and a last between them or at their edge', () => {
    for (const strike of [23_000, 24_000, 24_500, 25_000, 26_000]) {
      for (const kind of ['call', 'put'] as const) {
        const q = syntheticQuote(key(kind, strike), market);
        if (q.bid !== null) expect(q.bid).toBeLessThan(q.ask!);
        expect(q.last).toBeLessThanOrEqual(q.ask! + 1e-9);
        if (q.bid !== null) expect(q.last).toBeGreaterThanOrEqual(q.bid - 1e-9);
      }
    }
  });

  it('satisfies put-call parity to within the rounding of the prices', () => {
    const df = Math.exp(-0.07 * T);
    for (const strike of [23_500, 24_000, 24_500, 25_000, 25_500]) {
      const c = syntheticQuote(key('call', strike), market).last;
      const p = syntheticQuote(key('put', strike), market).last;
      expect(Math.abs(c - p - df * (F - strike))).toBeLessThan(0.11);
    }
  });

  it('gives volatilities back that are the smile it was made with, where the prices are large enough to carry them', () => {
    for (const strike of [23_500, 24_000, 24_500, 25_000, 25_500]) {
      for (const kind of ['call', 'put'] as const) {
        const q = syntheticQuote(key(kind, strike), market);
        if (q.last < 20) continue;
        const iv = impliedVol(kind, q.last, F, strike, T, 0.07);
        expect(iv.ok && iv.iv).toBeCloseTo(smileVol(F, strike, T), 3);
      }
    }
  });

  it('prices the future at the forward', () => {
    const f = syntheticQuote(key('future', 0), market);
    expect(f.last).toBeCloseTo(F, 1);
    expect(keyOf(key('future', 123))).toBe(keyOf(key('future', 456))); // a future has no strike
  });

  it('has open interest that peaks near the money and falls away, calls heavier above and puts heavier below', () => {
    const oi = (kind: 'call' | 'put', strike: number): number => syntheticQuote(key(kind, strike), market).oi;
    expect(oi('call', 24_500)).toBeGreaterThan(oi('call', 27_000) * 5);
    expect(oi('put', 24_500)).toBeGreaterThan(oi('put', 22_000) * 5);
    const heavier = [25_000, 25_200, 25_400].filter((k) => oi('call', k) > oi('put', k)).length;
    expect(heavier).toBeGreaterThanOrEqual(2);
  });

  it('changes with the day: another day, other open interest', () => {
    const a = syntheticQuote(key('call', 24_500), market);
    const b = syntheticQuote(key('call', 24_500), { ...market, now: now + 86_400 });
    expect(a.volume === b.volume && a.oiChange === b.oiChange).toBe(false);
  });
});

describe('the whole chain as the page will read it', () => {
  const strikes = Array.from({ length: 41 }, (_, i) => 23_000 + i * 100);
  const rows: StrikeRow[] = strikes.map((strike) => ({ strike, call: syntheticQuote(key('call', strike), market), put: syntheticQuote(key('put', strike), market) }));

  it('has its at-the-money strike next to the forward, a put-call ratio and a max pain strike inside the range', () => {
    const fwd = forwardOf(24_500, null, T, 0.07)!;
    expect(Math.abs(atmStrike(rows, fwd)! - fwd)).toBeLessThanOrEqual(50);
    const p = pcr(rows);
    expect(p.oi).toBeGreaterThan(0.3);
    expect(p.oi).toBeLessThan(3);
    const pain = maxPain(rows)!;
    expect(pain).toBeGreaterThanOrEqual(23_000);
    expect(pain).toBeLessThanOrEqual(27_000);
  });

  it('reads back as a smile: the volatility is lowest near the money and higher in both wings', () => {
    const fwd = forwardOf(24_500, null, T, 0.07)!;
    const points = smile(rows, fwd, T, 0.07).filter((p) => p.strike >= 23_500 && p.strike <= 25_500);
    const atm = points.reduce((a, b) => (Math.abs(a.strike - fwd) < Math.abs(b.strike - fwd) ? a : b));
    const lowest = points.reduce((a, b) => (a.iv < b.iv ? a : b));
    expect(Math.abs(lowest.strike - atm.strike)).toBeLessThanOrEqual(600); // the skew moves the minimum a little above the forward
    expect(points[0].iv).toBeGreaterThan(lowest.iv + 0.002); // low strikes dearest
    expect(points[points.length - 1].iv).toBeGreaterThan(lowest.iv);
  });

  it('is priced by the feed the same as by the function, and labelled synthetic', async () => {
    const feed = syntheticFeed(() => now);
    expect(feed.synthetic).toBe(true);
    const keys = [key('call', 24_500), key('put', 24_500), key('future', 0)];
    const got = await feed.quotes(keys);
    expect([...got.keys()]).toEqual(keys.map(keyOf));
    const spot = await feed.spot('NIFTY');
    expect(got.get(keyOf(keys[0]))).toEqual(syntheticQuote(keys[0], { spot: spot!, rate: 0.07, now }));
    expect(price('call', F, 24_500, T, smileVol(F, 24_500, T), 0.07)).toBeGreaterThan(0);
  });
});
