/** The calendar's logic without a page: date ranges, the merge of board meetings and corporate actions into one list, the categories a reader filters by, the
 *  filter, and the grouping by day. Dates are `YYYY-MM-DD` text throughout, so no time zone can move a day. */

export interface CalEvent {
  symbol: string;
  name: string;
  date: string;
  purpose: string;
  kinds: string[];
  description: string;
}

export interface CalAction {
  symbol: string;
  name: string;
  date: string;
  kind: string;
  subject: string;
}

export interface CalBody {
  from: string;
  to: string;
  events: CalEvent[];
  actions: CalAction[];
  /** The last date the stored board meetings reach, and the last ex-date held; null when none. */
  events_through: string | null;
  actions_through: string | null;
  truncated: boolean;
}

export type Category = 'results' | 'dividend' | 'splitbonus' | 'rights' | 'scheme' | 'meeting' | 'other';

export const CATEGORIES: { id: Category; label: string }[] = [
  { id: 'results', label: 'Results' },
  { id: 'dividend', label: 'Dividends' },
  { id: 'splitbonus', label: 'Splits and bonuses' },
  { id: 'rights', label: 'Rights, buybacks, fund raising' },
  { id: 'scheme', label: 'Schemes and capital' },
  { id: 'meeting', label: 'General meetings' },
  { id: 'other', label: 'Other' },
];

export interface Entry {
  date: string;
  symbol: string;
  name: string;
  /** A board meeting the company has called, or a corporate action's ex-date. */
  source: 'meeting' | 'action';
  /** What the exchange calls it. */
  label: string;
  /** For a meeting what it will consider; for an ex-date, the word "ex-date". */
  detail: string;
  cats: Category[];
}

export interface Range {
  from: string;
  to: string;
}

export type Preset = 'week' | 'fortnight' | 'month' | 'past';

const day = (text: string): Date => new Date(`${text}T00:00:00Z`);

export function addDays(text: string, n: number): string {
  const d = day(text);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function rangeFor(preset: Preset, today: string): Range {
  switch (preset) {
    case 'week':
      return { from: today, to: addDays(today, 6) };
    case 'fortnight':
      return { from: today, to: addDays(today, 13) };
    case 'month':
      return { from: today, to: addDays(today, 30) };
    case 'past':
      return { from: addDays(today, -30), to: today };
  }
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function dayLabel(text: string): string {
  const d = day(text);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

const MEETING_CATEGORY: Record<string, Category> = { results: 'results', dividend: 'dividend', fundraising: 'rights', bonus: 'splitbonus', split: 'splitbonus', buyback: 'rights', other: 'other' };
const ACTION_CATEGORY: Record<string, Category> = {
  dividend: 'dividend', bonus: 'splitbonus', split: 'splitbonus', rights: 'rights', buyback: 'rights', scheme: 'scheme', capital: 'scheme', meeting: 'meeting', distribution: 'other', other: 'other',
};

const unique = (list: Category[]): Category[] => [...new Set(list)];

/** One list from the API's two: each board meeting and each ex-date an entry. */
export function merge(body: CalBody): Entry[] {
  const meetings = body.events.map((e): Entry => ({
    date: e.date, symbol: e.symbol, name: e.name, source: 'meeting', label: e.purpose, detail: e.description,
    cats: unique((e.kinds.length ? e.kinds : ['other']).map((k) => MEETING_CATEGORY[k] ?? 'other')),
  }));
  const actions = body.actions.map((a): Entry => ({
    date: a.date, symbol: a.symbol, name: a.name, source: 'action', label: a.subject, detail: 'ex-date', cats: [ACTION_CATEGORY[a.kind] ?? 'other'],
  }));
  return [...meetings, ...actions];
}

export interface Filter {
  /** Categories wanted; empty means every one. */
  cats: Set<string>;
  text: string;
  /** Only these stocks (the current screen), or null for every stock. */
  symbols: Set<string> | null;
  /** Only these stocks (the watchlist), or null for every stock. */
  watched: Set<string> | null;
}

export function filterEntries(entries: Entry[], f: Filter): Entry[] {
  const needle = f.text.trim().toLowerCase();
  return entries.filter((e) => {
    if (f.cats.size && !e.cats.some((c) => f.cats.has(c))) return false;
    if (needle && !`${e.symbol} ${e.name} ${e.label} ${e.detail}`.toLowerCase().includes(needle)) return false;
    if (f.symbols && !f.symbols.has(e.symbol)) return false;
    if (f.watched && !f.watched.has(e.symbol)) return false;
    return true;
  });
}

export interface Day {
  date: string;
  label: string;
  entries: Entry[];
  meetings: number;
  actions: number;
}

/** The entries by date, oldest first; within a day the board meetings come first, then the ex-dates, each by symbol. */
export function groupByDate(entries: Entry[]): Day[] {
  const byDate = new Map<string, Entry[]>();
  for (const e of entries) byDate.set(e.date, [...(byDate.get(e.date) ?? []), e]);
  return [...byDate.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, list]) => {
      const sorted = [...list].sort((a, b) => (a.source === b.source ? a.symbol.localeCompare(b.symbol) : a.source === 'meeting' ? -1 : 1));
      return { date, label: dayLabel(date), entries: sorted, meetings: sorted.filter((e) => e.source === 'meeting').length, actions: sorted.filter((e) => e.source === 'action').length };
    });
}
