import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { INDICATORS } from '../src/formula/catalogue';
import { Context, evalCond, evalExpr } from '../src/formula/eval';
import goldenFile from './fixtures/formula-golden.json';
import { compile } from '../src/formula/parse';

/** The golden file is written by `scripts/formula_golden.py` from the real strategy engine (TA-Lib underneath). Each case holds a formula's text, the tree it must
 *  compile to, and what the engine computed for that tree on fixed bars. */
interface Case {
  name: string;
  text: string;
  kind: 'expr' | 'cond';
  tree: unknown;
  values?: (number | null)[];
  value?: boolean[];
  known?: boolean[];
}
const golden = goldenFile as unknown as {
  bars: { day: string; o: number; h: number; l: number; c: number; v: number }[];
  catalogue: { key: string; placement: string; params: { name: string; default: number; min: number; max: number; integer: boolean }[]; lines: string[] }[];
  cases: Case[];
};
const bars: Bar[] = golden.bars.map((b, i) => ({ t: 1_700_000_000 + i * 86_400, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v }));
const ctx = new Context(bars);

const close = (a: number, b: number | null): boolean => (b === null ? Number.isNaN(a) : Number.isFinite(a) && Math.abs(a - b) <= 1e-6 + 1e-7 * Math.abs(b));

describe('the formula catalogue and the strategy engine', () => {
  it('state the same indicators, settings, ranges and lines', () => {
    expect(INDICATORS.map((i) => ({ key: i.key, placement: i.placement, params: i.params, lines: i.lines }))).toEqual(golden.catalogue);
  });
});

describe('every golden case', () => {
  it('has text that compiles to the tree the engine was given', () => {
    for (const c of golden.cases) {
      const r = compile(c.text);
      expect(r.ok, `${c.name}: ${c.text}`).toBe(true);
      if (r.ok) {
        expect(r.value.kind, c.name).toBe(c.kind);
        expect(r.value.tree, `${c.name}: ${c.text}`).toEqual(c.tree);
      }
    }
  });

  it('is evaluated by the page to the numbers the engine computed', () => {
    const wrong: string[] = [];
    for (const c of golden.cases.filter((k) => k.kind === 'expr')) {
      const got = evalExpr(c.tree as never, ctx);
      const first = got.findIndex((x, i) => !close(x, c.values![i]));
      if (first >= 0) wrong.push(`${c.name} [${c.text}] at bar ${first}: page ${got[first]}, engine ${c.values![first]}`);
    }
    expect(wrong).toEqual([]);
  });

  it('is evaluated by the page to the signals and the unknowns the engine found', () => {
    const wrong: string[] = [];
    for (const c of golden.cases.filter((k) => k.kind === 'cond')) {
      const got = evalCond(c.tree as never, ctx);
      const v = got.value.findIndex((x, i) => x !== c.value![i]);
      const k = got.known.findIndex((x, i) => x !== c.known![i]);
      if (v >= 0) wrong.push(`${c.name} [${c.text}] value differs at bar ${v}`);
      if (k >= 0) wrong.push(`${c.name} [${c.text}] known differs at bar ${k}`);
    }
    expect(wrong).toEqual([]);
  });

  it('the cases do something: indicators have numbers, conditions fire sometimes and are sometimes unknown', () => {
    for (const c of golden.cases.filter((k) => k.kind === 'expr' && /\(/.test(k.text) && !k.name.includes('can be zero'))) expect(c.values!.some((v) => v !== null), c.name).toBe(true);
    const fires = golden.cases.filter((k) => k.kind === 'cond' && k.value!.some(Boolean)).length;
    expect(fires).toBeGreaterThan(8);
    expect(golden.cases.some((k) => k.kind === 'cond' && k.known!.some((x) => !x))).toBe(true);
  });
});
