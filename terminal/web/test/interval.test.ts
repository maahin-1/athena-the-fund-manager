import { describe, expect, it } from 'vitest';
import { applyTick, barTime, type Bar } from '../src/bars';
import { build, sourceOf } from '../src/chartlab/resample';
import { stepSeconds } from '../src/chartlab/model';
import { defaultRangeOf, groupBars, parseInterval, planOf, rangesOf, startOf, stepOf, type Parsed } from '../src/interval';
import { isIntraday } from '../src/ranges';

const IST = 19_800;
const utc = (y: number, m: number, d: number, hh = 0, mm = 0): number => Date.UTC(y, m - 1, d, hh, mm) / 1000;
/** A moment given in IST clock time. */
const ist = (y: number, m: number, d: number, hh: number, mm: number): number => utc(y, m, d, hh, mm) - IST;
const ok = (text: string): Parsed => {
  const p = parseInterval(text);
  if (!p.ok) throw new Error(p.error);
  return p;
};
const bar = (t: number, c: number, v = 10): Bar => ({ t, o: c, h: c + 1, l: c - 1, c, v });

describe('parsing what a person types', () => {
  it('reads minutes, hours, days, weeks and months in the spellings people use', () => {
    expect(ok('7m')).toMatchObject({ id: '7m', unit: 'm', minutes: 7, builtin: false });
    expect(ok('7')).toMatchObject({ id: '7m', minutes: 7 });
    expect(ok(' 7 min ')).toMatchObject({ id: '7m' });
    expect(ok('90m')).toMatchObject({ id: '90m', minutes: 90 });
    expect(ok('2h')).toMatchObject({ id: '2h', minutes: 120, builtin: false });
    expect(ok('2 hours')).toMatchObject({ id: '2h' });
    expect(ok('3D')).toMatchObject({ id: '3D', unit: 'D', n: 3 });
    expect(ok('3 days')).toMatchObject({ id: '3D' });
    expect(ok('2W')).toMatchObject({ id: '2W', unit: 'W', n: 2 });
    expect(ok('3M')).toMatchObject({ id: '3M', unit: 'M', n: 3 });
    expect(ok('3 months')).toMatchObject({ id: '3M' });
  });

  it('tells minutes from months by the case of the single letter', () => {
    expect(ok('1m')).toMatchObject({ id: '1m', unit: 'm' });
    expect(ok('1M')).toMatchObject({ id: '1M', unit: 'M' });
    expect(ok('3m').unit).toBe('m');
    expect(ok('3M').unit).toBe('M');
  });

  it('gives a built-in interval its built-in id however it is spelled', () => {
    for (const [text, id] of [['60m', '1h'], ['60', '1h'], ['1h', '1h'], ['1d', '1D'], ['1D', '1D'], ['1w', '1W'], ['5', '5m'], ['30 min', '30m'], ['1 month', '1M'], ['10m', '10m']]) {
      expect(ok(text)).toMatchObject({ id, builtin: true });
    }
    expect(ok('120m').id).toBe('2h'); // one spelling for one interval
    expect(ok('180m').id).toBe('3h');
  });

  it('refuses what is not an interval, and names the limit', () => {
    const bad = (text: string): string => {
      const p = parseInterval(text);
      if (p.ok) throw new Error(`${text} was accepted as ${p.id}`);
      return p.error;
    };
    expect(bad('')).toMatch(/number and a unit/);
    expect(bad('abc')).toMatch(/number and a unit/);
    expect(bad('7x')).toMatch(/"x" is not a unit/);
    expect(bad('0m')).toMatch(/at least 1/);
    expect(bad('-5m')).toMatch(/number and a unit/);
    expect(bad('1.5h')).toMatch(/number and a unit/);
    expect(bad('376m')).toMatch(/375 minutes/);
    expect(bad('7h')).toMatch(/375 minutes/);
    expect(bad('61D')).toMatch(/at most 60 days/);
    expect(bad('27W')).toMatch(/at most 26 weeks/);
    expect(bad('25M')).toMatch(/at most 24 months/);
    expect(parseInterval('375m').ok).toBe(true);
    expect(parseInterval('60D').ok).toBe(true);
  });

  it('knows which intervals are inside a day', () => {
    for (const id of ['7m', '90m', '2h', '1h', '1m']) expect(isIntraday(id)).toBe(true);
    for (const id of ['3D', '2W', '3M', '1D', '1W', '1M']) expect(isIntraday(id)).toBe(false);
  });
});

describe('where the bars come from', () => {
  const plan = (text: string) => planOf(ok(text));
  it('builds from the coarsest interval the server serves that divides it', () => {
    expect(plan('7m')).toEqual({ fetch: '1m', factor: 7, depthDays: 5 });
    expect(plan('25m')).toEqual({ fetch: '5m', factor: 5, depthDays: 22 });
    expect(plan('45m')).toEqual({ fetch: '15m', factor: 3, depthDays: 22 });
    expect(plan('90m')).toEqual({ fetch: '30m', factor: 3, depthDays: 22 });
    expect(plan('2h')).toEqual({ fetch: '1h', factor: 2, depthDays: 260 });
    expect(plan('3D')).toEqual({ fetch: '1D', factor: 1, depthDays: Infinity });
    expect(plan('3M').fetch).toBe('1D');
  });

  it('asks the server for built-in intervals as before, and for a custom one by its plan', () => {
    expect(sourceOf('2m')).toEqual({ fetch: '1m', factor: 2 });
    expect(sourceOf('1W')).toEqual({ fetch: '1D', factor: 1 });
    expect(sourceOf('7m')).toEqual({ fetch: '1m', factor: 7, depthDays: 5 });
    expect(sourceOf('2h').fetch).toBe('1h');
  });

  it('offers the ranges the free source can serve, never an empty one', () => {
    const labels = (text: string) => rangesOf(ok(text)).map((r) => r.label);
    expect(labels('7m')).toEqual(['1D', '5D']);
    expect(labels('25m')).toEqual(['1D', '5D', '1M']);
    expect(labels('2h')).toEqual(['5D', '1M', '6M', '1Y']);
    expect(labels('3D')).toEqual(['6M', '1Y', '5Y', 'All']);
    expect(labels('3M')).toEqual(['5Y', 'All']);
    for (const text of ['7m', '90m', '2h', '3D', '2W', '60D', '24M', '375m']) {
      const ranges = rangesOf(ok(text));
      expect(ranges.length).toBeGreaterThan(0);
      expect(ranges.some((r) => r.label === defaultRangeOf(ok(text)))).toBe(true);
      for (const r of ranges) expect(r.tf).toBe(ok(text).id);
    }
  });

  it('counts the bars of a range from the bars to a day', () => {
    const byLabel = Object.fromEntries(rangesOf(ok('25m')).map((r) => [r.label, r.bars]));
    expect(byLabel['1D']).toBe(15); // 375 / 25
    expect(byLabel['5D']).toBe(75);
    expect(byLabel['1M']).toBe(330);
  });

  it('shows a sensible range first', () => {
    expect(defaultRangeOf(ok('7m'))).toBe('5D'); // a day is 54 bars, too few to read; five days are 268
    expect(defaultRangeOf(ok('5m'))).toBe('1D'); // 75 bars
  });
});

describe('grouping minutes, from the 09:15 open', () => {
  it('starts bars at 09:15, 10:45, 12:15 and 13:45 for 90 minutes, and keeps the short last bar', () => {
    const p = ok('90m');
    const day = [[9, 15], [10, 0], [10, 44], [10, 45], [12, 14], [12, 15], [13, 44], [13, 45], [15, 29]].map(([h, m]) => bar(ist(2026, 10, 14, h, m), 100));
    const out = groupBars(day, p);
    expect(out.map((b) => new Date((b.t + IST) * 1000).toISOString().slice(11, 16))).toEqual(['09:15', '10:45', '12:15', '13:45', '15:15']);
    expect(out.map((b) => b.v)).toEqual([30, 20, 20, 10, 10]); // the last bar is the 15 minutes left of the session
  });

  it('never carries a bar across the night', () => {
    const p = ok('375m');
    const out = groupBars([bar(ist(2026, 10, 14, 15, 29), 1), bar(ist(2026, 10, 15, 9, 15), 2)], p);
    expect(out).toHaveLength(2);
  });

  it('takes open from the first, close from the last, high and low across the group', () => {
    const p = ok('7m');
    const t0 = ist(2026, 10, 14, 9, 15);
    const out = groupBars([{ t: t0, o: 10, h: 12, l: 9, c: 11, v: 1 }, { t: t0 + 60, o: 11, h: 15, l: 10, c: 14, v: 2 }, { t: t0 + 120, o: 14, h: 14, l: 8, c: 9, v: 3 }], p);
    expect(out).toEqual([{ t: t0, o: 10, h: 15, l: 8, c: 9, v: 6 }]);
  });
});

describe('grouping days, weeks and months', () => {
  const day = (y: number, m: number, d: number, c = 100): Bar => bar(utc(y, m, d), c);

  it('puts consecutive weekdays in a bar, and a bar is the same bar whatever history is loaded', () => {
    const p = ok('3D');
    const week = [day(2026, 10, 12), day(2026, 10, 13), day(2026, 10, 14), day(2026, 10, 15), day(2026, 10, 16), day(2026, 10, 19), day(2026, 10, 20)];
    const full = groupBars(week, p);
    const later = groupBars(week.slice(2), p); // history that begins two days later
    expect(full.reduce((s, b) => s + b.v, 0)).toBe(70);
    expect(full.length).toBeGreaterThanOrEqual(3);
    for (const g of full) expect(g.v).toBeLessThanOrEqual(30); // never more than three weekdays
    // every bar of the later history except its first (which may begin part way) is the bar the full history has
    for (const g of later.slice(1)) expect(full.find((f) => f.t === g.t)).toEqual(g);
    expect(later[0].t).toBe(full.find((f) => f.t === later[0].t)?.t);
  });

  it('a holiday makes a group shorter instead of moving later groups', () => {
    const p = ok('2D');
    const days = [day(2026, 10, 12), day(2026, 10, 13), day(2026, 10, 14), day(2026, 10, 15), day(2026, 10, 16)];
    const without = groupBars(days, p);
    const holiday = startOf(days[1].t, p); // the group the 13th is in
    const withHoliday = groupBars(days.filter((_, i) => i !== 1), p); // the 13th is a holiday
    expect(withHoliday.reduce((s, b) => s + b.v, 0)).toBe(40);
    for (const g of without.filter((g) => g.t !== holiday)) expect(withHoliday.find((w) => w.t === g.t)).toEqual(g); // the other groups did not move
    for (const w of withHoliday) expect(without.some((g) => g.t === w.t)).toBe(true); // and none is new
  });

  it('a Saturday session joins Friday', () => {
    const p = ok('2D');
    expect(startOf(utc(2026, 10, 17), p)).toBe(startOf(utc(2026, 10, 16), p));
  });

  it('stamps a bar with the date of its first weekday, as a daily bar is stamped', () => {
    for (const text of ['2D', '3D', '5D']) {
      const t = startOf(utc(2026, 10, 14), ok(text));
      expect(t % 86_400).toBe(0);
    }
  });

  it('groups two weeks from Mondays and three months from calendar months', () => {
    const w = ok('2W');
    const a = startOf(utc(2026, 10, 14), w);
    expect(new Date(a * 1000).getUTCDay()).toBe(1); // a Monday
    expect(startOf(utc(2026, 10, 14), w)).toBe(startOf(utc(2026, 10, 14) + 7 * 86_400 * 0, w));
    const weeks = groupBars([day(2026, 10, 5), day(2026, 10, 12), day(2026, 10, 19), day(2026, 10, 26)], w);
    expect(weeks).toHaveLength(2);
    const months = groupBars([day(2026, 1, 15), day(2026, 2, 15), day(2026, 3, 15), day(2026, 4, 15)], ok('3M'));
    expect(months).toHaveLength(2);
    expect(new Date(months[0].t * 1000).getUTCDate()).toBe(1);
    expect(months[0].v).toBe(30);
  });

  it('a month boundary and a year boundary start new groups', () => {
    const months = groupBars([day(2026, 11, 30), day(2026, 12, 1), day(2026, 12, 31), day(2027, 1, 1)], ok('2M'));
    expect(months.map((b) => b.v)).toEqual([30, 10]); // Nov and Dec 2026 are one bar; Jan 2027 begins another
  });
});

describe('a live tick and the history agree', () => {
  /** A synthetic run of source bars across several days, weeks and months. */
  function source(unit: 'm' | 'D'): Bar[] {
    const out: Bar[] = [];
    const days = [];
    for (let d = utc(2026, 8, 24); d < utc(2026, 11, 5); d += 86_400) {
      const dow = new Date(d * 1000).getUTCDay();
      if (dow !== 0 && dow !== 6) days.push(d);
    }
    for (const d of days) {
      if (unit === 'D') out.push(bar(d, 100 + (d % 7)));
      else for (let m = 0; m < 375; m += 5) out.push(bar(d + 9 * 3600 + 15 * 60 - IST + m * 60, 100 + (m % 11)));
    }
    return out;
  }

  it('every source bar lands in the bar history gives it, for each unit', () => {
    for (const [text, unit] of [['7m', 'm'], ['90m', 'm'], ['2h', 'm'], ['3D', 'D'], ['2W', 'D'], ['3M', 'D'], ['2M', 'D']] as const) {
      const p = ok(text);
      const src = source(unit === 'D' ? 'D' : 'm');
      const built = groupBars(src, p);
      const starts = new Set(built.map((b) => b.t));
      for (const b of src) {
        const t = barTime(unit === 'D' ? b.t + 12 * 3600 : b.t, p.id); // a tick inside the day for daily-based intervals
        expect(starts.has(t), `${text}: ${t}`).toBe(true);
        expect(t).toBe(startOf(b.t, p));
      }
    }
  });

  it('ticks fed one by one build the bars that history builds', () => {
    const p = ok('7m');
    const src = source('m').slice(0, 300);
    const built = groupBars(src, p);
    let last: Bar | undefined;
    const grown: Bar[] = [];
    for (const b of src) {
      const next = applyTick(last, b.c, b.t, p.id, 0);
      if (!last || next.t !== last.t) grown.push(next);
      else grown[grown.length - 1] = next;
      last = next;
    }
    expect(grown.map((b) => b.t)).toEqual(built.map((b) => b.t));
  });

  it('built-in intervals are untouched', () => {
    const t = ist(2026, 10, 14, 10, 7);
    expect(barTime(t, '30m')).toBe(ist(2026, 10, 14, 9, 45));
    expect(barTime(t, '1h')).toBe(ist(2026, 10, 14, 9, 15));
    expect(barTime(t, '1D')).toBe(utc(2026, 10, 14));
    expect(build('2m', [bar(ist(2026, 10, 14, 9, 15), 1), bar(ist(2026, 10, 14, 9, 16), 2)])).toHaveLength(1);
  });
});

describe('the step between stamped bars', () => {
  it('is the length of a bar', () => {
    expect(stepSeconds('7m')).toBe(420);
    expect(stepSeconds('2h')).toBe(7200);
    expect(stepSeconds('3D')).toBe(3 * 86_400);
    expect(stepSeconds('2W')).toBe(14 * 86_400);
    expect(stepSeconds('3M')).toBe(90 * 86_400);
    expect(stepSeconds('30m')).toBe(1800);
    expect(stepSeconds('1W')).toBe(7 * 86_400);
    expect(stepOf(ok('90m'))).toBe(5400);
  });
});
