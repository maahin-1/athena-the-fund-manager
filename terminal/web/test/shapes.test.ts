import { describe, expect, it } from 'vitest';
import { highLowShapes, kagiShapes, pnfShapes, volumeShapes, type Datum, type Item } from '../src/chartlab/shapes';

/** Price to pixel row: a higher price is nearer the top (a smaller y), one pixel to the unit. */
const y = (price: number): number => 200 - price;
const item = (x: number, d: Partial<Datum>): Item => ({ x, d: { open: 0, high: 0, low: 0, close: 0, volume: 0, ...d } });

describe('high-low', () => {
  it('is one floating bar from the low to the high, centred on the bar and as wide as the spacing allows', () => {
    const [s] = highLowShapes([item(50, { open: 11, high: 20, low: 10, close: 12 })], y, 10);
    expect(s).toMatchObject({ k: 'rect', role: 'accent', y: 180, h: 10 });
    if (s.k === 'rect') expect(s.x + s.w / 2).toBeCloseTo(50);
  });

  it('keeps a flat bar one pixel tall so it can be seen, and skips a bar whose prices have no position', () => {
    const [flat] = highLowShapes([item(5, { high: 10, low: 10 })], y, 10);
    expect(flat.k === 'rect' && flat.h).toBe(1);
    expect(highLowShapes([item(5, { high: 10, low: 10 })], () => null, 10)).toEqual([]);
  });
});

describe('volume candles', () => {
  const bars = [item(10, { open: 10, high: 14, low: 9, close: 13, volume: 100 }), item(20, { open: 13, high: 13.5, low: 11, close: 12, volume: 25 })];

  it('widens each body in proportion to its volume against the largest in view', () => {
    const shapes = volumeShapes(bars, y, 10, 100);
    const bodies = shapes.filter((s) => s.k === 'rect');
    expect(bodies.map((b) => (b.k === 'rect' ? b.w : 0))).toEqual([9, 2.25]);
  });

  it('draws a wick from high to low and a body from open to close, up in the up colour and down in the down colour', () => {
    const shapes = volumeShapes(bars, y, 10, 100);
    expect(shapes.filter((s) => s.k === 'line').map((l) => (l.k === 'line' ? [l.y1, l.y2, l.role] : null))).toEqual([[186, 191, 'up'], [186.5, 189, 'down']]);
    expect(shapes.filter((s) => s.k === 'rect').map((b) => (b.k === 'rect' ? [b.y, b.h, b.role] : null))).toEqual([[187, 3, 'up'], [187, 1, 'down']]);
  });

  it('never narrower than one pixel, and a zero largest volume falls back to a plain candle width', () => {
    const thin = volumeShapes([item(10, { open: 1, high: 2, low: 0, close: 1.5, volume: 1 })], y, 10, 1_000_000).find((s) => s.k === 'rect');
    expect(thin?.k === 'rect' && thin.w).toBe(1);
    const plain = volumeShapes([item(10, { open: 1, high: 2, low: 0, close: 1.5, volume: 0 })], y, 10, 0).find((s) => s.k === 'rect');
    expect(plain?.k === 'rect' && plain.w).toBe(6);
  });
});

describe('Kagi', () => {
  const line = (x: number, open: number, close: number, thick: boolean, flip?: number): Item => item(x, { open, close, high: Math.max(open, close), low: Math.min(open, close), thick, flip });

  it('draws each line vertically at its bar, thick or thin, and joins it to the line before at the price where that one ended', () => {
    const shapes = kagiShapes([line(10, 10, 20, true), line(20, 20, 12, false)], y, 10);
    const v = shapes.filter((s) => s.k === 'line' && s.x1 === s.x2);
    expect(v.map((s) => (s.k === 'line' ? [s.x1, s.y1, s.y2, s.role] : null))).toEqual([[10, 190, 180, 'thick'], [20, 180, 188, 'thin']]);
    const h = shapes.filter((s) => s.k === 'line' && s.y1 === s.y2);
    expect(h.map((s) => (s.k === 'line' ? [s.x1, s.x2, s.y1, s.role] : null))).toEqual([[10, 20, 180, 'thick']]); // at 20, in the weight the first line ended in
  });

  it('splits a line that changed weight at the price where it did', () => {
    const shapes = kagiShapes([line(10, 8, 15, true, 14)], y, 10);
    expect(shapes.map((s) => (s.k === 'line' ? [s.y1, s.y2, s.role] : null))).toEqual([[192, 186, 'thin'], [186, 185, 'thick']]);
  });

  it('thick lines are wider than thin ones', () => {
    const [thick, thin] = kagiShapes([line(10, 10, 20, true)], y, 10).concat(kagiShapes([line(10, 10, 20, false)], y, 10));
    expect(thick.k === 'line' && thin.k === 'line' && thick.width > thin.width).toBe(true);
  });
});

describe('Point and figure', () => {
  const column = (x: number, l: number, h: number, up: boolean, box: number): Item => item(x, { low: l, high: h, open: up ? l : h, close: up ? h : l, up, box });

  it('puts one X in each box of a rising column and one O in each box of a falling one', () => {
    const x = pnfShapes([column(10, 10, 13, true, 1)], y, 20);
    expect(x.filter((s) => s.k === 'line')).toHaveLength(6); // three boxes, two strokes each
    const o = pnfShapes([column(10, 10, 13, false, 1)], y, 20);
    expect(o.filter((s) => s.k === 'ellipse')).toHaveLength(3);
    expect(o.every((s) => s.k === 'ellipse' && s.role === 'down')).toBe(true);
    expect(x.every((s) => s.k === 'line' && s.role === 'up')).toBe(true);
  });

  it('centres each glyph in its box, boxes stacked from the low edge', () => {
    const o = pnfShapes([column(10, 10, 12, false, 1)], y, 20).filter((s) => s.k === 'ellipse');
    expect(o.map((s) => (s.k === 'ellipse' ? [s.cx, s.cy] : null))).toEqual([[10, 189.5], [10, 188.5]]);
  });

  it('keeps a glyph inside its box when boxes are shorter than the spacing is wide', () => {
    const [g] = pnfShapes([column(10, 10, 11, false, 1)], y, 40).filter((s) => s.k === 'ellipse');
    expect(g.k === 'ellipse' && g.rx).toBeLessThanOrEqual(0.5); // a 1 px box: no taller than half of it either side
  });
});
