import { compact, price } from '../format';
import { el } from '../panels/base';
import type { RowView, SideView } from './assemble';
import type { Quote } from './chain';

/** The option chain as a table: calls on the left, the strike in the middle, puts on the right, with open interest bars and optional Greeks. A click on a price picks
 *  that contract (a shift-click picks it as a sale). */

export interface Pick {
  kind: 'call' | 'put';
  strike: number;
  /** The price to enter at: the ask when buying and the bid when selling, else the last trade. */
  price: number;
  side: 1 | -1;
  lot: number;
  /** The implied volatility of the contract, to price the position before expiry. */
  iv: number | null;
}

export interface ChainOptions {
  greeks: boolean;
  onPick(pick: Pick): void;
}

const num = (n: number | null | undefined, digits = 2): string => (n === null || n === undefined || !Number.isFinite(n) ? '–' : n.toFixed(digits));
const money = (n: number | null | undefined): string => (n === null || n === undefined || !(n > 0) ? '–' : price(n));
const vol = (n: number | null): string => (n === null ? '–' : `${(n * 100).toFixed(1)}`);

const SIDE_COLUMNS = ['oi', 'oichg', 'vol', 'iv'] as const;
const GREEK_COLUMNS = ['delta', 'gamma', 'vega', 'theta'] as const;
const LABELS: Record<string, string> = { oi: 'OI', oichg: 'ΔOI', vol: 'Vol', iv: 'IV%', delta: 'Δ', gamma: 'Γ', vega: 'Vega', theta: 'Θ', ltp: 'LTP', bid: 'Bid', ask: 'Ask' };
const TITLES: Record<string, string> = {
  oi: 'Open interest, in contracts', oichg: 'Change in open interest since the previous close', vol: 'Volume traded today, in contracts', iv: 'Implied volatility in percent, from the middle of the bid and ask (else the last trade)',
  delta: 'Delta: change in price for a move of 1 in the forward', gamma: 'Gamma: change in delta for a move of 1', vega: 'Vega: change in price for one volatility point', theta: 'Theta: price lost to one calendar day', ltp: 'Last traded price', bid: 'Best bid', ask: 'Best ask',
};

function head(text: string, key: string): HTMLTableCellElement {
  const th = el('th', `c-${key}`, text);
  th.title = TITLES[key] ?? '';
  return th;
}

function sideCells(side: SideView | null, kind: 'call' | 'put', strike: number, maxOi: number, o: ChainOptions): HTMLTableCellElement[] {
  const q: Quote | null = side?.quote ?? null;
  const cells: HTMLTableCellElement[] = [];
  const cell = (key: string, text: string, extra = ''): HTMLTableCellElement => {
    const td = el('td', `c-${key} ${kind}${extra}`, text);
    td.dataset.side = kind;
    td.dataset.col = key;
    return td;
  };
  const oi = cell('oi', q ? compact(q.oi) : '–');
  if (q && maxOi > 0 && q.oi > 0) {
    const bar = el('span', `opt-oibar ${kind}`);
    bar.style.width = `${Math.max(2, (q.oi / maxOi) * 100).toFixed(1)}%`;
    oi.prepend(bar);
  }
  cells.push(oi);
  cells.push(cell('oichg', q ? (q.oiChange > 0 ? '+' : '') + compact(q.oiChange) : '–', q && q.oiChange < 0 ? ' dn' : q && q.oiChange > 0 ? ' up' : ''));
  cells.push(cell('vol', q ? compact(q.volume) : '–'));
  const iv = cell('iv', vol(side?.iv ?? null));
  if (side?.ivNote) iv.title = side.ivNote;
  cells.push(iv);
  if (o.greeks) {
    const g = side?.greeks ?? null;
    cells.push(cell('delta', num(g?.delta ?? null, 3)), cell('gamma', num(g?.gamma ?? null, 5)), cell('vega', num(g?.vega ?? null, 2)), cell('theta', num(g?.theta ?? null, 2)));
  }
  const priceCell = (key: 'ltp' | 'bid' | 'ask', value: number | null): HTMLTableCellElement => {
    const td = cell(key, money(value), side && value && value > 0 ? ' pick' : '');
    if (side && value && value > 0) {
      td.dataset.price = String(value);
      td.title = 'Click to buy this contract; shift-click to sell it';
      td.addEventListener('click', (e) => {
        const buy = !e.shiftKey;
        const px = buy ? (q?.ask ?? q?.last ?? value) : (q?.bid ?? q?.last ?? value);
        o.onPick({ kind, strike, price: px && px > 0 ? px : value, side: buy ? 1 : -1, lot: side.ref.lot, iv: side.iv });
      });
    }
    return td;
  };
  cells.push(priceCell('ltp', q?.last ?? null), priceCell('bid', q?.bid ?? null), priceCell('ask', q?.ask ?? null));
  return cells;
}

/** The chain table for these rows (already limited to the strikes to show). */
export function renderChain(rows: RowView[], o: ChainOptions): HTMLTableElement {
  const table = el('table', 'opt-chain');
  const maxCall = Math.max(0, ...rows.map((r) => r.call?.quote?.oi ?? 0));
  const maxPut = Math.max(0, ...rows.map((r) => r.put?.quote?.oi ?? 0));
  const cols = [...SIDE_COLUMNS, ...(o.greeks ? GREEK_COLUMNS : []), 'ltp', 'bid', 'ask'];
  const thead = el('thead');
  const group = el('tr', 'opt-group');
  const calls = el('th', 'opt-callhead', 'CALLS');
  calls.colSpan = cols.length;
  const mid = el('th', 'opt-strikehead', '');
  const puts = el('th', 'opt-puthead', 'PUTS');
  puts.colSpan = cols.length;
  group.append(calls, mid, puts);
  const labels = el('tr');
  labels.append(...cols.map((c) => head(LABELS[c], c)), head('Strike', 'strike'), ...[...cols].reverse().map((c) => head(LABELS[c], c)));
  thead.append(group, labels);
  const body = el('tbody');
  for (const r of rows) {
    const tr = el('tr', `opt-row${r.atm ? ' atm' : ''}`);
    tr.dataset.strike = String(r.strike);
    const left = sideCells(r.call, 'call', r.strike, maxCall, o);
    const right = sideCells(r.put, 'put', r.strike, maxPut, o).reverse();
    left.forEach((td) => r.itmCall && td.classList.add('itm'));
    right.forEach((td) => r.itmPut && td.classList.add('itm'));
    const strike = el('td', 'c-strike', price(r.strike).replace(/\.00$/, ''));
    strike.dataset.col = 'strike';
    tr.append(...left, strike, ...right);
    body.append(tr);
  }
  table.append(thead, body);
  return table;
}
