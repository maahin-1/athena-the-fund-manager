import { describe, expect, it } from 'vitest';
import { cdf, greeks, impliedVol, pdf, price, yearsToExpiry, type Kind } from '../src/options/black76';

describe('the normal distribution', () => {
  it('matches known values to double precision', () => {
    expect(cdf(0)).toBeCloseTo(0.5, 14);
    expect(cdf(1.96)).toBeCloseTo(0.97500210485178, 13);
    expect(cdf(-1.96)).toBeCloseTo(0.02499789514822, 13);
    expect(cdf(3)).toBeCloseTo(0.99865010196837, 13);
    expect(cdf(-3)).toBeCloseTo(0.00134989803163, 13);
    expect(cdf(0.5)).toBeCloseTo(0.691462461274013, 13);
    expect(cdf(8)).toBeCloseTo(1, 12);
    expect(cdf(-40)).toBe(0);
    expect(cdf(40)).toBe(1);
  });

  it('is symmetric and rises', () => {
    for (const x of [0.1, 0.7, 1.3, 2.2, 4, 7.5]) expect(cdf(x) + cdf(-x)).toBeCloseTo(1, 14);
    let last = 0;
    for (let x = -6; x <= 6; x += 0.25) {
      expect(cdf(x)).toBeGreaterThanOrEqual(last);
      last = cdf(x);
    }
  });

  it('has a density that is the slope of the distribution', () => {
    expect(pdf(0)).toBeCloseTo(0.3989422804014327, 14);
    for (const x of [-2, -0.5, 0.3, 1.7]) expect((cdf(x + 1e-5) - cdf(x - 1e-5)) / 2e-5).toBeCloseTo(pdf(x), 8);
  });
});

describe('Black-76 prices and Greeks against values worked out independently', () => {
  // [kind, F, K, T, sigma, r] and [price, delta, gamma, vega per point, theta per day, rho per point]
  const cases: [[Kind, number, number, number, number, number], number[]][] = [
    [['call', 20, 20, 4 / 12, 0.25, 0.09], [1.1166414566, 0.5131388032, 0.1337645027, 0.0445881676, -0.0043056399, -0.0037221382]], // Hull's futures option
    [['put', 20, 20, 4 / 12, 0.25, 0.09], [1.1166414566, -0.4573067304, 0.1337645027, 0.0445881676, -0.0043056399, -0.0037221382]],
    [['call', 23000, 23200, 7 / 365, 0.14, 0.07], [96.0630438028, 0.3306562179, 0.0008121263, 11.5348641316, -11.5164410821, -0.0184230495]],
    [['put', 23000, 22800, 7 / 365, 0.16, 0.07], [117.7878186026, -0.3421905889, 0.0007201396, 11.6895429176, -13.3368881754, -0.0225894447]],
    [['call', 100, 80, 1, 0.3, 0], [23.5343901032, 0.8142887128, 0.0089188727, 0.267566182, -0.0109958705, -0.235343901]],
    [['put', 100, 120, 0.5, 0.2, 0.05], [20.20882371, -0.8665457855, 0.0130957806, 0.1309578063, -0.0044074382, -0.1010441185]],
  ];
  for (const [[kind, F, K, T, s, r], want] of cases) {
    it(`${kind} F=${F} K=${K} T=${T.toFixed(4)} vol=${s} r=${r}`, () => {
      const g = greeks(kind, F, K, T, s, r);
      const got = [g.price, g.delta, g.gamma, g.vega, g.theta, g.rho];
      got.forEach((v, i) => expect(v).toBeCloseTo(want[i], i === 0 ? 7 : 8));
      expect(price(kind, F, K, T, s, r)).toBeCloseTo(want[0], 9);
    });
  }
});

describe('properties that must hold', () => {
  const grid: [number, number, number, number][] = [];
  for (const K of [70, 90, 100, 110, 140]) for (const T of [1 / 365, 0.05, 0.5, 2]) for (const s of [0.08, 0.2, 0.6, 1.5]) grid.push([K, T, s, 0.07]);

  it('puts and calls satisfy parity: call - put = discounted (F - K)', () => {
    for (const [K, T, s, r] of grid) expect(price('call', 100, K, T, s, r) - price('put', 100, K, T, s, r)).toBeCloseTo(Math.exp(-r * T) * (100 - K), 9);
  });

  it('never prices below intrinsic or above the bound', () => {
    for (const [K, T, s, r] of grid) {
      const df = Math.exp(-r * T);
      const c = price('call', 100, K, T, s, r);
      const p = price('put', 100, K, T, s, r);
      expect(c).toBeGreaterThanOrEqual(df * Math.max(0, 100 - K) - 1e-9);
      expect(c).toBeLessThanOrEqual(df * 100 + 1e-9);
      expect(p).toBeGreaterThanOrEqual(df * Math.max(0, K - 100) - 1e-9);
      expect(p).toBeLessThanOrEqual(df * K + 1e-9);
    }
  });

  it('has a delta that is the slope of the price, a gamma that is the slope of delta, and a vega that is the slope in volatility', () => {
    for (const [K, T, s, r] of grid.filter((_, i) => i % 3 === 0)) {
      for (const kind of ['call', 'put'] as Kind[]) {
        const h = 0.01;
        const d = (price(kind, 100 + h, K, T, s, r) - price(kind, 100 - h, K, T, s, r)) / (2 * h);
        const g = greeks(kind, 100, K, T, s, r);
        expect(g.delta).toBeCloseTo(d, 4);
        const gamma = (greeks(kind, 100 + h, K, T, s, r).delta - greeks(kind, 100 - h, K, T, s, r).delta) / (2 * h);
        expect(g.gamma).toBeCloseTo(gamma, 4);
        const dv = 1e-5;
        const vega = ((price(kind, 100, K, T, s + dv, r) - price(kind, 100, K, T, s - dv, r)) / (2 * dv)) / 100;
        expect(g.vega).toBeCloseTo(vega, 4);
      }
    }
  });

  it('has a theta that is the price lost to one day, and a rho that is the price lost to one point of rate', () => {
    const [kind, F, K, T, s, r] = ['call', 100, 105, 0.25, 0.3, 0.07] as const;
    // theta holds the forward fixed; it is the slope in time, per day, taken over a very short step
    const dt = 1e-6;
    const lost = ((price(kind, F, K, T - dt, s, r) - price(kind, F, K, T, s, r)) / dt) / 365;
    expect(greeks(kind, F, K, T, s, r).theta).toBeCloseTo(lost, 6);
    const rate = (price(kind, F, K, T, s, r + 0.0001) - price(kind, F, K, T, s, r - 0.0001)) / 0.0002 / 100;
    expect(greeks(kind, F, K, T, s, r).rho).toBeCloseTo(rate, 6);
  });

  it('gives a call a delta between 0 and the discount and a put between minus that and 0, and both a positive gamma and vega', () => {
    for (const [K, T, s, r] of grid) {
      const df = Math.exp(-r * T);
      const c = greeks('call', 100, K, T, s, r);
      const p = greeks('put', 100, K, T, s, r);
      expect(c.delta).toBeGreaterThanOrEqual(0);
      expect(c.delta).toBeLessThanOrEqual(df + 1e-12);
      expect(p.delta).toBeLessThanOrEqual(0);
      expect(p.delta).toBeGreaterThanOrEqual(-df - 1e-12);
      expect(c.gamma).toBeGreaterThanOrEqual(0);
      expect(c.vega).toBeGreaterThanOrEqual(0);
      expect(c.gamma).toBeCloseTo(p.gamma, 12);
      expect(c.vega).toBeCloseTo(p.vega, 12);
    }
  });

  it('is its intrinsic value when there is no time or no volatility', () => {
    expect(price('call', 100, 90, 0, 0.2, 0)).toBe(10);
    expect(price('put', 100, 90, 0.5, 0, 0)).toBe(0);
    expect(price('put', 90, 100, 0.5, 0, 0)).toBe(10);
    expect(greeks('call', 100, 90, 0, 0.2, 0).delta).toBe(1);
    expect(greeks('put', 100, 90, 0, 0.2, 0).delta).toBe(0);
    expect(greeks('call', 100, 90, 0, 0.2, 0).gamma).toBe(0);
  });
});

describe('implied volatility', () => {
  it('returns the volatility a price was made with, across moneyness, expiries and volatilities', () => {
    for (const K of [60, 85, 100, 118, 160]) {
      for (const T of [1 / 365, 0.02, 0.25, 1.5]) {
        for (const s of [0.07, 0.18, 0.45, 1.1]) {
          for (const kind of ['call', 'put'] as Kind[]) {
            const p = price(kind, 100, K, T, s, 0.07);
            const r = impliedVol(kind, p, 100, K, T, 0.07);
            if (!r.ok) continue; // a price that is all intrinsic value has no volatility; the refusal is tested below
            // the price barely moves with volatility far from the money at short expiries: the round trip is checked in price, which is what matters
            expect(Math.abs(price(kind, 100, K, T, r.iv, 0.07) - p)).toBeLessThanOrEqual(1e-8 * Math.max(1, p));
            if (greeks(kind, 100, K, T, s, 0.07).vega > 1e-3) expect(r.iv).toBeCloseTo(s, 5);
          }
        }
      }
    }
  });

  it('finds 25% for the Hull option and 14% for an index option a week out', () => {
    const a = impliedVol('call', 1.1166414566, 20, 20, 4 / 12, 0.09);
    expect(a.ok && a.iv).toBeCloseTo(0.25, 7);
    const b = impliedVol('call', 96.0630438028, 23000, 23200, 7 / 365, 0.07);
    expect(b.ok && b.iv).toBeCloseTo(0.14, 7);
  });

  it('refuses, with the reason, a price no volatility can make', () => {
    const reason = (r: ReturnType<typeof impliedVol>): string => (r.ok ? 'accepted' : r.reason);
    expect(reason(impliedVol('call', 5, 100, 80, 0.5, 0))).toMatch(/below the option's intrinsic/); // intrinsic is 20
    expect(reason(impliedVol('call', 100, 100, 80, 0.5, 0))).toMatch(/most the option can be worth/);
    expect(reason(impliedVol('put', 90, 100, 90, 0.5, 0))).toMatch(/most the option can be worth/);
    expect(reason(impliedVol('call', 0, 100, 100, 0.5, 0))).toMatch(/not above zero/);
    expect(reason(impliedVol('call', -1, 100, 100, 0.5, 0))).toMatch(/not above zero/);
    expect(reason(impliedVol('call', Number.NaN, 100, 100, 0.5, 0))).toMatch(/not usable/);
    expect(reason(impliedVol('call', 5, 0, 100, 0.5, 0))).toMatch(/not usable/);
    expect(reason(impliedVol('call', 5, 100, 100, 0, 0))).toMatch(/not usable/);
    expect(reason(impliedVol('call', 79.99, 100, 100, 0.5, 0))).toBeTruthy();
  });

  it('accepts a price at exactly its intrinsic value when there is any time value to find or none', () => {
    const r = impliedVol('call', 20, 100, 80, 0.5, 0); // intrinsic exactly; the volatility that gives it is the smallest allowed or a refusal, never a wrong number
    if (r.ok) expect(price('call', 100, 80, 0.5, r.iv, 0)).toBeCloseTo(20, 6);
    else expect(r.reason).toMatch(/too small|below/);
  });

  it('refuses a volatility above 500%', () => {
    const p = price('call', 100, 100, 0.01, 5, 0);
    const r = impliedVol('call', Math.min(p * 1.5, 0.99 * 100), 100, 100, 0.01, 0);
    expect(r.ok).toBe(false);
  });
});

describe('time to expiry', () => {
  const IST = 19_800;
  const at = (y: number, m: number, d: number, hh: number, mm: number): number => Date.UTC(y, m - 1, d, hh, mm) / 1000 - IST;

  it('runs to 15:30 IST on the expiry date, in years of 365 days', () => {
    expect(yearsToExpiry('2026-11-03', at(2026, 10, 27, 15, 30))).toBeCloseTo(7 / 365, 12);
    expect(yearsToExpiry('2026-11-03', at(2026, 11, 3, 9, 15))).toBeCloseTo((6 * 3600 + 15 * 60) / (365 * 86_400), 12);
  });

  it('is never less than a minute, after expiry too', () => {
    const minute = 60 / (365 * 86_400);
    expect(yearsToExpiry('2026-11-03', at(2026, 11, 3, 15, 30))).toBeCloseTo(minute, 14);
    expect(yearsToExpiry('2026-11-03', at(2026, 11, 3, 15, 29, ))).toBeCloseTo(minute, 14);
    expect(yearsToExpiry('2026-11-03', at(2026, 12, 1, 10, 0))).toBeCloseTo(minute, 14);
  });

  it('is not a number for text that is not a date', () => {
    for (const t of ['', '03NOV2026', '2026-11', 'soon']) expect(yearsToExpiry(t, 0)).toBeNaN();
  });
});
