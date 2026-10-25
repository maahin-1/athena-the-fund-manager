// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { FnoView } from '../src/screens/fno';
import { EVENT_LABELS, eventCounts, eventsText, filterBoard, noBoardFilter, readBoard, sortBoard, type BoardRow } from '../src/screens/fnologic';

const row = (symbol: string, over: Partial<BoardRow> = {}): BoardRow => ({
  symbol, kind: 'stock', close: 100, price_change: 0.01, oi: 1000, oi_change: 0.02, volume: 50, put_call: 0.8, events: [], ...over,
});
const ev = (kind: string) => ({ kind, line: `2026-10-23: ${kind}` });
const rows: BoardRow[] = [
  row('AAA', { price_change: 0.03, events: [ev('long_build_up'), ev('oi_spike')] }),
  row('BBB', { price_change: -0.04, oi: 5000, events: [ev('short_build_up')] }),
  row('CCC', { price_change: null, oi_change: null, put_call: null }),
  row('NIFTY', { kind: 'index', oi: 90000, events: [ev('put_call_up')] }),
];

describe('the board logic', () => {
  it('filters by kind, text and the event kinds a row must have one of', () => {
    expect(filterBoard(rows, noBoardFilter()).map((r) => r.symbol)).toEqual(['AAA', 'BBB', 'CCC', 'NIFTY']);
    expect(filterBoard(rows, { ...noBoardFilter(), kind: 'index' }).map((r) => r.symbol)).toEqual(['NIFTY']);
    expect(filterBoard(rows, { ...noBoardFilter(), text: ' bb ' }).map((r) => r.symbol)).toEqual(['BBB']);
    expect(filterBoard(rows, { ...noBoardFilter(), events: new Set(['oi_spike', 'short_build_up']) }).map((r) => r.symbol)).toEqual(['AAA', 'BBB']);
  });

  it('sorts either way and always puts a missing figure last', () => {
    expect(sortBoard(rows, 'price_change', true).map((r) => r.symbol)).toEqual(['AAA', 'NIFTY', 'BBB', 'CCC'].sort((a, b) => ['AAA', 'NIFTY', 'BBB', 'CCC'].indexOf(a) - ['AAA', 'NIFTY', 'BBB', 'CCC'].indexOf(b)));
    expect(sortBoard(rows, 'price_change', false).map((r) => r.symbol)).toEqual(['BBB', 'NIFTY', 'AAA', 'CCC']);
    expect(sortBoard(rows, 'events', true).map((r) => r.symbol)).toEqual(['AAA', 'BBB', 'NIFTY', 'CCC']);
    expect(sortBoard(rows, 'symbol', false).map((r) => r.symbol)).toEqual(['AAA', 'BBB', 'CCC', 'NIFTY']);
    expect(sortBoard(rows, 'oi', true)[0].symbol).toBe('NIFTY');
  });

  it('counts the rows that carry each event kind, in the listed order, and names the events', () => {
    expect(eventCounts(rows)).toEqual([['long_build_up', 1], ['short_build_up', 1], ['oi_spike', 1], ['put_call_up', 1]]);
    expect(eventsText(rows[0])).toBe('Long build-up, OI spike');
    expect(eventsText({ ...rows[0], events: [ev('something_new')] })).toBe('something_new');
    for (const [label, meaning] of Object.values(EVENT_LABELS)) expect(label && meaning).toBeTruthy();
  });

  it('reads a board from the server and refuses what is not one', () => {
    expect(readBoard({ day: '2026-10-23', rows: [row('AAA'), { nonsense: true }], events: { long_build_up: 1 }, universe: 1 })?.rows.map((r) => r.symbol)).toEqual(['AAA']);
    expect(readBoard({ day: null, rows: [], events: {}, universe: 0 })?.day).toBeNull();
    expect(readBoard(null)).toBeNull();
    expect(readBoard({ rows: 'no', day: '2026-01-01' })).toBeNull();
  });
});

describe('the board view', () => {
  const answer = (body: unknown, status = 200) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it('shows a row per underlying with the day, and selects one on a click', async () => {
    const picked: string[] = [];
    const view = new FnoView({ onSelect: (s) => picked.push(s) }, answer({ day: '2026-10-23', rows, events: {}, universe: 4 }));
    await view.show();
    expect(view.element.querySelector('.fno-note')?.textContent).toContain('4 underlyings with futures on 2026-10-23');
    const body = view.element.querySelectorAll('tr[data-symbol]');
    expect(body.length).toBe(4);
    expect(view.element.querySelector('tr[data-symbol="NIFTY"]')?.textContent).toContain('(index)');
    (view.element.querySelector('tr[data-symbol="BBB"]') as HTMLElement).click();
    expect(picked).toEqual(['BBB']);
    (view.element.querySelector('button[data-event="short_build_up"]') as HTMLElement).click();
    expect(view.element.querySelectorAll('tr[data-symbol]').length).toBe(1);
  });

  it('says what is wrong when there is no history or the server fails, and does not throw', async () => {
    const none = new FnoView({ onSelect: () => {} }, answer('no', 404));
    await none.show();
    expect(none.element.querySelector('.fno-note')?.textContent).toContain('No derivatives history is stored yet');
    const broken = new FnoView({ onSelect: () => {} }, answer('x', 503));
    await broken.show();
    expect(broken.element.querySelector('.fno-note')?.textContent).toContain('not available');
    const garbled = new FnoView({ onSelect: () => {} }, answer({ rows: 5 }));
    await garbled.show();
    expect(garbled.element.querySelector('.fno-note')?.textContent).toContain('not available');
  });

  it('an empty history says so', async () => {
    const view = new FnoView({ onSelect: () => {} }, answer({ day: null, rows: [], events: {}, universe: 0 }));
    await view.show();
    expect(view.element.querySelector('.fno-note')?.textContent).toContain('holds no futures yet');
  });
});
