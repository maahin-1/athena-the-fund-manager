import type { Bar } from './bars';
import type { Range, Timeframe } from './ranges';

/** Intervals a person can type (`7m`, `90`, `2h`, `3D`, `2W`, `3M`) and how the Chart Lab builds them from a finer interval the server already serves.
 *  Everything here is pure. A bar's start is a function of its moment alone (fixed anchors: the 09:15 IST open for minutes, 1970 for days, weeks and
 *  months), so history grouped here and a live tick given `startOf` cannot disagree, and a bar never moves when more history is loaded. */

export type Unit = 'm' | 'D' | 'W' | 'M';

export interface Parsed {
  /** The spelling used everywhere (`7m`, `2h`, `3D`); a built-in interval keeps its built-in id. */
  id: string;
  unit: Unit;
  n: number;
  /** For minutes, the length of a bar in minutes (hours already multiplied). */
  minutes: number;
  builtin: boolean;
}

export type ParseResult = ({ ok: true } & Parsed) | { ok: false; error: string };

export const BUILTIN: readonly string[] = ['1m', '2m', '3m', '5m', '10m', '15m', '30m', '1h', '1D', '1W', '1M'];
export const LIMITS = { minutes: 375, days: 60, weeks: 26, months: 24 } as const;

const IST = 19_800;
const DAY = 86_400;
const OPEN = 9 * 3600 + 15 * 60;
const SESSION_MINUTES = 375;
const TRADING_DAYS = { week: 5, month: 21 } as const;

const WORDS: { unit: Unit | 'h'; test: RegExp }[] = [
  { unit: 'm', test: /^(min|mins|minute|minutes)$/i },
  { unit: 'h', test: /^(h|hr|hrs|hour|hours)$/i },
  { unit: 'D', test: /^(d|day|days)$/i },
  { unit: 'W', test: /^(w|wk|wks|week|weeks)$/i },
  { unit: 'M', test: /^(mo|mon|month|months)$/i },
];

/** The id of an interval of this many minutes: the built-in button's id when there is one. */
const minuteId = (m: number): string => (m === 60 ? '1h' : m % 60 === 0 ? `${m / 60}h` : `${m}m`);

export function parseInterval(text: string): ParseResult {
  const t = text.trim();
  const m = /^(\d{1,4})\s*([A-Za-z]*)$/.exec(t);
  if (!m) return { ok: false, error: 'write a number and a unit, such as 7m, 2h, 3D, 2W or 3M' };
  const n = Number(m[1]);
  const word = m[2];
  let unit: Unit | 'h' | undefined;
  if (word === '' || word === 'm') unit = 'm'; // a plain number is minutes; one lowercase m is minutes and one capital M is months
  else if (word === 'M') unit = 'M';
  else unit = WORDS.find((w) => w.test.test(word))?.unit;
  if (!unit) return { ok: false, error: `"${word}" is not a unit; use m (minutes), h (hours), D (days), W (weeks) or M (months)` };
  if (n < 1) return { ok: false, error: 'the number must be at least 1' };
  if (unit === 'm' || unit === 'h') {
    const minutes = unit === 'h' ? n * 60 : n;
    if (minutes > LIMITS.minutes) return { ok: false, error: `a bar of at most ${LIMITS.minutes} minutes (a whole session, 6 hours) can be built` };
    const id = minuteId(minutes);
    return { ok: true, id, unit: 'm', n: minutes, minutes, builtin: BUILTIN.includes(id) };
  }
  const limit = unit === 'D' ? LIMITS.days : unit === 'W' ? LIMITS.weeks : LIMITS.months;
  const name = unit === 'D' ? 'days' : unit === 'W' ? 'weeks' : 'months';
  if (n > limit) return { ok: false, error: `at most ${limit} ${name} to a bar` };
  const id = `${n}${unit}`;
  return { ok: true, id, unit, n, minutes: 0, builtin: BUILTIN.includes(id) };
}

/** Whether bars of this interval are inside one trading day. */
export const isIntradayId = (id: string): boolean => /^\d+[mh]$/.test(id);

export interface Plan {
  /** The interval the server is asked for. */
  fetch: Timeframe;
  /** Source bars per bar built (1 when the source already is the interval). */
  factor: number;
  /** How many calendar days of history the free source holds for that interval (Infinity for daily bars). */
  depthDays: number;
}

const SOURCES: { minutes: number; tf: Timeframe; depth: number }[] = [
  { minutes: 60, tf: '1h', depth: 260 },
  { minutes: 30, tf: '30m', depth: 22 },
  { minutes: 15, tf: '15m', depth: 22 },
  { minutes: 5, tf: '5m', depth: 22 },
  { minutes: 1, tf: '1m', depth: 5 },
];

/** The finer interval a custom one is built from: the coarsest the server serves that divides it, so as much history as possible is reachable. */
export function planOf(p: Parsed): Plan {
  if (p.unit !== 'm') return { fetch: '1D', factor: 1, depthDays: Infinity };
  const source = SOURCES.find((s) => p.minutes % s.minutes === 0) ?? SOURCES[SOURCES.length - 1];
  return { fetch: source.tf, factor: p.minutes / source.minutes, depthDays: source.depth };
}

/** Seconds between the stamps of consecutive bars, for a time axis that needs the bars in order. */
export function stepOf(p: Parsed): number {
  return p.unit === 'm' ? p.minutes * 60 : p.unit === 'D' ? p.n * DAY : p.unit === 'W' ? p.n * 7 * DAY : p.n * 30 * DAY;
}

/** Days since 1970-01-01 of the IST date of a moment, and the weekday of it (Monday is 0). */
function istDay(tsSec: number): { day: number; dow: number } {
  const day = Math.floor((tsSec + IST) / DAY);
  return { day, dow: (((day + 3) % 7) + 7) % 7 }; // 1970-01-01 was a Thursday
}

/** Monday-based week number of a date (week 0 begins Monday 1969-12-29). */
const weekOf = (day: number, dow: number): number => (day - dow + 3) / 7;

/** The start of the bar an interval gives a moment: the time of its first minute for minutes (09:15 IST based), otherwise midnight UTC of the date of
 *  its first weekday, Monday or the 1st, the stamp a daily bar has. */
export function startOf(tsSec: number, p: Parsed): number {
  if (p.unit === 'm') {
    const width = p.minutes * 60;
    const local = tsSec + IST;
    const open = Math.floor(local / DAY) * DAY + OPEN;
    return open + Math.floor((local - open) / width) * width - IST;
  }
  const { day, dow } = istDay(tsSec);
  if (p.unit === 'D') {
    const weekday = weekOf(day, dow) * 5 + Math.min(dow, 4); // a weekend meets Friday's number
    const first = Math.floor(weekday / p.n) * p.n;
    return (Math.floor(first / 5) * 7 - 3 + (first % 5)) * DAY;
  }
  if (p.unit === 'W') return (Math.floor(weekOf(day, dow) / p.n) * p.n * 7 - 3) * DAY;
  const date = new Date(day * DAY * 1000);
  const month = date.getUTCFullYear() * 12 + date.getUTCMonth();
  const first = Math.floor(month / p.n) * p.n;
  return Date.UTC(Math.floor(first / 12), first % 12, 1) / 1000;
}

export function mergeBars(group: Bar[], t: number): Bar {
  return {
    t, o: group[0].o, h: Math.max(...group.map((b) => b.h)), l: Math.min(...group.map((b) => b.l)), c: group[group.length - 1].c,
    v: group.reduce((sum, b) => sum + b.v, 0),
  };
}

/** Bars of this interval from finer ones, oldest first. A group the data ends in is kept as it is (the last day's, week's or month's). */
export function groupBars(bars: Bar[], p: Parsed): Bar[] {
  const out: Bar[] = [];
  let group: Bar[] = [];
  let start = NaN;
  for (const b of bars) {
    const s = startOf(b.t, p);
    if (s !== start && group.length) {
      out.push(mergeBars(group, start));
      group = [];
    }
    start = s;
    group.push(b);
  }
  if (group.length) out.push(mergeBars(group, start));
  return out;
}

const SPANS: [label: string, days: number][] = [['1D', 1], ['5D', 5], ['1M', 22], ['6M', 126], ['1Y', 252], ['5Y', 1260]];

/** The ranges an interval offers: how much history the free source holds for it decides the longest, and a range with too few bars to draw is left out. */
export function rangesOf(p: Parsed): Range[] {
  const plan = planOf(p);
  const perDay = p.unit === 'm' ? SESSION_MINUTES / p.minutes : p.unit === 'D' ? 1 / p.n : p.unit === 'W' ? 1 / (TRADING_DAYS.week * p.n) : 1 / (TRADING_DAYS.month * p.n);
  const out: Range[] = [];
  for (const [label, days] of SPANS) {
    if (days > plan.depthDays) continue;
    if (p.unit !== 'm' && days < 126) continue; // daily-based bars are for months and years
    const bars = Math.ceil(days * perDay);
    if (bars >= 8) out.push({ label, tf: p.id, bars });
  }
  if (plan.depthDays === Infinity || !out.length) out.push({ label: 'All', tf: p.id, bars: null });
  return out;
}

/** The range shown first: the shortest with at least 60 bars, else the longest. */
export function defaultRangeOf(p: Parsed): string {
  const ranges = rangesOf(p);
  return (ranges.find((r) => typeof r.bars === 'number' && r.bars >= 60) ?? ranges[ranges.length - 1]).label;
}
