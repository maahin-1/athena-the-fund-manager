import { isIntradayId, parseInterval, stepOf } from '../interval';

/** The chart grid's pure parts: the layouts, what is kept between visits, how a bar of one interval is matched to a moment in another, and how a cell's
 *  actions reach the other cells. No page code is here, so all of it is tested without a browser. */

export type LayoutId = '2h' | '2v' | '3h' | '2x2' | '1+2';

export interface Layout {
  label: string;
  cells: number;
  /** CSS grid templates; cell `i` is the area `c<i>`. */
  columns: string;
  rows: string;
  areas: string;
}

export const LAYOUTS: Record<LayoutId, Layout> = {
  '2h': { label: '2 across', cells: 2, columns: '1fr 1fr', rows: '1fr', areas: '"c0 c1"' },
  '2v': { label: '2 stacked', cells: 2, columns: '1fr', rows: '1fr 1fr', areas: '"c0" "c1"' },
  '3h': { label: '3 across', cells: 3, columns: '1fr 1fr 1fr', rows: '1fr', areas: '"c0 c1 c2"' },
  '2x2': { label: '2 by 2', cells: 4, columns: '1fr 1fr', rows: '1fr 1fr', areas: '"c0 c1" "c2 c3"' },
  '1+2': { label: '1 large, 2 small', cells: 3, columns: '2fr 1fr', rows: '1fr 1fr', areas: '"c0 c1" "c0 c2"' },
};
export const LAYOUT_IDS = Object.keys(LAYOUTS) as LayoutId[];
export const MAX_CELLS = 4;
export const DEFAULT_INTERVALS = ['1D', '1W', '1h', '15m'] as const;

export interface SyncFlags {
  symbol: boolean;
  interval: boolean;
  crosshair: boolean;
  time: boolean;
}

export interface CellState {
  /** null: follow the page's link group (the first time) or the symbol of the other cells. */
  symbol: string | null;
  interval: string;
}

export interface GridState {
  layout: LayoutId;
  sync: SyncFlags;
  /** Always `MAX_CELLS` entries; the cells the layout does not show keep theirs for when it grows again. */
  cells: CellState[];
}

export const GRID_KEY = 'athena.chartlab.grid';

export function defaultGrid(): GridState {
  return {
    layout: '2x2',
    sync: { symbol: true, interval: false, crosshair: true, time: false },
    cells: DEFAULT_INTERVALS.map((interval) => ({ symbol: null, interval })),
  };
}

const SYMBOL = /^[A-Za-z0-9&^ -]{1,24}$/;

/** What was kept, or the defaults for anything missing or not usable. Never throws: a damaged value is as good as none. */
export function parseGridState(text: string | null): GridState {
  const state = defaultGrid();
  if (!text) return state;
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return state;
  }
  if (typeof raw !== 'object' || raw === null) return state;
  const r = raw as Record<string, unknown>;
  if (typeof r.layout === 'string' && (LAYOUT_IDS as string[]).includes(r.layout)) state.layout = r.layout as LayoutId;
  if (typeof r.sync === 'object' && r.sync !== null) {
    const s = r.sync as Record<string, unknown>;
    for (const key of ['symbol', 'interval', 'crosshair', 'time'] as const) if (typeof s[key] === 'boolean') state.sync[key] = s[key] as boolean;
  }
  if (Array.isArray(r.cells)) {
    r.cells.slice(0, MAX_CELLS).forEach((c, i) => {
      if (typeof c !== 'object' || c === null) return;
      const cell = c as Record<string, unknown>;
      if (typeof cell.symbol === 'string' && SYMBOL.test(cell.symbol)) state.cells[i].symbol = cell.symbol.toUpperCase();
      if (typeof cell.interval === 'string') {
        const p = parseInterval(cell.interval);
        if (p.ok) state.cells[i].interval = p.id;
      }
    });
  }
  return state;
}

export const serializeGrid = (state: GridState): string => JSON.stringify(state);

// ---- moments ---------------------------------------------------------------------------------------------------------------------------------

const IST = 19_800;
const DAY = 86_400;
/** 15:29 IST as seconds after midnight UTC of a date: inside the last minute bar of the session, so a day's moment falls in every intraday bar that ends the day. */
const DAY_MOMENT = 15 * 3600 + 29 * 60 - IST;

export interface Stamped {
  t: number;
}

/** The moment a bar stands for when another cell asks what time it is: the start of an intraday bar, or the end of the first trading day of a daily, weekly or
 *  monthly one. */
export function momentOf(barT: number, interval: string): number {
  return isIntradayId(interval) ? barT : barT + DAY_MOMENT;
}

/** Index of the last bar that starts at or before `key`, or -1. Bars are oldest first. */
function lastAtOrBefore(bars: Stamped[], key: number): number {
  let lo = 0;
  let hi = bars.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].t <= key) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/** Whether a moment lies in the bar at `i`: an intraday bar covers its own length; a daily-based one covers its dates, up to the next bar's start. After the
 *  last bar it covers its own period. */
function covers(bars: Stamped[], i: number, moment: number, interval: string): boolean {
  const p = parseInterval(interval);
  if (!p.ok) return false;
  if (isIntradayId(interval)) return moment < bars[i].t + stepOf(p);
  const day = Math.floor((moment + IST) / DAY) * DAY;
  const next = bars[i + 1];
  return next ? day < next.t : day < bars[i].t + stepOf(p) + (p.unit === 'M' ? 2 * DAY : 0); // a month is not always 30 days
}

/** The bar a moment falls in, for a chart of `interval`; null when the moment is before the first bar or after the last one's period. */
export function indexAtMoment(bars: Stamped[], moment: number, interval: string): number | null {
  if (!bars.length || !Number.isFinite(moment)) return null;
  const key = isIntradayId(interval) ? moment : Math.floor((moment + IST) / DAY) * DAY;
  const i = lastAtOrBefore(bars, key);
  return i >= 0 && covers(bars, i, moment, interval) ? i : null;
}

/** The bars of a chart that a period shows: from the bar the first moment is in (the next bar when it falls in a gap, the first bar when it is before them) to
 *  the last bar that began by the last moment. Null when the period is wholly before the bars or after them. */
export function periodIndexes(bars: Stamped[], from: number, to: number, interval: string): { from: number; to: number } | null {
  if (!bars.length || !(to >= from)) return null;
  const key = (m: number): number => (isIntradayId(interval) ? m : Math.floor((m + IST) / DAY) * DAY);
  const last = lastAtOrBefore(bars, key(to));
  if (last < 0) return null; // the period ends before the first bar
  let first = lastAtOrBefore(bars, key(from));
  if (first < 0) first = 0;
  else if (!covers(bars, first, from, interval)) first += 1; // the moment is in a gap or after the last bar: the period begins with the next bar
  return first <= last ? { from: first, to: last } : null;
}

// ---- routing ---------------------------------------------------------------------------------------------------------------------------------

/** What a cell can be told. A cell tells the grid what its person did; the grid tells the other cells. */
export interface CellPort {
  applySymbol(symbol: string): void;
  applyInterval(interval: string): void;
  showMoment(moment: number | null): void;
  showPeriod(from: number, to: number): void;
}

export class GridSync {
  flags: SyncFlags = { ...defaultGrid().sync };
  private cells: CellPort[] = [];

  setCells(cells: CellPort[]): void {
    this.cells = cells;
  }

  private others(origin: CellPort): CellPort[] {
    return this.cells.filter((c) => c !== origin);
  }

  symbolPicked(origin: CellPort, symbol: string): void {
    if (this.flags.symbol) for (const c of this.others(origin)) c.applySymbol(symbol);
  }

  intervalPicked(origin: CellPort, interval: string): void {
    if (this.flags.interval) for (const c of this.others(origin)) c.applyInterval(interval);
  }

  crosshair(origin: CellPort, moment: number | null): void {
    if (this.flags.crosshair) for (const c of this.others(origin)) c.showMoment(moment);
  }

  period(origin: CellPort, from: number, to: number): void {
    if (this.flags.time) for (const c of this.others(origin)) c.showPeriod(from, to);
  }
}
