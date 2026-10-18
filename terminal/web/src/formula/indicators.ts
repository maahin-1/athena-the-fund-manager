import type { Bar } from '../bars';
import * as m from '../chartlab/maths';
import * as m2 from '../chartlab/maths2';

/** The page's implementation of each indicator a formula can call, under the strategy engine's own names for its lines. A value that cannot be computed is
 *  NaN, as in the engine. A test fixes that every line here equals the Python engine's (TA-Lib) on the same bars. */

export type Values = number[];
export type Lines = Record<string, Values>;

const nan = (s: m.Series): Values => s.map((v) => (v === null || !Number.isFinite(v) ? NaN : v));

/** TA-Lib gives all the outputs of one call from the same bar, the one after the longest warm-up of any of them; the engine's numbers are TA-Lib's, so a formula
 *  has to start where it does (the chart's own MACD and stochastic show their first values earlier). */
const from = (values: Values, first: number): Values => values.map((x, i) => (i < first ? NaN : x));

export function computeIndicator(key: string, p: Record<string, number>, bars: Bar[]): Lines {
  const o = bars.map((b) => b.o);
  const h = bars.map((b) => b.h);
  const l = bars.map((b) => b.l);
  const c = bars.map((b) => b.c);
  const v = bars.map((b) => b.v);
  void o;
  switch (key) {
    case 'sma':
      return { value: nan(m.sma(c, p.length)) };
    case 'ema':
      return { value: nan(m.ema(c, p.length)) };
    case 'wma':
      return { value: nan(m.wma(c, p.length)) };
    case 'bbands': {
      const r = m.bollinger(c, p.length, p.width);
      return { upper: nan(r.upper), lower: nan(r.lower) };
    }
    case 'sar':
      return { value: nan(m.sar(h, l, p.acceleration, p.maximum)) };
    case 'channel':
      return { high: nan(h.map((_, i) => (i + 1 < p.length ? null : m.highest(h, i, p.length)))), low: nan(l.map((_, i) => (i + 1 < p.length ? null : m.lowest(l, i, p.length)))) };
    case 'rsi':
      return { value: nan(m.rsi(c, p.length)) };
    case 'macd': {
      const r = m.macd(c, p.fast, p.slow, p.signal);
      const start = p.slow + p.signal - 2;
      return { macd: from(nan(r.macd), start), signal: from(nan(r.signal), start), histogram: from(nan(r.hist), start) };
    }
    case 'stoch': {
      const r = m.stoch(h, l, c, p.k, p.smooth, p.d);
      const start = p.k + p.smooth + p.d - 3;
      return { k: from(nan(r.k), start), d: from(nan(r.d), start) };
    }
    case 'adx': {
      const di = m2.directional(h, l, c, p.length);
      return { adx: nan(m.adx(h, l, c, p.length)), plus_di: nan(di.plus), minus_di: nan(di.minus) };
    }
    case 'atr':
      return { value: nan(m.atr(h, l, c, p.length)) };
    case 'obv':
      return { value: nan(m.obv(c, v)) };
    case 'cci':
      return { value: nan(m.cci(h, l, c, p.length)) };
    case 'mfi':
      return { value: nan(m.mfi(h, l, c, v, p.length)) };
    case 'willr':
      return { value: nan(m.willr(h, l, c, p.length)) };
    case 'roc':
      return { value: nan(m.roc(c, p.length)) };
    default:
      throw new Error(`unknown indicator ${key}`);
  }
}
