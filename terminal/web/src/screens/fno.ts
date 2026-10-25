import { compact, pct, price } from '../format';
import { el } from '../panels/base';
import { EVENT_LABELS, eventCounts, eventsText, filterBoard, noBoardFilter, readBoard, sortBoard, type Board, type BoardRow, type SortKey } from './fnologic';

export interface FnoHost {
  /** A row was clicked. */
  onSelect(symbol: string): void;
}

const COLUMNS: [SortKey, string, boolean][] = [
  ['symbol', 'Underlying', false], ['close', 'Future', true], ['price_change', 'Change', true], ['oi', 'Open interest', true], ['oi_change', 'OI change', true],
  ['volume', 'Volume', true], ['put_call', 'Put-call', true], ['events', 'What the detectors found', false],
];

const tone = (v: number | null): string => (v === null || v === 0 ? 'flat' : v > 0 ? 'up' : 'dn');

/** The futures and options board: every underlying with futures on the latest day of the stored derivatives history, with its change, open interest and the events the detectors
 *  found. End of day, from files; the live chain and the MCX contracts are on the Options page. */
export class FnoView {
  readonly element = el('div', 'fno');
  private board: Board | null = null;
  private filter = noBoardFilter();
  private sortBy: SortKey = 'events';
  private descending = true;
  private readonly note = el('div', 'note fno-note');
  private readonly controls = el('div', 'fno-controls');
  private readonly chips = el('div', 'presets fno-chips');
  private readonly host = el('div', 'stable-host fno-table');
  private readonly search = el('input', 'cmd ssearch');
  private readonly kindBox = el('select', 'picker');
  private loaded = 0;

  constructor(private owner: FnoHost, private fetcher: typeof fetch = (...a) => fetch(...a)) {
    this.search.placeholder = 'underlying';
    this.search.setAttribute('aria-label', 'search the board');
    this.search.addEventListener('input', () => {
      this.filter.text = this.search.value;
      this.paint();
    });
    this.kindBox.setAttribute('aria-label', 'kind of underlying');
    this.kindBox.append(new Option('Stocks and indices', ''), new Option('Stocks', 'stock'), new Option('Indices', 'index'));
    this.kindBox.addEventListener('change', () => {
      this.filter.kind = this.kindBox.value as '' | 'stock' | 'index';
      this.paint();
    });
    this.controls.append(this.search, this.kindBox);
    this.element.append(this.note, this.controls, this.chips, this.host);
  }

  /** Fetch the board (kept for five minutes) and show it. */
  async show(): Promise<void> {
    if (this.board && Date.now() - this.loaded < 300_000) return this.paint();
    this.note.textContent = 'loading the futures and options board…';
    try {
      const r = await this.fetcher('/api/flow/board');
      if (r.status === 404) {
        this.note.textContent = 'No derivatives history is stored yet. Load it with python -m athena.derivs.ingest.';
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      const board = readBoard(await r.json());
      if (!board) throw new Error('shape');
      this.board = board;
      this.loaded = Date.now();
      this.paint();
    } catch {
      this.note.textContent = 'The futures and options board is not available (the derivatives file may be loading); try again in a minute.';
    }
  }

  private paintChips(): void {
    this.chips.replaceChildren();
    for (const [kind, n] of eventCounts(this.board?.rows ?? [])) {
      const [label, meaning] = EVENT_LABELS[kind];
      const b = el('button', `btn${this.filter.events.has(kind) ? ' on' : ''}`, `${label} ${n}`);
      b.type = 'button';
      b.title = meaning;
      b.dataset.event = kind;
      b.addEventListener('click', () => {
        if (this.filter.events.has(kind)) this.filter.events.delete(kind);
        else this.filter.events.add(kind);
        this.paint();
      });
      this.chips.append(b);
    }
  }

  private paint(): void {
    const board = this.board;
    if (!board) return;
    this.note.textContent = board.day
      ? `${board.universe} underlyings with futures on ${board.day}, the latest day of the stored history. End of day only; the detectors read price and open interest against each stock's own last 60 days. Not advice.`
      : 'The derivatives history holds no futures yet.';
    this.paintChips();
    const found = sortBoard(filterBoard(board.rows, this.filter), this.sortBy, this.descending);
    const table = el('table', 'stable fno-grid');
    const head = el('tr');
    for (const [key, label, numeric] of COLUMNS) {
      const th = el('th', `${key === this.sortBy ? 'sorted' : ''}${numeric ? ' num' : ''}`.trim(), `${label}${key === this.sortBy ? (this.descending ? ' ▼' : ' ▲') : ''}`);
      th.dataset.column = key;
      th.addEventListener('click', () => {
        this.descending = key === this.sortBy ? !this.descending : numeric || key === 'events';
        this.sortBy = key;
        this.paint();
      });
      head.append(th);
    }
    table.append(head);
    for (const r of found) table.append(this.row(r));
    this.host.replaceChildren(table);
    if (found.length === 0) this.host.append(el('div', 'note', 'No underlying matches.'));
  }

  private row(r: BoardRow): HTMLTableRowElement {
    const tr = el('tr');
    tr.dataset.symbol = r.symbol;
    const cell = (text: string, cls = ''): void => {
      tr.append(el('td', cls, text));
    };
    cell(`${r.symbol}${r.kind === 'index' ? ' (index)' : ''}`);
    cell(price(r.close), 'num');
    cell(r.price_change === null ? '—' : pct(r.price_change * 100), `num ${tone(r.price_change)}`);
    cell(compact(r.oi), 'num');
    cell(r.oi_change === null ? '—' : pct(r.oi_change * 100), `num ${tone(r.oi_change)}`);
    cell(compact(r.volume), 'num');
    cell(r.put_call === null ? '—' : r.put_call.toFixed(2), 'num');
    const events = el('td', 'fno-events', eventsText(r) || '—');
    events.title = r.events.map((e) => e.line).join('\n');
    tr.append(events);
    tr.addEventListener('click', () => this.owner.onSelect(r.symbol));
    return tr;
  }
}
