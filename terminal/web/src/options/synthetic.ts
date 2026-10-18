import { DEFAULT_RATE, price, yearsToExpiry } from './black76';
import type { Quote } from './chain';

/** A synthetic option market for the fake feed and tests: internally consistent prices (Black-76 at a smile, so put-call parity holds and the volatilities read
 *  back from the prices are the smile), open interest and volume shaped like a real chain, and the same numbers for the same contract on the same day. It makes
 *  no claim about any real market; the page labels it whenever it is in use. */

export interface ContractKey {
  underlying: string;
  /** `YYYY-MM-DD`. */
  expiry: string;
  kind: 'call' | 'put' | 'future';
  /** Ignored for a future. */
  strike: number;
  /** Where the live stream knows the contract from (the list's segment and token); the synthetic feed ignores it. */
  ref?: { segment: string; token: string; lot: number };
}

export const keyOf = (c: ContractKey): string => `${c.underlying}|${c.expiry}|${c.kind}|${c.kind === 'future' ? 0 : c.strike}`;

/** What the page reads prices and open interest through. A live implementation will read the broker's stream; this file has the synthetic one. */
export interface OptionFeed {
  /** True for a made-up market: the page says so. */
  readonly synthetic: boolean;
  spot(underlying: string): Promise<number | null>;
  quotes(keys: ContractKey[]): Promise<Map<string, Quote>>;
  /** What the page shows beside the prices instead of the default label, when the feed has something more exact to say. */
  badge?(): { text: string; title: string };
}

/** A number in [0, 1) that depends only on the text (FNV-1a), so a contract gets the same "random" numbers every time. */
export function unit(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h / 0x100000000;
}

const BASES: Record<string, number> = { NIFTY: 24_500, BANKNIFTY: 52_000, FINNIFTY: 23_500, MIDCPNIFTY: 12_000, SENSEX: 80_500, BANKEX: 60_500, GOLD: 165_000, SILVER: 285_000, CRUDEOIL: 6_500, NATURALGAS: 280 };

/** The synthetic spot of an underlying on a day (days since 1970): a known base for the index and commodity names, else a price made from the name, moved a little each day. */
export function syntheticSpot(underlying: string, day: number): number {
  const base = BASES[underlying] ?? 100 + Math.floor(unit(underlying) * 4900);
  const wobble = 1 + 0.02 * Math.sin(day / 3 + unit(`${underlying}wobble`) * 6.28);
  return Math.round(base * wobble * 20) / 20;
}

export interface SyntheticMarket {
  spot: number;
  rate: number;
  /** Unix seconds now. */
  now: number;
}

const TICK = 0.05;
const round = (x: number): number => Math.max(TICK, Math.round(x / TICK) * TICK);

/** The synthetic volatility smile: a level that falls with time to expiry, a skew that makes lower strikes dearer and a curve that makes both wings dearer. */
export function smileVol(F: number, K: number, T: number): number {
  const x = Math.log(K / F);
  return 0.13 + 0.03 * Math.exp(-4 * T) + 1.2 * x * x - 0.05 * x;
}

/** One contract's synthetic quote. */
export type SyntheticQuote = Quote & { last: number };

export function syntheticQuote(c: ContractKey, m: SyntheticMarket): SyntheticQuote {
  const T = yearsToExpiry(c.expiry, m.now);
  const F = m.spot * Math.exp(m.rate * T);
  const day = Math.floor(m.now / 86_400);
  const k = keyOf(c);
  const noise = unit(`${k}|${day}`);
  if (c.kind === 'future') {
    const last = round(F);
    return { last, bid: round(last - TICK), ask: round(last + TICK), volume: Math.round(200_000 * (0.5 + noise)), oi: Math.round(400_000 * (0.6 + 0.8 * unit(`${k}|oi`))), oiChange: Math.round((noise - 0.5) * 40_000) };
  }
  const vol = smileVol(F, c.strike, T);
  const fair = price(c.kind, F, c.strike, T, vol, m.rate);
  const last = round(fair);
  const spread = Math.max(TICK, round(last * 0.004));
  const x = Math.log(c.strike / F);
  const nearMoney = Math.exp(-((x / 0.06) ** 2)); // interest and trading cluster around the forward
  const side = (c.kind === 'call') === (x > 0) ? 1.25 : 0.8; // calls above and puts below the money are the heavier
  const oi = Math.round(250_000 * nearMoney * side * (0.7 + 0.6 * unit(`${k}|oi`))) + Math.round(1_000 * unit(`${k}|floor`));
  return {
    last,
    bid: last > 2 * TICK ? round(last - spread) : null,
    ask: round(last + spread),
    volume: Math.round(oi * (0.15 + 0.5 * noise)),
    oi,
    oiChange: Math.round(oi * (noise - 0.4) * 0.2),
  };
}

/** The synthetic feed: the spot of any underlying and a quote for any contract, on the clock it is given. */
export function syntheticFeed(now: () => number = () => Date.now() / 1000, rate = DEFAULT_RATE): OptionFeed {
  return {
    synthetic: true,
    async spot(underlying) {
      return syntheticSpot(underlying, Math.floor(now() / 86_400));
    },
    async quotes(keys) {
      const out = new Map<string, Quote>();
      const t = now();
      for (const c of keys) out.set(keyOf(c), syntheticQuote(c, { spot: syntheticSpot(c.underlying, Math.floor(t / 86_400)), rate, now: t }));
      return out;
    },
  };
}
