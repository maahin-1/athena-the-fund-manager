import { describe, expect, it } from 'vitest';
import { BarsCache, BARS_TTL_MS } from '../src/details/data';
import { ScreenStore, SCREEN_RETRY_MS, SCREEN_TTL_MS } from '../src/screenrows';

const body = { columns: ['symbol', 'kind', 'pe', 'sector'], rows: [['SBIN', 'stock', 9.8, 'Banks'], ['TCS', 'stock', null, 'IT']], market_day: '2026-10-16' };

function server(replies: (object | number)[]) {
  let calls = 0;
  const f = (async () => {
    const next = replies[Math.min(calls, replies.length - 1)];
    calls++;
    return typeof next === 'number' ? new Response('{}', { status: next }) : new Response(JSON.stringify(next));
  }) as unknown as typeof fetch;
  return { f, calls: () => calls };
}

describe('ScreenStore', () => {
  it('loads once for several lookups, finds symbols in any case, and tells listeners', async () => {
    const s = server([body]);
    const store = new ScreenStore(s.f, () => 0);
    let heard = 0;
    store.subscribe(() => heard++);
    expect(store.get('sbin')).toBeUndefined(); // not there yet; the first get starts the load
    store.get('TCS');
    await store.load();
    expect(s.calls()).toBe(1);
    expect(store.get('sbin')?.pe).toBe(9.8);
    expect(store.get('TCS')?.pe).toBeNull();
    expect(store.get('NOPE')).toBeUndefined();
    expect(store.marketDay).toBe('2026-10-16');
    expect(heard).toBe(1);
    expect(store.loaded).toBe(true);
  });

  it('reloads when the rows are ten minutes old', async () => {
    let t = 1_000;
    const s = server([body]);
    const store = new ScreenStore(s.f, () => t);
    await store.load();
    t += SCREEN_TTL_MS - 1;
    store.ensure();
    await new Promise((r) => setTimeout(r, 0));
    expect(s.calls()).toBe(1);
    t += 2;
    store.ensure();
    await new Promise((r) => setTimeout(r, 0));
    expect(s.calls()).toBe(2);
  });

  it('keeps the old rows on a failure and does not retry for 30 seconds', async () => {
    let t = 1_000;
    const s = server([body, 500, 500, body]);
    const store = new ScreenStore(s.f, () => t);
    await store.load();
    t += SCREEN_TTL_MS + 1;
    expect(await store.load()).toBe(false);
    expect(store.get('SBIN')?.pe).toBe(9.8); // the old rows still answer
    const after = s.calls();
    t += SCREEN_RETRY_MS - 1;
    store.ensure();
    expect(s.calls()).toBe(after);
    t += 2;
    store.ensure();
    await new Promise((r) => setTimeout(r, 0));
    expect(s.calls()).toBeGreaterThan(after);
  });
});

describe('BarsCache', () => {
  const bars = { bars: [{ t: 1, o: 1, h: 1, l: 1, c: 1, v: 1 }] };

  it('fetches once for the same symbol asked together or again within five minutes', async () => {
    let t = 0;
    const s = server([bars]);
    const cache = new BarsCache(s.f, () => t);
    const [a, b] = await Promise.all([cache.get('sbin'), cache.get('SBIN')]);
    expect(a).toEqual(bars.bars);
    expect(b).toBe(a);
    t += BARS_TTL_MS - 1;
    await cache.get('SBIN');
    expect(s.calls()).toBe(1);
    t += 2;
    await cache.get('SBIN');
    expect(s.calls()).toBe(2);
  });

  it('answers null on an error or no bars, and does not keep it', async () => {
    const s = server([500, { bars: [] }, bars]);
    const cache = new BarsCache(s.f, () => 0);
    expect(await cache.get('X')).toBeNull();
    expect(await cache.get('X')).toBeNull();
    expect(await cache.get('X')).toEqual(bars.bars);
  });
});
