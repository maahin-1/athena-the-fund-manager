import type { Quote } from '../feed';
import { age, compact, pct, price, signed } from '../format';
import { el, Panel, type AppContext } from './base';

/** Big last price and change, then the day's range, volume, circuit band and 52-week range. */
export class QuotePanel extends Panel {
  private big = el('div', 'big');
  private sub = el('div', 'sub');
  private facts = el('div', 'facts');
  private source = el('div', 'note');
  private timer: ReturnType<typeof setInterval> | null = null;
  private last: Quote | null = null;

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.start();
  }

  protected build(): void {
    this.body.append(this.big, this.sub, this.facts, this.source);
    this.timer = setInterval(() => this.paintSource(), 1000);
    this.draw(null);
  }

  protected symbolChanged(symbol: string): void {
    this.setTitle(`Quote · ${symbol}`);
    this.ctx.feed.need(this.id, [symbol]);
    this.draw(this.ctx.feed.quotes.get(symbol) ?? null);
  }

  protected onQuotes(quotes: Quote[]): void {
    const q = quotes.find((x) => x.s === this.symbol);
    if (q) this.draw(q);
  }

  private draw(q: Quote | null): void {
    this.last = q;
    this.facts.replaceChildren();
    if (!q) {
      this.big.textContent = this.symbol || '—';
      this.sub.textContent = this.symbol ? 'waiting for a quote…' : 'pick a symbol';
      this.source.textContent = '';
      return;
    }
    const change = q.l - q.pc;
    const dir = change > 0 ? 'up' : change < 0 ? 'dn' : 'flat';
    this.big.textContent = price(q.l);
    this.big.className = `big ${dir}`;
    this.sub.textContent = `${signed(change)}  ${pct(q.pc ? (change / q.pc) * 100 : 0)}`;
    this.sub.className = `sub ${dir}`;
    const fact = (k: string, v: string) => {
      this.facts.append(el('span', 'k', k), el('span', 'v', v));
    };
    const opt = (n: number | null) => (n === null ? '—' : price(n));
    fact('Open', opt(q.o));
    fact('High', opt(q.h));
    fact('Low', opt(q.lo));
    fact('Prev close', price(q.pc));
    fact('Volume', q.v === null ? '—' : compact(q.v));
    fact('Circuit', q.lb !== null && q.ub !== null ? `${price(q.lb)} – ${price(q.ub)}` : '—');
    fact('52 week', q.l52 !== null && q.h52 !== null ? `${price(q.l52)} – ${price(q.h52)}` : '—');
    this.paintSource();
  }

  private paintSource(): void {
    const q = this.last;
    if (!q) return;
    const when = q.t ? age(Date.now() - q.t) + ' old' : '';
    this.source.textContent = (q.src === 'stream' ? 'live stream' : 'polled from the exchange (delayed)') + (when ? ` · exchange time ${when}` : '');
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    super.dispose();
  }
}
