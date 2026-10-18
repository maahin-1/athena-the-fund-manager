import { applyTick, tickFitsChart, type Bar } from '../bars';
import { parseInterval, stepOf } from '../interval';
import type { Interval, Timeframe } from '../ranges';
import { atr, type Series } from './maths';
import { kagi, lineBreak, pointAndFigure, rangeBars, renko, sequence } from './derived';
import { MINUTES } from './resample';
import { defaults, find, validate, type IndicatorDef, type Params } from './registry';

export type ChartType = 'candles' | 'hollow' | 'bars' | 'line' | 'area' | 'baseline' | 'columns' | 'step' | 'heikin' | 'renko' | 'linebreak' | 'range' | 'highlow' | 'volcandles' | 'kagi' | 'pnf';
export type ScaleMode = 'auto' | 'log' | 'percent';
export const CHART_TYPES: { id: ChartType; label: string }[] = [
  { id: 'candles', label: 'Candles' }, { id: 'hollow', label: 'Hollow candles' }, { id: 'bars', label: 'Bars' },
  { id: 'line', label: 'Line' }, { id: 'area', label: 'Area' }, { id: 'baseline', label: 'Baseline' }, { id: 'columns', label: 'Columns' },
  { id: 'step', label: 'Step line' }, { id: 'heikin', label: 'Heikin Ashi' }, { id: 'renko', label: 'Renko' }, { id: 'linebreak', label: 'Line break' },
  { id: 'range', label: 'Range bars' }, { id: 'highlow', label: 'High-low' }, { id: 'volcandles', label: 'Volume candles' }, { id: 'kagi', label: 'Kagi' },
  { id: 'pnf', label: 'Point and figure' },
];

/** Types built from price moves, not time: indicators, compare and volume are not drawn on them. */
export const isDerived = (type: ChartType): boolean => type === 'renko' || type === 'linebreak' || type === 'range' || type === 'kagi' || type === 'pnf';

/** Types the library has no series for: the chart paints them itself from shapes (see `shapes.ts`). */
export const isShapeType = (type: ChartType): boolean => type === 'highlow' || type === 'volcandles' || type === 'kagi' || type === 'pnf';

/** Types drawn as one value per bar (the close) rather than as open, high, low and close. */
export const isValueType = (type: ChartType): boolean => ['line', 'area', 'baseline', 'columns', 'step'].includes(type);

/** Seconds between the stamps given to derived bars, so a time axis shows them in order. */
export function stepSeconds(tf: Interval): number {
  if (!(tf in MINUTES) && !['1D', '1W', '1M'].includes(tf)) {
    const p = parseInterval(tf);
    if (p.ok) return stepOf(p);
  }
  const minutes = MINUTES[tf as Timeframe];
  return minutes ? minutes * 60 : tf === '1W' ? 7 * 86_400 : tf === '1M' ? 30 * 86_400 : 86_400;
}

/** A brick or range size from the bars: the latest 14-bar average true range, rounded to two significant figures; one percent of the last close
 *  when there are too few bars for it. */
export function autoSize(bars: Bar[], factor = 1): number {
  const range = atr(bars.map((b) => b.h), bars.map((b) => b.l), bars.map((b) => b.c), 14);
  const last = [...range].reverse().find((v) => v !== null) ?? (bars.length ? bars[bars.length - 1].c * 0.01 : 1);
  const raw = Math.max((last as number) * factor, 1e-9);
  const digits = Math.floor(Math.log10(raw));
  const unit = 10 ** (digits - 1);
  return Math.max(Math.round(raw / unit) * unit, 1e-9);
}

/** One indicator on the chart. `colors` overrides an output's theme colour by output key. */
export interface IndicatorInstance {
  uid: string;
  id: string;
  params: Params;
  hidden: boolean;
  colors: Record<string, string>;
}

/** Everything about the chart a template or the undo history needs to restore. */
export interface LabState {
  type: ChartType;
  scale: ScaleMode;
  adjusted: boolean;
  indicators: { id: string; params: Params; hidden: boolean; colors: Record<string, string> }[];
  compare: string | null;
}

export interface WindowData {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  change: number;
  changePct: number;
  values: Record<string, Record<string, number | null>>;
}

/** `tick`: only the last bar (and what depends on it) moved; `structure`: bars, indicators, type, scale or compare changed. */
export type ChangeKind = 'tick' | 'structure';
export type TickResult = 'updated' | 'appended' | 'ignored';

/** Heikin Ashi bars from ordinary ones: close is the four-price average, open the midpoint of the previous Heikin Ashi bar. */
export function heikinAshi(bars: Bar[]): Bar[] {
  const out: Bar[] = [];
  for (const b of bars) {
    const close = (b.o + b.h + b.l + b.c) / 4;
    const open = out.length ? (out[out.length - 1].o + out[out.length - 1].c) / 2 : (b.o + b.c) / 2;
    out.push({ t: b.t, o: open, h: Math.max(b.h, open, close), l: Math.min(b.l, open, close), c: close, v: b.v });
  }
  return out;
}

/** The chart's state without any drawing: bars, live ticks, indicators, the compare series and the data window. The view binds to it. */
export class ChartModel {
  bars: Bar[] = [];
  tf: Interval = '1D';
  type: ChartType = 'candles';
  scale: ScaleMode = 'auto';
  adjusted = true;
  indicators: IndicatorInstance[] = [];
  compare: { symbol: string; bars: Bar[] } | null = null;
  private cache = new Map<string, Record<string, Series>>();
  private listeners = new Set<(kind: ChangeKind) => void>();
  private counter = 0;

  subscribe(fn: (kind: ChangeKind) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private changed(kind: ChangeKind = 'structure'): void {
    this.cache.clear();
    this.listeners.forEach((fn) => fn(kind));
  }

  /** The set of formula indicators changed (one was edited or deleted): drop the instances whose formula is gone, forget every computed series and redraw. Not an
   *  undo step. Returns whether any instance was dropped. */
  refreshCustom(): boolean {
    const before = this.indicators.length;
    this.indicators = this.indicators.filter((i) => find(i.id));
    this.cache.clear();
    this.listeners.forEach((fn) => fn('structure'));
    return this.indicators.length !== before;
  }

  setBars(bars: Bar[], tf: Interval): void {
    this.bars = bars;
    this.tf = tf;
    this.changed();
  }

  /** A live trade. The last bar is updated when the trade is in it, a new bar is added when it is in a later one, and a trade that does not fit
   *  (outside the session for an intraday chart, far from the last price, for an earlier bar) changes nothing. */
  tick(price: number, tsSec: number, volume = 0): TickResult {
    const last = this.bars[this.bars.length - 1];
    if (!last || !tickFitsChart(last, price, tsSec, this.tf)) return 'ignored';
    const next = applyTick(last, price, tsSec, this.tf, volume);
    if (next === last) return 'ignored';
    if (next.t === last.t) {
      this.bars[this.bars.length - 1] = next;
      this.changed('tick');
      return 'updated';
    }
    this.bars.push(next);
    this.changed('tick');
    return 'appended';
  }

  addIndicator(defId: string, params?: Params): string | null {
    const def = find(defId);
    if (!def) return null;
    const checked = validate(def, { ...defaults(def), ...params });
    if (!checked.ok) return null;
    const uid = `i${++this.counter}`;
    this.indicators.push({ uid, id: defId, params: checked.params, hidden: false, colors: {} });
    this.changed();
    return uid;
  }

  /** Change an indicator's settings or colours. Returns an error text when a setting is not acceptable (nothing is changed then). */
  updateIndicator(uid: string, params: Params, colors?: Record<string, string>): string | null {
    const inst = this.indicators.find((i) => i.uid === uid);
    const def = inst && find(inst.id);
    if (!inst || !def) return 'no such indicator';
    const checked = validate(def, { ...inst.params, ...params });
    if (!checked.ok) return checked.error;
    inst.params = checked.params;
    if (colors) inst.colors = { ...inst.colors, ...colors };
    this.changed();
    return null;
  }

  hideIndicator(uid: string, hidden: boolean): void {
    const inst = this.indicators.find((i) => i.uid === uid);
    if (inst) {
      inst.hidden = hidden;
      this.changed();
    }
  }

  removeIndicator(uid: string): void {
    this.indicators = this.indicators.filter((i) => i.uid !== uid);
    this.changed();
  }

  setType(type: ChartType): void {
    this.type = type;
    this.changed();
  }

  setScale(scale: ScaleMode): void {
    this.scale = scale;
    this.changed();
  }

  setAdjusted(adjusted: boolean): void {
    this.adjusted = adjusted;
    this.changed();
  }

  setCompare(symbol: string | null, bars: Bar[] = []): void {
    this.compare = symbol ? { symbol, bars } : null;
    this.changed();
  }

  def(inst: IndicatorInstance): IndicatorDef {
    return find(inst.id) as IndicatorDef;
  }

  /** An indicator's series, computed on the real bars (never on Heikin Ashi ones) and kept until the bars or settings change. */
  series(uid: string): Record<string, Series> {
    const hit = this.cache.get(uid);
    if (hit) return hit;
    const inst = this.indicators.find((i) => i.uid === uid);
    if (!inst) return {};
    const out = this.def(inst).compute(this.bars, inst.params);
    this.cache.set(uid, out);
    return out;
  }

  /** The bars as drawn: the real ones, Heikin Ashi bars, or (Renko, line break, range, Kagi, point and figure) bricks, lines and columns stamped in sequence after the first bar. */
  displayBars(): Bar[] {
    if (this.type === 'heikin') return heikinAshi(this.bars);
    if (!isDerived(this.type) || !this.bars.length) return this.bars;
    // Renko bricks and Kagi reversals are one average true range; range bars and point-and-figure boxes half of one (a P&F column turns after three boxes)
    const built: Bar[] =
      this.type === 'renko' ? renko(this.bars, autoSize(this.bars))
      : this.type === 'linebreak' ? lineBreak(this.bars, 3)
      : this.type === 'kagi' ? kagi(this.bars, autoSize(this.bars))
      : this.type === 'pnf' ? pointAndFigure(this.bars, autoSize(this.bars, 0.5), 3)
      : rangeBars(this.bars, autoSize(this.bars, 0.5));
    return sequence(built, this.bars[0].t, stepSeconds(this.tf));
  }

  /** Whether the chart now shows price moves rather than time (so indicators, compare and volume are not drawn). */
  get derived(): boolean {
    return isDerived(this.type);
  }

  /** The compare symbol's closes on this chart's bars, rebased so that it starts level with the chart's own close at the first bar both have. A
   *  bar the compare symbol lacks is `null`. */
  compareSeries(): Series {
    const out: Series = new Array(this.bars.length).fill(null);
    if (!this.compare) return out;
    const theirs = new Map(this.compare.bars.map((b) => [b.t, b.c]));
    const base = this.bars.findIndex((b) => theirs.has(b.t));
    if (base < 0) return out;
    const scale = this.bars[base].c / (theirs.get(this.bars[base].t) as number);
    this.bars.forEach((b, i) => {
      const value = theirs.get(b.t);
      if (value !== undefined && i >= base) out[i] = value * scale;
    });
    return out;
  }

  /** What the data window shows for bar `index`: its prices, change from the bar before, and every visible indicator's values there. */
  window(index: number): WindowData | null {
    const source = this.derived ? this.displayBars() : this.bars;
    const b = source[index];
    if (!b) return null;
    const prev = index > 0 ? source[index - 1].c : b.o;
    const values: WindowData['values'] = {};
    for (const inst of this.indicators.filter((i) => !i.hidden && !this.derived)) {
      const s = this.series(inst.uid);
      values[inst.uid] = Object.fromEntries(this.def(inst).outputs.map((o) => [o.key, s[o.key]?.[index] ?? null]));
    }
    return { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v, change: b.c - prev, changePct: prev ? ((b.c - prev) / prev) * 100 : 0, values };
  }

  state(): LabState {
    return {
      type: this.type, scale: this.scale, adjusted: this.adjusted,
      indicators: this.indicators.map((i) => ({ id: i.id, params: { ...i.params }, hidden: i.hidden, colors: { ...i.colors } })),
      compare: this.compare?.symbol ?? null,
    };
  }

  /** Put a saved state back. The compare symbol is returned for the caller to fetch its bars (the model holds no data it was not given). */
  restore(state: LabState): string | null {
    this.type = state.type;
    this.scale = state.scale;
    this.adjusted = state.adjusted;
    this.indicators = state.indicators.map((i) => ({ uid: `i${++this.counter}`, id: i.id, params: { ...i.params }, hidden: i.hidden, colors: { ...i.colors } }));
    if (!state.compare) this.compare = null;
    else if (this.compare?.symbol !== state.compare) this.compare = { symbol: state.compare, bars: [] }; // no line until `setCompare` gets its bars
    this.changed();
    return state.compare;
  }
}
