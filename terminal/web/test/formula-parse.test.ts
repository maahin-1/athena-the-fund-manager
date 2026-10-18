import { describe, expect, it } from 'vitest';
import { compile } from '../src/formula/parse';
import { INDICATORS } from '../src/formula/catalogue';

const tree = (text: string) => {
  const r = compile(text);
  if (!r.ok) throw new Error(`${text}: ${r.error.message}`);
  return r.value;
};
const error = (text: string) => {
  const r = compile(text);
  if (r.ok) throw new Error(`${text} should not compile`);
  return r.error;
};
const close = { price: 'close' };
const sma = (length: number) => ({ ind: 'sma', params: { length }, line: 'value' });

describe('values', () => {
  it('reads the price fields, their short names and any case', () => {
    expect(tree('close')).toEqual({ kind: 'expr', tree: close });
    expect(tree('C')).toEqual({ kind: 'expr', tree: close });
    expect(tree('Volume')).toEqual({ kind: 'expr', tree: { price: 'volume' } });
    expect(tree('o').kind).toBe('expr');
  });

  it('reads numbers, and a minus before one is part of it', () => {
    expect(tree('2.5')).toEqual({ kind: 'expr', tree: { const: 2.5 } });
    expect(tree('-5')).toEqual({ kind: 'expr', tree: { const: -5 } });
  });

  it('turns a shift into the shift of the value it follows, and shifts add', () => {
    expect(tree('close[1]')).toEqual({ kind: 'expr', tree: { price: 'close', shift: 1 } });
    expect(tree('close[1][2]')).toEqual({ kind: 'expr', tree: { price: 'close', shift: 3 } });
    expect(tree('(close + open)[2]')).toEqual({ kind: 'expr', tree: { arith: 'add', left: close, right: { price: 'open' }, shift: 2 } });
    expect(tree('sma(20)[1]')).toEqual({ kind: 'expr', tree: { ...sma(20), shift: 1 } });
    expect(error('close[501]').message).toMatch(/shift must be a whole number from 0 to 500/);
    expect(error('close[1.5]').message).toMatch(/whole number/);
  });
});

describe('indicators', () => {
  it('take their settings in order, by name, or from the defaults', () => {
    expect(tree('sma(20)').tree).toEqual(sma(20));
    expect(tree('sma()').tree).toEqual(sma(50));
    expect(tree('SMA(length=10)').tree).toEqual(sma(10));
    expect(tree('macd()').tree).toEqual({ ind: 'macd', params: { fast: 12, slow: 26, signal: 9 }, line: 'macd' });
    expect(tree('rsi(7)').tree).toEqual({ ind: 'rsi', params: { length: 7 }, line: 'value' });
    expect(tree('obv()').tree).toEqual({ ind: 'obv', params: {}, line: 'value' });
  });

  it('pick a line with a dot, the first line when none is named', () => {
    expect(tree('bbands(20, 2).lower').tree).toEqual({ ind: 'bbands', params: { length: 20, width: 2 }, line: 'lower' });
    expect(tree('bbands().upper').tree).toMatchObject({ params: { length: 20, width: 2 }, line: 'upper' });
    expect(tree('macd(12, 26, 9).histogram').tree).toMatchObject({ line: 'histogram' });
    expect(tree('bbands(20, 2)').tree).toMatchObject({ line: 'upper' });
    expect(error('rsi().signal').message).toMatch(/rsi has the line value/);
    expect(error('close.high').message).toMatch(/only an indicator has lines/);
  });

  it('refuse a setting that is out of range, not whole, not a number, unknown or too many', () => {
    expect(error('sma(1)').message).toMatch(/length must be between 2 and 400/);
    expect(error('sma(20.5)').message).toMatch(/length must be a whole number/);
    expect(error('sma(close)').message).toMatch(/settings are numbers/);
    expect(error('sma(foo=3)').message).toMatch(/sma has no setting "foo"; it has length/);
    expect(error('rsi(14, 15)').message).toMatch(/rsi takes at most 1 setting/);
    expect(error('sma(length=10, length=20)').message).toMatch(/length is given twice/);
  });

  it('keep the rule across settings that the Python registry has: the fast length of a MACD is shorter than the slow', () => {
    expect(error('macd(26, 12)').message).toMatch(/fast length must be shorter than the slow length/);
    expect(tree('macd(5, 35)').kind).toBe('expr');
  });

  it('every indicator of the catalogue compiles with no settings and with its own line names', () => {
    for (const i of INDICATORS) {
      expect(tree(`${i.key}()`).kind, i.key).toBe('expr');
      for (const line of i.lines) expect(tree(`${i.key}().${line}`).tree, `${i.key}.${line}`).toMatchObject({ ind: i.key, line });
    }
  });
});

describe('arithmetic', () => {
  it('multiplies before it adds, goes left to right, and takes brackets', () => {
    expect(tree('1 + 2 * 3').tree).toEqual({ arith: 'add', left: { const: 1 }, right: { arith: 'mul', left: { const: 2 }, right: { const: 3 } } });
    expect(tree('10 - 4 - 3').tree).toEqual({ arith: 'sub', left: { arith: 'sub', left: { const: 10 }, right: { const: 4 } }, right: { const: 3 } });
    expect(tree('(1 + 2) * 3').tree).toEqual({ arith: 'mul', left: { arith: 'add', left: { const: 1 }, right: { const: 2 } }, right: { const: 3 } });
    expect(tree('(high + low) / 2').tree).toEqual({ arith: 'div', left: { arith: 'add', left: { price: 'high' }, right: { price: 'low' } }, right: { const: 2 } });
  });

  it('writes a minus before a value as zero minus it', () => {
    expect(tree('-close').tree).toEqual({ arith: 'sub', left: { const: 0 }, right: close });
    expect(tree('2 * -close').tree).toMatchObject({ arith: 'mul', right: { arith: 'sub' } });
  });

  it('refuses to divide by a plain zero', () => {
    expect(error('close / 0').message).toMatch(/cannot divide by zero/);
    expect(tree('close / sma(20)').kind).toBe('expr');
  });
});

describe('comparisons and logic', () => {
  it('compares two values with each operator', () => {
    expect(tree('close > sma(200)')).toEqual({ kind: 'cond', tree: { op: 'gt', left: close, right: sma(200) } });
    expect(tree('close < 1').tree).toMatchObject({ op: 'lt' });
    expect(tree('close <= 1').tree).toMatchObject({ op: 'le' });
    expect(tree('close >= 1').tree).toMatchObject({ op: 'ge' });
  });

  it('says crosses above and crosses below in words or as functions', () => {
    const above = { op: 'crosses_above', left: { ind: 'ema', params: { length: 12 }, line: 'value' }, right: { ind: 'ema', params: { length: 26 }, line: 'value' } };
    expect(tree('ema(12) crosses above ema(26)').tree).toEqual(above);
    expect(tree('crossover(ema(12), ema(26))').tree).toEqual(above);
    expect(tree('crossunder(ema(12), ema(26))').tree).toMatchObject({ op: 'crosses_below' });
    expect(tree('close CROSSES BELOW sma(50)').tree).toMatchObject({ op: 'crosses_below' });
  });

  it('joins a chain of ands into one all, and not binds tightest, then and, then or', () => {
    const a = { op: 'gt', left: close, right: { const: 1 } };
    const b = { op: 'lt', left: close, right: { const: 9 } };
    const c = { op: 'gt', left: { price: 'volume' }, right: { const: 0 } };
    expect(tree('close > 1 and close < 9 and volume > 0').tree).toEqual({ all: [a, b, c] });
    expect(tree('close > 1 or close < 9 and volume > 0').tree).toEqual({ any: [a, { all: [b, c] }] });
    expect(tree('not close > 1 and close < 9').tree).toEqual({ all: [{ not: a }, b] });
    expect(tree('not (close > 1 or close < 9)').tree).toEqual({ not: { any: [a, b] } });
    expect(tree('(close > 1 and close < 9) and volume > 0').tree).toEqual({ all: [a, b, c] });
    expect(tree('RSI(14) < 30 AND NOT Close > Open').kind).toBe('cond');
  });

  it('allows eight conditions in one and, not nine', () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => `close > ${i}`).join(' and ');
    expect(tree(many(8)).kind).toBe('cond');
    expect(error(many(9)).message).toMatch(/more than 8 conditions/);
  });
});

describe('rolling values', () => {
  it('takes the highest, lowest or average of a value over the last bars', () => {
    expect(tree('highest(close, 252)[1]').tree).toEqual({ rolling: 'max', of: close, window: 252, shift: 1 });
    expect(tree('lowest(low, 10)').tree).toEqual({ rolling: 'min', of: { price: 'low' }, window: 10 });
    expect(tree('average(close, 5)').tree).toEqual({ rolling: 'mean', of: close, window: 5 });
    expect(tree('highest(sma(20), 5)').tree).toMatchObject({ rolling: 'max', of: { ind: 'sma' } });
  });

  it('refuses a window outside 2 to 1000 or one that is not a whole number', () => {
    expect(error('highest(close, 1)').message).toMatch(/window must be a whole number from 2 to 1000/);
    expect(error('highest(close, 1001)').message).toMatch(/window must be a whole number from 2 to 1000/);
    expect(error('highest(close, 2.5)').message).toMatch(/whole number/);
    expect(error('highest(close)').message).toMatch(/highest needs a value and a window/);
  });
});

describe('what a formula is', () => {
  it('is a number-valued indicator or a yes-or-no condition, and the two are not mixed', () => {
    expect(tree('sma(20) - sma(50)').kind).toBe('expr');
    expect(tree('close > open').kind).toBe('cond');
    expect(error('close and open > 1').message).toMatch(/and joins yes\/no conditions, and close is a number/);
    expect(error('(close > open) + 1').message).toMatch(/a comparison is yes or no, not a number/);
    expect(error('not close').message).toMatch(/not needs a yes\/no condition/);
    expect(error('(close > open)[1]').message).toMatch(/cannot be shifted/);
    expect(error('close > (open > 1)').message).toMatch(/a comparison is yes or no/);
  });
});

describe('errors', () => {
  it('say where, and suggest the nearest name for a mistyped function', () => {
    const e = error('smaa(20) > 1');
    expect(e.message).toMatch(/unknown function "smaa"; did you mean "sma"\?/);
    expect([e.at, e.length]).toEqual([0, 4]);
    expect(error('zzzz(1)').message).not.toMatch(/did you mean/);
    expect(error('closee > 1').message).toMatch(/unknown name "closee"; did you mean "close"\?/);
  });

  it('catch an empty formula, brackets, stray characters and a missing value', () => {
    expect(error('').message).toMatch(/type a formula/);
    expect(error('   ').message).toMatch(/type a formula/);
    expect(error('sma(20').message).toMatch(/missing "\)"/);
    expect(error('sma(').message).toMatch(/missing "\)"/);
    expect(error('close)').message).toMatch(/unexpected "\)"/);
    expect(error('close > ').message).toMatch(/expected a value after >/);
    expect(error('close # 1').message).toMatch(/unexpected character "#"/);
    expect(error('close close').message).toMatch(/unexpected "close"/);
    expect(error('close > > 1').message).toMatch(/expected a value/);
    expect(error('close crosses 1').message).toMatch(/expected "above" or "below"/);
    expect(error('close[1').message).toMatch(/missing "\]"/);
    expect(error('1 < 2 < 3').message).toMatch(/unexpected "<"/); // comparisons do not chain
  });

  it('count the characters from the start of the text', () => {
    const e = error('close > 1 and sma(1) > 1');
    expect(e.at).toBe(18); // the 1 inside sma(1)
  });
});

describe('limits of the strategy model', () => {
  it('allows at most 60 parts: eight conditions of seven parts each fit, of nine do not', () => {
    const small = Array.from({ length: 8 }, () => '(close + open) > (high + low)').join(' and '); // 1 + 8 * 7 = 57 parts
    expect(tree(small).kind).toBe('cond');
    const big = Array.from({ length: 8 }, () => '(close + open + high) > (low + close)').join(' and '); // 1 + 8 * 9 = 73 parts
    expect(error(big).message).toMatch(/more than 60 parts/);
  });

  it('allows at most 8 levels', () => {
    const deep = (n: number): string => (n === 0 ? '1' : `1 + (${deep(n - 1)})`);
    expect(tree(deep(6)).kind).toBe('expr');
    expect(error(deep(12)).message).toMatch(/nested more than 8 levels/);
    expect(error(Array.from({ length: 12 }, () => 'close').join(' + ')).message).toMatch(/nested more than 8 levels/); // a long chain nests too
  });
});
