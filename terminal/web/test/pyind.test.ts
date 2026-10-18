import { describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import { ChartModel } from '../src/chartlab/model';
import { caption, customDefs, find, setCustom } from '../src/chartlab/registry';
import { deserialize, serialize } from '../src/chartlab/store';
import { PyIndicators, cacheKey, type PyFile, type PyIO, type RunReply } from '../src/formula/pyind';

const bars = (closes: number[]): Bar[] => closes.map((c, i) => ({ t: 100 + i, o: c, h: c, l: c, c, v: 1 }));
const file = (extra: Partial<PyFile> = {}): PyFile => ({ file: 'avg.py', name: 'Average', pane: 'overlay', sha256: 'a'.repeat(64), size: 80, error: null, enabled: true, changed: false, ...extra });

function io(files: PyFile[], reply: RunReply = { lines: { avg: [null, 2, 3] } }) {
  const calls = { run: [] as { file: string; n: number }[], enable: [] as string[], disable: [] as string[] };
  const fake: PyIO & { calls: typeof calls; files: PyFile[] } = {
    calls, files,
    async list() { return { dir: '.athena/indicators', indicators: fake.files }; },
    async enable(f, sha) { calls.enable.push(`${f}:${sha}`); fake.files = fake.files.map((x) => (x.file === f ? { ...x, enabled: true, changed: false } : x)); return null; },
    async disable(f) { calls.disable.push(f); fake.files = fake.files.map((x) => (x.file === f ? { ...x, enabled: false } : x)); },
    async run(f, b) { calls.run.push({ file: f, n: b.length }); return reply; },
  };
  return fake;
}
const clear = () => {
  setCustom([], 'python');
  setCustom([]);
};

describe('python indicators as chart indicators', () => {
  it('registers every usable file under py:<file>, and not one that has an error', async () => {
    const store = new PyIndicators(io([file(), file({ file: 'bad.py', error: 'syntax error on line 1: invalid syntax', enabled: false })]), null);
    await store.load();
    expect(customDefs().map((d) => d.id)).toEqual(['py:avg.py']);
    expect(find('py:avg.py')?.name).toBe('Average');
    expect(find('py:avg.py')?.pane).toBe('overlay');
    clear();
  });

  it('asks for the numbers once per set of bars, shows nothing until they arrive, then draws the lines the file returned', async () => {
    const fake = io([file()]);
    const store = new PyIndicators(fake, null);
    await store.load();
    let ready = 0;
    store.subscribe(() => ready++);
    const def = find('py:avg.py')!;
    const b = bars([1, 3, 5]);
    expect(def.compute(b, {})).toEqual({}); // not here yet
    expect(def.compute(b, {})).toEqual({}); // asked for once, not again
    await vi.waitFor(() => expect(ready).toBeGreaterThan(0));
    expect(fake.calls.run).toEqual([{ file: 'avg.py', n: 3 }]);
    expect(def.compute(b, {})).toEqual({ avg: [null, 2, 3] });
    expect(def.outputs.map((o) => o.key)).toEqual(['avg']); // the lines are known once it has run
    expect(fake.calls.run).toHaveLength(1);
    clear();
  });

  it('is not asked again when only the last bar moves, but is when a bar is added', async () => {
    const fake = io([file()]);
    const store = new PyIndicators(fake, null);
    await store.load();
    const def = find('py:avg.py')!;
    def.compute(bars([1, 3, 5]), {});
    await vi.waitFor(() => expect(fake.calls.run).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 5));
    const moved = bars([1, 3, 5]);
    moved[2] = { ...moved[2], c: 9 }; // a live tick
    expect(cacheKey('avg.py', 'x', moved)).toBe(cacheKey('avg.py', 'x', bars([1, 3, 5])));
    def.compute(moved, {});
    expect(fake.calls.run).toHaveLength(1);
    def.compute(bars([1, 3, 5, 7]), {});
    await vi.waitFor(() => expect(fake.calls.run).toHaveLength(2));
    clear();
  });

  it('says why when the file fails, and draws nothing', async () => {
    const store = new PyIndicators(io([file()], { error: 'ZeroDivisionError: division by zero (line 3)' }), null);
    await store.load();
    const errors: string[] = [];
    store.onError((f, m) => errors.push(`${f}: ${m}`));
    find('py:avg.py')!.compute(bars([1, 2]), {});
    await vi.waitFor(() => expect(errors).toEqual(['avg.py: ZeroDivisionError: division by zero (line 3)']));
    expect(find('py:avg.py')!.compute(bars([1, 2]), {})).toEqual({});
    clear();
  });

  it('is captioned with its name until it has run, and with its line after', async () => {
    const store = new PyIndicators(io([file()]), null);
    await store.load();
    const def = find('py:avg.py')!;
    expect(caption(def, {})).toBe('Average'); // no lines known yet: must not throw
    let ready = 0;
    store.subscribe(() => ready++);
    def.compute(bars([1, 3, 5]), {});
    await vi.waitFor(() => expect(ready).toBeGreaterThan(0));
    expect(caption(def, {})).toBe('avg');
    clear();
  });

  it('a file that is not switched on is not run, and draws nothing', async () => {
    const fake = io([file({ enabled: false })]);
    const store = new PyIndicators(fake, null);
    await store.load();
    expect(find('py:avg.py')!.compute(bars([1, 2]), {})).toEqual({});
    await new Promise((r) => setTimeout(r, 10));
    expect(fake.calls.run).toEqual([]);
    clear();
  });
});

describe('switching a file on and off', () => {
  it('switches on the version the person saw (its hash) and tells listeners', async () => {
    const fake = io([file({ enabled: false })]);
    const store = new PyIndicators(fake, null);
    await store.load();
    let heard = 0;
    store.subscribe(() => heard++);
    expect(await store.enable('avg.py')).toBeNull();
    expect(fake.calls.enable).toEqual([`avg.py:${'a'.repeat(64)}`]);
    expect(store.file('avg.py')?.enabled).toBe(true);
    expect(heard).toBeGreaterThan(0);
    await store.disable('avg.py');
    expect(fake.calls.disable).toEqual(['avg.py']);
    expect(store.file('avg.py')?.enabled).toBe(false);
    clear();
  });

  it('does not switch on a file with an error, and says what the server said when it refuses', async () => {
    const store = new PyIndicators(io([file({ file: 'bad.py', error: 'syntax error', enabled: false })]), null);
    await store.load();
    expect(await store.enable('bad.py')).toMatch(/error/);
    const refusing = io([file({ enabled: false })]);
    refusing.enable = async () => 'that is not the file as it is now';
    const s2 = new PyIndicators(refusing, null);
    await s2.load();
    expect(await s2.enable('avg.py')).toBe('that is not the file as it is now');
    clear();
  });

  it('lists nothing and says so when the server cannot be reached', async () => {
    const down: PyIO = { async list() { throw new Error('offline'); }, async enable() { return 'x'; }, async disable() {}, async run() { return { error: 'x' }; } };
    const store = new PyIndicators(down, null);
    await store.load();
    expect(store.problem).toMatch(/could not be listed/);
    expect(store.files()).toEqual([]);
    clear();
  });
});

describe('a chart that uses one', () => {
  it('is restored from the browser copy before the server answers, and keeps its other indicators when a file is gone', async () => {
    const kept = new Map<string, string>();
    const local = { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v) };
    const first = new PyIndicators(io([file()]), local);
    await first.load();
    const model = new ChartModel();
    model.setBars(bars([1, 2, 3]), '1D');
    model.addIndicator('py:avg.py');
    model.addIndicator('rsi');
    const saved = serialize(model.state());
    clear();
    const second = new PyIndicators({ async list() { throw new Error('offline'); }, async enable() { return null; }, async disable() {}, async run() { return { error: 'x' }; } }, local);
    expect(deserialize(saved).indicators.map((i) => i.id)).toEqual(['py:avg.py', 'rsi']); // registered from the copy at once
    void second;
    clear();
    expect(deserialize(saved).indicators.map((i) => i.id)).toEqual(['rsi']); // the file is gone: only it is lost
  });
});
