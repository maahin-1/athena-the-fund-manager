import { describe, expect, it } from 'vitest';
import type { ChainStructure } from '../src/options/api';
import { price, yearsToExpiry } from '../src/options/black76';
import { assemble } from '../src/options/assemble';
import type { Quote } from '../src/options/chain';
import { assessQuote, decideForward, POLICY_V3, qualitySummary, type AssessContext, type SessionState } from '../src/options/quality';
import { keyOf } from '../src/options/synthetic';

/** T3 review corrections R4, R5 and R6 (the quality policy's composition and time validity), with controlled clocks. Fixtures only: nothing here says anything about a real market.
 *  The limits are written out (5 s, 60 s, 5 min, 15 min) so a change to the policy shows here. */

const MIN = 60_000;
const NOW = Date.UTC(2026, 9, 27, 11, 0) - 19_800_000; // ms
const live: SessionState = { state: 'live', referenceMs: NOW };

const quote = (o: Partial<Quote> = {}): Quote => ({ last: 100, bid: 99.5, ask: 100.5, volume: 10, oi: 100, oiChange: 0, time: NOW - 1_000, receivedAt: NOW - 1_000, bookSeenAt: NOW - 1_000, ...o });
const ctx = (o: Partial<AssessContext> = {}): AssessContext => ({ now: NOW, session: live, forward: null, ...o });

// ---- R6: a time in the future is not a fresh time ----
describe('R6: future-dated and invalid times are never counted as fresh', () => {
  it('the declared limits are the ones the tests below and the server rely on (the server\'s `MAX_EXCHANGE_AHEAD_MS` is 60,000 as well: a Rust test pins it)', () => {
    expect([POLICY_V3.version, POLICY_V3.maxReceiptAheadMs, POLICY_V3.maxExchangeAheadMs, POLICY_V3.maxSyncGapMs, POLICY_V3.maxBookAgeMs]).toEqual([4, 5_000, 60_000, 5 * MIN, 15 * MIN]); // (T3-C1, intentional change: version 3 declares the consequences of an invalid receipt time, an invalid packet time and an unverified session)
  });

  it('a book time an hour after the decision is invalid: withheld, never ok, with the value and the reason recorded', () => {
    const d = assessQuote(quote({ bookSeenAt: NOW + 60 * MIN }), ctx());
    expect(d.state).toBe('withheld');
    expect(d.flags).toEqual(['invalid_book_time']);
    expect(d.book).toBe('invalid');
    expect(d.inputs.bookAgeMs, 'no negative age is passed off as freshness').toBeNull();
    expect(d.inputs.timeFaults).toEqual([{ field: 'bookSeenAt', value: NOW + 60 * MIN, reason: 'future', aheadMs: 60 * MIN, origin: 'page' }]);
    expect(d.notes[0]).toMatch(/book time invalid/i);
  });

  it('a server-clock book time may be 5 s ahead (the clocks are estimates), 5 s and 1 ms is invalid', () => {
    const edge = assessQuote(quote({ bookSeenAt: NOW + 5_000 }), ctx());
    expect(edge).toMatchObject({ state: 'ok', flags: [], book: 'fresh' });
    expect(edge.inputs.bookAgeMs, 'within the tolerance the age is nil, not negative').toBe(0);
    expect(edge.inputs.bookAheadMs).toBe(5_000);
    expect(assessQuote(quote({ bookSeenAt: NOW + 5_001 }), ctx()).flags).toEqual(['invalid_book_time']);
  });

  it('an exchange-supplied book time may be 60 s ahead (another clock), 60 s and 1 ms is invalid', () => {
    expect(assessQuote(quote({ bookTime: NOW + 60_000 }), ctx())).toMatchObject({ state: 'ok', book: 'fresh' });
    const d = assessQuote(quote({ bookTime: NOW + 60_001 }), ctx());
    expect(d.flags).toEqual(['invalid_book_time']);
    expect(d.inputs.timeFaults[0]).toMatchObject({ field: 'bookTime', reason: 'future', aheadMs: 60_001 });
  });

  it('a book time that is not a number, or not an epoch time, is invalid with its reason, and an absent one is unknown rather than invalid', () => {
    expect(assessQuote(quote({ bookSeenAt: Number.NaN }), ctx()).inputs.timeFaults).toEqual([{ field: 'bookSeenAt', value: 'NaN', reason: 'not_finite', origin: 'page' }]);
    expect(assessQuote(quote({ bookSeenAt: Number.POSITIVE_INFINITY }), ctx()).flags).toEqual(['invalid_book_time']);
    const noFallback = assessQuote(quote({ bookTime: Number.NaN, bookSeenAt: NOW - 1_000 }), ctx());
    expect([noFallback.flags, noFallback.inputs.bookTimeBasis], 'a source that gives a book time and gives it wrongly is not trusted for the other').toEqual([['invalid_book_time'], 'book_time']);
    const tiny = assessQuote(quote({ bookSeenAt: 12_345 }), ctx());
    expect([tiny.flags, tiny.inputs.timeFaults[0]?.reason]).toEqual([['invalid_book_time'], 'implausible']);
    const absent = assessQuote(quote({ bookSeenAt: undefined }), ctx());
    expect([absent.flags, absent.inputs.timeFaults]).toEqual([['book_time_unknown'], []]);
  });

  it('a trade time in the future is invalid and labelled, never a recent trade; the fresh book is still read', () => {
    const d = assessQuote(quote({ time: NOW + 60 * MIN }), ctx());
    expect(d).toMatchObject({ state: 'labelled', flags: ['invalid_trade_time'], trade: 'invalid', book: 'fresh', priceBasis: 'mid' });
    expect(d.inputs.tradeAgeMs).toBeNull();
    expect(d.inputs.timeFaults).toEqual([{ field: 'tradeTime', value: NOW + 60 * MIN, reason: 'future', aheadMs: 60 * MIN, origin: 'page' }]);
    expect(assessQuote(quote({ time: NOW + 60_000 }), ctx()).state, 'at the limit').toBe('ok');
    expect(assessQuote(quote({ time: Number.NaN }), ctx()).flags).toEqual(['invalid_trade_time']);
  });

  it('a time the server refused is recorded and makes the trade time invalid; a refused packet time is recorded and labelled', () => {
    const d = assessQuote(quote({ time: undefined, timeFaults: [{ f: 'lt', v: 5, why: 'implausible' }] }), ctx());
    expect(d).toMatchObject({ state: 'labelled', flags: ['invalid_trade_time'], trade: 'invalid' });
    expect(d.inputs.timeFaults).toEqual([{ field: 'tradeTime', value: 5, reason: 'implausible', origin: 'server' }]);
    const t = assessQuote(quote({ timeFaults: [{ f: 't', v: 5, why: 'implausible' }] }), ctx());
    // (T3-C1, intentional change: a refused packet time used to be recorded only, `ok` with no flag; the consequences are tested in optioninvalidtimes.test.ts)
    expect([t.state, t.flags, t.inputs.timeFaults]).toEqual(['labelled', ['invalid_packet_time'], [{ field: 'packetTime', value: 5, reason: 'implausible', origin: 'server' }]]);
  });

  it('a forward input with a future time makes the forward invalid and withheld, and it is passed on to the option', () => {
    const f = decideForward({ now: NOW, session: live, source: 'future', value: 24_200, used: [quote({ bookSeenAt: NOW + 60 * MIN })], rejected: [] });
    expect(f).toMatchObject({ state: 'withheld', flags: ['forward_time_invalid'], at: null });
    const o = assessQuote(quote(), ctx({ forward: f }));
    expect(o).toMatchObject({ state: 'withheld', flags: ['forward_time_invalid'] });
  });
});

// ---- a chain, for R4, R5 and R6 ----
const EXPIRY = '2026-11-03';
const NOW_S = NOW / 1000;
const T = yearsToExpiry(EXPIRY, NOW_S);
const ref = (token: string) => ({ token, symbol: `S${token}`, lot: 65 });
const STRIKES = [23_800, 24_000, 24_200, 24_400, 24_600];
type Side = 'both' | 'call' | 'put' | 'none';
/** The chain's structure; `only` names strikes that have fewer than both contracts. */
const structure = (o: { future?: boolean; strikes?: number[]; only?: Record<number, Side> } = {}): ChainStructure => ({
  underlying: 'NIFTY', expiry: EXPIRY, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: o.future === false ? null : ref('F'),
  strikes: (o.strikes ?? STRIKES).map((strike) => {
    const s = o.only?.[strike] ?? 'both';
    return { strike, call: s === 'both' || s === 'call' ? ref(`C${strike}`) : null, put: s === 'both' || s === 'put' ? ref(`P${strike}`) : null };
  }),
});
const FORWARD = 24_200;
const px = (kind: 'call' | 'put', strike: number): number => Math.round(price(kind, FORWARD, strike, T, 0.15) * 20) / 20;
const side = (kind: 'call' | 'put', strike: number, o: Partial<Quote> = {}): Quote => {
  const p = px(kind, strike);
  return quote({ last: p, bid: Math.max(0.05, p - 0.5), ask: p + 0.5, ...o });
};
type Over = Partial<Record<string, Partial<Quote>>>;
/** Quotes for every contract of `STRIKES` (and the future), fresh unless `over` says otherwise (keys `C24200`, `P24200`, `F`); `skip` leaves a key out (the feed has not spoken about it). */
function chain(over: Over = {}, skip: string[] = []): Map<string, Quote> {
  const quotes = new Map<string, Quote>();
  const put = (kind: 'call' | 'put' | 'future', strike: number, k: string, q: Quote) => (skip.includes(k) ? undefined : quotes.set(keyOf({ underlying: 'NIFTY', expiry: EXPIRY, kind, strike }), q));
  for (const strike of STRIKES) {
    put('call', strike, `C${strike}`, side('call', strike, over[`C${strike}`]));
    put('put', strike, `P${strike}`, side('put', strike, over[`P${strike}`]));
  }
  put('future', 0, 'F', quote({ last: FORWARD, bid: FORWARD - 0.5, ask: FORWARD + 0.5, ...over.F }));
  return quotes;
}
const view = (quotes = chain(), s = structure(), spot: number | null = 24_190, session: SessionState = live) => assemble(s, quotes, spot, NOW_S, 0.07, { session });
const rowAt = (v: ReturnType<typeof view>, strike: number) => v.rows.find((r) => r.strike === strike)!;
const requiredOf = (v: ReturnType<typeof view>) => v.quality.required.map((r) => `${r.input}:${r.state}:${r.flags.join('+')}`);

describe('R6 in a chain: a future-dated chain is never ok', () => {
  it('every time an hour ahead (a server clock that rolled back): no volatility, nothing fit, no negative age passed off as fresh', () => {
    const ahead = { bookSeenAt: NOW + 60 * MIN, receivedAt: NOW + 60 * MIN, time: NOW + 60 * MIN };
    const over: Over = { F: ahead };
    for (const s of STRIKES) (over[`C${s}`] = ahead, (over[`P${s}`] = ahead));
    const v = view(chain(over));
    expect(v.quality.status).toBe('withheld');
    expect(v.atmIv).toBeNull();
    for (const r of v.rows) expect([r.call?.iv, r.put?.iv]).toEqual([null, null]);
    expect(rowAt(v, 24_200).call?.quality.flags).toContain('invalid_book_time');
    expect(v.quality.newestQuoteAgeMs === null || v.quality.newestQuoteAgeMs >= 0, 'the newest quote cannot be negatively old').toBe(true);
  });

  it('a future-dated future is passed over, on record, and the forward is read from the options that are fit', () => {
    const v = view(chain({ F: { bookSeenAt: NOW + 60 * MIN, receivedAt: NOW + 60 * MIN } }));
    expect(v.forwardFrom).toBe('parity');
    expect(v.quality.forward.rejected).toEqual([{ source: 'future', flags: ['invalid_book_time'] }]);
  });
});

// ---- R5: the required inputs are always listed, and a missing one has a named consequence ----
describe('R5: a missing required input is represented and has a declared consequence', () => {
  it('a complete pair lists the forward, the call and the put as present, and the chain has both sides', () => {
    const v = view();
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:ok:', 'atm_put:ok:']);
    expect(v.quality.required.map((r) => r.presence)).toEqual(['present', 'present', 'present']);
    expect(v.quality.coverage).toBe('both');
    expect(v.quality.status).toBe('ok');
  });

  it('no put contract at the money: the put is listed as absent and labelled, the headline is from the call alone, and the chain says labelled', () => {
    const v = view(chain(), structure({ only: { 24_200: 'call' } }));
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:ok:', 'atm_put:labelled:atm_side_absent']);
    expect(v.quality.required[2].presence).toBe('absent');
    expect(v.quality.coverage).toBe('call_only');
    expect(v.quality.status).toBe('labelled');
    expect(v.atmIv, 'the one side that exists still gives the headline').not.toBeNull();
    expect(qualitySummary(v.quality)).toMatch(/put/i);
    expect(qualitySummary(v.quality)).toMatch(/call only|one side/i);
  });

  it('no call contract at the money is the same the other way round', () => {
    const v = view(chain(), structure({ only: { 24_200: 'put' } }));
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:labelled:atm_side_absent', 'atm_put:ok:']);
    expect([v.quality.coverage, v.quality.status]).toEqual(['put_only', 'labelled']);
  });

  it('a contract the feed has not spoken about is a required input with no quote: withheld, named, and the chain is withheld', () => {
    const v = view(chain({}, ['C24200']));
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:withheld:no_quote', 'atm_put:ok:']);
    expect(v.quality.required[1].presence).toBe('present');
    expect(v.quality.status).toBe('withheld');
    expect(rowAt(v, 24_200).call?.ivNote).toBe('no price');
  });

  it('an empty strike list has a forward from the future but no at-the-money strike: both sides are listed as missing and the chain is withheld', () => {
    const v = view(chain(), structure({ strikes: [] }));
    expect(v.rows).toEqual([]);
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:withheld:no_atm', 'atm_put:withheld:no_atm']);
    expect(v.quality.required.map((r) => r.presence)).toEqual(['present', 'no_atm', 'no_atm']);
    expect([v.quality.coverage, v.quality.status]).toEqual(['none', 'withheld']);
  });

  it('no forward means no at-the-money strike: all three are listed, none passes', () => {
    const over: Over = { F: { bid: null, ask: null, last: null } };
    for (const s of STRIKES) (over[`C${s}`] = { bid: null, ask: null, last: null }, (over[`P${s}`] = { bid: null, ask: null, last: null }));
    const v = view(chain(over), structure(), null);
    expect(requiredOf(v)).toEqual(['forward:withheld:no_forward', 'atm_call:withheld:no_atm', 'atm_put:withheld:no_atm']);
    expect(v.quality.status).toBe('withheld');
  });

  it('a strike with neither contract at the money is no at-the-money strike for either side', () => {
    const v = view(chain(), structure({ only: { 24_200: 'none' } }));
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:withheld:no_atm', 'atm_put:withheld:no_atm']);
    expect(v.quality.coverage).toBe('none');
  });
});

// ---- R4: a forward is built from inputs, and what is wrong with an input is still wrong with the forward ----
describe('R4: forward construction keeps its inputs\' labels and checks that its inputs are in step', () => {
  it('a labelled future gives a labelled forward: the labels and their source are carried, the options and the chain inherit them', () => {
    const p = FORWARD;
    const v = view(chain({ F: { last: p, bid: p * 0.5, ask: p * 1.5, deliveryDelayMs: 45_000, outOfOrder: 2 } }));
    const f = v.quality.forward;
    expect(f).toMatchObject({ source: 'future', state: 'labelled' });
    expect(f.flags).toEqual(['forward_input_labelled']);
    expect(f.inputFlags).toEqual(expect.arrayContaining(['wide_book', 'delayed_feed']));
    expect(f.inputFlags, '(policy 4) a discard is a note on the future, not a label it passes on').not.toContain('out_of_order_discarded');
    expect(f.inputs).toEqual([expect.objectContaining({ role: 'future', strike: null, state: 'labelled' })]);
    expect(f.inputs[0].flags, 'still recorded on the input itself').toContain('out_of_order_discarded');
    expect(v.quality.status).toBe('labelled');
    expect(v.quality.required[0]).toMatchObject({ input: 'forward', state: 'labelled', flags: ['forward_input_labelled'] });
    const atm = rowAt(v, 24_200).call!;
    expect(atm.quality.flags).toContain('forward_input_labelled');
    expect(atm.quality.state).toBe('labelled');
    expect(atm.iv, 'labelled, not withheld').not.toBeNull();
    expect(atm.ivNote).toMatch(/forward input labelled/i);
    expect(qualitySummary(v.quality)).toMatch(/wide book/i);
  });

  it('a labelled contributor to the parity forward is carried with its strike and side', () => {
    const v = view(chain({ C24200: { deliveryDelayMs: 45_000 } }), structure({ future: false }));
    expect(v.forwardFrom).toBe('parity');
    const f = v.quality.forward;
    expect(f.state).toBe('labelled');
    expect(f.inputFlags).toEqual(['delayed_feed']);
    expect(f.inputs.find((i) => i.role === 'call' && i.strike === 24_200)).toMatchObject({ state: 'labelled', flags: ['delayed_feed'] });
    expect(v.quality.status).toBe('labelled');
  });

  it('no input warning disappears because the input became a forward: every label of every input is in the forward\'s record (a note stays on the input)', () => {
    const f = decideForward({ now: NOW, session: live, source: 'future', value: 24_200, used: [quote({ bid: 20, ask: 60, deliveryDelayMs: 45_000, outOfOrder: 1, time: NOW + 60 * MIN })], rejected: [] });
    expect(f.state).toBe('labelled');
    expect([...f.inputFlags].sort()).toEqual(['delayed_feed', 'invalid_trade_time', 'wide_book']);
    expect(f.inputs[0].flags).toContain('out_of_order_discarded');
  });

  it('(policy 4) a future whose only event is a discarded late packet gives a fit forward, and the chain is not marked', () => {
    const f = decideForward({ now: NOW, session: live, source: 'future', value: 24_200, used: [quote({ outOfOrder: 30, outOfOrderAt: NOW - 1_000 })], rejected: [] });
    expect(f.state).toBe('ok');
    expect(f.inputFlags).toEqual([]);
    expect(f.inputs[0]).toMatchObject({ state: 'ok', flags: ['out_of_order_discarded'] });
  });

  it('call and put of a parity pair 10 minutes apart, each under the 15-minute limit, are not in step: the forward is withheld', () => {
    const v = view(chain({ C24200: { bookSeenAt: NOW - 1_000 }, P24200: { bookSeenAt: NOW - 10 * MIN, receivedAt: NOW - 10 * MIN } }), structure({ future: false }));
    const f = v.quality.forward;
    expect(f).toMatchObject({ source: 'parity', state: 'withheld' });
    expect(f.flags).toContain('forward_unsynced');
    expect(f.spanMs).toBe(10 * MIN - 1_000);
    expect(rowAt(v, 24_000).call?.quality.flags, 'the unsynchronised forward is passed on to every option').toContain('forward_unsynced');
    expect(v.quality.status).toBe('withheld');
    expect(v.atmIv).toBeNull();
  });

  it('strikes read at different times are not in step either, though each strike\'s own pair is', () => {
    const six = { bookSeenAt: NOW - 6 * MIN, receivedAt: NOW - 6 * MIN };
    const v = view(chain({ C24400: six, P24400: six, C24000: { ...six, bookSeenAt: NOW - 1_000 }, P24000: { ...six, bookSeenAt: NOW - 1_000 } }), structure({ future: false }));
    expect(v.quality.forward.flags).toContain('forward_unsynced');
    expect(v.quality.forward.spanMs).toBeGreaterThan(5 * MIN);
  });

  it('the span is judged at the limit: 5 minutes is in step, 5 minutes and 1 ms is not', () => {
    const at = (gap: number) => decideForward({ now: NOW, session: live, source: 'parity', value: 24_200, used: [quote({ bookSeenAt: NOW - 1_000 }), quote({ bookSeenAt: NOW - 1_000 - gap })], rejected: [] });
    expect(at(5 * MIN)).toMatchObject({ state: 'ok', spanMs: 5 * MIN });
    expect(at(5 * MIN + 1)).toMatchObject({ state: 'withheld', flags: ['forward_unsynced'] });
  });

  it('every candidate that was evaluated is on record with the outcome and why: a stale future, then the options that were fit, with the pairs left out', () => {
    const old = { bookSeenAt: NOW - 40 * MIN, receivedAt: NOW - 40 * MIN, time: NOW - 40 * MIN };
    const v = view(chain({ F: old, C24000: old, P24000: old }));
    const f = v.quality.forward;
    expect(f.candidates.map((c) => `${c.source}:${c.outcome}`)).toEqual(['future:rejected', 'parity:used']);
    expect(f.candidates[0]).toMatchObject({ reason: 'withheld', flags: ['stale_book'] });
    expect(f.excluded).toEqual(expect.arrayContaining([{ strike: 24_000, kind: 'call', flags: ['stale_book'] }, { strike: 24_000, kind: 'put', flags: ['stale_book'] }]));
    expect(f.rejected).toEqual([{ source: 'future', flags: ['stale_book'] }]);
  });

  it('a parity forward set aside because it is far from spot, and no usable pair at all, are on record', () => {
    const far = view(chain({}), structure({ future: false }), 20_000); // spot nowhere near the options' forward
    expect(far.forwardFrom).toBe('spot');
    expect(far.quality.forward.candidates.map((c) => `${c.source}:${c.outcome}:${c.reason}`)).toEqual(['future:absent:no_future', 'parity:rejected:far_from_spot', 'spot:used:grown_from_spot']);
    const none = view(chain({}, STRIKES.flatMap((s) => [`C${s}`, `P${s}`])), structure({ future: false }), 24_190);
    expect(none.quality.forward.candidates.map((c) => `${c.source}:${c.outcome}:${c.reason}`)).toEqual(['future:absent:no_future', 'parity:rejected:no_usable_pair', 'spot:used:grown_from_spot']);
  });

  it('an input that is itself unfit (a crossed book, one side missing) makes the forward unfit, and the decision names it', () => {
    const crossed = decideForward({ now: NOW, session: live, source: 'future', value: 24_200, used: [quote({ bid: 101, ask: 100 })], rejected: [] });
    expect(crossed).toMatchObject({ state: 'withheld', flags: ['forward_input_withheld'] });
    expect(crossed.inputs[0]).toMatchObject({ role: 'future', state: 'withheld', flags: ['crossed_book'] });
    const oneSided = decideForward({ now: NOW, session: live, source: 'parity', value: 24_200, used: [quote(), quote({ bid: null })], rejected: [] });
    expect(oneSided.flags).toEqual(['forward_input_withheld']);
    expect(oneSided.inputs.map((i) => i.state)).toEqual(['ok', 'withheld']);
  });

  it('the decision carries the policy that judged it, version 4', () => {
    expect(view().quality.policy).toEqual({ id: 'option_quote_quality', version: 4 });
    expect(view().quality.forward.policy).toEqual({ id: 'option_quote_quality', version: 4 });
  });
});
