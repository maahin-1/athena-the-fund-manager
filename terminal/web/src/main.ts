import { createDockview, themeDark, themeLight, type DockviewApi, type DockviewGroupPanel, type IContentRenderer, type IHeaderActionsRenderer } from 'dockview-core';
import './dockview.css';
import './style.css';
import { parse, type Command } from './command';
import { attachSuggest, commandContext, commandFor, searchInstruments } from './suggest';
import { Feed } from './feed';
import {
  deserialize, LayoutError, NARROW_PX, PRESET_NAMES, PRESETS, serialize, DEFAULT_WATCHLIST,
  type LayoutModel, type PanelState, type PanelType, type PresetName,
} from './layout';
import { LinkBus } from './links';
import { marketState } from './market';
import { ChartLabView } from './chartlab/view';
import { FundamentalsView } from './fundamentals/view';
import { ResearchView } from './research';
import { ScreenView } from './screen';
import { el, TITLES, type AppContext, type Panel } from './panels/base';
import { IndexFeed } from './indices';
import { BarsCache } from './details/data';
import { ScreenStore } from './screenrows';
import { pct, price } from './format';
import { activeList, addSymbol, findList, setActive } from './watchlists/model';
import { serverIO, WatchlistsStore } from './watchlists/store';
import { AnalysisPanel } from './panels/analysis';
import { ChartPanel } from './panels/chart';
import { DepthPanel } from './panels/depth';
import { DetailsPanel } from './panels/details';
import { QuotePanel } from './panels/quote';
import { TapePanel } from './panels/tape';
import { VerdictPanel } from './panels/verdict';
import { WatchlistPanel } from './panels/watchlist';

const STORE_KEY = 'athena.terminal.current';
const LAST_NAME = '_last';
const VIEW_KEY = 'athena.terminal.view';
const NEW_PANEL_TYPES: PanelType[] = ['chart', 'watchlist', 'depth', 'quote', 'details', 'analysis', 'verdict', 'tape'];
type View = 'terminal' | 'lab' | 'fund' | 'research' | 'screen';
const BUILDERS: Record<PanelType, new (id: string, ctx: AppContext) => Panel> = {
  watchlist: WatchlistPanel, chart: ChartPanel, depth: DepthPanel, quote: QuotePanel, tape: TapePanel, analysis: AnalysisPanel, verdict: VerdictPanel, details: DetailsPanel,
};

class App implements AppContext {
  feed = new Feed(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  bus = new LinkBus();
  watchlist = new WatchlistsStore(serverIO(), (() => { try { return localStorage; } catch { return null; } })(), DEFAULT_WATCHLIST);
  indices = new IndexFeed();
  market = new ScreenStore();
  history = new BarsCache();
  private indexStrip = el('span', 'idxstrip');
  private states: Record<string, PanelState> = {};
  private theme: 'dark' | 'light' = 'dark';
  private dock!: DockviewApi;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private loading = false;
  private saved: string[] = [];
  private panels = new Map<string, Panel>();
  private bar = el('div', 'bar');
  private statusLine = el('span', 'statusline');
  private health = el('span', 'health');
  private idle = '';
  private message = '';
  private messageTimer: ReturnType<typeof setTimeout> | null = null;
  private layoutSelect = el('select', 'layout-select');
  private command = el('input', 'cmd');
  private nameBox = el('input', 'namebox');
  private addSelect = el('select', 'add-select');
  private deleteSelect = el('select', 'delete-select');
  private viewTabs = new Map<View, HTMLButtonElement>();
  private dockHost = el('div', 'dock');
  private research!: ResearchView;
  private fund!: FundamentalsView;
  private lab!: ChartLabView;
  private screen!: ScreenView;
  private view: View = 'terminal';
  private lastPreset: PresetName = 'A';
  /** The one-column arrangement was chosen by the window's width, not by the person: it is not saved over their own layout. */
  private narrow = false;
  private titles = new Map<string, string>();
  private headerPaints = new Set<() => void>();
  private known = new Map<string, boolean>();

  state(id: string): PanelState {
    return this.states[id];
  }

  changed(): void {
    if (this.loading) return;
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => void this.persist(), 800);
  }

  async start(): Promise<void> {
    const root = document.getElementById('app')!;
    const host = this.dockHost;
    this.research = new ResearchView(this);
    this.fund = new FundamentalsView(this);
    this.lab = new ChartLabView(this);
    this.screen = new ScreenView(this);
    root.append(this.buildBar(), host, this.lab.element, this.fund.element, this.research.element, this.screen.element);
    this.dock = createDockview(host, {
      theme: themeDark,
      createRightHeaderActionComponent: (group) => this.headerActions(group),
      createComponent: (options): IContentRenderer => {
        const panel = new BUILDERS[options.name as PanelType](options.id, this);
        this.panels.set(options.id, panel);
        return { element: panel.element, init: () => {}, dispose: () => { this.panels.delete(options.id); panel.dispose(); } };
      },
    });
    this.dock.onDidLayoutChange(() => {
      this.changed();
      this.paintHeaders();
    });
    this.dock.onDidActivePanelChange(() => this.paintHeaders());
    this.dock.onDidAddPanel((p) => {
      const t = this.titles.get(p.id);
      if (t) p.api.setTitle(t);
    });
    this.dock.onDidRemovePanel((p) => {
      delete this.states[p.id];
      this.changed();
    });
    this.feed.onStatus(() => this.paintHealth());
    this.feed.connect();
    // the panels are laid out to the window, and laid out again whenever the window changes (resize, maximise, rotate, fullscreen, back from the cache)
    const fit = (): void => {
      const w = this.dockHost.clientWidth;
      const h = this.dockHost.clientHeight;
      if (w > 0 && h > 0) this.dock.layout(w, h);
    };
    window.addEventListener('resize', fit);
    window.visualViewport?.addEventListener('resize', fit);
    window.addEventListener('orientationchange', fit);
    window.addEventListener('pageshow', fit);
    document.addEventListener('fullscreenchange', fit);
    window.addEventListener('keydown', (e) => this.hotkeys(e));
    this.indices.subscribe(() => this.paintIndexStrip());
    this.indices.start();
    window.addEventListener('focus', () => void this.watchlist.sync());
    await this.watchlist.sync();
    await this.refreshLayoutList();
    await this.restore();
    let view: string | null = null;
    try {
      view = localStorage.getItem(VIEW_KEY);
    } catch {
      /* storage can be blocked */
    }
    this.setView(view === 'lab' || view === 'fund' || view === 'research' || view === 'screen' ? view : 'terminal');
  }

  showSymbol(symbol: string): void {
    this.setView('terminal');
    void this.run(`${symbol} CHART`);
  }

  retitle(id: string, text: string): void {
    this.titles.set(id, text);
    this.dock?.getPanel(id)?.api.setTitle(text);
  }

  linkChanged(_id: string): void {
    this.paintHeaders();
  }

  private paintHeaders(): void {
    this.headerPaints.forEach((paint) => paint());
  }

  openFundamentals(symbol: string): void {
    this.bus.setSymbol('blue', symbol);
    this.setView('fund', symbol);
  }

  openResearch(symbol: string): void {
    this.bus.setSymbol('blue', symbol);
    this.setView('research', symbol);
  }

  private setView(view: View, symbol?: string, section?: string): void {
    this.view = view;
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* the view is a convenience */
    }
    this.dockHost.hidden = view !== 'terminal';
    this.viewTabs.forEach((b, v) => b.classList.toggle('on', v === view));
    if (view === 'lab') this.lab.show(symbol ?? this.bus.symbolOf('blue') ?? this.shownSymbol());
    else this.lab.hide();
    if (view === 'fund') this.fund.show(symbol ?? this.bus.symbolOf('blue') ?? this.shownSymbol(), section);
    else this.fund.hide();
    if (view === 'research') this.research.show(symbol ?? this.bus.symbolOf('blue') ?? this.shownSymbol());
    else this.research.hide();
    if (view === 'screen') void this.screen.show();
    else this.screen.hide();
    if (view === 'terminal') this.dock?.layout(this.dockHost.clientWidth, this.dockHost.clientHeight);
  }

  /** The symbol the focused panel shows, else the first panel that shows one. */
  private shownSymbol(): string | undefined {
    const active = this.dock?.activePanel?.id;
    return (active && this.states[active]?.symbol) || Object.values(this.states).find((s) => s.symbol)?.symbol;
  }

  /** The buttons on the right of each group's tab bar: maximise (fill the window) and float (a window you move and resize). */
  private headerActions(group: DockviewGroupPanel): IHeaderActionsRenderer {
    const element = el('div', 'group-actions');
    const dot = el('button', 'gbtn link-dot', '●');
    dot.title = 'Link colour: panels with the same colour follow one symbol (click to change)';
    dot.addEventListener('click', () => {
      const id = group.activePanel?.id;
      if (id) this.panels.get(id)?.cycleGroup();
    });
    const max = el('button', 'gbtn', '⤢');
    const float = el('button', 'gbtn', '⧉');
    const paint = () => {
      const id = group.activePanel?.id;
      dot.dataset.group = (id && this.states[id]?.group) || 'none';
      dot.hidden = !id || this.states[id]?.type === 'tape';
      const maximized = group.api.isMaximized();
      const floating = group.api.location.type === 'floating';
      max.textContent = maximized ? '⤡' : '⤢';
      max.title = maximized ? 'Restore the panel to its place (Esc)' : 'Maximise: fill the whole window';
      float.textContent = floating ? '⇲' : '⧉';
      float.title = floating ? 'Dock this window back into the layout' : 'Float: detach as a window you can move and resize';
    };
    max.addEventListener('click', () => {
      if (group.api.isMaximized()) group.api.exitMaximized();
      else group.api.maximize();
      paint();
    });
    float.addEventListener('click', () => {
      if (group.api.location.type === 'floating') {
        const target = this.dock.groups.find((g) => g.api.location.type === 'grid' && g !== group);
        group.api.moveTo(target ? { group: target, position: 'right' } : { position: 'right' });
      } else this.dock.addFloatingGroup(group, { width: Math.min(560, this.dockHost.clientWidth - 40), height: Math.min(420, this.dockHost.clientHeight - 40) });
      paint();
    });
    element.append(dot, max, float);
    this.headerPaints.add(paint);
    paint();
    return { element, init: paint, dispose: () => this.headerPaints.delete(paint) };
  }

  /** A new panel, split off to the right of the one in focus, showing the symbol the blue group follows. */
  private addPanel(type: PanelType): void {
    let n = 1;
    while (this.states[`${type}-${n}`]) n++;
    const id = `${type}-${n}`;
    const symbol = this.bus.symbolOf('blue') ?? this.watchlist.items[0] ?? 'SBIN';
    const state: PanelState = { type, group: type === 'tape' ? 'none' : 'blue' };
    if (type !== 'watchlist' && type !== 'tape') state.symbol = symbol;
    if (type === 'chart') state.range = '6M';
    this.states[id] = state;
    const ref = this.dock.activePanel?.id;
    this.dock.addPanel({ id, component: type, title: TITLES[type], ...(ref ? { position: { referencePanel: ref, direction: 'right' as const } } : {}) });
    this.dock.getPanel(id)?.api.setActive();
    this.say(`added a ${TITLES[type].toLowerCase()} panel; drag its tab to move it, drag the thin lines between panels to resize`);
  }

  // ---- layouts -------------------------------------------------------------------------------------------------------------------------

  private model(): LayoutModel {
    return { version: 1, dock: this.dock.toJSON(), arrangement: null, panels: structuredClone(this.states), watchlist: this.watchlist.items, theme: this.theme };
  }

  private load(model: LayoutModel): void {
    this.narrow = false;
    this.loading = true;
    try {
      this.dock.clear();
      this.states = structuredClone(model.panels);
      this.watchlist.seedFrom(model.watchlist);
      this.setTheme(model.theme);
      if (model.dock) {
        this.dock.fromJSON(model.dock as never);
      } else if (model.arrangement) {
        for (const p of model.arrangement) {
          const horizontal = p.direction === 'left' || p.direction === 'right';
          this.dock.addPanel({
            id: p.id, component: p.type, title: TITLES[p.type],
            ...(p.ref && p.direction ? { position: { referencePanel: p.ref, direction: p.direction } } : {}),
            ...(p.size ? (horizontal ? { initialWidth: p.size } : { initialHeight: p.size }) : {}),
          });
        }
      }
    } finally {
      this.loading = false;
    }
    // tabs added 'within' a group leave the last one on show: show the watchlist (the first thing a narrow window needs)
    if (model.arrangement?.some((p) => p.direction === 'within')) this.dock.getPanel(model.arrangement.find((p) => p.type === 'watchlist')?.id ?? '')?.api.setActive();
    this.changed();
  }

  private async restore(): Promise<void> {
    if (window.innerWidth < NARROW_PX) {
      this.loadPreset('M');
      this.narrow = true;
      return;
    }
    const keep = localStorage.getItem(STORE_KEY);
    if (keep) {
      try {
        this.load(deserialize(keep));
        return;
      } catch (e) {
        this.say(`the saved layout could not be used (${e instanceof LayoutError ? e.message : 'unknown error'}); showing layout A`, true);
      }
    }
    this.loadPreset('A');
  }

  private loadPreset(name: PresetName): void {
    this.lastPreset = name;
    this.load(PRESETS[name]());
    if (this.view !== 'terminal') this.setView('terminal');
  }

  private async persist(): Promise<void> {
    if (this.narrow) return;
    const text = serialize(this.model());
    try {
      localStorage.setItem(STORE_KEY, text);
    } catch {
      /* storage can be blocked; the server copy below still works */
    }
    try {
      await fetch(`/api/layouts/${LAST_NAME}`, { method: 'PUT', body: text });
    } catch {
      /* the server may be restarting */
    }
  }

  private async refreshLayoutList(): Promise<void> {
    try {
      const body = (await (await fetch('/api/layouts')).json()) as { names: string[] };
      this.saved = body.names.filter((n) => n !== LAST_NAME);
    } catch {
      this.saved = [];
    }
    this.layoutSelect.replaceChildren(new Option('Layouts', ''));
    const presets = document.createElement('optgroup');
    presets.label = 'Presets';
    PRESET_NAMES.forEach((n) => presets.append(new Option(`${n}${{ A: ' · one stock', B: ' · chart grid', C: ' · dense', D: ' · research', M: ' · narrow screen' }[n]}`, `preset:${n}`)));
    this.layoutSelect.append(presets);
    if (this.saved.length) {
      const mine = document.createElement('optgroup');
      mine.label = 'Saved';
      this.saved.forEach((n) => mine.append(new Option(n, `saved:${n}`)));
      this.layoutSelect.append(mine);
    }
  }

  private async saveAs(name: string): Promise<void> {
    const r = await fetch(`/api/layouts/${encodeURIComponent(name)}`, { method: 'PUT', body: serialize(this.model()) });
    if (!r.ok) return this.say(r.status === 400 ? 'a layout name is 1 to 40 letters, digits, spaces, - or _' : 'the layout could not be saved', true);
    await this.refreshLayoutList();
    this.say(`saved layout "${name}"`);
  }

  private async openSaved(name: string): Promise<void> {
    const r = await fetch(`/api/layouts/${encodeURIComponent(name)}`);
    if (!r.ok) return this.say(`no saved layout "${name}"`, true);
    try {
      this.load(deserialize(await r.text()));
      this.say(`loaded layout "${name}"`);
    } catch (e) {
      this.say(`layout "${name}" could not be used: ${e instanceof LayoutError ? e.message : 'unknown error'}`, true);
    }
  }

  private async deleteSaved(name: string): Promise<void> {
    await fetch(`/api/layouts/${encodeURIComponent(name)}`, { method: 'DELETE' });
    await this.refreshLayoutList();
    this.say(`deleted layout "${name}"`);
  }

  // ---- the top bar ---------------------------------------------------------------------------------------------------------------------

  private buildBar(): HTMLElement {
    this.layoutSelect.addEventListener('change', () => {
      const [kind, name] = this.layoutSelect.value.split(/:(.*)/s);
      this.layoutSelect.value = '';
      if (kind === 'preset') this.loadPreset(name as PresetName);
      else if (kind === 'saved') void this.openSaved(name);
    });
    const save = el('button', 'btn', 'Save');
    save.title = 'Save this arrangement under a name';
    this.nameBox.placeholder = 'layout name';
    this.nameBox.hidden = true;
    this.nameBox.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && this.nameBox.value.trim()) {
        void this.saveAs(this.nameBox.value.trim());
        this.nameBox.hidden = true;
      } else if (e.key === 'Escape') this.nameBox.hidden = true;
    });
    save.addEventListener('click', () => {
      this.nameBox.hidden = !this.nameBox.hidden;
      if (!this.nameBox.hidden) this.nameBox.focus();
    });
    const del = el('button', 'btn', 'Delete');
    del.title = 'Delete one of your saved layouts';
    this.deleteSelect.hidden = true;
    this.deleteSelect.addEventListener('change', async () => {
      const name = this.deleteSelect.value;
      this.deleteSelect.hidden = true;
      if (name && confirm(`Delete the saved layout "${name}"?`)) await this.deleteSaved(name);
    });
    del.addEventListener('click', () => {
      if (!this.saved.length) return this.say('you have no saved layouts to delete');
      this.deleteSelect.replaceChildren(new Option('Delete which layout?', ''), ...this.saved.map((n) => new Option(n, n)));
      this.deleteSelect.hidden = !this.deleteSelect.hidden;
    });
    this.command.placeholder = '> symbol or command  (/ to focus)';
    this.command.setAttribute('aria-label', 'command line');
    this.command.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        void this.run(this.command.value);
        this.command.value = '';
      } else if (e.key === 'Escape') this.command.blur();
    });
    attachSuggest(this.command, {
      query: (value) => commandContext(value)?.text ?? null,
      onPick: (hit, value) => {
        this.command.value = '';
        void this.exec(commandFor(hit.s, commandContext(value)), true);
      },
    });
    const theme = el('button', 'btn', 'Theme');
    theme.addEventListener('click', () => {
      this.setTheme(this.theme === 'dark' ? 'light' : 'dark');
      this.changed();
    });
    const reset = el('button', 'btn', 'Reset');
    reset.title = 'Put the panels back where the last preset (or layout A) puts them';
    reset.addEventListener('click', () => {
      this.loadPreset(this.lastPreset);
      this.say(`layout ${this.lastPreset} restored`);
    });
    this.addSelect.append(new Option('+ Panel', ''));
    NEW_PANEL_TYPES.forEach((t) => this.addSelect.append(new Option(TITLES[t], t)));
    this.addSelect.title = 'Add a panel to the terminal';
    this.addSelect.addEventListener('change', () => {
      const t = this.addSelect.value as PanelType;
      this.addSelect.value = '';
      if (!t) return;
      this.setView('terminal');
      this.addPanel(t);
    });
    for (const [v, label] of [['terminal', 'Terminal'], ['lab', 'Chart Lab'], ['fund', 'Fundamentals'], ['research', 'Research'], ['screen', 'Screen']] as [View, string][]) {
      const b = el('button', 'vtab', label);
      b.title = v === 'lab' ? 'One big chart with indicators, compare, log and percent scales, templates and a data window (F6 cycles the views)' : v === 'fund' ? 'Statements, ratios and growth for one company, from its filings (F6 cycles the views)' : v === 'research' ? 'Every analyst in full, plus fundamentals and technicals (F6 cycles the views)' : v === 'screen' ? 'Screen every stock and ETF with presets and filters (F6 cycles the views)' : 'Charts, depth and the dockable panels (F6 cycles the views)';
      b.addEventListener('click', () => this.setView(v));
      this.viewTabs.set(v, b);
    }
    const full = el('button', 'btn', 'Fullscreen');
    full.addEventListener('click', () => (document.fullscreenElement ? void document.exitFullscreen() : void document.documentElement.requestFullscreen()));
    this.bar.append(el('b', 'brand', 'ATHENA'), ...this.viewTabs.values(), this.layoutSelect, this.addSelect, reset, save, this.nameBox, del, this.deleteSelect, this.command, this.indexStrip, this.statusLine, this.health, theme, full);
    this.paintMarket();
    setInterval(() => this.paintMarket(), 30_000);
    this.paintHealth();
    return this.bar;
  }

  /** Nifty 50, Nifty Bank and India VIX in the top bar, from the exchange's snapshot. Old values are marked, never hidden. */
  private paintIndexStrip(): void {
    this.indexStrip.replaceChildren();
    for (const name of ['NIFTY 50', 'NIFTY BANK', 'INDIA VIX']) {
      const row = this.indices.get(name);
      if (!row) continue;
      const item = el('span', 'idx');
      item.dataset.index = name;
      const dir = (row.ch ?? 0) > 0 ? 'up' : (row.ch ?? 0) < 0 ? 'dn' : 'flat';
      item.append(el('b', '', name.replace('NIFTY ', 'N ').replace('INDIA ', '')), document.createTextNode(` ${price(row.l)} `), el('span', dir, row.pct === null ? '' : pct(row.pct)));
      this.indexStrip.append(item);
    }
    this.indexStrip.classList.toggle('stale', this.indices.unavailable);
    this.indexStrip.title = this.indices.unavailable ? 'index values are old: the server could not refresh them' : `exchange snapshot ${this.indices.asOf}`;
  }

  private paintMarket(): void {
    const s = marketState(new Date());
    this.idle = s.open ? '' : s.text;
    this.paintStatus();
  }

  private setTheme(theme: 'dark' | 'light'): void {
    this.theme = theme;
    document.documentElement.dataset.theme = theme;
    this.dock?.updateOptions({ theme: theme === 'dark' ? themeDark : themeLight });
  }

  private paintHealth(): void {
    const s = this.feed.status;
    const delay = s.latencyMs === null ? '' : ` · ${s.latencyMs} ms`;
    let text = '● offline';
    let cls = 'bad';
    if (s.socket === 'connecting') [text, cls] = ['● connecting', 'warn'];
    else if (s.socket === 'open') {
      if (s.feed === 'on') {
        // the stream can be up while single symbols are served by the delayed exchange poll (only a fault while the market is open)
        const polled = marketState(new Date()).open ? this.watchlist.items.filter((s2) => this.feed.quotes.get(s2)?.src === 'poll').length : 0;
        [text, cls] = polled ? [`● Angel live · ${polled} on delayed poll${delay}`, 'warn'] : [`● Angel live${delay}`, 'good'];
      }
      else if (s.feed === 'connecting') [text, cls] = ['● connecting to the feed', 'warn'];
      else [text, cls] = [`● polled (feed ${s.reason ?? 'off'})${delay}`, 'warn'];
    }
    this.health.textContent = text;
    this.health.className = `health ${cls}`;
  }

  /** A message from the last action stays for 10 seconds, then the market state (when it is shut) shows again. */
  private say(text: string, bad = false): void {
    this.message = text;
    this.statusLine.className = `statusline${bad ? ' bad' : ''}`;
    this.statusLine.textContent = text;
    this.statusLine.title = text;
    if (this.messageTimer) clearTimeout(this.messageTimer);
    this.messageTimer = setTimeout(() => {
      this.message = '';
      this.paintStatus();
    }, 10_000);
  }

  private paintStatus(): void {
    if (this.message) return;
    this.statusLine.className = 'statusline';
    this.statusLine.textContent = this.idle;
    this.statusLine.title = this.idle;
  }

  // ---- commands and keys ---------------------------------------------------------------------------------------------------------------

  /** Whether the warehouse lists the symbol (a stock, ETF or index). Unknown only when the server says so; a failed lookup lets the symbol through. */
  async isKnown(symbol: string): Promise<boolean> {
    const cached = this.known.get(symbol);
    if (cached !== undefined) return cached;
    try {
      const r = await fetch(`/api/known?symbol=${encodeURIComponent(symbol)}`);
      if (!r.ok) return true;
      const ok = ((await r.json()) as { known: boolean }).known;
      this.known.set(symbol, ok);
      return ok;
    } catch {
      return true;
    }
  }

  private async run(input: string): Promise<void> {
    let cmd = parse(input, { layouts: this.saved });
    let checked = false;
    const unlisted = 'symbol' in cmd && typeof cmd.symbol === 'string' && cmd.kind !== 'remove' && !(await this.isKnown(cmd.symbol));
    if (cmd.kind === 'error' || unlisted) {
      // not a command, or not a listed symbol (an old name such as ZOMATO): a name such as "tata motors" takes the best match, as a search box does on Enter
      const ctx = commandContext(input);
      const best = ctx ? (await searchInstruments(ctx.text, { limit: 1 }))[0] : undefined;
      if (best) {
        cmd = commandFor(best.s, ctx);
        checked = true; // the search only offers instruments the market lists
      }
    }
    return this.exec(cmd, checked);
  }

  private async exec(cmd: Command, checked = false): Promise<void> {
    if (!checked && 'symbol' in cmd && typeof cmd.symbol === 'string' && cmd.kind !== 'remove' && !(await this.isKnown(cmd.symbol))) {
      return this.say(`${cmd.symbol} is not a listed stock, ETF or index`, true);
    }
    switch (cmd.kind) {
      case 'error':
        return this.say(cmd.message, true);
      case 'add': {
        const target = cmd.list ? findList(this.watchlist.doc, cmd.list) : activeList(this.watchlist.doc);
        if (!target) return this.say(`no watchlist named "${cmd.list}"; LIST <name> switches, the list menu in the watchlist panel makes one`, true);
        this.watchlist.update((d) => addSymbol(d, cmd.symbol, target.id));
        this.changed();
        return this.say(`added ${cmd.symbol} to ${target.name}`);
      }
      case 'list': {
        const target = findList(this.watchlist.doc, cmd.name);
        if (!target) return this.say(`no watchlist named "${cmd.name}"`, true);
        this.watchlist.update((d) => setActive(d, target.id));
        return this.say(`watchlist ${target.name}`);
      }
      case 'remove':
        this.watchlist.remove(cmd.symbol);
        this.changed();
        return this.say(`removed ${cmd.symbol}`);
      case 'lab':
        this.say(cmd.symbol ? `chart lab ${cmd.symbol}` : 'chart lab');
        if (cmd.symbol) this.bus.setSymbol('blue', cmd.symbol);
        return this.setView('lab', cmd.symbol ?? undefined);
      case 'fund':
        this.say(cmd.section ? `${cmd.section.toLowerCase()}${cmd.symbol ? ` ${cmd.symbol}` : ''}` : cmd.symbol ? `fundamentals ${cmd.symbol}` : 'fundamentals');
        if (cmd.symbol) this.bus.setSymbol('blue', cmd.symbol);
        return this.setView('fund', cmd.symbol ?? undefined, cmd.section);
      case 'research':
        this.say(cmd.symbol ? `research ${cmd.symbol}` : 'research');
        if (cmd.symbol) this.bus.setSymbol('blue', cmd.symbol);
        return this.setView('research', cmd.symbol ?? undefined);
      case 'details':
        return this.showDetails(cmd.symbol);
      case 'terminal':
        return this.setView('terminal');
      case 'screen':
        return this.setView('screen');
      case 'layout':
        if (PRESET_NAMES.includes(cmd.name as PresetName)) this.loadPreset(cmd.name as PresetName);
        else void this.openSaved(cmd.name);
        return;
      case 'symbol':
      case 'chart': {
        if ((this.view === 'lab' || this.view === 'fund') && cmd.kind === 'symbol') {
          this.say(cmd.symbol);
          this.bus.setSymbol('blue', cmd.symbol);
          return this.view === 'lab' ? this.lab.show(cmd.symbol) : this.fund.show(cmd.symbol);
        }
        if (this.view === 'research' && cmd.kind === 'symbol') {
          this.say(cmd.symbol);
          this.bus.setSymbol('blue', cmd.symbol);
          return this.research.show(cmd.symbol);
        }
        if (this.view !== 'terminal') this.setView('terminal');
        const target = this.focusTarget(cmd.kind === 'chart' ? 'chart' : null);
        if (!target) return this.say('no panel to show it in', true);
        this.say(cmd.symbol);
        this.panels.get(target)?.setSymbol(cmd.symbol); // an unlinked panel does not hear the bus, so move it directly
        this.bus.setSymbol(this.states[target]?.group ?? 'none', cmd.symbol);
        this.dock.getPanel(target)?.api.setActive();
        return;
      }
    }
  }

  /** Bring the Details panel forward (adding one when the layout has none), on this stock when one is given. */
  private showDetails(symbol: string | null): void {
    if (this.view !== 'terminal') this.setView('terminal');
    let id = Object.entries(this.states).find(([, s]) => s.type === 'details')?.[0];
    if (!id) {
      this.addPanel('details');
      id = Object.entries(this.states).find(([, s]) => s.type === 'details')?.[0];
    }
    if (!id) return;
    if (symbol) {
      this.say(`details ${symbol}`);
      this.panels.get(id)?.setSymbol(symbol); // an unlinked panel does not hear the bus, so move it directly
      this.bus.setSymbol(this.states[id]?.group ?? 'none', symbol);
    }
    this.dock.getPanel(id)?.api.setActive();
  }

  /** The panel a typed symbol goes to: the active one if it shows a symbol, else the first chart. */
  private focusTarget(type: PanelType | null): string | null {
    const withSymbol = Object.entries(this.states).filter(([, s]) => s.type !== 'watchlist' && s.type !== 'tape');
    if (type) return withSymbol.find(([, s]) => s.type === type)?.[0] ?? null;
    const active = this.dock.activePanel?.id;
    if (active && this.states[active] && withSymbol.some(([id]) => id === active)) return active;
    return withSymbol.find(([, s]) => s.type === 'chart')?.[0] ?? withSymbol[0]?.[0] ?? null;
  }

  private hotkeys(e: KeyboardEvent): void {
    const typing = (e.target as HTMLElement | null)?.tagName === 'INPUT';
    if ((e.key === '/' && !typing) || (e.ctrlKey && e.key.toLowerCase() === 'k')) {
      e.preventDefault();
      this.command.focus();
      this.command.select();
      return;
    }
    if (e.key === 'Escape' && !typing && this.dock.hasMaximizedGroup()) {
      this.dock.exitMaximizedGroup();
      return;
    }
    if (e.key === 'F6') {
      e.preventDefault();
      this.setView(this.view === 'terminal' ? 'lab' : this.view === 'lab' ? 'fund' : this.view === 'fund' ? 'research' : this.view === 'research' ? 'screen' : 'terminal');
      return;
    }
    const groups: Record<string, PanelType[]> = { F1: ['watchlist'], F2: ['chart'], F3: ['depth', 'quote'], F4: ['tape'] };
    const wanted = groups[e.key];
    if (!wanted) return;
    e.preventDefault();
    const id = Object.entries(this.states).find(([, s]) => wanted.includes(s.type))?.[0];
    if (id) this.dock.getPanel(id)?.api.setActive();
  }
}

new App().start().catch((e) => {
  document.body.textContent = `Athena Terminal could not start: ${e instanceof Error ? e.message : 'unknown error'}`;
});
