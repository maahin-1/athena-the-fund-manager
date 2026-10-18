import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { newDrawing, serializeDoc, type Drawing } from '../src/chartlab/drawings/model';
import { ActionLog, DrawingStore, SAVE_DELAY_MS, serverDrawingIO, symbolKey, type DrawingIO } from '../src/chartlab/drawings/store';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const line = (): Drawing => newDrawing('trend', [{ t: 1, p: 1 }, { t: 2, p: 2 }]);

function memory() {
  const data: Record<string, string> = {};
  return { data, getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v) };
}

/** A fake server: documents by symbol, a switch to make it fail, a log of saves. */
function server(initial: Record<string, string> = {}) {
  const docs = { ...initial };
  const state = { down: false, puts: [] as string[] };
  const io: DrawingIO = {
    async get(s) {
      if (state.down) throw new Error('down');
      return docs[s] ?? null;
    },
    async put(s, text) {
      if (state.down) return false;
      docs[s] = text;
      state.puts.push(s);
      return true;
    },
  };
  return { io, docs, state };
}

describe('symbolKey', () => {
  it('upper-cases and accepts the symbols the page uses', () => {
    expect(symbolKey(' sbin ')).toBe('SBIN');
    expect(symbolKey('M&M')).toBe('M&M');
    expect(symbolKey('nifty 50')).toBe('NIFTY 50');
    expect(symbolKey('BAJAJ-AUTO')).toBe('BAJAJ-AUTO');
  });
  it('refuses others', () => {
    expect(symbolKey('')).toBeNull();
    expect(symbolKey('A/B')).toBeNull();
    expect(symbolKey('../x')).toBeNull();
    expect(symbolKey('A'.repeat(25))).toBeNull();
  });
});

describe('DrawingStore', () => {
  it('saves to the browser at once and to the server 400 ms after the last change', async () => {
    const s = server();
    const ls = memory();
    const store = new DrawingStore(s.io, ls);
    const one = [line()];
    store.save('sbin', one);
    expect(ls.data['athena.drawings.SBIN']).toContain('"trend"');
    store.save('sbin', [...one, line()]);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS - 1);
    expect(s.state.puts).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(2);
    expect(s.state.puts).toEqual(['SBIN']); // one save for two changes
    expect(JSON.parse(s.docs.SBIN).drawings).toHaveLength(2);
  });

  it('loads what the server has, in preference to the browser copy', async () => {
    const d = line();
    const s = server({ SBIN: serializeDoc('SBIN', [d]) });
    const ls = memory();
    ls.setItem('athena.drawings.SBIN', serializeDoc('SBIN', []));
    const got = await new DrawingStore(s.io, ls).load('sbin');
    expect(got.map((x) => x.id)).toEqual([d.id]);
    expect(JSON.parse(ls.data['athena.drawings.SBIN']).drawings).toHaveLength(1); // the browser copy follows
  });

  it('when the server is down uses the browser copy, and sends the next save when it is back', async () => {
    const d = line();
    const s = server();
    const ls = memory();
    ls.setItem('athena.drawings.SBIN', serializeDoc('SBIN', [d]));
    s.state.down = true;
    const store = new DrawingStore(s.io, ls);
    expect((await store.load('SBIN')).map((x) => x.id)).toEqual([d.id]);
    store.save('SBIN', [d, line()]);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 1);
    expect(store.notSent).toEqual(['SBIN']);
    expect(s.state.puts).toHaveLength(0);
    s.state.down = false;
    store.save('SBIN', [d, line(), line()]);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 1);
    expect(store.notSent).toEqual([]);
    expect(JSON.parse(s.docs.SBIN).drawings).toHaveLength(3);
  });

  it('the server having nothing while the browser has drawings sends them up', async () => {
    const d = line();
    const s = server();
    const ls = memory();
    ls.setItem('athena.drawings.TCS', serializeDoc('TCS', [d]));
    const store = new DrawingStore(s.io, ls);
    expect((await store.load('TCS')).map((x) => x.id)).toEqual([d.id]);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 1);
    expect(JSON.parse(s.docs.TCS).drawings[0].id).toBe(d.id);
  });

  it('ignores a bad document on the server and a bad browser copy', async () => {
    const s = server({ SBIN: '{"version":1,"symbol":"SBIN","drawings":[{"tool":"laser"}]}' });
    const ls = memory();
    ls.setItem('athena.drawings.SBIN', 'not json');
    expect(await new DrawingStore(s.io, ls).load('SBIN')).toEqual([]);
  });

  it('a change made while a load is in flight wins over what the load finds', async () => {
    const s = server({ SBIN: serializeDoc('SBIN', [line()]) });
    const store = new DrawingStore(s.io, memory());
    const mine = [line(), line()];
    const loading = store.load('SBIN');
    store.save('SBIN', mine);
    expect(await loading).toBe(mine);
  });

  it('flush sends at once, and a symbol the page does not draw on is ignored', async () => {
    const s = server();
    const store = new DrawingStore(s.io, null);
    store.save('SBIN', [line()]);
    await store.flush();
    expect(s.state.puts).toEqual(['SBIN']);
    store.save('A/B', [line()]);
    await store.flush();
    expect(s.state.puts).toEqual(['SBIN']);
    expect(await store.load('A/B')).toEqual([]);
  });

  it('works with no browser storage at all', async () => {
    const s = server();
    const store = new DrawingStore(s.io, { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } });
    store.save('SBIN', [line()]);
    await vi.advanceTimersByTimeAsync(SAVE_DELAY_MS + 1);
    expect(s.state.puts).toEqual(['SBIN']);
    expect((await store.load('SBIN')).length).toBe(1);
  });
});

describe('serverDrawingIO', () => {
  it('reads 204 as nothing saved, other errors as a failure, and saves with PUT', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const reply = (status: number, body = '') => (async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return new Response(status === 204 ? null : body, { status });
    }) as unknown as typeof fetch;
    expect(await serverDrawingIO(reply(204)).get('M&M')).toBeNull();
    expect(calls[0][0]).toBe('/api/drawings/M%26M');
    expect(await serverDrawingIO(reply(200, '{"a":1}')).get('SBIN')).toBe('{"a":1}');
    await expect(serverDrawingIO(reply(500)).get('SBIN')).rejects.toThrow();
    expect(await serverDrawingIO(reply(204)).put('SBIN', '{}')).toBe(true);
    expect(calls[calls.length - 1][1]?.method).toBe('PUT');
    expect(await serverDrawingIO(reply(413)).put('SBIN', '{}')).toBe(false);
  });
});

describe('ActionLog', () => {
  it('undoes and redoes in the order things were done, across kinds', () => {
    const log = new ActionLog();
    log.push('chart');
    log.push('drawings');
    log.push('drawings');
    expect(log.undo()).toBe('drawings');
    expect(log.undo()).toBe('drawings');
    expect(log.undo()).toBe('chart');
    expect(log.undo()).toBeNull();
    expect(log.redo()).toBe('chart');
    expect(log.redo()).toBe('drawings');
    expect(log.canRedo).toBe(true);
    expect(log.canUndo).toBe(true);
  });

  it('a new action drops what could have been redone', () => {
    const log = new ActionLog();
    log.push('chart');
    log.undo();
    expect(log.canRedo).toBe(true);
    log.push('drawings');
    expect(log.canRedo).toBe(false);
  });

  it('forgets one kind when its history is reset', () => {
    const log = new ActionLog();
    log.push('drawings');
    log.push('chart');
    log.push('drawings');
    log.undo();
    log.drop('drawings');
    expect(log.canRedo).toBe(false);
    expect(log.undo()).toBe('chart');
    expect(log.undo()).toBeNull();
  });
});
