import type { Bar } from '../../bars';
import type { Anchor } from './model';

/** What the chart provides: a fractional bar index to a pixel x and back, a price to a pixel y and back. Any of them can be null (the chart has nothing to show). */
export interface Axes {
  x(index: number): number | null;
  index(x: number): number | null;
  y(price: number): number | null;
  price(y: number): number | null;
}

/** Between drawing data (a bar's time and a price) and pixels. A time between two bars is placed by interpolating their times; before the first and after the last bar by whole
 *  bar steps, so a drawing can run into the future. Pixel positions go back to the nearest bar (or the nearest whole step beyond the ends). */
export class Projection {
  constructor(
    readonly times: number[],
    readonly step: number,
    private readonly axes: Axes,
  ) {}

  get ready(): boolean {
    return this.times.length > 0;
  }

  /** The fractional index of a time: whole numbers are bars, fractions lie between bars, and beyond the ends the step is the unit. */
  indexOf(t: number): number {
    const n = this.times.length;
    if (n === 0) return 0;
    if (t <= this.times[0]) return (t - this.times[0]) / this.step;
    if (t >= this.times[n - 1]) return n - 1 + (t - this.times[n - 1]) / this.step;
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (this.times[mid] <= t) lo = mid;
      else hi = mid;
    }
    return lo + (t - this.times[lo]) / (this.times[hi] - this.times[lo]);
  }

  /** The time at a fractional index; the inverse of `indexOf`. */
  timeAt(index: number): number {
    const n = this.times.length;
    if (n === 0) return 0;
    if (index <= 0) return this.times[0] + index * this.step;
    if (index >= n - 1) return this.times[n - 1] + (index - (n - 1)) * this.step;
    const i = Math.floor(index);
    return this.times[i] + (index - i) * (this.times[i + 1] - this.times[i]);
  }

  x(t: number): number | null {
    return this.ready ? this.axes.x(this.indexOf(t)) : null;
  }

  y(price: number): number | null {
    return this.axes.y(price);
  }

  /** The time of the bar nearest to a pixel x (a whole step beyond the ends). */
  t(x: number): number | null {
    const index = this.ready ? this.axes.index(x) : null;
    return index === null ? null : this.timeAt(Math.round(index));
  }

  price(y: number): number | null {
    return this.axes.price(y);
  }

  /** The nearest bar's index at a pixel x, or null. */
  barAt(x: number): number | null {
    const index = this.ready ? this.axes.index(x) : null;
    return index === null ? null : Math.round(index);
  }
}

export type Magnet = 'off' | 'weak' | 'strong';
export const MAGNET_PX = 12;

/** The anchor a pointer at (x, y) means. Time snaps to the nearest bar. The price is the pointer's own, unless the magnet is on: weak snaps to the open, high, low or close
 *  of that bar when one is within 12 pixels, strong always to the nearest of the four. Null when the chart cannot say. */
export function snap(projection: Projection, bars: Bar[], x: number, y: number, mode: Magnet): Anchor | null {
  const t = projection.t(x);
  const price = projection.price(y);
  if (t === null || price === null) return null;
  if (mode === 'off') return { t, p: price };
  const i = projection.barAt(x);
  const bar = i === null ? undefined : bars[i];
  if (!bar) return { t, p: price };
  let best: { p: number; d: number } | null = null;
  for (const p of [bar.o, bar.h, bar.l, bar.c]) {
    const py = projection.y(p);
    if (py === null) continue;
    const d = Math.abs(py - y);
    if (!best || d < best.d) best = { p, d };
  }
  if (best && (mode === 'strong' || best.d <= MAGNET_PX)) return { t, p: best.p };
  return { t, p: price };
}
