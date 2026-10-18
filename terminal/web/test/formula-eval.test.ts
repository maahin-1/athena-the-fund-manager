import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import type { Cond, Expr } from '../src/formula/ast';
import { Context, evalCond, series, signals } from '../src/formula/eval';
import { compile } from '../src/formula/parse';

const bars = (closes: number[]): Bar[] => closes.map((c, i) => ({ t: i, o: c - 1, h: c + 2, l: c - 3, c, v: 100 * (i + 1) }));
const expr = (text: string): Expr => {
  const r = compile(text);
  if (!r.ok || r.value.kind !== 'expr') throw new Error(text);
  return r.value.tree;
};
const cond = (text: string): Cond => {
  const r = compile(text);
  if (!r.ok || r.value.kind !== 'cond') throw new Error(text);
  return r.value.tree;
};
const b = bars([10, 12, 11, 15, 14, 18]);

describe('numbers', () => {
  it('reads prices and constants for every bar', () => {
    expect(series(expr('close'), b)).toEqual([10, 12, 11, 15, 14, 18]);
    expect(series(expr('open'), b)).toEqual([9, 11, 10, 14, 13, 17]);
    expect(series(expr('volume'), b)).toEqual([100, 200, 300, 400, 500, 600]);
    expect(series(expr('2.5'), b)).toEqual([2.5, 2.5, 2.5, 2.5, 2.5, 2.5]);
  });

  it('shifts a value back, leaving the first bars unknown', () => {
    expect(series(expr('close[1]'), b)).toEqual([NaN, 10, 12, 11, 15, 14]);
    expect(series(expr('close[2]'), b).slice(0, 3)).toEqual([NaN, NaN, 10]);
    expect(series(expr('close[9]'), b).every(Number.isNaN)).toBe(true); // further back than there are bars
  });

  it('does arithmetic bar by bar, and an unknown makes an unknown', () => {
    expect(series(expr('close - close[1]'), b)).toEqual([NaN, 2, -1, 4, -1, 4]);
    expect(series(expr('(high + low) / 2'), b)).toEqual([9.5, 11.5, 10.5, 14.5, 13.5, 17.5]);
    expect(series(expr('close * 2 + 1'), b)[0]).toBe(21);
  });

  it('calls a division by a zero value unknown, never infinite', () => {
    expect(series(expr('close / (close - close)'), b).every(Number.isNaN)).toBe(true);
    expect(series(expr('0 / (close - close)'), b).every(Number.isNaN)).toBe(true);
  });

  it('takes the highest, lowest or average of the last bars, unknown until the window is full', () => {
    expect(series(expr('highest(close, 3)'), b)).toEqual([NaN, NaN, 12, 15, 15, 18]);
    expect(series(expr('lowest(close, 3)'), b)).toEqual([NaN, NaN, 10, 11, 11, 14]);
    expect(series(expr('average(close, 2)'), b)).toEqual([NaN, 11, 11.5, 13, 14.5, 16]);
    expect(series(expr('highest(close, 3)[1]'), b)).toEqual([NaN, NaN, NaN, 12, 15, 15]);
  });

  it('an unknown inside a window makes that window unknown', () => {
    expect(series(expr('average(close[1], 2)'), b)).toEqual([NaN, NaN, 11, 11.5, 13, 14.5]);
  });

  it('computes indicators over the bars, unknown while they warm up', () => {
    const s = series(expr('sma(2)'), bars([1, 2, 3, 4, 5]));
    expect(s).toEqual([NaN, 1.5, 2.5, 3.5, 4.5]);
  });
});

describe('conditions and the unknown', () => {
  it('compares each bar, and a bar where either side is unknown is not a signal', () => {
    expect(signals(cond('close > close[1]'), b)).toEqual([false, true, false, true, false, true]);
    expect(evalCond(cond('close > close[1]'), new Context(b)).known).toEqual([false, true, true, true, true, true]);
  });

  it('crosses above and below need the bar before as well', () => {
    const c = bars([1, 3, 2, 5, 1, 6]);
    expect(signals(cond('close crosses above 2.5'), c)).toEqual([false, true, false, true, false, true]);
    expect(signals(cond('close crosses below 2.5'), c)).toEqual([false, false, true, false, true, false]);
    expect(evalCond(cond('close crosses above 2.5'), new Context(c)).known[0]).toBe(false);
  });

  it('not of an unknown stays unknown, so it is never a signal', () => {
    const t = evalCond(cond('not close > close[1]'), new Context(b));
    expect(t.value).toEqual([false, false, true, false, true, false]);
    expect(t.known).toEqual([false, true, true, true, true, true]);
  });

  it('and is true when all are true, known false when one is known false, and unknown otherwise', () => {
    const unknownThenTrue = cond('close[1] > 0 and close > 0'); // first bar: left unknown, right true
    const t = evalCond(unknownThenTrue, new Context(b));
    expect(t.value[0]).toBe(false);
    expect(t.known[0]).toBe(false);
    const knownFalse = evalCond(cond('close[1] > 0 and close > 1000'), new Context(b)); // first bar: left unknown, right known false
    expect(knownFalse.known[0]).toBe(true);
    expect(knownFalse.value[0]).toBe(false);
  });

  it('or is true when one is true, known false when all are known false, and unknown otherwise', () => {
    const trueWins = evalCond(cond('close[1] > 0 or close > 0'), new Context(b));
    expect(trueWins.value[0]).toBe(true);
    expect(trueWins.known[0]).toBe(true);
    const unknown = evalCond(cond('close[1] > 0 or close > 1000'), new Context(b));
    expect(unknown.value[0]).toBe(false);
    expect(unknown.known[0]).toBe(false);
  });

  it('a whole strategy of an entry and an exit is just two conditions evaluated over the same bars', () => {
    const entry = signals(cond('close > highest(close, 3)[1]'), b);
    const exit = signals(cond('close < lowest(close, 3)[1]'), b);
    expect(entry).toEqual([false, false, false, true, false, true]);
    expect(exit.some(Boolean)).toBe(false);
  });
});

describe('nothing leaks from the future', () => {
  it('the value at a bar does not change when later bars are added', () => {
    const more = bars([10, 12, 11, 15, 14, 18, 3, 40, 2]);
    for (const text of ['sma(3)', 'rsi(3)', 'highest(close, 3)[1]', 'macd(2, 3, 2).macd', 'close - close[2]']) {
      const a = series(expr(text), b);
      const c = series(expr(text), more).slice(0, b.length);
      expect(c, text).toEqual(a);
    }
  });
});
