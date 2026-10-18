import { price } from '../format';
import { el } from '../panels/base';
import { yearsToExpiry } from './black76';
import type { Pick } from './chainview';
import { breakEvens, extremes, netDebit, netGreeks, payoffAtExpiry, payoffNow, type Extreme, type Leg, type Market } from './payoff';
import { lineChart } from './svgchart';

/** The payoff builder: legs picked from the chain (or a future added by hand), the profit and loss at expiry and now, break-evens, the most it can make and lose, and
 *  the net Greeks. The legs share one expiry (the one they were picked from); the page says so when another is picked. */

export interface BuilderContext {
  underlying: string;
  expiry: string;
  forward: number | null;
  lotSize: number | null;
  /** The at-the-money volatility, for a leg that has none of its own. */
  atmIv: number | null;
  rate: number;
  now: () => number;
}

export interface BuilderLeg extends Leg {
  id: number;
  lots: number;
  lotSize: number;
  iv: number | null;
}

const rupees = (n: number): string => (n < 0 ? '-' : '') + price(Math.abs(n));
const extreme = (e: Extreme): string => (e === 'unlimited' ? 'unlimited' : rupees(e));

export class PayoffBuilder {
  readonly element = el('div', 'opt-payoff');
  private legs: BuilderLeg[] = [];
  private expiry: string | null = null;
  private underlying: string | null = null;
  private counter = 0;
  private volShift = 0;
  private daysForward = 0;
  private message = el('div', 'note opt-message');
  private body = el('div', 'opt-payoff-body');

  constructor(private context: () => BuilderContext) {
    this.element.append(el('h3', 'opt-h', 'Payoff builder'), this.message, this.body);
    this.render();
  }

  get all(): readonly BuilderLeg[] {
    return this.legs;
  }

  private say(text: string): void {
    this.message.textContent = text;
  }

  /** Add the contract picked in the chain. Refused, with the reason, when it is for another expiry or underlying than the legs already there. */
  addPick(pick: Pick): void {
    const c = this.context();
    if (this.legs.length && (this.expiry !== c.expiry || this.underlying !== c.underlying)) {
      this.say(`The legs share one expiry (${this.expiry}, ${this.underlying}); clear them to build another position.`);
      return;
    }
    this.expiry = c.expiry;
    this.underlying = c.underlying;
    this.say('');
    this.legs.push({ id: ++this.counter, kind: pick.kind, side: pick.side, qty: pick.lot, lots: 1, lotSize: pick.lot, strike: pick.strike, price: pick.price, iv: pick.iv });
    this.render();
  }

  /** Add a future of this expiry at the forward (or at a price typed afterwards). */
  addFuture(side: 1 | -1): void {
    const c = this.context();
    if (c.forward === null || !c.lotSize) return this.say('There is no price for the future yet.');
    if (this.legs.length && (this.expiry !== c.expiry || this.underlying !== c.underlying)) {
      this.say(`The legs share one expiry (${this.expiry}, ${this.underlying}); clear them to build another position.`);
      return;
    }
    this.expiry = c.expiry;
    this.underlying = c.underlying;
    this.say('');
    this.legs.push({ id: ++this.counter, kind: 'future', side, qty: c.lotSize, lots: 1, lotSize: c.lotSize, strike: 0, price: Math.round(c.forward * 20) / 20, iv: null });
    this.render();
  }

  clear(): void {
    this.legs = [];
    this.expiry = null;
    this.underlying = null;
    this.daysForward = 0;
    this.volShift = 0;
    this.say('');
    this.render();
  }

  /** Called when the underlying changes: the position belongs to the one it was built on. */
  underlyingChanged(underlying: string): void {
    if (this.underlying !== null && this.underlying !== underlying) this.clear();
  }

  private market(): Market {
    const c = this.context();
    const T = yearsToExpiry(this.expiry ?? c.expiry, c.now());
    return { years: this.legs.map(() => T), vols: this.legs.map((l) => l.iv ?? c.atmIv ?? 0.2), rate: c.rate };
  }

  /** The legs as the maths takes them (units = lots × lot size). */
  private maths(): Leg[] {
    return this.legs.map((l) => ({ kind: l.kind, side: l.side, qty: l.lots * l.lotSize, strike: l.strike, price: l.price }));
  }

  render(): void {
    this.body.replaceChildren();
    if (!this.legs.length) {
      this.body.append(el('div', 'note', 'Click a price in the chain to add that contract (shift-click to sell it), or add a future.'), this.futureButtons());
      return;
    }
    const table = el('table', 'opt-legs');
    for (const leg of this.legs) {
      const tr = el('tr');
      tr.dataset.leg = String(leg.id);
      const side = el('button', `btn lab-mini opt-side ${leg.side === 1 ? 'buy' : 'sell'}`, leg.side === 1 ? 'Buy' : 'Sell');
      side.type = 'button';
      side.setAttribute('aria-label', `flip ${leg.kind} ${leg.strike}`);
      side.addEventListener('click', () => {
        leg.side = leg.side === 1 ? -1 : 1;
        this.render();
      });
      const name = el('td', 'opt-legname', leg.kind === 'future' ? 'Future' : `${leg.strike} ${leg.kind === 'call' ? 'CE' : 'PE'}`);
      const lots = el('input', 'cmd opt-lots');
      lots.type = 'number';
      lots.min = '1';
      lots.max = '1000';
      lots.value = String(leg.lots);
      lots.setAttribute('aria-label', `lots ${leg.kind} ${leg.strike}`);
      lots.addEventListener('change', () => {
        const n = Math.round(Number(lots.value));
        leg.lots = Number.isFinite(n) ? Math.min(1000, Math.max(1, n)) : 1;
        this.render();
      });
      const px = el('input', 'cmd opt-price');
      px.type = 'number';
      px.step = '0.05';
      px.min = '0';
      px.value = String(leg.price);
      px.setAttribute('aria-label', `price ${leg.kind} ${leg.strike}`);
      px.addEventListener('change', () => {
        const n = Number(px.value);
        if (Number.isFinite(n) && n >= 0) leg.price = n;
        this.render();
      });
      const remove = el('button', 'btn lab-mini', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', `remove ${leg.kind} ${leg.strike}`);
      remove.addEventListener('click', () => {
        this.legs = this.legs.filter((l) => l.id !== leg.id);
        if (!this.legs.length) this.clear();
        else this.render();
      });
      const cell = (n: Node): HTMLTableCellElement => {
        const td = el('td');
        td.append(n);
        return td;
      };
      tr.append(cell(side), name, cell(lots), el('td', 'note', `× ${leg.lotSize}`), cell(px), cell(remove));
      table.append(tr);
    }
    this.body.append(table, this.futureButtons(), this.summary());
  }

  private futureButtons(): HTMLElement {
    const row = el('div', 'opt-futbuttons');
    for (const [label, side] of [['+ Future (buy)', 1], ['+ Future (sell)', -1]] as const) {
      const b = el('button', 'btn lab-mini', label);
      b.type = 'button';
      b.setAttribute('aria-label', side === 1 ? 'add future buy' : 'add future sell');
      b.addEventListener('click', () => this.addFuture(side));
      row.append(b);
    }
    const clear = el('button', 'btn lab-mini', 'Clear');
    clear.type = 'button';
    clear.setAttribute('aria-label', 'clear legs');
    clear.addEventListener('click', () => this.clear());
    row.append(clear);
    return row;
  }

  private summary(): HTMLElement {
    const box = el('div', 'opt-summary');
    const legs = this.maths();
    const c = this.context();
    const market = this.market();
    const debit = netDebit(legs);
    const bes = breakEvens(legs);
    const ex = extremes(legs);
    const line = (label: string, value: string, cls = ''): HTMLElement => {
      const row = el('div', `opt-line-row ${cls}`);
      row.dataset.key = label;
      row.append(el('span', '', label), el('span', 'v', value));
      return row;
    };
    box.append(
      line('Net premium', debit >= 0 ? `${rupees(debit)} paid` : `${rupees(-debit)} received`),
      line('Max profit', extreme(ex.maxProfit)),
      line('Max loss', extreme(ex.maxLoss)),
      line('Break-even', bes.length ? bes.map((b) => price(b).replace(/\.00$/, '')).join(' · ') : 'none'),
    );
    const F = c.forward ?? (bes[0] ?? legs.find((l) => l.kind !== 'future')?.strike ?? 100);
    const g = netGreeks(legs, F, market, this.daysForward);
    box.append(line('Net Δ', g.delta.toFixed(2)), line('Net Γ', g.gamma.toFixed(5)), line('Net Θ / day', rupees(g.theta)), line('Net vega / pt', rupees(g.vega)));
    box.append(this.controls(market.years[0] ?? 0), this.chart(legs, F, market, bes));
    box.append(el('div', 'note', 'Margin is not estimated: there is no free exact source for it. The "now" line uses each contract\'s implied volatility (the at-the-money one when it has none).'));
    return box;
  }

  private controls(years: number): HTMLElement {
    const row = el('div', 'opt-controls');
    const vol = el('input', 'opt-range');
    vol.type = 'range';
    vol.min = '-10';
    vol.max = '10';
    vol.step = '1';
    vol.value = String(this.volShift * 100);
    vol.setAttribute('aria-label', 'volatility shift');
    const volText = el('span', 'note', `volatility ${this.volShift >= 0 ? '+' : ''}${(this.volShift * 100).toFixed(0)} pt`);
    vol.addEventListener('input', () => {
      this.volShift = Number(vol.value) / 100;
      this.render();
    });
    const maxDays = Math.max(0, Math.floor(years * 365));
    const days = el('input', 'opt-range');
    days.type = 'range';
    days.min = '0';
    days.max = String(maxDays);
    days.step = '1';
    days.value = String(Math.min(this.daysForward, maxDays));
    days.setAttribute('aria-label', 'days forward');
    const daysText = el('span', 'note', `${this.daysForward} days forward`);
    days.addEventListener('input', () => {
      this.daysForward = Number(days.value);
      this.render();
    });
    row.append(vol, volText, days, daysText);
    return row;
  }

  private chart(legs: Leg[], F: number, market: Market, bes: number[]): SVGSVGElement {
    const strikes = legs.filter((l) => l.kind !== 'future').map((l) => l.strike);
    const lo = Math.min(F * 0.9, ...strikes.map((s) => s * 0.95), ...bes.map((b) => b * 0.97));
    const hi = Math.max(F * 1.1, ...strikes.map((s) => s * 1.05), ...bes.map((b) => b * 1.03));
    const n = 90;
    const pts = Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1));
    return lineChart({
      label: 'Profit and loss of the position at expiry and now',
      lines: [
        { cls: 'expiry', points: pts.map((s) => ({ x: s, y: payoffAtExpiry(legs, s) })) },
        { cls: 'now', points: pts.map((s) => ({ x: s, y: payoffNow(legs, s, market, this.daysForward, this.volShift) })) },
      ],
      marks: [{ x: F, label: 'F', cls: 'forward' }, ...bes.map((b) => ({ x: b, label: 'BE', cls: 'be' }))],
      zero: true,
      x: (v) => price(v).replace(/\.00$/, ''),
      y: (v) => (Math.abs(v) >= 1e5 ? `${(v / 1e5).toFixed(1)}L` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toFixed(0)),
    });
  }
}
