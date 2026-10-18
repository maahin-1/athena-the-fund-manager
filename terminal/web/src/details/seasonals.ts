import type { Bar } from '../bars';

const DAY = 86_400;
const GRID = 52; // points of the average path across the year

export interface YearPath {
  year: number;
  points: [number, number][]; // [fraction of the year 0..1, cumulative return in percent since the previous year's last close]
}

export interface MonthStat {
  month: number; // 0 = January
  avg: number | null; // mean monthly return in percent
  up: number | null; // share of years the month closed higher, 0..1
  n: number; // years used
}

export interface Seasonals {
  years: YearPath[]; // up to the last `show` years, the current (partial) one last
  average: [number, number][]; // mean path over the complete years
  months: MonthStat[];
}

const yearOf = (t: number): number => new Date(t * 1000).getUTCFullYear();
const monthOf = (t: number): number => new Date(t * 1000).getUTCMonth();

/** Year-on-year paths and month statistics from daily bars. A year needs the previous year's last close to start from, so the first year in the data is skipped; the statistics use complete years only (every year but the last). Null with fewer than two complete years. */
export function seasonals(bars: Bar[], show = 10): Seasonals | null {
  if (bars.length < 2) return null;
  const endClose = new Map<number, number>(); // last close of each year
  const monthEnd = new Map<string, number>(); // last close of each year-month
  for (const b of bars) {
    endClose.set(yearOf(b.t), b.c);
    monthEnd.set(`${yearOf(b.t)}-${monthOf(b.t)}`, b.c);
  }
  const lastYear = yearOf(bars[bars.length - 1].t);
  const years: YearPath[] = [];
  for (const y of [...endClose.keys()].sort((a, b) => a - b)) {
    const prior = endClose.get(y - 1);
    if (prior === undefined || !(prior > 0)) continue;
    const jan1 = Date.UTC(y, 0, 1) / 1000;
    const span = ((Date.UTC(y + 1, 0, 1) / 1000) - jan1) / DAY;
    const points: [number, number][] = [];
    for (const b of bars) if (yearOf(b.t) === y) points.push([Math.min(1, (b.t - jan1) / DAY / span), (b.c / prior - 1) * 100]);
    if (points.length) years.push({ year: y, points });
  }
  const complete = years.filter((p) => p.year < lastYear);
  if (complete.length < 2) return null;

  const average: [number, number][] = [];
  for (let i = 0; i <= GRID; i++) {
    const x = i / GRID;
    let sum = 0;
    for (const p of complete) {
      let v = 0;
      for (const [px, py] of p.points) {
        if (px > x) break;
        v = py; // the path holds its last value between bars
      }
      sum += v;
    }
    average.push([x, sum / complete.length]);
  }

  const months: MonthStat[] = [];
  for (let m = 0; m < 12; m++) {
    const rets: number[] = [];
    for (const p of complete) {
      const end = monthEnd.get(`${p.year}-${m}`);
      const before = m === 0 ? endClose.get(p.year - 1) : monthEnd.get(`${p.year}-${m - 1}`);
      if (end !== undefined && before !== undefined && before > 0) rets.push((end / before - 1) * 100);
    }
    months.push({ month: m, avg: rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : null, up: rets.length ? rets.filter((r) => r > 0).length / rets.length : null, n: rets.length });
  }
  return { years: years.slice(-show), average, months };
}
