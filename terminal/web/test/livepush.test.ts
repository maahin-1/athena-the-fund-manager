import { describe, expect, it } from 'vitest';
import { AGE_LIVE_MS, Feed, type DerivRow, type SocketLike } from '../src/feed';
import { liveFeed, PUSH_KEEP_MS } from '../src/options/livefeed';
import { keyOf, type ContractKey } from '../src/options/synthetic';

function socket() {
  const sent: unknown[] = [];
  const s: SocketLike & { sent: unknown[]; open(): void; push(m: object): void } = {
    sent,
    send: (d) => void sent.push(JSON.parse(d)),
    close: () => {},
    onopen: null,
    onclose: null,
    onmessage: null,
    open: () => s.onopen?.({}),
    push: (m) => s.onmessage?.({ data: JSON.stringify(m) }),
  };
  return s;
}

function connected() {
  const s = socket();
  const feed = new Feed('ws://x', () => s, () => 0);
  feed.connect();
  s.open();
  s.push({ type: 'status', feed: 'on' });
  return { s, feed };
}

const row = (token: string, last: number, t: number | null = Date.now()): DerivRow => ({ seg: 'NFO', token, last, bid: last - 1, ask: last + 1, volume: 650, oi: 1300, oip: 0, t });

describe('futures and options on the socket', () => {
  it('sends the union of what panels name, once per change, and again after a reconnect', () => {
    const { s, feed } = connected();
    feed.needDerivs('a', ['NFO:2', 'NFO:1']);
    feed.needDerivs('b', ['NFO:1']);
    feed.needDerivs('b', ['NFO:1']);
    const lists = () => s.sent.filter((m) => (m as { type: string }).type === 'derivs').map((m) => (m as { keys: string }).keys);
    expect(lists()).toEqual(['NFO:1,NFO:2']);
    feed.needDerivs('a', []);
    expect(lists()).toEqual(['NFO:1,NFO:2', 'NFO:1']);
  });

  it('keeps each pushed row and tells the listeners', () => {
    const { s, feed } = connected();
    const heard: string[] = [];
    feed.onDerivs((rows) => heard.push(...rows.map((r) => r.token)));
    s.push({ type: 'd', sm: Date.now(), d: [row('1', 100), row('2', 50)] });
    expect(feed.derivs.get('NFO:1')?.last).toBe(100);
    expect(heard).toEqual(['1', '2']);
    expect(feed.pushing).toBe(true);
  });

  it('measures how old a streamed tick is when it arrives, and ignores the last session', () => {
    const { s, feed } = connected();
    s.push({ type: 'd', sm: Date.now(), d: [row('1', 100, Date.now() - AGE_LIVE_MS * 10)] });
    expect(feed.status.ageMs).toBeNull();
    s.push({ type: 'd', sm: Date.now(), d: [row('1', 101, Date.now() - 40)] });
    expect(feed.status.ageMs).toBeGreaterThanOrEqual(40);
    expect(feed.status.ageMs).toBeLessThan(1000);
  });
});

describe('the options feed with a push source', () => {
  const key = (token: string): ContractKey => ({ underlying: 'NIFTY', expiry: '2026-11-03', kind: 'call', strike: 24_000, ref: { segment: 'NFO', token, lot: 65 } });

  it('reads pushed prices without asking the quotes route, names what it was asked for, and lets go when released', async () => {
    const { s, feed } = connected();
    const urls: string[] = [];
    let t = 1_000;
    const live = liveFeed({ spotOf: () => 24_000, push: feed, now: () => t, fetcher: (async (u: string) => (urls.push(u), new Response(JSON.stringify({ live: true, quotes: [] })))) as typeof fetch });
    expect(await live.probe()).toBe(true);
    let updates = 0;
    live.onUpdate!(() => updates++);
    expect((await live.quotes([key('7')])).size).toBe(0); // nothing pushed yet
    expect(feed.derivs.size).toBe(0);
    s.push({ type: 'd', sm: Date.now(), d: [row('7', 120)] });
    s.push({ type: 'd', sm: Date.now(), d: [row('8', 1)] }); // not asked for
    expect(updates).toBe(1);
    const q = await live.quotes([key('7')]);
    expect(q.get(keyOf(key('7')))?.last).toBe(120);
    expect(urls).toEqual(['/api/derivs/quotes?tokens=']); // only the probe went to the route
    const named = () => s.sent.filter((m) => (m as { type: string }).type === 'derivs').map((m) => (m as { keys: string }).keys);
    expect(named()).toEqual(['NFO:7']);
    t += PUSH_KEEP_MS + 1;
    await live.quotes([key('9')]); // an old ask lapses when another is made
    expect(named().at(-1)).toBe('NFO:9');
    live.release();
    expect(named().at(-1)).toBe('');
  });

  it('asks the quotes route when the socket is not pushing', async () => {
    const urls: string[] = [];
    const s = socket();
    const feed = new Feed('ws://x', () => s, () => 0); // never connected
    const live = liveFeed({ spotOf: () => null, push: feed, fetcher: (async (u: string) => (urls.push(u), new Response(JSON.stringify({ live: true, quotes: [row('7', 5)] })))) as typeof fetch });
    await live.probe();
    const q = await live.quotes([key('7')]);
    expect(q.get(keyOf(key('7')))?.last).toBe(5);
    expect(urls.at(-1)).toBe('/api/derivs/quotes?tokens=NFO%3A7');
  });
});
