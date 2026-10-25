/** The futures and options board: the rows the server builds from the derivatives history (see `athena.flow.board`), filtered and sorted here. Pure. */
export interface BoardEvent {
  kind: string;
  line: string;
}

export interface BoardRow {
  symbol: string;
  kind: 'stock' | 'index';
  close: number;
  price_change: number | null;
  oi: number;
  oi_change: number | null;
  volume: number;
  put_call: number | null;
  events: BoardEvent[];
}

export interface Board {
  day: string | null;
  rows: BoardRow[];
  events: Record<string, number>;
  universe: number;
}

/** What each detector's event is called, and the plain meaning shown beside it. */
export const EVENT_LABELS: Record<string, [string, string]> = {
  long_build_up: ['Long build-up', 'price up with open interest up: new buyers'],
  short_build_up: ['Short build-up', 'price down with open interest up: new sellers'],
  short_covering: ['Short covering', 'price up with open interest down: sellers leaving'],
  long_unwinding: ['Long unwinding', 'price down with open interest down: buyers leaving'],
  oi_spike: ['OI spike', 'open interest far above its usual daily change'],
  put_call_up: ['Put-call up', 'put open interest high against its usual level'],
  put_call_down: ['Put-call down', 'put open interest low against its usual level'],
  max_pain_up: ['Max pain up', 'the max-pain strike has moved up over five days'],
  max_pain_down: ['Max pain down', 'the max-pain strike has moved down over five days'],
};

export type SortKey = 'symbol' | 'close' | 'price_change' | 'oi' | 'oi_change' | 'volume' | 'put_call' | 'events';

export interface BoardFilter {
  kind: '' | 'stock' | 'index';
  /** Event kinds the row must have at least one of; empty means any row. */
  events: Set<string>;
  text: string;
}

export const noBoardFilter = (): BoardFilter => ({ kind: '', events: new Set(), text: '' });

export function filterBoard(rows: readonly BoardRow[], f: BoardFilter): BoardRow[] {
  const text = f.text.trim().toLowerCase();
  return rows.filter((r) => (!f.kind || r.kind === f.kind) && (!text || r.symbol.toLowerCase().includes(text)) && (f.events.size === 0 || r.events.some((e) => f.events.has(e.kind))));
}

const value = (r: BoardRow, key: SortKey): number | string | null => (key === 'events' ? r.events.length : (r[key] as number | string | null));

/** Rows sorted by one column; a missing figure goes last whichever way the sort runs, and ties keep the symbol order. */
export function sortBoard(rows: readonly BoardRow[], key: SortKey, descending: boolean): BoardRow[] {
  const sign = descending ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = value(a, key);
    const y = value(b, key);
    if (x === null && y === null) return a.symbol.localeCompare(b.symbol);
    if (x === null) return 1;
    if (y === null) return -1;
    const c = typeof x === 'string' && typeof y === 'string' ? x.localeCompare(y) : Number(x) - Number(y);
    return c !== 0 ? sign * c : a.symbol.localeCompare(b.symbol);
  });
}

/** The event kinds present on the board, in the order they are listed above, with how many rows carry each. */
export function eventCounts(rows: readonly BoardRow[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const r of rows) for (const k of new Set(r.events.map((e) => e.kind))) counts.set(k, (counts.get(k) ?? 0) + 1);
  return Object.keys(EVENT_LABELS).filter((k) => counts.has(k)).map((k) => [k, counts.get(k) ?? 0]);
}

/** One line for a row's events, by their labels. */
export const eventsText = (r: BoardRow): string => r.events.map((e) => EVENT_LABELS[e.kind]?.[0] ?? e.kind).join(', ');

/** Whether a value read from the server has the shape of a board; anything else is refused rather than shown. */
export function readBoard(raw: unknown): Board | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const b = raw as Partial<Board>;
  if (!Array.isArray(b.rows) || (b.day !== null && typeof b.day !== 'string')) return null;
  const rows = b.rows.filter((r): r is BoardRow => typeof r === 'object' && r !== null && typeof (r as BoardRow).symbol === 'string' && Array.isArray((r as BoardRow).events));
  return { day: b.day ?? null, rows, events: b.events ?? {}, universe: typeof b.universe === 'number' ? b.universe : rows.length };
}
