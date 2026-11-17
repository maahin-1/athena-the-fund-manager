import type { DerivRow } from '../feed';
import type { Quote } from './chain';
import type { SessionState } from './quality';
import { decideSession, sessionBadge, type Observation } from './session';
import { keyOf, type ContractKey, type OptionFeed } from './synthetic';

/** The live `OptionFeed`: prices, volume and open interest of futures and options as the broker's stream last sent them, read through the terminal server's
 *  `/api/derivs/quotes` route. Asking for contracts is what makes the server subscribe them, so the page keeps asking while it is open; a contract the stream has
 *  not yet spoken about is simply missing from the answer. */

/** The server answers at most this many contracts a request. */
const MAX_ASKED = 100;

export type LiveQuoteRow = DerivRow;

/** The page's socket, when it can push futures and options (the `Feed`). */
export interface PushSource {
  readonly derivs: ReadonlyMap<string, DerivRow>;
  readonly pushing: boolean;
  /** The server's clock less this page's, as of the last frame (ms), when known. */
  readonly serverSkewMs?: number | null;
  needDerivs(owner: string, keys: string[]): void;
  onDerivs(fn: (rows: DerivRow[]) => void): () => void;
}

/** A contract asked for is kept on the socket's list this long after the last ask, so the chain, the futures and the volatility curve share one list. */
export const PUSH_KEEP_MS = 30_000;
const OWNER = 'options';

export interface LiveFeed extends OptionFeed {
  /** Whether the server has a broker stream to ask. False under the fake feed or while the broker is not connected. */
  probe(): Promise<boolean>;
  /** The newest exchange timestamp (ms) of any packet seen, or null. Information only: the session is decided by `session()`. */
  newest(): number | null;
  /** The one session policy (`session.ts`), in the server's clock: live only while packets are being received and carry current exchange times; else the last session (or unknown), with the reason and the
   *  evidence. `referenceMs` is the newest **receipt** time seen, the time ages are measured from after the session. */
  session(): SessionState;
  /** The server's clock now (ms): this page's clock moved by the offset the last answer showed. */
  serverNow(): number;
  /** What the feed is, said beside the prices; the same decision as `session()`. */
  badge(): { text: string; title: string };
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

/** A number as sent, a non-finite one included (the quality policy records it as invalid rather than losing it); null and absent are none. */
const raw = (v: number | null | undefined): number | undefined => (typeof v === 'number' ? v : undefined);

/** A server row as a quote. The times stay apart: `lt` is the exchange's time of the last **trade** (from the packet's own last-traded field), `t` the exchange's timestamp of the **packet** (not a trade time),
 *  `r` the server's receipt of the packet that gave the quote its content, `bs` the server's receipt of the last change of the best bid or ask (the stream carries no exchange time for the book), `dly` how
 *  late the last new packet arrived, `ooo` how many older packets were discarded, `tf` the times the server refused. A server that sends none of them gives none, and the quote is then labelled rather than
 *  called fresh; a server that predates `lt` gives no trade time (the packet's time is never used in its place). */
export function quoteOf(r: LiveQuoteRow, lot = 1): Quote {
  const per = unitsPerContract(r.seg, lot);
  const oi = Math.round(r.oi / per);
  return {
    last: r.last, bid: r.bid, ask: r.ask, volume: Math.round(r.volume / per), oi, oiChange: oiChange(oi, r.oip), time: raw(r.lt), packetTime: raw(r.t),
    receivedAt: raw(r.r), bookSeenAt: raw(r.bs), deliveryDelayMs: raw(r.dly), outOfOrder: r.ooo ? r.ooo : undefined, timeFaults: r.tf?.length ? r.tf : undefined,
  };
}

export function liveFeed(o: LiveOptions): LiveFeed {
  const fetcher = o.fetcher ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const now = o.now ?? (() => Date.now());
  let live = true;
  /** The server's clock less this page's, as of the last answer or frame. */
  let skew = 0;
  /** The newest packet seen of each contract: when the server received it and the exchange's own time on it. A repeat of a packet carries the same receipt (the server does not renew it), so it changes nothing. */
  const observed = new Map<string, Observation>();

  async function ask(tokens: string): Promise<{ live: boolean; quotes: LiveQuoteRow[] }> {
    const r = await fetcher(`/api/derivs/quotes?tokens=${encodeURIComponent(tokens)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const answer = (await r.json()) as { live: boolean; quotes: LiveQuoteRow[]; now?: number };
    if (typeof answer.now === 'number' && Number.isFinite(answer.now)) skew = answer.now - now();
    return answer;
  }

  function observe(row: LiveQuoteRow): void {
    observed.set(`${row.seg}:${row.token}`, { receivedAt: typeof row.r === 'number' ? row.r : null, eventTime: typeof row.t === 'number' ? row.t : null });
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

  const serverNow = (): number => now() + skew;
  const session = (): SessionState => decideSession([...observed.values()], serverNow());

  return {
    synthetic: false,
    onUpdate: o.push
      ? (fn) =>
          o.push!.onDerivs((rows) => {
            if (rows.some((r) => asked.has(`${r.seg}:${r.token}`))) fn();
          })
      : undefined,
    release() {
      observed.clear();
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
    newest: () => {
      const times = [...observed.values()].map((v) => v.eventTime).filter((t): t is number => typeof t === 'number' && Number.isFinite(t));
      return times.length ? Math.max(...times) : null;
    },
    session,
    serverNow,
    async spot(underlying) {
      return o.spotOf(underlying);
    },
    async quotes(keys) {
      const out = new Map<string, Quote>();
      const byRef = new Map<string, ContractKey>();
      for (const k of keys) if (k.ref) byRef.set(`${k.ref.segment}:${k.ref.token}`, k);
      const refs = [...byRef.keys()];
      if (o.push?.pushing && live) {
        name(refs); // asking is what keeps them on the stream; what has been pushed so far is the answer
        if (typeof o.push.serverSkewMs === 'number') skew = o.push.serverSkewMs;
        for (const ref of refs) {
          const row = o.push.derivs.get(ref);
          const key = byRef.get(ref);
          if (!row || !key) continue;
          out.set(keyOf(key), quoteOf(row, key.ref?.lot ?? 1));
          observe(row);
        }
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
          out.set(keyOf(key), quoteOf(row, key.ref?.lot ?? 1));
          observe(row);
        }
      }
      return out;
    },
    badge() {
      if (!live) return { text: 'no live prices', title: 'The server has no broker stream to ask.' };
      return sessionBadge(session());
    },
  };
}
