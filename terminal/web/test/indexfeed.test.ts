import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Quote } from '../src/feed';
import { IndexFeed, POLL_OPEN_MS, POLL_RETRY_MS, POLL_SHUT_MS, type IndexSnapshot } from '../src/indices';

const row = (s: string, l: number) => ({ s, l, pc: null, ch: 1, pct: 0.5, o: null, h: null, lo: null, h52: null, l52: null, pe: null, pb: null, dy: null, w1: null, m1: null, y1: null, adv: null, dec: null });
const snap = (open: boolean): IndexSnapshot => ({ as_of: '16-Oct-2026 15:30', open, stale_s: 2, rows: [row('NIFTY 50', 23000), row('INDIA VIX', 14)] });

function server(replies: (IndexSnapshot | number)[]) {
  const calls: number[] = [];
  const f = (async () => {
    const next = replies.length > 1 ? replies.shift()! : replies[0];
    calls.push(Date.now());
    return typeof next === 'number' ? new Response('{}', { status: next }) : new Response(JSON.stringify(next));
  }) as unknown as typeof fetch;
  return { f, calls };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('IndexFeed', () => {
  it('finds an index by name ignoring case and tells listeners', async () => {
    const feed = new IndexFeed(server([snap(true)]).f);
    let heard = 0;
    feed.subscribe(() => heard++);
    expect(await feed.refresh()).toBe(true);
    expect(feed.get(' nifty 50 ')?.l).toBe(23000);
    expect(feed.has('INDIA VIX')).toBe(true);
    expect(feed.get('NIFTY IT')).toBeUndefined();
    expect(feed.unavailable).toBe(false);
    expect(feed.asOf).toBe('16-Oct-2026 15:30');
    expect(heard).toBe(1);
  });

  it('keeps the last values when a refresh fails and says they are old', async () => {
    const feed = new IndexFeed(server([snap(true), 503]).f);
    await feed.refresh();
    expect(await feed.refresh()).toBe(false);
    expect(feed.unavailable).toBe(true);
    expect(feed.get('NIFTY 50')?.l).toBe(23000);
  });

  it('polls every five seconds while trading, once a minute when shut, and soon again after a failure', async () => {
    const open = server([snap(true)]);
    const feed = new IndexFeed(open.f);
    feed.start();
    await vi.advanceTimersByTimeAsync(1);
    expect(open.calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(POLL_OPEN_MS);
    expect(open.calls).toHaveLength(2);
    feed.stop();

    const shut = server([snap(false)]);
    const quiet = new IndexFeed(shut.f);
    quiet.start();
    await vi.advanceTimersByTimeAsync(POLL_OPEN_MS * 3);
    expect(shut.calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(POLL_SHUT_MS);
    expect(shut.calls).toHaveLength(2);
    quiet.stop();

    const down = server([503]);
    const retry = new IndexFeed(down.f);
    retry.start();
    await vi.advanceTimersByTimeAsync(POLL_RETRY_MS + 5);
    expect(down.calls).toHaveLength(2);
    retry.stop();
    await vi.advanceTimersByTimeAsync(POLL_SHUT_MS);
    expect(down.calls).toHaveLength(2); // stopped means stopped
  });
});

describe('IndexFeed with live quotes from the stream', () => {
  const quote = (s: string, l: number, pc: number, t: number | null, src: 'stream' | 'poll' = 'stream'): Quote => ({ s, l, pc, o: l - 5, h: l + 10, lo: l - 20, v: null, avg: null, h52: null, l52: null, lb: null, ub: null, t, b: [], a: [], src });
  function live(now = 1_000_000) {
    const quotes = new Map<string, Quote>();
    const listeners = new Set<(q: Quote[]) => void>();
    const needs: [string, string[]][] = [];
    const source = { quotes, onQuotes: (fn: (q: Quote[]) => void) => (listeners.add(fn), () => listeners.delete(fn)), need: (o: string, s: string[]) => void needs.push([o, s]) };
    const push = (q: Quote) => {
      quotes.set(q.s, q);
      listeners.forEach((fn) => fn([q]));
    };
    return { source, push, needs, now };
  }

  it('asks the server to stream the indices it shows, and shows the streamed price with the change from the stream\'s previous close', async () => {
    const feed = new IndexFeed(server([snap(true)]).f);
    await feed.refresh();
    const l = live();
    feed.attachLive(l.source, ['NIFTY 50', 'INDIA VIX'], () => l.now);
    expect(l.needs).toEqual([['index-strip', ['NIFTY 50', 'INDIA VIX']]]);
    expect(feed.get('NIFTY 50')!.l).toBe(23000); // nothing streamed yet: the snapshot
    l.push(quote('NIFTY 50', 23100, 23000, l.now - 1_000));
    const ix = feed.get('nifty 50')!;
    expect([ix.l, ix.pc, ix.ch]).toEqual([23100, 23000, 100]);
    expect(ix.pct).toBeCloseTo(0.4348, 3);
    expect([ix.o, ix.h, ix.lo]).toEqual([23095, 23110, 23080]);
  });

  it('keeps what only the snapshot has (P/E and the rest) beside the streamed price', async () => {
    const snapshot = snap(true);
    snapshot.rows[0] = { ...snapshot.rows[0], pe: 19.8, h52: 26000 };
    const feed = new IndexFeed(server([snapshot]).f);
    await feed.refresh();
    const l = live();
    feed.attachLive(l.source, [], () => l.now);
    l.push(quote('NIFTY 50', 23100, 23000, l.now));
    expect([feed.get('NIFTY 50')!.pe, feed.get('NIFTY 50')!.h52]).toEqual([19.8, 26000]);
  });

  it('shows an index the snapshot has not got, from the stream alone, and says it has it', () => {
    const feed = new IndexFeed(server([snap(true)]).f);
    const l = live();
    feed.attachLive(l.source, [], () => l.now);
    expect(feed.has('NIFTY IT')).toBe(false);
    l.push(quote('NIFTY IT', 38000, 37900, l.now));
    expect(feed.has('NIFTY IT')).toBe(true);
    expect(feed.get('NIFTY IT')!.l).toBe(38000);
  });

  it('falls back to the snapshot when the streamed quote is old (the market is shut), has no previous close, has no time, or is a polled one', async () => {
    const feed = new IndexFeed(server([snap(true)]).f);
    await feed.refresh();
    const l = live();
    feed.attachLive(l.source, [], () => l.now);
    for (const q of [quote('NIFTY 50', 23100, 23000, l.now - 10 * 60_000), quote('NIFTY 50', 23100, 0, l.now), quote('NIFTY 50', 23100, 23000, null), quote('NIFTY 50', 23100, 23000, l.now, 'poll')]) {
      l.push(q);
      expect(feed.get('NIFTY 50')!.l).toBe(23000);
    }
  });

  it('tells listeners of live changes at most a few times a second, and not about a stock', async () => {
    const feed = new IndexFeed(server([snap(true)]).f);
    await feed.refresh();
    const l = live();
    feed.attachLive(l.source, [], () => l.now);
    let heard = 0;
    feed.subscribe(() => heard++);
    for (let i = 0; i < 10; i++) l.push(quote('NIFTY 50', 23100 + i, 23000, l.now));
    expect(heard).toBe(0);
    await vi.advanceTimersByTimeAsync(450);
    expect(heard).toBe(1);
    l.push(quote('SBIN', 800, 790, l.now));
    await vi.advanceTimersByTimeAsync(450);
    expect(heard).toBe(1);
  });
});
