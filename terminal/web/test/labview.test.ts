// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import { ChartLabView, type BarsFetcher } from '../src/chartlab/view';
import { REGISTRY } from '../src/chartlab/registry';
import type { ChartRenderer } from '../src/chartlab/render';
import type { KeyValue } from '../src/chartlab/store';
import type { Quote } from '../src/feed';
import type { AppContext } from '../src/panels/base';

const IST = 19_800;
const today = Math.floor((Date.UTC(2026, 9, 14, 9, 15) / 1000 + IST) / 86_400) * 86_400;
const bars: Bar[] = Array.from({ length: 80 }, (_, i) => ({ t: today - (79 - i) * 86_400, o: 100 + i, h: 103 + i, l: 99 + i, c: 101 + i + (i % 3), v: 1000 + i }));

class Memory implements KeyValue {
  data: Record<string, string> = {};
  getItem(k: string): string | null {
    return this.data[k] ?? null;
  }
  setItem(k: string, v: string): void {
    this.data[k] = v;
  }
}

interface Harness {
  view: ChartLabView;
  paints: number;
  ticks: number;
  fetched: [string, string, number | null, boolean][];
  quotes: (qs: Quote[]) => void;
  storage: Memory;
  shown: (number | null)[]; // what each range put in view
}

function harness(storage = new Memory()): Harness {
  const h = { paints: 0, ticks: 0, fetched: [] as Harness['fetched'], quotes: (() => {}) as (qs: Quote[]) => void, storage, shown: [] as (number | null)[] } as Harness;
  const renderer = (): ChartRenderer => ({
    paint: () => void h.paints++,
    tick: () => void h.ticks++,
    fit: () => {},
    showLast: (c) => void h.shown.push(c),
    onCrosshair: () => {},
    paneElement: () => null,
    paneOf: (uid) => REGISTRY.length + Number(uid.slice(1)), // any number: each indicator has its own pane in this stand-in
    snapshot: () => null,
    applyTheme: () => {},
    dispose: () => {},
  });
  const fetchBars: BarsFetcher = async (symbol, tf, limit, adjusted) => {
    h.fetched.push([symbol, tf, limit, adjusted]);
    return { bars: symbol === 'NIFTY 50' ? bars.slice(10) : bars, note: '' };
  };
  const ctx = {
    feed: { onQuotes: (fn: (qs: Quote[]) => void) => ((h.quotes = fn), () => {}), need: vi.fn(), release: vi.fn(), quotes: new Map() },
    bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => undefined },
    watchlist: { items: ['SBIN'], subscribe: () => () => {} },
  } as unknown as AppContext;
  h.view = new ChartLabView(ctx, { renderer, fetchBars, storage });
  return h;
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const click = (root: HTMLElement, selector: string) => (root.querySelector(selector) as HTMLElement).click();

async function shown(): Promise<Harness> {
  const h = harness();
  h.view.show('SBIN');
  await flush();
  return h;
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe('chart lab page', () => {
  it('loads daily bars for the symbol, paints them and shows the market badge and data window', async () => {
    const h = await shown();
    expect(h.fetched[0]).toEqual(['SBIN', '1D', null, true]);
    expect(h.view.model.bars).toHaveLength(80);
    expect(h.paints).toBeGreaterThan(0);
    expect(h.view.element.querySelector('.lab-head')?.textContent).toContain('SBIN · 1D');
    expect(h.view.element.querySelector('.lab-window')?.textContent).toContain('Data window');
    expect(h.view.element.querySelector('.lab-badge')?.textContent).toMatch(/market (open|closed)/);
  });

  it('the Indicators list holds every indicator, and adding one gives a legend row, a data-window block, and undo and redo', async () => {
    const h = await shown();
    click(h.view.element, '.lab-ind');
    const items = [...h.view.element.querySelectorAll('.lab-item[data-indicator]')].map((i) => (i as HTMLElement).dataset.indicator);
    expect(items).toEqual(REGISTRY.map((d) => d.id));
    click(h.view.element, '.lab-item[data-indicator="rsi"]');
    expect(h.view.element.querySelector('.lab-lrow .lg-name')?.textContent).toBe('RSI 14');
    expect(h.view.element.querySelector('.lab-window')?.textContent).toContain('RSI 14');
    expect(h.view.element.querySelector('.lab-pop')).toHaveProperty('hidden', true);
    const undo = [...h.view.element.querySelectorAll('button')].find((b) => b.textContent === 'Undo') as HTMLButtonElement;
    const redo = [...h.view.element.querySelectorAll('button')].find((b) => b.textContent === 'Redo') as HTMLButtonElement;
    expect([undo.disabled, redo.disabled]).toEqual([false, true]);
    undo.click();
    expect(h.view.model.indicators).toHaveLength(0);
    expect(h.view.element.querySelector('.lab-lrow')).toBeNull();
    redo.click();
    expect(h.view.model.indicators.map((i) => i.id)).toEqual(['rsi']);
  });

  it('search narrows the indicator list', async () => {
    const h = await shown();
    click(h.view.element, '.lab-ind');
    const search = h.view.element.querySelector('.lab-pop input') as HTMLInputElement;
    search.value = 'bands';
    search.dispatchEvent(new Event('input'));
    expect([...h.view.element.querySelectorAll('.lab-item[data-indicator]')].map((i) => (i as HTMLElement).dataset.indicator)).toEqual(['bb']);
  });

  it('the gear opens the settings, a change is applied and shown in the legend, and a bad one is refused in place', async () => {
    const h = await shown();
    h.view.model.addIndicator('rsi');
    h.view.model.setScale('auto'); // any structural change repaints the legends
    click(h.view.element, '[data-action="settings"]');
    const input = h.view.element.querySelector('[data-input="length"]') as HTMLInputElement;
    expect(input.value).toBe('14');
    input.value = '';
    click(h.view.element, '.lab-dialog .btn:last-child');
    expect(h.view.element.querySelector('.lab-dialog .bad')?.textContent).toMatch(/number/);
    expect(h.view.model.indicators[0].params).toEqual({ length: 14 });
    input.value = '21';
    click(h.view.element, '.lab-dialog .btn:last-child');
    expect(h.view.element.querySelector('.lab-dialog')).toBeNull();
    expect(h.view.model.indicators[0].params).toEqual({ length: 21 });
    expect(h.view.element.querySelector('.lab-lrow .lg-name')?.textContent).toBe('RSI 21');
  });

  it('hide keeps the legend row greyed, and remove deletes it', async () => {
    const h = await shown();
    h.view.model.addIndicator('ema');
    h.view.model.setScale('auto');
    click(h.view.element, '[data-action="hide"]');
    expect(h.view.model.indicators[0].hidden).toBe(true);
    expect(h.view.element.querySelector('.lab-lrow.off')).not.toBeNull();
    click(h.view.element, '[data-action="remove"]');
    expect(h.view.model.indicators).toHaveLength(0);
  });

  it('scale, chart type and ADJ change the model, and ADJ asks the server for raw bars', async () => {
    const h = await shown();
    click(h.view.element, '[data-scale="log"]');
    expect(h.view.model.scale).toBe('log');
    const type = h.view.element.querySelector('.lab-type') as HTMLSelectElement;
    type.value = 'heikin';
    type.dispatchEvent(new Event('change'));
    expect(h.view.model.type).toBe('heikin');
    click(h.view.element, '[data-action="adjust"]');
    await flush();
    expect(h.view.model.adjusted).toBe(false);
    expect(h.fetched[h.fetched.length - 1]).toEqual(['SBIN', '1D', null, false]);
  });

  it('an interval implies its own ranges and a range button only moves the window over the bars already loaded', async () => {
    const h = await shown();
    expect([...h.view.element.querySelectorAll('[data-range]')].map((b) => b.textContent)).toEqual(['1M', '3M', '6M', 'YTD', '1Y', '5Y', '8Y', 'All']);
    expect(h.shown[h.shown.length - 1]).toBe(252); // the default range, 1Y, is in view, with every stored bar loaded
    const loads = h.fetched.length;
    click(h.view.element, '[data-range="1M"]');
    await flush();
    expect(h.fetched.length).toBe(loads); // no new request: the earlier bars are already there to scroll back to
    expect(h.shown[h.shown.length - 1]).toBe(22);
    click(h.view.element, '[data-range="All"]');
    expect(h.shown[h.shown.length - 1]).toBeNull();
    click(h.view.element, '[data-interval="5m"]');
    await flush();
    expect(h.fetched[h.fetched.length - 1].slice(0, 2)).toEqual(['SBIN', '5m']);
    expect([...h.view.element.querySelectorAll('[data-range]')].map((b) => b.textContent)).toEqual(['1D', '5D', '1M']);
  });

  it('weekly and monthly bars are built from every daily bar, and a derived interval is asked for from a finer source', async () => {
    const h = await shown();
    click(h.view.element, '[data-interval="1W"]');
    await flush();
    expect(h.fetched[h.fetched.length - 1].slice(0, 3)).toEqual(['SBIN', '1D', null]); // all the daily bars, then built into weeks
    expect(h.view.model.tf).toBe('1W');
    expect(h.view.model.bars.length).toBeLessThan(80); // 80 daily bars make about 16 weeks
    click(h.view.element, '[data-interval="2m"]');
    await flush();
    expect(h.fetched[h.fetched.length - 1].slice(0, 2)).toEqual(['SBIN', '1m']);
    click(h.view.element, '[data-interval="1h"]');
    await flush();
    expect(h.fetched[h.fetched.length - 1].slice(0, 2)).toEqual(['SBIN', '1h']);
  });

  it('every chart type is offered, and a price-move type says indicators are not drawn on it', async () => {
    const h = await shown();
    const type = h.view.element.querySelector('.lab-type') as HTMLSelectElement;
    expect([...type.options].map((o) => o.value)).toEqual(['candles', 'hollow', 'bars', 'line', 'area', 'baseline', 'columns', 'step', 'heikin', 'renko', 'linebreak', 'range', 'highlow', 'volcandles', 'kagi', 'pnf']);
    h.view.model.addIndicator('rsi');
    type.value = 'renko';
    type.dispatchEvent(new Event('change'));
    expect(h.view.element.querySelector('.lab-note')?.textContent).toMatch(/not drawn on Renko/);
    type.value = 'candles';
    type.dispatchEvent(new Event('change'));
    expect(h.view.model.derived).toBe(false);
  });

  it('compare fetches the other symbol, draws it and can be cleared', async () => {
    const h = await shown();
    const box = h.view.element.querySelector('.lab-compare') as HTMLInputElement;
    box.value = 'nifty 50';
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await flush();
    expect(h.fetched[h.fetched.length - 1][0]).toBe('NIFTY 50');
    expect(h.view.model.compare?.symbol).toBe('NIFTY 50');
    expect(h.view.model.compare?.bars.length).toBe(70);
    box.value = '';
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(h.view.model.compare).toBeNull();
  });

  it('templates save under a name and load back, and the chart state survives a new page on the same storage', async () => {
    const h = await shown();
    h.view.model.addIndicator('macd');
    h.view.model.setType('line');
    vi.stubGlobal('prompt', () => 'My setup');
    const select = h.view.element.querySelector('.lab-templates') as HTMLSelectElement;
    select.value = ':save';
    select.dispatchEvent(new Event('change'));
    expect([...select.options].map((o) => o.value)).toContain('load:My setup');
    click(h.view.element, '[data-action="remove"]');
    expect(h.view.model.indicators).toHaveLength(0);
    select.value = 'load:My setup';
    select.dispatchEvent(new Event('change'));
    expect(h.view.model.indicators.map((i) => i.id)).toEqual(['macd']);
    const again = harness(h.storage); // a new page on the same browser storage
    expect(again.view.state.type).toBe('line');
    expect(again.view.state.indicators.map((i) => i.id)).toEqual(['macd']);
    vi.unstubAllGlobals();
  });

  it('a live quote updates the last bar without a full repaint', async () => {
    const h = await shown();
    const paints = h.paints;
    const last = h.view.model.bars[h.view.model.bars.length - 1];
    h.quotes([{ s: 'SBIN', l: last.c + 1, pc: last.c, t: Date.now() } as Quote]);
    expect(h.ticks).toBe(1);
    expect(h.paints).toBe(paints);
    expect(h.view.model.bars[h.view.model.bars.length - 1].c).toBe(last.c + 1);
    h.quotes([{ s: 'OTHER', l: 5, pc: 5, t: Date.now() } as Quote]);
    expect(h.ticks).toBe(1);
  });
});
