/** Indicator maths for the Chart Lab. Every function returns arrays as long as its input with `null` where history is short. Where TA-Lib has a
 *  convention that differs from the textbook (Wilder seeds, the ADX warm-up, parabolic SAR's rules) this follows TA-Lib, because the tests compare
 *  against it element by element (`scripts/make_indicator_vectors.py`). Supertrend, Donchian, Keltner and VWAP have no TA-Lib twin and are tested on
 *  hand-computed vectors. */
import { bollinger, ema, rsi, sma, type Series } from '../indicators';

export { bollinger, ema, rsi, sma };
export type { Series };

const blank = (n: number): Series => new Array(n).fill(null);

/** MACD with TA-Lib's alignment: both averages start at the slow period's first bar, the fast one seeded with the plain average of the `fast`
 *  bars ending there (not of the first `fast` bars). The line starts at bar slow-1, the signal and histogram `signal` bars later. */
export function macd(values: number[], fast: number, slow: number, signal: number): { macd: Series; signal: Series; hist: Series } {
  const line = blank(values.length);
  const slowAvg = ema(values, slow);
  if (values.length >= slow) {
    const k = 2 / (fast + 1);
    let f = values.slice(slow - fast, slow).reduce((a, b) => a + b, 0) / fast;
    line[slow - 1] = f - (slowAvg[slow - 1] as number);
    for (let i = slow; i < values.length; i++) {
      f = (values[i] - f) * k + f;
      line[i] = f - (slowAvg[i] as number);
    }
  }
  const sig = blank(values.length);
  if (values.length >= slow) ema(line.slice(slow - 1) as number[], signal).forEach((v, i) => (sig[slow - 1 + i] = v));
  const hist = line.map((v, i) => (v !== null && sig[i] !== null ? v - (sig[i] as number) : null));
  return { macd: line, signal: sig, hist };
}

/** Linearly weighted average: the latest bar weighs n, the one before n-1, and so on. */
export function wma(values: number[], n: number): Series {
  const out = blank(values.length);
  const divisor = (n * (n + 1)) / 2;
  for (let i = n - 1; i < values.length; i++) {
    let sum = 0;
    for (let j = 0; j < n; j++) sum += values[i - j] * (n - j);
    out[i] = sum / divisor;
  }
  return out;
}

export function highest(values: number[], i: number, n: number): number {
  let m = -Infinity;
  for (let j = i - n + 1; j <= i; j++) m = Math.max(m, values[j]);
  return m;
}

export function lowest(values: number[], i: number, n: number): number {
  let m = Infinity;
  for (let j = i - n + 1; j <= i; j++) m = Math.min(m, values[j]);
  return m;
}

/** Slow stochastic: %K is the simple average of the raw %K over `smoothK` bars, %D the average of %K over `smoothD`. */
export function stoch(h: number[], l: number[], c: number[], n: number, smoothK: number, smoothD: number): { k: Series; d: Series } {
  const raw = blank(c.length);
  for (let i = n - 1; i < c.length; i++) {
    const hh = highest(h, i, n);
    const ll = lowest(l, i, n);
    raw[i] = hh === ll ? 0 : ((c[i] - ll) / (hh - ll)) * 100;
  }
  const average = (series: Series, period: number): Series => {
    const first = series.findIndex((v) => v !== null);
    const out = blank(series.length);
    if (first < 0) return out;
    sma(series.slice(first) as number[], period).forEach((v, i) => (out[first + i] = v));
    return out;
  };
  const k = average(raw, smoothK);
  return { k, d: average(k, smoothD) };
}

/** True range of each bar; the first bar has none (it needs a previous close). */
export function trueRange(h: number[], l: number[], c: number[]): Series {
  const out = blank(c.length);
  for (let i = 1; i < c.length; i++) out[i] = Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
  return out;
}

/** Average true range, Wilder-smoothed and seeded with the plain average of the first n true ranges. */
export function atr(h: number[], l: number[], c: number[], n: number): Series {
  const out = blank(c.length);
  if (c.length <= n) return out;
  const tr = trueRange(h, l, c) as number[];
  let prev = 0;
  for (let i = 1; i <= n; i++) prev += tr[i];
  prev /= n;
  out[n] = prev;
  for (let i = n + 1; i < c.length; i++) {
    prev = (prev * (n - 1) + tr[i]) / n;
    out[i] = prev;
  }
  return out;
}

/** Average directional index with TA-Lib's warm-up: the first sums cover bars 1 to n-1, then every bar is Wilder-smoothed. */
export function adx(h: number[], l: number[], c: number[], n: number): Series {
  const out = blank(c.length);
  if (c.length < 2 * n) return out;
  let plus = 0;
  let minus = 0;
  let tr = 0;
  const step = (i: number) => {
    const up = h[i] - h[i - 1];
    const down = l[i - 1] - l[i];
    return {
      plus: up > down && up > 0 ? up : 0,
      minus: down > up && down > 0 ? down : 0,
      tr: Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])),
    };
  };
  for (let i = 1; i < n; i++) {
    const s = step(i);
    plus += s.plus;
    minus += s.minus;
    tr += s.tr;
  }
  const smooth = (i: number) => {
    const s = step(i);
    plus = plus - plus / n + s.plus;
    minus = minus - minus / n + s.minus;
    tr = tr - tr / n + s.tr;
    const plusDI = tr ? (100 * plus) / tr : 0;
    const minusDI = tr ? (100 * minus) / tr : 0;
    const total = plusDI + minusDI;
    return total ? (100 * Math.abs(plusDI - minusDI)) / total : 0;
  };
  let sum = 0;
  for (let i = n; i < 2 * n; i++) sum += smooth(i);
  let prev = sum / n;
  out[2 * n - 1] = prev;
  for (let i = 2 * n; i < c.length; i++) {
    prev = (prev * (n - 1) + smooth(i)) / n;
    out[i] = prev;
  }
  return out;
}

/** On-balance volume: volume added on an up close, subtracted on a down close, starting from the first bar's volume. */
export function obv(c: number[], v: number[]): Series {
  const out = blank(c.length);
  if (!c.length) return out;
  let total = v[0];
  out[0] = total;
  for (let i = 1; i < c.length; i++) {
    if (c[i] > c[i - 1]) total += v[i];
    else if (c[i] < c[i - 1]) total -= v[i];
    out[i] = total;
  }
  return out;
}

/** Commodity channel index: (typical price - its average) / (0.015 x mean absolute deviation). */
export function cci(h: number[], l: number[], c: number[], n: number): Series {
  const tp = c.map((_, i) => (h[i] + l[i] + c[i]) / 3);
  const mean = sma(tp, n);
  const out = blank(c.length);
  for (let i = n - 1; i < c.length; i++) {
    let dev = 0;
    for (let j = i - n + 1; j <= i; j++) dev += Math.abs(tp[j] - (mean[i] as number));
    dev /= n;
    out[i] = dev === 0 ? 0 : (tp[i] - (mean[i] as number)) / (0.015 * dev);
  }
  return out;
}

/** Williams %R: where the close sits in the n-bar range, from 0 (at the high) to -100 (at the low). */
export function willr(h: number[], l: number[], c: number[], n: number): Series {
  const out = blank(c.length);
  for (let i = n - 1; i < c.length; i++) {
    const hh = highest(h, i, n);
    const ll = lowest(l, i, n);
    out[i] = hh === ll ? 0 : ((hh - c[i]) / (hh - ll)) * -100;
  }
  return out;
}

/** Rate of change in percent over n bars. */
export function roc(values: number[], n: number): Series {
  const out = blank(values.length);
  for (let i = n; i < values.length; i++) out[i] = values[i - n] === 0 ? 0 : (values[i] / values[i - n] - 1) * 100;
  return out;
}

/** Money flow index: RSI-like, on typical price times volume over the last n bars. */
export function mfi(h: number[], l: number[], c: number[], v: number[], n: number): Series {
  const tp = c.map((_, i) => (h[i] + l[i] + c[i]) / 3);
  const out = blank(c.length);
  for (let i = n; i < c.length; i++) {
    let up = 0;
    let down = 0;
    for (let j = i - n + 1; j <= i; j++) {
      const flow = tp[j] * v[j];
      if (tp[j] > tp[j - 1]) up += flow;
      else if (tp[j] < tp[j - 1]) down += flow;
    }
    out[i] = up + down === 0 ? 0 : (100 * up) / (up + down);
  }
  return out;
}

/** TRIX: the one-bar percent change of a triple-smoothed exponential average. */
export function trix(values: number[], n: number): Series {
  let series: Series = ema(values, n);
  for (let pass = 0; pass < 2; pass++) {
    const first = series.findIndex((v) => v !== null);
    const next = blank(values.length);
    if (first >= 0) ema(series.slice(first) as number[], n).forEach((v, i) => (next[first + i] = v));
    series = next;
  }
  return series.map((v, i) => {
    const prev = i > 0 ? series[i - 1] : null;
    return v !== null && prev !== null && prev !== 0 ? ((v - prev) / prev) * 100 : null;
  });
}

/** Aroon: how recently the highest high and lowest low of the last n+1 bars happened, as 0 to 100. A tie goes to the later bar. */
export function aroon(h: number[], l: number[], n: number): { up: Series; down: Series } {
  const up = blank(h.length);
  const down = blank(h.length);
  for (let i = n; i < h.length; i++) {
    let hi = i - n;
    let lo = i - n;
    for (let j = i - n; j <= i; j++) {
      if (h[j] >= h[hi]) hi = j;
      if (l[j] <= l[lo]) lo = j;
    }
    up[i] = (100 * (n - (i - hi))) / n;
    down[i] = (100 * (n - (i - lo))) / n;
  }
  return { up, down };
}

/** Parabolic SAR with TA-Lib's initial direction and reversal rules. Starts at the second bar. */
export function sar(h: number[], l: number[], step: number, max: number): Series {
  const out = blank(h.length);
  if (h.length < 2) return out;
  const up = h[1] - h[0];
  const down = l[0] - l[1];
  const minusDM = down > 0 && up < down ? down : 0;
  let long = minusDM <= 0;
  let af = step;
  let ep = long ? h[1] : l[1];
  let value = long ? l[0] : h[0];
  let newHigh = h[1];
  let newLow = l[1];
  for (let today = 1; today < h.length; ) {
    const prevHigh = newHigh;
    const prevLow = newLow;
    newHigh = h[today];
    newLow = l[today];
    const at = today;
    today++;
    if (long) {
      if (newLow <= value) {
        long = false;
        value = Math.max(ep, prevHigh, newHigh);
        out[at] = value;
        af = step;
        ep = newLow;
        value = value + af * (ep - value);
        value = Math.max(value, prevHigh, newHigh);
      } else {
        out[at] = value;
        if (newHigh > ep) {
          ep = newHigh;
          af = Math.min(af + step, max);
        }
        value = value + af * (ep - value);
        value = Math.min(value, prevLow, newLow);
      }
    } else if (newHigh >= value) {
      long = true;
      value = Math.min(ep, prevLow, newLow);
      out[at] = value;
      af = step;
      ep = newHigh;
      value = value + af * (ep - value);
      value = Math.min(value, prevLow, newLow);
    } else {
      out[at] = value;
      if (newLow < ep) {
        ep = newLow;
        af = Math.min(af + step, max);
      }
      value = value + af * (ep - value);
      value = Math.max(value, prevHigh, newHigh);
    }
  }
  return out;
}

/** Supertrend: an ATR band that flips side when the close crosses it. `line` is the active band, `up` is true on an up-trend bar. */
export function supertrend(h: number[], l: number[], c: number[], n: number, mult: number): { line: Series; up: (boolean | null)[] } {
  const range = atr(h, l, c, n);
  const line = blank(c.length);
  const up: (boolean | null)[] = new Array(c.length).fill(null);
  let upper = 0;
  let lower = 0;
  let trendUp = true;
  for (let i = 0; i < c.length; i++) {
    const a = range[i];
    if (a === null) continue;
    const mid = (h[i] + l[i]) / 2;
    const basicUpper = mid + mult * a;
    const basicLower = mid - mult * a;
    if (line[i - 1] === null || line[i - 1] === undefined) {
      upper = basicUpper;
      lower = basicLower;
      trendUp = c[i] >= mid;
    } else {
      upper = basicUpper < upper || c[i - 1] > upper ? basicUpper : upper;
      lower = basicLower > lower || c[i - 1] < lower ? basicLower : lower;
      if (trendUp && c[i] < lower) trendUp = false;
      else if (!trendUp && c[i] > upper) trendUp = true;
    }
    line[i] = trendUp ? lower : upper;
    up[i] = trendUp;
  }
  return { line, up };
}

/** Donchian channel: the highest high, lowest low and their midpoint over n bars. */
export function donchian(h: number[], l: number[], n: number): { upper: Series; lower: Series; mid: Series } {
  const upper = blank(h.length);
  const lower = blank(h.length);
  const mid = blank(h.length);
  for (let i = n - 1; i < h.length; i++) {
    upper[i] = highest(h, i, n);
    lower[i] = lowest(l, i, n);
    mid[i] = ((upper[i] as number) + (lower[i] as number)) / 2;
  }
  return { upper, lower, mid };
}

/** Keltner channel: an exponential average of the close with bands `mult` average true ranges either side. */
export function keltner(h: number[], l: number[], c: number[], n: number, atrN: number, mult: number): { upper: Series; mid: Series; lower: Series } {
  const mid = ema(c, n);
  const range = atr(h, l, c, atrN);
  const at = (f: (m: number, a: number) => number): Series => mid.map((m, i) => (m !== null && range[i] !== null ? f(m, range[i] as number) : null));
  return { mid, upper: at((m, a) => m + mult * a), lower: at((m, a) => m - mult * a) };
}

/** Volume-weighted average price, restarting each trading day (IST). Daily bars therefore give each bar's own typical price. */
export function vwap(t: number[], h: number[], l: number[], c: number[], v: number[]): Series {
  const out = blank(c.length);
  const day = (ts: number) => Math.floor((ts + 19_800) / 86_400);
  let pv = 0;
  let vol = 0;
  let current = -1;
  for (let i = 0; i < c.length; i++) {
    if (day(t[i]) !== current) {
      current = day(t[i]);
      pv = 0;
      vol = 0;
    }
    const tp = (h[i] + l[i] + c[i]) / 3;
    pv += tp * v[i];
    vol += v[i];
    out[i] = vol ? pv / vol : tp;
  }
  return out;
}
