/** Black-76 prices, implied volatility and Greeks for a European option on a forward `F`, pure functions only. A stock or index option here is priced on the
 *  forward of its expiry (the future when it trades, else spot grown at the interest rate); discounting uses the same rate. Volatility is a fraction (0.18 is 18%). */

export type Kind = 'call' | 'put';

export const DEFAULT_RATE = 0.07;
const DAY = 86_400;
const YEAR = 365 * DAY;
/** An option with less than a minute left is priced as if it had a minute: the maths needs time to be above zero. */
export const MIN_SECONDS = 60;
const EXPIRY_UTC_SECONDS = 10 * 3600; // 15:30 IST is 10:00 UTC

const SQRT_2PI = Math.sqrt(2 * Math.PI);

/** The standard normal density. */
export const pdf = (x: number): number => Math.exp(-0.5 * x * x) / SQRT_2PI;

/** The standard normal distribution function, to about 1e-15 (Hart's algorithm as Graeme West gives it for double precision). */
export function cdf(x: number): number {
  const a = Math.abs(x);
  let c: number;
  if (a > 37) c = 0;
  else {
    const e = Math.exp((-a * a) / 2);
    if (a < 7.07106781186547) {
      let b = 3.52624965998911e-2 * a + 0.700383064443688;
      b = b * a + 6.37396220353165;
      b = b * a + 33.912866078383;
      b = b * a + 112.079291497871;
      b = b * a + 221.213596169931;
      b = b * a + 220.206867912376;
      c = e * b;
      let d = 8.83883476483184e-2 * a + 1.75566716318264;
      d = d * a + 16.064177579207;
      d = d * a + 86.7807322029461;
      d = d * a + 296.564248779674;
      d = d * a + 637.333633378831;
      d = d * a + 793.826512519948;
      d = d * a + 440.413735824752;
      c /= d;
    } else {
      let b = a + 0.65;
      b = a + 4 / b;
      b = a + 3 / b;
      b = a + 2 / b;
      b = a + 1 / b;
      c = e / b / 2.506628274631;
    }
  }
  return x > 0 ? 1 - c : c;
}

/** Years from `nowSec` to 15:30 IST on the expiry date (`YYYY-MM-DD`), of 365 days, never less than a minute. NaN for a date that is not one. */
export function yearsToExpiry(expiry: string, nowSec: number): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(expiry);
  if (!m) return Number.NaN;
  const end = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / 1000 + EXPIRY_UTC_SECONDS;
  return Math.max(MIN_SECONDS, end - nowSec) / YEAR;
}

function d1d2(F: number, K: number, T: number, sigma: number): [number, number] {
  const s = sigma * Math.sqrt(T);
  const d1 = (Math.log(F / K) + 0.5 * s * s) / s;
  return [d1, d1 - s];
}

/** The option's price. A forward, strike, time or volatility that is not above zero is a price of intrinsic value (discounted), which is what the limit is. */
export function price(kind: Kind, F: number, K: number, T: number, sigma: number, r = DEFAULT_RATE): number {
  const df = Math.exp(-r * T);
  if (!(F > 0) || !(K > 0) || !(T > 0) || !(sigma > 0)) return df * Math.max(0, kind === 'call' ? F - K : K - F);
  const [d1, d2] = d1d2(F, K, T, sigma);
  return kind === 'call' ? df * (F * cdf(d1) - K * cdf(d2)) : df * (K * cdf(-d2) - F * cdf(-d1));
}

export interface Greeks {
  price: number;
  /** Change in price for a move of 1 in the forward. */
  delta: number;
  /** Change in delta for a move of 1 in the forward. */
  gamma: number;
  /** Change in price for 1 volatility point (0.01). */
  vega: number;
  /** Change in price for one calendar day passing. */
  theta: number;
  /** Change in price for 1 percentage point in the interest rate. */
  rho: number;
}

export function greeks(kind: Kind, F: number, K: number, T: number, sigma: number, r = DEFAULT_RATE): Greeks {
  const df = Math.exp(-r * T);
  const p = price(kind, F, K, T, sigma, r);
  if (!(F > 0) || !(K > 0) || !(T > 0) || !(sigma > 0)) {
    const inTheMoney = kind === 'call' ? F > K : F < K;
    return { price: p, delta: inTheMoney ? (kind === 'call' ? df : -df) : 0, gamma: 0, vega: 0, theta: 0, rho: -T * p * 0.01 };
  }
  const [d1] = d1d2(F, K, T, sigma);
  const root = Math.sqrt(T);
  const density = pdf(d1);
  return {
    price: p,
    delta: kind === 'call' ? df * cdf(d1) : -df * cdf(-d1),
    gamma: (df * density) / (F * sigma * root),
    vega: (df * F * density * root) / 100,
    theta: (r * p - (df * F * density * sigma) / (2 * root)) / 365,
    rho: (-T * p) / 100,
  };
}

export type IvResult = { ok: true; iv: number } | { ok: false; reason: string };

const IV_LOW = 1e-4;
const IV_HIGH = 5; // 500%

/** The volatility at which Black-76 gives `target`. Refuses with a reason when no volatility can: a price below the option's discounted intrinsic value, at or
 *  above the most it can be worth, or not a number. */
export function impliedVol(kind: Kind, target: number, F: number, K: number, T: number, r = DEFAULT_RATE): IvResult {
  if (![target, F, K, T].every(Number.isFinite) || !(F > 0) || !(K > 0) || !(T > 0)) return { ok: false, reason: 'the inputs are not usable' };
  const df = Math.exp(-r * T);
  const intrinsic = df * Math.max(0, kind === 'call' ? F - K : K - F);
  const bound = df * (kind === 'call' ? F : K);
  if (!(target > 0)) return { ok: false, reason: 'the price is not above zero' };
  if (target < intrinsic - 1e-9 * bound) return { ok: false, reason: 'the price is below the option\'s intrinsic value' };
  if (target >= bound) return { ok: false, reason: 'the price is at or above the most the option can be worth' };
  let lo = IV_LOW;
  let hi = IV_HIGH;
  if (price(kind, F, K, T, lo, r) > target) return { ok: false, reason: 'the price is too small to have a volatility above 0.01%' };
  if (price(kind, F, K, T, hi, r) < target) return { ok: false, reason: 'the volatility would be above 500%' };
  // Newton from the Brenner-Subrahmanyam guess, kept inside the bracket; bisection when a step leaves it
  let sigma = Math.min(hi, Math.max(lo, Math.sqrt((2 * Math.PI) / T) * (target / (df * F))));
  for (let i = 0; i < 100; i++) {
    const p = price(kind, F, K, T, sigma, r);
    const diff = p - target;
    if (Math.abs(diff) < 1e-10 * Math.max(1, target)) return { ok: true, iv: sigma };
    if (diff > 0) hi = sigma;
    else lo = sigma;
    const vega = greeks(kind, F, K, T, sigma, r).vega * 100;
    let next = vega > 1e-12 ? sigma - diff / vega : NaN;
    if (!(next > lo && next < hi)) next = 0.5 * (lo + hi);
    sigma = next;
  }
  return { ok: true, iv: sigma };
}
