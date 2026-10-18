// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import { ChartLabView, type BarsFetcher, type CellSend } from '../src/chartlab/view';
import { momentOf } from '../src/chartlab/grid';
import type { ChartRenderer } from '../src/chartlab/render';
import type { KeyValue } from '../src/chartlab/store';
import type { AppContext } from '../src/panels/base';

const lastDay = Date.UTC(2026, 9, 14) / 1000;
const bars: Bar[] = Array.from({ length: 80 }, (_, i) => ({ t: lastDay - (79 - i) * 86_400, o: 100 + i, h: 103 + i, l: 99 + i, c: 101 + i, v: 1000 }));

class Memory implements KeyValue {
  data: Record<string, string> = {};
  getItem(k: string): string | null {
    return this.data[k] ?? null;
  }
  setItem(k: string, v: string): void {
    this.data[k] = v;
  }
}

function make(opts: { cell?: boolean; storage?: Memory } = {}) {
  const storage = opts.storage ?? new Memory();
  const sent = { symbol: [] as string[], interval: [] as string[], crosshair: [] as (number | null)[], period: [] as [number, number][], replay: [] as (number | null)[] };
  const send: CellSend = {
    symbol: (s) => sent.symbol.push(s),
    interval: (i) => sent.interval.push(i),
    crosshair: (m) => sent.crosshair.push(m),
    period: (a, b) => sent.period.push([a, b]),
    replay: (m) => sent.replay.push(m),
  };
  const calls = { crosshair: [] as (number | null)[], range: [] as [number, number][], fetched: [] as [string, string][] };
  let pointer: (i: number | null) => void = () => {};
  let rangeChanged: (from: number, to: number) => void = () => {};
  const renderer = (): ChartRenderer => ({
    paint: () => {}, tick: () => {}, fit: () => {}, showLast: () => {},
    onCrosshair: (cb) => (pointer = cb),
    onRangeChanged: (cb) => (rangeChanged = cb),
    showCrosshair: (i) => void calls.crosshair.push(i),
    showRange: (a, b) => void calls.range.push([a, b]),
    paneElement: () => null, paneOf: () => 0, snapshot: () => null, applyTheme: () => {}, dispose: () => {},
  });
  const fetchBars: BarsFetcher = async (symbol, tf) => {
    calls.fetched.push([symbol, tf]);
    return { bars, note: '' };
  };
  const feed = { onQuotes: () => () => {}, need: vi.fn(), release: vi.fn(), quotes: new Map() };
  const bus = { subscribe: vi.fn(() => () => {}), setSymbol: vi.fn(), symbolOf: () => undefined };
  const ctx = { feed, bus, watchlist: { items: ['SBIN'], subscribe: () => () => {} } } as unknown as AppContext;
  const first = new ChartLabView(ctx, { renderer, fetchBars, storage });
  const view = opts.cell
    ? new ChartLabView(ctx, { renderer, fetchBars, storage, cell: { id: 'c1', send, shared: first.shared() } })
    : first;
  return { view, first, sent, calls, feed, bus, storage, pointer: (i: number | null) => pointer(i), range: (a: number, b: number) => rangeChanged(a, b) };
}
const flush = () => new Promise((r) => setTimeout(r, 0));
const pointerIn = (view: ChartLabView) => view.element.querySelector('.lab-chart')!.dispatchEvent(new MouseEvent('mouseenter'));

beforeEach(() => {
  document.body.replaceChildren();
});

describe('a chart in a grid cell', () => {
  it('keeps its chart state, feed name and symbol of its own, and does not follow the shared link group', async () => {
    const c = make({ cell: true });
    expect(c.bus.subscribe).toHaveBeenCalledTimes(1); // the Chart Lab page's own, not the cell's
    c.view.show('TCS');
    await flush();
    expect(c.feed.need).toHaveBeenCalledWith('chartlab:c1', ['TCS']);
    c.view.model.setType('line');
    c.view.hide();
    expect(c.feed.release).toHaveBeenCalledWith('chartlab:c1');
    // the state is stored under the cell's key, not the Chart Lab page's
    const view = c.view as unknown as { persist(): void };
    view.persist();
    expect(Object.keys(c.storage.data).some((k) => k.endsWith('.c1'))).toBe(true);
    expect(c.storage.data['athena.chartlab.state']).toBeUndefined();
  });

  it('is compact: no side panel, drawing rail, compare, scales, ADJ, undo, templates or snapshot', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    const e = c.view.element;
    expect(e.classList.contains('lab-cell')).toBe(true);
    for (const sel of ['.lab-window', '.lab-rail', '.lab-compare', '[data-scale]', '[data-action="adjust"]', '.lab-templates']) expect(e.querySelector(sel), sel).toBeNull();
    for (const sel of ['.lab-symbol', '[data-interval]', '.lab-interval', '.lab-type', '.lab-ind']) expect(e.querySelector(sel), sel).not.toBeNull();
  });

  it('does not take the page keyboard shortcuts', () => {
    const add = vi.spyOn(document, 'addEventListener');
    make({ cell: true });
    expect(add.mock.calls.filter(([type]) => type === 'keydown')).toHaveLength(1); // the Chart Lab page's own
    add.mockRestore();
  });

  it('tells the grid the symbol and interval a person chose, and nothing when the grid chose them', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    const input = c.view.element.querySelector('.lab-symbol') as HTMLInputElement;
    input.value = 'tcs';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(c.sent.symbol).toEqual(['TCS']);
    expect(c.bus.setSymbol).not.toHaveBeenCalled();
    (c.view.element.querySelector('[data-interval="1W"]') as HTMLElement).click();
    expect(c.sent.interval).toEqual(['1W']);

    c.view.applySymbol('infy');
    c.view.applyInterval('15m');
    await flush();
    expect(c.sent.symbol).toEqual(['TCS']); // unchanged
    expect(c.sent.interval).toEqual(['1W']);
    expect(c.view.currentSymbol).toBe('INFY');
    expect(c.view.interval).toBe('15m');
    expect(c.calls.fetched.some(([s, tf]) => s === 'INFY' && tf === '15m')).toBe(true);
  });

  it('takes a typed interval and a built-in one in any spelling from the grid, and refuses one that is not an interval', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.applyInterval('60m');
    expect(c.view.interval).toBe('1h');
    c.view.applyInterval('7m');
    expect(c.view.interval).toBe('7m');
    c.view.applyInterval('nonsense');
    expect(c.view.interval).toBe('7m');
  });

  it('sends the moment under the pointer only while the pointer is in it', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.pointer(5); // the crosshair moved by something else (another cell): not sent on
    expect(c.sent.crosshair).toEqual([]);
    pointerIn(c.view);
    c.pointer(5);
    expect(c.sent.crosshair).toEqual([momentOf(bars[5].t, '1D')]);
    c.pointer(null);
    expect(c.sent.crosshair).toEqual([momentOf(bars[5].t, '1D'), null]);
    c.view.element.querySelector('.lab-chart')!.dispatchEvent(new MouseEvent('mouseleave'));
    expect(c.sent.crosshair.at(-1)).toBeNull(); // leaving clears the others
  });

  it('shows another chart\'s moment as a crosshair at the bar it falls in, and clears it', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.showMoment(momentOf(bars[40].t, '1D'));
    c.view.showMoment(Date.UTC(2020, 0, 1) / 1000); // before the first bar
    c.view.showMoment(null);
    expect(c.calls.crosshair).toEqual([40, null, null]);
  });

  it('does not show a moment while its own pointer is in it', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    pointerIn(c.view);
    c.view.showMoment(momentOf(bars[40].t, '1D'));
    expect(c.calls.crosshair).toEqual([]);
  });

  it('sends the period in view as moments, and shows another chart\'s period as bar numbers', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.range(10, 60); // not sent: the pointer is not in this chart
    expect(c.sent.period).toEqual([]);
    pointerIn(c.view);
    c.range(10.4, 200); // clamped to the bars it has
    expect(c.sent.period).toEqual([[momentOf(bars[10].t, '1D'), momentOf(bars[79].t, '1D')]]);

    const d = make({ cell: true });
    d.view.show('SBIN');
    await flush();
    d.view.showPeriod(momentOf(bars[20].t, '1D'), momentOf(bars[50].t, '1D'));
    d.view.showPeriod(Date.UTC(2020, 0, 1) / 1000, Date.UTC(2020, 5, 1) / 1000); // before every bar: left as it is
    expect(d.calls.range).toEqual([[20, 50]]);
  });

  it('neither sends nor shows crosshair or period on a chart of price moves', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.model.setType('renko');
    pointerIn(c.view);
    c.pointer(3);
    c.range(1, 5);
    c.view.showMoment(momentOf(bars[40].t, '1D'));
    c.view.showPeriod(momentOf(bars[20].t, '1D'), momentOf(bars[50].t, '1D'));
    expect(c.sent.crosshair).toEqual([]);
    expect(c.sent.period).toEqual([]);
    expect(c.calls.crosshair).toEqual([]);
    expect(c.calls.range).toEqual([]);
  });

  it('shares the Chart Lab page\'s indicator lists and fetches none of its own', () => {
    const c = make({ cell: true });
    expect(c.view.shared()).toEqual(c.first.shared());
  });
});

describe('replay in a grid cell', () => {
  const click = (c: ReturnType<typeof make>) => c.view.element.querySelector('.lab-host')!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

  it('drives: picking a bar starts it, tells the grid the moment of every step, and tells it when it ends', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.pickReplayStart();
    expect(c.view.replayActive).toBe(true);
    c.pointer(9);
    click(c);
    expect(c.view.model.bars).toHaveLength(10);
    const end9 = Date.UTC(2026, 9, 14) / 1000 - 70 * 86_400 + 10 * 3600; // bar 9 of 80 ends at 15:30 IST of its date
    expect(c.sent.replay).toEqual([end9]);
    (c.view.element.querySelector('[aria-label="replay next"]') as HTMLElement).click();
    expect(c.sent.replay).toEqual([end9, end9 + 86_400]);
    (c.view.element.querySelector('[aria-label="replay exit"]') as HTMLElement).click();
    expect(c.sent.replay.at(-1)).toBeNull();
    expect(c.view.replayActive).toBe(false);
  });

  it('follows: shows the bars that ended by another chart\'s moment, with no controls, and sends nothing', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    const moment = Date.UTC(2026, 9, 14) / 1000 - 60 * 86_400 + 10 * 3600;
    c.view.followReplay(moment);
    expect(c.view.model.replaying).toBe(true);
    expect(c.view.model.bars).toHaveLength(20);
    expect((c.view.element.querySelector('.lab-replay') as HTMLElement).hidden).toBe(true);
    c.view.followReplay(moment + 86_400 * 5);
    expect(c.view.model.bars).toHaveLength(25);
    c.view.followReplay(null);
    expect(c.view.model.replaying).toBe(false);
    expect(c.view.model.bars).toHaveLength(80);
    expect(c.sent.replay).toEqual([]);
  });

  it('a follower that changes interval keeps the moment and does not become a driver', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.followReplay(Date.UTC(2026, 9, 14) / 1000 - 40 * 86_400 + 10 * 3600);
    c.view.applyInterval('1W');
    await flush();
    expect(c.view.model.replaying).toBe(true);
    expect((c.view.element.querySelector('.lab-replay') as HTMLElement).hidden).toBe(true);
    expect(c.sent.replay).toEqual([]);
  });

  it('picking is cancelled when another chart starts the replay', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.pickReplayStart();
    c.view.followReplay(Date.UTC(2026, 9, 14) / 1000 - 40 * 86_400 + 10 * 3600);
    c.pointer(3);
    click(c);
    expect(c.sent.replay).toEqual([]); // the click no longer starts one here
    expect(c.view.model.bars.length).toBeLessThan(80);
  });

  it('stopping a replay that a chart drives ends it for the grid too, and one it follows does not', async () => {
    const c = make({ cell: true });
    c.view.show('SBIN');
    await flush();
    c.view.followReplay(Date.UTC(2026, 9, 14) / 1000 - 40 * 86_400 + 10 * 3600);
    c.view.stopReplay();
    expect(c.sent.replay).toEqual([]);
    c.view.pickReplayStart();
    c.view.stopReplay(); // cancelling a pick is the driver's end
    expect(c.sent.replay).toEqual([null]);
  });
});

describe('the Chart Lab page is unchanged', () => {
  it('follows the link group, sends symbols to it, and stays full', async () => {
    const c = make();
    c.view.show('SBIN');
    await flush();
    expect(c.feed.need).toHaveBeenCalledWith('chartlab', ['SBIN']);
    const input = c.view.element.querySelector('.lab-symbol') as HTMLInputElement;
    input.value = 'tcs';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(c.bus.setSymbol).toHaveBeenCalledWith('blue', 'TCS');
    expect(c.view.element.querySelector('.lab-window')).not.toBeNull();
    expect(c.view.element.classList.contains('lab-cell')).toBe(false);
  });
});
