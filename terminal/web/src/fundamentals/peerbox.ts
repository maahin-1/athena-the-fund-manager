import { el, type AppContext } from '../panels/base';
import { COMPARE, DEFAULT_PEERS, indicesOf, peersOf, standing, type Peers, type Standing } from '../peers';
import { COLUMNS, type Row } from '../screenlogic';
import { ColumnsMenu } from '../screens/controls';
import { formatCell } from '../screens/format';

/** The columns of the peer table until the reader picks others: the figures a reader compares first. */
export const PEER_COLUMNS = ['close', 'market_cap_cr', 'pe', 'pb', 'roe', 'net_margin', 'debt_to_equity', 'dividend_yield', 'revenue_growth', 'ret_1y'];
const COUNTS = [6, 12, 20, 30];

/** The Peers section of a company page: where the company sits (sector, industry, indices), a table of it beside its peers with their median, and what each
 *  figure says about it against them. It reads the stored market screen, so it shows for any stock the screen holds, with or without statements. */
export class PeerBox {
  readonly element = el('section', 'fcard wide fpeers');
  private symbol = '';
  private columns = [...PEER_COLUMNS];
  private limit = DEFAULT_PEERS;
  private off: (() => void) | null = null;
  private menu = new ColumnsMenu(
    () => this.columns,
    (columns) => {
      this.columns = columns;
      this.menu.sync();
      this.paint();
    },
  );

  constructor(private ctx: AppContext) {
    this.element.dataset.section = 'Peers';
    this.off = ctx.market.subscribe(() => this.paint());
  }

  show(symbol: string): void {
    this.symbol = symbol;
    this.paint();
  }

  dispose(): void {
    this.off?.();
  }

  private paint(): void {
    const head = el('h3', '', 'Peers');
    this.element.replaceChildren(head);
    if (!this.symbol) return;
    const rows = this.ctx.market.rows();
    if (!this.ctx.market.loaded) return void this.element.append(el('div', 'note', 'loading the market screen…'));
    const found = peersOf(rows, this.symbol, this.limit);
    if (!found) return void this.element.append(el('div', 'note', `No peers: ${this.symbol} is not a stock in the stored market screen.`));
    this.element.append(this.where(found));
    if (!found.peers.length) return void this.element.append(el('div', 'note', 'No other stock in the screen shares this sector, so there is nothing to compare with.'));
    this.element.append(this.controls(found), this.table(found), this.against(found));
  }

  private where(p: Peers): HTMLElement {
    const box = el('div', 'fwhere');
    const crumbs = [p.subject.sector, p.subject.industry].filter((c, i, all): c is string => typeof c === 'string' && c !== '' && all.findIndex((x) => String(x).toLowerCase() === c.toLowerCase()) === i);
    box.append(el('span', 'fcrumbs', crumbs.length ? crumbs.join(' › ') : 'sector not known'));
    const indices = indicesOf(p.subject);
    if (indices.length) {
      const chips = el('span', 'fchips');
      for (const name of indices) chips.append(el('span', 'fchip', name));
      box.append(chips);
    }
    const why =
      p.basis === 'industry'
        ? `Peers: the other stocks in the exchange's industry "${p.group}", nearest in market cap.`
        : p.basis === 'sector'
          ? `Peers: the other stocks in the sector "${p.group}", nearest in market cap (the exchange's industry is not known for every stock, or too few share it).`
          : 'No peers could be chosen.';
    box.append(el('div', 'note', why));
    return box;
  }

  private controls(p: Peers): HTMLElement {
    const row = el('div', 'fpeer-tools');
    const count = el('select', 'picker');
    count.setAttribute('aria-label', 'number of peers');
    for (const n of COUNTS) count.append(new Option(`${n} peers`, String(n)));
    count.value = String(COUNTS.includes(this.limit) ? this.limit : DEFAULT_PEERS);
    count.addEventListener('change', () => {
      this.limit = Number(count.value);
      this.paint();
    });
    this.menu.sync();
    row.append(count, this.menu.element, el('span', 'note', `${p.peers.length} shown`));
    return row;
  }

  private table(p: Peers): HTMLElement {
    const wrap = el('div', 'ftable-wrap');
    const table = el('table', 'ftable fpeer-table');
    const head = el('tr');
    head.append(el('th', '', 'Company'), ...this.columns.map((c) => Object.assign(el('th', 'num', COLUMNS[c]?.[0] ?? c), { title: c })));
    const thead = el('thead');
    thead.append(head);
    const body = el('tbody');
    const line = (row: Row, cls: string): HTMLElement => {
      const tr = el('tr', cls);
      const name = el('td', 'flabel');
      const symbol = String(row.symbol);
      if (cls === 'subject') name.textContent = symbol;
      else {
        const open = el('button', 'linklike', symbol);
        open.type = 'button';
        open.title = String(row.name ?? symbol);
        open.addEventListener('click', () => this.ctx.openFundamentals(symbol));
        name.append(open);
      }
      tr.append(name);
      for (const c of this.columns) {
        const [text, tone] = formatCell(row[c] ?? null, COLUMNS[c]?.[1] ?? 'number', c);
        tr.append(el('td', `num ${tone}`, text));
      }
      return tr;
    };
    body.append(line(p.subject, 'subject'));
    for (const r of p.peers) body.append(line(r, 'peer'));
    // a median share price says nothing, so the price column is left blank on that row
    body.append(line({ symbol: 'Median of peers', ...Object.fromEntries(this.columns.map((c) => [c, c === 'close' ? null : (p.medians[c] ?? null)])) }, 'median'));
    table.append(thead, body);
    wrap.append(table);
    return wrap;
  }

  /** Each comparable figure: the company's, the peers' median, where it ranks and which end is better. */
  private against(p: Peers): HTMLElement {
    const box = el('div', 'fagainst');
    box.append(el('div', 'fg-title', `${p.subject.symbol} against its peers`));
    const table = el('table', 'ftable fagainst-table');
    const head = el('tr');
    head.append(...['Figure', p.subject.symbol, 'Peer median', 'Rank', 'Better'].map((h, i) => el('th', i ? 'num' : '', String(h))));
    const thead = el('thead');
    thead.append(head);
    const body = el('tbody');
    for (const s of standing(p, COMPARE.map((c) => c.column))) {
      const tr = el('tr');
      const style = COLUMNS[s.column]?.[1] ?? 'number';
      const mine = formatCell(s.value, style, s.column);
      const theirs = formatCell(s.median, style, s.column);
      tr.append(el('td', 'flabel', COLUMNS[s.column]?.[0] ?? s.column), el('td', 'num', mine[0]), el('td', 'num', theirs[0]));
      tr.append(el('td', `num ${tone(s)}`, s.rank === null ? '—' : `${s.rank} of ${s.of}`), el('td', 'num note', s.better === 'high' ? 'higher' : s.better === 'low' ? 'lower' : 'no better end'));
      body.append(tr);
    }
    table.append(thead, body);
    box.append(table, el('div', 'note', 'Rank 1 is the best of the company and the peers that have the figure. A loss-making P/E is not ranked.'));
    return box;
  }
}

/** The top third of the peers is good, the bottom third is not. */
function tone(s: Standing): string {
  if (s.rank === null) return 'flat';
  const share = (s.rank - 1) / Math.max(1, s.of - 1);
  return share <= 1 / 3 ? 'up' : share >= 2 / 3 ? 'dn' : '';
}
