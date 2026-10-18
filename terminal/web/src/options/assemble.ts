import type { ChainStructure, ContractRef } from './api';
import { greeks, yearsToExpiry, type Greeks } from './black76';
import { atmIv, atmStrike, forwardOf, parityForward, maxPain, pcr, quotePrice, rowIv, smile, type Pcr, type Quote, type SmilePoint, type StrikeRow } from './chain';
import { keyOf, type ContractKey } from './synthetic';

/** One chain, put together for the page: each strike's call and put with its quote, implied volatility and Greeks, and the statistics of the whole. Pure. */

export interface SideView {
  ref: ContractRef;
  quote: Quote | null;
  iv: number | null;
  /** Why there is no volatility (no price, below intrinsic value), for a tooltip. */
  ivNote: string | null;
  greeks: Greeks | null;
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
}

const refOf = (s: ChainStructure, c: { token: string; lot: number }): Pick<ContractKey, 'ref'> => (s.segment ? { ref: { segment: s.segment, token: c.token, lot: c.lot } } : {});

/** The feed keys of everything a chain needs prices for. */
export function chainKeys(s: ChainStructure): ContractKey[] {
  const keys: ContractKey[] = [];
  for (const row of s.strikes) {
    if (row.call) keys.push({ underlying: s.underlying, expiry: s.expiry, kind: 'call', strike: row.strike, ...refOf(s, row.call) });
    if (row.put) keys.push({ underlying: s.underlying, expiry: s.expiry, kind: 'put', strike: row.strike, ...refOf(s, row.put) });
  }
  if (s.future) keys.push({ underlying: s.underlying, expiry: s.expiry, kind: 'future', strike: 0, ...refOf(s, s.future) });
  return keys;
}

const quoteOf = (quotes: Map<string, Quote>, s: ChainStructure, kind: ContractKey['kind'], strike: number): Quote | null =>
  quotes.get(keyOf({ underlying: s.underlying, expiry: s.expiry, kind, strike })) ?? null;

/** A wing option must be priced at least this many ticks to count in the smile. */
const SMILE_MIN_TICKS = 10;

export function assemble(structure: ChainStructure, quotes: Map<string, Quote>, spot: number | null, nowSec: number, rate: number): ChainView {
  const T = yearsToExpiry(structure.expiry, nowSec);
  const futureQuote = structure.future ? quoteOf(quotes, structure, 'future', 0) : null;
  const futurePrice = futureQuote ? quotePrice(futureQuote) : null;
  const stats: StrikeRow[] = structure.strikes.map((r) => ({ strike: r.strike, call: r.call ? quoteOf(quotes, structure, 'call', r.strike) : null, put: r.put ? quoteOf(quotes, structure, 'put', r.strike) : null }));
  const parity = parityForward(stats, T, rate);
  const forward = forwardOf(spot, futurePrice, T, rate, parity);
  const forwardFrom = forward === null ? null : futurePrice !== null && futurePrice > 0 ? 'future' : forward === parity ? 'parity' : 'spot';
  const atm = atmStrike(stats, forward);
  const side = (ref: ContractRef | null, quote: Quote | null, kind: 'call' | 'put', strike: number): SideView | null => {
    if (!ref) return null;
    if (forward === null) return { ref, quote, iv: null, ivNote: 'no forward price', greeks: null };
    const result = rowIv(quote, kind, strike, forward, T, rate);
    return {
      ref,
      quote,
      iv: result.ok ? result.iv : null,
      ivNote: result.ok ? null : result.reason,
      greeks: result.ok ? greeks(kind, forward, strike, T, result.iv, rate) : null,
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
  return {
    underlying: structure.underlying,
    expiry: structure.expiry,
    T,
    spot,
    forward,
    forwardFrom,
    atm,
    atmIv: atmIv(stats, forward, T, rate),
    pcr: pcr(stats),
    maxPain: maxPain(stats),
    smile: smile(stats, forward, T, rate, (structure.tick ?? 0.05) * SMILE_MIN_TICKS),
    rows,
    future: structure.future ? { ref: structure.future, quote: futureQuote } : null,
    lot: structure.lot,
  };
}

/** The rows to show: all of them, or the `count` strikes either side of the at-the-money strike (fewer at the ends of the list). */
export function around(rows: RowView[], count: number | 'all'): RowView[] {
  if (count === 'all') return rows;
  const at = rows.findIndex((r) => r.atm);
  if (at < 0) return rows.slice(0, 2 * count + 1);
  return rows.slice(Math.max(0, at - count), at + count + 1);
}
