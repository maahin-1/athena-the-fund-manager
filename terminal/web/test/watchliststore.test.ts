import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { migrate, setFlag } from '../src/watchlists/model';
import { LOCAL_KEY, SAVE_DELAY_MS, serverIO, WatchlistsStore, type KeyValue, type WatchlistIO } from '../src/watchlists/store';

class Memory implements KeyValue {
  data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
}

function fakeServer(initial: unknown | null) {
  const state = { doc: initial as unknown | null, saves: [] as string[], down: false };
  const io: WatchlistIO = {
    async load() {
      if (state.down) throw new Error('offline');
      return state.doc;
    },
    async save(body) {
      if (state.down) throw new Error('offline');
      state.saves.push(body);
      state.doc = JSON.parse(body);
    },
  };
  return { state, io };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('WatchlistsStore', () => {
  it('starts from the seed symbols when nothing is saved anywhere, and sends them to the server on the first sync', async () => {
    const { state, io } = fakeServer(null);
    const store = new WatchlistsStore(io, new Memory(), ['SBIN', 'TCS']);
    expect(store.items).toEqual(['SBIN', 'TCS']); // the two index rows are in the list but not in `items`
    expect(store.all).toEqual(['NIFTY 50', 'NIFTY BANK', 'SBIN', 'TCS']);
    await store.sync();
    await vi.advanceTimersByTimeAsync(1);
    await store.flush();
    expect(state.saves).toHaveLength(1);
    expect(JSON.parse(state.saves[0]).lists[0].name).toBe('Main');
  });

  it('takes the server copy over the seed and tells listeners', async () => {
    const saved = setFlag(migrate(['HDFCBANK']), 'HDFCBANK', 'red');
    const { io } = fakeServer(saved);
    const store = new WatchlistsStore(io, new Memory(), ['SBIN']);
    let heard = 0;
    store.subscribe(() => heard++);
    await store.sync();
    expect(store.items).toContain('HDFCBANK');
    expect(store.items).not.toContain('SBIN');
    expect(heard).toBe(1);
  });

  it('a burst of changes makes one save, and the browser copy is written at once', async () => {
    const { state, io } = fakeServer(migrate([]));
    const local = new Memory();
    const store = new WatchlistsStore(io, local, []);
    await store.sync();
    store.add('SBIN');
    store.add('TCS');
    store.remove('NIFTY BANK');
    expect(JSON.parse(local.getItem(LOCAL_KEY)!).lists[0].items.length).toBe(5); // the copy is already current
    expect(state.saves).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 5);
    expect(state.saves).toHaveLength(1);
    expect(store.dirty).toBe(false);
    expect(store.all).toEqual(['NIFTY 50', 'SBIN', 'TCS']);
  });

  it('adding what is already there changes nothing and saves nothing', async () => {
    const { state, io } = fakeServer(migrate(['SBIN']));
    const store = new WatchlistsStore(io, new Memory(), []);
    await store.sync();
    store.add('sbin');
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 5);
    expect(state.saves).toHaveLength(0);
  });

  it('offline: the lists work from the browser copy and are saved when the server returns', async () => {
    const { state, io } = fakeServer(migrate([]));
    const local = new Memory();
    const first = new WatchlistsStore(io, local, []);
    state.down = true;
    await first.sync();
    first.add('SBIN');
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 5);
    expect(first.dirty).toBe(true);
    const reopened = new WatchlistsStore(io, local, []); // a reload while the server is still down
    expect(reopened.items).toContain('SBIN');
    state.down = false;
    reopened.dirty = true;
    await reopened.sync();
    await vi.advanceTimersByTimeAsync(1);
    expect((state.doc as { lists: { items: { s?: string }[] }[] }).lists[0].items.some((i) => i.s === 'SBIN')).toBe(true);
  });

  it('an unsaved change is not wiped by an older server copy', async () => {
    const { io } = fakeServer(migrate([]));
    const store = new WatchlistsStore(io, new Memory(), []);
    await store.sync();
    store.add('SBIN');
    await store.sync();
    expect(store.items).toContain('SBIN');
  });

  it('an unreadable server copy and a damaged browser copy are both ignored', async () => {
    const { io } = fakeServer({ version: 1, lists: 'nonsense' });
    const local = new Memory();
    local.setItem(LOCAL_KEY, '{not json');
    const store = new WatchlistsStore(io, local, ['SBIN']);
    expect(store.items).toContain('SBIN');
    await store.sync();
    expect(store.items).toContain('SBIN');
  });

  it('serverIO reads 404 as nothing saved and any other failure as an error', async () => {
    const status = (code: number, body: unknown = {}) => (async () => new Response(JSON.stringify(body), { status: code })) as unknown as typeof fetch;
    expect(await serverIO(status(404)).load()).toBeNull();
    expect(await serverIO((async () => new Response(null, { status: 204 })) as unknown as typeof fetch).load()).toBeNull();
    expect(await serverIO(status(200, { a: 1 })).load()).toEqual({ a: 1 });
    await expect(serverIO(status(500)).load()).rejects.toThrow(/500/);
    await expect(serverIO(status(413)).save('{}')).rejects.toThrow(/413/);
  });
});

describe('seeding from a layout', () => {
  it('a layout\'s own list seeds the very first document only', async () => {
    const { state, io } = fakeServer(null);
    const store = new WatchlistsStore(io, new Memory(), ['SBIN']);
    await store.sync();
    store.seedFrom(['LUPIN', 'RVNL']);
    expect(store.items).toEqual(['LUPIN', 'RVNL']);
    store.seedFrom(['TCS']); // too late: the document already exists
    expect(store.items).toEqual(['LUPIN', 'RVNL']);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 5);
    expect(JSON.stringify(state.doc)).toContain('LUPIN');
  });

  it('is ignored when the server already holds the lists', async () => {
    const { io } = fakeServer(migrate(['HDFCBANK']));
    const store = new WatchlistsStore(io, new Memory(), ['SBIN']);
    await store.sync();
    store.seedFrom(['LUPIN']);
    expect(store.items).toEqual(['HDFCBANK']);
  });
});
