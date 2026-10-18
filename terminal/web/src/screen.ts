import { el, type AppContext } from './panels/base';
import { ColumnsMenu, QueryBar, SavedBar } from './screens/controls';
import { toCsv } from './screens/csv';
import { CalendarView } from './screens/calendar';
import { HeatmapView } from './screens/heatmap';
import { formatCell } from './screens/format';
import { SavedScreens, serverScreensIO, type SavedScreen } from './screens/store';
import { evaluate, limitsToQuery } from './screenquery';
import {
  COLUMNS, dataNote, DEFAULT_COLUMNS, filterRows, noFilters, PRESETS, RANGE_FILTERS, sortRows, toRows,
  type Cell, type Filters, type Row, type Style,
} from './screenlogic';

const MAX_SHOWN = 300;

/** The market screen: every stored stock and ETF in one table to filter and sort, with six presets. Reads the warehouse only; no market data and no model call. */
export class ScreenView {
  readonly element = el('div', 'screen');
  private rows: Row[] = [];
  private marketDay: string | null = null;
  private loaded = 0;
  private filters: Filters = noFilters();
  private sortBy = 'traded_value_cr';
  private descending = true;
  private presetLabel = '';
  private selected = '';
  private columns = [...DEFAULT_COLUMNS];
  private note = el('div', 'note');
  private presetRow = el('div', 'presets');
  private presetNote = el('div', 'note');
  private filterBox = el('div', 'sfilters');
  private actions = el('div', 'actions saction');
  private count = el('div', 'note');
  private tableHost = el('div', 'stable-host');
  private inputs = new Map<string, [HTMLInputElement, HTMLInputElement]>();
  private searchBox = el('input', 'cmd ssearch');
  private kindBox = el('select', 'picker');
  private trendBox = el('select', 'picker');
  private sectorBox = el('select', 'picker');
  private industryBox = el('select', 'picker');
  private indexBox = el('select', 'picker');
  private query = new QueryBar(() => this.paintTable());
  private saved = new SavedScreens(serverScreensIO());
  private savedLoaded = false;
  private savedBar = new SavedBar(this.saved, (name) => this.snapshot(name), (screen) => this.applyScreen(screen));
  private colsMenu = new ColumnsMenu(() => this.columns, (columns) => this.setColumns(columns));
  private tools = el('div', 'stools');
  private heat = new HeatmapView((symbol, row) => this.select(symbol, row));
  private cal = new CalendarView({
    screenSymbols: () => (this.rows.length ? new Set(this.found().map((r) => String(r.symbol))) : null),
    watched: () => new Set(this.ctx.watchlist.items),
    onSelect: (symbol) => this.select(symbol, this.rows.find((r) => r.symbol === symbol) ?? { symbol, name: symbol }),
  });
  private mode: 'table' | 'heatmap' | 'calendar' = 'table';
  private modeRow = el('div', 'smodes');
  private cleanBox = el('input');

  constructor(private ctx: AppContext) {
    this.element.hidden = true;
    this.buildControls();
    this.element.append(this.note, this.modeRow, this.query.element, this.savedBar.element, this.presetRow, this.presetNote, this.filterBox, this.actions, this.tools, this.count, this.tableHost, this.heat.element, this.cal.element);
    this.heat.element.hidden = true;
    this.cal.element.hidden = true;
  }

  /** Show the page; the table is fetched once and kept for five minutes. */
  async show(): Promise<void> {
    this.element.hidden = false;
    if (!this.savedLoaded) {
      this.savedLoaded = true;
      void this.saved.load().then(() => this.savedBar.refresh());
    }
    if (Date.now() - this.loaded < 300_000 && this.rows.length) return this.paint();
    this.note.textContent = 'loading the market screen…';
    try {
      const r = await fetch('/api/screen');
      if (!r.ok) throw new Error('busy');
      const body = (await r.json()) as { columns: string[]; rows: Cell[][]; market_day: string | null };
      this.rows = toRows(body.columns, body.rows);
      this.marketDay = body.market_day;
      this.loaded = Date.now();
      this.fillSectors();
      this.paint();
    } catch {
      this.note.textContent = 'The market database is busy (a build is running); try again in a minute.';
    }
  }

  hide(): void {
    this.element.hidden = true;
  }

  private buildControls(): void {
    for (const [mode, label] of [['table', 'Table'], ['heatmap', 'Heatmap'], ['calendar', 'Calendar']] as const) {
      const b = el('button', 'btn', label);
      b.dataset.mode = mode;
      b.addEventListener('click', () => {
        this.mode = mode;
        this.paintTable();
      });
      this.modeRow.append(b);
    }
    for (const p of PRESETS) {
      const b = el('button', 'btn', p.label);
      b.dataset.preset = p.label;
      b.addEventListener('click', () => this.applyPreset(p.label));
      this.presetRow.append(b);
    }
    const clear = el('button', 'btn', 'Clear filters');
    clear.addEventListener('click', () => this.applyPreset(''));
    const exportBtn = el('button', 'btn', 'Export CSV');
    exportBtn.type = 'button';
    exportBtn.addEventListener('click', () => this.exportCsv());
    this.tools.append(this.colsMenu.element, exportBtn);
    this.presetRow.append(clear);

    this.searchBox.placeholder = 'symbol or name';
    this.searchBox.setAttribute('aria-label', 'search the screen');
    this.searchBox.addEventListener('input', () => {
      this.filters.text = this.searchBox.value;
      this.paintTable();
    });
    this.kindBox.append(new Option('Stocks', 'stock'), new Option('ETFs', 'etf'), new Option('Stocks and ETFs', ''));
    this.kindBox.addEventListener('change', () => {
      this.filters.kinds = this.kindBox.value ? [this.kindBox.value] : [];
      this.paintTable();
    });
    this.trendBox.append(new Option('Any trend', ''), new Option('Aligned up', 'aligned_up'), new Option('Aligned down', 'aligned_down'), new Option('Mixed', 'not_aligned'));
    this.trendBox.addEventListener('change', () => {
      this.filters.trends = this.trendBox.value ? [this.trendBox.value] : [];
      this.paintTable();
    });
    this.sectorBox.addEventListener('change', () => {
      this.filters.sector = this.sectorBox.value;
      this.paintTable();
    });
    this.industryBox.setAttribute('aria-label', 'industry');
    this.industryBox.addEventListener('change', () => {
      this.filters.industry = this.industryBox.value;
      this.paintTable();
    });
    this.indexBox.setAttribute('aria-label', 'index');
    this.indexBox.addEventListener('change', () => {
      this.filters.index = this.indexBox.value;
      this.paintTable();
    });
    this.cleanBox.type = 'checkbox';
    this.cleanBox.addEventListener('change', () => {
      this.filters.cleanOnly = this.cleanBox.checked;
      this.paintTable();
    });
    const clean = el('label', 'sclean');
    clean.append(this.cleanBox, document.createTextNode(' no data flags'));

    const top = el('div', 'sfilter-row');
    top.append(this.searchBox, this.kindBox, this.trendBox, this.sectorBox, this.industryBox, this.indexBox, clean);
    const ranges = el('div', 'sranges');
    for (const [column, [label, unit]] of Object.entries(RANGE_FILTERS)) {
      const lo = el('input', 'snum');
      const hi = el('input', 'snum');
      lo.placeholder = 'min';
      hi.placeholder = 'max';
      lo.inputMode = hi.inputMode = 'decimal';
      lo.setAttribute('aria-label', `${label} minimum`);
      hi.setAttribute('aria-label', `${label} maximum`);
      const onInput = () => {
        this.filters.ranges[column] = [this.limit(lo.value, column), this.limit(hi.value, column)];
        this.presetLabel = '';
        this.paintTable();
      };
      lo.addEventListener('input', onInput);
      hi.addEventListener('input', onInput);
      this.inputs.set(column, [lo, hi]);
      const box = el('label', 'srange');
      box.append(el('span', 'k', `${label} (${unit})`), lo, hi);
      ranges.append(box);
    }
    this.filterBox.append(top, ranges);
  }

  /** The rows that pass the filters and the query, sorted as chosen. */
  private found(): Row[] {
    return sortRows(filterRows(this.rows, this.filters).filter((r) => evaluate(this.query.node, r)), this.sortBy, this.descending);
  }

  private setColumns(columns: string[]): void {
    this.columns = columns.filter((c) => c in COLUMNS);
    if (!this.columns.includes(this.sortBy)) this.sortBy = this.columns[0];
    this.colsMenu.sync();
    this.paintTable();
  }

  /** The screen as it is now, for saving under `name`. */
  private snapshot(name: string): SavedScreen {
    const ranges = Object.fromEntries(Object.entries(this.filters.ranges).map(([c, r]) => [c, [...r] as [number | null, number | null]]));
    return { name, query: this.query.text, filters: { ...this.filters, kinds: [...this.filters.kinds], trends: [...this.filters.trends], ranges }, columns: [...this.columns], sortBy: this.sortBy, descending: this.descending };
  }

  private applyScreen(screen: SavedScreen): void {
    const ranges = Object.fromEntries(Object.entries(screen.filters.ranges).map(([c, r]) => [c, [...r] as [number | null, number | null]]));
    this.filters = { ...screen.filters, kinds: [...screen.filters.kinds], trends: [...screen.filters.trends], ranges };
    this.query.text = screen.query;
    this.columns = [...screen.columns];
    this.sortBy = screen.sortBy;
    this.descending = screen.descending;
    this.presetLabel = '';
    this.syncControls();
    this.paintTable();
  }

  private exportCsv(): void {
    const blob = new Blob([toCsv(this.found(), this.columns)], { type: 'text/csv' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `screen-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  /** What the user typed in the shown unit, in the stored unit; empty or not a number is no limit. */
  private limit(text: string, column: string): number | null {
    const n = Number(text.trim());
    return text.trim() === '' || !Number.isFinite(n) ? null : n * RANGE_FILTERS[column][2];
  }

  private fillSectors(): void {
    const sectors = [...new Set(this.rows.map((r) => r.sector).filter((s): s is string => typeof s === 'string' && s !== ''))].sort();
    this.sectorBox.replaceChildren(new Option('Any sector', ''), ...sectors.map((s) => new Option(s, s)));
    const industries = [...new Set(this.rows.map((r) => r.industry).filter((s): s is string => typeof s === 'string' && s !== ''))].sort();
    this.industryBox.replaceChildren(new Option('Any industry', ''), ...industries.map((s) => new Option(s, s)));
    const indices = [...new Set(this.rows.flatMap((r) => (typeof r.indices === 'string' && r.indices ? r.indices.split('|') : [])))].sort();
    this.indexBox.replaceChildren(new Option('Any index', ''), ...indices.map((s) => new Option(s, s)));
  }

  private applyPreset(label: string): void {
    const p = PRESETS.find((x) => x.label === label);
    this.filters = noFilters();
    this.query.text = '';
    this.presetLabel = p?.label ?? '';
    if (p) {
      this.filters.ranges = Object.fromEntries(Object.entries(p.ranges).map(([c, r]) => [c, [...r] as [number | null, number | null]]));
      this.filters.kinds = [...p.kinds];
      this.filters.trends = [...p.trends];
    }
    this.syncControls();
    this.paintTable();
  }

  private syncControls(): void {
    this.searchBox.value = this.filters.text;
    this.kindBox.value = this.filters.kinds[0] ?? '';
    this.trendBox.value = this.filters.trends[0] ?? '';
    this.sectorBox.value = this.filters.sector;
    this.industryBox.value = this.filters.industry;
    this.indexBox.value = this.filters.index;
    this.cleanBox.checked = this.filters.cleanOnly;
    this.colsMenu.sync();
    for (const [column, [lo, hi]] of this.inputs) {
      const [a, b] = this.filters.ranges[column] ?? [null, null];
      const factor = RANGE_FILTERS[column][2];
      lo.value = a === null ? '' : String(+(a / factor).toPrecision(8));
      hi.value = b === null ? '' : String(+(b / factor).toPrecision(8));
    }
  }

  private paint(): void {
    this.note.textContent = dataNote(this.rows, this.marketDay, new Date());
    this.syncControls();
    this.paintTable();
  }

  private paintTable(): void {
    this.presetRow.querySelectorAll<HTMLButtonElement>('button[data-preset]').forEach((b) => b.classList.toggle('on', b.dataset.preset === this.presetLabel));
    const preset = PRESETS.find((p) => p.label === this.presetLabel);
    this.presetNote.textContent = preset ? `${preset.note} As a query: ${limitsToQuery(preset.ranges)}` : '';
    const found = this.found();
    this.modeRow.querySelectorAll<HTMLButtonElement>('button[data-mode]').forEach((b) => b.classList.toggle('on', b.dataset.mode === this.mode));
    this.tableHost.hidden = this.mode !== 'table';
    this.tools.hidden = this.mode !== 'table';
    this.heat.element.hidden = this.mode !== 'heatmap';
    this.cal.element.hidden = this.mode !== 'calendar';
    this.count.hidden = this.mode === 'calendar';
    if (this.mode === 'heatmap') this.heat.render(found);
    if (this.mode === 'calendar') void this.cal.show().then(() => this.cal.refresh());
    const shown = found.slice(0, MAX_SHOWN);
    this.count.textContent = `${found.length.toLocaleString('en-IN')} match${found.length === 1 ? '' : 'es'}${this.mode === 'table' && found.length > shown.length ? `; showing the first ${shown.length}, sorted by ${COLUMNS[this.sortBy]?.[0] ?? this.sortBy}` : ''}.`;

    const table = el('table', 'stable');
    const head = el('tr');
    for (const c of this.columns) {
      const th = el('th', c === this.sortBy ? 'sorted' : '', `${COLUMNS[c][0]}${c === this.sortBy ? (this.descending ? ' ▼' : ' ▲') : ''}`);
      th.dataset.column = c;
      if (COLUMNS[c][1] !== 'text') th.classList.add('num');
      th.addEventListener('click', () => {
        this.descending = c === this.sortBy ? !this.descending : COLUMNS[c][1] !== 'text';
        this.sortBy = c;
        this.paintTable();
      });
      head.append(th);
    }
    table.append(head);
    for (const row of shown) {
      const tr = el('tr', `srow${row.symbol === this.selected ? ' selected' : ''}`);
      tr.dataset.symbol = String(row.symbol);
      for (const c of this.columns) {
        const [text, tone] = this.cell(row[c], COLUMNS[c][1], c);
        const td = el('td', `${COLUMNS[c][1] === 'text' ? '' : 'num '}${tone}`, text);
        tr.append(td);
      }
      tr.addEventListener('click', () => this.select(String(row.symbol), row));
      tr.addEventListener('dblclick', () => this.ctx.openResearch(String(row.symbol)));
      table.append(tr);
    }
    this.tableHost.replaceChildren(table);
    if (!shown.length) this.tableHost.append(el('div', 'note', 'Nothing matches. A stock with no figure in a limited column is left out, not counted as zero.'));
  }

  private cell(value: Cell, style: Style, column: string): [string, string] {
    return formatCell(value, style, column);
  }

  private select(symbol: string, row: Row): void {
    this.selected = symbol;
    this.actions.replaceChildren(el('span', 'ssel', `${symbol} · ${row.name ?? ''}`));
    const chart = el('button', 'btn', 'Chart');
    chart.addEventListener('click', () => this.ctx.showSymbol(symbol));
    const research = el('button', 'btn', 'Research');
    research.addEventListener('click', () => this.ctx.openResearch(symbol));
    const fund = el('button', 'btn', 'Fundamentals');
    fund.addEventListener('click', () => this.ctx.openFundamentals(symbol));
    const add = el('button', 'btn', '+ Watchlist');
    add.addEventListener('click', () => {
      this.ctx.watchlist.add(symbol);
      this.ctx.changed();
      add.textContent = 'Added';
    });
    this.actions.append(chart, research, fund, add);
    this.element.querySelectorAll('.srow').forEach((r) => r.classList.toggle('selected', (r as HTMLElement).dataset.symbol === symbol));
  }
}
