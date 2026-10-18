// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import { GRID_KEY } from '../src/chartlab/grid';
import { GridView, sharedFetcher } from '../src/chartlab/gridview';
import type { ChartRenderer } from '../src/chartlab/render';
import type { KeyValue } from '../src/chartlab/store';
import { ChartLabView, type BarsFetcher } from '../src/chartlab/view';
import type { AppContext } from '../src/panels/base';

const bars: Bar[] = Array.from({ length: 30 }, (_, i) => ({ t: Date.UTC(2026, 9, 1) / 1000 + i * 86_400, o: 100, h: 102, l: 99, c: 101, v: 10 }));

class Memory implements KeyValue {
  data: Record<string, string> = {};
  getItem(k: string): string | null {
    return this.data[k] ?? null;
  }
  setItem(k: string, v: string): void {
    this.data[k] = v;
  }
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function make(storage = new Memory()) {
  const fetched: [string, string][] = [];
  const pointers: ((i: number | null) => void)[] = []; // the crosshair callback of each cell's chart, in the order the cells were shown
  const fetchBars: BarsFetcher = async (symbol, tf) => {
    fetched.push([symbol, tf]);
    return { bars, note: '' };
  };
  const renderer = (): ChartRenderer => ({
    paint: () => {}, tick: () => {}, fit: () => {}, showLast: () => {}, onCrosshair: (cb) => void pointers.push(cb),
    paneElement: () => null, paneOf: () => 0, snapshot: () => null, applyTheme: () => {}, dispose: () => {},
  });
  const feed = { onQuotes: () => () => {}, need: vi.fn(), release: vi.fn(), quotes: new Map() };
  const ctx = { feed, bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => 'SBIN' }, watchlist: { items: ['SBIN'], subscribe: () => () => {} } } as unknown as AppContext;
  const lab = new ChartLabView(ctx, { renderer, fetchBars, storage });
  const grid = new GridView(ctx, lab.shared(), { renderer, fetchBars, storage });
  document.body.append(grid.element);
  return { grid, feed, fetched, storage, pointers };
}
const cells = (g: GridView) => [...g.element.querySelectorAll<HTMLElement>('.lab-cell')];
const visible = (g: GridView) => cells(g).filter((c) => !c.hidden);
const heads = (g: GridView) => cells(g).map((c) => c.querySelector('.lab-head')?.textContent ?? '');

beforeEach(() => {
  document.body.replaceChildren();
});

describe('one request for the same bars', () => {
  it('asks once for charts that ask together, keeps the answer for a while, and asks again after it', async () => {
    let now = 0;
    const inner = vi.fn(async () => ({ bars, note: '' }));
    const f = sharedFetcher(inner, 1000, () => now);
    await Promise.all([f('SBIN', '1D', null, true), f('SBIN', '1D', null, true), f('SBIN', '1D', null, true)]);
    expect(inner).toHaveBeenCalledTimes(1);
    await f('SBIN', '1W', null, true);
    await f('TCS', '1D', null, true);
    await f('SBIN', '1D', 5000, true);
    await f('SBIN', '1D', null, false);
    expect(inner).toHaveBeenCalledTimes(5); // a different symbol, interval, limit or adjustment is a different request
    now = 999;
    await f('SBIN', '1D', null, true);
    expect(inner).toHaveBeenCalledTimes(5);
    now = 1001;
    await f('SBIN', '1D', null, true);
    expect(inner).toHaveBeenCalledTimes(6);
  });

  it('does not keep a failure', async () => {
    const inner = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue({ bars, note: '' });
    const f = sharedFetcher(inner);
    await expect(f('SBIN', '1D', null, true)).rejects.toThrow('down');
    await flush();
    await expect(f('SBIN', '1D', null, true)).resolves.toBeTruthy();
    expect(inner).toHaveBeenCalledTimes(2);
  });
});

describe('the grid page', () => {
  it('opens as four charts of the symbol the other pages show, at 1D, 1W, 1h and 15m', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    expect(visible(g.grid)).toHaveLength(4);
    expect(heads(g.grid).map((h) => h.split('O')[0])).toEqual(['SBIN · 1D', 'SBIN · 1W', 'SBIN · 1h', 'SBIN · 15m']);
    expect(g.feed.need).toHaveBeenCalledWith('chartlab:g0', ['SBIN']);
    expect(g.feed.need).toHaveBeenCalledWith('chartlab:g3', ['SBIN']);
  });

  it('fetches the same daily bars once for the cells that need them', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    const daily = g.fetched.filter(([s, tf]) => s === 'SBIN' && tf === '1D');
    expect(daily.length).toBeLessThanOrEqual(2); // the Chart Lab page's own and the one the daily and weekly cells share
  });

  it('shows the cells a layout has, stops the others, and does not reload the ones that stay', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    const before = g.fetched.length;
    const select = g.grid.element.querySelector<HTMLSelectElement>('[aria-label="grid layout"]')!;
    select.value = '2h';
    select.dispatchEvent(new Event('change'));
    await flush();
    expect(visible(g.grid)).toHaveLength(2);
    expect(g.feed.release).toHaveBeenCalledWith('chartlab:g2');
    expect(g.feed.release).toHaveBeenCalledWith('chartlab:g3');
    expect(g.fetched.length).toBe(before); // the two that stay were not loaded again
    select.value = '2x2';
    select.dispatchEvent(new Event('change'));
    await flush();
    expect(visible(g.grid)).toHaveLength(4);
  });

  it('keeps the layout, the switches and each cell\'s symbol and interval, and comes back with them', async () => {
    const storage = new Memory();
    const a = make(storage);
    a.grid.show('SBIN');
    await flush();
    const select = a.grid.element.querySelector<HTMLSelectElement>('[aria-label="grid layout"]')!;
    select.value = '3h';
    select.dispatchEvent(new Event('change'));
    const time = a.grid.element.querySelector<HTMLInputElement>('[aria-label="sync time"]')!;
    time.checked = true;
    time.dispatchEvent(new Event('change'));
    (cells(a.grid)[1].querySelector('[data-interval="30m"]') as HTMLElement).click();
    const saved = JSON.parse(storage.data[GRID_KEY]);
    expect(saved.layout).toBe('3h');
    expect(saved.sync.time).toBe(true);
    expect(saved.cells[1].interval).toBe('30m');
    expect(saved.cells[0].symbol).toBe('SBIN');

    document.body.replaceChildren();
    const b = make(storage);
    b.grid.show('SBIN');
    await flush();
    expect(visible(b.grid)).toHaveLength(3);
    expect(heads(b.grid)[1].split('O')[0]).toBe('SBIN · 30m');
    expect(b.grid.element.querySelector<HTMLInputElement>('[aria-label="sync time"]')!.checked).toBe(true);
  });

  it('a symbol picked in one cell reaches the others while Symbol is on, and not when it is off', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    const pick = (i: number, symbol: string): void => {
      const input = cells(g.grid)[i].querySelector<HTMLInputElement>('.lab-symbol')!;
      input.value = symbol;
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    };
    pick(2, 'tcs');
    await flush();
    expect(heads(g.grid).map((h) => h.split('O')[0].split(' · ')[0])).toEqual(['TCS', 'TCS', 'TCS', 'TCS']);
    const box = g.grid.element.querySelector<HTMLInputElement>('[aria-label="sync symbol"]')!;
    box.checked = false;
    box.dispatchEvent(new Event('change'));
    pick(0, 'infy');
    await flush();
    expect(heads(g.grid).map((h) => h.split('O')[0].split(' · ')[0])).toEqual(['INFY', 'TCS', 'TCS', 'TCS']);
  });

  it('an interval chosen in one cell reaches the others while Interval is on', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    (cells(g.grid)[0].querySelector('[data-interval="5m"]') as HTMLElement).click();
    await flush();
    expect(heads(g.grid).map((h) => h.split('O')[0].split(' · ')[1])).toEqual(['5m', '1W', '1h', '15m']); // off by default
    const box = g.grid.element.querySelector<HTMLInputElement>('[aria-label="sync interval"]')!;
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    (cells(g.grid)[3].querySelector('[data-interval="1D"]') as HTMLElement).click();
    await flush();
    expect(heads(g.grid).map((h) => h.split('O')[0].split(' · ')[1])).toEqual(['1D', '1D', '1D', '1D']);
  });

  it('stops every chart when the tab is left', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    g.grid.hide();
    for (let i = 0; i < 4; i++) expect(g.feed.release).toHaveBeenCalledWith(`chartlab:g${i}`);
    expect(g.grid.element.hidden).toBe(true);
  });

  it('Replay: a click on a bar in one chart starts it there, the others show what had ended by then, and leaving ends it for all', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    const button = g.grid.element.querySelector<HTMLElement>('[aria-label="grid replay"]')!;
    button.click();
    expect(button.classList.contains('on')).toBe(true);
    for (const c of cells(g.grid)) expect(c.querySelector('.lab-note')?.textContent).toContain('Click the bar'); // every chart waits for the click
    g.pointers[0](9); // the crosshair of cell 0 is on its tenth bar
    cells(g.grid)[0].querySelector('.lab-host')!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    const bars = (i: number) => cells(g.grid)[i].querySelector('.lab-replay-status')?.textContent ?? '';
    expect(bars(0)).toContain('bar 10 of 30'); // cell 0 drives and has the controls
    expect(cells(g.grid)[0].querySelector<HTMLElement>('.lab-replay')!.hidden).toBe(false);
    for (const i of [1, 2, 3]) expect(cells(g.grid)[i].querySelector<HTMLElement>('.lab-replay')!.hidden).toBe(true); // the others follow
    expect(button.classList.contains('on')).toBe(true);

    (cells(g.grid)[0].querySelector('[aria-label="replay next"]') as HTMLElement).click();
    expect(bars(0)).toContain('bar 11 of 30');

    button.click(); // leaves, for every chart
    expect(button.classList.contains('on')).toBe(false);
    for (const c of cells(g.grid)) expect(c.querySelector<HTMLElement>('.lab-replay')!.hidden).toBe(true);
  });

  it('Replay: a cancelled pick leaves the charts as they were', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    const button = g.grid.element.querySelector<HTMLElement>('[aria-label="grid replay"]')!;
    button.click();
    button.click();
    expect(button.classList.contains('on')).toBe(false);
    g.pointers[1](4);
    cells(g.grid)[1].querySelector('.lab-host')!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(cells(g.grid)[1].querySelector<HTMLElement>('.lab-replay')!.hidden).toBe(true); // a click on a chart is only a click now
  });

  it('Replay: a chart brought in by a larger layout during a replay joins it', async () => {
    const g = make();
    g.grid.show('SBIN');
    await flush();
    const select = g.grid.element.querySelector<HTMLSelectElement>('[aria-label="grid layout"]')!;
    select.value = '2h';
    select.dispatchEvent(new Event('change'));
    await flush();
    g.grid.element.querySelector<HTMLElement>('[aria-label="grid replay"]')!.click();
    g.pointers[0](9);
    cells(g.grid)[0].querySelector('.lab-host')!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    select.value = '2x2';
    select.dispatchEvent(new Event('change'));
    await flush();
    expect(visible(g.grid)).toHaveLength(4);
    expect(cells(g.grid)[2].querySelector('.lab-replay-status')).not.toBeNull();
  });

  it('opens with the defaults when what was kept is damaged', async () => {
    const storage = new Memory();
    storage.setItem(GRID_KEY, '{"layout":"nope","cells":"x"');
    const g = make(storage);
    g.grid.show('SBIN');
    await flush();
    expect(visible(g.grid)).toHaveLength(4);
  });
});
