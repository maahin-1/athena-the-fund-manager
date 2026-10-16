import type { Quote } from '../feed';
import { compact, price } from '../format';
import { marketState } from '../market';
import { el, Panel, type AppContext } from './base';

/** Five bids and five asks with a bar for each size, and the last quote's age. */
export class DepthPanel extends Panel {
  private grid = el('div', 'depth');
  private note = el('div', 'note');

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.start();
  }

  protected build(): void {
    this.body.append(this.grid, this.note);
    this.draw(null);
  }

  protected symbolChanged(symbol: string): void {
    this.setTitle(`Depth · ${symbol}`);
    this.ctx.feed.need(this.id, [symbol]);
    this.draw(this.ctx.feed.quotes.get(symbol) ?? null);
  }

  protected onQuotes(quotes: Quote[]): void {
    const q = quotes.find((x) => x.s === this.symbol);
    if (q) this.draw(q);
  }

  private draw(q: Quote | null): void {
    this.grid.replaceChildren();
    if (!q) {
      this.note.textContent = this.symbol ? 'waiting for a quote…' : 'pick a symbol';
      return;
    }
    const widest = Math.max(1, ...q.b.map((x) => x[1]), ...q.a.map((x) => x[1]));
    const side = (rows: [number, number][], kind: 'bid' | 'ask') => {
      const col = el('div', `col ${kind}`);
      col.append(el('div', 'colh', kind === 'bid' ? 'Bid' : 'Ask'));
      for (const [p, qty] of rows) {
        const r = el('div', 'lvl');
        r.style.setProperty('--w', `${Math.round((qty / widest) * 100)}%`);
        r.append(el('span', 'p', price(p)), el('span', 'q', compact(qty)));
        col.append(r);
      }
      return col;
    };
    this.grid.append(side(q.b, 'bid'), side(q.a, 'ask'));
    this.note.textContent = q.b.length && q.a.length ? '' : marketState(new Date()).open ? 'no resting orders on one side right now' : 'The market is closed, so the order book is empty or one-sided.';
  }
}
