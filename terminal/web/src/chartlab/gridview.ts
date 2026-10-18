import { el, type AppContext } from '../panels/base';
import { defaultGrid, GRID_KEY, GridSync, LAYOUT_IDS, LAYOUTS, parseGridState, serializeGrid, type GridState, type SyncFlags } from './grid';
import type { RendererFactory } from './render';
import { ChartLabView, fetchBars, type BarsFetcher, type SharedIndicators } from './view';
import type { KeyValue } from './store';

const SWITCHES: { key: keyof SyncFlags; label: string; title: string }[] = [
  { key: 'symbol', label: 'Symbol', title: 'A symbol picked in one chart is shown in all' },
  { key: 'interval', label: 'Interval', title: 'An interval chosen in one chart is chosen in all' },
  { key: 'crosshair', label: 'Crosshair', title: 'The pointer in one chart shows the same moment in the others' },
  { key: 'time', label: 'Time', title: 'Scrolling or zooming one chart shows the same period in the others' },
];

export interface GridOptions {
  storage?: KeyValue | null;
  renderer?: RendererFactory;
  fetchBars?: BarsFetcher;
}

/** Charts of one symbol at one interval ask for the same bars at the same moment (a symbol change reaches every cell): the same request is made once, and its
 *  answer is kept for a few seconds. A failed request is not kept. */
export function sharedFetcher(inner: BarsFetcher, keepMs = 15_000, now: () => number = Date.now): BarsFetcher {
  const kept = new Map<string, { at: number; answer: ReturnType<BarsFetcher> }>();
  return (symbol, tf, limit, adjusted) => {
    const key = `${symbol}|${tf}|${limit}|${adjusted}`;
    const hit = kept.get(key);
    if (hit && now() - hit.at < keepMs) return hit.answer;
    const answer = inner(symbol, tf, limit, adjusted);
    kept.set(key, { at: now(), answer });
    answer.catch(() => kept.delete(key));
    return answer;
  };
}

function browserStorage(): KeyValue | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** The Grid tab: up to four Chart Lab charts with the symbol, interval, crosshair and time range kept in step as the person chooses. The page holds the layout,
 *  the four switches and each chart's symbol and interval; each chart keeps its own type, indicators and scale. */
export class GridView {
  readonly element = el('div', 'grid-page');
  private bar = el('div', 'grid-bar');
  private board = el('div', 'grid-board');
  private layoutSelect = el('select', 'lab-select');
  private checks = new Map<keyof SyncFlags, HTMLInputElement>();
  private cells: ChartLabView[] = [];
  private sync = new GridSync();
  private state: GridState;
  private storage: KeyValue | null;
  private visible = false;
  private replayButton = el('button', 'rbtn', 'Replay');
  /** The moment the chart driving a replay has reached, or null when no replay is on. */
  private moment: number | null = null;

  constructor(private ctx: AppContext, shared: SharedIndicators, options: GridOptions = {}) {
    this.element.hidden = true;
    this.storage = options.storage === undefined ? browserStorage() : options.storage;
    const fetcher = sharedFetcher(options.fetchBars ?? fetchBars);
    this.state = this.load();
    this.sync.flags = { ...this.state.sync };
    this.buildBar();
    for (let i = 0; i < this.state.cells.length; i++) {
      const view = new ChartLabView(ctx, {
        renderer: options.renderer,
        fetchBars: fetcher,
        storage: this.storage,
        cell: {
          id: `g${i}`,
          shared,
          send: {
            symbol: (s) => {
              this.sync.symbolPicked(view, s);
              this.save();
            },
            interval: (id) => {
              this.sync.intervalPicked(view, id);
              this.save();
            },
            crosshair: (m) => this.sync.crosshair(view, m),
            period: (a, b) => this.sync.period(view, a, b),
            replay: (m) => {
              this.moment = m;
              this.sync.replay(view, m);
              this.paintReplay();
            },
          },
        },
      });
      view.element.dataset.cell = String(i);
      view.applyInterval(this.state.cells[i].interval); // before it is shown, so its first load is at this interval
      this.cells.push(view);
      this.board.append(view.element);
    }
    this.element.append(this.bar, this.board);
    this.layout();
  }

  private load(): GridState {
    try {
      return parseGridState(this.storage?.getItem(GRID_KEY) ?? null);
    } catch {
      return defaultGrid();
    }
  }

  private save(): void {
    this.cells.forEach((c, i) => {
      this.state.cells[i] = { symbol: c.currentSymbol || this.state.cells[i].symbol, interval: c.interval };
    });
    try {
      this.storage?.setItem(GRID_KEY, serializeGrid(this.state));
    } catch {
      /* the grid works without it */
    }
  }

  private buildBar(): void {
    this.layoutSelect.setAttribute('aria-label', 'grid layout');
    for (const id of LAYOUT_IDS) this.layoutSelect.append(new Option(LAYOUTS[id].label, id));
    this.layoutSelect.value = this.state.layout;
    this.layoutSelect.addEventListener('change', () => {
      this.state.layout = this.layoutSelect.value as GridState['layout'];
      this.layout();
      this.save();
    });
    this.bar.append(this.layoutSelect);
    for (const s of SWITCHES) {
      const label = el('label', 'grid-switch');
      label.title = s.title;
      const box = el('input');
      box.type = 'checkbox';
      box.checked = this.state.sync[s.key];
      box.setAttribute('aria-label', `sync ${s.key}`);
      box.addEventListener('change', () => {
        this.state.sync[s.key] = box.checked;
        this.sync.flags = { ...this.state.sync };
        this.save();
      });
      this.checks.set(s.key, box);
      label.append(box, document.createTextNode(` ${s.label}`));
      this.bar.append(label);
    }
    this.replayButton.type = 'button';
    this.replayButton.setAttribute('aria-label', 'grid replay');
    this.replayButton.title = 'Replay: click a bar in any chart to start; every chart shows what had happened by then';
    this.replayButton.addEventListener('click', () => this.toggleReplay());
    this.bar.append(this.replayButton, el('span', 'note', 'Each chart keeps its own type and indicators.'));
  }

  /** Arrange the cells the layout shows, and stop the ones it does not (they keep their symbol and interval for when it grows again). */
  private layout(): void {
    const l = LAYOUTS[this.state.layout];
    this.board.style.gridTemplateColumns = l.columns;
    this.board.style.gridTemplateRows = l.rows;
    this.board.style.gridTemplateAreas = l.areas;
    this.cells.forEach((c, i) => {
      c.element.style.gridArea = `c${i}`;
      if (i >= l.cells) c.hide();
      else if (this.visible && c.element.hidden) {
        // a chart the layout has just brought back joins the others' symbol when they are kept in step; the ones already shown are left alone
        if (this.state.sync.symbol && i > 0) this.state.cells[i].symbol = this.cells[0].currentSymbol || this.state.cells[i].symbol;
        this.start(c, i);
      }
    });
    this.sync.setCells(this.cells.slice(0, l.cells));
  }

  private start(view: ChartLabView, i: number): void {
    const base = this.ctx.bus.symbolOf('blue') || this.ctx.watchlist.items[0] || '';
    view.show(this.state.cells[i].symbol || this.cells[0].currentSymbol || base);
    if (this.moment !== null) view.followReplay(this.moment); // a chart brought in during a replay joins it
  }

  private shownCells(): ChartLabView[] {
    return this.cells.slice(0, LAYOUTS[this.state.layout].cells);
  }

  private paintReplay(): void {
    this.replayButton.classList.toggle('on', this.moment !== null || this.shownCells().some((c) => c.replayActive));
  }

  /** Start a replay (the next click on a bar in any chart is its start) or leave the one that is on. */
  private toggleReplay(): void {
    const cells = this.shownCells();
    if (this.moment !== null || cells.some((c) => c.replayActive)) {
      this.moment = null;
      for (const c of this.cells) c.stopReplay();
    } else for (const c of cells) c.pickReplayStart();
    this.paintReplay();
  }

  show(symbol?: string): void {
    this.visible = true;
    this.element.hidden = false;
    this.cells.slice(0, LAYOUTS[this.state.layout].cells).forEach((c, i) => {
      if (symbol && !this.state.cells[i].symbol) this.state.cells[i].symbol = symbol; // a first visit shows the symbol the other pages show
      this.start(c, i);
    });
    this.save();
  }

  hide(): void {
    this.visible = false;
    this.element.hidden = true;
    for (const c of this.cells) c.hide();
  }

  dispose(): void {
    for (const c of this.cells) c.dispose();
  }
}
