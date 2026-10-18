// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { CalendarView } from '../src/screens/calendar';
import type { CalBody } from '../src/screens/calendarlogic';

const body: CalBody = {
  from: '2026-10-18', to: '2026-10-24',
  events: [
    { symbol: 'AAA', name: 'Alpha Ltd', date: '2026-10-23', purpose: 'Financial Results/Dividend', kinds: ['results', 'dividend'], description: 'To consider the results' },
    { symbol: 'BBB', name: 'Beta Ltd', date: '2026-10-19', purpose: 'Financial Results', kinds: ['results'], description: 'To consider the results' },
  ],
  actions: [{ symbol: 'DDD', name: 'Delta Ltd', date: '2026-10-19', kind: 'split', subject: 'Face Value Split' }],
  events_through: '2026-11-13', actions_through: '2026-10-23', truncated: false,
};

function make(extra: { screen?: Set<string> | null; watched?: string[]; status?: number } = {}) {
  const urls: string[] = [];
  const fetcher = vi.fn(async (url: string) => {
    urls.push(url);
    return extra.status && extra.status !== 200 ? new Response('no', { status: extra.status }) : new Response(JSON.stringify(body));
  }) as unknown as typeof fetch;
  const selected = vi.fn();
  const view = new CalendarView({ screenSymbols: () => extra.screen ?? null, watched: () => new Set(extra.watched ?? []), onSelect: selected }, fetcher, () => '2026-10-18');
  document.body.replaceChildren(view.element); // checkboxes only report a change while they are in the page
  return { view, urls, selected };
}

const rows = (v: CalendarView) => [...v.element.querySelectorAll('.cal-row')].map((r) => r.querySelector('.cal-sym')?.textContent);

describe('the calendar page', () => {
  it('asks for the next fortnight by default and groups what comes back by day, oldest first', async () => {
    const { view, urls } = make();
    await view.show();
    expect(urls[0]).toBe('/api/calendar?from=2026-10-18&to=2026-10-31');
    expect([...view.element.querySelectorAll('.cal-day h4')].map((h) => h.textContent)).toEqual(['Mon 19 Oct 2026 · 1 board meeting, 1 ex-date', 'Fri 23 Oct 2026 · 1 board meeting']);
    expect(rows(view)).toEqual(['BBB', 'DDD', 'AAA']);
  });

  it('says how far each source reaches, and that there is no economic calendar', async () => {
    const { view } = make();
    await view.show();
    const note = view.element.querySelector('.cal-note')!.textContent!;
    expect(note).toContain('Board meetings are known to 13 Nov 2026');
    expect(note).toContain('ex-dates announced to 23 Oct 2026');
    expect(note).toContain('no economic calendar');
  });

  it('refetches for another range and for two dates the reader types', async () => {
    const { view, urls } = make();
    await view.show();
    const range = view.element.querySelector('select[aria-label="date range"]') as HTMLSelectElement;
    range.value = 'past';
    range.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(urls).toContain('/api/calendar?from=2026-09-18&to=2026-10-18'));
    range.value = 'custom';
    range.dispatchEvent(new Event('change'));
    const [from, to] = [...view.element.querySelectorAll<HTMLInputElement>('input[type="date"]')];
    from.value = '2026-11-01';
    to.value = '2026-11-07';
    to.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(urls).toContain('/api/calendar?from=2026-11-01&to=2026-11-07'));
  });

  it('filters by category chips and by what is typed, without asking the server again', async () => {
    const { view, urls } = make();
    await view.show();
    const chip = (label: string) => [...view.element.querySelectorAll<HTMLButtonElement>('.cal-chip')].find((b) => b.textContent === label)!;
    chip('Splits and bonuses').click();
    expect(rows(view)).toEqual(['DDD']);
    chip('Splits and bonuses').click();
    chip('Results').click();
    expect(rows(view)).toEqual(['BBB', 'AAA']);
    chip('Results').click();
    const search = view.element.querySelector('input[aria-label="search the calendar"]') as HTMLInputElement;
    search.value = 'alpha';
    search.dispatchEvent(new Event('input'));
    expect(rows(view)).toEqual(['AAA']);
    expect(urls).toHaveLength(1);
  });

  it('can be limited to the stocks of the current screen or of the watchlist', async () => {
    const { view } = make({ screen: new Set(['AAA', 'DDD']), watched: ['BBB'] });
    await view.show();
    const only = (label: string) => view.element.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
    only('only stocks in my screen').click();
    expect(rows(view)).toEqual(['DDD', 'AAA']);
    only('only stocks in my screen').click();
    only('only my watchlist').click();
    expect(rows(view)).toEqual(['BBB']);
  });

  it('marks watchlist stocks, and opens a stock when its symbol is clicked', async () => {
    const { view, selected } = make({ watched: ['AAA'] });
    await view.show();
    expect(view.element.querySelector('.cal-row.watched .cal-sym')?.textContent).toBe('AAA');
    (view.element.querySelector('.cal-sym') as HTMLButtonElement).click();
    expect(selected).toHaveBeenCalledWith('BBB');
  });

  it('says so when nothing is in the range or the server cannot answer', async () => {
    const none = make({ screen: new Set() });
    await none.view.show();
    (none.view.element.querySelector('input[aria-label="only stocks in my screen"]') as HTMLInputElement).click();
    expect(none.view.element.textContent).toContain('Nothing in this range matches');
    const down = make({ status: 503 });
    await down.view.show();
    expect(down.view.element.textContent).toContain('The market database is busy');
  });
});
