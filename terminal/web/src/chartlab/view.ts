import { attachSuggest } from '../suggest';
import type { Bar } from '../bars';
import type { Quote } from '../feed';
import { compact, pct, price, signed } from '../format';
import { marketState } from '../market';
import { el, type AppContext } from '../panels/base';
import { defaultRangeOf, parseInterval, rangesOf } from '../interval';
import { barLimit, isIntraday, type Interval, type Range, type Timeframe } from '../ranges';
import { settingsDialog } from './dialog';
import { CHART_TYPES, ChartModel, isDerived, type ChartType, type LabState, type ScaleMode } from './model';
import { rowMid, type Profile } from './profile';
import { caption, isCustom, REGISTRY } from './registry';
import { formulaDialog } from './formuladialog';
import { switchOnDialog } from './pydialog';
import { PyIndicators, serverPyIO, type PyIO } from '../formula/pyind';
import { formulaId } from '../formula/defs';
import { FormulaStore, serverFormulaIO, type FormulaIO } from '../formula/store';
import { build, sourceOf } from './resample';
import { lightweightRenderer, type ChartRenderer, type RendererFactory } from './render';
import { deserialize, History, serialize, StateError, Templates, type KeyValue } from './store';
import { DrawingController } from './drawings/controller';
import { buildObjects } from './drawings/objects';
import { buildProps } from './drawings/props';
import { buildRail, type Rail } from './drawings/rail';
import { ActionLog, DrawingStore, serverDrawingIO, type DrawingIO } from './drawings/store';

const LINK_GROUP = 'blue' as const;
const STATE_KEY = 'athena.chartlab.state';
const RECENT_KEY = 'athena.chartlab.intervals';
const RECENT_MAX = 3;
const INTERVALS: Timeframe[] = ['1m', '2m', '3m', '5m', '10m', '15m', '30m', '1h', '1D', '1W', '1M'];
const SCALES: { id: ScaleMode; label: string; title: string }[] = [
  { id: 'auto', label: 'Auto', title: 'Linear price scale' },
  { id: 'log', label: 'Log', title: 'Logarithmic price scale' },
  { id: 'percent', label: '%', title: 'Percent change from the first visible bar' },
];

/** The ranges an interval offers (a range implies its interval). `bars` null shows every bar; 'ytd' is worked out from today's date. The counts
 *  follow the trading day (375 minutes) and what the free source holds: 5 days of 1-minute bars, a month of 5, 15 and 30-minute bars, a year of hourly. */
const r = (label: string, tf: Timeframe, bars: number | null | 'ytd'): Range => ({ label, tf, bars });
const RANGES: Record<Timeframe, Range[]> = {
  '1m': [r('1D', '1m', 375), r('5D', '1m', 1875)],
  '2m': [r('1D', '2m', 188), r('5D', '2m', 938)],
  '3m': [r('1D', '3m', 125), r('5D', '3m', 625)],
  '5m': [r('1D', '5m', 75), r('5D', '5m', 375), r('1M', '5m', 1650)],
  '10m': [r('1D', '10m', 38), r('5D', '10m', 188), r('1M', '10m', 825)],
  '15m': [r('5D', '15m', 125), r('1M', '15m', 550)],
  '30m': [r('5D', '30m', 65), r('1M', '30m', 275)],
  '1h': [r('5D', '1h', 35), r('1M', '1h', 154), r('6M', '1h', 880), r('1Y', '1h', null)],
  '1D': [r('1M', '1D', 22), r('3M', '1D', 63), r('6M', '1D', 126), r('YTD', '1D', 'ytd'), r('1Y', '1D', 252), r('5Y', '1D', 1260), r('8Y', '1D', 2016), r('All', '1D', null)],
  '1W': [r('1Y', '1W', 52), r('5Y', '1W', 260), r('All', '1W', null)],
  '1M': [r('5Y', '1M', 60), r('All', '1M', null)],
};
const DEFAULT_RANGE: Record<Timeframe, string> = { '1m': '1D', '2m': '1D', '3m': '1D', '5m': '5D', '10m': '5D', '15m': '5D', '30m': '1M', '1h': '1M', '1D': '1Y', '1W': '5Y', '1M': 'All' };

export interface Fetched {
  bars: Bar[];
  note: string;
}
export type BarsFetcher = (symbol: string, tf: Timeframe, limit: number | null, adjusted: boolean) => Promise<Fetched>;

/** The server's bars; an intraday request answers "loading" the first time and is asked again a few times. */
export const fetchBars: BarsFetcher = async (symbol, tf, limit, adjusted) => {
  const url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf=${tf}${tf === '1D' ? `&limit=${limit ?? 5000}${adjusted ? '' : '&adj=0'}` : ''}`;
  for (let attempt = 0; ; attempt++) {
    const body = (await (await fetch(url)).json()) as { bars: Bar[]; note?: string };
    if (body.note === 'loading' && attempt < 6) {
      await new Promise((r) => setTimeout(r, 1500));
      continue;
    }
    return { bars: body.bars, note: body.note === 'loading' ? '' : (body.note ?? '') };
  }
};

export interface LabOptions {
  renderer?: RendererFactory;
  fetchBars?: BarsFetcher;
  storage?: KeyValue | null;
  drawingIO?: DrawingIO;
  formulaIO?: FormulaIO;
  pyIO?: PyIO;
}

function browserStorage(): KeyValue | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function fmt(v: number | null | undefined): string {
  return v === null || v === undefined ? '–' : Math.abs(v) >= 1e6 ? compact(v) : price(v);
}

/** The Chart Lab page: one full-size chart with the toolbar, indicator legends, a data window and the controls the reference chart page has
 *  (interval, type, indicators with settings, compare, log and percent scales, adjusted or raw prices, undo, templates, snapshot). */
export class ChartLabView {
  readonly element = el('div', 'lab');
  readonly model = new ChartModel();
  private history = new History<LabState>(this.model.state());
  private templates: Templates;
  private formulas: FormulaStore;
  private py: PyIndicators;
  private renderer: ChartRenderer | null = null;
  private fetcher: BarsFetcher;
  private makeRenderer: RendererFactory;
  private storage: KeyValue | null;
  private toolbar = el('div', 'lab-bar');
  private wrap = el('div', 'lab-chart');
  private host = el('div', 'lab-host');
  private legends = el('div', 'lab-legends');
  private windowBox = el('div', 'lab-window');
  private main = el('div', 'lab-main');
  private side = el('div', 'lab-side');
  private tabs = el('div', 'lab-tabs');
  private objectsBox = el('div', 'lab-objects-box');
  private log = new ActionLog();
  private drawingStore: DrawingStore;
  private drawings: DrawingController | null = null;
  private rail: Rail | null = null;
  private onKey = (e: KeyboardEvent) => this.hotkey(e);
  private note = el('div', 'note lab-note');
  private badge = el('span', 'lab-badge');
  private headline = el('span', 'lab-head');
  private symbolInput = el('input', 'cmd lab-symbol');
  private compareInput = el('input', 'cmd lab-compare');
  private typeSelect = el('select', 'lab-select lab-type');
  private profileButton = el('button', 'rbtn', 'VP');
  private profileRows = el('select', 'lab-select lab-rows');
  private profile: Profile | null = null;
  private rangeRow = el('span', 'ranges');
  private intervalRow = el('span', 'ranges');
  private recentRow = el('span', 'ranges');
  private intervalInput = el('input', 'cmd lab-interval');
  private recent: string[] = [];
  private scaleRow = el('span', 'ranges');
  private adjButton = el('button', 'rbtn', 'ADJ');
  private undoButton = el('button', 'btn', 'Undo');
  private redoButton = el('button', 'btn', 'Redo');
  private templateSelect = el('select', 'lab-select lab-templates');
  private popover = el('div', 'lab-pop');
  private legendValues = new Map<string, HTMLElement>();
  private symbol = '';
  private tf: Interval = '1D';
  private rangeLabel = '1Y';
  private token = 0;
  private visible = false;
  private hover: number | null = null;
  private offQuotes: (() => void) | null = null;
  private offLink: (() => void) | null = null;
  private resize: ResizeObserver | null = null;
  private themeWatch: MutationObserver | null = null;

  constructor(private ctx: AppContext, options: LabOptions = {}) {
    this.element.hidden = true;
    this.storage = options.storage === undefined ? browserStorage() : options.storage;
    this.templates = new Templates(this.storage);
    this.drawingStore = new DrawingStore(options.drawingIO ?? serverDrawingIO(), this.storage);
    this.py = new PyIndicators(options.pyIO ?? serverPyIO(), this.storage); // registered from the browser copy at once, as the formulas are
    this.formulas = new FormulaStore(options.formulaIO ?? serverFormulaIO(), this.storage); // registered from the browser copy at once, so a saved chart can use them
    this.fetcher = options.fetchBars ?? fetchBars;
    this.makeRenderer = options.renderer ?? lightweightRenderer;
    this.buildToolbar();
    this.popover.hidden = true;
    this.wrap.append(this.host, this.legends);
    const sub = el('div', 'lab-sub');
    sub.append(this.rangeRow);
    this.tabs.hidden = true;
    this.objectsBox.hidden = true;
    this.side.append(this.tabs, this.windowBox, this.objectsBox);
    this.main.append(this.wrap, this.side);
    this.element.append(this.toolbar, sub, this.popover, this.main, this.note);
    document.addEventListener('keydown', this.onKey);
    this.model.subscribe((kind) => this.modelChanged(kind));
    this.offQuotes = ctx.feed.onQuotes((qs) => this.onQuotes(qs));
    this.offLink = ctx.bus.subscribe(LINK_GROUP, (s) => this.follow(s));
    this.loadRecent();
    this.restoreSaved();
    this.formulas.subscribe(() => this.formulasChanged());
    this.py.subscribe(() => this.formulasChanged());
    this.py.onError((file, message) => this.say(`${file}: ${message}`));
    void this.formulas.load();
    void this.py.load();
  }

  /** The formulas changed (loaded, saved, deleted): charts that use one that is gone lose it, and every formula line is computed again. */
  private formulasChanged(): void {
    if (this.model.refreshCustom()) {
      this.history.push(this.model.state());
      this.persist();
      this.say('A formula indicator was taken off the chart because its formula was deleted.');
    }
  }

  /** Open the formula editor; a new formula is put on the chart when it is saved. */
  private editFormula(name?: string): void {
    const existing = name ? this.formulas.get(name) ?? null : null;
    formulaDialog(this.element, existing, async (formula) => {
      try {
        await this.formulas.save(formula);
      } catch (e) {
        return e instanceof Error ? e.message : 'the formula could not be saved';
      }
      if (!existing) this.commit(() => this.model.addIndicator(formulaId(formula.name)));
      return null;
    });
  }

  // ---- showing and loading -----------------------------------------------------------------------------------------------------------------

  show(symbol?: string): void {
    this.visible = true;
    this.element.hidden = false;
    if (!this.renderer) {
      this.renderer = this.makeRenderer(this.host);
      this.renderer.onProfile?.((profile) => {
        this.profile = profile;
        this.paintValues();
      });
      this.renderer.onCrosshair((i) => {
        this.hover = i;
        this.paintValues();
      });
      if (typeof ResizeObserver !== 'undefined') {
        this.resize = new ResizeObserver(() => this.placeLegends());
        this.resize.observe(this.host);
      }
      this.themeWatch = new MutationObserver(() => {
        this.renderer?.applyTheme();
        this.paintChart();
      });
      this.themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      this.mountDrawings();
    }
    const next = symbol || this.ctx.bus.symbolOf(LINK_GROUP) || this.symbol || this.ctx.watchlist.items[0] || '';
    if (next) this.follow(next, true);
  }

  hide(): void {
    this.visible = false;
    this.element.hidden = true;
    this.ctx.feed.release('chartlab');
  }

  private follow(symbol: string, force = false): void {
    if (!this.visible || !symbol || (symbol === this.symbol && !force)) return;
    this.symbol = symbol;
    void this.drawings?.setSymbol(symbol);
    this.symbolInput.value = symbol;
    this.ctx.feed.need('chartlab', [symbol]);
    void this.reload();
  }

  private pick(symbol: string): void {
    const s = symbol.trim().toUpperCase();
    if (!s) return;
    this.follow(s);
    this.ctx.bus.setSymbol(LINK_GROUP, s);
  }

  /** An interval typed in the box: refused with the reason, or chosen (a built-in one selects its button; another is kept among the last few). */
  private chooseInterval(text: string): void {
    const p = parseInterval(text);
    if (!p.ok) return this.say(`"${text.trim()}": ${p.error}`);
    if (!p.builtin) {
      this.recent = [p.id, ...this.recent.filter((id) => id !== p.id)].slice(0, RECENT_MAX);
      try {
        this.storage?.setItem(RECENT_KEY, JSON.stringify(this.recent));
      } catch {
        /* the buttons are a convenience */
      }
    }
    this.intervalInput.value = '';
    this.tf = p.id;
    this.rangeLabel = this.defaultRangeFor(p.id);
    void this.reload();
    this.paintBar();
  }

  private loadRecent(): void {
    try {
      const list = JSON.parse(this.storage?.getItem(RECENT_KEY) ?? '[]') as unknown;
      if (Array.isArray(list)) this.recent = list.filter((id): id is string => typeof id === 'string').filter((id) => { const p = parseInterval(id); return p.ok && !p.builtin && p.id === id; }).slice(0, RECENT_MAX);
    } catch {
      /* none kept, or not readable */
    }
  }

  private paintRecent(): void {
    const shown = this.tf in RANGES || this.recent.includes(this.tf) ? this.recent : [this.tf, ...this.recent];
    this.recentRow.replaceChildren(
      ...shown.map((id) => {
        const b = el('button', `rbtn${id === this.tf ? ' on' : ''}`, id);
        b.dataset.interval = id;
        b.title = `${id} bars`;
        b.addEventListener('click', () => this.chooseInterval(id));
        return b;
      }),
    );
  }

  private range(): Range {
    const list = this.rangesFor(this.tf);
    return list.find((r) => r.label === this.rangeLabel) ?? list[0];
  }

  /** The ranges of an interval: the table for a built-in one, worked out from the text for one the person typed. */
  private rangesFor(tf: Interval): Range[] {
    if (tf in RANGES) return RANGES[tf as Timeframe];
    const p = parseInterval(tf);
    return p.ok ? rangesOf(p) : RANGES['1D'];
  }

  private defaultRangeFor(tf: Interval): string {
    if (tf in DEFAULT_RANGE) return DEFAULT_RANGE[tf as Timeframe];
    const p = parseInterval(tf);
    return p.ok ? defaultRangeOf(p) : '1Y';
  }

  private async reload(): Promise<void> {
    if (!this.symbol) return;
    const mine = ++this.token;
    const source = sourceOf(this.tf);
    // every bar held for the interval is loaded, whatever the range: the range only decides how much is in view, and the rest can be scrolled back to
    const limit = null;
    this.say('loading…');
    let fetched: Fetched;
    try {
      fetched = await this.fetcher(this.symbol, source.fetch, limit, this.model.adjusted);
    } catch {
      if (mine === this.token) this.say('could not reach the server');
      return;
    }
    if (mine !== this.token) return;
    const bars = build(this.tf, fetched.bars);
    this.hover = null;
    this.model.setBars(bars, this.tf);
    this.applyRange();
    const custom = !(this.tf in RANGES);
    this.say(bars.length ? fetched.note || (custom ? `${this.tf} bars are built from ${source.fetch} bars` : '') : `no ${this.tf} bars for ${this.symbol}`);
    if (this.model.compare) void this.loadCompare(this.model.compare.symbol);
    this.paintBar();
  }

  /** How many bars the chosen range puts in view: null means all. */
  private rangeBars(): number | null {
    return barLimit(this.range(), new Date());
  }

  /** Put the range in view (the last N bars) without touching what is loaded. */
  private applyRange(): void {
    this.renderer?.showLast(this.rangeBars());
  }

  private async loadCompare(symbol: string): Promise<void> {
    const mine = this.token;
    try {
      const got = await this.fetcher(symbol, sourceOf(this.tf).fetch, null, true);
      if (mine === this.token && this.model.compare?.symbol === symbol) this.model.setCompare(symbol, build(this.tf, got.bars));
    } catch {
      this.say(`could not load ${symbol} to compare`);
    }
  }

  private say(text: string): void {
    this.note.textContent = text;
  }

  // ---- changes, undo and persistence -------------------------------------------------------------------------------------------------------

  /** A user change: apply it, remember the new state for undo and keep it for the next visit. */
  private commit(change: () => void): void {
    change();
    this.history.push(this.model.state());
    this.log.push('chart');
    this.persist();
    this.paintBar();
  }

  private persist(): void {
    try {
      this.storage?.setItem(STATE_KEY, serialize(this.model.state()));
    } catch {
      /* the chart works without it */
    }
  }

  private restoreSaved(): void {
    try {
      const text = this.storage?.getItem(STATE_KEY);
      if (text) this.apply(deserialize(text), false);
    } catch {
      /* nothing saved, or not usable: start plain */
    }
    this.history.reset(this.model.state());
  }

  /** Put a state on the chart (undo, redo, template, saved). Prices are fetched again when adjusted/raw changed or a compare symbol is new. */
  private apply(state: LabState, load = true): void {
    const wasAdjusted = this.model.adjusted;
    const wasCompare = this.model.compare?.symbol ?? null;
    this.model.restore(state);
    if (load && this.visible && wasAdjusted !== state.adjusted) void this.reload();
    else if (load && state.compare && state.compare !== wasCompare) void this.loadCompare(state.compare);
    this.paintBar();
  }

  /** Undo the last thing done, whether it was to the chart or to the drawings. */
  private undo(): void {
    const kind = this.log.undo();
    if (kind === 'drawings') {
      this.drawings?.undoStep();
    } else if (kind === 'chart') {
      const s = this.history.undo();
      if (s) {
        this.apply(s);
        this.persist();
      }
    }
    this.paintBar();
  }

  private redo(): void {
    const kind = this.log.redo();
    if (kind === 'drawings') {
      this.drawings?.redoStep();
    } else if (kind === 'chart') {
      const s = this.history.redo();
      if (s) {
        this.apply(s);
        this.persist();
      }
    }
    this.paintBar();
  }

  private hotkey(e: KeyboardEvent): void {
    if (!this.visible || !(e.ctrlKey || e.metaKey)) return;
    const t = e.target as HTMLElement | null;
    if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return;
    const k = e.key.toLowerCase();
    if (k === 'z' && !e.shiftKey) {
      e.preventDefault();
      this.undo();
    } else if (k === 'y' || (k === 'z' && e.shiftKey)) {
      e.preventDefault();
      this.redo();
    }
  }

  // ---- drawings ------------------------------------------------------------------------------------------------------------------------------

  /** The drawing tools, once the chart exists: the rail on the left, the properties bar over the chart and the Objects tab on the right. */
  private mountDrawings(): void {
    const host = this.renderer?.drawings;
    if (!host || this.drawings) return;
    const c = new DrawingController({ element: this.host, host, store: this.drawingStore, log: this.log, bars: () => this.model.displayBars(), enabled: () => this.visible && !isDerived(this.model.type) });
    this.drawings = c;
    this.rail = buildRail(c);
    this.main.prepend(this.rail.element);
    this.main.classList.add('has-rail');
    this.wrap.append(buildProps(c));
    this.objectsBox.append(buildObjects(c));
    const dataTab = this.button('Data', 'Values under the crosshair', () => this.showTab('data'), 'lab-tab on');
    const objectsTab = this.button('Objects', 'Drawings on this symbol', () => this.showTab('objects'), 'lab-tab');
    dataTab.dataset.tab = 'data';
    objectsTab.dataset.tab = 'objects';
    this.tabs.append(dataTab, objectsTab);
    this.tabs.hidden = false;
    c.subscribe(() => {
      objectsTab.textContent = c.list.length ? `Objects (${c.list.length})` : 'Objects';
      this.paintBar();
    });
    this.rail.setEnabled(!isDerived(this.model.type), 'Drawings are not available on Renko, line-break, range, Kagi or point-and-figure charts');
  }

  private showTab(tab: 'data' | 'objects'): void {
    this.windowBox.hidden = tab !== 'data';
    this.objectsBox.hidden = tab !== 'objects';
    this.tabs.querySelectorAll<HTMLElement>('.lab-tab').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  }

  // ---- toolbar -------------------------------------------------------------------------------------------------------------------------------

  private button(text: string, title: string, click: () => void, cls = 'btn'): HTMLButtonElement {
    const b = el('button', cls, text);
    b.title = title;
    b.addEventListener('click', click);
    return b;
  }

  private buildToolbar(): void {
    this.symbolInput.placeholder = 'symbol';
    this.symbolInput.setAttribute('aria-label', 'chart symbol');
    this.symbolInput.addEventListener('keydown', (e) => e.key === 'Enter' && this.pick(this.symbolInput.value));
    attachSuggest(this.symbolInput, {
      enterTakesBest: true,
      onPick: (hit) => {
        this.symbolInput.value = hit.s;
        this.pick(hit.s);
      },
    });
    for (const tf of INTERVALS) {
      const b = el('button', 'rbtn', tf);
      b.dataset.interval = tf;
      b.title = `${tf} bars`;
      b.addEventListener('click', () => {
        this.tf = tf;
        this.rangeLabel = this.defaultRangeFor(tf);
        void this.reload();
        this.paintBar();
      });
      this.intervalRow.append(b);
    }
    this.intervalInput.placeholder = 'e.g. 7m, 2h, 3D';
    this.intervalInput.title = 'Type any interval: 7m, 90m, 2h, 3D, 2W, 3M. Press Enter.';
    this.intervalInput.setAttribute('aria-label', 'custom interval');
    this.intervalInput.addEventListener('keydown', (e) => e.key === 'Enter' && this.chooseInterval(this.intervalInput.value));
    this.profileButton.type = 'button';
    this.profileButton.title = 'Volume profile: the volume traded at each price, for the bars in view';
    this.profileButton.setAttribute('aria-label', 'volume profile');
    this.profileButton.addEventListener('click', () => this.commit(() => this.model.setProfile({ ...this.model.profile, on: !this.model.profile.on })));
    this.profileRows.setAttribute('aria-label', 'profile rows');
    this.profileRows.title = 'Price rows in the volume profile';
    for (const n of [12, 24, 48, 96]) this.profileRows.append(new Option(`${n} rows`, String(n)));
    this.profileRows.addEventListener('change', () => this.commit(() => this.model.setProfile({ ...this.model.profile, rows: Number(this.profileRows.value) })));
    this.typeSelect.setAttribute('aria-label', 'chart type');
    for (const t of CHART_TYPES) this.typeSelect.append(new Option(t.label, t.id));
    this.typeSelect.addEventListener('change', () => this.commit(() => this.model.setType(this.typeSelect.value as ChartType)));
    const indicators = this.button('Indicators', 'Add an indicator to the chart', () => this.togglePopover());
    indicators.classList.add('lab-ind');
    this.compareInput.placeholder = 'compare symbol';
    this.compareInput.setAttribute('aria-label', 'compare symbol');
    const compare = (): void => {
      const s = this.compareInput.value.trim().toUpperCase();
      if (!s) return this.commit(() => this.model.setCompare(null));
      this.commit(() => this.model.setCompare(s, []));
      void this.loadCompare(s);
    };
    this.compareInput.addEventListener('keydown', (e) => e.key === 'Enter' && compare());
    attachSuggest(this.compareInput, {
      enterTakesBest: true,
      onPick: (hit) => {
        this.compareInput.value = hit.s;
        compare();
      },
    });
    for (const s of SCALES) {
      const b = el('button', 'rbtn', s.label);
      b.dataset.scale = s.id;
      b.title = s.title;
      b.addEventListener('click', () => this.commit(() => this.model.setScale(s.id)));
      this.scaleRow.append(b);
    }
    this.adjButton.dataset.action = 'adjust';
    this.adjButton.title = 'Prices adjusted for splits and bonuses (on) or as printed (off)';
    this.adjButton.addEventListener('click', () => {
      this.commit(() => this.model.setAdjusted(!this.model.adjusted));
      void this.reload();
    });
    this.undoButton.addEventListener('click', () => this.undo());
    this.redoButton.addEventListener('click', () => this.redo());
    this.undoButton.title = 'Undo the last change to the chart or its drawings (Ctrl+Z)';
    this.redoButton.title = 'Redo';
    this.templateSelect.setAttribute('aria-label', 'templates');
    this.templateSelect.addEventListener('change', () => {
      const name = this.templateSelect.value;
      if (name === ':save') this.saveTemplate();
      else if (name.startsWith('load:')) this.loadTemplate(name.slice(5));
      else if (name.startsWith('del:')) {
        this.templates.remove(name.slice(4));
        this.say(`removed template "${name.slice(4)}"`);
      }
      this.paintTemplates();
    });
    const snapshot = this.button('Snapshot', 'Download the chart as a PNG picture', () => this.snapshot());
    this.badge.title = 'Market status';
    this.toolbar.append(
      this.symbolInput, this.intervalRow, this.intervalInput, this.recentRow, this.typeSelect, this.profileButton, this.profileRows, indicators, this.compareInput, this.scaleRow, this.adjButton, this.undoButton, this.redoButton,
      this.templateSelect, snapshot, this.badge,
    );
    this.paintTemplates();
  }

  private paintTemplates(): void {
    this.templateSelect.replaceChildren(new Option('Templates', ''), new Option('Save current as…', ':save'));
    for (const name of this.templates.list()) this.templateSelect.append(new Option(`Load: ${name}`, `load:${name}`));
    for (const name of this.templates.list()) this.templateSelect.append(new Option(`Delete: ${name}`, `del:${name}`));
    this.templateSelect.value = '';
  }

  private saveTemplate(): void {
    const name = prompt('Template name (letters, digits, spaces, - or _)')?.trim();
    if (!name) return;
    try {
      this.templates.save(name, this.model.state());
      this.say(`saved template "${name}"`);
    } catch (e) {
      this.say(e instanceof StateError ? e.message : 'the template could not be saved');
    }
  }

  private loadTemplate(name: string): void {
    try {
      const state = this.templates.load(name);
      this.commit(() => this.apply(state));
      this.say(`loaded template "${name}"`);
    } catch (e) {
      this.say(e instanceof StateError ? e.message : 'the template could not be loaded');
    }
  }

  private snapshot(): void {
    const canvas = this.renderer?.snapshot();
    if (!canvas) return this.say('the picture could not be made');
    canvas.toBlob((blob) => {
      if (!blob) return this.say('the picture could not be made');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${this.symbol}-${this.tf}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    });
  }

  private togglePopover(): void {
    if (!this.popover.hidden) {
      this.popover.hidden = true;
      return;
    }
    const search = el('input', 'cmd');
    search.placeholder = 'search indicators';
    search.setAttribute('aria-label', 'search indicators');
    const list = el('div', 'lab-list');
    const fill = () => {
      const q = search.value.trim().toLowerCase();
      list.replaceChildren();
      for (const def of REGISTRY.filter((d) => !q || d.name.toLowerCase().includes(q) || d.id.includes(q))) {
        const row = el('button', 'lab-item', def.name);
        row.dataset.indicator = def.id;
        row.append(el('span', 'note', def.pane === 'overlay' ? 'on price' : 'own pane'));
        row.addEventListener('click', () => {
          this.commit(() => this.model.addIndicator(def.id));
          this.popover.hidden = true;
        });
        list.append(row);
      }
      this.formulaSection(list, q);
      this.pythonSection(list, q);
    };
    search.addEventListener('input', fill);
    search.addEventListener('keydown', (e) => e.key === 'Escape' && (this.popover.hidden = true));
    fill();
    this.popover.replaceChildren(search, list);
    this.popover.hidden = false;
    search.focus();
  }

  /** The user's formulas at the end of the Indicators list: each can be added, edited or deleted (a second click confirms), and a new one written. */
  private formulaSection(list: HTMLElement, q: string): void {
    list.append(el('div', 'lab-sep', 'My formulas'));
    for (const def of this.formulas.defs().filter((d) => !q || d.name.toLowerCase().includes(q))) {
      const row = el('div', 'lab-item-row');
      const add = el('button', 'lab-item', def.name);
      add.dataset.indicator = def.id;
      add.append(el('span', 'note', def.pane === 'overlay' ? 'on price' : 'own pane'));
      add.addEventListener('click', () => {
        this.commit(() => this.model.addIndicator(def.id));
        this.popover.hidden = true;
      });
      const edit = el('button', 'btn lab-mini', 'Edit');
      edit.type = 'button';
      edit.setAttribute('aria-label', `edit ${def.name}`);
      edit.addEventListener('click', () => {
        this.popover.hidden = true;
        this.editFormula(def.name);
      });
      const del = el('button', 'btn lab-mini', 'Delete');
      del.type = 'button';
      del.setAttribute('aria-label', `delete ${def.name}`);
      let armed = false;
      del.addEventListener('click', () => {
        if (!armed) {
          armed = true;
          del.textContent = 'Sure?';
          setTimeout(() => {
            armed = false;
            del.textContent = 'Delete';
          }, 3000);
          return;
        }
        void this.formulas.remove(def.name).catch(() => this.say('The formula could not be deleted on the server.'));
        this.popover.hidden = true;
      });
      row.append(add, edit, del);
      list.append(row);
    }
    for (const [name, error] of Object.entries(this.formulas.errors)) {
      const row = el('div', 'lab-item-row');
      row.append(el('span', 'note bad', `${name}: ${error}`));
      const edit = el('button', 'btn lab-mini', 'Edit');
      edit.type = 'button';
      edit.addEventListener('click', () => {
        this.popover.hidden = true;
        this.editFormula(name);
      });
      row.append(edit);
      list.append(row);
    }
    const create = el('button', 'lab-item', 'New formula…');
    create.dataset.action = 'new-formula';
    create.addEventListener('click', () => {
      this.popover.hidden = true;
      this.editFormula();
    });
    list.append(create);
  }

  /** The Python files in the indicators folder: add one that is switched on, switch one on (after a plain warning) or off. */
  private pythonSection(list: HTMLElement, q: string): void {
    list.append(el('div', 'lab-sep', 'My Python indicators'));
    const files = this.py.files().filter((f) => !q || f.name.toLowerCase().includes(q) || f.file.toLowerCase().includes(q));
    for (const f of files) {
      const row = el('div', 'lab-item-row');
      row.dataset.file = f.file;
      if (f.error) {
        row.append(el('span', 'note bad', `${f.file}: ${f.error}`));
      } else if (f.enabled) {
        const add = el('button', 'lab-item', f.name);
        add.dataset.indicator = `py:${f.file}`;
        add.append(el('span', 'note', f.pane === 'overlay' ? 'on price' : 'own pane'));
        add.addEventListener('click', () => {
          this.commit(() => this.model.addIndicator(`py:${f.file}`));
          this.popover.hidden = true;
        });
        const off = el('button', 'btn lab-mini', 'Switch off');
        off.type = 'button';
        off.addEventListener('click', () => {
          this.popover.hidden = true;
          void this.py.disable(f.file).catch(() => this.say('The file could not be switched off on the server.'));
        });
        row.append(add, off);
      } else {
        row.append(el('span', 'lab-item off', `${f.name}`), el('span', 'note', f.changed ? 'changed since it was switched on: off' : 'off'));
        const on = el('button', 'btn lab-mini', 'Switch on…');
        on.type = 'button';
        on.addEventListener('click', () => {
          this.popover.hidden = true;
          switchOnDialog(this.element, f, () => this.py.enable(f.file));
        });
        row.append(on);
      }
      list.append(row);
    }
    if (!files.length) list.append(el('div', 'note', q ? 'No Python file matches.' : `No Python indicators yet. Put a .py file with a compute(bars) function in ${this.py.dir || '.athena/indicators'}.`));
    if (this.py.problem) list.append(el('div', 'note bad', this.py.problem));
    const help = el('details', 'lab-formula-help');
    help.append(el('summary', '', 'How to write one'));
    help.append(
      el('pre', 'lab-pre', 'NAME = "Two-bar average"   # optional\nPANE = "overlay"            # or "own" (default)\n\ndef compute(bars):\n    # bars: a list of {t, o, h, l, c, v}, oldest first\n    out = []\n    for i in range(len(bars)):\n        out.append(None if i < 1 else (bars[i]["c"] + bars[i - 1]["c"]) / 2)\n    return {"avg": out}    # one list per line, one value (or None) per bar'),
      el('div', 'note', 'Up to 8 lines. The file is read, not run, to be listed; it runs only after you switch it on, in a separate process, for at most 20 seconds. It is not sandboxed.'),
    );
    list.append(help);
  }

  // ---- painting ------------------------------------------------------------------------------------------------------------------------------

  private modelChanged(kind: 'tick' | 'structure'): void {
    if (kind === 'tick') {
      this.renderer?.tick(this.model);
      this.paintValues();
      return;
    }
    this.paintChart();
  }

  private paintChart(): void {
    this.renderer?.paint(this.model);
    this.rail?.setEnabled(!isDerived(this.model.type), 'Drawings are not available on Renko, line-break, range, Kagi or point-and-figure charts');
    if (isDerived(this.model.type) && (this.model.indicators.length || this.model.compare)) this.say('Indicators, compare and volume are not drawn on Renko, line-break, range, Kagi or point-and-figure charts; they come back with another chart type.');
    this.paintLegends();
    this.paintValues();
    this.paintBar();
  }

  private paintBar(): void {
    this.symbolInput.value = this.symbol;
    this.intervalRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.interval === this.tf));
    this.paintRecent();
    this.scaleRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.scale === this.model.scale));
    this.adjButton.classList.toggle('on', this.model.adjusted);
    this.typeSelect.value = this.model.type;
    this.profileButton.classList.toggle('on', this.model.profile.on);
    this.profileRows.hidden = !this.model.profile.on;
    if (![...this.profileRows.options].some((o) => o.value === String(this.model.profile.rows))) this.profileRows.append(new Option(`${this.model.profile.rows} rows`, String(this.model.profile.rows)));
    this.profileRows.value = String(this.model.profile.rows);
    this.compareInput.value = this.model.compare?.symbol ?? '';
    this.undoButton.disabled = !this.log.canUndo;
    this.redoButton.disabled = !this.log.canRedo;
    this.rangeRow.replaceChildren();
    for (const r of this.rangesFor(this.tf)) {
      const b = el('button', `rbtn${r.label === this.range().label ? ' on' : ''}`, r.label);
      b.dataset.range = r.label;
      b.addEventListener('click', () => {
        this.rangeLabel = r.label;
        this.applyRange(); // the bars are already loaded; only the window moves
        this.paintBar();
      });
      this.rangeRow.append(b);
    }
    const open = marketState(new Date()).open;
    this.badge.textContent = open ? '● market open' : '● market closed';
    this.badge.className = `lab-badge ${open ? 'good' : 'warn'}`;
    if (!this.model.adjusted) this.note.title = 'Prices are as printed, not adjusted for splits and bonuses';
  }

  /** One legend block per pane, above the chart: price pane first (symbol, OHLC and the overlays), then one per own-pane indicator. */
  private paintLegends(): void {
    this.legends.replaceChildren();
    this.legendValues.clear();
    const head = el('div', 'lab-legend');
    head.dataset.pane = '0';
    head.append(this.headline);
    this.legends.append(head);
    const blocks = new Map<number, HTMLElement>([[0, head]]);
    for (const inst of this.model.indicators) {
      const def = this.model.def(inst);
      const pane = inst.hidden ? 0 : (this.renderer?.paneOf(inst.uid) ?? 0);
      let block = blocks.get(pane);
      if (!block) {
        block = el('div', 'lab-legend');
        block.dataset.pane = String(pane);
        blocks.set(pane, block);
        this.legends.append(block);
      }
      const row = el('div', `lab-lrow${inst.hidden ? ' off' : ''}`);
      row.dataset.uid = inst.uid;
      const eye = this.button(inst.hidden ? '○' : '◉', inst.hidden ? 'Show' : 'Hide', () => this.commit(() => this.model.hideIndicator(inst.uid, !inst.hidden)), 'lg');
      eye.dataset.action = 'hide';
      const name = el('span', 'lg-name', caption(def, inst.params));
      const values = el('span', 'lg-vals');
      this.legendValues.set(inst.uid, values);
      const gear = this.button('⚙', `${def.name} settings`, () => this.openSettings(inst.uid), 'lg');
      gear.dataset.action = 'settings';
      const remove = this.button('×', 'Remove from the chart', () => this.commit(() => this.model.removeIndicator(inst.uid)), 'lg');
      remove.dataset.action = 'remove';
      row.append(eye, name, values, gear, remove);
      block.append(row);
    }
    requestAnimationFrame(() => this.placeLegends());
  }

  /** Put each pane's legend at the top of its pane, using where the engine drew the panes. */
  private placeLegends(): void {
    const base = this.wrap.getBoundingClientRect().top;
    this.legends.querySelectorAll<HTMLElement>('.lab-legend').forEach((block) => {
      const pane = this.renderer?.paneElement(Number(block.dataset.pane));
      block.style.top = `${pane ? Math.max(0, pane.getBoundingClientRect().top - base) + 2 : 2}px`;
    });
  }

  private openSettings(uid: string): void {
    const inst = this.model.indicators.find((i) => i.uid === uid);
    if (!inst) return;
    if (inst.id.startsWith('py:')) return this.say(`${inst.id.slice(3)} is a Python file in ${this.py.dir || 'the indicators folder'}: edit it there (it is switched off when it changes).`);
    if (isCustom(inst.id)) return this.editFormula(inst.id.slice(2)); // a formula's settings are its text
    settingsDialog(this.element, this.model.def(inst), inst, (params, colors) => {
      let error: string | null = null;
      this.commit(() => (error = this.model.updateIndicator(uid, params, colors)));
      return error;
    });
  }

  /** The volume profile's numbers in the data window, or why there is none. */
  private profileLines(): void {
    if (!this.model.profile.on) return;
    this.windowBox.append(el('div', 'lab-wh', 'Volume profile'));
    const add = (k: string, v: string): void => {
      const row = el('div', 'lab-wrow');
      row.append(el('span', '', k), el('span', '', v));
      this.windowBox.append(row);
    };
    if (this.model.derived) return void this.windowBox.append(el('div', 'note', 'Not drawn for this chart type: its bars are price moves, not times.'));
    const p = this.profile;
    if (!p) return void this.windowBox.append(el('div', 'note', 'No volume in view (an index has none).'));
    add('Point of control', price(rowMid(p.rows[p.poc])));
    add('Value area high', price(p.rows[p.areaHigh].hi));
    add('Value area low', price(p.rows[p.areaLow].lo));
    add('Volume in view', compact(p.totalVolume));
    this.windowBox.append(el('div', 'note', `${p.rows.length} rows. A bar's volume is spread evenly over its range (its price of trade is not known).`));
  }

  private paintValues(): void {
    const index = this.hover ?? this.model.displayBars().length - 1; // a price-move chart has its own bars, not the loaded ones
    const w = this.model.window(index);
    this.windowBox.replaceChildren();
    if (!w) {
      this.headline.textContent = this.symbol;
      this.windowBox.append(el('div', 'note', 'No bars yet.'));
      return;
    }
    const dir = w.change >= 0 ? 'up' : 'dn';
    this.headline.replaceChildren(
      el('b', '', `${this.symbol} · ${this.tf}`), ...[['O', w.o], ['H', w.h], ['L', w.l], ['C', w.c]].flatMap(([k, v]) => [el('span', 'lk', String(k)), el('span', `lv ${dir}`, price(Number(v)))]),
      el('span', `lv ${dir}`, `${signed(w.change)} (${pct(w.changePct)})`),
    );
    const intraday = isIntraday(this.tf);
    const when = new Date((w.t + (intraday ? 19_800 : 0)) * 1000).toISOString().replace('T', ' ').slice(0, intraday ? 16 : 10);
    this.windowBox.append(el('div', 'lab-wh', 'Data window'), el('div', 'note', when));
    for (const [k, v] of [['Open', price(w.o)], ['High', price(w.h)], ['Low', price(w.l)], ['Close', price(w.c)], ['Change', `${signed(w.change)} (${pct(w.changePct)})`], ['Volume', compact(w.v)]]) {
      const row = el('div', 'lab-wrow');
      row.append(el('span', '', k), el('span', k === 'Change' ? dir : '', v));
      this.windowBox.append(row);
    }
    this.profileLines();
    for (const inst of this.model.indicators) {
      const def = this.model.def(inst);
      const got = w.values[inst.uid];
      const holder = this.legendValues.get(inst.uid);
      holder?.replaceChildren();
      if (!got) continue; // hidden
      const head = el('div', 'lab-wh', caption(def, inst.params));
      this.windowBox.append(head);
      for (const out of def.outputs) {
        const value = got[out.key];
        const row = el('div', 'lab-wrow');
        row.append(el('span', '', out.label), el('span', '', fmt(value)));
        this.windowBox.append(row);
        const chip = el('span', 'lg-val', fmt(value));
        chip.style.color = inst.colors[out.key] ?? `var(${out.color})`;
        holder?.append(chip);
      }
    }
    this.placeLegends();
  }

  private onQuotes(quotes: Quote[]): void {
    if (!this.visible) return;
    const q = quotes.find((x) => x.s === this.symbol);
    if (q) this.model.tick(q.l, q.t ? q.t / 1000 : Date.now() / 1000);
  }

  dispose(): void {
    this.offQuotes?.();
    this.offLink?.();
    this.resize?.disconnect();
    this.themeWatch?.disconnect();
    document.removeEventListener('keydown', this.onKey);
    this.drawings?.dispose();
    this.renderer?.dispose();
    this.ctx.feed.release('chartlab');
  }

  /** The state of the chart (for tests and templates). */
  get state(): LabState {
    return this.model.state();
  }
}
