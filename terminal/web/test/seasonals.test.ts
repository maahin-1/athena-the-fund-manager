import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { seasonals } from '../src/details/seasonals';

const day = (y: number, m: number, d: number) => Date.UTC(y, m, d) / 1000;
const bar = (t: number, c: number): Bar => ({ t, o: c, h: c, l: c, c, v: 1 });

/** Dec 2022 as the first close, then two bars a month (the 10th and the last day) from 2023 to Oct 2025. `rets(year, month)` is the month's return in percent. */
function series(rets: (y: number, m: number) => number): Bar[] {
  const out: Bar[] = [bar(day(2022, 11, 30), 100)];
  let c = 100;
  for (let y = 2023; y <= 2025; y++) {
    for (let m = 0; m < (y === 2025 ? 10 : 12); m++) {
      const start = c;
      const r = rets(y, m) / 100;
      out.push(bar(day(y, m, 10), start * (1 + r / 2)));
      c = start * (1 + r);
      out.push(bar(new Date(Date.UTC(y, m + 1, 0)).getTime() / 1000, c));
    }
  }
  return out;
}

describe('seasonals', () => {
  const bars = series((y, m) => (y === 2024 && m === 0 ? -2 : 1));
  const s = seasonals(bars)!;

  it('averages each month over the complete years and counts the hits', () => {
    expect(s.months).toHaveLength(12);
    expect(s.months[0].avg).toBeCloseTo((1 + -2) / 2, 9);
    expect(s.months[0].up).toBe(0.5);
    expect(s.months[0].n).toBe(2);
    expect(s.months[5].avg).toBeCloseTo(1, 9);
    expect(s.months[5].up).toBe(1);
  });

  it('starts each year at the previous year-end close, and puts the partial current year last', () => {
    expect(s.years.map((y) => y.year)).toEqual([2023, 2024, 2025]);
    const first = s.years[0].points[0];
    expect(first[0]).toBeCloseTo(9 / 365, 6); // 10 January
    expect(first[1]).toBeCloseTo(0.5, 6); // half of January's 1%
    const lastPoint = s.years[2].points[s.years[2].points.length - 1];
    expect(lastPoint[0]).toBeLessThan(0.85); // only to October
  });

  it('draws an average path across the whole year from the complete years', () => {
    expect(s.average[0][0]).toBe(0);
    expect(s.average[s.average.length - 1][0]).toBe(1);
    const y23 = s.years[0].points[s.years[0].points.length - 1][1];
    const y24 = s.years[1].points[s.years[1].points.length - 1][1];
    expect(s.average[s.average.length - 1][1]).toBeCloseTo((y23 + y24) / 2, 6);
  });

  it('keeps only the last N years of paths but still uses every complete year for the statistics', () => {
    const one = seasonals(bars, 1)!;
    expect(one.years.map((y) => y.year)).toEqual([2025]);
    expect(one.months[0].n).toBe(2);
  });

  it('is null with fewer than two complete years', () => {
    expect(seasonals(bars.filter((b) => new Date(b.t * 1000).getUTCFullYear() >= 2024))).toBeNull();
    expect(seasonals([])).toBeNull();
  });
});
