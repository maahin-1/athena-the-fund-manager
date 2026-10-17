import type { Quote } from '../feed';
import { compact, pct, price, signed } from '../format';
import {
  activeList, addSection, colourSection, COLUMN_TITLES, COLUMNS, cycleSort, deleteList, exportText, FLAGS, filterByFlag, importText, isIndexSymbol, moveItem,
  newList, nextFlag, removeSection, removeSymbol, renameList, renameSection, setActive, setColumns, setFlag, sortedSymbols, symbolsInText, toggleSection,
  type Column, type Flag, type List, type SortKey,
} from '../watchlists/model';
import { el, Panel, type AppContext } from './base';

const MAX_FED = 200; // the most symbols asked of the feed at once; rows beyond are shown without live values

interface Cell {
  text: string;
  dir?: 'up' | 'dn' | 'flat';
}

interface Row {
  tr: HTMLTableRowElement;
  cells: Map<Column, HTMLElement>;
  src: HTMLElement;
  prev: number | null;
}

type Mode = { kind: 'new' } | { kind: 'rename' } | { kind: 'section' } | { kind: 'sectionName'; index: number } | null;
type Pop = 'columns' | 'transfer' | null;

/** The watchlist: many named lists with sections and flags, columns of your choice, sorting by a heading, drag to reorder, text import and export.
 * Index rows take their values from the exchange's index snapshot; stocks from the live feed. Clicking a row moves every panel linked to this one. */
export class WatchlistPanel extends Panel {
  private rows = new Map<string, Row>();
  private offs: (() => void)[] = [];
  private flagFilter: Flag | null = null;
  private mode: Mode = null;
  private pop: Pop = null;
  private transferText = '';
  private transferNote = '';
  private deleteArmed = false;
  private dragFrom: number | null = null;
  private resort: ReturnType<typeof setTimeout> | null = null;
  private closes = new Map<string, { last: number; prev: number }>(); // index closes from stored daily bars, until the snapshot arrives

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.setTitle('Watchlist');
    this.start();
  }

  protected build(): void {
    this.offs.push(this.ctx.watchlist.subscribe(() => this.render()), this.ctx.indices.subscribe(() => this.paintIndices()));
    this.render();
  }

  private get list(): List {
    return activeList(this.ctx.watchlist.doc);
  }

  // ---- values --------------------------------------------------------------------------------------------------------------------------

  private quote(symbol: string): Quote | undefined {
    return this.ctx.feed.quotes.get(symbol);
  }

  /** The numbers behind a row, whether it is a stock (the feed) or an index (the snapshot, else the stored close). */
  private numbers(symbol: string): { last: number | null; prev: number | null; o: number | null; h: number | null; lo: number | null; v: number | null; h52: number | null; l52: number | null } {
    if (isIndexSymbol(symbol)) {
      const ix = this.ctx.indices.get(symbol);
      if (ix) return { last: ix.l, prev: ix.pc, o: ix.o, h: ix.h, lo: ix.lo, v: null, h52: ix.h52, l52: ix.l52 };
      const close = this.closes.get(symbol);
      return { last: close?.last ?? null, prev: close?.prev ?? null, o: null, h: null, lo: null, v: null, h52: null, l52: null };
    }
    const q = this.quote(symbol);
    return { last: q?.l ?? null, prev: q?.pc ?? null, o: q?.o ?? null, h: q?.h ?? null, lo: q?.lo ?? null, v: q?.v ?? null, h52: q?.h52 ?? null, l52: q?.l52 ?? null };
  }

  private sortValue = (symbol: string, key: SortKey): number | string | null => {
    if (key === 'sym') return symbol;
    const n = this.numbers(symbol);
    const change = n.last !== null && n.prev ? n.last - n.prev : null;
    switch (key) {
      case 'last': return n.last;
      case 'chg': return change;
      case 'chgpct': return change !== null && n.prev ? (change / n.prev) * 100 : null;
      case 'open': return n.o;
      case 'high': return n.h;
      case 'low': return n.lo;
      case 'prev': return n.prev;
      case 'volume': return n.v;
      case 'h52': return n.h52;
      case 'l52': return n.l52;
      case 'src': return this.quote(symbol)?.src ?? null;
    }
  };

  private cell(symbol: string, column: Column): Cell {
    const n = this.numbers(symbol);
    const change = n.last !== null && n.prev ? n.last - n.prev : null;
    const dir = change === null ? undefined : change > 0 ? 'up' : change < 0 ? 'dn' : 'flat';
    const money = (x: number | null): Cell => ({ text: x === null ? '—' : price(x) });
    switch (column) {
      case 'last': return money(n.last);
      case 'chg': return { text: change === null ? '' : signed(change), dir };
      case 'chgpct': return { text: change === null || !n.prev ? '' : pct((change / n.prev) * 100), dir };
      case 'open': return money(n.o);
      case 'high': return money(n.h);
      case 'low': return money(n.lo);
      case 'prev': return money(n.prev);
      case 'volume': return { text: n.v === null ? '—' : compact(n.v) };
      case 'h52': return money(n.h52);
      case 'l52': return money(n.l52);
      case 'src': return { text: isIndexSymbol(symbol) ? 'index' : this.quote(symbol)?.src === 'stream' ? 'live' : this.quote(symbol) ? 'delayed' : '—' };
    }
  }

  // ---- drawing -------------------------------------------------------------------------------------------------------------------------

  private render(): void {
    this.body.replaceChildren();
    this.rows.clear();
    const list = this.list;
    this.body.append(this.toolbar(list));
    if (this.mode) this.body.append(this.inputRow());
    if (this.pop === 'columns') this.body.append(this.columnsPopover(list));
    if (this.pop === 'transfer') this.body.append(this.transferPopover(list));
    const table = el('table', 'wl');
    table.append(this.head(list));
    const flat = sortedSymbols(list, this.sortValue);
    const visible: string[] = [];
    if (flat || this.flagFilter) {
      const keep = new Set(filterByFlag(list, this.flagFilter).map((s) => s.s));
      for (const s of (flat ?? filterByFlag(list, null).map((x) => x.s)).filter((x) => keep.has(x))) {
        const at = list.items.findIndex((i) => i.t === 'sym' && i.s === s);
        table.append(this.symbolRow(list, at, false));
        visible.push(s);
      }
    } else {
      let open = true;
      list.items.forEach((item, at) => {
        if (item.t === 'section') {
          open = !item.closed;
          table.append(this.sectionRow(list, at));
        } else if (open) {
          table.append(this.symbolRow(list, at, true));
          visible.push(item.s);
        }
      });
    }
    this.body.append(table);
    const stocks = visible.filter((s) => !isIndexSymbol(s)).slice(0, MAX_FED);
    this.ctx.feed.need(this.id, stocks);
    for (const s of this.rows.keys()) this.paintRow(s, false);
    for (const s of visible.filter(isIndexSymbol)) if (!this.ctx.indices.has(s)) void this.loadClose(s);
    this.select(this.symbol);
  }

  private toolbar(list: List): HTMLElement {
    const bar = el('div', 'wl-bar');
    const pick = el('select', 'wl-lists');
    pick.title = 'Switch watchlist';
    for (const l of this.ctx.watchlist.doc.lists) {
      const o = new Option(l.name, l.id);
      o.selected = l.id === list.id;
      pick.append(o);
    }
    pick.addEventListener('change', () => this.ctx.watchlist.update((d) => setActive(d, pick.value)));
    const button = (text: string, title: string, run: () => void, cls = ''): HTMLButtonElement => {
      const b = el('button', `wl-btn ${cls}`.trim(), text);
      b.title = title;
      b.addEventListener('click', run);
      return b;
    };
    const flags = el('select', 'wl-flagfilter');
    flags.title = 'Show only the symbols with this flag';
    flags.append(new Option('All flags', ''));
    for (const f of FLAGS) {
      const o = new Option(f, f);
      o.selected = f === this.flagFilter;
      flags.append(o);
    }
    flags.addEventListener('change', () => {
      this.flagFilter = (flags.value || null) as Flag | null;
      this.render();
    });
    const del = button(this.deleteArmed ? 'Sure?' : 'Delete', 'Delete this watchlist (click twice)', () => {
      if (!this.deleteArmed) {
        this.deleteArmed = true;
        this.render();
        setTimeout(() => {
          if (this.deleteArmed) {
            this.deleteArmed = false;
            if (!this.disposed) this.render();
          }
        }, 3000);
        return;
      }
      this.deleteArmed = false;
      this.ctx.watchlist.update((d) => deleteList(d, list.id));
    }, this.deleteArmed ? 'armed' : '');
    del.disabled = this.ctx.watchlist.doc.lists.length <= 1;
    bar.append(
      pick,
      button('New', 'Make a new watchlist', () => this.setMode({ kind: 'new' })),
      button('Rename', 'Rename this watchlist', () => this.setMode({ kind: 'rename' })),
      del,
      button('+ Section', 'Add a heading at the end of the list', () => this.setMode({ kind: 'section' })),
      button('Columns', 'Choose the columns', () => this.togglePop('columns')),
      button('Import / Export', 'Copy the list as text or paste symbols in', () => this.togglePop('transfer')),
      flags,
    );
    return bar;
  }

  private setMode(mode: Mode): void {
    this.mode = mode;
    this.render();
    (this.body.querySelector('.wl-input') as HTMLInputElement | null)?.focus();
  }

  private togglePop(pop: Pop): void {
    this.pop = this.pop === pop ? null : pop;
    if (this.pop === 'transfer') {
      this.transferText = exportText(this.list);
      this.transferNote = '';
    }
    this.render();
  }

  private inputRow(): HTMLElement {
    const mode = this.mode!;
    const row = el('div', 'wl-inputrow');
    const input = el('input', 'wl-input');
    input.maxLength = 40;
    input.placeholder = mode.kind === 'new' ? 'name of the new list' : mode.kind === 'rename' ? 'new name for this list' : mode.kind === 'section' ? 'heading' : 'new heading';
    if (mode.kind === 'rename') input.value = this.list.name;
    if (mode.kind === 'sectionName') {
      const item = this.list.items[mode.index];
      if (item?.t === 'section') input.value = item.title;
    }
    const done = (): void => {
      const text = input.value;
      this.mode = null;
      this.ctx.watchlist.update((d) =>
        mode.kind === 'new' ? newList(d, text) : mode.kind === 'rename' ? renameList(d, this.list.id, text) : mode.kind === 'section' ? addSection(d, text) : renameSection(d, mode.index, text),
      );
      this.render();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') done();
      if (e.key === 'Escape') this.setMode(null);
      e.stopPropagation();
    });
    const ok = el('button', 'wl-btn', 'OK');
    ok.addEventListener('click', done);
    const cancel = el('button', 'wl-btn', 'Cancel');
    cancel.addEventListener('click', () => this.setMode(null));
    row.append(input, ok, cancel);
    return row;
  }

  private columnsPopover(list: List): HTMLElement {
    const box = el('div', 'wl-pop');
    for (const c of COLUMNS) {
      const label = el('label', 'wl-check');
      const box1 = el('input');
      box1.type = 'checkbox';
      box1.checked = list.columns.includes(c);
      box1.addEventListener('change', () => {
        const next = box1.checked ? [...list.columns, c] : list.columns.filter((x) => x !== c);
        this.ctx.watchlist.update((d) => setColumns(d, next));
      });
      label.append(box1, document.createTextNode(` ${COLUMN_TITLES[c]}`));
      box.append(label);
    }
    return box;
  }

  private transferPopover(list: List): HTMLElement {
    const box = el('div', 'wl-pop');
    const area = el('textarea', 'wl-text');
    area.value = this.transferText;
    area.rows = 6;
    area.spellcheck = false;
    area.addEventListener('input', () => (this.transferText = area.value));
    area.addEventListener('keydown', (e) => e.stopPropagation());
    const note = el('div', 'wl-note', this.transferNote);
    const imp = el('button', 'wl-btn', 'Import into this list');
    imp.addEventListener('click', () => void this.runImport());
    const save = el('button', 'wl-btn', 'Download .txt');
    save.addEventListener('click', () => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([exportText(list)], { type: 'text/plain' }));
      a.download = `${list.name}.txt`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    });
    const buttons = el('div', 'wl-btns');
    buttons.append(imp, save);
    box.append(area, buttons, note);
    return box;
  }

  private async runImport(): Promise<void> {
    const wanted = symbolsInText(this.transferText);
    const known = new Set<string>();
    await Promise.all(wanted.map(async (s) => ((await this.ctx.isKnown(s)) ? known.add(s) : undefined)));
    const result = importText(this.ctx.watchlist.doc, this.transferText, (s) => known.has(s));
    this.ctx.watchlist.update(() => result.doc);
    this.transferNote = `added ${result.added}${result.skipped.length ? `; not listed, skipped: ${result.skipped.slice(0, 8).join(', ')}${result.skipped.length > 8 ? '…' : ''}` : ''}`;
    this.transferText = exportText(this.list);
    this.render();
  }

  private head(list: List): HTMLElement {
    const head = el('tr', 'wl-head');
    const th = (key: SortKey, text: string): HTMLElement => {
      const cell = el('th', 'wl-th', text + (list.sort.key === key ? (list.sort.dir === 1 ? ' ▲' : ' ▼') : ''));
      cell.title = 'Sort by this column (click again to reverse, once more to clear)';
      cell.addEventListener('click', () => this.ctx.watchlist.update((d) => cycleSort(d, key)));
      return cell;
    };
    head.append(th('sym', 'Symbol'), ...list.columns.map((c) => th(c, COLUMN_TITLES[c])));
    return head;
  }

  private sectionRow(list: List, at: number): HTMLElement {
    const item = list.items[at];
    if (item.t !== 'section') throw new Error('not a section');
    const tr = el('tr', 'wl-section');
    tr.dataset.index = String(at);
    if (item.color) tr.dataset.color = item.color;
    const cell = el('td', '', '');
    cell.colSpan = list.columns.length + 1;
    const title = el('span', 'wl-title', `${item.closed ? '▸' : '▾'} ${item.title}`);
    title.title = 'Click to fold; double-click to rename';
    title.addEventListener('click', () => this.ctx.watchlist.update((d) => toggleSection(d, at)));
    title.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      this.setMode({ kind: 'sectionName', index: at });
    });
    const dot = el('button', 'wl-flag sec', '');
    dot.dataset.color = item.color ?? '';
    dot.title = 'Colour of this heading';
    dot.addEventListener('click', (e) => {
      e.stopPropagation();
      this.ctx.watchlist.update((d) => colourSection(d, at, nextFlag(item.color)));
    });
    const x = el('button', 'x', '×');
    x.title = 'Remove this heading (its symbols stay)';
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      this.ctx.watchlist.update((d) => removeSection(d, at));
    });
    cell.append(title, dot, x);
    tr.append(cell);
    this.draggable(tr, at);
    return tr;
  }

  private symbolRow(list: List, at: number, sections: boolean): HTMLElement {
    const item = list.items[at];
    if (item.t !== 'sym') throw new Error('not a symbol');
    const symbol = item.s;
    const tr = el('tr', 'wl-row');
    tr.dataset.symbol = symbol;
    tr.dataset.index = String(at);
    const name = el('td', 'sym');
    const src = el('i', 'src');
    const flag = el('button', 'wl-flag', '');
    flag.dataset.color = item.flag ?? '';
    flag.title = 'Flag this symbol (click to change colour)';
    flag.addEventListener('click', (e) => {
      e.stopPropagation();
      this.ctx.watchlist.update((d) => setFlag(d, symbol, nextFlag(item.flag)));
    });
    const label = el('span', 'wl-sym', symbol);
    const x = el('button', 'x', '×');
    x.title = `Remove ${symbol}`;
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      this.ctx.watchlist.update((d) => removeSymbol(d, symbol));
    });
    name.append(flag, src, label, x);
    tr.append(name);
    const cells = new Map<Column, HTMLElement>();
    for (const c of list.columns) {
      const td = el('td', 'num', '');
      td.dataset.col = c;
      cells.set(c, td);
      tr.append(td);
    }
    tr.addEventListener('click', () => {
      this.select(symbol);
      this.pick(symbol);
    });
    if (sections) this.draggable(tr, at);
    this.rows.set(symbol, { tr, cells, src, prev: null });
    return tr;
  }

  /** Drag a row or heading; drop on the upper half of another row to go before it, the lower half to go after it. */
  private draggable(tr: HTMLElement, at: number): void {
    tr.draggable = true;
    tr.addEventListener('dragstart', (e) => {
      this.dragFrom = at;
      e.dataTransfer?.setData('text/plain', String(at));
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
      tr.classList.add('dragging');
    });
    tr.addEventListener('dragend', () => {
      this.dragFrom = null;
      this.body.querySelectorAll('.drop-above,.drop-below,.dragging').forEach((n) => n.classList.remove('drop-above', 'drop-below', 'dragging'));
    });
    tr.addEventListener('dragover', (e) => {
      if (this.dragFrom === null) return;
      e.preventDefault();
      const r = tr.getBoundingClientRect();
      const below = e.clientY > r.top + r.height / 2;
      tr.classList.toggle('drop-below', below);
      tr.classList.toggle('drop-above', !below);
    });
    tr.addEventListener('dragleave', () => tr.classList.remove('drop-above', 'drop-below'));
    tr.addEventListener('drop', (e) => {
      e.preventDefault();
      const from = this.dragFrom;
      this.dragFrom = null;
      if (from === null) return;
      const below = tr.classList.contains('drop-below');
      // dropping under an open heading puts the symbol inside it; under the last row of a heading it stays in that heading
      const to = at + (below ? 1 : 0);
      this.ctx.watchlist.update((d) => moveItem(d, from, to));
    });
  }

  // ---- live values ---------------------------------------------------------------------------------------------------------------------

  private paintRow(symbol: string, flash: boolean): void {
    const row = this.rows.get(symbol);
    if (!row) return;
    const last = this.numbers(symbol).last;
    for (const [column, td] of row.cells) {
      const c = this.cell(symbol, column);
      td.textContent = c.text;
      td.className = `num${c.dir ? ` ${c.dir}` : ''}`;
      if (column === 'last' && flash && last !== null && row.prev !== null && row.prev !== last) {
        td.classList.add(last > row.prev ? 'flash-up' : 'flash-dn');
      }
    }
    row.prev = last;
    const q = this.quote(symbol);
    row.src.dataset.src = q?.src ?? '';
    row.src.title = isIndexSymbol(symbol) ? 'exchange index snapshot' : q?.src === 'stream' ? 'live stream' : q ? 'polled from the exchange (delayed)' : '';
  }

  private paintIndices(): void {
    for (const s of this.rows.keys()) if (isIndexSymbol(s)) this.paintRow(s, true);
    if (this.list.sort.key) this.scheduleResort();
  }

  protected onQuotes(quotes: Quote[]): void {
    for (const q of quotes) this.paintRow(q.s, true);
    if (this.list.sort.key) this.scheduleResort();
  }

  /** A list sorted by a live column is put in order again now and then, not on every tick. */
  private scheduleResort(): void {
    if (this.resort || this.dragFrom !== null || this.mode || this.pop) return;
    this.resort = setTimeout(() => {
      this.resort = null;
      if (!this.disposed && this.dragFrom === null) this.render();
    }, 1500);
  }

  private async loadClose(name: string): Promise<void> {
    if (this.closes.has(name)) return;
    try {
      const r = await fetch(`/api/bars?symbol=${encodeURIComponent(name)}&tf=1D&limit=2`);
      const body = (await r.json()) as { bars: { c: number }[] };
      const [prev, last] = body.bars.slice(-2);
      if (prev && last) {
        this.closes.set(name, { last: last.c, prev: prev.c });
        this.paintRow(name, false);
      }
    } catch {
      /* the server may be restarting; the row keeps its dash */
    }
  }

  private select(symbol: string): void {
    this.rows.forEach((row, s) => row.tr.classList.toggle('selected', s === symbol));
  }

  protected symbolChanged(symbol: string): void {
    this.select(symbol);
  }

  dispose(): void {
    this.offs.forEach((off) => off());
    if (this.resort) clearTimeout(this.resort);
    super.dispose();
  }
}
