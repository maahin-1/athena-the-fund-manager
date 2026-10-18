import type { Bar } from '../bars';
import type { Cond, Expr } from './ast';
import { computeIndicator, type Lines, type Values } from './indicators';

/** Evaluate a formula tree over bars, the way the strategy engine does (`athena.strategies.compile`): the value at bar t uses only bars up to t; where a value
 *  cannot be computed (warm-up, a shift past the start, a window not yet full, a division by zero) it is unknown (NaN), and a comparison involving an unknown is
 *  neither true nor false: `not` of an unknown stays unknown, so a bar with a gap is never a signal. */

export interface Truth {
  /** True only where the condition holds and is known. */
  value: boolean[];
  /** Where it could be decided at all. */
  known: boolean[];
}

export class Context {
  private indicators = new Map<string, Lines>();
  private fields: Record<string, Values>;

  constructor(readonly bars: Bar[]) {
    this.fields = { open: bars.map((b) => b.o), high: bars.map((b) => b.h), low: bars.map((b) => b.l), close: bars.map((b) => b.c), volume: bars.map((b) => b.v) };
  }

  get size(): number {
    return this.bars.length;
  }

  price(field: string): Values {
    return this.fields[field];
  }

  indicator(key: string, params: Record<string, number>): Lines {
    const id = `${key}:${JSON.stringify(params)}`;
    let lines = this.indicators.get(id);
    if (!lines) {
      lines = computeIndicator(key, params, this.bars);
      this.indicators.set(id, lines);
    }
    return lines;
  }
}

function shift(values: Values, bars: number): Values {
  if (bars === 0) return values;
  const out = new Array<number>(values.length).fill(NaN);
  for (let i = bars; i < values.length; i++) out[i] = values[i - bars];
  return out;
}

function rolling(values: Values, fn: 'max' | 'min' | 'mean', window: number): Values {
  const out = new Array<number>(values.length).fill(NaN);
  for (let i = window - 1; i < values.length; i++) {
    let acc = fn === 'max' ? -Infinity : fn === 'min' ? Infinity : 0;
    let unknown = false;
    for (let j = i - window + 1; j <= i; j++) {
      const x = values[j];
      if (!Number.isFinite(x)) {
        unknown = true;
        break;
      }
      acc = fn === 'max' ? Math.max(acc, x) : fn === 'min' ? Math.min(acc, x) : acc + x;
    }
    out[i] = unknown ? NaN : fn === 'mean' ? acc / window : acc;
  }
  return out;
}

export function evalExpr(e: Expr, ctx: Context): Values {
  if ('const' in e) return new Array<number>(ctx.size).fill(e.const);
  const by = e.shift ?? 0;
  if ('price' in e) return shift(ctx.price(e.price), by);
  if ('ind' in e) return shift(ctx.indicator(e.ind, e.params)[e.line], by);
  if ('rolling' in e) return shift(rolling(evalExpr(e.of, ctx), e.rolling, e.window), by);
  const left = evalExpr(e.left, ctx);
  const right = evalExpr(e.right, ctx);
  const out = left.map((a, i) => {
    const b = right[i];
    const r = e.arith === 'add' ? a + b : e.arith === 'sub' ? a - b : e.arith === 'mul' ? a * b : a / b;
    return Number.isFinite(r) ? r : NaN; // a division by a zero value is unknown, not infinite
  });
  return shift(out, by);
}

export function evalCond(c: Cond, ctx: Context): Truth {
  if ('op' in c) {
    const left = evalExpr(c.left, ctx);
    const right = evalExpr(c.right, ctx);
    const known = left.map((a, i) => Number.isFinite(a) && Number.isFinite(right[i]));
    if (c.op === 'crosses_above' || c.op === 'crosses_below') {
      const pl = shift(left, 1);
      const pr = shift(right, 1);
      const value = left.map((a, i) => {
        const b = right[i];
        const ok = known[i] && Number.isFinite(pl[i]) && Number.isFinite(pr[i]);
        if (!ok) return false;
        return c.op === 'crosses_above' ? a > b && pl[i] <= pr[i] : a < b && pl[i] >= pr[i];
      });
      return { value, known: known.map((k, i) => k && Number.isFinite(pl[i]) && Number.isFinite(pr[i])) };
    }
    const value = left.map((a, i) => {
      if (!known[i]) return false;
      const b = right[i];
      return c.op === 'gt' ? a > b : c.op === 'ge' ? a >= b : c.op === 'lt' ? a < b : a <= b;
    });
    return { value, known };
  }
  if ('not' in c) {
    const { value, known } = evalCond(c.not, ctx);
    return { value: value.map((v, i) => known[i] && !v), known };
  }
  const parts = ('all' in c ? c.all : c.any).map((item) => evalCond(item, ctx));
  const size = ctx.size;
  const value = new Array<boolean>(size);
  const known = new Array<boolean>(size);
  for (let i = 0; i < size; i++) {
    if ('all' in c) {
      const v = parts.every((p) => p.value[i]);
      value[i] = v;
      known[i] = v || parts.some((p) => p.known[i] && !p.value[i]); // all true, or one known false
    } else {
      const v = parts.some((p) => p.value[i]);
      value[i] = v;
      known[i] = v || parts.every((p) => p.known[i] && !p.value[i]); // one true, or all known false
    }
  }
  return { value, known };
}

/** The bars where a condition is a signal: true and known. */
export const signals = (c: Cond, bars: Bar[]): boolean[] => evalCond(c, new Context(bars)).value;

/** A number-valued formula over bars, NaN where unknown. */
export const series = (e: Expr, bars: Bar[]): Values => evalExpr(e, new Context(bars));
