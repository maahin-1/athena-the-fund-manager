/** The formula language's tree: exactly the strategy model's version 1 (`athena.strategies`), so what the page writes is what the backtest engine reads and
 *  what the page evaluates is what the engine evaluates. Python validates every tree again; this file only says what shape one has. */

export type PriceField = 'open' | 'high' | 'low' | 'close' | 'volume';

export type Expr =
  | { const: number }
  | { price: PriceField; shift?: number }
  | { ind: string; params: Record<string, number>; line: string; shift?: number }
  | { rolling: 'max' | 'min' | 'mean'; of: Expr; window: number; shift?: number }
  | { arith: 'add' | 'sub' | 'mul' | 'div'; left: Expr; right: Expr; shift?: number };

export type CompareOp = 'gt' | 'ge' | 'lt' | 'le' | 'crosses_above' | 'crosses_below';

export type Cond = { op: CompareOp; left: Expr; right: Expr } | { all: Cond[] } | { any: Cond[] } | { not: Cond };

export type Compiled = { kind: 'expr'; tree: Expr } | { kind: 'cond'; tree: Cond };

export const MAX_NODES = 60;
export const MAX_DEPTH = 8;
export const MAX_CONDITIONS = 8;
export const MAX_WINDOW = 1000;
export const MAX_SHIFT = 500;

export class FormulaError extends Error {
  constructor(message: string, public at: number, public length: number) {
    super(message);
  }
}
