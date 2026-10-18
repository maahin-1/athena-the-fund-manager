/** The technicals gauge: a Buy / Sell / Neutral verdict from eleven oscillators and fourteen moving averages, summed to a score. The rules are written in
 *  the design (2026-10-18-symbol-details-design.md section 4) and are our reading of the commonly used definitions, not a copy of any vendor's. */
import type { Bar } from '../bars';
import { ema, rsi, sma, type Series } from '../indicators';
import { adx, cci, macd, stoch, willr } from '../chartlab/maths';
import { awesome, directional, hma, mom, stochRsi, ultosc } from '../chartlab/maths2';
import { resampleDaily } from '../chartlab/resample';

export type Verdict = 'buy' | 'sell' | 'neutral';
export type Label = 'Strong sell' | 'Sell' | 'Neutral' | 'Buy' | 'Strong buy';
export interface Vote {
  name: string;
  value: number | null; // the indicator's latest value
  verdict: Verdict;
}
export interface Summary {
  buy: number;
  neutral: number;
  sell: number;
  score: number; // -1..1
  label: Label;
}
export interface Technicals {
  oscillators: Vote[];
  averages: Vote[];
  oscillatorSummary: Summary;
  averageSummary: Summary;
  summary: Summary;
}
export type Frame = '1D' | '1W' | '1M';

export const MIN_BARS = 30;

export function labelFor(score: number): Label {
  if (score <= -0.5) return 'Strong sell';
  if (score < -0.1) return 'Sell';
  if (score <= 0.1) return 'Neutral';
  if (score < 0.5) return 'Buy';
  return 'Strong buy';
}

export function summarize(votes: Vote[]): Summary {
  const count = (v: Verdict) => votes.filter((x) => x.verdict === v).length;
  const buy = count('buy');
  const sell = count('sell');
  const score = votes.length ? (buy - sell) / votes.length : 0;
  return { buy, sell, neutral: votes.length - buy - sell, score, label: labelFor(score) };
}

/** The verdict of each rule from the latest value and the one before it. Exported so every rule is tested on plain numbers. */
export const RULES = {
  rsi: (v: number, p: number): Verdict => (v < 30 && v > p ? 'buy' : v > 70 && v < p ? 'sell' : 'neutral'),
  stoch: (k: number, d: number): Verdict => (k < 20 && d < 20 && k > d ? 'buy' : k > 80 && d > 80 && k < d ? 'sell' : 'neutral'),
  cci: (v: number, p: number): Verdict => (v < -100 && v > p ? 'buy' : v > 100 && v < p ? 'sell' : 'neutral'),
  adx: (a: number, plus: number, minus: number): Verdict => (a > 20 && plus > minus ? 'buy' : a > 20 && plus < minus ? 'sell' : 'neutral'),
  zeroRising: (v: number, p: number): Verdict => (v > 0 && v > p ? 'buy' : v < 0 && v < p ? 'sell' : 'neutral'), // awesome oscillator and bull bear power
  momentum: (v: number, p: number): Verdict => (v > p ? 'buy' : v < p ? 'sell' : 'neutral'),
  macd: (line: number, signal: number): Verdict => (line > signal ? 'buy' : line < signal ? 'sell' : 'neutral'),
  stochRsi: (k: number, d: number): Verdict => (k < 20 && k > d ? 'buy' : k > 80 && k < d ? 'sell' : 'neutral'),
  williams: (v: number, p: number): Verdict => (v < -80 && v > p ? 'buy' : v > -20 && v < p ? 'sell' : 'neutral'),
  ultimate: (v: number): Verdict => (v > 70 ? 'buy' : v < 30 ? 'sell' : 'neutral'),
  average: (close: number, avg: number): Verdict => (close > avg ? 'buy' : close < avg ? 'sell' : 'neutral'),
};

const fin = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x);
const last = (s: Series, back = 0): number | null => {
  const v = s[s.length - 1 - back];
  return fin(v) ? v : null;
};

/** Simple average of a series that starts with nulls (the smoothing of %K and %D). */
function smaOfSeries(s: Series, n: number): Series {
  const first = s.findIndex((v) => fin(v));
  const out: Series = new Array(s.length).fill(null);
  if (first >= 0) sma(s.slice(first) as number[], n).forEach((v, i) => (out[first + i] = v));
  return out;
}

function vwma(c: number[], v: number[], n: number): Series {
  const out: Series = new Array(c.length).fill(null);
  for (let i = n - 1; i < c.length; i++) {
    let pv = 0;
    let vol = 0;
    for (let j = i - n + 1; j <= i; j++) {
      pv += c[j] * v[j];
      vol += v[j];
    }
    out[i] = vol > 0 ? pv / vol : null;
  }
  return out;
}

/** Daily bars as the chosen frame's bars. */
export function frameBars(bars: Bar[], frame: Frame): Bar[] {
  return frame === '1D' ? bars : resampleDaily(bars, frame === '1W' ? 'W' : 'M');
}

/** The gauge for these bars, or null with fewer than MIN_BARS. An indicator without enough history is left out of the counts. */
export function technicals(bars: Bar[]): Technicals | null {
  if (bars.length < MIN_BARS) return null;
  const h = bars.map((b) => b.h);
  const l = bars.map((b) => b.l);
  const c = bars.map((b) => b.c);
  const v = bars.map((b) => b.v);

  const osc: Vote[] = [];
  const add = (name: string, value: number | null, verdict: Verdict | null) => {
    if (value !== null && verdict !== null) osc.push({ name, value, verdict });
  };
  const two = (a: Series, b: Series): [number, number] | null => {
    const x = last(a);
    const y = last(b);
    return x !== null && y !== null ? [x, y] : null;
  };
  const rising = (s: Series, rule: (v: number, p: number) => Verdict): Verdict | null => {
    const x = last(s);
    const p = last(s, 1);
    return x !== null && p !== null ? rule(x, p) : null;
  };

  const rsiS = rsi(c, 14);
  add('RSI (14)', last(rsiS), rising(rsiS, RULES.rsi));
  const st = stoch(h, l, c, 14, 3, 3);
  const stKD = two(st.k, st.d);
  add('Stochastic %K (14, 3, 3)', last(st.k), stKD && RULES.stoch(stKD[0], stKD[1]));
  const cciS = cci(h, l, c, 20);
  add('CCI (20)', last(cciS), rising(cciS, RULES.cci));
  const adxS = adx(h, l, c, 14);
  const di = directional(h, l, c, 14);
  const a = last(adxS);
  const plus = last(di.plus);
  const minus = last(di.minus);
  add('ADX (14)', a, a !== null && plus !== null && minus !== null ? RULES.adx(a, plus, minus) : null);
  const ao = awesome(h, l);
  add('Awesome oscillator', last(ao), rising(ao, RULES.zeroRising));
  const momS = mom(c, 10);
  add('Momentum (10)', last(momS), rising(momS, RULES.momentum));
  const m = macd(c, 12, 26, 9);
  const mm = two(m.macd, m.signal);
  add('MACD (12, 26, 9)', last(m.macd), mm && RULES.macd(mm[0], mm[1]));
  const sr = stochRsi(c, 14, 14, 3);
  const srK = smaOfSeries(sr.k, 3);
  const srD = smaOfSeries(srK, 3);
  const srKD = two(srK, srD);
  add('Stochastic RSI (14, 14, 3, 3)', last(srK), srKD && RULES.stochRsi(srKD[0], srKD[1]));
  const wr = willr(h, l, c, 14);
  add('Williams %R (14)', last(wr), rising(wr, RULES.williams));
  const e13 = ema(c, 13);
  const bbp: Series = c.map((_, i) => (fin(e13[i]) ? h[i] + l[i] - 2 * (e13[i] as number) : null));
  add('Bull Bear Power (13)', last(bbp), rising(bbp, RULES.zeroRising));
  const uo = ultosc(h, l, c, 7, 14, 28);
  const u = last(uo);
  add('Ultimate oscillator (7, 14, 28)', u, u !== null ? RULES.ultimate(u) : null);

  const avg: Vote[] = [];
  const close = c[c.length - 1];
  const addAvg = (name: string, s: Series) => {
    const x = last(s);
    if (x !== null) avg.push({ name, value: x, verdict: RULES.average(close, x) });
  };
  for (const n of [10, 20, 30, 50, 100, 200]) {
    addAvg(`EMA (${n})`, ema(c, n));
    addAvg(`SMA (${n})`, sma(c, n));
  }
  addAvg('Hull MA (9)', hma(c, 9));
  addAvg('Volume-weighted MA (20)', vwma(c, v, 20));

  const oscillatorSummary = summarize(osc);
  const averageSummary = summarize(avg);
  return { oscillators: osc, averages: avg, oscillatorSummary, averageSummary, summary: summarize([...osc, ...avg]) };
}
