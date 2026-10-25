import type { DerivRow } from '../feed';
import type { Quote } from './chain';
import { keyOf, type ContractKey, type OptionFeed } from './synthetic';

/** The live `OptionFeed`: prices, volume and open interest of futures and options as the broker's stream last sent them, read through the terminal server's
 *  `/api/derivs/quotes` route. Asking for contracts is what makes the server subscribe them, so the page keeps asking while it is open; a contract the stream has
 *  not yet spoken about is simply missing from the answer. */

/** The server answers at most this many contracts a request. */
const MAX_ASKED = 100;
/** A newest exchange time older than this means the market is shut (or the contract is idle) and the prices are the last ones sent. */
const FRESH_MS = 120_000;

export type LiveQuoteRow = DerivRow;

/** The page's socket, when it can push futures and options (the `Feed`). */
export interface PushSource {
  readonly derivs: ReadonlyMap<string, DerivRow>;
  readonly pushing: boolean;
  needDerivs(owner: string, keys: string[]): void;
  onDerivs(fn: (rows: DerivRow[]) => void): () => void;
}

/** A contract asked for is kept on the socket's list this long after the last ask, so the chain, the futures and the volatility curve share one list. */
export const PUSH_KEEP_MS = 30_000;
const OWNER = 'options';

export interface LiveFeed extends OptionFeed {
  /** Whether the server has a broker stream to ask. False under the fake feed or while the broker is not connected. */
  probe(): Promise<boolean>;
  /** The exchange time (ms) of the newest quote seen, or null. */
  newest(): number | null;
  /** Called on each pushed change of a contract this feed was asked about (only with a push source). */
  onUpdate?(fn: () => void): () => void;
  /** Let go of every contract named on the socket (the page was left). */
  release(): void;
}

export interface LiveOptions {
  fetcher?: typeof fetch;
  /** The price of the underlying itself (an index value or a share price) when the page has one, else null. */
  spotOf(underlying: string): number | null;
  now?: () => number;
  /** The socket: while it pushes, quotes are read from what it has pushed and the quotes route is not asked. */
  push?: PushSource;
}

/** The change in open interest, in contracts, from the stream's change in percent: the open interest is `previous × (1 + pct/100)`. */
export function oiChange(oi: number, pct: number): number {
  if (!Number.isFinite(pct) || pct <= -100 || oi <= 0) return 0;
  return Math.round(oi - oi / (1 + pct / 100));
}

/** Open interest and volume come from NSE and BSE derivatives as a quantity of the underlying (checked against the real stream: every value is a whole number of
 *  lots), and from MCX already as lots; the page shows contracts, which are lots. */
export const unitsPerContract = (segment: string, lot: number): number => (segment === 'MCX' || !(lot > 1) ? 1 : lot);

export function quoteOf(r: LiveQuoteRow, lot = 1): Quote {
  const per = unitsPerContract(r.seg, lot);
  const oi = Math.round(r.oi / per);
  return { last: r.last, bid: r.bid, ask: r.ask, volume: Math.round(r.volume / per), oi, oiChange: oiChange(oi, r.oip), time: r.t ?? undefined };
}

export function liveFeed(o: LiveOptions): LiveFeed {
  const fetcher = o.fetcher ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const now = o.now ?? (() => Date.now());
  let newest: number | null = null;
  /** When the newest exchange time last moved on: a feed whose times keep advancing is live whatever the exchange's clock says, and one whose times stand still
   *  is showing the last session. The first sight of a time is not an advance. */
  let advancedAt: number | null = null;
  let live = true;

  async function ask(tokens: string): Promise<{ live: boolean; quotes: LiveQuoteRow[] }> {
    const r = await fetcher(`/api/derivs/quotes?tokens=${encodeURIComponent(tokens)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return (await r.json()) as { live: boolean; quotes: LiveQuoteRow[] };
  }

  /** Contracts named on the socket, with when each was last asked for. */
  const asked = new Map<string, number>();
  function name(refs: string[]): void {
    const t = now();
    let added = false;
    for (const r of refs) {
      if (!asked.has(r)) added = true;
      asked.set(r, t);
    }
    let dropped = false;
    for (const [r, at] of asked) if (t - at > PUSH_KEEP_MS) (asked.delete(r), (dropped = true));
    if (added || dropped) o.push?.needDerivs(OWNER, [...asked.keys()]);
  }

  function note(seen: number | null): void {
    if (seen !== null && (newest === null || seen > newest)) {
      if (newest !== null) advancedAt = now();
      newest = seen;
    }
  }

  return {
    synthetic: false,
    onUpdate: o.push
      ? (fn) =>
          o.push!.onDerivs((rows) => {
            if (rows.some((r) => asked.has(`${r.seg}:${r.token}`))) fn();
          })
      : undefined,
    release() {
      if (asked.size === 0) return;
      asked.clear();
      o.push?.needDerivs(OWNER, []);
    },
    async probe() {
      try {
        live = (await ask('')).live;
      } catch {
        live = false;
      }
      return live;
    },
    newest: () => newest,
    async spot(underlying) {
      return o.spotOf(underlying);
    },
    async quotes(keys) {
      const out = new Map<string, Quote>();
      const byRef = new Map<string, ContractKey>();
      for (const k of keys) if (k.ref) byRef.set(`${k.ref.segment}:${k.ref.token}`, k);
      const refs = [...byRef.keys()];
      let seen: number | null = null; // the newest time in this call's answers
      if (o.push?.pushing && live) {
        name(refs); // asking is what keeps them on the stream; what has been pushed so far is the answer
        for (const ref of refs) {
          const row = o.push.derivs.get(ref);
          const key = byRef.get(ref);
          if (!row || !key) continue;
          out.set(keyOf(key), quoteOf(row, key.ref?.lot ?? 1));
          if (row.t !== null && (seen === null || row.t > seen)) seen = row.t;
        }
        note(seen);
        return out;
      }
      for (let i = 0; i < refs.length; i += MAX_ASKED) {
        const answer = await ask(refs.slice(i, i + MAX_ASKED).join(','));
        live = answer.live;
        // the server's stream is down (it answers with no quotes): say so and let the page keep the prices it has rather than draw an empty chain
        if (!live) throw new Error('the server has no stream');
        for (const row of answer.quotes) {
          const key = byRef.get(`${row.seg}:${row.token}`);
          if (!key) continue;
          const q = quoteOf(row, key.ref?.lot ?? 1);
          out.set(keyOf(key), q);
          if (row.t !== null && (seen === null || row.t > seen)) seen = row.t;
        }
      }
      note(seen);
      return out;
    },
    badge() {
      if (!live) return { text: 'no live prices', title: 'The server has no broker stream to ask.' };
      const t = newest;
      if ((t !== null && now() - t <= FRESH_MS) || (advancedAt !== null && now() - advancedAt <= FRESH_MS)) return { text: 'live prices', title: 'Prices, volume and open interest as the broker\'s stream last sent them.' };
      return { text: 'last session prices', title: 'The market is shut or these contracts have not traded lately: these are the last prices the exchange sent.' };
    },
  };
}
