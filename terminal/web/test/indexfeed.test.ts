import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
