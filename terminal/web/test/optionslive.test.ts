// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AppContext } from '../src/panels/base';
import type { ChainStructure, OptionsApi } from '../src/options/api';
import { liveFeed, oiChange, quoteOf, type LiveQuoteRow } from '../src/options/livefeed';
import { keyOf, type ContractKey } from '../src/options/synthetic';
import { OptionsView } from '../src/options/view';
import { compact } from '../src/format';

const IST = 19_800;
const nowS = Date.UTC(2026, 9, 27, 11, 0) / 1000 - IST;
const nowMs = nowS * 1000;
const expiry = '2026-11-03';
const ref = (token: string) => ({ token, symbol: `S${token}`, lot: 65 });

const structure = (): ChainStructure => ({
  underlying: 'NIFTY', expiry, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: ref('F1'),
  strikes: Array.from({ length: 21 }, (_, i) => 24_000 + i * 50).map((strike) => ({ strike, call: ref(`C${strike}`), put: ref(`P${strike}`) })),
});

function api(): OptionsApi {
  return {
    underlyings: async () => [{ underlying: 'NIFTY', segment: 'NFO', class: 'index', odd: false, expiries: 1, lot: 65, futures: true, options: true }],
    expiries: async () => [{ date: expiry, futures: true, options: true, series: 'weekly' }],
    chain: async () => structure(),
    futures: async () => [{ expiry, token: 'F1', symbol: 'NIFTYFUT', lot: 65, tick: 0.05, segment: 'NFO' }],
  };
}

/** A server stand-in: answers the quotes route from a price level it can move, and counts the requests. */
function server(opts: { live?: boolean | (() => boolean); at?: number | (() => number); level?: () => number } = {}) {
  const requests: string[] = [];
  const level = opts.level ?? (() => 24_200);
  const fetcher = (async (url: string) => {
    requests.push(url);
    const tokens = decodeURIComponent(url.split('tokens=')[1] ?? '').split(',').filter(Boolean);
    const spot = level();
    const isLive = typeof opts.live === 'function' ? opts.live() : opts.live ?? true;
    const rows: LiveQuoteRow[] = tokens.map((ref) => {
      const [seg, token] = ref.split(':');
      const kind = token[0];
      const strike = Number(token.slice(1));
      const intrinsic = kind === 'C' ? Math.max(0, spot - strike) : kind === 'P' ? Math.max(0, strike - spot) : 0;
      const last = kind === 'F' ? spot + 30 : Math.round((intrinsic + 40 * Math.exp(-Math.abs(spot - strike) / 120)) * 20) / 20;
      return { seg, token, last, bid: last - 0.05, ask: last + 0.05, volume: 1000, oi: 650_000, oip: -2, t: typeof opts.at === 'function' ? opts.at() : opts.at ?? nowMs - 5_000 };
    });
    return { ok: true, status: 200, json: async () => ({ live: isLive, quotes: isLive ? rows : [] }) } as unknown as Response;
  }) as unknown as typeof fetch;
  return { fetcher, requests };
}

const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await vi.advanceTimersByTimeAsync(0);
};

function make(s = server()) {
  const live = liveFeed({ fetcher: s.fetcher, spotOf: () => 24_200, now: () => nowMs });
  const ctx = { indices: { get: (n: string) => (n === 'NIFTY 50' ? { l: 24_200 } : undefined) } } as unknown as AppContext;
  const view = new OptionsView(ctx, { api: api(), live, storage: null, now: () => nowS });
  document.body.replaceChildren(view.element);
  return { view, s };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('the live feed', () => {
  it('turns the change in open interest percent into contracts', () => {
    expect(oiChange(1_250_000, -3.5)).toBe(-45_337);
    expect(oiChange(1_100_000, 10)).toBe(100_000);
    expect(oiChange(0, 5)).toBe(0);
    expect(oiChange(100, -100)).toBe(0);
    expect(oiChange(100, Number.NaN)).toBe(0);
  });

  it('maps a server row to a quote', () => {
    const q = quoteOf({ seg: 'NFO', token: '1', last: 10, bid: 9.9, ask: 10.1, volume: 7, oi: 200, oip: 100, t: 5 });
    expect(q).toEqual({ last: 10, bid: 9.9, ask: 10.1, volume: 7, oi: 200, oiChange: 100, time: 5 });
    // NSE and BSE derivatives give open interest and volume as a quantity (a whole number of lots); the page shows lots. MCX gives lots already.
    const nifty = quoteOf({ seg: 'NFO', token: '1', last: 10, bid: null, ask: null, volume: 596_245, oi: 542_685, oip: -2, t: 5 }, 65);
    expect([nifty.oi, nifty.volume]).toEqual([8349, 9173]);
    expect(nifty.oiChange).toBe(oiChange(8349, -2));
    const crude = quoteOf({ seg: 'MCX', token: '1', last: 10, bid: null, ask: null, volume: 934, oi: 673, oip: 0, t: 5 }, 100);
    expect([crude.oi, crude.volume]).toEqual([673, 934]);
    expect(quoteOf({ seg: 'NFO', token: '1', last: null, bid: null, ask: null, volume: 0, oi: 0, oip: 0, t: null }).time).toBeUndefined();
  });

  it('asks for contracts by segment and token, a hundred at a time, and skips keys with no reference', async () => {
    const s = server();
    const feed = liveFeed({ fetcher: s.fetcher, spotOf: () => null, now: () => nowMs });
    const keys: ContractKey[] = Array.from({ length: 150 }, (_, i) => ({ underlying: 'NIFTY', expiry, kind: 'call' as const, strike: 20_000 + i * 50, ref: { segment: 'NFO', token: `C${20_000 + i * 50}`, lot: 1 } }));
    keys.push({ underlying: 'NIFTY', expiry, kind: 'put', strike: 1 });
    const got = await feed.quotes(keys);
    expect(s.requests.length).toBe(2);
    expect(decodeURIComponent(s.requests[0]).split('tokens=')[1].split(',').length).toBe(100);
    expect(got.size).toBe(150);
    expect(got.get(keyOf(keys[0]))!.oi).toBe(650_000);
    expect(got.get(keyOf(keys[0]))!.oiChange).toBe(-13_265);
    expect(await feed.probe()).toBe(true);
  });

  it('is not live when the server says so or does not answer', async () => {
    expect(await liveFeed({ fetcher: server({ live: false }).fetcher, spotOf: () => null }).probe()).toBe(false);
    const broken = (async () => {
      throw new Error('down');
    }) as unknown as typeof fetch;
    expect(await liveFeed({ fetcher: broken, spotOf: () => null }).probe()).toBe(false);
  });
});

describe('the Options page on live prices', () => {
  it('uses them when the server has a stream: asks for the chain contracts, shows what came back and says the prices are live', async () => {
    const { view, s } = make();
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('live prices');
    expect(s.requests.some((r) => decodeURIComponent(r).includes('NFO:C24200'))).toBe(true);
    expect(s.requests.some((r) => decodeURIComponent(r).includes('NFO:F1'))).toBe(true);
    const atm = view.element.querySelector('tr[data-strike="24200"]')!;
    expect(atm.querySelector('td[data-side="call"][data-col="ltp"]')!.textContent).toBe('40.00');
    expect(atm.querySelector('td[data-side="call"][data-col="oi"]')!.textContent).toContain(compact(10_000)); // 650,000 units of 65 is 10,000 lots
    expect(view.element.querySelector('[data-stat="Forward"] .v')!.textContent).toBe('24,230.00'); // from the live future
    view.hide();
  });

  it('says last session prices when the newest exchange time is old', async () => {
    const { view } = make(server({ at: nowMs - 6 * 3_600_000 }));
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    const badge = view.element.querySelector('.opt-synth')!;
    expect(badge.textContent).toBe('last session prices');
    expect(badge.getAttribute('data-kind')).toBe('last');
    view.hide();
  });

  it('says live prices when the exchange times keep moving even if the exchange clock disagrees with this one', async () => {
    let t = nowMs - 6 * 3_600_000; // a clock hours away from ours
    const { view } = make(server({ at: () => t }));
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('last session prices'); // seen once, not moving
    t += 4_000;
    await vi.advanceTimersByTimeAsync(2_100);
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('live prices');
    view.hide();
  });

  it('keeps asking while it is open, shows the new prices and keeps the reader\'s place, and stops when hidden', async () => {
    let level = 24_200;
    const { view, s } = make(server({ level: () => level }));
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    const box = view.element.querySelector<HTMLElement>('.opt-chainbox')!;
    box.scrollTop = 123;
    const before = s.requests.length;
    level = 24_300;
    await vi.advanceTimersByTimeAsync(2_100);
    expect(s.requests.length).toBeGreaterThan(before);
    const atm = view.element.querySelector('tr[data-strike="24300"]')!;
    expect(atm.querySelector('td[data-side="call"][data-col="ltp"]')!.textContent).toBe('40.00');
    expect(box.scrollTop).toBe(123);
    view.hide();
    const after = s.requests.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(s.requests.length).toBe(after);
  });

  it('keeps the last prices and says there are no live prices when the stream drops, and recovers when it is back', async () => {
    let up = true;
    const { view } = make(server({ live: () => up }));
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    const ltp = () => view.element.querySelector('tr[data-strike="24200"] td[data-side="call"][data-col="ltp"]')!.textContent;
    const futures = () => view.element.querySelector('.opt-futtable tr[data-expiry]')?.textContent;
    expect(ltp()).toBe('40.00');
    const futuresBefore = futures();
    up = false;
    await vi.advanceTimersByTimeAsync(4_100);
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('no live prices');
    expect(ltp()).toBe('40.00'); // not blanked
    expect(futures()).toBe(futuresBefore);
    up = true;
    await vi.advanceTimersByTimeAsync(2_100);
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('live prices');
    expect(ltp()).toBe('40.00');
    view.hide();
  });

  it('stays on synthetic prices when the server has no stream', async () => {
    const { view, s } = make(server({ live: false }));
    view.show();
    await flush();
    await vi.advanceTimersByTimeAsync(3_000);
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('synthetic prices');
    expect(s.requests.every((r) => decodeURIComponent(r).endsWith('tokens='))).toBe(true); // only the probe
    expect(view.element.querySelectorAll('tr.opt-row').length).toBeGreaterThan(5);
    view.hide();
  });
});
