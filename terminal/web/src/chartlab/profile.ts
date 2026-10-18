import type { Bar } from '../bars';

/** A volume profile: the volume that traded at each price level over some bars. A bar does not say where inside its range its volume traded, so each bar's
 *  volume is spread evenly over the price rows its low-to-high range covers, in proportion to the overlap. The total over the rows equals the bars' total. */

export const ROWS = { min: 4, max: 200, default: 24 } as const;
export const VALUE_AREA = 0.7;

export interface ProfileRow {
  lo: number;
  hi: number;
  /** Volume of bars that closed at or above their open, and of those that closed below. */
  up: number;
  down: number;
  total: number;
}

export interface Profile {
  rows: ProfileRow[];
  low: number;
  high: number;
  totalVolume: number;
  /** Index of the row with the most volume (the point of control; the lower one when two tie). */
  poc: number;
  /** The rows holding at least 70% of the volume around the point of control, as first and last row index. */
  areaLow: number;
  areaHigh: number;
}

export const clampRows = (n: number): number => Math.min(ROWS.max, Math.max(ROWS.min, Math.round(Number.isFinite(n) ? n : ROWS.default)));

/** The profile of these bars in `rowCount` equal price rows between their lowest low and highest high; null when there is no volume to show. */
export function volumeProfile(bars: Bar[], rowCount: number): Profile | null {
  if (!bars.length) return null;
  let low = Infinity;
  let high = -Infinity;
  let totalVolume = 0;
  for (const b of bars) {
    low = Math.min(low, b.l);
    high = Math.max(high, b.h);
    totalVolume += b.v > 0 ? b.v : 0;
  }
  if (!(totalVolume > 0) || !Number.isFinite(low) || !Number.isFinite(high)) return null;
  const n = high > low ? clampRows(rowCount) : 1; // bars that never moved are one level
  const step = (high - low) / n;
  const rows: ProfileRow[] = Array.from({ length: n }, (_, i) => ({ lo: low + i * step, hi: i === n - 1 ? high : low + (i + 1) * step, up: 0, down: 0, total: 0 }));
  const index = (price: number): number => Math.min(n - 1, Math.max(0, Math.floor((price - low) / step)));
  for (const b of bars) {
    if (!(b.v > 0)) continue;
    const side = b.c >= b.o ? 'up' : 'down';
    if (n === 1 || b.h <= b.l) {
      rows[n === 1 ? 0 : index(b.l)][side] += b.v;
      continue;
    }
    const span = b.h - b.l;
    for (let i = index(b.l); i <= index(b.h); i++) {
      const overlap = Math.min(b.h, rows[i].hi) - Math.max(b.l, rows[i].lo);
      if (overlap > 0) rows[i][side] += (b.v * overlap) / span;
    }
  }
  let poc = 0;
  for (const r of rows) r.total = r.up + r.down;
  rows.forEach((r, i) => {
    if (r.total > rows[poc].total) poc = i;
  });
  // the value area grows from the point of control by the larger of the next two rows on either side
  let areaLow = poc;
  let areaHigh = poc;
  let held = rows[poc].total;
  const sum = (from: number, count: number): number => rows.slice(Math.max(0, from), from + count).reduce((s, r) => s + r.total, 0);
  while (held < VALUE_AREA * totalVolume && (areaLow > 0 || areaHigh < n - 1)) {
    const above = areaHigh < n - 1 ? sum(areaHigh + 1, 2) : -1;
    const below = areaLow > 0 ? sum(areaLow - 2, Math.min(2, areaLow)) : -1;
    if (above >= below) {
      const take = Math.min(2, n - 1 - areaHigh);
      held += sum(areaHigh + 1, take);
      areaHigh += take;
    } else {
      const take = Math.min(2, areaLow);
      held += sum(areaLow - take, take);
      areaLow -= take;
    }
  }
  return { rows, low, high, totalVolume, poc, areaLow, areaHigh };
}

/** The middle price of a row, for the point of control as a number. */
export const rowMid = (r: ProfileRow): number => (r.lo + r.hi) / 2;
