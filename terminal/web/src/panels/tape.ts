import type { Quote } from '../feed';
import { pct, price } from '../format';
import { el, Panel, type AppContext } from './base';

/** A strip of the watchlist's moves, scrolling slowly from right to left. */
export class TapePanel extends Panel {
  private track = el('div', 'track');
  private cells = new Map<string, HTMLElement[]>();
  private offStore: (() => void) | null = null;

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.element.classList.add('tape');
    this.start();
  }

  protected wantsLinkedSymbol(): boolean {
    return false;
  }

  protected build(): void {
    this.titleEl.textContent = 'Tape';
    this.body.append(this.track);
    this.offStore = this.ctx.watchlist.subscribe(() => this.render());
    this.render();
  }

  private render(): void {
    this.track.replaceChildren();
    this.cells.clear();
    // every symbol appears twice so the scrolling loop has no gap
    for (let pass = 0; pass < 2; pass++) {
      for (const s of this.ctx.watchlist.items) {
        const cell = el('span', 'tcell', s);
        this.cells.set(s, [...(this.cells.get(s) ?? []), cell]);
        this.track.append(cell);
      }
    }
    this.ctx.feed.need(this.id, this.ctx.watchlist.items);
    for (const q of this.ctx.feed.quotes.values()) this.paint(q);
  }

  protected onQuotes(quotes: Quote[]): void {
    quotes.forEach((q) => this.paint(q));
  }

  private paint(q: Quote): void {
    const dir = q.l > q.pc ? 'up' : q.l < q.pc ? 'dn' : 'flat';
    const text = `${q.s} ${price(q.l)} ${q.l >= q.pc ? '▲' : '▼'} ${pct(q.pc ? ((q.l - q.pc) / q.pc) * 100 : 0)}`;
    for (const cell of this.cells.get(q.s) ?? []) {
      cell.textContent = text;
      cell.className = `tcell ${dir}`;
    }
  }

  dispose(): void {
    this.offStore?.();
    super.dispose();
  }
}
