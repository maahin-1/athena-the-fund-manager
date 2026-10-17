import { describe, expect, it } from 'vitest';
import vectors from './fixtures/indicator_vectors.json';
import * as m from '../src/chartlab/maths2';
import type { Series } from '../src/chartlab/maths';

const bars = vectors.bars as { t: number; o: number; h: number; l: number; c: number; v: number }[];
const expected = vectors.expected as Record<string, (number | null)[]>;
const o = bars.map((b) => b.o);
const h = bars.map((b) => b.h);
const l = bars.map((b) => b.l);
const c = bars.map((b) => b.c);
const v = bars.map((b) => b.v);

/** Element by element against TA-Lib: null where it has none (unless `early`, where ours may start sooner), else equal to a relative 1e-6. */
function same(got: Series, want: (number | null)[], label: string, early = false): void {
  expect(got.length, `${label} length`).toBe(want.length);
  for (let i = 0; i < want.length; i++) {
    if (want[i] === null) {
      if (!early) expect(got[i], `${label}[${i}] should be null`).toBeNull();
      continue;
    }
    expect(got[i], `${label}[${i}] should have a value`).not.toBeNull();
    const tolerance = Math.max(1e-6, Math.abs(want[i] as number) * 1e-6);
    expect(Math.abs((got[i] as number) - (want[i] as number)), `${label}[${i}] got ${got[i]} want ${want[i]}`).toBeLessThanOrEqual(tolerance);
  }
}

describe('tier 2 indicator maths against TA-Lib', () => {
  it('moving averages', () => {
    same(m.dema(c, 20), expected.dema_20, 'dema');
    same(m.tema(c, 20), expected.tema_20, 'tema');
    same(m.trima(c, 20), expected.trima_20, 'trima');
    same(m.kama(c, 30), expected.kama_30, 'kama');
  });
  it('momentum', () => {
    same(m.ppo(c, 12, 26, true), expected.ppo_12_26, 'ppo');
    same(m.ppo(c, 12, 26, false), expected.apo_12_26, 'apo');
    same(m.mom(c, 10), expected.mom_10, 'mom');
    same(m.cmo(c, 14), expected.cmo_14, 'cmo');
    const s = m.stochRsi(c, 14, 14, 3);
    same(s.k, expected.stochrsi_k, 'stochrsi k', true);
    same(s.d, expected.stochrsi_d, 'stochrsi d');
    same(m.ultosc(h, l, c, 7, 14, 28), expected.ultosc, 'ultosc');
    same(m.bop(o, h, l, c), expected.bop, 'bop');
    same(m.aroonOsc(h, l, 14), expected.aroonosc_14, 'aroon oscillator');
  });
  it('volume', () => {
    same(m.ad(h, l, c, v), expected.ad, 'ad');
    same(m.adosc(h, l, c, v, 3, 10), expected.adosc_3_10, 'adosc');
  });
  it('volatility, direction and regression', () => {
    same(m.natr(h, l, c, 14), expected.natr_14, 'natr');
    const d = m.directional(h, l, c, 14);
    same(d.plus, expected.plus_di_14, 'plus di');
    same(d.minus, expected.minus_di_14, 'minus di');
    const r = m.linreg(c, 14);
    same(r.value, expected.linearreg_14, 'linear regression');
    same(r.slope, expected.linearreg_slope_14, 'slope');
    same(m.stddev(c, 20), expected.stddev_20, 'stddev');
  });
});

describe('tier 2 indicators without a TA-Lib twin', () => {
  it('hull average on a straight line lags it by two thirds of a bar after its warm-up', () => {
    const line = Array.from({ length: 40 }, (_, i) => 100 + 2 * i); // two a bar
    const out = m.hma(line, 16);
    expect(out.slice(0, 18).every((x) => x === null)).toBe(true);
    expect(out[39]).toBeCloseTo(line[39] - 2 * (2 / 3), 8); // the half and full averages lead by a third of a bar, the final smoothing lags by one
  });

  it('chaikin money flow is the share of volume that closed at the high', () => {
    const out = m.cmf([10, 10, 10], [0, 0, 0], [10, 10, 5], [100, 100, 100], 3);
    expect(out[2]).toBeCloseTo((100 + 100 + 0) / 300, 12); // closes at the high count in full, a close at the middle counts for nothing
  });

  it('force index smooths close change times volume', () => {
    const out = m.force([10, 12, 11, 15], [0, 100, 200, 300], 2);
    expect(out[0]).toBeNull(); // the first bar has no change
    expect(out[1]).toBeNull(); // two raw values (200 and -200) are needed to start a 2-bar average
    expect(out[2]).toBeCloseTo(0, 12); // their mean
    expect(out[3]).toBeCloseTo(800, 12); // (4 x 300 - 0) x 2/3: the big up day lifts it
  });

  it('awesome oscillator is zero for a flat market and positive in a steady rise', () => {
    const flat = new Array(40).fill(10);
    expect(m.awesome(flat, flat)[39]).toBeCloseTo(0, 12);
    const rising = Array.from({ length: 40 }, (_, i) => 10 + i);
    expect(m.awesome(rising, rising)[39]).toBeGreaterThan(0);
    expect(m.awesome(rising, rising)[30]).toBeNull();
  });

  it('vortex lines stay positive and the up line leads in a rise', () => {
    const rising = Array.from({ length: 30 }, (_, i) => 100 + 2 * i);
    const out = m.vortex(rising.map((x) => x + 1), rising.map((x) => x - 1), rising, 14);
    expect(out.plus[29]).toBeGreaterThan(out.minus[29] as number);
    expect(out.plus[5]).toBeNull();
  });

  it('choppiness is high in a sideways market and low in a trend', () => {
    const sideways = Array.from({ length: 40 }, (_, i) => 100 + (i % 2 ? 1 : -1));
    const trend = Array.from({ length: 40 }, (_, i) => 100 + 3 * i);
    const side = m.choppiness(sideways.map((x) => x + 1), sideways.map((x) => x - 1), sideways, 14)[39] as number;
    const up = m.choppiness(trend.map((x) => x + 1), trend.map((x) => x - 1), trend, 14)[39] as number;
    expect(side).toBeGreaterThan(61.8);
    expect(up).toBeLessThan(38.2);
  });

  it('historical volatility of a constant return is zero and of a swinging price is positive', () => {
    const steady = Array.from({ length: 30 }, (_, i) => 100 * 1.01 ** i);
    expect(m.volatility(steady, 20)[29]).toBeCloseTo(0, 6);
    const swing = Array.from({ length: 30 }, (_, i) => (i % 2 ? 105 : 100));
    expect(m.volatility(swing, 20)[29] as number).toBeGreaterThan(50);
  });

  it('percent B is 0.5 on the middle band and bandwidth grows with the swings', () => {
    const quiet = Array.from({ length: 30 }, (_, i) => 100 + (i % 2));
    const loud = Array.from({ length: 30 }, (_, i) => 100 + 10 * (i % 2));
    const a = m.bollingerStats(quiet, 20, 2);
    expect((a.percentB[29] as number) > 0).toBe(true);
    expect((m.bollingerStats(loud, 20, 2).width[29] as number) > (a.width[29] as number)).toBe(true);
    const flat = m.bollingerStats(new Array(25).fill(50), 20, 2);
    expect(flat.percentB[24]).toBeNull(); // no band width: no %B
    expect(flat.width[24]).toBeCloseTo(0, 12);
  });
});
