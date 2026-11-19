// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AppContext } from '../src/panels/base';
import type { ChainStructure, OptionsApi } from '../src/options/api';
import { price, yearsToExpiry } from '../src/options/black76';
import { assemble } from '../src/options/assemble';
import type { Quote } from '../src/options/chain';
import { liveFeed, type LiveQuoteRow } from '../src/options/livefeed';
import { assessQuote, decideForward, POLICY_V3, qualitySummary, wording, type AssessContext, type QualityFlag, type SessionState } from '../src/options/quality';
import { decideSession } from '../src/options/session';
import { keyOf } from '../src/options/synthetic';
import { OptionsView } from '../src/options/view';

/** T3 review follow-up T3-C1: an invalid receipt time, an invalid packet time, a time the server refused and an unverified session each have a declared consequence, and the consequence is applied to
 *  the contract they belong to, whatever the rest of the chain says about the session. Recording a fault in `inputs.timeFaults` is not a consequence: these tests assert the flags, the state, the
 *  forward's labels, the required inputs and the visible badge. Controlled clocks and fixtures only; nothing here says anything about a real market. */

const MIN = 60_000;
const NOW = Date.UTC(2026, 9, 27, 11, 0) - 19_800_000; // ms
const live: SessionState = { state: 'live', referenceMs: NOW };

const quote = (o: Partial<Quote> = {}): Quote => ({ last: 100, bid: 99.5, ask: 100.5, volume: 10, oi: 100, oiChange: 0, time: NOW - 1_000, receivedAt: NOW - 1_000, bookSeenAt: NOW - 1_000, packetTime: NOW - 2_000, ...o });
const ctx = (o: Partial<AssessContext> = {}): AssessContext => ({ now: NOW, session: live, forward: null, ...o });
const flagsOf = (q: Partial<Quote>, c: Partial<AssessContext> = {}) => assessQuote(quote(q), ctx(c));

// ---- the four probes of the review, and the edges of the declared limits ----
describe('T3-C1: an invalid receipt or packet time has a consequence on its own contract', () => {
  it('the policy declares the consequences, and they are the ones applied (version 4)', () => {
    expect(POLICY_V3.version).toBe(4);
    const c = POLICY_V3.consequences as Record<string, string>;
    expect([c.invalid_receipt_time, c.invalid_packet_time, c.session_unverified]).toEqual(['label', 'label', 'label']);
    for (const f of ['invalid_receipt_time', 'invalid_packet_time', 'session_unverified']) expect(wording(f as QualityFlag), `${f} has words for the cell and the badge`).toMatch(/\w/);
  });

  it('probe 1: a receipt an hour ahead is labelled, not ok; the book is still read; the fault is recorded', () => {
    const d = flagsOf({ receivedAt: NOW + 60 * MIN });
    expect(d).toMatchObject({ state: 'labelled', flags: ['invalid_receipt_time'], labelled: ['invalid_receipt_time'], withheld: [], book: 'fresh', priceBasis: 'mid' });
    expect(d.inputs.receivedAt).toBeNull();
    expect(d.inputs.timeFaults).toEqual([{ field: 'receivedAt', value: NOW + 60 * MIN, reason: 'future', aheadMs: 60 * MIN, origin: 'page' }]);
  });

  it('probe 2: a packet time an hour ahead is labelled, not ok', () => {
    const d = flagsOf({ packetTime: NOW + 60 * MIN });
    expect(d).toMatchObject({ state: 'labelled', flags: ['invalid_packet_time'], withheld: [], book: 'fresh', priceBasis: 'mid' });
    expect(d.inputs.packetTime).toBeNull();
    expect(d.inputs.timeFaults).toEqual([{ field: 'packetTime', value: NOW + 60 * MIN, reason: 'future', aheadMs: 60 * MIN, origin: 'page' }]);
  });

  it('probe 3: a packet time the server refused (no packet time, tf says why) is labelled, and says the server refused it', () => {
    const d = flagsOf({ packetTime: undefined, timeFaults: [{ f: 't', v: NOW + 60 * MIN, why: 'future' }] });
    expect(d).toMatchObject({ state: 'labelled', flags: ['invalid_packet_time'], withheld: [] });
    expect(d.inputs.timeFaults).toEqual([{ field: 'packetTime', value: NOW + 60 * MIN, reason: 'future', origin: 'server' }]);
  });

  it('probe 4: a session that is unknown for want of receipts labels a valid book, as one with no exchange time already did; so does a feed that gave no session at all', () => {
    const noReceipts: SessionState = { state: 'unknown', referenceMs: null, reason: 'no_receipts' };
    expect(flagsOf({}, { session: noReceipts })).toMatchObject({ state: 'labelled', flags: ['session_unverified'], book: 'fresh' });
    const noEventTime: SessionState = { state: 'unknown', referenceMs: NOW, reason: 'no_event_time' };
    expect(flagsOf({}, { session: noEventTime }).flags).toEqual(['session_unverified']);
    const noSession: SessionState = { state: 'unknown', referenceMs: null };
    expect(flagsOf({}, { session: noSession }).flags, 'no session was assessed at all: it is not verified either').toEqual(['session_unverified']);
    expect(flagsOf({}, { session: live }).flags, 'a live session labels nothing').toEqual([]);
  });

  it('the limits are the declared ones: a receipt may be 5 s ahead, a packet time 60 s ahead, one millisecond more is invalid', () => {
    expect(flagsOf({ receivedAt: NOW + 5_000 }).flags).toEqual([]);
    expect(flagsOf({ receivedAt: NOW + 5_001 }).flags).toEqual(['invalid_receipt_time']);
    expect(flagsOf({ packetTime: NOW + 60_000 }).flags).toEqual([]);
    expect(flagsOf({ packetTime: NOW + 60_001 }).flags).toEqual(['invalid_packet_time']);
  });

  it('a time that is not a number or not an epoch time is invalid in the same way, and an absent one is not a fault', () => {
    expect(flagsOf({ receivedAt: Number.NaN }).flags).toEqual(['invalid_receipt_time']);
    expect(flagsOf({ receivedAt: 12_345 }).flags).toEqual(['invalid_receipt_time']);
    expect(flagsOf({ packetTime: Number.POSITIVE_INFINITY }).flags).toEqual(['invalid_packet_time']);
    expect(flagsOf({ packetTime: 5 }).flags).toEqual(['invalid_packet_time']);
    expect(flagsOf({ receivedAt: undefined, packetTime: undefined }), 'a server that sends neither is not accused of sending a bad one').toMatchObject({ state: 'ok', flags: [] });
  });

  it('a refusal the server made of any other field is a fault too, not a silent ok; a refused trade time keeps its own flag', () => {
    expect(flagsOf({ timeFaults: [{ f: 'x', v: 1, why: 'implausible' }] })).toMatchObject({ state: 'labelled', flags: ['invalid_packet_time'] });
    const trade = flagsOf({ time: undefined, timeFaults: [{ f: 'lt', v: 5, why: 'implausible' }] });
    expect([trade.state, trade.flags]).toEqual(['labelled', ['invalid_trade_time']]);
  });

  it('the consequences add up without hiding each other: an invalid book time still withholds, and the invalid receipt beside it is still said', () => {
    const d = flagsOf({ bookSeenAt: NOW + 60 * MIN, receivedAt: NOW + 60 * MIN, packetTime: NOW + 60 * MIN });
    expect(d.state).toBe('withheld');
    expect(d.withheld).toEqual(['invalid_book_time']);
    expect([...d.labelled].sort()).toEqual(['invalid_packet_time', 'invalid_receipt_time']);
    expect(d.inputs.timeFaults.map((f) => f.field).sort()).toEqual(['bookSeenAt', 'packetTime', 'receivedAt']);
  });

  it('an old trade beside a fresh book changes nothing (the earlier rule is kept): ok, no flag', () => {
    expect(flagsOf({ time: NOW - 120 * MIN })).toMatchObject({ state: 'ok', flags: [], book: 'fresh', trade: 'old' });
  });

  it('the forward passes an input\'s invalid receipt on as a label, never as nothing', () => {
    const f = decideForward({ now: NOW, session: live, source: 'future', value: 24_200, used: [quote({ receivedAt: NOW + 60 * MIN })], rejected: [] });
    expect(f).toMatchObject({ state: 'labelled', flags: ['forward_input_labelled'], inputFlags: ['invalid_receipt_time'] });
    expect(f.inputs[0]).toMatchObject({ state: 'labelled', flags: ['invalid_receipt_time'] });
    const o = assessQuote(quote(), ctx({ forward: f }));
    expect([o.state, o.flags]).toEqual(['labelled', ['forward_input_labelled']]);
  });
});

// ---- a chain in which another valid contract makes the session live ----
const EXPIRY = '2026-11-03';
const NOW_S = NOW / 1000;
const T = yearsToExpiry(EXPIRY, NOW_S);
const ref = (token: string) => ({ token, symbol: `S${token}`, lot: 65 });
const STRIKES = [23_800, 24_000, 24_200, 24_400, 24_600];
const structure = (o: { future?: boolean } = {}): ChainStructure => ({
  underlying: 'NIFTY', expiry: EXPIRY, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: o.future === false ? null : ref('F'),
  strikes: STRIKES.map((strike) => ({ strike, call: ref(`C${strike}`), put: ref(`P${strike}`) })),
});
const FORWARD = 24_200;
const px = (kind: 'call' | 'put', strike: number): number => Math.round(price(kind, FORWARD, strike, T, 0.15) * 20) / 20;
const side = (kind: 'call' | 'put', strike: number, o: Partial<Quote> = {}): Quote => {
  const p = px(kind, strike);
  return quote({ last: p, bid: Math.max(0.05, p - 0.5), ask: p + 0.5, ...o });
};
type Over = Partial<Record<string, Partial<Quote>>>;
function chain(over: Over = {}, withFuture = true): Map<string, Quote> {
  const quotes = new Map<string, Quote>();
  for (const strike of STRIKES) {
    quotes.set(keyOf({ underlying: 'NIFTY', expiry: EXPIRY, kind: 'call', strike }), side('call', strike, over[`C${strike}`]));
    quotes.set(keyOf({ underlying: 'NIFTY', expiry: EXPIRY, kind: 'put', strike }), side('put', strike, over[`P${strike}`]));
  }
  if (withFuture) quotes.set(keyOf({ underlying: 'NIFTY', expiry: EXPIRY, kind: 'future', strike: 0 }), quote({ last: FORWARD, bid: FORWARD - 0.5, ask: FORWARD + 0.5, ...over.F }));
  return quotes;
}
/** The session the page would reach: each contract's newest packet (its receipt and the packet time it carries) goes to the session policy, so a valid contract can make it live. */
function sessionOf(quotes: Map<string, Quote>): SessionState {
  return decideSession([...quotes.values()].map((q) => ({ receivedAt: q.receivedAt, eventTime: q.packetTime })), NOW);
}
const view = (quotes: Map<string, Quote>, s = structure(), session: SessionState = sessionOf(quotes)) => assemble(s, quotes, 24_190, NOW_S, 0.07, { session, nowMs: NOW });
const rowAt = (v: ReturnType<typeof view>, strike: number) => v.rows.find((r) => r.strike === strike)!;
const requiredOf = (v: ReturnType<typeof view>) => v.quality.required.map((r) => `${r.input}:${r.state}:${r.flags.join('+')}`);

describe('T3-C1 in a chain: a valid contract makes the session live, and the affected contract\'s fault stays visible', () => {
  it('the chain with nothing wrong is live and fit (the control)', () => {
    const v = view(chain());
    expect([v.quality.session.state, v.quality.status]).toEqual(['live', 'ok']);
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:ok:', 'atm_put:ok:']);
  });

  it('the at-the-money call has a packet time an hour ahead: the session is live, the call is labelled, the chain says labelled, the put is untouched', () => {
    const quotes = chain({ C24200: { packetTime: NOW + 60 * MIN } });
    const v = view(quotes);
    expect(v.quality.session.state, 'the other contracts make the aggregate live').toBe('live');
    expect(rowAt(v, 24_200).call!.quality).toMatchObject({ state: 'labelled', flags: ['invalid_packet_time'] });
    expect(rowAt(v, 24_200).call!.iv, 'a labelled side keeps its volatility').not.toBeNull();
    expect(rowAt(v, 24_200).put!.quality).toMatchObject({ state: 'ok', flags: [] });
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:labelled:invalid_packet_time', 'atm_put:ok:']);
    expect([v.quality.status, v.quality.worst?.input]).toEqual(['labelled', 'atm_call']);
    expect(qualitySummary(v.quality)).toMatch(/at-the-money call \(labelled\): .*packet time invalid/i);
  });

  it('the at-the-money put\'s packet time was refused by the server: labelled the same way, with the server named in the record', () => {
    const quotes = chain({ P24200: { packetTime: undefined, timeFaults: [{ f: 't', v: NOW + 60 * MIN, why: 'future' }] } });
    const v = view(quotes);
    expect(v.quality.session.state).toBe('live');
    expect(rowAt(v, 24_200).put!.quality).toMatchObject({ state: 'labelled', flags: ['invalid_packet_time'] });
    expect(rowAt(v, 24_200).put!.quality.inputs.timeFaults).toEqual([{ field: 'packetTime', value: NOW + 60 * MIN, reason: 'future', origin: 'server' }]);
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:ok:', 'atm_put:labelled:invalid_packet_time']);
    expect(v.quality.status).toBe('labelled');
  });

  it('the at-the-money call has a receipt an hour ahead: the receipt is no evidence for the session, and the call is labelled', () => {
    const v = view(chain({ C24200: { receivedAt: NOW + 60 * MIN } }));
    expect(v.quality.session.state).toBe('live');
    expect(requiredOf(v)).toEqual(['forward:ok:', 'atm_call:labelled:invalid_receipt_time', 'atm_put:ok:']);
    expect(v.quality.status).toBe('labelled');
  });

  it('the future has a receipt an hour ahead: the forward carries the label and every option read against it says so', () => {
    const v = view(chain({ F: { receivedAt: NOW + 60 * MIN } }));
    expect(v.forwardFrom).toBe('future');
    expect(v.quality.forward).toMatchObject({ state: 'labelled', flags: ['forward_input_labelled'], inputFlags: ['invalid_receipt_time'] });
    expect(v.quality.forward.inputs[0]).toMatchObject({ role: 'future', state: 'labelled', flags: ['invalid_receipt_time'] });
    expect(requiredOf(v)).toEqual(['forward:labelled:forward_input_labelled', 'atm_call:labelled:forward_input_labelled', 'atm_put:labelled:forward_input_labelled']);
    expect([v.quality.status, v.quality.worst?.input]).toEqual(['labelled', 'forward']);
    expect(qualitySummary(v.quality)).toMatch(/forward's inputs are labelled: receipt time invalid/i);
  });

  it('a parity forward (no future) read from a contract whose packet time is invalid carries that label', () => {
    const v = view(chain({ C24200: { packetTime: NOW + 60 * MIN } }, false), structure({ future: false }));
    expect(v.forwardFrom).toBe('parity');
    expect(v.quality.forward.inputs.find((i) => i.role === 'call' && i.strike === 24_200)?.flags).toEqual(['invalid_packet_time']);
    expect(v.quality.forward.inputFlags).toEqual(['invalid_packet_time']);
    expect(v.quality.forward.state).toBe('labelled');
    expect(v.quality.status).toBe('labelled');
  });

  it('a wing contract with an invalid receipt is not a required input: the chain stays fit, but the side is labelled and counted, never an unmarked ok', () => {
    const v = view(chain({ C23800: { receivedAt: NOW + 60 * MIN } }));
    expect(rowAt(v, 23_800).call!.quality).toMatchObject({ state: 'labelled', flags: ['invalid_receipt_time'] });
    expect(rowAt(v, 23_800).call!.ivNote).toMatch(/labelled: receipt time invalid/i);
    expect(v.quality.status).toBe('ok');
    expect(v.quality.rows).toMatchObject({ labelled: 1, withheld: 0 });
    expect(qualitySummary(v.quality)).toMatch(/1 labelled/);
  });

  it('a session that is unknown labels every contract, whatever the book says', () => {
    const quotes = chain({}, true);
    const v = view(quotes, structure(), { state: 'unknown', referenceMs: null, reason: 'no_receipts' });
    expect(requiredOf(v)).toEqual(['forward:labelled:session_unverified', 'atm_call:labelled:session_unverified', 'atm_put:labelled:session_unverified']);
    expect(v.quality.status).toBe('labelled');
  });
});

// ---- the page: the same, through the live feed and the visible badge ----
const server = (patch: (token: string, row: LiveQuoteRow) => LiveQuoteRow) => {
  const fetcher = (async (url: string) => {
    const tokens = decodeURIComponent(url.split('tokens=')[1] ?? '').split(',').filter(Boolean);
    const rows: LiveQuoteRow[] = tokens.map((r) => {
      const [seg, token] = r.split(':');
      const kind = token[0];
      const strike = Number(token.slice(1));
      const intrinsic = kind === 'C' ? Math.max(0, 24_200 - strike) : kind === 'P' ? Math.max(0, strike - 24_200) : 0;
      const last = kind === 'F' ? 24_230 : Math.round((intrinsic + 40 * Math.exp(-Math.abs(24_200 - strike) / 120)) * 20) / 20;
      const rcv = NOW - 5_000;
      const t = rcv - 1_000;
      return patch(token, { seg, token, last, bid: last - 0.05, ask: last + 0.05, volume: 1000, oi: 650_000, oip: -2, t, lt: t, r: rcv, bs: rcv, dly: 50, ooo: 0 });
    });
    return { ok: true, status: 200, json: async () => ({ live: true, quotes: rows, now: NOW }) } as unknown as Response;
  }) as unknown as typeof fetch;
  return fetcher;
};
const structureOf = (): ChainStructure => ({
  underlying: 'NIFTY', expiry: EXPIRY, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: ref('F1'),
  strikes: Array.from({ length: 21 }, (_, i) => 24_000 + i * 50).map((strike) => ({ strike, call: ref(`C${strike}`), put: ref(`P${strike}`) })),
});
const api = (): OptionsApi => ({
  underlyings: async () => [{ underlying: 'NIFTY', segment: 'NFO', class: 'index', odd: false, expiries: 1, lot: 65, futures: true, options: true }],
  expiries: async () => [{ date: EXPIRY, futures: true, options: true, series: 'weekly' }],
  chain: async () => structureOf(),
  futures: async () => [{ expiry: EXPIRY, token: 'F1', symbol: 'NIFTYFUT', lot: 65, tick: 0.05, segment: 'NFO' }],
});
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await vi.advanceTimersByTimeAsync(0);
};
async function page(patch: (token: string, row: LiveQuoteRow) => LiveQuoteRow) {
  const feed = liveFeed({ fetcher: server(patch), spotOf: () => 24_200, now: () => NOW });
  const ctxApp = { indices: { get: (n: string) => (n === 'NIFTY 50' ? { l: 24_200 } : undefined) } } as unknown as AppContext;
  const v = new OptionsView(ctxApp, { api: api(), live: feed, storage: null, now: () => NOW_S });
  document.body.replaceChildren(v.element);
  v.show();
  await flush();
  await vi.advanceTimersByTimeAsync(3_000);
  return v;
}
const cell = (v: OptionsView, strike: number, sideName: 'call' | 'put', col: string) => v.element.querySelector<HTMLElement>(`tr[data-strike="${strike}"] td[data-side="${sideName}"][data-col="${col}"]`)!;
const badge = (v: OptionsView) => v.element.querySelector('.opt-synth')!;

describe('T3-C1 on the page: other contracts make the feed live, and the badge still shows the affected contract', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('control: nothing wrong, live prices', async () => {
    const v = await page((_t, row) => row);
    expect(badge(v).textContent).toBe('live prices');
    v.hide();
  });

  it('the at-the-money call carries a packet time an hour ahead: the badge says quotes labelled, with the reason, and the cell is marked while its volatility stays', async () => {
    // (the page's future trades at 24,230, so the at-the-money strike of its chain, which has a strike every 50, is 24,250)
    const v = await page((token, row) => (token === 'C24250' ? { ...row, t: NOW + 60 * MIN } : row));
    expect(badge(v).textContent).toBe('quotes labelled');
    expect(badge(v).getAttribute('data-kind')).toBe('quality');
    expect(badge(v).getAttribute('title')).toMatch(/at-the-money call \(labelled\): .*packet time invalid/i);
    expect(cell(v, 24_250, 'call', 'iv').textContent).not.toBe('–');
    expect(cell(v, 24_250, 'call', 'iv').classList.contains('q-labelled')).toBe(true);
    expect(cell(v, 24_250, 'call', 'iv').title).toMatch(/packet time invalid/i);
    expect(cell(v, 24_250, 'put', 'iv').classList.contains('q-labelled')).toBe(false);
    v.hide();
  });

  it('the server refused the at-the-money put\'s packet time (tf): the badge says quotes labelled', async () => {
    const v = await page((token, row) => (token === 'P24250' ? { ...row, t: null, tf: [{ f: 't', v: NOW + 60 * MIN, why: 'future' }] } : row));
    expect(badge(v).textContent).toBe('quotes labelled');
    expect(badge(v).getAttribute('title')).toMatch(/at-the-money put \(labelled\): .*packet time invalid/i);
    v.hide();
  });

  it('the future\'s receipt is an hour ahead: the badge says quotes labelled, and the forward\'s labels are in its title', async () => {
    const v = await page((token, row) => (token === 'F1' ? { ...row, r: NOW + 60 * MIN } : row));
    expect(badge(v).textContent).toBe('quotes labelled');
    expect(badge(v).getAttribute('title')).toMatch(/forward's inputs are labelled: receipt time invalid/i);
    v.hide();
  });

  it('a wing\'s packet time an hour ahead: the badge stays live (it is not a required input), the wing\'s own cell is labelled', async () => {
    const v = await page((token, row) => (token === 'P24100' ? { ...row, t: NOW + 60 * MIN } : row));
    expect(badge(v).textContent).toBe('live prices');
    expect(cell(v, 24_100, 'put', 'iv').textContent).not.toBe('–');
    expect(cell(v, 24_100, 'put', 'iv').classList.contains('q-labelled')).toBe(true);
    expect(cell(v, 24_100, 'put', 'iv').title).toMatch(/packet time invalid/i);
    v.hide();
  });
});
