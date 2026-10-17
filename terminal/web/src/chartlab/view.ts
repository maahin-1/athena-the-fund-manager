import type { Bar } from '../bars';
import type { Quote } from '../feed';
import { compact, pct, price, signed } from '../format';
import { marketState } from '../market';
import { el, type AppContext } from '../panels/base';
import { barLimit, type Range, type Timeframe } from '../ranges';
import { settingsDialog } from './dialog';
import { CHART_TYPES, ChartModel, type ChartType, type LabState, type ScaleMode } from './model';
import { caption, REGISTRY } from './registry';
import { lightweightRenderer, type ChartRenderer, type RendererFactory } from './render';
import { deserialize, History, serialize, StateError, Templates, type KeyValue } from './store';

const LINK_GROUP = 'blue' as const;
const STATE_KEY = 'athena.chartlab.state';
const INTERVALS: Timeframe[] = ['1m', '5m', '15m', '1D'];
const SCALES: { id: ScaleMode; label: string; title: string }[] = [
  { id: 'auto', label: 'Auto', title: 'Linear price scale' },
  { id: 'log', label: 'Log', title: 'Logarithmic price scale' },
  { id: 'percent', label: '%', title: 'Percent change from the first visible bar' },
];

/** The ranges an interval offers (a range implies its interval). `bars` null shows every bar; 'ytd' is worked out from today's date. */
const RANGES: Record<Timeframe, Range[]> = {
  '1m': [{ label: '1D', tf: '1m', bars: 375 }],
  '5m': [{ label: '1D', tf: '5m', bars: 75 }, { label: '5D', tf: '5m', bars: 375 }],
  '15m': [{ label: '5D', tf: '15m', bars: 125 }, { label: 'All', tf: '15m', bars: null }],
  '1D': [
    { label: '1M', tf: '1D', bars: 22 }, { label: '3M', tf: '1D', bars: 63 }, { label: '6M', tf: '1D', bars: 126 }, { label: 'YTD', tf: '1D', bars: 'ytd' },
    { label: '1Y', tf: '1D', bars: 252 }, { label: '5Y', tf: '1D', bars: 1260 }, { label: 'All', tf: '1D', bars: null },
  ],
};
const DEFAULT_RANGE: Record<Timeframe, string> = { '1m': '1D', '5m': '5D', '15m': '5D', '1D': '1Y' };

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
  private renderer: ChartRenderer | null = null;
  private fetcher: BarsFetcher;
  private makeRenderer: RendererFactory;
  private storage: KeyValue | null;
  private toolbar = el('div', 'lab-bar');
  private wrap = el('div', 'lab-chart');
  private host = el('div', 'lab-host');
  private legends = el('div', 'lab-legends');
  private windowBox = el('div', 'lab-window');
  private note = el('div', 'note lab-note');
  private badge = el('span', 'lab-badge');
  private headline = el('span', 'lab-head');
  private symbolInput = el('input', 'cmd lab-symbol');
  private compareInput = el('input', 'cmd lab-compare');
  private typeSelect = el('select', 'lab-select lab-type');
  private rangeRow = el('span', 'ranges');
  private intervalRow = el('span', 'ranges');
  private scaleRow = el('span', 'ranges');
  private adjButton = el('button', 'rbtn', 'ADJ');
  private undoButton = el('button', 'btn', 'Undo');
  private redoButton = el('button', 'btn', 'Redo');
  private templateSelect = el('select', 'lab-select lab-templates');
  private popover = el('div', 'lab-pop');
  private legendValues = new Map<string, HTMLElement>();
  private symbol = '';
  private tf: Timeframe = '1D';
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
    this.fetcher = options.fetchBars ?? fetchBars;
    this.makeRenderer = options.renderer ?? lightweightRenderer;
    this.buildToolbar();
    this.popover.hidden = true;
    this.wrap.append(this.host, this.legends);
    const sub = el('div', 'lab-sub');
    sub.append(this.rangeRow);
    const main = el('div', 'lab-main');
    main.append(this.wrap, this.windowBox);
    this.element.append(this.toolbar, sub, this.popover, main, this.note);
    this.model.subscribe((kind) => this.modelChanged(kind));
    this.offQuotes = ctx.feed.onQuotes((qs) => this.onQuotes(qs));
    this.offLink = ctx.bus.subscribe(LINK_GROUP, (s) => this.follow(s));
    this.restoreSaved();
  }

  // ---- showing and loading -----------------------------------------------------------------------------------------------------------------

  show(symbol?: string): void {
    this.visible = true;
    this.element.hidden = false;
    if (!this.renderer) {
      this.renderer = this.makeRenderer(this.host);
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

  private range(): Range {
    return RANGES[this.tf].find((r) => r.label === this.rangeLabel) ?? RANGES[this.tf][0];
  }

  private async reload(): Promise<void> {
    if (!this.symbol) return;
    const mine = ++this.token;
    const range = this.range();
    const limit = this.tf === '1D' ? (range.bars === 'ytd' ? barLimit(range, new Date()) : range.bars) : null;
    this.say('loading…');
    let fetched: Fetched;
    try {
      fetched = await this.fetcher(this.symbol, this.tf, limit, this.model.adjusted);
    } catch {
      if (mine === this.token) this.say('could not reach the server');
      return;
    }
    if (mine !== this.token) return;
    let bars = fetched.bars;
    if (this.tf !== '1D' && typeof range.bars === 'number') bars = bars.slice(-range.bars);
    this.hover = null;
    this.model.setBars(bars, this.tf);
    this.renderer?.fit();
    this.say(bars.length ? fetched.note : `no ${this.tf} bars for ${this.symbol}`);
    if (this.model.compare) void this.loadCompare(this.model.compare.symbol);
    this.paintBar();
  }

  private async loadCompare(symbol: string): Promise<void> {
    const mine = this.token;
    try {
      const got = await this.fetcher(symbol, this.tf, null, true);
      if (mine === this.token && this.model.compare?.symbol === symbol) this.model.setCompare(symbol, got.bars);
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

  private undo(): void {
    const s = this.history.undo();
    if (s) {
      this.apply(s);
      this.persist();
    }
  }

  private redo(): void {
    const s = this.history.redo();
    if (s) {
      this.apply(s);
      this.persist();
    }
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
    for (const tf of INTERVALS) {
      const b = el('button', 'rbtn', tf);
      b.dataset.interval = tf;
      b.title = `${tf} bars`;
      b.addEventListener('click', () => {
        this.tf = tf;
        this.rangeLabel = DEFAULT_RANGE[tf];
        void this.reload();
        this.paintBar();
      });
      this.intervalRow.append(b);
    }
    this.typeSelect.setAttribute('aria-label', 'chart type');
    for (const t of CHART_TYPES) this.typeSelect.append(new Option(t.label, t.id));
    this.typeSelect.addEventListener('change', () => this.commit(() => this.model.setType(this.typeSelect.value as ChartType)));
    const indicators = this.button('Indicators', 'Add an indicator to the chart', () => this.togglePopover());
    indicators.classList.add('lab-ind');
    this.compareInput.placeholder = 'compare symbol';
    this.compareInput.setAttribute('aria-label', 'compare symbol');
    this.compareInput.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const s = this.compareInput.value.trim().toUpperCase();
      if (!s) return this.commit(() => this.model.setCompare(null));
      this.commit(() => this.model.setCompare(s, []));
      void this.loadCompare(s);
    });
    for (const s of SCALES) {
      const b = el('button', 'rbtn', s.label);
      b.dataset.scale = s.id;
      b.title = s.title;
      b.addEventListener('click', () => this.commit(() => this.model.setScale(s.id)));
      this.scaleRow.append(b);
    }
    this.adjButton.title = 'Prices adjusted for splits and bonuses (on) or as printed (off)';
    this.adjButton.addEventListener('click', () => {
      this.commit(() => this.model.setAdjusted(!this.model.adjusted));
      void this.reload();
    });
    this.undoButton.addEventListener('click', () => this.undo());
    this.redoButton.addEventListener('click', () => this.redo());
    this.undoButton.title = 'Undo the last change to the chart';
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
      this.symbolInput, this.intervalRow, this.typeSelect, indicators, this.compareInput, this.scaleRow, this.adjButton, this.undoButton, this.redoButton,
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
    };
    search.addEventListener('input', fill);
    search.addEventListener('keydown', (e) => e.key === 'Escape' && (this.popover.hidden = true));
    fill();
    this.popover.replaceChildren(search, list);
    this.popover.hidden = false;
    search.focus();
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
    this.paintLegends();
    this.paintValues();
    this.paintBar();
  }

  private paintBar(): void {
    this.symbolInput.value = this.symbol;
    this.intervalRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.interval === this.tf));
    this.scaleRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.scale === this.model.scale));
    this.adjButton.classList.toggle('on', this.model.adjusted);
    this.typeSelect.value = this.model.type;
    this.compareInput.value = this.model.compare?.symbol ?? '';
    this.undoButton.disabled = !this.history.canUndo;
    this.redoButton.disabled = !this.history.canRedo;
    this.rangeRow.replaceChildren();
    for (const r of RANGES[this.tf]) {
      const b = el('button', `rbtn${r.label === this.range().label ? ' on' : ''}`, r.label);
      b.dataset.range = r.label;
      b.addEventListener('click', () => {
        this.rangeLabel = r.label;
        void this.reload();
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
    settingsDialog(this.element, this.model.def(inst), inst, (params, colors) => {
      let error: string | null = null;
      this.commit(() => (error = this.model.updateIndicator(uid, params, colors)));
      return error;
    });
  }

  private paintValues(): void {
    const index = this.hover ?? this.model.bars.length - 1;
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
    const when = new Date((w.t + (this.tf === '1D' ? 0 : 19_800)) * 1000).toISOString().replace('T', ' ').slice(0, this.tf === '1D' ? 10 : 16);
    this.windowBox.append(el('div', 'lab-wh', 'Data window'), el('div', 'note', when));
    for (const [k, v] of [['Open', price(w.o)], ['High', price(w.h)], ['Low', price(w.l)], ['Close', price(w.c)], ['Change', `${signed(w.change)} (${pct(w.changePct)})`], ['Volume', compact(w.v)]]) {
      const row = el('div', 'lab-wrow');
      row.append(el('span', '', k), el('span', k === 'Change' ? dir : '', v));
      this.windowBox.append(row);
    }
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
    this.renderer?.dispose();
    this.ctx.feed.release('chartlab');
  }

  /** The state of the chart (for tests and templates). */
  get state(): LabState {
    return this.model.state();
  }
}
