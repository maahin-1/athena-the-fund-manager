import { el } from './panels/base';
import { bandText, gainClass, gainText, ipoProblem, readIpo, sizeText, sourceClass, timesText, type FilingRow, type IpoDoc, type IssueRow, type ListedRow } from './ipologic';

export interface IpoHost {
  /** The user chose a listed stock: the other pages follow it. */
  onSymbol(symbol: string): void;
}

const text = (tag: 'span' | 'div', cls: string, value: string): HTMLElement => el(tag, cls, value);

function link(label: string, href: string, title = ''): HTMLElement {
  if (!href) return el('span', 'note', label);
  const a = el('a', '', label);
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  if (title) a.title = title;
  return a;
}

/** The IPO tab: issues open now, coming and waiting to list, those that listed recently with their gain against the issue price, and the drafts filed with SEBI, each with a link to the document.
 *  Facts from the exchange and SEBI; display only. */
export class IpoView {
  readonly element = el('div', 'ipo');
  private readonly bar = el('div', 'sent-bar');
  private readonly refresh = el('button', 'btn', 'Refresh');
  private readonly status = el('div', 'note');
  private readonly body = el('div', 'ipo-body');
  private doc: IpoDoc | null = null;
  private token = 0;

  constructor(private host: IpoHost, private fetcher: typeof fetch = (...a) => fetch(...a)) {
    this.element.hidden = true;
    this.refresh.type = 'button';
    this.refresh.title = 'Read the page again (the data itself is collected every two hours while the software is open)';
    this.refresh.addEventListener('click', () => void this.load());
    this.bar.append(el('b', 'rtitle', 'IPOs'), this.refresh);
    this.element.append(this.bar, this.status, this.body);
  }

  show(): void {
    this.element.hidden = false;
    if (!this.doc) void this.load();
  }

  hide(): void {
    this.element.hidden = true;
  }

  async load(): Promise<void> {
    const mine = ++this.token;
    this.status.textContent = 'Reading the issues…';
    try {
      const r = await this.fetcher('/api/ipo');
      if (mine !== this.token) return;
      if (!r.ok) {
        this.status.textContent = ipoProblem(r.status);
        return;
      }
      const doc = readIpo(await r.json());
      if (mine !== this.token) return;
      if (!doc) {
        this.status.textContent = ipoProblem(502);
        return;
      }
      this.doc = doc;
      this.status.textContent = '';
      this.paint();
    } catch {
      if (mine === this.token) this.status.textContent = ipoProblem(502);
    }
  }

  private filingLinks(filings: readonly FilingRow[]): HTMLElement {
    const cell = el('td', 'ipo-docs');
    if (!filings.length) cell.append(text('span', 'note', 'no draft found'));
    for (const f of filings) {
      const group = el('span', 'ipo-doc');
      group.append(link(`${f.kind} ${f.filed}`, f.page, f.title), text('span', 'note', ' '), f.pdf ? link('[document]', f.pdf, `${f.kind}: the PDF on SEBI's site`) : text('span', 'note', ''));
      cell.append(group, text('span', 'note', '  '));
    }
    return cell;
  }

  private issueTable(rows: readonly IssueRow[], showSubscription: boolean): HTMLElement {
    const table = el('table', 'stable ipo-table');
    const head = el('tr');
    for (const h of ['Company', 'Type', 'Opens', 'Closes', 'Price band', 'Size', 'Lot (min. application)', ...(showSubscription ? ['Subscribed'] : []), 'Offer documents']) head.append(el('th', '', h));
    table.append(head);
    for (const r of rows) {
      const tr = el('tr');
      const name = el('td', 'ipo-name');
      name.append(el('b', '', r.symbol), text('span', 'note', ` ${r.name}`));
      tr.append(name, el('td', '', r.kind === 'sme' ? 'SME' : 'Main board'), el('td', '', r.start), el('td', '', r.end), el('td', '', bandText(r.priceLow, r.priceHigh)), el('td', '', sizeText(r.sizeCr)),
        el('td', '', r.lot === null ? '-' : `${r.lot} (Rs ${r.minApplication?.toLocaleString('en-IN') ?? '-'})`));
      if (showSubscription) {
        const cell = el('td', 'ipo-sub');
        if (r.subscription) {
          cell.append(el('b', '', timesText(r.subscription.totalTimes)));
          const detail = el('details');
          detail.append(el('summary', 'note', 'by category'));
          for (const s of r.subscription.rows) detail.append(text('div', s.level ? 'note ipo-sub2' : 'note', `${s.category}: ${timesText(s.times)}`));
          detail.append(text('div', 'note', `as of ${r.subscription.takenAt.slice(0, 16).replace('T', ' ')} UTC; ${r.subscription.snapshots} snapshot${r.subscription.snapshots === 1 ? '' : 's'} kept`));
          cell.append(detail);
        } else cell.append(text('span', 'note', 'not yet'));
        tr.append(cell);
      }
      tr.append(this.filingLinks(r.filings));
      table.append(tr);
    }
    return table;
  }

  private listedTable(rows: readonly ListedRow[]): HTMLElement {
    const table = el('table', 'stable ipo-table');
    const head = el('tr');
    for (const h of ['Company', 'Type', 'Listed', 'Issue price', 'Listing price', 'Gain at listing', 'Gain at first close', 'Latest close', 'Gain now', 'Offer documents']) head.append(el('th', '', h));
    table.append(head);
    for (const r of rows) {
      const tr = el('tr');
      const name = el('td', 'ipo-name');
      const open = el('button', 'linkbtn', r.symbol);
      open.type = 'button';
      open.title = 'Show this stock on the other pages';
      open.addEventListener('click', () => this.host.onSymbol(r.symbol));
      name.append(open, text('span', 'note', ` ${r.name}`));
      const gain = (p: number | null) => el('td', gainClass(p), gainText(p));
      tr.append(name, el('td', '', r.kind === 'sme' ? 'SME' : 'Main board'), el('td', '', r.listing), el('td', '', r.issuePrice === null ? '-' : String(r.issuePrice)), el('td', '', r.listingOpen === null ? '-' : String(r.listingOpen)),
        gain(r.listingPct), gain(r.firstClosePct), el('td', '', r.lastClose === null ? '-' : String(r.lastClose)), gain(r.lastPct), this.filingLinks(r.filings));
      table.append(tr);
    }
    return table;
  }

  private filedTable(rows: readonly FilingRow[]): HTMLElement {
    const table = el('table', 'stable ipo-table');
    const head = el('tr');
    for (const h of ['Filed', 'Company', 'Document', 'Links']) head.append(el('th', '', h));
    table.append(head);
    for (const f of rows) {
      const tr = el('tr');
      const links = el('td');
      links.append(link('SEBI page', f.page, f.title), text('span', 'note', '  '), f.pdf ? link('[document]', f.pdf) : text('span', 'note', 'no document found yet'));
      tr.append(el('td', '', f.filed), el('td', '', f.company), el('td', '', f.kind), links);
      table.append(tr);
    }
    return table;
  }

  private paint(): void {
    const d = this.doc;
    if (!d) return;
    const out: HTMLElement[] = [];
    const chips = el('div', 'sent-sources');
    for (const s of d.sources) {
      const chip = el('span', `chip ${sourceClass(s.status)}`, `${s.name} · ${s.status}`);
      chip.title = s.reason || (s.lastOk ? `last worked ${s.lastOk.slice(0, 16).replace('T', ' ')} UTC` : '');
      chips.append(chip);
    }
    out.push(chips);
    const section = (title: string, count: number, empty: string, table: () => HTMLElement): void => {
      out.push(text('div', 'sent-section', `${title} (${count})`));
      out.push(count ? table() : text('div', 'note', empty));
    };
    section('Open now', d.open.length, 'No issue is open for bidding.', () => this.issueTable(d.open, true));
    section('Coming', d.upcoming.length, 'No issue is announced as coming.', () => this.issueTable(d.upcoming, false));
    if (d.closed.length) section('Closed, waiting to list', d.closed.length, '', () => this.issueTable(d.closed, true));
    section('Listed in the last 60 days', d.listed.length, 'Nothing listed in this window.', () => this.listedTable(d.listed));
    section('Draft offer documents filed with SEBI, not yet an open issue', d.filed.length, 'No draft in the last four months.', () => this.filedTable(d.filed));
    out.push(text('div', 'note', 'The size is the most an issue can raise (shares times the upper price). Gains are worked out from the issue price and the price bars we hold; a dash means we could not work it out. Documents link to the exchange and SEBI.'));
    out.push(text('div', 'note sent-caveat', d.caveat));
    this.body.replaceChildren(...out);
  }
}
