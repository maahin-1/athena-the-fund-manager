import { el } from '../panels/base';
import { CATEGORIES, filterEntries, groupByDate, merge, rangeFor, type CalBody, type Entry, type Preset, type Range } from './calendarlogic';

export interface CalendarHost {
  /** The symbols of the Screen tab's current result, or null when it has none loaded. */
  screenSymbols(): Set<string> | null;
  /** The symbols in the active watchlist. */
  watched(): Set<string>;
  /** A stock was clicked. */
  onSelect(symbol: string): void;
}

const PRESETS: { id: Preset | 'custom'; label: string }[] = [
  { id: 'week', label: 'Next 7 days' }, { id: 'fortnight', label: 'Next 14 days' }, { id: 'month', label: 'Next 30 days' }, { id: 'past', label: 'Past 30 days' }, { id: 'custom', label: 'Choose dates' },
];

const indiaToday = (): string => new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });

const nice = (text: string | null): string => (text ? new Date(`${text}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : 'no date');

/** The calendar: the exchange's board meetings (results, dividends, fund raising) and corporate actions by ex-date for a range of dates, filtered by kind, text,
 *  the Screen tab's current result and the watchlist. The range is fetched; everything else filters what was fetched. */
export class CalendarView {
  readonly element = el('div', 'calendar');
  private body: CalBody | null = null;
  private entries: Entry[] = [];
  private range: Range;
  private preset: Preset | 'custom' = 'fortnight';
  private cats = new Set<string>();
  private token = 0;
  private readonly controls = el('div', 'cal-controls');
  private readonly note = el('div', 'note cal-note');
  private readonly list = el('div', 'cal-list');
  private readonly search = el('input', 'cmd');
  private readonly presetBox = el('select', 'picker');
  private readonly fromBox = el('input', 'cal-date');
  private readonly toBox = el('input', 'cal-date');
  private readonly screenOnly = el('input');
  private readonly watchOnly = el('input');
  private readonly chips = new Map<string, HTMLButtonElement>();

  constructor(private host: CalendarHost, private fetcher: typeof fetch = (...a) => fetch(...a), private today: () => string = indiaToday) {
    this.range = rangeFor('fortnight', this.today());
    this.presetBox.setAttribute('aria-label', 'date range');
    this.presetBox.append(...PRESETS.map((p) => new Option(p.label, p.id)));
    this.presetBox.value = this.preset;
    this.presetBox.addEventListener('change', () => {
      this.preset = this.presetBox.value as Preset | 'custom';
      if (this.preset !== 'custom') this.range = rangeFor(this.preset, this.today());
      this.syncDates();
      if (this.preset !== 'custom') void this.load();
    });
    for (const box of [this.fromBox, this.toBox]) {
      box.type = 'date';
      box.setAttribute('aria-label', box === this.fromBox ? 'from date' : 'to date');
      box.addEventListener('change', () => {
        if (this.fromBox.value && this.toBox.value) {
          this.range = { from: this.fromBox.value, to: this.toBox.value };
          void this.load();
        }
      });
    }
    this.search.placeholder = 'symbol, name or wording';
    this.search.setAttribute('aria-label', 'search the calendar');
    this.search.addEventListener('input', () => this.paint());
    const chips = el('div', 'cal-chips');
    for (const c of CATEGORIES) {
      const b = el('button', 'btn cal-chip', c.label);
      b.type = 'button';
      b.addEventListener('click', () => {
        if (this.cats.has(c.id)) this.cats.delete(c.id);
        else this.cats.add(c.id);
        b.classList.toggle('on', this.cats.has(c.id));
        this.paint();
      });
      this.chips.set(c.id, b);
      chips.append(b);
    }
    const toggle = (box: HTMLInputElement, label: string, text: string): HTMLElement => {
      box.type = 'checkbox';
      box.setAttribute('aria-label', label);
      box.addEventListener('change', () => this.paint());
      const wrap = el('label', 'sclean');
      wrap.append(box, document.createTextNode(` ${text}`));
      return wrap;
    };
    const top = el('div', 'cal-row-top');
    top.append(this.presetBox, this.fromBox, this.toBox, this.search, toggle(this.screenOnly, 'only stocks in my screen', 'only stocks in my screen'), toggle(this.watchOnly, 'only my watchlist', 'only my watchlist'));
    this.controls.append(top, chips);
    this.element.append(this.controls, this.note, this.list);
    this.syncDates();
  }

  private syncDates(): void {
    this.fromBox.value = this.range.from;
    this.toBox.value = this.range.to;
    this.fromBox.hidden = this.toBox.hidden = this.preset !== 'custom';
  }

  /** Show the page: fetch the range the first time. */
  async show(): Promise<void> {
    if (!this.body) await this.load();
    else this.paint();
  }

  /** The screen's result changed: redraw (the rows are already here). */
  refresh(): void {
    if (this.body) this.paint();
  }

  private async load(): Promise<void> {
    const mine = ++this.token;
    this.note.textContent = 'loading the calendar…';
    try {
      const r = await this.fetcher(`/api/calendar?from=${this.range.from}&to=${this.range.to}`);
      if (mine !== this.token) return;
      if (r.status === 400) return this.fail('Those dates cannot be used: give the start and the end in order, no more than a year apart.');
      if (!r.ok) throw new Error(String(r.status));
      this.body = (await r.json()) as CalBody;
      this.entries = merge(this.body);
      this.paint();
    } catch {
      if (mine === this.token) this.fail('The market database is busy (a build is running); try again in a minute.');
    }
  }

  private fail(text: string): void {
    this.body = null;
    this.entries = [];
    this.list.replaceChildren();
    this.note.textContent = text;
  }

  private paint(): void {
    const body = this.body;
    if (!body) return;
    const found = filterEntries(this.entries, {
      cats: this.cats,
      text: this.search.value,
      symbols: this.screenOnly.checked ? (this.host.screenSymbols() ?? new Set()) : null,
      watched: this.watchOnly.checked ? this.host.watched() : null,
    });
    const days = groupByDate(found);
    const meetings = found.filter((e) => e.source === 'meeting').length;
    this.note.textContent =
      `${meetings.toLocaleString('en-IN')} board meetings and ${(found.length - meetings).toLocaleString('en-IN')} ex-dates, ${nice(body.from)} to ${nice(body.to)}. ` +
      `Board meetings are known to ${nice(body.events_through)}; ex-dates announced to ${nice(body.actions_through)} (the exchange lists an action only once it is announced). ` +
      `There is no economic calendar (central bank decisions, inflation releases): no free source was found.${body.truncated ? ' The list was cut at 5,000 rows; narrow the range.' : ''}`;
    this.note.classList.add('cal-note');
    this.list.replaceChildren();
    if (!days.length) return void this.list.append(el('div', 'note', 'Nothing in this range matches.'));
    const watched = this.host.watched();
    for (const d of days) {
      const section = el('section', 'cal-day');
      const parts = [d.meetings ? `${d.meetings} board meeting${d.meetings === 1 ? '' : 's'}` : '', d.actions ? `${d.actions} ex-date${d.actions === 1 ? '' : 's'}` : ''].filter(Boolean);
      section.append(el('h4', '', `${d.label} · ${parts.join(', ')}`));
      for (const e of d.entries) section.append(this.row(e, watched.has(e.symbol)));
      this.list.append(section);
    }
  }

  private row(e: Entry, watched: boolean): HTMLElement {
    const row = el('div', `cal-row ${e.source}${watched ? ' watched' : ''}`);
    const sym = el('button', 'linklike cal-sym', e.symbol);
    sym.type = 'button';
    sym.title = `Open ${e.symbol}`;
    sym.addEventListener('click', () => this.host.onSelect(e.symbol));
    const badges = el('span', 'cal-badges');
    for (const c of e.cats) badges.append(el('span', `cal-badge ${c}`, CATEGORIES.find((x) => x.id === c)?.label ?? c));
    row.append(el('span', 'cal-star', watched ? '★' : ''), sym, el('span', 'cal-name', e.name), badges, el('span', 'cal-label', e.label), el('span', 'note cal-detail', e.detail));
    return row;
  }
}
