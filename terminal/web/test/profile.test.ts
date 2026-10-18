import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { ChartModel } from '../src/chartlab/model';
import { deserialize, serialize } from '../src/chartlab/store';
import { clampRows, rowMid, volumeProfile } from '../src/chartlab/profile';

const bar = (o: number, h: number, l: number, c: number, v: number, t = 0): Bar => ({ t, o, h, l, c, v });
const sum = (xs: number[]): number => xs.reduce((a, b) => a + b, 0);

describe('volume profile', () => {
  it('has the volume of the bars in its rows, however the bars fall', () => {
    const bars = [bar(10, 14, 9, 13, 100), bar(13, 15, 11, 12, 250), bar(12, 12.5, 8, 9, 75), bar(9, 16, 9, 15, 40), bar(15, 15, 15, 15, 5)];
    for (const rows of [4, 7, 24, 50, 200]) {
      const p = volumeProfile(bars, rows)!;
      expect(p.rows).toHaveLength(rows);
      expect(sum(p.rows.map((r) => r.total))).toBeCloseTo(470, 6);
      expect(p.totalVolume).toBe(470);
      for (const r of p.rows) expect(r.up + r.down).toBeCloseTo(r.total, 9);
    }
  });

  it('puts a bar over two rows in the proportion of the range in each', () => {
    const p = volumeProfile([bar(1, 4, 0, 3, 0.0001), bar(1, 3, 1, 2, 300)], 4)!; // rows: 0-1, 1-2, 2-3, 3-4
    // the 300-volume bar covers 1 to 3: rows 1 and 2, half each
    expect(p.rows.map((r) => Math.round(r.total))).toEqual([0, 150, 150, 0]);
  });

  it('splits each row into rising and falling volume', () => {
    const p = volumeProfile([bar(10, 12, 10, 12, 60), bar(12, 12, 10, 10, 40)], 4)!;
    expect(sum(p.rows.map((r) => r.up))).toBeCloseTo(60, 9);
    expect(sum(p.rows.map((r) => r.down))).toBeCloseTo(40, 9);
  });

  it('counts a bar that closed at its open as rising', () => {
    const p = volumeProfile([bar(10, 11, 9, 10, 50)], 4)!;
    expect(sum(p.rows.map((r) => r.up))).toBeCloseTo(50, 9);
  });

  it('puts a bar with no range, and bars that never moved, in one row', () => {
    const flat = volumeProfile([bar(5, 5, 5, 5, 100), bar(5, 5, 5, 5, 30)], 24)!;
    expect(flat.rows).toHaveLength(1);
    expect(flat.rows[0].total).toBe(130);
    expect(flat.poc).toBe(0);
    const tick = volumeProfile([bar(10, 14, 9, 13, 100), bar(12, 12, 12, 12, 70)], 4)!; // low 9 high 14: rows of 1.25; 12 is in row 2 (11.5 to 12.75)
    expect(tick.rows[2].total).toBeGreaterThanOrEqual(70);
    expect(sum(tick.rows.map((r) => r.total))).toBeCloseTo(170, 9);
  });

  it('puts a price at the very top in the last row', () => {
    const p = volumeProfile([bar(1, 5, 1, 5, 10), bar(5, 5, 5, 5, 90)], 4)!;
    expect(p.rows[3].total).toBeGreaterThanOrEqual(90);
  });

  it('finds the point of control, the row with the most volume', () => {
    const p = volumeProfile([bar(10, 11, 10, 11, 10), bar(14, 15, 14, 14.5, 500), bar(12, 13, 12, 12.5, 40)], 5)!;
    expect(rowMid(p.rows[p.poc])).toBeGreaterThan(14);
    expect(p.rows[p.poc].total).toBe(Math.max(...p.rows.map((r) => r.total)));
  });

  it('takes the lower row when two tie', () => {
    const p = volumeProfile([bar(1, 2, 1, 2, 100), bar(3, 4, 3, 4, 100)], 4)!;
    expect(p.poc).toBe(0);
  });

  it('has a value area that is contiguous, holds the point of control and at least 70% of the volume', () => {
    const bars: Bar[] = [];
    for (let i = 0; i < 60; i++) {
      const mid = 100 + Math.sin(i / 5) * 8 + (i % 7);
      bars.push(bar(mid - 0.5, mid + 1.5, mid - 1.5, mid + 0.5, 100 + ((i * 37) % 90)));
    }
    for (const rows of [4, 12, 24, 48, 96]) {
      const p = volumeProfile(bars, rows)!;
      expect(p.areaLow).toBeLessThanOrEqual(p.poc);
      expect(p.areaHigh).toBeGreaterThanOrEqual(p.poc);
      const held = sum(p.rows.slice(p.areaLow, p.areaHigh + 1).map((r) => r.total));
      expect(held).toBeGreaterThanOrEqual(0.7 * p.totalVolume - 1e-9);
      // and it is not wider than it needs to be: without the last step (at most the two outermost rows on one side) it held less
      const top = sum(p.rows.slice(Math.max(p.areaLow, p.areaHigh - 1), p.areaHigh + 1).map((r) => r.total));
      const bottom = sum(p.rows.slice(p.areaLow, Math.min(p.areaHigh, p.areaLow + 1) + 1).map((r) => r.total));
      if (p.areaHigh > p.areaLow) expect(Math.min(held - top, held - bottom)).toBeLessThan(0.7 * p.totalVolume + 1e-9);
    }
  });

  it('is the whole range when the point of control is at one end and little else has volume', () => {
    const p = volumeProfile([bar(1, 2, 1, 2, 1000), bar(9, 10, 9, 9.5, 1)], 4)!;
    expect(p.areaLow).toBe(0);
    expect(p.rows[p.poc].total).toBeGreaterThan(900);
  });

  it('gives nothing when there is no volume, no bars, or a price that is not a number', () => {
    expect(volumeProfile([], 24)).toBeNull();
    expect(volumeProfile([bar(1, 2, 1, 2, 0), bar(2, 3, 2, 3, 0)], 24)).toBeNull(); // an index
    expect(volumeProfile([bar(1, Number.NaN, 1, 2, 10)], 24)).toBeNull();
  });

  it('keeps the number of rows between 4 and 200', () => {
    expect(clampRows(1)).toBe(4);
    expect(clampRows(1000)).toBe(200);
    expect(clampRows(24.4)).toBe(24);
    expect(clampRows(Number.NaN)).toBe(24);
    expect(volumeProfile([bar(1, 9, 1, 9, 5)], 2)!.rows).toHaveLength(4);
  });
});

describe('the profile as part of a chart state', () => {
  const bars = Array.from({ length: 10 }, (_, i) => ({ t: 100 + i, o: 10 + i, h: 12 + i, l: 9 + i, c: 11 + i, v: 100 }));

  it('is off by default, set with its rows, and kept in the state', () => {
    const model = new ChartModel();
    model.setBars(bars, '1D');
    expect(model.state().profile).toEqual({ on: false, rows: 24 });
    model.setProfile({ on: true, rows: 48 });
    expect(model.state().profile).toEqual({ on: true, rows: 48 });
    model.setProfile({ on: true, rows: 9999 });
    expect(model.state().profile.rows).toBe(200);
    const again = new ChartModel();
    again.restore(deserialize(serialize(model.state())));
    expect(again.profile).toEqual({ on: true, rows: 200 });
  });

  it('opens from a state saved before the profile existed', () => {
    const old = JSON.stringify({ version: 1, type: 'candles', scale: 'auto', adjusted: true, indicators: [], compare: null });
    expect(deserialize(old).profile).toEqual({ on: false, rows: 24 });
  });

  it('refuses a profile that is not on or off with a whole number of rows', () => {
    const base = { version: 1, type: 'candles', scale: 'auto', adjusted: true, indicators: [], compare: null };
    expect(() => deserialize(JSON.stringify({ ...base, profile: { on: 'yes', rows: 24 } }))).toThrow(/profile/);
    expect(() => deserialize(JSON.stringify({ ...base, profile: { on: true, rows: 'many' } }))).toThrow(/profile/);
    expect(deserialize(JSON.stringify({ ...base, profile: { on: true, rows: 5000 } })).profile.rows).toBe(200);
  });
});
