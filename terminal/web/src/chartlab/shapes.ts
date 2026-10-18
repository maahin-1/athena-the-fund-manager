/** The picture of the chart types the library has no series for (high-low, volume candles, Kagi, point and figure), as plain shapes in pixels. These
 *  functions know nothing about a canvas or the chart: they take each bar's horizontal position and a function from price to vertical position, and
 *  return what to paint, so they can be tested with numbers. `role` says which of the theme's colours a shape takes; the painter chooses the colour. */

/** One bar or line or column of the chart as the painter hands it over. The optional fields belong to the type: `thick` and `flip` to Kagi, `up` and `box`
 *  to point and figure. */
export interface Datum {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  thick?: boolean;
  flip?: number;
  up?: boolean;
  box?: number;
}

/** A datum with its horizontal position in the pane, in pixels. */
export interface Item {
  x: number;
  d: Datum;
}

/** Price to vertical pixel, or null when the price has no position (off the scale, or the chart is not ready). */
export type Y = (price: number) => number | null;

export type Role = 'up' | 'down' | 'accent' | 'thick' | 'thin';

export type Shape =
  | { k: 'rect'; x: number; y: number; w: number; h: number; role: Role }
  | { k: 'line'; x1: number; y1: number; x2: number; y2: number; role: Role; width: number }
  | { k: 'ellipse'; cx: number; cy: number; rx: number; ry: number; role: Role };

export const THICK_WIDTH = 3;
export const THIN_WIDTH = 1;

const direction = (d: Datum): Role => (d.close >= d.open ? 'up' : 'down');

/** A box from price `a` to price `b`, at least one pixel tall; null when either has no position. */
function band(y: Y, a: number, b: number): { top: number; height: number } | null {
  const ya = y(a);
  const yb = y(b);
  if (ya === null || yb === null) return null;
  return { top: Math.min(ya, yb), height: Math.max(1, Math.abs(ya - yb)) };
}

/** High-low: a floating bar from each bar's low to its high, in one colour. */
export function highLowShapes(items: Item[], y: Y, spacing: number): Shape[] {
  const w = Math.max(1, spacing * 0.6);
  const out: Shape[] = [];
  for (const { x, d } of items) {
    const box = band(y, d.high, d.low);
    if (box) out.push({ k: 'rect', x: x - w / 2, y: box.top, w, h: box.height, role: 'accent' });
  }
  return out;
}

/** Volume candles: the usual candle (a wick from high to low, a body from open to close, up or down colour), the body `spacing * 0.9` wide for the
 *  largest volume in view and in proportion for the others, never under one pixel. Without a largest volume the bodies are a plain 0.6 of the spacing. */
export function volumeShapes(items: Item[], y: Y, spacing: number, largest: number): Shape[] {
  const out: Shape[] = [];
  for (const { x, d } of items) {
    const role = direction(d);
    const wick = [y(d.high), y(d.low)];
    if (wick[0] !== null && wick[1] !== null) out.push({ k: 'line', x1: x, y1: wick[0], x2: x, y2: wick[1], role, width: THIN_WIDTH });
    const body = band(y, Math.max(d.open, d.close), Math.min(d.open, d.close));
    if (!body) continue;
    const w = largest > 0 ? Math.max(1, spacing * 0.9 * (d.volume / largest)) : spacing * 0.6;
    out.push({ k: 'rect', x: x - w / 2, y: body.top, w, h: body.height, role });
  }
  return out;
}

/** Kagi: each line vertical at its bar, thick or thin by its weight at its end and split at the price where it changed weight; a horizontal joins each
 *  line to the one before at the price the earlier ended, in the weight the earlier ended in. */
export function kagiShapes(items: Item[], y: Y, _spacing: number): Shape[] {
  const out: Shape[] = [];
  const weight = (thick: boolean): { role: Role; width: number } => (thick ? { role: 'thick', width: THICK_WIDTH } : { role: 'thin', width: THIN_WIDTH });
  items.forEach(({ x, d }, i) => {
    const previous = items[i - 1];
    if (previous) {
      const at = y(previous.d.close);
      if (at !== null) out.push({ k: 'line', x1: previous.x, y1: at, x2: x, y2: at, ...weight(!!previous.d.thick) });
    }
    const start = y(d.open);
    const end = y(d.close);
    if (start === null || end === null) return;
    const flip = d.flip !== undefined ? y(d.flip) : null;
    if (flip !== null && d.flip !== undefined && d.flip >= Math.min(d.open, d.close) && d.flip <= Math.max(d.open, d.close)) {
      out.push({ k: 'line', x1: x, y1: start, x2: x, y2: flip, ...weight(!d.thick) });
      out.push({ k: 'line', x1: x, y1: flip, x2: x, y2: end, ...weight(!!d.thick) });
    } else {
      out.push({ k: 'line', x1: x, y1: start, x2: x, y2: end, ...weight(!!d.thick) });
    }
  });
  return out;
}

/** Point and figure: one X (two strokes) in each box of a rising column and one O in each box of a falling one, boxes stacked from the column's low
 *  edge, each glyph centred in its box and kept inside it. */
export function pnfShapes(items: Item[], y: Y, spacing: number): Shape[] {
  const out: Shape[] = [];
  for (const { x, d } of items) {
    const size = d.box ?? 0;
    if (!(size > 0)) continue;
    const boxes = Math.max(1, Math.round((d.high - d.low) / size));
    for (let i = 0; i < boxes; i++) {
      const bottom = y(d.low + i * size);
      const top = y(d.low + (i + 1) * size);
      if (bottom === null || top === null) continue;
      const cy = (bottom + top) / 2;
      const r = Math.min(spacing, Math.abs(bottom - top)) * 0.4;
      if (d.up) {
        out.push({ k: 'line', x1: x - r, y1: cy - r, x2: x + r, y2: cy + r, role: 'up', width: THIN_WIDTH });
        out.push({ k: 'line', x1: x - r, y1: cy + r, x2: x + r, y2: cy - r, role: 'up', width: THIN_WIDTH });
      } else {
        out.push({ k: 'ellipse', cx: x, cy, rx: r, ry: r, role: 'down' });
      }
    }
  }
  return out;
}
