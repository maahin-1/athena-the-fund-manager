import { describe, expect, it } from 'vitest';
import { handlesOf, pick, shapesOf, type Shape } from '../src/chartlab/drawings/geometry';
import { Creator } from '../src/chartlab/drawings/creator';
import { GROUPS, newDrawing, serializeDoc, TOOL_IDS, TOOLS, validateDoc, DrawingError, type Drawing, type Style, type ToolId } from '../src/chartlab/drawings/model';
import { legRatio, pitchforkOrigin } from '../src/chartlab/drawings/extra';
import { Projection } from '../src/chartlab/drawings/projection';

/** Bar i at x = 10 i + 5, time 1000 i; price p at y = 400 - 2 p. The pane is 600 x 400. */
const times = Array.from({ length: 60 }, (_, i) => i * 1000);
const proj = new Projection(times, 1000, { x: (i) => i * 10 + 5, index: (x) => (x - 5) / 10, y: (p) => 400 - 2 * p, price: (y) => (400 - y) / 2 });
const bounds = { w: 600, h: 400 };
const make = (tool: ToolId, pts: [number, number][], style: Partial<Style> = {}, text?: string): Drawing => newDrawing(tool, pts.map(([i, p]) => ({ t: i * 1000, p })), style, text);
type Line = Extract<Shape, { k: 'line' }>;
type Poly = Extract<Shape, { k: 'poly' }>;
type Text = Extract<Shape, { k: 'text' }>;
const lines = (s: Shape[]): Line[] => s.filter((x): x is Line => x.k === 'line');
const polys = (s: Shape[]): Poly[] => s.filter((x): x is Poly => x.k === 'poly');
const texts = (s: Shape[]): string[] => s.filter((x): x is Text => x.k === 'text').map((x) => x.text);
const slope = (l: Line): number => (l.y2 - l.y1) / (l.x2 - l.x1);

/** A sensible set of anchors for every tool added after the first 22 (bar index, price). */
const SAMPLE: Partial<Record<ToolId, [number, number][]>> = {
  angle: [[10, 100], [20, 150]], arrow: [[10, 100], [20, 130]], disjoint: [[10, 100], [20, 110], [12, 130], [25, 150]],
  pitchfork: [[10, 100], [20, 130], [30, 110]], schiff: [[10, 100], [20, 130], [30, 110]], mschiff: [[10, 100], [20, 130], [30, 110]],
  fibchannel: [[10, 100], [20, 120], [15, 140]], fibtime: [[10, 100], [15, 120]], fibfan: [[10, 100], [20, 140]], fibcircles: [[10, 100], [20, 140]], fibarcs: [[10, 100], [20, 140]],
  gannfan: [[10, 100], [20, 120]], gannbox: [[10, 100], [20, 140]],
  circle: [[20, 100], [25, 100]], arrowup: [[10, 100]], arrowdown: [[10, 100]], forecast: [[10, 100], [30, 140]],
  abcd: [[5, 100], [15, 120], [22, 108], [32, 130]], xabcd: [[5, 100], [10, 140], [15, 115], [20, 130], [25, 110]], cypher: [[5, 100], [10, 140], [15, 115], [20, 150], [25, 125]],
  headshoulders: [[5, 100], [10, 130], [14, 110], [20, 150], [26, 110], [30, 130], [36, 100]], threedrives: [[5, 100], [10, 130], [13, 118], [18, 148], [21, 135], [26, 165], [30, 150]],
  trianglepat: [[5, 100], [12, 150], [20, 115], [28, 140]], elliottimpulse: [[5, 100], [10, 130], [13, 115], [22, 170], [27, 145], [34, 180]], elliottcorrective: [[5, 180], [12, 140], [17, 160], [25, 120]],
  cyclic: [[10, 100], [20, 100]], timecycles: [[10, 100], [20, 120]], sine: [[10, 100], [20, 140]],
};

describe('the later tools exist and are grouped', () => {
  it('there are 50 tools in 11 groups, every group has a tool, and the tools added after the first 22 each have anchors for a sample drawing', () => {
    expect(TOOL_IDS).toHaveLength(50);
    expect(GROUPS).toHaveLength(11);
    for (const g of GROUPS) expect(TOOL_IDS.some((t) => TOOLS[t].group === g.id)).toBe(true);
    expect(Object.keys(SAMPLE)).toHaveLength(28);
    for (const [id, pts] of Object.entries(SAMPLE) as [ToolId, [number, number][]][]) expect(pts).toHaveLength(TOOLS[id].anchors);
  });

  it('every new tool draws something and puts a handle on each anchor', () => {
    for (const [id, pts] of Object.entries(SAMPLE) as [ToolId, [number, number][]][]) {
      const d = make(id, pts);
      expect(shapesOf(d, proj, bounds).length, id).toBeGreaterThan(0);
      expect(handlesOf(d, proj), id).toHaveLength(pts.length);
    }
  });

  it('a hidden drawing gives no shape', () => {
    const d = { ...make('pitchfork', SAMPLE.pitchfork!), hidden: true };
    expect(shapesOf(d, proj, bounds)).toEqual([]);
  });

  it('every new tool survives saving and loading, and a wrong number of points is refused', () => {
    const all = (Object.entries(SAMPLE) as [ToolId, [number, number][]][]).map(([id, pts]) => make(id, pts));
    const back = validateDoc(JSON.parse(serializeDoc('SBIN', all)), 'SBIN');
    expect(back.drawings.map((d) => d.tool)).toEqual(all.map((d) => d.tool));
    expect(() => newDrawing('headshoulders', [{ t: 1, p: 1 }])).toThrow(DrawingError);
  });
});

describe('lines and channels', () => {
  it('a trend angle writes the angle the line makes on the screen: 45 degrees for equal rise and run in pixels', () => {
    const s = shapesOf(make('angle', SAMPLE.angle!), proj, bounds);
    expect(texts(s)).toContain('45.0°');
  });

  it('an arrow is a line with a filled head at the second anchor', () => {
    const s = shapesOf(make('arrow', SAMPLE.arrow!), proj, bounds);
    expect(lines(s)).toHaveLength(1);
    const head = polys(s)[0];
    expect(head.fill).toBeDefined();
    expect(head.pts[0]).toEqual([205, 140]); // the tip: bar 20 at price 130
  });

  it('a disjoint channel is two lines that are not parallel, with the space between filled', () => {
    const s = shapesOf(make('disjoint', SAMPLE.disjoint!), proj, bounds);
    const [l1, l2] = lines(s);
    expect(slope(l1)).not.toBeCloseTo(slope(l2), 3);
    expect(polys(s)[0].fill).toBeDefined();
  });
});

describe('pitchforks', () => {
  it('the start of the median line: the pivot, halfway in price (Schiff) or halfway in time and price (modified Schiff)', () => {
    const p0: [number, number] = [105, 200];
    const p1: [number, number] = [205, 140];
    expect(pitchforkOrigin('pitchfork', p0, p1)).toEqual([105, 200]);
    expect(pitchforkOrigin('schiff', p0, p1)).toEqual([105, 170]);
    expect(pitchforkOrigin('mschiff', p0, p1)).toEqual([155, 170]);
  });

  it('the median runs from the pivot through the middle of the other two, and the two outer lines are parallel to it', () => {
    const s = shapesOf(make('pitchfork', SAMPLE.pitchfork!), proj, bounds);
    // P0 (105,200); P1 (205,140); P2 (305,180); middle of P1 and P2 (255,160)
    const solid = lines(s).filter((l) => l.dash === 'solid' && l.width > 1);
    expect(solid).toHaveLength(3);
    const median = solid.find((l) => l.x1 === 105)!;
    expect(median.y1).toBe(200);
    expect(median.x2).toBeCloseTo(600, 6); // continued to the edge of the pane
    expect(slope(median)).toBeCloseTo((160 - 200) / (255 - 105), 6);
    expect(median.y1 + slope(median) * (255 - 105)).toBeCloseTo(160, 6); // passes through the middle point
    for (const tine of solid.filter((l) => l !== median)) expect(slope(tine)).toBeCloseTo(slope(median), 6);
  });

  it('a Schiff fork starts its median halfway between the first two pivots in price; a modified one in time as well', () => {
    const first = (tool: ToolId) => lines(shapesOf(make(tool, SAMPLE.pitchfork!), proj, bounds)).find((l) => l.width > 1 && l.dash === 'solid' && l.x1 !== 205 && l.x1 !== 305)!;
    expect([first('schiff').x1, first('schiff').y1]).toEqual([105, 170]);
    expect([first('mschiff').x1, first('mschiff').y1]).toEqual([155, 170]);
  });

  it('can be picked by its median', () => {
    const d = make('pitchfork', SAMPLE.pitchfork!);
    const found = pick([d], proj, bounds, 255, 160);
    expect(found?.id).toBe(d.id);
  });
});

describe('Fibonacci and Gann', () => {
  it('time zones stand at 0, 1, 2, 3, 5, 8, 13 ... units of the distance between the two anchors, counted in bars', () => {
    const xs = lines(shapesOf(make('fibtime', SAMPLE.fibtime!), proj, bounds)).map((l) => l.x1);
    // anchors at bars 10 and 15 (unit 5 bars = 50 px) from x 105
    expect(xs.slice(0, 6)).toEqual([105, 155, 205, 255, 355, 505]);
    expect(xs.every((x) => x <= 620)).toBe(true); // those off the right edge are left out
  });

  it('a Fib channel repeats the base line by the ratios of the third anchor distance', () => {
    const s = shapesOf(make('fibchannel', SAMPLE.fibchannel!), proj, bounds);
    expect(lines(s)).toHaveLength(9); // the tenth, ratio 4.236, is above the pane
    const base = lines(s)[0];
    const one = lines(s)[6]; // ratio 1 is the line through the third anchor (bar 15, price 140: y 120)
    expect(slope(one)).toBeCloseTo(slope(base), 6);
    expect(one.y1 + slope(one) * (155 - one.x1)).toBeCloseTo(120, 6);
  });

  it('a speed fan has lines from the first anchor to both edges of the box at each ratio', () => {
    const s = shapesOf(make('fibfan', SAMPLE.fibfan!), proj, bounds);
    expect(texts(s)).toContain('0.618');
    expect(lines(s).length).toBeGreaterThanOrEqual(FAN_LINES);
  });

  it('the circle at ratio 1 passes through both anchors', () => {
    const e = shapesOf(make('fibcircles', SAMPLE.fibcircles!), proj, bounds).filter((x): x is Extract<Shape, { k: 'ellipse' }> => x.k === 'ellipse');
    const one = e.find((c) => Math.abs(c.rx - Math.hypot(100, 80) / 2) < 1e-6)!;
    expect(one.cx).toBe(155);
    expect(one.cy).toBe(160);
    expect(one.rx).toBe(one.ry);
  });

  it('arcs are centred on the second anchor and the full-length arc reaches the first', () => {
    const s = shapesOf(make('fibarcs', SAMPLE.fibarcs!), proj, bounds);
    const arcs = polys(s);
    expect(arcs).toHaveLength(4);
    const full = arcs[3];
    const centre: [number, number] = [205, 120];
    for (const [x, y] of full.pts) expect(Math.hypot(x - centre[0], y - centre[1])).toBeCloseTo(Math.hypot(100, 80), 6);
  });

  it('a Gann fan has the nine angles, and the 1x1 line runs through the second anchor', () => {
    const s = shapesOf(make('gannfan', SAMPLE.gannfan!), proj, bounds);
    const fan = lines(s);
    expect(fan).toHaveLength(9);
    const one = fan[4];
    expect(one.y1 + slope(one) * (205 - one.x1)).toBeCloseTo(160, 6); // bar 20, price 120
    expect(texts(s)).toEqual(['8x1', '4x1', '3x1', '2x1', '1x1', '1x2', '1x3', '1x4', '1x8']);
    expect(slope(fan[3])).toBeCloseTo(slope(one) * 2, 6);
  });

  it('a Gann box has its levels and both diagonals', () => {
    const s = shapesOf(make('gannbox', SAMPLE.gannbox!), proj, bounds);
    expect(lines(s)).toHaveLength(12); // five horizontal, five vertical, two diagonals
    expect(s.filter((x) => x.k === 'rect')).toHaveLength(1);
  });
});

const FAN_LINES = 6 + 5; // six to the far edge and five to the near edge

describe('patterns', () => {
  it('leg ratios come from the anchor prices', () => {
    const prices = [100, 120, 110, 130];
    expect(legRatio(prices, [0, 1], [1, 2])).toBeCloseTo(0.5, 9); // B C is half of A B
    expect(legRatio(prices, [1, 2], [2, 3])).toBeCloseTo(2, 9);
    expect(legRatio([100, 100, 110], [0, 1], [1, 2])).toBeNull(); // a flat first leg has no ratio
  });

  it('ABCD names its points and writes the two ratios', () => {
    const s = shapesOf(make('abcd', SAMPLE.abcd!), proj, bounds);
    const words = texts(s);
    for (const name of ['A', 'B', 'C', 'D']) expect(words).toContain(name);
    expect(words).toContain('0.6'); // B C against A B: 12 / 20
    expect(words).toContain('1.833'); // C D against B C: 22 / 12
  });

  it('XABCD fills two triangles and cypher names its ratios', () => {
    expect(polys(shapesOf(make('xabcd', SAMPLE.xabcd!), proj, bounds)).filter((p) => p.fill)).toHaveLength(2);
    const words = texts(shapesOf(make('cypher', SAMPLE.cypher!), proj, bounds));
    for (const name of ['X', 'A', 'B', 'C', 'D']) expect(words).toContain(name);
  });

  it('head and shoulders labels the three peaks and draws the neckline through the two lows', () => {
    const s = shapesOf(make('headshoulders', SAMPLE.headshoulders!), proj, bounds);
    expect(texts(s)).toEqual(expect.arrayContaining(['Left shoulder', 'Head', 'Right shoulder']));
    const neck = lines(s)[0];
    expect([neck.x1, neck.y1]).toEqual([145, 180]); // bar 14 at 110
  });

  it('Elliott waves label their points in order', () => {
    expect(texts(shapesOf(make('elliottimpulse', SAMPLE.elliottimpulse!), proj, bounds)).filter((t) => t.startsWith('('))).toEqual(['(0)', '(1)', '(2)', '(3)', '(4)', '(5)']);
    expect(texts(shapesOf(make('elliottcorrective', SAMPLE.elliottcorrective!), proj, bounds)).filter((t) => t.startsWith('('))).toEqual(['(0)', '(A)', '(B)', '(C)']);
  });

  it('three drives names its drives', () => {
    expect(texts(shapesOf(make('threedrives', SAMPLE.threedrives!), proj, bounds))).toEqual(expect.arrayContaining(['Drive 1', 'Drive 2', 'Drive 3']));
  });

  it('a triangle pattern continues its two lines to the right', () => {
    const l = lines(shapesOf(make('trianglepat', SAMPLE.trianglepat!), proj, bounds));
    expect(l).toHaveLength(2);
    for (const x of l) expect(x.x2).toBeCloseTo(600, 6);
  });

  it('placing a seven-point pattern takes seven clicks', () => {
    const c = new Creator();
    c.begin('headshoulders');
    let done: Drawing | null = null;
    for (let i = 0; i < 7; i++) {
      expect(done).toBeNull();
      done = c.click({ t: i * 1000, p: 100 + i });
    }
    expect(done?.points).toHaveLength(7);
  });
});

describe('cycles, forecast and markers', () => {
  it('cyclic lines repeat at the distance between the anchors, to the left as well as the right', () => {
    const xs = lines(shapesOf(make('cyclic', SAMPLE.cyclic!), proj, bounds)).map((l) => l.x1).sort((a, b) => a - b);
    expect(xs).toEqual([5, 105, 205, 305, 405, 505]);
  });

  it('time cycles draw an arch between each pair of cycle lines', () => {
    const arches = polys(shapesOf(make('timecycles', SAMPLE.timecycles!), proj, bounds));
    expect(arches.length).toBeGreaterThanOrEqual(5);
    const mid = arches[0].pts[10];
    expect(mid[1]).toBeLessThan(arches[0].pts[0][1]); // the arch rises above its baseline
  });

  it('the sine line passes through the first anchor and ends the half-wave at the second', () => {
    const [wave] = polys(shapesOf(make('sine', SAMPLE.sine!), proj, bounds));
    const start = wave.pts.find((p) => p[0] === 105)!;
    expect(start[1]).toBeCloseTo(200, 6);
    const end = wave.pts.find((p) => p[0] === 204)!; // the nearest sample before bar 20 (x 205)
    expect(Math.abs(end[1] - 120)).toBeLessThan(1); // bar 20 at 140 is y 120
  });

  it('a forecast writes the change, the bars and the target the person drew', () => {
    const t = texts(shapesOf(make('forecast', SAMPLE.forecast!), proj, bounds))[0];
    expect(t).toContain('+40.00');
    expect(t).toContain('20 bars');
    expect(t).toContain('to 140');
  });

  it('markers sit with the tip on the anchor and take words', () => {
    const up = polys(shapesOf(make('arrowup', SAMPLE.arrowup!, {}, 'Buy zone'), proj, bounds));
    expect(up[0].pts[0]).toEqual([105, 200]);
    expect(up[0].pts[3][1]).toBeGreaterThan(200); // the body lies below an up arrow
    const down = polys(shapesOf(make('arrowdown', SAMPLE.arrowdown!, {}, 'Sell zone'), proj, bounds));
    expect(down[0].pts[3][1]).toBeLessThan(200);
    expect(texts(shapesOf(make('arrowdown', SAMPLE.arrowdown!, {}, 'Sell zone'), proj, bounds))).toContain('Sell zone');
  });

  it('a circle has the first anchor as its centre and the second on its edge', () => {
    const [c] = shapesOf(make('circle', SAMPLE.circle!), proj, bounds).filter((x): x is Extract<Shape, { k: 'ellipse' }> => x.k === 'ellipse');
    expect([c.cx, c.cy, c.rx, c.ry]).toEqual([205, 200, 50, 50]);
  });
});
