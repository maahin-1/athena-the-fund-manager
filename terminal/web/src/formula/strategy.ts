import { compile } from './parse';

/** The strategy builder's logic without a page: a form (a name, an entry formula, an exit formula and the risk options as typed) to the strategy tree the
 *  backtest engine reads (`athena.strategies`, version 1). The engine validates the tree again; this only gives errors early, in the user's own words and at the
 *  place in their formula. */

export interface StrategyForm {
  name: string;
  entry: string;
  exit: string;
  stop_atr: string;
  trailing_stop_atr: string;
  take_profit_pct: string;
  max_hold_bars: string;
  position_pct: string;
}

export type StrategyField = keyof StrategyForm;

export interface StrategyTree {
  version: 1;
  name: string;
  entry: unknown;
  exit: unknown;
  stop_atr?: number;
  trailing_stop_atr?: number;
  take_profit_pct?: number;
  max_hold_bars?: number;
  position_pct?: number;
}

export type Built = { ok: true; tree: StrategyTree } | { ok: false; field: StrategyField; message: string; at?: number };

export const blankForm = (): StrategyForm => ({ name: '', entry: '', exit: '', stop_atr: '', trailing_stop_atr: '', take_profit_pct: '', max_hold_bars: '', position_pct: '' });

/** The limits of the strategy model, with the words shown for each option. */
const RISK: { field: Exclude<StrategyField, 'name' | 'entry' | 'exit'>; label: string; max: number; whole: boolean }[] = [
  { field: 'stop_atr', label: 'the protective stop', max: 10, whole: false },
  { field: 'trailing_stop_atr', label: 'the trailing stop', max: 10, whole: false },
  { field: 'take_profit_pct', label: 'the profit target', max: 1000, whole: false },
  { field: 'max_hold_bars', label: 'the longest hold', max: 1000, whole: true },
  { field: 'position_pct', label: 'the share of cash per entry', max: 100, whole: false },
];

export function buildStrategy(form: StrategyForm): Built {
  const name = form.name.trim();
  if (name.length < 1 || name.length > 60) return { ok: false, field: 'name', message: 'the name must be 1 to 60 characters' };
  const sides: Record<'entry' | 'exit', unknown> = { entry: null, exit: null };
  for (const side of ['entry', 'exit'] as const) {
    if (!form[side].trim()) return { ok: false, field: side, message: `the ${side} needs a formula` };
    const r = compile(form[side]);
    if (!r.ok) return { ok: false, field: side, message: r.error.message, at: r.error.at };
    if (r.value.kind !== 'cond') return { ok: false, field: side, message: `the ${side} must be a yes/no condition (this formula is a number: compare it with something, for example ${form[side].trim()} > 0)` };
    sides[side] = r.value.tree;
  }
  const tree: StrategyTree = { version: 1, name, entry: sides.entry, exit: sides.exit };
  for (const r of RISK) {
    const text = form[r.field].trim();
    if (!text) continue;
    const n = Number(text);
    if (!Number.isFinite(n)) return { ok: false, field: r.field, message: `${r.label} must be a number` };
    if (r.whole && !Number.isInteger(n)) return { ok: false, field: r.field, message: `${r.label} must be a whole number` };
    if (!(n > 0 && n <= r.max)) return { ok: false, field: r.field, message: `${r.label} must be above 0 and at most ${r.max}` };
    tree[r.field] = n;
  }
  return { ok: true, tree };
}

/** The built-in strategies as formulas: where a user can start. A test requires each to build to the tree the engine's own preset holds. */
export const PRESET_FORMS: { key: string; form: StrategyForm }[] = [
  { key: 'breakout_52w', form: { ...blankForm(), name: '52-week high breakout', entry: 'close >= highest(close, 252)[1]', exit: 'close <= lowest(close, 252)[1]' } },
  { key: 'golden_cross', form: { ...blankForm(), name: 'Golden cross', entry: 'sma(50) crosses above sma(200)', exit: 'sma(50) crosses below sma(200)' } },
  { key: 'rsi_reversion', form: { ...blankForm(), name: 'RSI mean reversion', entry: 'rsi(14) < 30', exit: 'rsi(14) > 55', stop_atr: '2' } },
  { key: 'bollinger_rebound', form: { ...blankForm(), name: 'Bollinger rebound', entry: 'close crosses above bbands(20, 2).lower', exit: 'close crosses above sma(20)', stop_atr: '2' } },
];
