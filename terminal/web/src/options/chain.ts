import { impliedVol, type IvResult } from './black76';

/** Statistics of an option chain: the forward, the at-the-money strike, put-call ratio, max pain, the volatility smile and the at-the-money volatility. Pure. */

export interface Quote {
  last: number | null;
  bid: number | null;
  ask: number | null;
  volume: number;
  /** Open interest in contracts, and its change since the previous day's close. */
  oi: number;
  oiChange: number;
}

export interface StrikeRow {
  strike: number;
  call: Quote | null;
  put: Quote | null;
}

/** The forward of an expiry: its future when it trades, else spot grown at the rate to the expiry. Null when there is neither. */
export function forwardOf(spot: number | null, future: number | null, T: number, r: number): number | null {
  if (future !== null && future > 0) return future;
  if (spot !== null && spot > 0 && T > 0) return spot * Math.exp(r * T);
  return null;
}

/** The strike nearest the forward; the lower one when two are equally near. Null for no strikes or no forward. */
export function atmStrike(rows: StrikeRow[], forward: number | null): number | null {
  if (!rows.length || forward === null) return null;
  let best = rows[0].strike;
  for (const row of rows) {
    const d = Math.abs(row.strike - forward);
    const b = Math.abs(best - forward);
    if (d < b || (d === b && row.strike < best)) best = row.strike;
  }
  return best;
}

export interface Pcr {
  /** Put open interest over call open interest; null when the calls have none. */
  oi: number | null;
  volume: number | null;
  callOi: number;
  putOi: number;
}

export function pcr(rows: StrikeRow[]): Pcr {
  let callOi = 0;
  let putOi = 0;
  let callVol = 0;
  let putVol = 0;
  for (const r of rows) {
    callOi += r.call?.oi ?? 0;
    putOi += r.put?.oi ?? 0;
    callVol += r.call?.volume ?? 0;
    putVol += r.put?.volume ?? 0;
  }
  return { oi: callOi > 0 ? putOi / callOi : null, volume: callVol > 0 ? putVol / callVol : null, callOi, putOi };
}

/** The strike at which the writers of all the options would pay out least if the underlying expired there (the lowest such strike when two are equal). Null for a
 *  chain with no open interest. */
export function maxPain(rows: StrikeRow[]): number | null {
  if (!rows.some((r) => (r.call?.oi ?? 0) > 0 || (r.put?.oi ?? 0) > 0)) return null;
  let best: number | null = null;
  let least = Infinity;
  for (const at of [...rows].sort((a, b) => a.strike - b.strike)) {
    let pain = 0;
    for (const r of rows) {
      pain += (r.call?.oi ?? 0) * Math.max(0, at.strike - r.strike);
      pain += (r.put?.oi ?? 0) * Math.max(0, r.strike - at.strike);
    }
    if (pain < least) {
      least = pain;
      best = at.strike;
    }
  }
  return best;
}

/** The price to read an implied volatility from: the middle of a two-sided market, else the last trade; null when there is neither. */
export function quotePrice(q: Quote | null): number | null {
  if (!q) return null;
  if (q.bid !== null && q.ask !== null && q.bid > 0 && q.ask >= q.bid) return (q.bid + q.ask) / 2;
  return q.last !== null && q.last > 0 ? q.last : null;
}

export function rowIv(q: Quote | null, kind: 'call' | 'put', strike: number, forward: number, T: number, r: number): IvResult {
  const p = quotePrice(q);
  if (p === null) return { ok: false, reason: 'no price' };
  return impliedVol(kind, p, forward, strike, T, r);
}

export interface SmilePoint {
  strike: number;
  iv: number;
  /** Which option the volatility came from: calls above the forward, puts below. */
  side: 'call' | 'put';
}

/** Implied volatility by strike from the out-of-the-money options, calls at and above the forward and puts below it, the usual smile. A strike whose option has no
 *  price or no volatility is left out. */
export function smile(rows: StrikeRow[], forward: number | null, T: number, r: number): SmilePoint[] {
  if (forward === null) return [];
  const out: SmilePoint[] = [];
  for (const row of [...rows].sort((a, b) => a.strike - b.strike)) {
    const side = row.strike >= forward ? 'call' : 'put';
    const iv = rowIv(side === 'call' ? row.call : row.put, side, row.strike, forward, T, r);
    if (iv.ok) out.push({ strike: row.strike, iv: iv.iv, side });
  }
  return out;
}

/** The at-the-money volatility: the average of the call's and the put's at the at-the-money strike (whichever has one when only one does); null when neither. */
export function atmIv(rows: StrikeRow[], forward: number | null, T: number, r: number): number | null {
  const strike = atmStrike(rows, forward);
  if (strike === null || forward === null) return null;
  const row = rows.find((x) => x.strike === strike);
  if (!row) return null;
  const values: number[] = [];
  const c = rowIv(row.call, 'call', strike, forward, T, r);
  const p = rowIv(row.put, 'put', strike, forward, T, r);
  if (c.ok) values.push(c.iv);
  if (p.ok) values.push(p.iv);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

export interface TermPoint {
  expiry: string;
  T: number;
  iv: number;
}

/** The at-the-money volatility of each expiry, in order of expiry; an expiry with none is left out. */
export function termStructure(expiries: { expiry: string; T: number; forward: number | null; rows: StrikeRow[] }[], r: number): TermPoint[] {
  const out: TermPoint[] = [];
  for (const e of [...expiries].sort((a, b) => a.T - b.T)) {
    const iv = atmIv(e.rows, e.forward, e.T, r);
    if (iv !== null) out.push({ expiry: e.expiry, T: e.T, iv });
  }
  return out;
}
