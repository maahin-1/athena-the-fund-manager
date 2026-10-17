/** Live index values from the server's copy of the exchange's all-indices snapshot (`/api/indices`). */

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

export class IndexFeed {
  private byName = new Map<string, IndexRow>();
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  asOf = '';
  open = false;
  staleS = 0;
  /** The last request failed or the server has no snapshot yet. */
  unavailable = true;

  constructor(private fetcher: typeof fetch = (...a) => fetch(...a)) {}

  get(name: string): IndexRow | undefined {
    return this.byName.get(name.trim().toUpperCase());
  }

  has(name: string): boolean {
    return this.byName.has(name.trim().toUpperCase());
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
