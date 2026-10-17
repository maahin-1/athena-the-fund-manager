/** Second tier of indicator maths for the Chart Lab. As in `maths.ts`, every function returns arrays as long as its input with `null` where history is
 *  short, and where TA-Lib has a twin the tests compare against it element by element. Hull, CMF, force index, awesome oscillator, vortex, choppiness,
 *  historical volatility and the Bollinger statistics have no TA-Lib twin and are tested on hand-computed vectors. */
import { aroon, atr, bollinger, ema, highest, lowest, rsi, sma, trueRange, wma, type Series } from './maths';

const blank = (n: number): Series => new Array(n).fill(null);

/** An exponential average of a series that may start with nulls: the average starts at its first value. */
function emaOfSeries(series: Series, n: number): Series {
  const first = series.findIndex((v) => v !== null);
  const out = blank(series.length);
  if (first >= 0) ema(series.slice(first) as number[], n).forEach((v, i) => (out[first + i] = v));
  return out;
}

/** Double exponential average: 2 x EMA - EMA of EMA. */
export function dema(values: number[], n: number): Series {
  const e1 = ema(values, n);
  const e2 = emaOfSeries(e1, n);
  return values.map((_, i) => (e1[i] !== null && e2[i] !== null ? 2 * (e1[i] as number) - (e2[i] as number) : null));
}

/** Triple exponential average: 3 x EMA - 3 x EMA of EMA + EMA of EMA of EMA. */
export function tema(values: number[], n: number): Series {
  const e1 = ema(values, n);
  const e2 = emaOfSeries(e1, n);
  const e3 = emaOfSeries(e2, n);
  return values.map((_, i) => (e1[i] !== null && e2[i] !== null && e3[i] !== null ? 3 * (e1[i] as number) - 3 * (e2[i] as number) + (e3[i] as number) : null));
}

/** Triangular average: weights rise to the middle of the window and fall again. */
export function trima(values: number[], n: number): Series {
  const out = blank(values.length);
  const up = Math.ceil(n / 2);
  const weights = Array.from({ length: n }, (_, k) => (k < up ? k + 1 : n - k));
  const total = weights.reduce((a, b) => a + b, 0);
  for (let i = n - 1; i < values.length; i++) {
    let sum = 0;
    for (let k = 0; k < n; k++) sum += values[i - n + 1 + k] * weights[k];
    out[i] = sum / total;
  }
  return out;
}

/** Hull moving average: a weighted average of 2 x WMA(n/2) - WMA(n), smoothed over the square root of n bars. */
export function hma(values: number[], n: number): Series {
  const half = wma(values, Math.max(1, Math.floor(n / 2)));
  const full = wma(values, n);
  const raw = values.map((_, i) => (half[i] !== null && full[i] !== null ? 2 * (half[i] as number) - (full[i] as number) : null));
  const first = raw.findIndex((v) => v !== null);
  const out = blank(values.length);
  if (first >= 0) wma(raw.slice(first) as number[], Math.max(1, Math.round(Math.sqrt(n)))).forEach((v, i) => (out[first + i] = v));
  return out;
}

/** Kaufman adaptive average: it follows price quickly when the move is efficient and slowly when it is choppy. Seeded with the close before the
 *  first output, as TA-Lib does. */
export function kama(values: number[], n: number): Series {
  const out = blank(values.length);
  if (values.length <= n) return out;
  const fast = 2 / 3;
  const slow = 2 / 31;
  let prev = values[n - 1];
  for (let i = n; i < values.length; i++) {
    let path = 0;
    for (let j = i - n + 1; j <= i; j++) path += Math.abs(values[j] - values[j - 1]);
    const efficiency = path === 0 ? 0 : Math.abs(values[i] - values[i - n]) / path;
    const factor = (efficiency * (fast - slow) + slow) ** 2;
    prev = prev + factor * (values[i] - prev);
    out[i] = prev;
  }
  return out;
}

/** The difference of two simple averages, as a percent of the slow one (PPO) or as a price difference (APO). */
export function ppo(values: number[], fast: number, slow: number, percent: boolean): Series {
  const f = sma(values, fast);
  const s = sma(values, slow);
  return values.map((_, i) => {
    if (f[i] === null || s[i] === null) return null;
    const diff = (f[i] as number) - (s[i] as number);
    return percent ? (diff / (s[i] as number)) * 100 : diff;
  });
}

/** Momentum: the change over n bars. */
export function mom(values: number[], n: number): Series {
  const out = blank(values.length);
  for (let i = n; i < values.length; i++) out[i] = values[i] - values[i - n];
  return out;
}

/** Chande momentum oscillator: Wilder-smoothed gains against losses, from -100 to 100 (it equals 2 x RSI - 100). */
export function cmo(values: number[], n: number): Series {
  return rsi(values, n).map((v) => (v === null ? null : 2 * v - 100));
}

/** Stochastic of the RSI: where the RSI sits in its own `k`-bar range (%K), and its `d`-bar simple average (%D). */
export function stochRsi(values: number[], n: number, k: number, d: number): { k: Series; d: Series } {
  const r = rsi(values, n);
  const first = r.findIndex((v) => v !== null);
  const kline = blank(values.length);
  for (let i = first + k - 1; first >= 0 && i < values.length; i++) {
    let hi = -Infinity;
    let lo = Infinity;
    for (let j = i - k + 1; j <= i; j++) {
      hi = Math.max(hi, r[j] as number);
      lo = Math.min(lo, r[j] as number);
    }
    kline[i] = hi === lo ? 0 : (((r[i] as number) - lo) / (hi - lo)) * 100;
  }
  const dline = blank(values.length);
  const start = kline.findIndex((v) => v !== null);
  if (start >= 0) sma(kline.slice(start) as number[], d).forEach((v, i) => (dline[start + i] = v));
  return { k: kline, d: dline };
}

/** Ultimate oscillator: buying pressure over true range on three windows, weighted 4, 2 and 1. */
export function ultosc(h: number[], l: number[], c: number[], a: number, b: number, z: number): Series {
  const out = blank(c.length);
  const bp = blank(c.length);
  const tr = blank(c.length);
  for (let i = 1; i < c.length; i++) {
    const low = Math.min(l[i], c[i - 1]);
    bp[i] = c[i] - low;
    tr[i] = Math.max(h[i], c[i - 1]) - low;
  }
  const ratio = (i: number, n: number) => {
    let p = 0;
    let t = 0;
    for (let j = i - n + 1; j <= i; j++) {
      p += bp[j] as number;
      t += tr[j] as number;
    }
    return t === 0 ? 0 : p / t;
  };
  for (let i = z; i < c.length; i++) out[i] = (100 * (4 * ratio(i, a) + 2 * ratio(i, b) + ratio(i, z))) / 7;
  return out;
}

const flowOf = (h: number[], l: number[], c: number[], v: number[]): number[] =>
  c.map((_, i) => (h[i] === l[i] ? 0 : ((c[i] - l[i] - (h[i] - c[i])) / (h[i] - l[i])) * v[i]));

/** Accumulation/distribution: a running total of where each bar closed in its range, times its volume. */
export function ad(h: number[], l: number[], c: number[], v: number[]): Series {
  let total = 0;
  return flowOf(h, l, c, v).map((x) => (total += x));
}

/** Chaikin oscillator: a fast minus a slow exponential average of accumulation/distribution, both started at its first value as TA-Lib does. */
export function adosc(h: number[], l: number[], c: number[], v: number[], fast: number, slow: number): Series {
  const line = ad(h, l, c, v) as number[];
  const out = blank(c.length);
  const kf = 2 / (fast + 1);
  const ks = 2 / (slow + 1);
  let f = line[0];
  let s = line[0];
  for (let i = 1; i < line.length; i++) {
    f = line[i] * kf + f * (1 - kf);
    s = line[i] * ks + s * (1 - ks);
    if (i >= slow - 1) out[i] = f - s;
  }
  return out;
}

/** Chaikin money flow: money flow volume over volume across n bars. */
export function cmf(h: number[], l: number[], c: number[], v: number[], n: number): Series {
  const flow = flowOf(h, l, c, v);
  const out = blank(c.length);
  for (let i = n - 1; i < c.length; i++) {
    let money = 0;
    let volume = 0;
    for (let j = i - n + 1; j <= i; j++) {
      money += flow[j];
      volume += v[j];
    }
    out[i] = volume === 0 ? 0 : money / volume;
  }
  return out;
}

/** Force index: the change in close times volume, smoothed exponentially. */
export function force(c: number[], v: number[], n: number): Series {
  const raw = blank(c.length);
  for (let i = 1; i < c.length; i++) raw[i] = (c[i] - c[i - 1]) * v[i];
  return emaOfSeries(raw, n);
}

/** Awesome oscillator: the 5-bar minus the 34-bar simple average of the bar midpoint. */
export function awesome(h: number[], l: number[]): Series {
  const mid = h.map((x, i) => (x + l[i]) / 2);
  const a = sma(mid, 5);
  const b = sma(mid, 34);
  return mid.map((_, i) => (a[i] !== null && b[i] !== null ? (a[i] as number) - (b[i] as number) : null));
}

/** Average true range as a percent of the close. */
export function natr(h: number[], l: number[], c: number[], n: number): Series {
  return atr(h, l, c, n).map((v, i) => (v === null ? null : (v / c[i]) * 100));
}

/** Plus and minus directional indicators, Wilder-smoothed with the same warm-up as `adx`: the first sums cover bars 1 to n-1 and the first output is
 *  the smoothing step of bar n, as TA-Lib's DI lines do. */
export function directional(h: number[], l: number[], c: number[], n: number): { plus: Series; minus: Series } {
  const plus = blank(c.length);
  const minus = blank(c.length);
  if (c.length <= n) return { plus, minus };
  let p = 0;
  let m = 0;
  let t = 0;
  const parts = (i: number) => {
    const up = h[i] - h[i - 1];
    const down = l[i - 1] - l[i];
    return {
      p: up > down && up > 0 ? up : 0,
      m: down > up && down > 0 ? down : 0,
      t: Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])),
    };
  };
  for (let i = 1; i < n; i++) {
    const x = parts(i);
    p += x.p;
    m += x.m;
    t += x.t;
  }
  for (let i = n; i < c.length; i++) {
    const x = parts(i);
    p = p - p / n + x.p;
    m = m - m / n + x.m;
    t = t - t / n + x.t;
    plus[i] = t ? (100 * p) / t : 0;
    minus[i] = t ? (100 * m) / t : 0;
  }
  return { plus, minus };
}

/** Vortex indicator: upward and downward movement against true range over n bars. */
export function vortex(h: number[], l: number[], c: number[], n: number): { plus: Series; minus: Series } {
  const plus = blank(c.length);
  const minus = blank(c.length);
  for (let i = n; i < c.length; i++) {
    let vp = 0;
    let vm = 0;
    let t = 0;
    for (let j = i - n + 1; j <= i; j++) {
      vp += Math.abs(h[j] - l[j - 1]);
      vm += Math.abs(l[j] - h[j - 1]);
      t += Math.max(h[j] - l[j], Math.abs(h[j] - c[j - 1]), Math.abs(l[j] - c[j - 1]));
    }
    plus[i] = t ? vp / t : 0;
    minus[i] = t ? vm / t : 0;
  }
  return { plus, minus };
}

/** Choppiness index: near 100 when price goes nowhere and near 0 in a clean trend, from the sum of true ranges against the n-bar range. */
export function choppiness(h: number[], l: number[], c: number[], n: number): Series {
  const out = blank(c.length);
  const tr = trueRange(h, l, c);
  for (let i = n; i < c.length; i++) {
    let sum = 0;
    for (let j = i - n + 1; j <= i; j++) sum += tr[j] as number;
    const range = highest(h, i, n) - lowest(l, i, n);
    out[i] = range > 0 ? (100 * Math.log10(sum / range)) / Math.log10(n) : null;
  }
  return out;
}

/** Linear regression over n bars: the fitted value at the last bar, and the slope per bar. */
export function linreg(values: number[], n: number): { value: Series; slope: Series } {
  const value = blank(values.length);
  const slope = blank(values.length);
  const meanX = (n - 1) / 2;
  const sxx = Array.from({ length: n }, (_, x) => (x - meanX) ** 2).reduce((a, b) => a + b, 0);
  for (let i = n - 1; i < values.length; i++) {
    let sum = 0;
    for (let x = 0; x < n; x++) sum += values[i - n + 1 + x];
    const meanY = sum / n;
    let sxy = 0;
    for (let x = 0; x < n; x++) sxy += (x - meanX) * (values[i - n + 1 + x] - meanY);
    const b = sxy / sxx;
    slope[i] = b;
    value[i] = meanY + b * (n - 1 - meanX);
  }
  return { value, slope };
}

/** Standard deviation of the close over n bars (population, as in the Bollinger bands). */
export function stddev(values: number[], n: number): Series {
  const mean = sma(values, n);
  const out = blank(values.length);
  for (let i = n - 1; i < values.length; i++) {
    let sq = 0;
    for (let j = i - n + 1; j <= i; j++) sq += (values[j] - (mean[i] as number)) ** 2;
    out[i] = Math.sqrt(sq / n);
  }
  return out;
}

/** Historical volatility: the standard deviation of log returns over n bars, scaled to a year of 252 sessions, in percent. */
export function volatility(values: number[], n: number): Series {
  const returns = blank(values.length);
  for (let i = 1; i < values.length; i++) returns[i] = Math.log(values[i] / values[i - 1]);
  const out = blank(values.length);
  for (let i = n; i < values.length; i++) {
    const window = returns.slice(i - n + 1, i + 1) as number[];
    const mean = window.reduce((a, b) => a + b, 0) / n;
    out[i] = Math.sqrt(window.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) * Math.sqrt(252) * 100;
  }
  return out;
}

/** Bollinger %B (where the close sits between the bands, 0 to 1) and bandwidth (the bands' width over the middle, in percent). */
export function bollingerStats(values: number[], n: number, k: number): { percentB: Series; width: Series } {
  const b = bollinger(values, n, k);
  return {
    percentB: values.map((v, i) => (b.upper[i] !== null && b.upper[i] !== b.lower[i] ? (v - (b.lower[i] as number)) / ((b.upper[i] as number) - (b.lower[i] as number)) : null)),
    width: values.map((_, i) => (b.upper[i] !== null && b.mid[i] ? (((b.upper[i] as number) - (b.lower[i] as number)) / (b.mid[i] as number)) * 100 : null)),
  };
}

/** Balance of power: where the close is against the open, over the bar's range. */
export function bop(o: number[], h: number[], l: number[], c: number[]): Series {
  return c.map((_, i) => (h[i] === l[i] ? 0 : (c[i] - o[i]) / (h[i] - l[i])));
}

/** Aroon oscillator: Aroon up minus Aroon down. */
export function aroonOsc(h: number[], l: number[], n: number): Series {
  const a = aroon(h, l, n);
  return a.up.map((v, i) => (v !== null && a.down[i] !== null ? v - (a.down[i] as number) : null));
}
