import { compact, price } from '../format';
import { el, type AppContext } from '../panels/base';
import { ApiError, serverOptionsApi, type ChainStructure, type ExpiryInfo, type FutureInfo, type OptionsApi, type UnderlyingInfo } from './api';
import { around, assemble, chainKeys, type ChainView } from './assemble';
import { DEFAULT_RATE, yearsToExpiry } from './black76';
import { renderChain } from './chainview';
import type { Quote } from './chain';
import { PayoffBuilder } from './payoffview';
import { lineChart } from './svgchart';
import { liveFeed, type LiveFeed } from './livefeed';
import { keyOf, syntheticFeed, type OptionFeed } from './synthetic';

const STORE_KEY = 'athena.options';
const TERM_EXPIRIES = 6;
const AROUND = [10, 20, 40, 'all'] as const;
/** While prices are live the chain asks for fresh ones this often (the server's stream may send several ticks a second; the page needs no more than this). */
const REFRESH_MS = 2000;
/** After asking for the contracts of the term curve, the live stream is given this long to answer before they are read. */
const SUBSCRIBE_WAIT_MS = 2500;
/** The names an index has in the exchange's all-indices snapshot, for the spot of its options. */
const INDEX_NAMES: Record<string, string[]> = {
  NIFTY: ['NIFTY 50'],
  BANKNIFTY: ['NIFTY BANK'],
  FINNIFTY: ['NIFTY FINANCIAL SERVICES', 'NIFTY FIN SERVICE'],
  MIDCPNIFTY: ['NIFTY MIDCAP SELECT', 'NIFTY MID SELECT'],
};

export interface OptionsOptions {
  api?: OptionsApi;
  /** A feed to use as given (tests); without one the page uses the live feed when the server has a stream, else the synthetic one. */
  feed?: OptionFeed;
  /** The live feed, or null for none (tests); by default one reading the server. */
  live?: LiveFeed | null;
  storage?: { getItem(k: string): string | null; setItem(k: string, v: string): void } | null;
  now?: () => number;
}

interface Saved {
  underlying?: string;
  around?: number | 'all';
  greeks?: boolean;
  rate?: number;
}

function browserStorage(): OptionsOptions['storage'] {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** A rate as the percent text for the box (7, not 7.000000000000001). */
const percent = (rate: number): string => String(Number((rate * 100).toFixed(4)));

const dateLabel = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00Z`);
  return `${String(d.getUTCDate()).padStart(2, '0')} ${d.toLocaleString('en-GB', { month: 'short', timeZone: 'UTC' })} ${d.getUTCFullYear()}`;
};

/** The Options tab: the chain of an underlying and expiry with implied volatility, Greeks, open interest, put-call ratio and max pain, a volatility smile and term
 *  structure, the futures with their basis, and a payoff builder. Prices come through an `OptionFeed`; until live derivative quotes are connected that is the
 *  synthetic one, and the page says so. */
export class OptionsView {
  readonly element = el('div', 'opt-page');
  private api: OptionsApi;
  private feed: OptionFeed;
  private synthetic: OptionFeed;
  private live: LiveFeed | null;
  private ctx: AppContext;
  private badge = el('span', 'opt-synth');
  private smileBox = el('div', 'opt-smile');
  private termBox = el('div', 'opt-term');
  private futureList: FutureInfo[] = [];
  private refresher: ReturnType<typeof setInterval> | null = null;
  private refreshing = false;
  private storage: OptionsOptions['storage'];
  private now: () => number;
  private toolbar = el('div', 'opt-bar');
  private underlyingInput = el('input', 'cmd opt-underlying');
  private datalist = el('datalist');
  private expirySelect = el('select', 'lab-select opt-expiry');
  private aroundSelect = el('select', 'lab-select opt-around');
  private greeksBox = el('input');
  private rateInput = el('input', 'cmd opt-rate');
  private stats = el('div', 'opt-stats');
  private note = el('div', 'note opt-note');
  private chainBox = el('div', 'opt-chainbox');
  private curves = el('div', 'opt-curves');
  private futuresBox = el('div', 'opt-futures');
  private builder: PayoffBuilder;
  private underlyings: UnderlyingInfo[] = [];
  private expiries: ExpiryInfo[] = [];
  private underlying = '';
  private expiry = '';
  private view: ChainView | null = null;
  private structure: ChainStructure | null = null;
  private token = 0;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private visible = false;
  private settings: Required<Saved> = { underlying: 'NIFTY', around: 20, greeks: false, rate: DEFAULT_RATE };

  constructor(ctx: AppContext, options: OptionsOptions = {}) {
    this.ctx = ctx;
    this.element.hidden = true;
    this.api = options.api ?? serverOptionsApi();
    this.synthetic = options.feed ?? syntheticFeed();
    this.feed = this.synthetic;
    this.storage = options.storage === undefined ? browserStorage() : options.storage;
    this.now = options.now ?? (() => Date.now() / 1000);
    this.live = options.feed ? null : options.live === undefined ? liveFeed({ spotOf: (u) => this.spotOf(u) }) : options.live;
    this.load();
    this.builder = new PayoffBuilder(() => ({
      underlying: this.underlying,
      expiry: this.expiry,
      forward: this.view?.forward ?? null,
      lotSize: this.view?.lot ?? null,
      atmIv: this.view?.atmIv ?? null,
      rate: this.settings.rate,
      now: this.now,
    }));
    this.buildToolbar();
    const side = el('div', 'opt-side-col');
    this.curves.append(el('h3', 'opt-h', 'Volatility smile'), this.smileBox, el('h3', 'opt-h', 'ATM volatility by expiry'), this.termBox);
    side.append(this.curves, this.builder.element);
    const main = el('div', 'opt-main');
    main.append(this.chainBox, side);
    this.element.append(this.toolbar, this.stats, this.note, main, this.futuresBox);
  }

  private load(): void {
    try {
      const saved = JSON.parse(this.storage?.getItem(STORE_KEY) ?? '{}') as Saved;
      if (typeof saved.underlying === 'string' && /^[A-Z0-9&_-]{1,24}$/.test(saved.underlying)) this.settings.underlying = saved.underlying;
      if (saved.around === 'all' || AROUND.includes(saved.around as 10)) this.settings.around = saved.around as number | 'all';
      if (typeof saved.greeks === 'boolean') this.settings.greeks = saved.greeks;
      if (typeof saved.rate === 'number' && saved.rate >= 0 && saved.rate <= 0.3) this.settings.rate = saved.rate;
    } catch {
      /* nothing kept, or not readable: the defaults */
    }
  }

  private save(): void {
    try {
      this.storage?.setItem(STORE_KEY, JSON.stringify({ ...this.settings, underlying: this.underlying || this.settings.underlying }));
    } catch {
      /* the page works without it */
    }
  }

  private buildToolbar(): void {
    this.underlyingInput.placeholder = 'underlying, e.g. NIFTY';
    this.underlyingInput.setAttribute('aria-label', 'underlying');
    this.underlyingInput.setAttribute('list', 'opt-underlyings');
    this.datalist.id = 'opt-underlyings';
    this.underlyingInput.addEventListener('keydown', (e) => e.key === 'Enter' && void this.choose(this.underlyingInput.value));
    this.underlyingInput.addEventListener('change', () => void this.choose(this.underlyingInput.value));
    this.expirySelect.setAttribute('aria-label', 'expiry');
    this.expirySelect.addEventListener('change', () => {
      this.expiry = this.expirySelect.value;
      void this.loadChain();
    });
    this.aroundSelect.setAttribute('aria-label', 'strikes shown');
    for (const a of AROUND) this.aroundSelect.append(new Option(a === 'all' ? 'All strikes' : `${a} strikes each side`, String(a)));
    this.aroundSelect.value = String(this.settings.around);
    this.aroundSelect.addEventListener('change', () => {
      this.settings.around = this.aroundSelect.value === 'all' ? 'all' : Number(this.aroundSelect.value);
      this.save();
      this.paintChain();
    });
    const greeksLabel = el('label', 'grid-switch');
    this.greeksBox.type = 'checkbox';
    this.greeksBox.checked = this.settings.greeks;
    this.greeksBox.setAttribute('aria-label', 'show Greeks');
    this.greeksBox.addEventListener('change', () => {
      this.settings.greeks = this.greeksBox.checked;
      this.save();
      this.paintChain();
    });
    greeksLabel.append(this.greeksBox, document.createTextNode(' Greeks'));
    this.rateInput.type = 'number';
    this.rateInput.step = '0.25';
    this.rateInput.min = '0';
    this.rateInput.max = '30';
    this.rateInput.value = percent(this.settings.rate);
    this.rateInput.setAttribute('aria-label', 'interest rate');
    this.rateInput.title = 'Interest rate in percent, used to bring spot to the forward and to discount';
    this.rateInput.addEventListener('change', () => {
      const r = Number(this.rateInput.value) / 100;
      if (Number.isFinite(r) && r >= 0 && r <= 0.3) this.settings.rate = r;
      this.rateInput.value = percent(this.settings.rate);
      this.save();
      void this.loadChain();
    });
    const refresh = el('button', 'btn', 'Refresh');
    refresh.type = 'button';
    refresh.addEventListener('click', () => void this.loadChain());
    this.updateBadge();
    this.toolbar.append(this.underlyingInput, this.datalist, this.expirySelect, this.aroundSelect, greeksLabel, el('span', 'note', 'Rate %'), this.rateInput, refresh, this.badge);
  }

  /** What the prices are, said beside them: made up, live, or the last ones the exchange sent. */
  private updateBadge(): void {
    const b = this.feed.badge?.() ?? (this.feed.synthetic ? { text: 'synthetic prices', title: 'These prices are made up for a test market; the server has no live derivative stream (the fake feed is on, or the broker is not connected).' } : { text: 'live prices', title: '' });
    this.badge.textContent = b.text;
    this.badge.title = b.title;
    this.badge.dataset.kind = this.feed.synthetic ? 'synthetic' : b.text.startsWith('live') ? 'live' : 'last';
  }

  /** The price of the underlying itself, for a live feed: an index from the exchange's snapshot, a share from the cash stream; none for a commodity. */
  private spotOf(underlying: string): number | null {
    const info = this.underlyings.find((u) => u.underlying === underlying);
    if (info?.class === 'index') {
      for (const name of INDEX_NAMES[underlying] ?? []) {
        const row = this.ctx.indices?.get(name);
        if (row) return row.l;
      }
      return null;
    }
    if (info?.class === 'stock') {
      this.ctx.feed?.need?.('options', [underlying]);
      return this.ctx.feed?.quotes?.get(underlying)?.l ?? null;
    }
    return null;
  }

  // ---- showing and loading ---------------------------------------------------------------------------------------------------------------

  show(underlying?: string): void {
    this.visible = true;
    this.element.hidden = false;
    void this.start(underlying);
  }

  hide(): void {
    this.visible = false;
    this.element.hidden = true;
    if (this.retry) clearTimeout(this.retry);
    this.retry = null;
    if (this.refresher) clearInterval(this.refresher);
    this.refresher = null;
    this.ctx.feed?.release?.('options');
    this.token++;
  }

  private async start(underlying?: string): Promise<void> {
    if (this.live) {
      this.feed = (await this.live.probe()) ? this.live : this.synthetic;
      this.updateBadge();
    }
    if (!this.underlyings.length) {
      try {
        this.underlyings = await this.api.underlyings();
      } catch (e) {
        return this.failed(e, () => void this.start(underlying));
      }
      this.datalist.replaceChildren(...this.underlyings.filter((u) => !u.odd && u.options).map((u) => new Option(`${u.underlying} · ${u.class}`, u.underlying)));
    }
    const wanted = (underlying ?? '').toUpperCase();
    const pick = this.underlyings.find((u) => u.underlying === wanted && u.options) ?? this.underlyings.find((u) => u.underlying === (this.underlying || this.settings.underlying) && u.options) ?? this.underlyings.find((u) => u.options && !u.odd);
    if (!pick) {
      this.note.textContent = 'No option contracts are listed.';
      return;
    }
    await this.choose(pick.underlying);
  }

  private failed(e: unknown, again: () => void): void {
    const why = e instanceof ApiError ? e.message : 'something went wrong';
    this.note.textContent = `${why}.${e instanceof ApiError && e.status === 503 ? ' Trying again in a few seconds.' : ''}`;
    if (e instanceof ApiError && e.status === 503 && this.visible) this.retry = setTimeout(again, 3000);
  }

  /** Choose an underlying: its expiries, then the chain of the first. An underlying that is not listed says so and leaves the page as it was. */
  private async choose(text: string): Promise<void> {
    const name = text.trim().toUpperCase();
    const info = this.underlyings.find((u) => u.underlying === name);
    if (!info || !info.options) {
      this.note.textContent = name ? `${name} has no option contracts in the list.` : '';
      return;
    }
    const mine = ++this.token;
    this.note.textContent = 'loading…';
    let expiries: ExpiryInfo[];
    try {
      expiries = (await this.api.expiries(name)).filter((e) => e.options);
    } catch (e) {
      return this.failed(e, () => void this.choose(name));
    }
    if (mine !== this.token) return;
    if (name !== this.underlying) this.builder.underlyingChanged(name);
    this.underlying = name;
    this.underlyingInput.value = name;
    this.expiries = expiries;
    this.expirySelect.replaceChildren(...expiries.map((e) => new Option(`${dateLabel(e.date)} · ${e.series}`, e.date)));
    if (!expiries.some((e) => e.date === this.expiry) || name !== this.settings.underlying) this.expiry = expiries[0]?.date ?? '';
    this.expirySelect.value = this.expiry;
    this.settings.underlying = name;
    this.save();
    void this.loadFutures(name);
    await this.loadChain();
  }

  private async loadChain(): Promise<void> {
    if (!this.underlying || !this.expiry) return;
    const mine = ++this.token;
    const [underlying, expiry] = [this.underlying, this.expiry];
    this.note.textContent = 'loading…';
    try {
      const structure = await this.api.chain(underlying, expiry);
      const [quotes, spot] = await Promise.all([this.feed.quotes(chainKeys(structure)), this.feed.spot(underlying)]);
      if (mine !== this.token) return;
      this.structure = structure;
      this.view = assemble(structure, quotes, spot, this.now(), this.settings.rate);
      this.note.textContent = '';
      this.paintChain();
      this.paintCurves();
      this.builder.render();
      this.updateBadge();
      this.arm();
      void this.loadTerm(mine);
    } catch (e) {
      if (mine === this.token) this.failed(e, () => void this.loadChain());
    }
  }

  // ---- painting --------------------------------------------------------------------------------------------------------------------------

  /** Redraw the chain from the current view. `centre` scrolls the at-the-money row to the middle (not on a refresh, which keeps the place the reader is at). */
  private paintChain(centre = true): void {
    const v = this.view;
    if (!v) return;
    const scrolled = this.chainBox.scrollTop;
    const f = (n: number | null, d = 2): string => (n === null ? '–' : price(n).replace(/\.00$/, d === 0 ? '' : '.00'));
    this.stats.replaceChildren();
    const stat = (label: string, value: string, title = ''): void => {
      const s = el('span', 'opt-stat');
      s.dataset.stat = label;
      s.title = title;
      s.append(el('span', 'k', label), el('span', 'v', value));
      this.stats.append(s);
    };
    stat('Spot', v.spot === null ? '–' : price(v.spot));
    stat('Forward', v.forward === null ? '–' : price(v.forward), v.forwardFrom === 'future' ? 'From the future of this expiry' : v.forwardFrom === 'parity' ? 'Implied by the call and put prices at the money (put-call parity)' : 'Spot grown at the interest rate to this expiry');
    stat('ATM', f(v.atm, 0));
    stat('ATM IV', v.atmIv === null ? '–' : `${(v.atmIv * 100).toFixed(1)}%`);
    stat('PCR (OI)', v.pcr.oi === null ? '–' : v.pcr.oi.toFixed(2), 'Put open interest over call open interest');
    stat('PCR (vol)', v.pcr.volume === null ? '–' : v.pcr.volume.toFixed(2));
    stat('Max pain', f(v.maxPain, 0), 'The strike where the writers of all the options would pay out least at expiry');
    stat('Days', (v.T * 365).toFixed(1));
    stat('Lot', v.lot === null ? '–' : String(v.lot));
    const shown = around(v.rows, this.settings.around);
    this.chainBox.replaceChildren(renderChain(shown, { greeks: this.settings.greeks, onPick: (p) => this.builder.addPick(p) }));
    const at = this.chainBox.querySelector('tr.atm');
    if (centre) (at as HTMLElement | null)?.scrollIntoView?.({ block: 'center' });
    else this.chainBox.scrollTop = scrolled;
  }

  private paintCurves(): void {
    const v = this.view;
    this.smileBox.replaceChildren();
    if (!v) return;
    this.smileBox.append(
      lineChart({
        label: 'Implied volatility by strike',
        lines: [{ cls: 'smile', points: v.smile.map((p) => ({ x: p.strike, y: p.iv * 100 })) }],
        marks: v.forward === null ? [] : [{ x: v.forward, label: 'F', cls: 'forward' }],
        x: (n) => price(n).replace(/\.00$/, ''),
        y: (n) => `${n.toFixed(1)}%`,
      }),
    );
  }

  /** The at-the-money volatility of the next few expiries: each needs its own chain (only the strikes next to the money are priced). With a live feed the contracts
   *  are asked for first, so the server subscribes them, and read a moment later when the stream has answered. */
  private async loadTerm(mine: number): Promise<void> {
    const spot = this.view?.spot ?? null;
    const T = (date: string): number => yearsToExpiry(date, this.now());
    const near: { date: string; chain: ChainStructure }[] = [];
    for (const e of this.expiries.slice(0, TERM_EXPIRIES)) {
      try {
        const s = e.date === this.expiry && this.structure ? this.structure : await this.api.chain(this.underlying, e.date);
        const guess = spot === null ? null : spot * Math.exp(this.settings.rate * T(e.date));
        const strikes = guess === null ? s.strikes.slice(0, 3) : [...s.strikes].sort((a, b) => Math.abs(a.strike - guess) - Math.abs(b.strike - guess)).slice(0, 3);
        near.push({ date: e.date, chain: { ...s, strikes: strikes.sort((a, b) => a.strike - b.strike) } });
      } catch {
        /* an expiry that cannot be read is left out of the curve */
      }
      if (mine !== this.token) return;
    }
    if (!this.feed.synthetic) {
      try {
        await this.feed.quotes(near.flatMap((n) => chainKeys(n.chain)));
      } catch {
        /* the second read below will say the same */
      }
      await new Promise((r) => setTimeout(r, SUBSCRIBE_WAIT_MS));
      if (mine !== this.token) return;
    }
    const points: { x: number; y: number }[] = [];
    for (const n of near) {
      try {
        const quotes = await this.feed.quotes(chainKeys(n.chain));
        const v = assemble(n.chain, quotes, spot, this.now(), this.settings.rate);
        if (v.atmIv !== null) points.push({ x: T(n.date) * 365, y: v.atmIv * 100 });
      } catch {
        /* an expiry that cannot be read is left out of the curve */
      }
      if (mine !== this.token) return;
    }
    this.termBox.replaceChildren(lineChart({ label: 'At-the-money implied volatility by expiry', lines: [{ cls: 'term', points }], x: (n) => `${n.toFixed(0)}d`, y: (n) => `${n.toFixed(1)}%` }));
  }

  // ---- live refresh ----------------------------------------------------------------------------------------------------------------------

  /** With a live feed, keep asking for fresh prices while the page is open. */
  private arm(): void {
    if (this.feed.synthetic || !this.visible || this.refresher) return;
    this.refresher = setInterval(() => void this.refreshQuotes(), REFRESH_MS);
  }

  private async refreshQuotes(): Promise<void> {
    const s = this.structure;
    if (!s || !this.visible || this.refreshing || this.feed.synthetic) return;
    this.refreshing = true;
    const mine = this.token;
    try {
      const [quotes, spot] = await Promise.all([this.feed.quotes(chainKeys(s)), this.feed.spot(s.underlying)]);
      if (mine !== this.token) return;
      this.view = assemble(s, quotes, spot, this.now(), this.settings.rate);
      this.paintChain(false);
      this.paintCurves();
      this.updateBadge();
      void this.paintFutures();
    } catch {
      this.updateBadge(); // the stream may have dropped: the chain keeps the last prices and the badge says so; the next refresh tries again
    } finally {
      this.refreshing = false;
    }
  }

  private async loadFutures(name: string): Promise<void> {
    try {
      this.futureList = await this.api.futures(name);
    } catch {
      this.futureList = [];
      return void this.futuresBox.replaceChildren();
    }
    await this.paintFutures();
  }

  private async paintFutures(): Promise<void> {
    const [name, list] = [this.underlying, this.futureList];
    let spot: number | null;
    let quotes: Map<string, Quote>;
    try {
      spot = await this.feed.spot(name);
      quotes = await this.feed.quotes(list.map((f) => ({ underlying: name, expiry: f.expiry, kind: 'future' as const, strike: 0, ref: { segment: f.segment, token: f.token, lot: f.lot } })));
    } catch {
      return; // no prices just now (the stream is down): the table stays as it was
    }
    if (name !== this.underlying || list !== this.futureList) return;
    this.futuresBox.replaceChildren(el('h3', 'opt-h', 'Futures'));
    if (!list.length) return void this.futuresBox.append(el('div', 'note', 'This underlying has no futures.'));
    const table = el('table', 'opt-futtable');
    const headRow = el('tr');
    for (const h of ['Expiry', 'Symbol', 'Lot', 'Price', 'OI', 'Basis', 'Annualised']) headRow.append(el('th', '', h));
    table.append(headRow);
    for (const f of list) {
      const q = quotes.get(keyOf({ underlying: name, expiry: f.expiry, kind: 'future', strike: 0 }));
      const days = Math.max(1, yearsToExpiry(f.expiry, this.now()) * 365);
      const basis = q?.last && spot ? q.last - spot : null;
      const annual = q?.last && spot ? ((q.last / spot - 1) * 365) / days : null;
      const tr = el('tr');
      tr.dataset.expiry = f.expiry;
      for (const text of [dateLabel(f.expiry), f.symbol, String(f.lot), q?.last ? price(q.last) : '–', q ? compact(q.oi) : '–', basis === null ? '–' : price(basis), annual === null ? '–' : `${(annual * 100).toFixed(1)}%`]) tr.append(el('td', '', text));
      table.append(tr);
    }
    this.futuresBox.append(table);
  }

  dispose(): void {
    this.hide();
  }
}
