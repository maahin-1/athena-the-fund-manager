import { createDockview, themeDark, themeLight, type DockviewApi, type IContentRenderer } from 'dockview-core';
import './dockview.css';
import './style.css';
import { parse } from './command';
import { Feed } from './feed';
import {
  deserialize, LayoutError, PRESET_NAMES, PRESETS, serialize, DEFAULT_WATCHLIST,
  type LayoutModel, type PanelState, type PanelType, type PresetName,
} from './layout';
import { LinkBus } from './links';
import { marketState } from './market';
import { el, TITLES, WatchlistStore, type AppContext, type Panel } from './panels/base';
import { AnalysisPanel } from './panels/analysis';
import { ChartPanel } from './panels/chart';
import { DepthPanel } from './panels/depth';
import { QuotePanel } from './panels/quote';
import { TapePanel } from './panels/tape';
import { VerdictPanel } from './panels/verdict';
import { WatchlistPanel } from './panels/watchlist';

const STORE_KEY = 'athena.terminal.current';
const LAST_NAME = '_last';
const BUILDERS: Record<PanelType, new (id: string, ctx: AppContext) => Panel> = {
  watchlist: WatchlistPanel, chart: ChartPanel, depth: DepthPanel, quote: QuotePanel, tape: TapePanel, analysis: AnalysisPanel, verdict: VerdictPanel,
};

class App implements AppContext {
  feed = new Feed(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  bus = new LinkBus();
  watchlist = new WatchlistStore([...DEFAULT_WATCHLIST]);
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
  private market = el('span', 'market');
  private layoutSelect = el('select', 'layout-select');
  private command = el('input', 'cmd');
  private nameBox = el('input', 'namebox');

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
    const host = el('div', 'dock');
    root.append(this.buildBar(), host);
    this.dock = createDockview(host, {
      theme: themeDark,
      createComponent: (options): IContentRenderer => {
        const panel = new BUILDERS[options.name as PanelType](options.id, this);
        this.panels.set(options.id, panel);
        return { element: panel.element, init: () => {}, dispose: () => { this.panels.delete(options.id); panel.dispose(); } };
      },
    });
    this.dock.onDidLayoutChange(() => this.changed());
    this.dock.onDidRemovePanel((p) => {
      delete this.states[p.id];
      this.changed();
    });
    this.feed.onStatus(() => this.paintHealth());
    this.feed.connect();
    window.addEventListener('keydown', (e) => this.hotkeys(e));
    await this.refreshLayoutList();
    await this.restore();
  }

  // ---- layouts -------------------------------------------------------------------------------------------------------------------------

  private model(): LayoutModel {
    return { version: 1, dock: this.dock.toJSON(), arrangement: null, panels: structuredClone(this.states), watchlist: this.watchlist.items, theme: this.theme };
  }

  private load(model: LayoutModel): void {
    this.loading = true;
    try {
      this.dock.clear();
      this.states = structuredClone(model.panels);
      this.watchlist.set(model.watchlist);
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
    this.changed();
  }

  private async restore(): Promise<void> {
    const keep = localStorage.getItem(STORE_KEY);
    if (keep) {
      try {
        this.load(deserialize(keep));
        return;
      } catch (e) {
        this.say(`the saved layout could not be used (${e instanceof LayoutError ? e.message : 'unknown error'}); showing layout A`, true);
      }
    }
    this.load(PRESETS.A());
  }

  private async persist(): Promise<void> {
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
    PRESET_NAMES.forEach((n) => presets.append(new Option(`${n}${{ A: ' · one stock', B: ' · chart grid', C: ' · dense', D: ' · research' }[n]}`, `preset:${n}`)));
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
      if (kind === 'preset') this.load(PRESETS[name as PresetName]());
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
    del.title = 'Delete the saved layout chosen in the list';
    del.addEventListener('click', async () => {
      const chosen = prompt('Delete which saved layout?', this.saved[0] ?? '');
      if (chosen && this.saved.includes(chosen)) await this.deleteSaved(chosen);
    });
    this.command.placeholder = '> symbol or command  (/ to focus)';
    this.command.setAttribute('aria-label', 'command line');
    this.command.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        this.run(this.command.value);
        this.command.value = '';
      } else if (e.key === 'Escape') this.command.blur();
    });
    const theme = el('button', 'btn', 'Theme');
    theme.addEventListener('click', () => {
      this.setTheme(this.theme === 'dark' ? 'light' : 'dark');
      this.changed();
    });
    const full = el('button', 'btn', 'Fullscreen');
    full.addEventListener('click', () => (document.fullscreenElement ? void document.exitFullscreen() : void document.documentElement.requestFullscreen()));
    this.bar.append(el('b', 'brand', 'ATHENA'), this.layoutSelect, save, this.nameBox, del, this.command, this.statusLine, this.market, this.health, theme, full);
    this.paintMarket();
    setInterval(() => this.paintMarket(), 30_000);
    this.paintHealth();
    return this.bar;
  }

  private paintMarket(): void {
    const s = marketState(new Date());
    this.market.textContent = s.open ? '' : s.text;
    this.market.className = 'market';
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
      if (s.feed === 'on') [text, cls] = [`● Angel live${delay}`, 'good'];
      else if (s.feed === 'connecting') [text, cls] = ['● connecting to the feed', 'warn'];
      else [text, cls] = [`● polled (feed ${s.reason ?? 'off'})${delay}`, 'warn'];
    }
    this.health.textContent = text;
    this.health.className = `health ${cls}`;
  }

  private say(text: string, bad = false): void {
    this.statusLine.textContent = text;
    this.statusLine.className = `statusline${bad ? ' bad' : ''}`;
  }

  // ---- commands and keys ---------------------------------------------------------------------------------------------------------------

  private run(input: string): void {
    const cmd = parse(input, { layouts: this.saved });
    switch (cmd.kind) {
      case 'error':
        return this.say(cmd.message, true);
      case 'add':
        this.watchlist.add(cmd.symbol);
        this.changed();
        return this.say(`added ${cmd.symbol}`);
      case 'remove':
        this.watchlist.remove(cmd.symbol);
        this.changed();
        return this.say(`removed ${cmd.symbol}`);
      case 'layout':
        if (PRESET_NAMES.includes(cmd.name as PresetName)) this.load(PRESETS[cmd.name as PresetName]());
        else void this.openSaved(cmd.name);
        return;
      case 'symbol':
      case 'chart': {
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
