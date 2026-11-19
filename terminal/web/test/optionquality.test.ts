import { describe, expect, it } from 'vitest';
import type { ChainStructure } from '../src/options/api';
import { price, yearsToExpiry } from '../src/options/black76';
import { assemble } from '../src/options/assemble';
import type { Quote } from '../src/options/chain';
import { assessQuote, decideForward, POLICY_V3, type AssessContext, type ForwardDecision, type QualityPolicy, type SessionState } from '../src/options/quality';
import { keyOf } from '../src/options/synthetic';

/** T3: a versioned quote-quality policy, judged per contract from three separate times (when the server received the quote, when its book last changed, when it last traded), with controlled clocks.
 *  Fixtures only: nothing here says anything about a real market. */

const MIN = 60_000;
const NOW = Date.UTC(2026, 9, 27, 11, 0) - 19_800_000; // ms
const live: SessionState = { state: 'live', referenceMs: NOW };

const quote = (o: Partial<Quote> = {}): Quote => ({ last: 100, bid: 99.5, ask: 100.5, volume: 10, oi: 100, oiChange: 0, time: NOW - 1_000, receivedAt: NOW - 1_000, bookSeenAt: NOW - 1_000, ...o });
const ctx = (o: Partial<AssessContext> = {}): AssessContext => ({ now: NOW, session: live, forward: null, ...o });
const fwd = (o: Partial<ForwardDecision> = {}): ForwardDecision => ({
  source: 'future', value: 24_200, at: NOW - 1_000, state: 'ok', flags: [], rejected: [], policy: { id: POLICY_V3.id, version: POLICY_V3.version }, inputs: [], inputFlags: [], spanMs: null, candidates: [], excluded: [], ...o,
});

describe('one contract: its book and its last trade are judged separately', () => {
  it('a fresh two-sided book with a recent trade is ok, read from the middle, and records its inputs and the policy', () => {
    const d = assessQuote(quote(), ctx());
    expect(d).toMatchObject({ state: 'ok', flags: [], priceBasis: 'mid', book: 'fresh', trade: 'recent', policy: { id: 'option_quote_quality', version: 4 } }); // (T3-C1 made it 3; 4 is the out-of-order window)
    expect(d.inputs).toMatchObject({ now: NOW, ageBase: NOW, receivedAt: NOW - 1_000, bookSeenAt: NOW - 1_000, tradeTime: NOW - 1_000, bookTimeBasis: 'book_seen', bookAgeMs: 1_000, tradeAgeMs: 1_000, bid: 99.5, ask: 100.5 });
  });

  it('an old trade beside a fresh book does not make the book stale: the volatility is read from the book', () => {
    const d = assessQuote(quote({ time: NOW - 3 * 60 * MIN }), ctx());
    expect(d.trade).toBe('old');
    expect(d).toMatchObject({ state: 'ok', flags: [], priceBasis: 'mid', book: 'fresh' });
    expect(d.inputs.tradeAgeMs).toBe(3 * 60 * MIN);
  });

  it('a recent trade beside a stale book does not make the book fresh: the volatility is withheld', () => {
    const d = assessQuote(quote({ bookSeenAt: NOW - 20 * MIN, receivedAt: NOW - 1_000, time: NOW - 1_000 }), ctx());
    expect(d.trade).toBe('recent');
    expect(d).toMatchObject({ state: 'withheld', flags: ['stale_book'], book: 'stale' });
    expect(d.inputs.bookAgeMs).toBe(20 * MIN);
  });

  it('the book age is judged at the limit: equal is fresh, one millisecond more is stale', () => {
    expect(assessQuote(quote({ bookSeenAt: NOW - POLICY_V3.maxBookAgeMs }), ctx()).state).toBe('ok');
    expect(assessQuote(quote({ bookSeenAt: NOW - POLICY_V3.maxBookAgeMs - 1 }), ctx()).flags).toEqual(['stale_book']);
  });

  it('the exchange\'s own book time is used when a source gives one, and the basis is recorded', () => {
    const d = assessQuote(quote({ bookTime: NOW - 30 * MIN, bookSeenAt: NOW - 1_000 }), ctx());
    expect(d.flags).toEqual(['stale_book']);
    expect(d.inputs.bookTimeBasis).toBe('book_time');
    expect(d.inputs.bookAgeMs).toBe(30 * MIN);
  });

  it('a crossed book is withheld; a wide one is labelled; neither is read as a price', () => {
    expect(assessQuote(quote({ bid: 101, ask: 100 }), ctx())).toMatchObject({ state: 'withheld', flags: ['crossed_book'], book: 'crossed' });
    const wide = assessQuote(quote({ bid: 50, ask: 150 }), ctx());
    expect(wide).toMatchObject({ state: 'labelled', flags: ['wide_book'], book: 'wide' });
    expect(wide.inputs.spreadPct).toBeCloseTo(1, 6);
    expect(assessQuote(quote({ bid: 90, ask: 90 }), ctx()).state, 'a locked book is not crossed').toBe('ok');
  });

  it('a book with no bid or no ask is not two-sided: the volatility is withheld, and a last trade is not used in its place', () => {
    const d = assessQuote(quote({ bid: null }), ctx());
    expect(d).toMatchObject({ state: 'withheld', flags: ['no_two_sided_book'], book: 'none' });
    expect(assessQuote(quote({ bid: 0, ask: 100.5 }), ctx()).flags).toEqual(['no_two_sided_book']);
  });

  it('where the policy does not require a book, the last trade is the price and an old one is withheld', () => {
    const lax: QualityPolicy = { ...POLICY_V3, requireTwoSidedBook: false };
    expect(assessQuote(quote({ bid: null, time: NOW - 1_000 }), ctx({ policy: lax }))).toMatchObject({ state: 'ok', priceBasis: 'last' });
    expect(assessQuote(quote({ bid: null, time: NOW - 20 * MIN }), ctx({ policy: lax }))).toMatchObject({ state: 'withheld', flags: ['old_trade_price'], priceBasis: 'last' });
  });

  it('a delayed trade is labelled, not withheld; a discarded out-of-order packet is a note (policy 4): recorded, the contract still fit', () => {
    expect(assessQuote(quote({ deliveryDelayMs: 60_000 }), ctx())).toMatchObject({ state: 'labelled', flags: ['delayed_feed'] });
    expect(assessQuote(quote({ deliveryDelayMs: POLICY_V3.maxDeliveryDelayMs }), ctx()).state, 'at the limit').toBe('ok');
    expect(POLICY_V3.consequences.out_of_order_discarded).toBe('note');
    expect(assessQuote(quote({ outOfOrder: 2 }), ctx())).toMatchObject({ state: 'ok', flags: ['out_of_order_discarded'], labelled: [], withheld: [] });
  });

  it('(version 4) a discarded out-of-order packet labels the contract only while the discard is recent; with no discard time it is labelled', () => {
    expect(POLICY_V3.outOfOrderWindowMs).toBe(60_000);
    expect(assessQuote(quote({ outOfOrder: 2, outOfOrderAt: NOW - POLICY_V3.outOfOrderWindowMs }), ctx()).flags, 'at the limit').toEqual(['out_of_order_discarded']);
    expect(assessQuote(quote({ outOfOrder: 2, outOfOrderAt: NOW - POLICY_V3.outOfOrderWindowMs - 1 }), ctx()).state, 'an old discard').toBe('ok');
    expect(assessQuote(quote({ outOfOrder: 2 }), ctx()).flags, 'age unknown').toEqual(['out_of_order_discarded']);
    expect(assessQuote(quote({ outOfOrder: 0, outOfOrderAt: NOW }), ctx()).state, 'no discard').toBe('ok');
  });

  it('a quote with no book time at all (an older server, a test feed) is labelled, never judged fresh, and its trade time is not used for the book', () => {
    const d = assessQuote({ last: 100, bid: 99.5, ask: 100.5, volume: 1, oi: 1, oiChange: 0, time: NOW - 1_000 }, ctx());
    expect(d).toMatchObject({ state: 'labelled', flags: ['book_time_unknown'], book: 'unknown' });
    expect(d.inputs.bookTimeBasis).toBe('none');
    expect(d.inputs.bookAgeMs).toBeNull();
  });

  it('after the session ages are measured from the newest quote seen, every quote is an observation, and a quote older than that is still stale', () => {
    const session: SessionState = { state: 'last_session', referenceMs: NOW - 5 * 60 * MIN };
    const fresh = assessQuote(quote({ bookSeenAt: NOW - 5 * 60 * MIN - 1_000, time: NOW - 5 * 60 * MIN - 1_000 }), ctx({ session }));
    expect(fresh).toMatchObject({ state: 'labelled', flags: ['session_observation'], book: 'fresh' });
    expect(fresh.inputs.ageBase).toBe(NOW - 5 * 60 * MIN);
    const wing = assessQuote(quote({ bookSeenAt: NOW - 5 * 60 * MIN - 30 * MIN }), ctx({ session }));
    expect(wing.flags).toEqual(expect.arrayContaining(['stale_book', 'session_observation']));
    expect(wing.state).toBe('withheld');
  });

  it('the declared consequence is the only difference between withholding and labelling', () => {
    const stale = quote({ bookSeenAt: NOW - 20 * MIN });
    const wide = quote({ bid: 50, ask: 150 });
    const asLabel: QualityPolicy = { ...POLICY_V3, consequences: { ...POLICY_V3.consequences, stale_book: 'label' } };
    const asWithhold: QualityPolicy = { ...POLICY_V3, consequences: { ...POLICY_V3.consequences, wide_book: 'withhold' } };
    expect(assessQuote(stale, ctx({ policy: asLabel }))).toMatchObject({ state: 'labelled', flags: ['stale_book'], labelled: ['stale_book'], withheld: [] });
    expect(assessQuote(wide, ctx({ policy: asWithhold }))).toMatchObject({ state: 'withheld', flags: ['wide_book'], withheld: ['wide_book'] });
  });

  it('the forward and the option must be close in time: a forward older than the option is withheld, at the limit it is not', () => {
    const old = fwd({ at: NOW - 10 * MIN });
    const d = assessQuote(quote({ bookSeenAt: NOW - 1_000 }), ctx({ forward: old }));
    expect(d).toMatchObject({ state: 'withheld', flags: ['forward_unsynced'] });
    expect(d.inputs).toMatchObject({ forwardAt: NOW - 10 * MIN, syncGapMs: 10 * MIN - 1_000 });
    expect(assessQuote(quote({ bookSeenAt: NOW - 1_000 }), ctx({ forward: fwd({ at: NOW - 1_000 - POLICY_V3.maxSyncGapMs }) })).state).toBe('ok');
    expect(assessQuote(quote({ bookSeenAt: NOW - 1_000 }), ctx({ forward: fwd({ at: NOW - 1_001 - POLICY_V3.maxSyncGapMs }) })).flags).toEqual(['forward_unsynced']);
    // an option older than the forward is out of step the other way round
    expect(assessQuote(quote({ bookSeenAt: NOW - 8 * MIN, time: NOW - 8 * MIN }), ctx({ forward: fwd({ at: NOW - 1_000 }) })).flags).toEqual(['forward_unsynced']);
  });

  it('a forward whose own decision is withheld or from spot passes its flag to the option', () => {
    expect(assessQuote(quote(), ctx({ forward: fwd({ source: null, value: null, at: null, state: 'withheld', flags: ['no_forward'] }) }))).toMatchObject({ state: 'withheld', flags: ['no_forward'] });
    expect(assessQuote(quote(), ctx({ forward: fwd({ source: 'spot', at: null, state: 'labelled', flags: ['forward_age_unknown'] }) }))).toMatchObject({ state: 'labelled', flags: ['forward_age_unknown'] });
  });

  it('the forward\'s time is the oldest of its inputs, spot has none and is labelled, a stale input makes it stale, and no forward is withheld', () => {
    const base = { now: NOW, session: live, policy: POLICY_V3, rejected: [] };
    const a = quote({ bookSeenAt: NOW - 2 * MIN });
    const b = quote({ bookSeenAt: NOW - 5 * MIN }); // (review R4: 2 and 9 minutes were each under the age limit but 7 minutes apart: no longer in step; the inputs must be within 5 minutes of each other)
    expect(decideForward({ ...base, source: 'parity', value: 24_200, used: [a, b] })).toMatchObject({ source: 'parity', at: NOW - 5 * MIN, state: 'ok', flags: [] });
    expect(decideForward({ ...base, source: 'future', value: 24_200, used: [a, quote({ bookSeenAt: NOW - 16 * MIN })] })).toMatchObject({ at: NOW - 16 * MIN, state: 'withheld', flags: ['forward_stale', 'forward_unsynced'] });
    expect(decideForward({ ...base, source: 'future', value: 24_200, used: [a, { ...b, bookSeenAt: undefined }] })).toMatchObject({ at: null, state: 'labelled', flags: ['forward_age_unknown'] });
    expect(decideForward({ ...base, source: 'spot', value: 24_200, used: [] })).toMatchObject({ at: null, state: 'labelled', flags: ['forward_age_unknown'] });
    expect(decideForward({ ...base, source: null, value: null, used: [] })).toMatchObject({ state: 'withheld', flags: ['no_forward'] });
    const after = { state: 'last_session' as const, referenceMs: NOW - 60 * MIN };
    expect(decideForward({ ...base, source: 'future', value: 24_200, used: [quote({ bookSeenAt: NOW - 76 * MIN })], session: after }).flags, 'aged from the newest quote seen, and an observation').toEqual(['forward_stale', 'session_observation']);
    expect(decideForward({ ...base, source: 'future', value: 24_200, used: [quote({ bookSeenAt: NOW - 61 * MIN })], session: after }).flags).toEqual(['session_observation']);
  });

  it('a missing contract has no quote to judge and says so without a price', () => {
    expect(assessQuote(null, ctx())).toMatchObject({ state: 'withheld', flags: ['no_quote'], book: 'none', priceBasis: null }); // (review R5: the state has its own name, not "no two-sided book")
  });
});

// ---- a chain: each row is judged on its own times, the forward is chosen from inputs that pass, and the chain's badge is its worst required input ----
const EXPIRY = '2026-11-03';
const NOW_S = NOW / 1000;
const T = yearsToExpiry(EXPIRY, NOW_S);
const ref = (token: string) => ({ token, symbol: `S${token}`, lot: 65 });
const STRIKES = [23_800, 24_000, 24_200, 24_400, 24_600];
const structure = (withFuture = true): ChainStructure => ({
  underlying: 'NIFTY', expiry: EXPIRY, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: withFuture ? ref('F') : null,
  strikes: STRIKES.map((strike) => ({ strike, call: ref(`C${strike}`), put: ref(`P${strike}`) })),
});
const FORWARD = 24_200;
const px = (kind: 'call' | 'put', strike: number): number => Math.round(price(kind, FORWARD, strike, T, 0.15) * 20) / 20;
const side = (kind: 'call' | 'put', strike: number, o: Partial<Quote> = {}): Quote => {
  const p = px(kind, strike);
  return quote({ last: p, bid: Math.max(0.05, p - 0.5), ask: p + 0.5, ...o });
};
type Times = Partial<Record<string, Partial<Quote>>>;
/** A chain's quotes: every contract fresh unless `over` says otherwise (keys `C24200`, `P24200`, `F`). */
function chain(over: Times = {}, spotless = false): { quotes: Map<string, Quote>; spot: number | null } {
  const quotes = new Map<string, Quote>();
  const put = (kind: 'call' | 'put' | 'future', strike: number, q: Quote) => quotes.set(keyOf({ underlying: 'NIFTY', expiry: EXPIRY, kind, strike }), q);
  for (const strike of STRIKES) {
    put('call', strike, side('call', strike, over[`C${strike}`]));
    put('put', strike, side('put', strike, over[`P${strike}`]));
  }
  put('future', 0, quote({ last: FORWARD, bid: FORWARD - 0.5, ask: FORWARD + 0.5, ...over.F }));
  return { quotes, spot: spotless ? null : 24_190 };
}
/** Every contract without a book (no bid, no ask): nothing the chain could read a forward from. */
function noBooks(): Times {
  const over: Times = { F: { bid: null, ask: null } };
  for (const s of STRIKES) (over[`C${s}`] = { bid: null, ask: null }, (over[`P${s}`] = { bid: null, ask: null }));
  return over;
}
const view = (c = chain(), s = structure(), session: SessionState = live) => assemble(s, c.quotes, c.spot, NOW_S, 0.07, { session });
const rowAt = (v: ReturnType<typeof view>, strike: number) => v.rows.find((r) => r.strike === strike)!;

describe('a chain: one fresh option beside a stale wing or a stale future', () => {
  it('everything fresh: every row has a volatility and Greeks, the forward is the future, and the chain is ok', () => {
    const v = view();
    expect(v.quality.status).toBe('ok');
    expect(v.quality.forward).toMatchObject({ source: 'future', state: 'ok' });
    for (const r of v.rows) expect([r.call?.iv != null, r.put?.iv != null, r.call?.greeks != null]).toEqual([true, true, true]);
    expect(v.quality.policy).toEqual({ id: 'option_quote_quality', version: 4 });
    expect(rowAt(v, 24_200).call?.quality.inputs.now).toBe(NOW);
  });

  it('a stale wing has its volatility and Greeks withheld while the fresh strikes keep theirs, and one contract updating does not refresh another', () => {
    const stale = { bookSeenAt: NOW - 40 * MIN, receivedAt: NOW - 40 * MIN, time: NOW - 40 * MIN };
    const v = view(chain({ C23800: stale, P23800: stale, C24600: stale, P24600: stale }));
    const wing = rowAt(v, 23_800);
    expect([wing.call?.iv, wing.call?.greeks, wing.put?.iv]).toEqual([null, null, null]);
    expect(wing.call?.ivNote).toMatch(/stale book/i);
    expect(wing.call?.quality).toMatchObject({ state: 'withheld', flags: ['stale_book'] });
    expect(rowAt(v, 24_200).call?.iv).not.toBeNull();
    expect(rowAt(v, 24_000).put?.iv).not.toBeNull();
    expect(v.smile.map((p) => p.strike)).not.toContain(23_800);
    expect(v.smile.map((p) => p.strike)).not.toContain(24_600);
    expect(v.quality.rows.withheld).toBe(4);
    expect(v.quality.status, 'the wing is not a required input: the headline inputs are fresh').toBe('ok');
  });

  it('a stale future is not used as the forward: the forward is read from the fresh at-the-money pair, and the rejection is on record', () => {
    const staleFuture = { bookSeenAt: NOW - 40 * MIN, receivedAt: NOW - 40 * MIN, time: NOW - 40 * MIN };
    const v = view(chain({ F: staleFuture }));
    expect(v.forwardFrom).toBe('parity');
    expect(v.quality.forward).toMatchObject({ source: 'parity', state: 'ok' });
    expect(v.quality.forward.rejected).toEqual([{ source: 'future', flags: ['stale_book'] }]);
    expect(rowAt(v, 24_200).call?.iv).not.toBeNull();
  });

  it('quotes that fail the rules are not inputs of the forward the options imply: a stale pair priced for another forward does not move it', () => {
    // no future: the forward is read from the options. The pairs at 24000 and 24200 are 40 minutes old and were priced when the forward was 24300; the rest are fresh at 24200.
    const stalePair = (strike: number): Times => {
      const old = { bookSeenAt: NOW - 40 * MIN, receivedAt: NOW - 40 * MIN, time: NOW - 40 * MIN };
      const at = (kind: 'call' | 'put') => {
        const p = Math.round(price(kind, 24_300, strike, T, 0.15) * 20) / 20;
        return { ...old, last: p, bid: Math.max(0.05, p - 0.5), ask: p + 0.5 };
      };
      return { [`C${strike}`]: at('call'), [`P${strike}`]: at('put') };
    };
    const v = view(chain({ ...stalePair(24_000), ...stalePair(24_200) }), structure(false));
    expect(v.forwardFrom).toBe('parity');
    expect(v.forward).toBeGreaterThan(24_190);
    expect(v.forward).toBeLessThan(24_210);
    expect(v.quality.forward.at, 'its inputs are all fresh').toBe(NOW - 1_000);
    expect(rowAt(v, 24_200).call?.quality.flags).toEqual(['stale_book']);
  });

  it('a forward older than the option withholds the volatility and the chain badge is its worst required input, not its newest quote', () => {
    const oldFuture = { bookSeenAt: NOW - 10 * MIN, receivedAt: NOW - 10 * MIN, time: NOW - 10 * MIN };
    const v = view(chain({ F: oldFuture }));
    expect(v.forwardFrom).toBe('future');
    const atm = rowAt(v, 24_200);
    expect(atm.call?.quality).toMatchObject({ state: 'withheld', flags: ['forward_unsynced'] });
    expect(atm.call?.iv).toBeNull();
    expect(v.atmIv).toBeNull();
    expect(v.quality.status).toBe('withheld');
    expect(v.quality.worst).toMatchObject({ input: expect.stringMatching(/^atm_/), flags: ['forward_unsynced'] });
    expect(v.quality.newestQuoteAgeMs, 'the newest quote is fresh and says nothing about the inputs').toBeLessThan(5_000);
  });

  it('with no usable forward and no spot the chain has no volatility at all, and says why', () => {
    const v = view(chain(noBooks(), true));
    expect(v.quality.forward).toMatchObject({ source: null, state: 'withheld', flags: ['no_forward'] });
    expect(v.quality.status).toBe('withheld');
    expect(rowAt(v, 24_200).call?.iv).toBeNull();
  });

  it('a forward grown from spot has no timestamp: labelled, not withheld', () => {
    const v = view(chain(noBooks()));
    expect(v.forwardFrom).toBe('spot');
    expect(v.quality.forward).toMatchObject({ source: 'spot', state: 'labelled', flags: ['forward_age_unknown'] });
  });

  it('a crossed ATM book withholds that side, labels nothing else, and the chain badge is withheld', () => {
    const v = view(chain({ C24200: { bid: 160, ask: 150 } }));
    expect(rowAt(v, 24_200).call?.quality).toMatchObject({ state: 'withheld', flags: ['crossed_book'] });
    expect(rowAt(v, 24_200).put?.iv).not.toBeNull();
    expect(v.quality.status).toBe('withheld');
    expect(v.quality.worst).toMatchObject({ input: 'atm_call', flags: ['crossed_book'] });
  });

  it('a wide book keeps its volatility but is labelled, and the chain says labelled', () => {
    const p = px('call', 24_200);
    const v = view(chain({ C24200: { bid: p * 0.4, ask: p * 1.6 } }));
    expect(rowAt(v, 24_200).call?.quality).toMatchObject({ state: 'labelled', flags: ['wide_book'] });
    expect(rowAt(v, 24_200).call?.iv).not.toBeNull();
    expect(v.quality.status).toBe('labelled');
  });

  it('an old trade beside a fresh book changes nothing about the volatility or the badge', () => {
    const v = view(chain({ C24200: { time: NOW - 5 * 60 * MIN }, P24200: { time: NOW - 5 * 60 * MIN } }));
    expect(rowAt(v, 24_200).call?.quality).toMatchObject({ state: 'ok', trade: 'old', book: 'fresh' });
    expect(rowAt(v, 24_200).call?.iv).not.toBeNull();
    expect(v.quality.status).toBe('ok');
  });

  it('a delayed and an out-of-order contract keep their volatility, labelled with what happened', () => {
    const v = view(chain({ C24200: { deliveryDelayMs: 45_000 }, P24200: { outOfOrder: 3 } }));
    expect(rowAt(v, 24_200).call?.quality.flags).toEqual(['delayed_feed']);
    expect(rowAt(v, 24_200).put?.quality.flags).toEqual(['out_of_order_discarded']);
    expect(rowAt(v, 24_200).call?.iv).not.toBeNull();
    expect(v.quality.status).toBe('labelled');
  });

  it('last-session data is labelled an observation, not a tradable price, and the volatility is kept', () => {
    const ref = NOW - 6 * 60 * MIN;
    const old = { bookSeenAt: ref - 1_000, receivedAt: ref - 1_000, time: ref - 1_000 };
    const over: Times = { F: old };
    for (const s of STRIKES) (over[`C${s}`] = old, (over[`P${s}`] = old));
    const v = view(chain(over), structure(), { state: 'last_session', referenceMs: ref });
    expect(rowAt(v, 24_200).call?.quality.flags).toEqual(['session_observation']);
    expect(rowAt(v, 24_200).call?.iv).not.toBeNull();
    expect(v.quality.status).toBe('labelled');
    expect(v.quality.session.state).toBe('last_session');
  });

  it('every row records the inputs and the policy that produced its decision', () => {
    const d = rowAt(view(), 24_000).put!.quality;
    expect(d.policy).toEqual({ id: 'option_quote_quality', version: 4 });
    // (review: version 2 adds the valid book time used, how far ahead of the clock it was, the packet time and every time that was not valid)
    expect(Object.keys(d.inputs).sort()).toEqual(['ageBase', 'ask', 'bid', 'bookAgeMs', 'bookAheadMs', 'bookAt', 'bookSeenAt', 'bookTime', 'bookTimeBasis', 'deliveryDelayMs', 'forwardAt', 'last', 'now', 'outOfOrder', 'packetTime', 'receivedAt', 'spreadPct', 'syncGapMs', 'timeFaults', 'tradeAgeMs', 'tradeTime']);
  });
});
