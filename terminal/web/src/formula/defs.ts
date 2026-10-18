import type { IndicatorDef } from '../chartlab/registry';
import type { Series } from '../chartlab/maths';
import { Context, evalCond, evalExpr } from './eval';
import { compile } from './parse';
import { FormulaError } from './ast';

/** A user's formula as an indicator the Chart Lab can draw, under the id `f:` and its name. */

export interface SavedFormula {
  name: string;
  text: string;
  /** On the price chart, or in a pane of its own. */
  pane: 'overlay' | 'own';
}

export const formulaId = (name: string): string => `f:${name}`;

const toSeries = (values: number[]): Series => values.map((v) => (Number.isFinite(v) ? v : null));

/** The indicator for a formula, or the error that says what is wrong with it. A number-valued formula is one line; a yes-or-no formula is a line of 1 (yes) and
 *  0 (no), empty where it cannot be decided. */
export function defFor(f: SavedFormula): { def: IndicatorDef } | { error: FormulaError } {
  const compiled = compile(f.text);
  if (!compiled.ok) return { error: compiled.error };
  const tree = compiled.value;
  const def: IndicatorDef = {
    id: formulaId(f.name),
    name: f.name,
    pane: f.pane,
    inputs: [],
    outputs: [{ key: 'value', label: f.name, color: '--ind1', kind: 'line', width: 2 }],
    compute: (bars) => {
      const ctx = new Context(bars);
      if (tree.kind === 'expr') return { value: toSeries(evalExpr(tree.tree, ctx)) };
      const truth = evalCond(tree.tree, ctx);
      return { value: truth.value.map((v, i) => (truth.known[i] ? (v ? 1 : 0) : null)) };
    },
  };
  return { def };
}
