import { describe, expect, it } from 'vitest';
import { barTime, type Bar } from '../src/bars';
import { lineBreak, rangeBars, renko, sequence } from '../src/chartlab/derived';
import { ChartModel, autoSize, stepSeconds } from '../src/chartlab/model';
import { build, resampleDaily, resampleIntraday, SOURCE } from '../src/chartlab/resample';

const IST = 19_800;
/** unix seconds of a wall-clock time in IST on 2026-10-14 (a Wednesday). */
const at = (h: number, m: number, day = 14) => Date.UTC(2026, 9, day, h, m) / 1000 - IST;
const bar = (t: number, o: number, h: number, l: number, c: number, v = 100): Bar => ({ t, o, h, l, c, v });
const flat = (c: number, t = 0): Bar => bar(t, c, c, c, c, 0);

describe('intraday resampling', () => {
  it('combines minutes into bars from the 09:15 open: first open, highest high, lowest low, last close, summed volume', () => {
    const ones = [bar(at(9, 15), 10, 12, 9, 11), bar(at(9, 16), 11, 15, 10, 14), bar(at(9, 17), 14, 14, 8, 9), bar(at(9, 18), 9, 10, 9, 10), bar(at(9, 19), 10, 11, 10, 11)];
    const out = resampleIntraday(ones, 2);
    expect(out.map((b) => b.t)).toEqual([at(9, 15), at(9, 17), at(9, 19)]);
    expect(out[0]).toEqual({ t: at(9, 15), o: 10, h: 15, l: 9, c: 14, v: 200 });
    expect(out[1]).toMatchObject({ o: 14, h: 14, l: 8, c: 10, v: 200 });
    expect(out[2]).toMatchObject({ o: 10, c: 11, v: 100 }); // the last bar may hold fewer
  });

  it('half-hour and hour bars start at 09:15, not on the clock half hour', () => {
    const fives = [at(9, 15), at(9, 20), at(9, 40), at(9, 45), at(10, 10), at(10, 15)].map((t, i) => bar(t, 100 + i, 101 + i, 99 + i, 100 + i));
    expect(resampleIntraday(fives, 30).map((b) => b.t)).toEqual([at(9, 15), at(9, 45), at(10, 15)]);
    expect(resampleIntraday(fives, 60).map((b) => b.t)).toEqual([at(9, 15), at(10, 15)]);
  });

  it('never joins the end of one day to the start of the next', () => {
    const out = resampleIntraday([bar(at(15, 29), 1, 1, 1, 1), bar(at(9, 15, 15), 2, 2, 2, 2)], 10);
    expect(out).toHaveLength(2);
  });

  it('handles nothing', () => {
    expect(resampleIntraday([], 5)).toEqual([]);
    expect(resampleDaily([], 'W')).toEqual([]);
  });
});

describe('daily resampling', () => {
  const day = (d: number) => Date.UTC(2026, 9, d) / 1000; // midnight UTC of the date, as daily bars are stamped
  const week = [12, 13, 14, 15, 16].map((d, i) => bar(day(d), 100 + i, 105 + i, 95 + i, 101 + i, 10)); // Monday 12 Oct to Friday 16 Oct

  it('a week is Monday to Friday stamped with its Monday', () => {
    const next = bar(day(19), 200, 210, 190, 205, 10);
    const out = resampleDaily([...week, next], 'W');
    expect(out).toHaveLength(2);
    expect(out[0]).toEqual({ t: day(12), o: 100, h: 109, l: 95, c: 105, v: 50 });
    expect(out[1].t).toBe(day(19));
  });

  it('a month is stamped with its first day', () => {
    const out = resampleDaily([bar(Date.UTC(2026, 8, 29) / 1000, 1, 2, 1, 2), bar(Date.UTC(2026, 8, 30) / 1000, 2, 3, 2, 3), bar(Date.UTC(2026, 9, 1) / 1000, 3, 4, 3, 4)], 'M');
    expect(out.map((b) => b.t)).toEqual([Date.UTC(2026, 8, 1) / 1000, Date.UTC(2026, 9, 1) / 1000]);
    expect(out[0]).toMatchObject({ o: 1, h: 3, c: 3 });
  });

  it('build picks the right combination for each interval and passes native ones through', () => {
    expect(build('1W', week)).toHaveLength(1);
    const ones = Array.from({ length: 10 }, (_, i) => bar(at(9, 15 + i), 1, 1, 1, 1));
    expect(build('1m', ones)).toBe(ones);
    expect(build('2m', ones)).toHaveLength(5);
    expect(build('3m', ones)).toHaveLength(4);
    expect(SOURCE['10m']).toEqual({ fetch: '5m', factor: 2 });
    expect(SOURCE['1h']).toEqual({ fetch: '1h', factor: 1 });
    expect(SOURCE['1W'].fetch).toBe('1D');
  });
});

describe('live ticks on the new intervals', () => {
  it('bar stamps follow the 09:15 anchor, the week starts on Monday and the month on the first', () => {
    expect(barTime(at(9, 44), '30m')).toBe(at(9, 15));
    expect(barTime(at(9, 45), '30m')).toBe(at(9, 45));
    expect(barTime(at(10, 14), '1h')).toBe(at(9, 15));
    expect(barTime(at(10, 15), '1h')).toBe(at(10, 15));
    expect(barTime(at(9, 16), '2m')).toBe(at(9, 15));
    expect(barTime(at(9, 17), '3m')).toBe(at(9, 15));
    expect(barTime(at(9, 25), '10m')).toBe(at(9, 25));
    expect(barTime(at(11, 0), '1W')).toBe(Date.UTC(2026, 9, 12) / 1000); // the Wednesday's Monday
    expect(barTime(at(11, 0, 18), '1W')).toBe(Date.UTC(2026, 9, 12) / 1000); // the Sunday, still that week
    expect(barTime(at(11, 0), '1M')).toBe(Date.UTC(2026, 9, 1) / 1000);
  });

  it('a tick joins the weekly bar it belongs to and a half-hour bar updates in place', () => {
    const m = new ChartModel();
    const monday = Date.UTC(2026, 9, 12) / 1000;
    m.setBars([bar(monday - 7 * 86_400, 90, 95, 88, 92), bar(monday, 92, 100, 91, 99)], '1W');
    expect(m.tick(101, at(11, 0))).toBe('updated');
    expect(m.bars[1]).toMatchObject({ h: 101, c: 101 });
    const half = new ChartModel();
    half.setBars([bar(at(9, 15), 100, 101, 99, 100.5)], '30m');
    expect(half.tick(102, at(9, 40))).toBe('updated');
    expect(half.tick(103, at(9, 46))).toBe('appended');
  });
});

describe('price-move chart types', () => {
  it('Renko: a brick per full move, and a reversal needs two', () => {
    const out = renko([100, 112, 125, 118, 105, 100].map((c) => flat(c)), 10);
    expect(out.map((b) => [b.o, b.c])).toEqual([[100, 110], [110, 120], [110, 100]]);
    expect(renko([flat(100)], 10)).toEqual([]);
    expect(renko([flat(100), flat(101)], 0)).toEqual([]);
  });

  it('Renko: a big move makes several bricks, each the same size', () => {
    const out = renko([flat(100), flat(145)], 10);
    expect(out.map((b) => b.c)).toEqual([110, 120, 130, 140]);
    expect(out.every((b) => b.h - b.l === 10)).toBe(true);
  });

  it('three-line break: a line only when the close passes the last three lines', () => {
    const out = lineBreak([10, 11, 12, 13, 12.5, 9].map((c) => flat(c)));
    expect(out.map((b) => [b.o, b.c])).toEqual([[10, 11], [11, 12], [12, 13], [13, 9]]);
  });

  it('range bars: closed bars span exactly the range, and the next opens where the last closed', () => {
    const out = rangeBars([bar(1, 100, 112, 99, 110, 40)], 5);
    expect(out.map((b) => [b.o, b.l, b.h, b.c])).toEqual([[100, 99, 104, 104], [104, 104, 109, 109], [109, 109, 112, 110]]);
    expect(out.slice(0, -1).every((b) => b.h - b.l <= 5)).toBe(true);
    expect(out.reduce((sum, b) => sum + b.v, 0)).toBe(40); // volume is carried, not lost
    expect(rangeBars([], 5)).toEqual([]);
  });

  it('a falling move makes down bars of the same range', () => {
    const out = rangeBars([bar(1, 100, 101, 88, 90)], 5); // bearish: path 100, 101, 88, 90
    const closed = out.slice(0, -1);
    expect(closed.length).toBeGreaterThanOrEqual(2);
    expect(closed.every((b) => Math.abs(b.h - b.l - 5) < 1e-9 || b.h - b.l < 5)).toBe(true);
    expect(out[out.length - 1].c).toBe(90);
  });

  it('sequence stamps bars one step apart', () => {
    expect(sequence([flat(1), flat(2), flat(3)], 1000, 60).map((b) => b.t)).toEqual([1000, 1060, 1120]);
  });
});

describe('the model with price-move types', () => {
  const closes = [100, 103, 108, 104, 99, 95, 101, 109, 117, 112];
  const bars = closes.map((c, i) => bar(1_700_000_000 + i * 86_400, c, c + 2, c - 2, c, 1000));

  it('derived types draw bricks, hide indicators from the data window and leave the real bars alone', () => {
    const m = new ChartModel();
    m.setBars(bars, '1D');
    const uid = m.addIndicator('sma', { length: 3 })!;
    m.setType('renko');
    expect(m.derived).toBe(true);
    const shown = m.displayBars();
    expect(shown.length).toBeGreaterThan(0);
    expect(shown[1].t - shown[0].t).toBe(stepSeconds('1D'));
    expect(m.window(0)!.values[uid]).toBeUndefined();
    expect(m.bars).toHaveLength(10);
    m.setType('candles');
    expect(m.derived).toBe(false);
    expect(m.window(5)!.values[uid]).toBeDefined();
  });

  it('the automatic brick size is a rounded average true range, or a share of the price when there are too few bars', () => {
    expect(autoSize(bars.slice(0, 3))).toBeCloseTo(1.08, 1); // 1 percent of the last close, to two significant figures
    const long = Array.from({ length: 30 }, (_, i) => bar(i, 100, 104, 96, 100));
    expect(autoSize(long)).toBe(8);
    expect(autoSize(long, 0.5)).toBe(4);
  });

  it('Kagi and point and figure are price-move types, like Renko; high-low and volume candles stay on the calendar with every feature', () => {
    const m = new ChartModel();
    m.setBars(bars, '1D');
    const uid = m.addIndicator('sma', { length: 3 })!;
    for (const t of ['kagi', 'pnf'] as const) {
      m.setType(t);
      expect(m.derived).toBe(true);
      const shown = m.displayBars();
      expect(shown.length).toBeGreaterThan(0);
      expect(shown[0].t).toBe(bars[0].t);
      if (shown.length > 1) expect(shown[1].t - shown[0].t).toBe(stepSeconds('1D'));
      expect(m.window(0)!.values[uid]).toBeUndefined();
    }
    m.setType('kagi');
    expect(m.displayBars().every((b) => 'thick' in b)).toBe(true);
    m.setType('pnf');
    expect(m.displayBars().every((b) => 'up' in b && 'box' in b)).toBe(true);
    for (const t of ['highlow', 'volcandles'] as const) {
      m.setType(t);
      expect(m.derived).toBe(false);
      expect(m.displayBars()).toBe(m.bars); // the real bars, one for one
      expect(m.window(5)!.values[uid]).toBeDefined();
    }
  });

  it('every type is a state the store accepts', async () => {
    const { deserialize, serialize } = await import('../src/chartlab/store');
    const m = new ChartModel();
    for (const t of ['baseline', 'columns', 'step', 'renko', 'linebreak', 'range', 'highlow', 'volcandles', 'kagi', 'pnf'] as const) {
      m.setType(t);
      expect(deserialize(serialize(m.state())).type).toBe(t);
    }
  });
});
