import type { Feed, Quote } from '../feed';
import type { PanelState, PanelType } from '../layout';
import { nextGroup, type Group, type LinkBus } from '../links';

/** The ordered list of symbols the watchlist shows; shared by every panel that needs it. */

import type { BarsCache } from '../details/data';
import type { IndexFeed } from '../indices';
import type { ScreenStore } from '../screenrows';
import type { WatchlistsStore } from '../watchlists/store';

export interface AppContext {
  feed: Feed;
  bus: LinkBus;
  watchlist: WatchlistsStore;
  indices: IndexFeed;
  market: ScreenStore; // the stored market screen: sector, market cap, P/E and more for every stock
  history: BarsCache; // daily bars, kept five minutes
  state(id: string): PanelState;
  changed(): void; // something worth saving changed
  openResearch(symbol: string): void; // switch to the Research page on this stock
  openFundamentals(symbol: string): void; // switch to the Fundamentals page on this stock
  showSymbol(symbol: string): void; // switch to the terminal and show this stock's chart
  retitle(id: string, text: string): void; // the panel's tab shows this
  linkChanged(id: string): void; // a panel's link colour changed
  isKnown(symbol: string): Promise<boolean>; // whether the market lists this stock, ETF or index
}

export const TITLES: Record<PanelType, string> = { watchlist: 'Watchlist', chart: 'Chart', depth: 'Depth', quote: 'Quote', tape: 'Tape', analysis: 'Analysis', verdict: 'Verdict', details: 'Details', notes: 'Notes' };

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = ''): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

/** A panel: a header with a link-colour dot, a body, a symbol that follows its link group, and quotes pushed in. */
export abstract class Panel {
  readonly element = el('div', 'panel');
  protected body = el('div', 'pb');
  protected titleEl = el('span', 'pt');
  private dot = el('button', 'dot');
  private shownTitle = '';
  private offLink: (() => void) | null = null;
  private offQuotes: (() => void) | null = null;
  private own: PanelState;
  protected disposed = false;

  constructor(
    protected id: string,
    protected ctx: AppContext,
  ) {
    this.own = ctx.state(id); // kept, so a panel that outlives its entry in the layout (a closing panel) still has a state
    const head = el('div', 'ph');
    this.dot.title = 'Link colour: panels with the same colour follow one symbol (click to change)';
    this.dot.addEventListener('click', () => this.setGroup(nextGroup(this.state.group)));
    head.append(this.titleEl, this.dot);
    this.element.append(head, this.body);
  }

  /** The text on the panel's tab. */
  protected setTitle(text: string): void {
    this.shownTitle = text;
    this.titleEl.textContent = text;
    this.ctx.retitle(this.id, text);
  }

  get title(): string {
    return this.shownTitle;
  }

  get linkGroup(): Group {
    return this.state.group;
  }

  /** The next link colour (called by the colour dot on the group's tab bar). */
  cycleGroup(): void {
    this.setGroup(nextGroup(this.state.group));
  }

  protected get state(): PanelState {
    return this.own;
  }

  protected get symbol(): string {
    return this.state.symbol ?? '';
  }

  /** Call once, after construction, from the subclass's constructor end. */
  protected start(): void {
    this.paintDot();
    this.listenToGroup();
    const shared = this.ctx.bus.symbolOf(this.state.group);
    if (shared && this.wantsLinkedSymbol()) this.state.symbol = shared;
    this.build();
    if (this.symbol) this.symbolChanged(this.symbol);
    this.offQuotes = this.ctx.feed.onQuotes((qs) => this.onQuotes(qs));
  }

  protected wantsLinkedSymbol(): boolean {
    return true;
  }

  /** Put the body together. */
  protected abstract build(): void;
  /** The panel's symbol changed (also called once at the start when there is one). */
  protected symbolChanged(_symbol: string): void {}
  protected onQuotes(_quotes: Quote[]): void {}

  setSymbol(symbol: string): void {
    this.state.symbol = symbol;
    this.symbolChanged(symbol);
    this.ctx.changed();
  }

  /** The user picked a symbol here: move this panel and everything linked to it. */
  protected pick(symbol: string): void {
    this.setSymbol(symbol);
    this.ctx.bus.setSymbol(this.state.group, symbol);
  }

  protected setGroup(group: Group): void {
    this.state.group = group;
    this.paintDot();
    this.listenToGroup();
    // joining a colour means following the symbol that colour already shows
    const shared = this.ctx.bus.symbolOf(group);
    if (shared && this.wantsLinkedSymbol() && shared !== this.symbol) this.setSymbol(shared);
    this.ctx.linkChanged(this.id);
    this.ctx.changed();
  }

  private listenToGroup(): void {
    this.offLink?.();
    this.offLink = this.ctx.bus.subscribe(this.state.group, (symbol) => {
      if (this.wantsLinkedSymbol() && symbol !== this.symbol) this.setSymbol(symbol);
    });
  }

  private paintDot(): void {
    this.dot.dataset.group = this.state.group;
    this.dot.setAttribute('aria-label', `link colour ${this.state.group}`);
  }

  dispose(): void {
    this.disposed = true;
    this.offLink?.();
    this.offQuotes?.();
    this.ctx.feed.release(this.id);
  }
}
