import type { ChainStructure, ContractRef } from './api';
import { greeks, yearsToExpiry, type Greeks } from './black76';
import { atmIv, atmStrike, forwardOf, parityForwardDetail, maxPain, pcr, quotePrice, rowIv, smile, type Pcr, type Quote, type SmilePoint, type StrikeRow } from './chain';
import { assessQuote, chainQuality, decideForward, POLICY_V3, wording, type ChainQuality, type ForwardCandidate, type ForwardDecision, type QualityPolicy, type QuoteDecision, type SessionState } from './quality';
import { keyOf, type ContractKey } from './synthetic';

/** One chain, put together for the page: each strike's call and put with its quote, implied volatility and Greeks, and the statistics of the whole. Pure.
 *
 *  Quality (audit T3): every option and the forward are judged by a versioned policy (`quality.ts`) from their own times. A contract whose quote fails a rule that withholds has no volatility and no
 *  Greeks, and is left out of the smile, the at-the-money volatility and the parity forward; one that fails a rule that labels keeps them, with the flags on its `quality`. The forward is the future when
 *  the future's own quote passes, else the one the passing options imply, else spot grown at the rate (labelled: it has no time). The chain's badge is its worst **required** input (the forward and the
 *  at-the-money call and put), not its newest quote. */

export interface SideView {
  ref: ContractRef;
  quote: Quote | null;
  iv: number | null;
  /** Why there is no volatility (no price, below intrinsic value, a quality rule), for a tooltip. */
  ivNote: string | null;
  greeks: Greeks | null;
  /** The decision for this contract, with the inputs and the policy that produced it. */
  quality: QuoteDecision;
}

export interface RowView {
  strike: number;
  atm: boolean;
  /** The call is in the money (strike below the forward) and the put is (strike above it). */
  itmCall: boolean;
  itmPut: boolean;
  call: SideView | null;
  put: SideView | null;
}

export interface ChainView {
  underlying: string;
  expiry: string;
  /** Years to expiry, from now to 15:30 IST on the expiry date. */
  T: number;
  spot: number | null;
  forward: number | null;
  /** Where the forward came from: the future of that expiry, spot grown at the rate, or nothing. */
  forwardFrom: 'future' | 'parity' | 'spot' | null;
  atm: number | null;
  atmIv: number | null;
  pcr: Pcr;
  maxPain: number | null;
  smile: SmilePoint[];
  rows: RowView[];
  future: { ref: ContractRef; quote: Quote | null } | null;
  lot: number | null;
  /** Which inputs were fit to read a volatility from, under which policy: the chain's badge and the forward's decision. */
  quality: ChainQuality;
}

/** What the page tells `assemble` about the moment and the feed: the server's clock (the receipt times are in it), the session, and the policy (the current version unless a test says otherwise). */
export interface AssembleQuality {
  nowMs?: number;
  session?: SessionState;
  policy?: QualityPolicy;
}

const refOf = (s: ChainStructure, c: { token: string; lot: number }): Pick<ContractKey, 'ref'> => (s.segment ? { ref: { segment: s.segment, token: c.token, lot: c.lot } } : {});

/** The feed keys of everything a chain needs prices for, most needed first: the future (the forward is read from it), then the strikes nearest `centre` (the price of the underlying when
 *  known, else the middle strike), outwards. A live feed can stream only so many contracts; this order is what decides which. */
export function chainKeys(s: ChainStructure, centre: number | null = null): ContractKey[] {
  const keys: ContractKey[] = [];
  if (s.future) keys.push({ underlying: s.underlying, expiry: s.expiry, kind: 'future', strike: 0, ...refOf(s, s.future) });
  const mid = centre !== null && Number.isFinite(centre) ? centre : (s.strikes[Math.floor(s.strikes.length / 2)]?.strike ?? 0);
  const rows = [...s.strikes].sort((a, b) => Math.abs(a.strike - mid) - Math.abs(b.strike - mid) || a.strike - b.strike);
  for (const row of rows) {
    if (row.call) keys.push({ underlying: s.underlying, expiry: s.expiry, kind: 'call', strike: row.strike, ...refOf(s, row.call) });
    if (row.put) keys.push({ underlying: s.underlying, expiry: s.expiry, kind: 'put', strike: row.strike, ...refOf(s, row.put) });
  }
  return keys;
}

const quoteOf = (quotes: Map<string, Quote>, s: ChainStructure, kind: ContractKey['kind'], strike: number): Quote | null =>
  quotes.get(keyOf({ underlying: s.underlying, expiry: s.expiry, kind, strike })) ?? null;

/** A wing option must be priced at least this many ticks to count in the smile. */
const SMILE_MIN_TICKS = 10;

export function assemble(structure: ChainStructure, quotes: Map<string, Quote>, spot: number | null, nowSec: number, rate: number, qo: AssembleQuality = {}): ChainView {
  const T = yearsToExpiry(structure.expiry, nowSec);
  const policy = qo.policy ?? POLICY_V3;
  const nowMs = qo.nowMs ?? nowSec * 1000;
  const session: SessionState = qo.session ?? { state: 'unknown', referenceMs: null };
  const futureQuote = structure.future ? quoteOf(quotes, structure, 'future', 0) : null;
  const stats: StrikeRow[] = structure.strikes.map((r) => ({ strike: r.strike, call: r.call ? quoteOf(quotes, structure, 'call', r.strike) : null, put: r.put ? quoteOf(quotes, structure, 'put', r.strike) : null }));

  // 1. each contract on its own times, before any forward is chosen: only quotes that pass may be a forward's inputs
  const decided = new Map<Quote, QuoteDecision>();
  const own = (q: Quote | null): QuoteDecision => {
    if (q === null) return assessQuote(null, { now: nowMs, session, forward: null, policy });
    let d = decided.get(q);
    if (!d) decided.set(q, (d = assessQuote(q, { now: nowMs, session, forward: null, policy })));
    return d;
  };
  const futureOwn = futureQuote ? own(futureQuote) : null;
  const futureUsable = futureQuote !== null && futureOwn !== null && futureOwn.state !== 'withheld';
  const futurePrice = futureUsable ? quotePrice(futureQuote) : null;
  const usable = (q: Quote | null): Quote | null => (q && own(q).state !== 'withheld' ? q : null);
  const parityRows: StrikeRow[] = stats.map((r) => ({ strike: r.strike, call: usable(r.call), put: usable(r.put) }));
  const parityDetail = parityForwardDetail(parityRows, T, rate);
  const parity = parityDetail?.forward ?? null;
  // contracts that would have been parity inputs (a quote with a price on both sides of a strike) but failed their own rules: on record, with why
  const excluded: ForwardDecision['excluded'] = [];
  for (const r of stats) {
    if (!r.call || !r.put || quotePrice(r.call) === null || quotePrice(r.put) === null) continue;
    for (const [kind, q] of [['call', r.call], ['put', r.put]] as const) {
      const d = own(q);
      if (d.state === 'withheld') excluded.push({ strike: r.strike, kind, flags: d.withheld });
    }
  }

  // 2. the forward, and its own decision (judged from its inputs: their labels are carried, they must be in step, and its time is the oldest of them; a stale or unsynchronised one is passed on to every option)
  const forward = forwardOf(spot, futurePrice, T, rate, parity);
  const forwardFrom = forward === null ? null : futurePrice !== null && futurePrice > 0 ? 'future' : forward === parity ? 'parity' : 'spot';
  const rejected = futureOwn && futureOwn.state === 'withheld' ? [{ source: 'future' as const, flags: futureOwn.withheld }] : [];
  // every source that was looked at, in the order tried, with the outcome and why
  const candidates: ForwardCandidate[] = [];
  if (!structure.future) candidates.push({ source: 'future', outcome: 'absent', reason: 'no_future', flags: [] });
  else if (!futureQuote) candidates.push({ source: 'future', outcome: 'rejected', reason: 'no_quote', flags: ['no_quote'] });
  else if (futureOwn!.state === 'withheld') candidates.push({ source: 'future', outcome: 'rejected', reason: 'withheld', flags: futureOwn!.withheld });
  else if (futurePrice === null) candidates.push({ source: 'future', outcome: 'rejected', reason: 'no_price', flags: [] });
  else candidates.push({ source: 'future', outcome: 'used', reason: 'own_quote_fit', flags: futureOwn!.flags });
  if (forwardFrom !== 'future') {
    if (parity === null) candidates.push({ source: 'parity', outcome: 'rejected', reason: 'no_usable_pair', flags: [] });
    else if (forwardFrom !== 'parity') candidates.push({ source: 'parity', outcome: 'rejected', reason: 'far_from_spot', flags: [] });
    else candidates.push({ source: 'parity', outcome: 'used', reason: 'implied_by_fit_pairs', flags: [] });
    if (forwardFrom === 'spot') candidates.push({ source: 'spot', outcome: 'used', reason: 'grown_from_spot', flags: ['forward_age_unknown'] });
    else if (forwardFrom === null) candidates.push({ source: 'spot', outcome: 'absent', reason: 'no_spot', flags: [] });
  }
  const forwardDecision = decideForward({
    now: nowMs, session, policy, source: forwardFrom, value: forward, rejected, candidates, excluded,
    used: forwardFrom === 'future' && futureQuote ? [futureQuote] : forwardFrom === 'parity' && parityDetail ? parityDetail.quotes : [],
    roles: forwardFrom === 'future' ? [{ role: 'future', strike: null }] : forwardFrom === 'parity' && parityDetail ? parityDetail.inputs.map((i) => ({ role: i.role, strike: i.strike })) : [],
  });

  // 3. each option against the forward
  const judge = (q: Quote | null): QuoteDecision => assessQuote(q, { now: nowMs, session, forward: forwardDecision, policy });
  const atm = atmStrike(stats, forward);
  const decisions: QuoteDecision[] = [];
  const side = (ref: ContractRef | null, quote: Quote | null, kind: 'call' | 'put', strike: number): SideView | null => {
    if (!ref) return null;
    const quality = judge(quote);
    decisions.push(quality);
    if (forward === null) return { ref, quote, iv: null, ivNote: 'no forward price', greeks: null, quality };
    // a contract the feed has not spoken about has no quote, and no price: that is not a failure of a quote's quality (but it is a named state: `no_quote`)
    if (quality.state === 'withheld') return { ref, quote, iv: null, ivNote: quote === null ? 'no price' : (quality.notes[0] ?? `withheld: ${quality.withheld.map(wording).join(', ')}`), greeks: null, quality };
    const result = rowIv(quote, kind, strike, forward, T, rate);
    return {
      ref,
      quote,
      iv: result.ok ? result.iv : null,
      ivNote: result.ok ? (quality.state === 'labelled' ? `labelled: ${quality.labelled.map(wording).join(', ')}` : null) : result.reason,
      greeks: result.ok ? greeks(kind, forward, strike, T, result.iv, rate) : null,
      quality,
    };
  };
  const rows: RowView[] = structure.strikes.map((r, i) => ({
    strike: r.strike,
    atm: r.strike === atm,
    itmCall: forward !== null && r.strike < forward,
    itmPut: forward !== null && r.strike > forward,
    call: side(r.call, stats[i].call, 'call', r.strike),
    put: side(r.put, stats[i].put, 'put', r.strike),
  }));
  // the statistics that read prices use only the sides that passed; open interest and volume are not prices and use every quote
  const priced: StrikeRow[] = stats.map((r, i) => ({ strike: r.strike, call: rows[i].call?.quality.state === 'withheld' ? null : r.call, put: rows[i].put?.quality.state === 'withheld' ? null : r.put }));
  const atmRow = rows.find((r) => r.atm);
  const quality = chainQuality({
    now: nowMs, session, policy, forward: forwardDecision, atm, atmCall: atmRow?.call?.quality ?? null, atmPut: atmRow?.put?.quality ?? null, sides: decisions,
    quotes: [...stats.flatMap((r) => [r.call, r.put]), futureQuote].filter((q): q is Quote => q !== null),
  });
  return {
    underlying: structure.underlying,
    expiry: structure.expiry,
    T,
    spot,
    forward,
    forwardFrom,
    atm,
    atmIv: atmIv(priced, forward, T, rate),
    pcr: pcr(stats),
    maxPain: maxPain(stats),
    smile: smile(priced, forward, T, rate, (structure.tick ?? 0.05) * SMILE_MIN_TICKS),
    rows,
    future: structure.future ? { ref: structure.future, quote: futureQuote } : null,
    lot: structure.lot,
    quality,
  };
}

/** The rows to show: all of them, or the `count` strikes either side of the at-the-money strike (fewer at the ends of the list). */
export function around(rows: RowView[], count: number | 'all'): RowView[] {
  if (count === 'all') return rows;
  const at = rows.findIndex((r) => r.atm);
  if (at < 0) return rows.slice(0, 2 * count + 1);
  return rows.slice(Math.max(0, at - count), at + count + 1);
}
