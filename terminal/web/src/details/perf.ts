import type { Bar } from '../bars';

export type PerfKey = '1W' | '1M' | '3M' | '6M' | 'YTD' | '1Y' | '3Y' | '5Y';
export interface PerfRow {
  key: PerfKey;
  pct: number | null;
}

const DAY = 86_400;
const MONTHS: Partial<Record<PerfKey, number>> = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, '5Y': 60 };
export const PERF_KEYS: PerfKey[] = ['1W', '1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y'];

/** The calendar date `n` months before `t` (midnight UTC seconds), the day clamped to the end of a shorter month. */
export function minusMonths(t: number, n: number): number {
  const d = new Date(t * 1000);
  const month = d.getUTCFullYear() * 12 + d.getUTCMonth() - n;
  const year = Math.floor(month / 12);
  const m = month - year * 12;
  const last = new Date(Date.UTC(year, m + 1, 0)).getUTCDate();
  return Date.UTC(year, m, Math.min(d.getUTCDate(), last)) / 1000;
}

/** Index of the last bar at or before `t`, or -1 when every bar is later. */
function lastAtOrBefore(bars: Bar[], t: number): number {
  let lo = 0;
  let hi = bars.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].t <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/** Return over each standard period, from the last close (or `last`, a live price) back to the close on or before the same calendar date; null where the stored bars start later. */
export function performance(bars: Bar[], last?: number): PerfRow[] {
  const end = bars.length - 1;
  if (end < 1) return PERF_KEYS.map((key) => ({ key, pct: null }));
  const now = last !== undefined && last > 0 ? last : bars[end].c;
  const at = bars[end].t;
  return PERF_KEYS.map((key) => {
    let target: number;
    if (key === '1W') target = at - 7 * DAY;
    else if (key === 'YTD') target = Date.UTC(new Date(at * 1000).getUTCFullYear() - 1, 11, 31) / 1000;
    else target = minusMonths(at, MONTHS[key] as number);
    const i = lastAtOrBefore(bars, target);
    if (i < 0 || i >= end || !(bars[i].c > 0)) return { key, pct: null };
    return { key, pct: (now / bars[i].c - 1) * 100 };
  });
}
