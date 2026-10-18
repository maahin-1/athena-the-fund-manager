import { describe, expect, it } from 'vitest';
import { price } from '../src/options/black76';
import { atmIv, atmStrike, forwardOf, maxPain, pcr, quotePrice, smile, termStructure, type Quote, type StrikeRow } from '../src/options/chain';
import { breakEvens, curve, extremes, netDebit, netGreeks, payoffAtExpiry, payoffNow, slopeAbove, type Leg, type Market } from '../src/options/payoff';

const q = (oi: number, extra: Partial<Quote> = {}): Quote => ({ last: 1, bid: null, ask: null, volume: 0, oi, oiChange: 0, ...extra });
const chain = (rows: [number, number, number][]): StrikeRow[] => rows.map(([strike, callOi, putOi]) => ({ strike, call: q(callOi), put: q(putOi) }));

describe('the forward', () => {
  it('is the future when it trades, else spot grown at the rate', () => {
    expect(forwardOf(100, 102, 0.5, 0.07)).toBe(102);
    expect(forwardOf(100, null, 0.5, 0.07)).toBeCloseTo(100 * Math.exp(0.035), 12);
    expect(forwardOf(100, 0, 0.5, 0.07)).toBeCloseTo(100 * Math.exp(0.035), 12);
    expect(forwardOf(null, null, 0.5, 0.07)).toBeNull();
    expect(forwardOf(100, null, 0, 0.07)).toBeNull();
  });
});

describe('the at-the-money strike', () => {
  const rows = chain([[90, 1, 1], [100, 1, 1], [110, 1, 1]]);
  it('is the strike nearest the forward, the lower when two are equally near', () => {
    expect(atmStrike(rows, 103)).toBe(100);
    expect(atmStrike(rows, 107)).toBe(110);
    expect(atmStrike(rows, 105)).toBe(100);
    expect(atmStrike(rows, 1000)).toBe(110);
    expect(atmStrike(rows, 0)).toBe(90);
  });
  it('is null for no strikes or no forward', () => {
    expect(atmStrike([], 100)).toBeNull();
    expect(atmStrike(rows, null)).toBeNull();
  });
});

describe('put-call ratio', () => {
  it('is put open interest over call open interest, and by volume', () => {
    const rows: StrikeRow[] = [
      { strike: 100, call: q(1000, { volume: 50 }), put: q(1500, { volume: 100 }) },
      { strike: 110, call: q(3000, { volume: 150 }), put: q(500, { volume: 100 }) },
    ];
    const p = pcr(rows);
    expect(p.oi).toBeCloseTo(0.5, 12);
    expect(p.volume).toBeCloseTo(1, 12);
    expect(p.callOi).toBe(4000);
    expect(p.putOi).toBe(2000);
  });
  it('is null when the calls have none, and counts a missing side as nothing', () => {
    expect(pcr([{ strike: 100, call: null, put: q(10) }]).oi).toBeNull();
    expect(pcr([]).oi).toBeNull();
    expect(pcr([{ strike: 100, call: q(10), put: null }]).oi).toBe(0);
  });
});

describe('max pain', () => {
  it('is the strike where writers pay out least', () => {
    // calls and puts all at 100 and 110 and 120; heavy interest in the middle
    const rows = chain([[100, 100, 100], [110, 500, 500], [120, 100, 100]]);
    expect(maxPain(rows)).toBe(110);
  });
  it('follows a one-sided chain', () => {
    const rows = chain([[100, 0, 1000], [110, 0, 10], [120, 10, 0]]);
    // puts pay only below their strike and calls only above theirs: at 110 and at 120 nothing is paid; 110 is the lower
    expect(maxPain(rows)).toBe(110);
  });
  it('takes the lowest strike when two are equal, and is null with no open interest', () => {
    expect(maxPain(chain([[100, 10, 10], [110, 10, 10]]))).toBe(100);
    expect(maxPain(chain([[100, 0, 0], [110, 0, 0]]))).toBeNull();
    expect(maxPain([])).toBeNull();
  });
  it('is worked out the same whatever order the strikes arrive in', () => {
    const rows = chain([[100, 100, 100], [110, 500, 500], [120, 100, 100]]);
    expect(maxPain([...rows].reverse())).toBe(maxPain(rows));
  });
});

describe('the price a volatility is read from', () => {
  it('is the middle of a two-sided market, else the last trade, else nothing', () => {
    expect(quotePrice(q(0, { bid: 9, ask: 11, last: 5 }))).toBe(10);
    expect(quotePrice(q(0, { bid: 0, ask: 11, last: 5 }))).toBe(5);
    expect(quotePrice(q(0, { bid: 12, ask: 11, last: 5 }))).toBe(5); // crossed
    expect(quotePrice(q(0, { bid: null, ask: null, last: 7 }))).toBe(7);
    expect(quotePrice(q(0, { last: null }))).toBeNull();
    expect(quotePrice(q(0, { last: 0 }))).toBeNull();
    expect(quotePrice(null)).toBeNull();
  });
});

/** A chain priced at a smile: volatility 20% at the forward, rising 0.05% per strike point away. */
function smiling(F: number, T: number, r: number): StrikeRow[] {
  const vol = (K: number): number => 0.2 + 0.0005 * Math.abs(K - F);
  const strikes = [80, 90, 95, 100, 105, 110, 120];
  const side = (kind: 'call' | 'put', K: number): Quote => ({ last: price(kind, F, K, T, vol(K), r), bid: null, ask: null, volume: 10, oi: 100, oiChange: 0 });
  return strikes.map((K) => ({ strike: K, call: side('call', K), put: side('put', K) }));
}

describe('the volatility smile', () => {
  const F = 100;
  const T = 0.1;
  const r = 0.07;

  it('reads calls at and above the forward and puts below it, and recovers the volatilities it was priced with', () => {
    const points = smile(smiling(F, T, r), F, T, r);
    expect(points.map((p) => p.strike)).toEqual([80, 90, 95, 100, 105, 110, 120]);
    expect(points.map((p) => p.side)).toEqual(['put', 'put', 'put', 'call', 'call', 'call', 'call']);
    for (const p of points) expect(p.iv).toBeCloseTo(0.2 + 0.0005 * Math.abs(p.strike - F), 6);
  });

  it('leaves out a strike whose option has no price or no volatility', () => {
    const rows = smiling(F, T, r);
    rows[1].put = null; // 90 put missing
    rows[5].call = { last: 1000, bid: null, ask: null, volume: 0, oi: 0, oiChange: 0 }; // more than a call can be worth: no volatility
    const points = smile(rows, F, T, r);
    expect(points.map((p) => p.strike)).toEqual([80, 95, 100, 105, 120]);
  });

  it('is empty with no forward, and in order whatever order the strikes arrive in', () => {
    expect(smile(smiling(F, T, r), null, T, r)).toEqual([]);
    const rows = smiling(F, T, r);
    expect(smile([...rows].reverse(), F, T, r)).toEqual(smile(rows, F, T, r));
  });
});

describe('at-the-money volatility and its term structure', () => {
  const r = 0.07;
  it('averages the call and the put at the at-the-money strike, and uses whichever there is', () => {
    const rows = smiling(100, 0.1, r);
    expect(atmIv(rows, 100, 0.1, r)).toBeCloseTo(0.2, 6);
    rows[3].put = null;
    expect(atmIv(rows, 100, 0.1, r)).toBeCloseTo(0.2, 6);
    rows[3].call = null;
    expect(atmIv(rows, 100, 0.1, r)).toBeNull();
    expect(atmIv(rows, null, 0.1, r)).toBeNull();
  });

  it('gives the at-the-money volatility of each expiry in order, leaving out one with none', () => {
    const near = { expiry: '2026-11-03', T: 0.02, forward: 100, rows: smiling(100, 0.02, r) };
    const far = { expiry: '2026-12-29', T: 0.2, forward: 100, rows: smiling(100, 0.2, r) };
    const empty = { expiry: '2027-03-30', T: 0.4, forward: 100, rows: [] };
    const t = termStructure([far, empty, near], r);
    expect(t.map((p) => p.expiry)).toEqual(['2026-11-03', '2026-12-29']);
    expect(t[0].iv).toBeCloseTo(0.2, 6);
  });
});

const leg = (kind: Leg['kind'], side: 1 | -1, strike: number, price: number, qty = 1): Leg => ({ kind, side, qty, strike, price });

describe('payoff at expiry', () => {
  it('is intrinsic value less the price paid, for a call, a put and a future, bought and sold', () => {
    expect(payoffAtExpiry([leg('call', 1, 100, 5)], 110)).toBe(5);
    expect(payoffAtExpiry([leg('call', 1, 100, 5)], 90)).toBe(-5);
    expect(payoffAtExpiry([leg('call', -1, 100, 5)], 110)).toBe(-5);
    expect(payoffAtExpiry([leg('put', 1, 100, 4)], 90)).toBe(6);
    expect(payoffAtExpiry([leg('put', -1, 100, 4)], 110)).toBe(4);
    expect(payoffAtExpiry([leg('future', 1, 0, 100)], 108)).toBe(8);
    expect(payoffAtExpiry([leg('future', -1, 0, 100)], 108)).toBe(-8);
  });
  it('scales with quantity and adds across legs', () => {
    expect(payoffAtExpiry([leg('call', 1, 100, 5, 50), leg('put', 1, 100, 4, 50)], 120)).toBe(50 * 15 + 50 * -4);
  });
});

describe('break-evens, extremes and premium of the usual positions', () => {
  const straddle = [leg('call', 1, 100, 5), leg('put', 1, 100, 4)];
  const bullSpread = [leg('call', 1, 100, 6), leg('call', -1, 110, 2)];
  const condor = [leg('put', -1, 90, 1.5), leg('put', 1, 85, 0.5), leg('call', -1, 110, 1.5), leg('call', 1, 115, 0.5)];
  const covered = [leg('future', 1, 0, 100), leg('call', -1, 105, 3)];
  const nakedCall = [leg('call', -1, 100, 5)];
  const nakedPut = [leg('put', -1, 100, 4)];

  it('a long straddle breaks even at the strike plus and minus the premium, loses the premium at most and can win without limit', () => {
    expect(breakEvens(straddle)).toEqual([91, 109]);
    expect(extremes(straddle)).toEqual({ maxProfit: 'unlimited', maxLoss: -9 });
    expect(netDebit(straddle)).toBe(9);
  });

  it('a bull call spread breaks even at the lower strike plus the debit, and is capped both ways', () => {
    expect(breakEvens(bullSpread)).toEqual([104]);
    expect(extremes(bullSpread)).toEqual({ maxProfit: 6, maxLoss: -4 });
    expect(netDebit(bullSpread)).toBe(4);
  });

  it('an iron condor has two break-evens and a credit, and is capped both ways', () => {
    expect(breakEvens(condor)).toEqual([88, 112]);
    expect(extremes(condor)).toEqual({ maxProfit: 2, maxLoss: -3 });
    expect(netDebit(condor)).toBe(-2);
  });

  it('a covered future breaks even below the entry by the premium, is capped above and loses a great deal but not without limit', () => {
    expect(breakEvens(covered)).toEqual([97]);
    expect(extremes(covered)).toEqual({ maxProfit: 8, maxLoss: -97 });
  });

  it('a short call loses without limit, a short put loses at most its strike less the premium', () => {
    expect(extremes(nakedCall)).toEqual({ maxProfit: 5, maxLoss: 'unlimited' });
    expect(breakEvens(nakedCall)).toEqual([105]);
    expect(extremes(nakedPut)).toEqual({ maxProfit: 4, maxLoss: -96 });
    expect(breakEvens(nakedPut)).toEqual([96]);
  });

  it('a long future breaks even at its entry and wins without limit', () => {
    expect(breakEvens([leg('future', 1, 0, 100)])).toEqual([100]);
    expect(extremes([leg('future', 1, 0, 100)])).toEqual({ maxProfit: 'unlimited', maxLoss: -100 });
  });

  it('an empty position and one that never crosses zero have no break-even', () => {
    expect(breakEvens([])).toEqual([]);
    expect(extremes([])).toEqual({ maxProfit: 0, maxLoss: 0 });
    expect(breakEvens([leg('call', 1, 100, -5)])).toEqual([]); // a free call with a credit: never loses
    expect(breakEvens([leg('put', 1, 100, 0)])).toEqual([100]); // a put that cost nothing: flat at zero from its strike up, so the strike is where it starts
  });

  it('reads the slope above the top strike as units long less units short in calls and futures', () => {
    expect(slopeAbove(straddle)).toBe(1);
    expect(slopeAbove(bullSpread)).toBe(0);
    expect(slopeAbove(nakedCall)).toBe(-1);
    expect(slopeAbove(covered)).toBe(0);
    expect(slopeAbove([leg('put', 1, 100, 1)])).toBe(0);
  });

  it('every break-even is a zero of the payoff, and the extremes are never beaten anywhere on a wide grid', () => {
    for (const legs of [straddle, bullSpread, condor, covered, nakedCall, nakedPut]) {
      for (const be of breakEvens(legs)) expect(payoffAtExpiry(legs, be)).toBeCloseTo(0, 6);
      const e = extremes(legs);
      for (let s = 0; s <= 400; s += 0.5) {
        const v = payoffAtExpiry(legs, s);
        if (e.maxProfit !== 'unlimited') expect(v).toBeLessThanOrEqual(e.maxProfit + 1e-9);
        if (e.maxLoss !== 'unlimited') expect(v).toBeGreaterThanOrEqual(e.maxLoss - 1e-9);
      }
    }
  });

  it('draws the payoff as evenly spaced points', () => {
    const c = curve(straddle, 80, 120, 5);
    expect(c.map((p) => p.s)).toEqual([80, 90, 100, 110, 120]);
    expect(c.map((p) => p.value)).toEqual([11, 1, -9, 1, 11]);
  });
});

describe('payoff now, and Greeks of a position', () => {
  const market = (years: number[], vols: number[]): Market => ({ years, vols, rate: 0.07 });
  const straddle = [leg('call', 1, 100, 5), leg('put', 1, 100, 4)];

  it('is the payoff at expiry when no time is left', () => {
    const m = market([0, 0], [0.2, 0.2]);
    for (const s of [80, 95, 100, 105, 130]) expect(payoffNow(straddle, s, m)).toBeCloseTo(payoffAtExpiry(straddle, s), 10);
  });

  it('is the payoff at expiry when the days forward reach the expiry', () => {
    const m = market([10 / 365, 10 / 365], [0.2, 0.2]);
    for (const s of [80, 100, 130]) expect(payoffNow(straddle, s, m, 10)).toBeCloseTo(payoffAtExpiry(straddle, s), 10);
    expect(payoffNow(straddle, 100, m, 400)).toBeCloseTo(payoffAtExpiry(straddle, 100), 10); // past it, the same
  });

  it('lies above the payoff at expiry for a long option and is at the model price now', () => {
    const m = market([30 / 365, 30 / 365], [0.2, 0.2]);
    const now = payoffNow(straddle, 100, m);
    expect(now).toBeCloseTo(price('call', 100, 100, 30 / 365, 0.2, 0.07) + price('put', 100, 100, 30 / 365, 0.2, 0.07) - 9, 12);
    expect(now).toBeGreaterThan(payoffAtExpiry(straddle, 100));
  });

  it('gains from a volatility shift for a long straddle and loses with a short one, and shifts every leg', () => {
    const m = market([30 / 365, 30 / 365], [0.2, 0.2]);
    expect(payoffNow(straddle, 100, m, 0, 0.05)).toBeGreaterThan(payoffNow(straddle, 100, m, 0, 0));
    const short = straddle.map((l) => ({ ...l, side: -1 as const }));
    expect(payoffNow(short, 100, m, 0, 0.05)).toBeLessThan(payoffNow(short, 100, m, 0, 0));
  });

  it('uses each leg\'s own time and volatility', () => {
    const near = leg('call', 1, 100, 1);
    const far = leg('call', 1, 100, 1);
    const m = market([10 / 365, 100 / 365], [0.2, 0.3]);
    const both = payoffNow([near, far], 100, m);
    const sum = price('call', 100, 100, 10 / 365, 0.2, 0.07) + price('call', 100, 100, 100 / 365, 0.3, 0.07) - 2;
    expect(both).toBeCloseTo(sum, 12);
  });

  it('has a net delta near zero and a positive gamma and vega for a long straddle at the money, and a futures leg adds its units to delta', () => {
    const m = market([30 / 365, 30 / 365], [0.2, 0.2]);
    const g = netGreeks(straddle, 100, m);
    expect(Math.abs(g.delta)).toBeLessThan(0.05);
    expect(g.gamma).toBeGreaterThan(0);
    expect(g.vega).toBeGreaterThan(0);
    expect(g.theta).toBeLessThan(0);
    const withFuture = netGreeks([...straddle, leg('future', 1, 0, 100, 10)], 100, { ...m, years: [...m.years, 0], vols: [...m.vols, 0] });
    expect(withFuture.delta - netGreeks(straddle, 100, m).delta).toBeCloseTo(10, 12);
  });

  it('flips every Greek when the position is sold, and scales with quantity', () => {
    const m = market([30 / 365, 30 / 365], [0.2, 0.2]);
    const long = netGreeks(straddle, 105, m);
    const short = netGreeks(straddle.map((l) => ({ ...l, side: -1 as const })), 105, m);
    const double = netGreeks(straddle.map((l) => ({ ...l, qty: 2 })), 105, m);
    for (const k of ['delta', 'gamma', 'vega', 'theta', 'rho'] as const) {
      expect(short[k]).toBeCloseTo(-long[k], 12);
      expect(double[k]).toBeCloseTo(2 * long[k], 12);
    }
  });
});
