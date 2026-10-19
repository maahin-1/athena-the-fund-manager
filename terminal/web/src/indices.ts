/** Live index values: the broker stream's quote for an index when there is a recent one (about ten updates a second for the Nifty 50), else the server's copy of the
 *  exchange's all-indices snapshot (`/api/indices`), which changes about once a minute. */

import type { Quote } from './feed';
import { isIndexSymbol } from './watchlists/model';

export interface IndexRow {
  s: string;
  l: number;
  pc: number | null;
  ch: number | null;
  pct: number | null;
  o: number | null;
  h: number | null;
  lo: number | null;
  h52: number | null;
  l52: number | null;
  pe: number | null;
  pb: number | null;
  dy: number | null;
  w1: number | null;
  m1: number | null;
  y1: number | null;
  adv: number | null;
  dec: number | null;
}

export interface IndexSnapshot {
  as_of: string;
  open: boolean;
  stale_s: number;
  rows: IndexRow[];
}

/** While trading the snapshot changes about every five seconds; outside trading it does not change, so look once a minute. */
export const POLL_OPEN_MS = 5_000;
export const POLL_SHUT_MS = 60_000;
export const POLL_RETRY_MS = 3_000;

/** The part of the feed an index reads its live quotes from. */
export interface LiveQuotes {
  quotes: Map<string, Quote>;
  onQuotes(fn: (quotes: Quote[]) => void): () => void;
  need(owner: string, symbols: string[]): void;
}

/** A streamed index quote is used while the exchange's time on it is this recent (after the close it is old, and the snapshot, which then is the close, is shown). */
export const LIVE_WITHIN_MS = 120_000;
/** The page is told of live changes at most this often, so a row or a bar is not redrawn ten times a second. */
const NOTIFY_EVERY_MS = 400;

export class IndexFeed {
  private byName = new Map<string, IndexRow>();
  private live: LiveQuotes | null = null;
  private notifyTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  asOf = '';
  open = false;
  staleS = 0;
  /** The last request failed or the server has no snapshot yet. */
  unavailable = true;

  constructor(private fetcher: typeof fetch = (...a) => fetch(...a)) {}

  /** Take live quotes from the feed, and ask the server to stream these indices (the top bar's). Other panels ask for their own. */
  attachLive(feed: LiveQuotes, names: string[], now: () => number = () => Date.now()): void {
    this.live = feed;
    this.clock = now;
    feed.need('index-strip', names);
    feed.onQuotes((quotes) => {
      if (quotes.some((q) => this.liveQuote(q.s))) this.notifySoon();
    });
  }

  private clock: () => number = () => Date.now();

  private notifySoon(): void {
    if (this.notifyTimer) return;
    this.notifyTimer = setTimeout(() => {
      this.notifyTimer = null;
      this.listeners.forEach((fn) => fn());
    }, NOTIFY_EVERY_MS);
  }

  /** The streamed quote of an index when its exchange time is recent. */
  private liveQuote(name: string): Quote | undefined {
    const key = name.trim().toUpperCase();
    if (!isIndexSymbol(key)) return undefined; // a share's quote is not an index's
    const q = this.live?.quotes.get(key);
    return q && q.src === 'stream' && q.pc > 0 && q.t !== null && this.clock() - q.t <= LIVE_WITHIN_MS ? q : undefined;
  }

  get(name: string): IndexRow | undefined {
    const row = this.byName.get(name.trim().toUpperCase());
    const q = this.liveQuote(name);
    if (!q) return row;
    const ch = q.l - q.pc;
    return {
      s: row?.s ?? name.trim().toUpperCase(), pe: null, pb: null, dy: null, h52: null, l52: null, w1: null, m1: null, y1: null, adv: null, dec: null, ...row,
      l: q.l, pc: q.pc, ch, pct: (ch / q.pc) * 100, o: q.o ?? row?.o ?? null, h: q.h ?? row?.h ?? null, lo: q.lo ?? row?.lo ?? null,
    };
  }

  has(name: string): boolean {
    return this.byName.has(name.trim().toUpperCase()) || this.liveQuote(name) !== undefined;
  }

  get names(): string[] {
    return [...this.byName.values()].map((r) => r.s);
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    void this.tick();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** One request. Returns whether a snapshot arrived. */
  async refresh(): Promise<boolean> {
    try {
      const r = await this.fetcher('/api/indices');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = (await r.json()) as IndexSnapshot;
      if (!Array.isArray(body.rows)) throw new Error('no rows');
      this.byName = new Map(body.rows.map((row) => [row.s.toUpperCase(), row]));
      this.asOf = body.as_of;
      this.open = body.open;
      this.staleS = body.stale_s;
      this.unavailable = false;
      this.listeners.forEach((fn) => fn());
      return true;
    } catch {
      if (!this.unavailable) {
        this.unavailable = true; // keep the last values; say they are old
        this.listeners.forEach((fn) => fn());
      }
      return false;
    }
  }

  private async tick(): Promise<void> {
    const ok = await this.refresh();
    if (this.stopped) return;
    this.timer = setTimeout(() => void this.tick(), ok ? (this.open ? POLL_OPEN_MS : POLL_SHUT_MS) : POLL_RETRY_MS);
  }
}
