import { greeks, price, type Kind } from './black76';

/** The payoff builder's maths: a position of option and future legs, its profit and loss at expiry and now, break-evens, the most it can make and lose, and its net
 *  Greeks. Pure. Quantities are in units of the underlying (lots times lot size); `side` is +1 to buy and -1 to sell. */

export type LegKind = 'call' | 'put' | 'future';

export interface Leg {
  kind: LegKind;
  side: 1 | -1;
  qty: number;
  /** Not used by a future. */
  strike: number;
  /** What was paid or received per unit: the premium of an option, the entry price of a future. */
  price: number;
}

const intrinsic = (leg: Leg, S: number): number => (leg.kind === 'call' ? Math.max(0, S - leg.strike) : leg.kind === 'put' ? Math.max(0, leg.strike - S) : S);

/** The profit or loss of the position if the underlying is `S` at expiry, before costs. */
export function payoffAtExpiry(legs: Leg[], S: number): number {
  return legs.reduce((sum, l) => sum + l.side * l.qty * (intrinsic(l, S) - l.price), 0);
}

/** What each leg is worth per unit before expiry: Black-76 for an option at its volatility and time left; a future is the underlying itself. */
export interface Market {
  /** Years left for each leg, by index (a position can span expiries). */
  years: number[];
  /** Implied volatility of each leg, by index (a fraction). */
  vols: number[];
  rate: number;
}

/** The profit or loss of the position if the forward is `S` after `daysForward` days, at volatilities shifted by `volShift` (a fraction: 0.02 is two points). A leg
 *  whose time has run out is worth its intrinsic value. */
export function payoffNow(legs: Leg[], S: number, market: Market, daysForward = 0, volShift = 0): number {
  return legs.reduce((sum, l, i) => {
    if (l.kind === 'future') return sum + l.side * l.qty * (S - l.price);
    const T = Math.max(0, (market.years[i] ?? 0) - daysForward / 365);
    const vol = Math.max(1e-4, (market.vols[i] ?? 0.2) + volShift);
    const value = T > 0 ? price(l.kind as Kind, S, l.strike, T, vol, market.rate) : intrinsic(l, S);
    return sum + l.side * l.qty * (value - l.price);
  }, 0);
}

/** The position's premium: positive is paid out (a debit), negative received (a credit). Futures have no premium. */
export function netDebit(legs: Leg[]): number {
  return legs.filter((l) => l.kind !== 'future').reduce((sum, l) => sum + l.side * l.qty * l.price, 0);
}

/** Net delta, gamma, vega, theta and rho of the position, per unit of the underlying held (a future has a delta of its side and quantity only). */
export function netGreeks(legs: Leg[], F: number, market: Market, daysForward = 0): { delta: number; gamma: number; vega: number; theta: number; rho: number } {
  const total = { delta: 0, gamma: 0, vega: 0, theta: 0, rho: 0 };
  legs.forEach((l, i) => {
    if (l.kind === 'future') {
      total.delta += l.side * l.qty;
      return;
    }
    const T = Math.max(1e-9, (market.years[i] ?? 0) - daysForward / 365);
    const g = greeks(l.kind as Kind, F, l.strike, T, market.vols[i] ?? 0.2, market.rate);
    const w = l.side * l.qty;
    total.delta += w * g.delta;
    total.gamma += w * g.gamma;
    total.vega += w * g.vega;
    total.theta += w * g.theta;
    total.rho += w * g.rho;
  });
  return total;
}

/** The prices at which the profit or loss at expiry is zero, lowest first. The payoff is straight between strikes, so each is found exactly; one beyond the highest
 *  strike is found from the slope there. The underlying cannot go below zero. */
export function breakEvens(legs: Leg[]): number[] {
  if (!legs.length) return [];
  const strikes = [...new Set(legs.filter((l) => l.kind !== 'future' && l.strike > 0).map((l) => l.strike))].sort((a, b) => a - b);
  const top = (strikes[strikes.length - 1] ?? 0) + 1;
  const xs = [0, ...strikes, top];
  const ys = xs.map((x) => payoffAtExpiry(legs, x));
  const found: number[] = [];
  const zero = (i: number): boolean => Math.abs(ys[i]) < 1e-9;
  for (let i = 0; i < xs.length; i++) {
    // a node that is exactly zero is a break-even; of a run of zero nodes (a payoff that is flat at zero) the two ends are
    if (zero(i) && (i === 0 || !zero(i - 1) || (i + 1 < xs.length && !zero(i + 1)))) found.push(xs[i]);
    if (i + 1 < xs.length && ys[i] * ys[i + 1] < 0) found.push(xs[i] + (-ys[i] * (xs[i + 1] - xs[i])) / (ys[i + 1] - ys[i]));
  }
  // beyond the top strike the payoff keeps the slope it has there, and may cross zero on the way
  const slope = slopeAbove(legs);
  const yTop = ys[ys.length - 1];
  if (slope !== 0 && Math.abs(yTop) >= 1e-9 && Math.sign(yTop) !== Math.sign(slope)) found.push(top - yTop / slope);
  return [...new Set(found.map((v) => Math.round(v * 1e9) / 1e9))].sort((a, b) => a - b);
}

/** The slope of the payoff as the price rises without limit: units long less units short in calls and futures. */
export function slopeAbove(legs: Leg[]): number {
  return legs.reduce((sum, l) => (l.kind === 'put' ? sum : sum + l.side * l.qty), 0);
}

export type Extreme = number | 'unlimited';

/** The most the position can make and the most it can lose at expiry (a loss is a negative number). A price that rises without limit can make or cost without
 *  limit; one that falls stops at zero. */
export function extremes(legs: Leg[]): { maxProfit: Extreme; maxLoss: Extreme } {
  if (!legs.length) return { maxProfit: 0, maxLoss: 0 };
  const strikes = legs.filter((l) => l.kind !== 'future').map((l) => l.strike);
  const points = [0, ...strikes];
  const values = points.map((p) => payoffAtExpiry(legs, p));
  const slope = slopeAbove(legs);
  const best = Math.max(...values);
  const worst = Math.min(...values);
  return {
    maxProfit: slope > 0 ? 'unlimited' : Math.max(best, payoffAtExpiry(legs, Math.max(...points) + 1)),
    maxLoss: slope < 0 ? 'unlimited' : Math.min(worst, payoffAtExpiry(legs, Math.max(...points) + 1)),
  };
}

/** The payoff curve for a chart: `count` evenly spaced prices from `low` to `high`, each with the profit or loss at expiry. */
export function curve(legs: Leg[], low: number, high: number, count = 120): { s: number; value: number }[] {
  return Array.from({ length: count }, (_, i) => {
    const s = low + ((high - low) * i) / (count - 1);
    return { s, value: payoffAtExpiry(legs, s) };
  });
}
