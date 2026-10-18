import type { Bar } from '../bars';

export const BARS_TTL_MS = 5 * 60_000;
const LIMIT = 5000;

/** Daily bars for a symbol, fetched once and kept five minutes, so choosing the same symbol again (or three sections wanting the same bars) costs one request. A failure is not kept. */
export class BarsCache {
  private kept = new Map<string, { at: number; bars: Bar[] }>();
  private running = new Map<string, Promise<Bar[] | null>>();

  constructor(
    private readonly fetcher: typeof fetch = (...a) => fetch(...a),
    private readonly now: () => number = () => Date.now(),
  ) {}

  get(symbol: string): Promise<Bar[] | null> {
    const key = symbol.toUpperCase();
    const hit = this.kept.get(key);
    if (hit && this.now() - hit.at < BARS_TTL_MS) return Promise.resolve(hit.bars);
    let p = this.running.get(key);
    if (!p) {
      p = this.fetchBars(key).finally(() => this.running.delete(key));
      this.running.set(key, p);
    }
    return p;
  }

  private async fetchBars(key: string): Promise<Bar[] | null> {
    try {
      const r = await this.fetcher(`/api/bars?symbol=${encodeURIComponent(key)}&tf=1D&limit=${LIMIT}`);
      if (!r.ok) return null;
      const body = (await r.json()) as { bars?: Bar[] };
      if (!Array.isArray(body.bars) || body.bars.length === 0) return null;
      this.kept.set(key, { at: this.now(), bars: body.bars });
      return body.bars;
    } catch {
      return null;
    }
  }
}
