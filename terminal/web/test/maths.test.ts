import { describe, expect, it } from 'vitest';
import vectors from './fixtures/indicator_vectors.json';
import * as m from '../src/chartlab/maths';

const bars = vectors.bars as { t: number; o: number; h: number; l: number; c: number; v: number }[];
const expected = vectors.expected as Record<string, (number | null)[]>;
const h = bars.map((b) => b.h);
const l = bars.map((b) => b.l);
const c = bars.map((b) => b.c);
const v = bars.map((b) => b.v);

/** Element-by-element comparison (`early`: the series may start sooner than TA-Lib's, which waits for its signal line; the shared bars must still agree): null where TA-Lib has no value, otherwise equal to a relative 1e-6 (and 1e-6 absolute near zero). */
function same(got: m.Series, want: (number | null)[], label: string, early = false): void {
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

describe('indicator maths against TA-Lib', () => {
  it('moving averages', () => {
    same(m.sma(c, 20), expected.sma_20, 'sma');
    same(m.ema(c, 20), expected.ema_20, 'ema');
    same(m.wma(c, 10), expected.wma_10, 'wma');
  });
  it('rsi, macd, bollinger', () => {
    same(m.rsi(c, 14), expected.rsi_14, 'rsi');
    const mac = m.macd(c, 12, 26, 9);
    same(mac.macd, expected.macd, 'macd', true); // TA-Lib starts the line with its signal; ours starts when both averages exist
    same(mac.signal, expected.macd_signal, 'signal');
    same(mac.hist, expected.macd_hist, 'hist');
    const bb = m.bollinger(c, 20, 2);
    same(bb.upper, expected.bb_upper, 'bb upper');
    same(bb.mid, expected.bb_mid, 'bb mid');
    same(bb.lower, expected.bb_lower, 'bb lower');
  });
  it('stochastic, atr, adx', () => {
    const s = m.stoch(h, l, c, 14, 3, 3);
    same(s.k, expected.stoch_k, 'stoch k', true);
    same(s.d, expected.stoch_d, 'stoch d');
    same(m.atr(h, l, c, 14), expected.atr_14, 'atr');
    same(m.adx(h, l, c, 14), expected.adx_14, 'adx');
  });
  it('volume and oscillators', () => {
    same(m.obv(c, v), expected.obv, 'obv');
    same(m.cci(h, l, c, 20), expected.cci_20, 'cci');
    same(m.willr(h, l, c, 14), expected.willr_14, 'willr');
    same(m.roc(c, 10), expected.roc_10, 'roc');
    same(m.mfi(h, l, c, v, 14), expected.mfi_14, 'mfi');
    same(m.trix(c, 15), expected.trix_15, 'trix');
  });
  it('aroon and parabolic sar', () => {
    const a = m.aroon(h, l, 14);
    same(a.up, expected.aroon_up, 'aroon up');
    same(a.down, expected.aroon_down, 'aroon down');
    same(m.sar(h, l, 0.02, 0.2), expected.sar, 'sar');
  });
});

describe('indicators without a TA-Lib twin', () => {
  it('donchian is the rolling high, low and midpoint', () => {
    const d = m.donchian([5, 7, 6, 9, 8], [3, 4, 2, 5, 6], 3);
    expect(d.upper).toEqual([null, null, 7, 9, 9]);
    expect(d.lower).toEqual([null, null, 2, 2, 2]);
    expect(d.mid).toEqual([null, null, 4.5, 5.5, 5.5]);
  });
  it('vwap restarts each day and weighs typical price by volume', () => {
    const day = 86_400;
    const t = [0, 60, day, day + 60];
    const out = m.vwap(t, [11, 13, 21, 21], [9, 11, 19, 19], [10, 12, 20, 20], [100, 300, 50, 50]);
    expect(out[0]).toBeCloseTo(10, 10);
    expect(out[1]).toBeCloseTo((10 * 100 + 12 * 300) / 400, 10);
    expect(out[2]).toBeCloseTo(20, 10); // a new day starts over
    expect(out[3]).toBeCloseTo(20, 10);
  });
  it('keltner is an exponential mid with bands of average true range', () => {
    const k = m.keltner(h, l, c, 20, 10, 2);
    const mid = m.ema(c, 20);
    const range = m.atr(h, l, c, 10);
    expect(k.mid).toEqual(mid);
    expect(k.upper[50]).toBeCloseTo((mid[50] as number) + 2 * (range[50] as number), 10);
    expect(k.lower[50]).toBeCloseTo((mid[50] as number) - 2 * (range[50] as number), 10);
    expect(k.upper[5]).toBeNull();
  });
  it('supertrend sits below price in an up-trend, above it in a down-trend, and only flips on a close through the band', () => {
    const up = Array.from({ length: 40 }, (_, i) => 100 + i * 2);
    const st = m.supertrend(up.map((x) => x + 1), up.map((x) => x - 1), up, 10, 3);
    expect(st.up[39]).toBe(true);
    expect(st.line[39] as number).toBeLessThan(up[39]);
    const down = Array.from({ length: 40 }, (_, i) => 200 - i * 2);
    const sd = m.supertrend(down.map((x) => x + 1), down.map((x) => x - 1), down, 10, 3);
    expect(sd.line[39] as number).toBeGreaterThan(down[39]);
    expect(st.line[3]).toBeNull();
  });
});
