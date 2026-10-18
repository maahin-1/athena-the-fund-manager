import { describe, expect, it } from 'vitest';
import { fibLevels, handlesOf, hit, measureOf, pick, positionOf, rgba, shapesOf, spanText, type Shape } from '../src/chartlab/drawings/geometry';
import { newDrawing, update, type Drawing, type Style, type ToolId } from '../src/chartlab/drawings/model';
import { Projection } from '../src/chartlab/drawings/projection';

/** Bar i at x = 10 i + 5, time 1000 i; price p at y = 400 - 2 p. The pane is 600 x 400. */
const times = Array.from({ length: 60 }, (_, i) => i * 1000);
const proj = new Projection(times, 1000, { x: (i) => i * 10 + 5, index: (x) => (x - 5) / 10, y: (p) => 400 - 2 * p, price: (y) => (400 - y) / 2 });
const bounds = { w: 600, h: 400 };
const at = (i: number, p: number) => ({ t: i * 1000, p });
const make = (tool: ToolId, pts: [number, number][], style: Partial<Style> = {}, text?: string): Drawing => newDrawing(tool, pts.map(([i, p]) => at(i, p)), style, text);
const kinds = (s: Shape[]) => s.map((x) => x.k);
const lines = (s: Shape[]) => s.filter((x): x is Extract<Shape, { k: 'line' }> => x.k === 'line');

describe('lines', () => {
  it('a trend line is the segment between its anchors, and continues past either end when asked', () => {
    const d = make('trend', [[10, 100], [20, 120]]); // (105,200) to (205,160)
    const [l] = lines(shapesOf(d, proj, bounds));
    expect([l.x1, l.y1, l.x2, l.y2]).toEqual([105, 200, 205, 160]);
    const right = lines(shapesOf(update([d], d.id, { style: { extendRight: true } })[0], proj, bounds))[0];
    expect(right.x1).toBe(105);
    expect(right.x2).toBeCloseTo(600, 6); // runs to the right edge
    const both = lines(shapesOf(update([d], d.id, { style: { extendLeft: true, extendRight: true } })[0], proj, bounds))[0];
    expect(both.x1).toBeCloseTo(0, 6);
    expect(both.x2).toBeCloseTo(600, 6);
  });

  it('a ray runs from the first anchor through the second to the edge; an extended line runs both ways', () => {
    const ray = lines(shapesOf(make('ray', [[10, 100], [20, 120]]), proj, bounds))[0];
    expect(ray.x1).toBe(105);
    expect(ray.x2).toBeCloseTo(600, 6);
    const ext = lines(shapesOf(make('extended', [[10, 100], [20, 120]]), proj, bounds))[0];
    expect(ext.x1).toBeCloseTo(0, 6);
    expect(ext.x2).toBeCloseTo(600, 6);
  });

  it('an info line is a trend line with its change, percent and bar count written beside it', () => {
    const s = shapesOf(make('info', [[10, 100], [20, 120]]), proj, bounds);
    const t = s.find((x) => x.k === 'text') as Extract<Shape, { k: 'text' }>;
    expect(t.text).toContain('+20.00');
    expect(t.text).toContain('+20.00%');
    expect(t.text).toContain('10 bars');
  });

  it('horizontal line, horizontal ray, vertical line and cross line', () => {
    const h = lines(shapesOf(make('hline', [[10, 100]]), proj, bounds))[0];
    expect([h.x1, h.y1, h.x2, h.y2]).toEqual([0, 200, 600, 200]);
    const hr = lines(shapesOf(make('hray', [[10, 100]]), proj, bounds))[0];
    expect(hr.x1).toBe(105);
    expect(hr.y1).toBe(200);
    expect(hr.x2).toBeCloseTo(600, 6);
    const v = lines(shapesOf(make('vline', [[10, 100]]), proj, bounds))[0];
    expect([v.x1, v.y1, v.x2, v.y2]).toEqual([105, 0, 105, 400]);
    expect(lines(shapesOf(make('cross', [[10, 100]]), proj, bounds))).toHaveLength(2);
  });

  it('a line wholly outside the pane gives no shape, and a hidden drawing gives none', () => {
    expect(lines(shapesOf(make('trend', [[10, 500], [20, 520]]), proj, bounds))).toHaveLength(0);
    const d = make('trend', [[10, 100], [20, 120]]);
    expect(shapesOf(update([d], d.id, { hidden: true })[0], proj, bounds)).toEqual([]);
  });
});

describe('channels', () => {
  it('a parallel channel puts the second line through the third anchor, parallel to the first', () => {
    const d = make('channel', [[10, 100], [30, 110], [20, 120]]); // base (105,200)-(305,180); at x=205 base y=190, third at y=160 -> offset -30
    const s = shapesOf(d, proj, bounds);
    expect(kinds(s)[0]).toBe('poly'); // the fill goes first, under the lines
    const [base, second, median] = lines(s);
    expect([base.y1, base.y2]).toEqual([200, 180]);
    expect([second.y1, second.y2]).toEqual([170, 150]);
    expect([median.y1, median.y2]).toEqual([185, 165]);
    expect(median.dash).toBe('dashed');
  });

  it('flat top/bottom pairs the trend segment with a horizontal line at the third anchor price', () => {
    const d = make('flat', [[10, 100], [30, 110], [20, 130]]);
    const [seg, flat] = lines(shapesOf(d, proj, bounds));
    expect([seg.x1, seg.y1, seg.x2, seg.y2]).toEqual([105, 200, 305, 180]);
    expect([flat.x1, flat.y1, flat.x2, flat.y2]).toEqual([105, 140, 305, 140]);
  });
});

describe('fibonacci', () => {
  it('retracement levels run from the second anchor (0) to the first (1)', () => {
    const d = make('fib', [[10, 100], [30, 200]]); // A = 100, B = 200
    const lv = fibLevels(d);
    expect(lv.map((l) => l.ratio)).toEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
    expect(lv[0].price).toBe(200);
    expect(lv[3].price).toBe(150);
    expect(lv[4].price).toBeCloseTo(138.2, 6);
    expect(lv[6].price).toBe(100);
    const down = fibLevels(make('fib', [[10, 200], [30, 100]]));
    expect(down[0].price).toBe(100);
    expect(down[6].price).toBe(200);
  });

  it('draws a line and a label per level between the anchors, plus the dashed trend line', () => {
    const s = shapesOf(make('fib', [[10, 100], [30, 200]]), proj, bounds);
    expect(lines(s)).toHaveLength(8);
    expect(s.filter((x) => x.k === 'text')).toHaveLength(7);
    const half = lines(s).find((l) => l.y1 === 400 - 2 * 150)!;
    expect([half.x1, half.x2]).toEqual([105, 305]);
    expect(s.find((x) => x.k === 'text' && x.text.startsWith('0.5 '))).toBeTruthy();
  });

  it('the extension projects the first move from the third anchor', () => {
    const d = make('fibext', [[10, 100], [20, 120], [30, 110]]); // move +20 from C = 110
    const lv = fibLevels(d);
    expect(lv.map((l) => l.ratio)).toEqual([0, 0.618, 1, 1.272, 1.618, 2.618]);
    expect(lv[0].price).toBe(110);
    expect(lv[2].price).toBe(130);
    expect(lv[4].price).toBeCloseTo(142.36, 6);
    expect(fibLevels(make('trend', [[1, 1], [2, 2]]))).toEqual([]);
  });
});

describe('shapes and notes', () => {
  it('a rectangle spans its two corners whichever way they are given; an ellipse is inscribed in them', () => {
    const r = shapesOf(make('rect', [[30, 100], [10, 120]]), proj, bounds)[0] as Extract<Shape, { k: 'rect' }>;
    expect([r.x, r.y, r.w, r.h]).toEqual([105, 160, 200, 40]);
    const e = shapesOf(make('ellipse', [[10, 100], [30, 120]]), proj, bounds)[0] as Extract<Shape, { k: 'ellipse' }>;
    expect([e.cx, e.cy, e.rx, e.ry]).toEqual([205, 180, 100, 20]);
  });

  it('a triangle joins its three anchors', () => {
    const t = shapesOf(make('triangle', [[10, 100], [20, 120], [30, 100]]), proj, bounds)[0] as Extract<Shape, { k: 'poly' }>;
    expect(t.pts).toEqual([[105, 200], [205, 160], [305, 200]]);
    expect(t.closed).toBe(true);
  });

  it('text shows its words, a price label shows the price unless given words', () => {
    const t = shapesOf(make('text', [[10, 100]], {}, 'support'), proj, bounds)[0] as Extract<Shape, { k: 'text' }>;
    expect(t.text).toBe('support');
    const l = shapesOf(make('label', [[10, 100]]), proj, bounds).find((x) => x.k === 'text') as Extract<Shape, { k: 'text' }>;
    expect(l.text).toBe('100.00');
  });
});

describe('measure, ranges and positions', () => {
  it('measure reports price change, percent of the first price, bars and time', () => {
    const d = make('measure', [[10, 100], [30, 120]]);
    const m = measureOf(d, proj);
    expect(m).toEqual({ dp: 20, pct: 20, bars: 20, seconds: 20_000 });
    const label = shapesOf(d, proj, bounds).find((x) => x.k === 'text') as Extract<Shape, { k: 'text' }>;
    expect(label.text).toContain('+20.00 (+20.00%)');
    expect(label.text).toContain('20 bars');
    const down = measureOf(make('measure', [[30, 120], [10, 100]]), proj);
    expect(down.dp).toBe(-20);
    expect(down.bars).toBe(20);
    expect(down.pct).toBeCloseTo(-16.6667, 3);
  });

  it('price range and date range draw their boxes and labels', () => {
    const pr = shapesOf(make('pricerange', [[10, 100], [20, 130]]), proj, bounds);
    expect(pr.find((x) => x.k === 'text')).toMatchObject({ text: '+30.00 (+30.00%)' });
    const dr = shapesOf(make('daterange', [[10, 100], [25, 100]]), proj, bounds);
    const box = dr.find((x) => x.k === 'rect') as Extract<Shape, { k: 'rect' }>;
    expect([box.x, box.w, box.y, box.h]).toEqual([105, 150, 0, 400]);
    expect((dr.find((x) => x.k === 'text') as Extract<Shape, { k: 'text' }>).text).toContain('15 bars');
  });

  it('a position tool shows reward and risk boxes and the ratio', () => {
    const d = make('long', [[10, 100], [30, 95], [30, 115]]); // entry 100, stop 95, target 115
    const p = positionOf(d);
    expect(p).toMatchObject({ entry: 100, stop: 95, target: 115, risk: 5, reward: 15, ratio: 3, long: true });
    expect(p.targetPct).toBeCloseTo(15, 9);
    expect(p.stopPct).toBeCloseTo(-5, 9);
    const rects = shapesOf(d, proj, bounds).filter((x): x is Extract<Shape, { k: 'rect' }> => x.k === 'rect');
    expect(rects).toHaveLength(2);
    expect(rects[0]).toMatchObject({ x: 105, w: 200, y: 400 - 2 * 115, h: 30 }); // reward above entry
    expect(rects[1]).toMatchObject({ y: 200, h: 10 }); // risk below entry
    const label = shapesOf(d, proj, bounds).map((x) => (x.k === 'text' ? x.text : '')).join('|');
    expect(label).toContain('Long  R:R 3.0');
    const short = make('short', [[10, 100], [30, 105], [30, 90]]);
    expect(positionOf(short).long).toBe(false);
    expect(shapesOf(short, proj, bounds).map((x) => (x.k === 'text' ? x.text : '')).join('|')).toContain('Short  R:R 2.0');
    expect(positionOf(make('long', [[1, 100], [2, 100], [2, 110]])).ratio).toBeNull(); // no risk
  });
});

describe('handles and hit-testing', () => {
  const d = make('trend', [[10, 100], [20, 120]]); // (105,200) to (205,160)

  it('handles sit on the anchors', () => {
    expect(handlesOf(d, proj)).toEqual([[105, 200], [205, 160]]);
  });

  it('a handle wins over the body, the body is hit on the line and a miss is null', () => {
    expect(hit(d, proj, bounds, 107, 198)).toEqual({ kind: 'handle', index: 0 });
    expect(hit(d, proj, bounds, 205, 160)).toEqual({ kind: 'handle', index: 1 });
    expect(hit(d, proj, bounds, 155, 180)).toEqual({ kind: 'body' }); // the middle of the line
    expect(hit(d, proj, bounds, 155, 200)).toBeNull();
  });

  it('the tolerance is 6 pixels from a line and not beyond', () => {
    expect(hit(d, proj, bounds, 155, 180 + 5)).toEqual({ kind: 'body' });
    expect(hit(d, proj, bounds, 155, 180 + 8)).toBeNull();
  });

  it('a filled shape is hit inside, an outline-only parallel is not', () => {
    const rect = make('rect', [[10, 100], [30, 140]]);
    expect(hit(rect, proj, bounds, 205, 160)).toEqual({ kind: 'body' }); // inside
    expect(hit(rect, proj, bounds, 450, 160)).toBeNull();
    const ell = make('ellipse', [[10, 100], [30, 140]]);
    expect(hit(ell, proj, bounds, 205, 160)).toEqual({ kind: 'body' });
    const tri = make('triangle', [[10, 100], [20, 140], [30, 100]]);
    expect(hit(tri, proj, bounds, 205, 190)).toEqual({ kind: 'body' });
    expect(hit(tri, proj, bounds, 110, 130)).toBeNull();
  });

  it('text is hit on its box, and a hidden drawing is never hit', () => {
    const t = make('text', [[10, 100]], {}, 'support');
    expect(hit(t, proj, bounds, 120, 197)).toEqual({ kind: 'body' });
    expect(hit(t, proj, bounds, 300, 197)).toBeNull();
    expect(hit(update([d], d.id, { hidden: true })[0], proj, bounds, 155, 180)).toBeNull();
  });

  it('the topmost drawing under the pointer wins', () => {
    const under = make('trend', [[10, 100], [20, 120]]);
    const over = make('trend', [[10, 100], [20, 120]]);
    expect(pick([under, over], proj, bounds, 155, 180)?.id).toBe(over.id);
    expect(pick([over, under], proj, bounds, 155, 180)?.id).toBe(under.id);
    expect(pick([under], proj, bounds, 500, 20)).toBeNull();
  });

  it('every tool can be hit on its own anchor', () => {
    const tools: [ToolId, [number, number][]][] = [
      ['hline', [[10, 100]]], ['vline', [[10, 100]]], ['cross', [[10, 100]]], ['channel', [[10, 100], [30, 110], [20, 120]]], ['fibext', [[10, 100], [20, 120], [30, 110]]],
      ['long', [[10, 100], [30, 95], [30, 115]]], ['label', [[10, 100]]], ['pricerange', [[10, 100], [20, 130]]],
    ];
    for (const [tool, pts] of tools) {
      const dd = make(tool, pts);
      const [hx, hy] = handlesOf(dd, proj)[0];
      expect(hit(dd, proj, bounds, hx, hy), tool).toEqual({ kind: 'handle', index: 0 });
    }
  });
});

describe('helpers', () => {
  it('rgba reads #rrggbb', () => {
    expect(rgba('#ff9800', 0.5)).toBe('rgba(255, 152, 0, 0.5)');
  });
  it('spanText says days for a day or more, hours and minutes below', () => {
    expect(spanText(86_400 * 3)).toBe('3d');
    expect(spanText(3600 * 2 + 1800)).toBe('2h 30m');
    expect(spanText(900)).toBe('15m');
  });
});
