import { toRows, type Cell, type Row } from './screenlogic';

export const SCREEN_TTL_MS = 10 * 60_000;
export const SCREEN_RETRY_MS = 30_000;

/** The stored market screen (every stock and ETF: sector, market cap, P/E, P/B, ROE, returns, RSI and more), loaded once and kept ten minutes. Anything that wants a company's market-wide figures asks `get`, which also starts the load when it is missing or stale; listeners hear when new rows arrive. A failed load keeps the old rows and is retried after 30 seconds. */
export class ScreenStore {
  private rows = new Map<string, Row>();
  private loadedAt = 0;
  private ever = false;
  private failedAt: number | null = null;
  private inFlight: Promise<boolean> | null = null;
  private listeners = new Set<() => void>();
  marketDay: string | null = null;

  constructor(
    private readonly fetcher: typeof fetch = (...a) => fetch(...a),
    private readonly now: () => number = () => Date.now(),
  ) {}

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  get loaded(): boolean {
    return this.ever;
  }

  /** The screen row for a symbol (any case), or undefined when it is not in the market data or the rows have not arrived yet. */
  get(symbol: string): Row | undefined {
    this.ensure();
    return this.rows.get(symbol.toUpperCase());
  }

  /** Start a load if the rows are missing or stale and no load is running and a recent failure has cooled off. */
  ensure(): void {
    const age = this.now() - this.loadedAt;
    if (this.inFlight || (this.ever && age < SCREEN_TTL_MS)) return;
    if (this.failedAt !== null && this.now() - this.failedAt < SCREEN_RETRY_MS) return;
    void this.load();
  }

  load(): Promise<boolean> {
    this.inFlight ??= this.fetchRows().finally(() => (this.inFlight = null));
    return this.inFlight;
  }

  private async fetchRows(): Promise<boolean> {
    try {
      const r = await this.fetcher('/api/screen');
      if (!r.ok) throw new Error(String(r.status));
      const body = (await r.json()) as { columns: string[]; rows: Cell[][]; market_day: string | null };
      const next = new Map<string, Row>();
      for (const row of toRows(body.columns, body.rows)) if (typeof row.symbol === 'string') next.set(row.symbol.toUpperCase(), row);
      this.rows = next;
      this.marketDay = body.market_day;
      this.loadedAt = this.now();
      this.ever = true;
      this.failedAt = null;
      for (const fn of [...this.listeners]) fn();
      return true;
    } catch {
      this.failedAt = this.now();
      return false;
    }
  }
}
