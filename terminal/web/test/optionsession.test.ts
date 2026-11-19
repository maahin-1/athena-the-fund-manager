// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import cases from './fixtures/deriv-quotes.cases.json';
import type { AppContext } from '../src/panels/base';
import type { DerivRow } from '../src/feed';
import type { ChainStructure, OptionsApi } from '../src/options/api';
import { liveFeed, quoteOf, type LiveQuoteRow, type PushSource } from '../src/options/livefeed';
import { assessQuote, type SessionState } from '../src/options/quality';
import type { ContractKey } from '../src/options/synthetic';
import { OptionsView } from '../src/options/view';

/** T3 review corrections R1 (the packet's time and the last trade's time are different fields, end to end) and R3 (one session policy in the server's clock that cannot take a replay, a cache or a stopped
 *  feed for current activity). Controlled clocks; the server's rows are those of `fixtures/deriv-quotes.cases.json`, which the Rust decoder and store produce (terminal/src/deriv_quotes.rs), or small
 *  stand-ins where a situation needs a clock to move. Fixtures only: nothing here says anything about a real market. */

const S = Date.UTC(2026, 9, 27, 11, 0) - 19_800_000; // the server's clock, ms
const DAY = 86_400_000;
const row = (token: string, o: Partial<LiveQuoteRow> = {}): LiveQuoteRow => ({
  seg: 'NFO', token, last: 10, bid: 9.9, ask: 10.1, volume: 1, oi: 1, oip: 0, t: S - 3_000, lt: S - 3_000, r: S - 2_000, bs: S - 2_000, dly: 1_000, ooo: 0, ...o,
});
const key = (token: string): ContractKey => ({ underlying: 'NIFTY', expiry: '2026-11-03', kind: 'call', strike: 24_200, ref: { segment: 'NFO', token, lot: 65 } });
const fetcherOf = (rows: () => LiveQuoteRow[], serverNow: () => number): typeof fetch =>
  (async () => ({ ok: true, status: 200, json: async () => ({ live: true, quotes: rows(), now: serverNow() }) })) as unknown as typeof fetch;

describe('R3: one session policy, in the server\'s clock', () => {
  it('quotes received just now but stamped yesterday are a replay, and the next one a second newer, still yesterday, does not make them live', async () => {
    let t = S - DAY;
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1', { t, lt: t, r: S - 1_000, bs: S - 1_000 })], () => S), spotOf: () => 24_200, now: () => S });
    await f.quotes([key('1')]);
    expect(f.session().state).toBe('last_session');
    expect(f.session().reason).toBe('event_times_old');
    expect(f.badge().text).toBe('last session prices');
    t += 1_000;
    await f.quotes([key('1')]);
    expect(f.session().state, 'an advancing stale stamp is not current activity').toBe('last_session');
    expect(f.badge().text).toBe('last session prices');
  });

  it('switching to cached contracts, even slightly newer ones, is not current activity: their receipts are old', async () => {
    const rows: Record<string, LiveQuoteRow> = { '1': row('1', { r: S - 3_600_000, bs: S - 3_600_000, t: S - 3_601_000, lt: S - 3_601_000 }), '2': row('2', { r: S - 3_540_000, bs: S - 3_540_000, t: S - 3_541_000, lt: S - 3_541_000 }) };
    let asked = '1';
    const f = liveFeed({ fetcher: fetcherOf(() => [rows[asked]], () => S), spotOf: () => 24_200, now: () => S });
    await f.quotes([key('1')]);
    expect([f.session().state, f.session().reason]).toEqual(['last_session', 'no_recent_packets']);
    asked = '2'; // the page moved to another expiry whose contracts the server holds from a minute later
    await f.quotes([key('2')]);
    expect([f.session().state, f.session().reason]).toEqual(['last_session', 'no_recent_packets']);
    expect(f.badge().text).toBe('last session prices');
  });

  it('a packet received a moment ago whose time is close to its receipt is current activity: live', async () => {
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1')], () => S), spotOf: () => 24_200, now: () => S });
    await f.quotes([key('1')]);
    expect(f.session()).toMatchObject({ state: 'live', reason: 'recent_packets_with_current_event_times', referenceMs: S - 2_000 });
    expect(f.badge().text).toBe('live prices');
  });

  it('a page clock three hours behind or an hour ahead of the server\'s changes nothing: ages and the session are the server\'s', async () => {
    for (const offset of [-3 * 3_600_000, 3_600_000]) {
      const f = liveFeed({ fetcher: fetcherOf(() => [row('1')], () => S), spotOf: () => 24_200, now: () => S + offset });
      await f.quotes([key('1')]);
      expect(f.serverNow(), `offset ${offset}`).toBe(S);
      expect(f.session().state, `offset ${offset}`).toBe('live');
      expect(f.badge().text).toBe('live prices');
    }
  });

  it('a feed that has gone quiet is the last session, and says so by how long it has been quiet; its quotes are observations, not fit', async () => {
    let server = S;
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1')], () => server), spotOf: () => 24_200, now: () => server });
    await f.quotes([key('1')]);
    expect(f.session().state).toBe('live');
    server = S + 10 * 60_000; // ten minutes on, the same packet is still the newest
    await f.quotes([key('1')]);
    expect([f.session().state, f.session().reason]).toEqual(['last_session', 'no_recent_packets']);
    expect(f.badge().text).toBe('last session prices');
    expect(f.badge().title).toMatch(/no packet|stopped|shut/i);
    const d = assessQuote(quoteOf(row('1'), 65), { now: server, session: f.session(), forward: null });
    expect(d.flags).toContain('session_observation');
    expect(d.state).not.toBe('ok');
  });

  it('a packet repeated unchanged is not new activity, and a reconnect that re-delivers held rows does not make them current', async () => {
    const held = new Map<string, DerivRow>([['NFO:1', row('1') as DerivRow]]);
    let page = S;
    const push: PushSource = { derivs: held, pushing: true, serverSkewMs: 0, needDerivs: () => undefined, onDerivs: () => () => undefined };
    const f = liveFeed({ fetcher: fetcherOf(() => [], () => S), spotOf: () => 24_200, now: () => page, push });
    await f.probe();
    await f.quotes([key('1')]);
    expect(f.session().state).toBe('live');
    page = S + 10 * 60_000;
    held.set('NFO:1', { ...(row('1') as DerivRow) }); // re-delivered on a reconnect: the same receipt, the same times
    await f.quotes([key('1')]);
    expect(f.session().state).toBe('last_session');
    held.set('NFO:1', row('1', { t: S + 10 * 60_000 - 1_000, r: S + 10 * 60_000 - 500, bs: S + 10 * 60_000 - 500, lt: S + 10 * 60_000 - 1_000 }) as DerivRow); // a genuinely new packet
    await f.quotes([key('1')]);
    expect(f.session().state).toBe('live');
  });

  it('a time-less packet gives no evidence either way: unknown, and a quote judged under it is labelled unverified', async () => {
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1', { t: null, lt: null })], () => S), spotOf: () => 24_200, now: () => S });
    await f.quotes([key('1')]);
    expect([f.session().state, f.session().reason]).toEqual(['unknown', 'no_event_time']);
    expect(f.badge().text).not.toBe('live prices');
    const d = assessQuote(quoteOf(row('1', { t: null, lt: null }), 65), { now: S, session: f.session(), forward: null });
    expect(d.flags).toContain('session_unverified');
  });

  it('a server that sends no receipt times at all (an older server) gives no session evidence: unknown, never live', async () => {
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1', { r: undefined, bs: undefined })], () => S), spotOf: () => 24_200, now: () => S });
    expect(f.session()).toMatchObject({ state: 'unknown', referenceMs: null });
    await f.quotes([key('1')]);
    expect([f.session().state, f.session().reason]).toEqual(['unknown', 'no_receipts']);
    expect(f.badge().text).not.toBe('live prices');
  });

  it('receipts ahead of the server clock beyond the tolerance are not evidence (a clock that rolled back)', async () => {
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1', { r: S + 3_600_000, bs: S + 3_600_000 })], () => S), spotOf: () => 24_200, now: () => S });
    await f.quotes([key('1')]);
    expect(f.session().state).not.toBe('live');
    expect(f.session().referenceMs, 'a future receipt is not a reference').toBeNull();
  });

  it('the session records what it was decided from, and the policy', async () => {
    const f = liveFeed({ fetcher: fetcherOf(() => [row('1', { t: S - 3_000, r: S - 2_000 })], () => S), spotOf: () => 24_200, now: () => S });
    await f.quotes([key('1')]);
    const s = f.session();
    expect(s.policy).toEqual({ id: 'option_session', version: 1 });
    expect(s.evidence).toMatchObject({ serverNow: S, newestReceiptMs: S - 2_000, newestEventMs: S - 3_000, eventLagMs: 1_000, receiptAgeMs: 2_000, contracts: 1, current: 1 });
  });
});

// ---- R1: the fields of the packet, through the decoder, the store and the route's rows to the page (the fixture is made by the Rust code) ----
type Case = { note: string; now: number; rows: LiveQuoteRow[] };
const fixture = (): Record<string, Case> => cases as unknown as Record<string, Case>;
const feedOf = (c: Case) => {
  const derivs = new Map<string, DerivRow>(c.rows.map((r) => [`${r.seg}:${r.token}`, r as DerivRow]));
  const push: PushSource = { derivs, pushing: true, serverSkewMs: 0, needDerivs: () => undefined, onDerivs: () => () => undefined };
  return liveFeed({ fetcher: fetcherOf(() => [], () => c.now), spotOf: () => 24_200, now: () => c.now, push });
};
/** The page's reading of a case: the feed's session, the quote, and the decision under the feed's session or under `session` where the test is about the quote alone. */
const judged = async (c: Case, token: string, session?: SessionState) => {
  const f = feedOf(c);
  await f.probe();
  await f.quotes(c.rows.map((r) => key(r.token)));
  const q = quoteOf(c.rows.find((r) => r.token === token)!, 65);
  return { f, q, d: assessQuote(q, { now: c.now, session: session ?? f.session(), forward: null }) };
};
const LIVE = (now: number): SessionState => ({ state: 'live', referenceMs: now });

describe('R1 end to end: real packets, decoded and stored by the Rust code, read by the page', () => {
  it('a book that changed 2 s ago beside a trade two hours old: the trade time is the trade\'s, the book is fresh, the session is live', async () => {
    const c = fixture().fresh_book_old_trade;
    const { f, q, d } = await judged(c, '40');
    expect(c.rows[0].lt).toBe(c.now - 7_200_000);
    expect(q.time, 'the trade time comes from the last-traded field').toBe(c.now - 7_200_000);
    expect(q.packetTime, 'the packet time is its own field').toBe(c.now - 3_000);
    expect(q.bookSeenAt).toBe(c.now - 2_000);
    expect(d).toMatchObject({ state: 'ok', book: 'fresh', trade: 'old' });
    expect(d.inputs.tradeAgeMs).toBe(7_200_000);
    expect(f.session().state).toBe('live');
  });

  it('an absent last-traded time stays unknown: it is not replaced by the packet\'s time', async () => {
    const c = fixture().no_trade_time;
    expect(c.rows[0].lt ?? null).toBeNull();
    const { q, d } = await judged(c, '41');
    expect(q.time).toBeUndefined();
    expect(q.packetTime).toBe(c.now - 3_000);
    expect(d.trade).toBe('unknown');
    expect(d.state, 'a fresh two-sided book needs no trade time').toBe('ok');
  });

  it('times the server refused arrive with their reasons and are recorded, never used', async () => {
    const c = fixture().refused_times;
    expect(c.rows[0].tf).toEqual([{ f: 't', v: expect.any(Number), why: 'future' }, { f: 'lt', v: 5, why: 'implausible' }]);
    const { q, d } = await judged(c, '42', LIVE(c.now)); // the quote alone: the session is tested elsewhere
    expect([q.time, q.packetTime]).toEqual([undefined, undefined]);
    expect(d.flags, '(T3-C1, intentional change: the refused packet time is labelled too; it used to be recorded only, with no flag)').toEqual(['invalid_trade_time', 'invalid_packet_time']);
    expect(d.inputs.timeFaults.map((x) => [x.field, x.reason, x.origin])).toEqual([['packetTime', 'future', 'server'], ['tradeTime', 'implausible', 'server']]);
  });

  it('a packet time known, then none, then an older one: the older packet was discarded by the store and the page notes it while the discard is recent (policy 4: a note, not a label)', async () => {
    const c = fixture().known_missing_older;
    expect(c.rows[0].ooo).toBe(1);
    const at = c.rows[0].oooAt!;
    expect(c.now - at).toBeGreaterThan(60_000); // the store's case is judged 100 s after its discard
    const recent = await judged({ ...c, now: at + 1_000 }, '43', LIVE(at + 1_000));
    expect(recent.d.flags).toEqual(['out_of_order_discarded']);
    expect(recent.d.state).toBe('ok');
    const later = await judged(c, '43', LIVE(c.now));
    expect(later.d.flags).toEqual([]);
    expect(later.d.state).toBe('ok');
  });

  it('replayed packets (received now, stamped yesterday) are the last session; a silent feed is the last session for another reason; a live one is live', async () => {
    const all = fixture();
    const replay = await judged(all.replay, '44');
    expect([replay.f.session().state, replay.f.session().reason, replay.f.badge().text]).toEqual(['last_session', 'event_times_old', 'last session prices']);
    const stopped = await judged(all.stopped, '46');
    expect([stopped.f.session().state, stopped.f.session().reason]).toEqual(['last_session', 'no_recent_packets']);
    const livefeed = await judged(all.live, '47');
    expect([livefeed.f.session().state, livefeed.f.badge().text]).toEqual(['live', 'live prices']);
  });
});

// ---- the page: session, feed badge and assembled chain together ----
const IST = 19_800;
const EXPIRY = '2026-11-03';
const ref = (token: string) => ({ token, symbol: `S${token}`, lot: 65 });
const strikes = (dropPutAt?: number): ChainStructure['strikes'] =>
  Array.from({ length: 21 }, (_, i) => 24_000 + i * 50).map((strike) => ({ strike, call: ref(`C${strike}`), put: strike === dropPutAt ? null : ref(`P${strike}`) }));
const structureOf = (dropPutAt?: number): ChainStructure => ({ underlying: 'NIFTY', expiry: EXPIRY, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: ref('F1'), strikes: strikes(dropPutAt) });
const apiOf = (dropPutAt?: number): OptionsApi => ({
  underlyings: async () => [{ underlying: 'NIFTY', segment: 'NFO', class: 'index', odd: false, expiries: 1, lot: 65, futures: true, options: true }],
  expiries: async () => [{ date: EXPIRY, futures: true, options: true, series: 'weekly' }],
  chain: async () => structureOf(dropPutAt),
  futures: async () => [{ expiry: EXPIRY, token: 'F1', symbol: 'NIFTYFUT', lot: 65, tick: 0.05, segment: 'NFO' }],
});

/** A server stand-in whose clock the test moves; `stamp(token)` gives each row's times. */
function server(clock: { ms: number }, stamp: (token: string) => Partial<LiveQuoteRow>) {
  const spot = 24_200;
  const fetcher = (async (url: string) => {
    const tokens = decodeURIComponent(url.split('tokens=')[1] ?? '').split(',').filter(Boolean);
    const rows = tokens.map((refText) => {
      const [seg, token] = refText.split(':');
      const strike = Number(token.slice(1));
      const intrinsic = token[0] === 'C' ? Math.max(0, spot - strike) : token[0] === 'P' ? Math.max(0, strike - spot) : 0;
      const last = token[0] === 'F' ? spot + 30 : Math.round((intrinsic + 40 * Math.exp(-Math.abs(spot - strike) / 120)) * 20) / 20;
      return { seg, token, last, bid: last - 0.05, ask: last + 0.05, volume: 1000, oi: 650_000, oip: -2, dly: 50, ooo: 0, ...stamp(token) } as LiveQuoteRow;
    });
    return { ok: true, status: 200, json: async () => ({ live: true, quotes: rows, now: clock.ms }) } as unknown as Response;
  }) as unknown as typeof fetch;
  return fetcher;
}
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await vi.advanceTimersByTimeAsync(0);
};
function make(clock: { ms: number }, stamp: (token: string) => Partial<LiveQuoteRow>, dropPutAt?: number) {
  const live = liveFeed({ fetcher: server(clock, stamp), spotOf: () => 24_200, now: () => clock.ms });
  const ctx = { indices: { get: (n: string) => (n === 'NIFTY 50' ? { l: 24_200 } : undefined) } } as unknown as AppContext;
  const view = new OptionsView(ctx, { api: apiOf(dropPutAt), live, storage: null, now: () => clock.ms / 1000 + 0 * IST });
  document.body.replaceChildren(view.element);
  return view;
}
const cell = (view: OptionsView, strike: number, side: 'call' | 'put', col: string) => view.element.querySelector<HTMLElement>(`tr[data-strike="${strike}"] td[data-side="${side}"][data-col="${col}"]`)!;
const badge = (view: OptionsView) => view.element.querySelector('.opt-synth')!;

describe('the page: the session, the feed badge and the assembled chain say the same thing', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('a replay (received now, stamped yesterday) is shown as the last session, its volatilities labelled observations, and an advancing stale stamp changes nothing', async () => {
    const clock = { ms: S };
    let t = S - DAY;
    const view = make(clock, () => ({ t, lt: t, r: clock.ms - 1_000, bs: clock.ms - 1_000 }));
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(badge(view).textContent).toBe('last session prices');
    expect(cell(view, 24_200, 'call', 'iv').classList.contains('q-labelled')).toBe(true);
    expect(cell(view, 24_200, 'call', 'iv').title).toMatch(/last-session/i);
    t += 1_000;
    await vi.advanceTimersByTimeAsync(2_100);
    expect(badge(view).textContent, 'a stale stamp that advances is still a replay').toBe('last session prices');
    view.hide();
  });

  it('a feed that stops is shown as the last session when its receipts go quiet, and the chain is not called fit', async () => {
    const clock = { ms: S };
    const view = make(clock, () => ({ t: S - 3_000, lt: S - 3_000, r: S - 2_000, bs: S - 2_000 })); // the same packets each time: nothing new arrives
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(badge(view).textContent).toBe('live prices');
    clock.ms = S + 10 * 60_000;
    await vi.advanceTimersByTimeAsync(2_100);
    expect(badge(view).textContent).toBe('last session prices');
    expect(cell(view, 24_200, 'call', 'iv').classList.contains('q-labelled')).toBe(true);
    view.hide();
  });

  it('a chain with no put at the money says so on the badge: labelled, naming the put, and the headline is still from the call', async () => {
    const clock = { ms: S };
    const view = make(clock, () => ({ t: S - 3_000, lt: S - 3_000, r: S - 2_000, bs: S - 2_000 }), 24_250); // the future is 24,230: the strike nearest it is 24,250
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(badge(view).textContent).toBe('quotes labelled');
    expect(badge(view).getAttribute('title')).toMatch(/put/i);
    expect(view.element.querySelector('[data-stat="ATM IV"] .v')!.textContent).not.toBe('–');
    view.hide();
  });
});
