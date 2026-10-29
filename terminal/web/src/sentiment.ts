import { el } from './panels/base';
import { citation, insiderReachNote, outcomeClass, outcomeLabel, readSentiment, rupees, sentimentProblem, statusClass, toneClass, trustLabel, visibleNews, type ClaimView, type SentimentDoc } from './sentimentlogic';

export interface SentimentHost {
  /** The symbol the user chose here, for the other pages to follow. */
  onSymbol(symbol: string): void;
}

const text = (tag: 'span' | 'div', cls: string, value: string): HTMLElement => el(tag, cls, value);

/** The Sentiment page: what the exchange and the press reported about one stock in the last 30 days, the Reddit posts that name it (when the owner has set Reddit up) and, on
 *  request, a model's reading of the news. Display only: nothing here feeds a verdict, and the page says that nothing about tone has been tested against returns. */
export class SentimentView {
  readonly element = el('div', 'sentiment');
  private readonly bar = el('div', 'sent-bar');
  private readonly input = el('input', 'cmd sent-symbol');
  private readonly toneButton = el('button', 'btn', 'Read the tone');
  private readonly onlyUsed = el('input');
  private readonly status = el('div', 'note');
  private readonly body = el('div', 'sent-body');
  private symbol = '';
  private doc: SentimentDoc | null = null;
  private token = 0;
  /** A source chosen from the Sources list: only its rows are shown. */
  private sourceId = '';

  constructor(private host: SentimentHost, private fetcher: typeof fetch = (...a) => fetch(...a)) {
    this.element.hidden = true;
    this.input.placeholder = 'symbol';
    this.input.setAttribute('aria-label', 'sentiment symbol');
    this.input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') this.choose(this.input.value);
    });
    this.toneButton.type = 'button';
    this.toneButton.title = 'Ask the news analyst for a reading of the news (one model call); the posts are never sent to it';
    this.toneButton.addEventListener('click', () => void this.load(this.symbol, true));
    this.onlyUsed.type = 'checkbox';
    this.onlyUsed.setAttribute('aria-label', 'only the news an analyst would use');
    this.onlyUsed.addEventListener('change', () => this.paint());
    const filter = el('label', 'sclean');
    filter.append(this.onlyUsed, document.createTextNode(' only what an analyst would use'));
    this.bar.append(this.input, this.toneButton, filter);
    this.element.append(this.bar, this.status, this.body);
  }

  show(symbol?: string): void {
    this.element.hidden = false;
    const next = (symbol ?? this.symbol).toUpperCase();
    if (!next) {
      this.status.textContent = 'Type a symbol to see what was reported and posted about it.';
      return;
    }
    if (next !== this.symbol || !this.doc) void this.load(next, false);
  }

  hide(): void {
    this.element.hidden = true;
  }

  private choose(raw: string): void {
    const symbol = raw.trim().toUpperCase();
    if (!symbol) return;
    this.host.onSymbol(symbol);
    void this.load(symbol, false);
  }

  /** Fetch the page for a symbol, with the model's reading when `tone`. A newer request replaces an older one still on its way. */
  async load(symbol: string, tone: boolean): Promise<void> {
    if (!symbol) return;
    const mine = ++this.token;
    this.symbol = symbol;
    this.input.value = symbol;
    this.status.textContent = tone ? `Reading the news about ${symbol} (one model call)…` : `Looking for what was reported about ${symbol}…`;
    this.toneButton.disabled = true;
    try {
      const r = await this.fetcher(`/api/sentiment/${encodeURIComponent(symbol)}${tone ? '?tone=1' : ''}`);
      if (mine !== this.token) return;
      if (!r.ok) {
        this.status.textContent = sentimentProblem(r.status);
        return;
      }
      const doc = readSentiment(await r.json());
      if (mine !== this.token) return;
      if (!doc) {
        this.status.textContent = sentimentProblem(502);
        return;
      }
      if (tone || !this.doc || this.doc.symbol !== doc.symbol) this.doc = doc;
      else this.doc = { ...doc, tone: this.doc.tone };
      this.status.textContent = '';
      this.paint();
    } catch {
      if (mine === this.token) this.status.textContent = sentimentProblem(502);
    } finally {
      if (mine === this.token) this.toneButton.disabled = false;
    }
  }

  /** Every source as a chip: its tier, whether it is working, and how many rows it gave for this stock. A chip filters the list to that source; an off or failing one says why. */
  private sourcesBox(d: SentimentDoc): HTMLElement {
    const box = el('div', 'sent-sources');
    box.append(text('div', 'note', 'Sources read for this page. Tier A: the exchange or a regulator; B: an established publisher; C: anything else. A tier is a starting estimate, not a measured record.'));
    for (const s of d.sources) {
      const chip = el('button', `chip ${statusClass(s.status)}${this.sourceId === s.id ? ' chosen' : ''}`, `${s.name} · ${s.tier} · ${s.rowsHere}`);
      chip.type = 'button';
      chip.title = `${s.status}${s.reason ? `: ${s.reason}` : ''}${s.held ? `; holds ${s.held} items` : ''}${s.newest ? `; newest ${s.newest.slice(0, 16).replace('T', ' ')} UTC` : ''}${s.trust ? `; ${trustLabel(s.trust)}` : ''}`;
      chip.disabled = s.rowsHere === 0 && this.sourceId !== s.id;
      chip.addEventListener('click', () => {
        this.sourceId = this.sourceId === s.id ? '' : s.id;
        this.paint();
      });
      box.append(chip);
    }
    const trouble = d.sources.filter((s) => s.status !== 'ok' && s.reason);
    for (const s of trouble) box.append(text('div', 'note', `${s.name} (${s.id}): ${s.status}. ${s.reason}`));
    return box;
  }

  private claimLine(c: ClaimView): HTMLElement {
    const line = el('div', 'sent-claim');
    const chip = el('span', `chip ${outcomeClass(c.outcome)}`, outcomeLabel(c.outcome));
    chip.title = c.detail;
    const said = c.link ? el('a', '', c.text) : el('span', '', c.text);
    if (c.link && said instanceof HTMLAnchorElement) {
      said.href = c.link;
      said.target = '_blank';
      said.rel = 'noopener noreferrer';
    }
    line.append(chip, el('span', 'note', ` ${c.publisher}, tier ${c.tier}, ${c.published.slice(0, 10)}: `), said);
    if (c.detail) line.append(el('div', 'note', `Our data: ${c.detail}`));
    return line;
  }

  /** Checked claims: what the headlines said that our own data can be asked about, and what it said. Contradicted claims stay visible; a majority never settles a fact. */
  private claimsBox(d: SentimentDoc): HTMLElement[] {
    const c = d.claims;
    const total = Object.values(c.counts).reduce((a, b) => a + b, 0);
    const out: HTMLElement[] = [text('div', 'sent-section', 'Checked claims (headline statements compared with our own data)')];
    if (total === 0 && c.unconfirmed.length === 0 && c.market.length === 0) {
      out.push(text('div', 'note', 'No headline in this window made a statement that can be checked against prices, filed figures or the exchange filings.'));
      return out;
    }
    const k = c.counts;
    out.push(text('div', 'note', `${k.confirmed ?? 0} confirmed, ${k.partly ?? 0} partly right, ${k.contradicted ?? 0} contradicted, ${k.pending ?? 0} waiting for data, ${k.unverified ?? 0} not confirmed, ${k.not_checkable ?? 0} cannot be checked.`));
    if (c.conflicts.length) {
      out.push(text('div', 'note', 'Where our data disagrees with a headline (kept on screen, and counted against its source):'));
      for (const r of c.conflicts) out.push(this.claimLine(r));
    }
    for (const dis of c.disagreements) {
      out.push(text('div', 'note', `Opposite reports on ${dis.day} (${dis.kind.replace('_', ' ')}); our data decides, not the count:`));
      for (const s of dis.sides) out.push(this.claimLine({ kind: dis.kind, text: s.text, outcome: s.outcome, detail: s.detail, publisher: s.publisher, sourceId: '', tier: s.tier, published: dis.day, link: s.link }));
    }
    if (c.confirmed.length) {
      out.push(text('div', 'note', 'Confirmed by our data:'));
      for (const r of c.confirmed.slice(0, 8)) out.push(this.claimLine(r));
    }
    if (c.unconfirmed.length) {
      out.push(text('div', 'note', 'Reports of talks or plans, which cannot be checked here:'));
      for (const u of c.unconfirmed) out.push(text('div', 'sent-claim', `${u.text} (${u.note})`));
    }
    if (c.market.length) {
      out.push(text('div', 'note', 'Market-wide statements (institutional flows):'));
      for (const r of c.market) out.push(this.claimLine(r));
    }
    if (c.method) out.push(text('div', 'note', c.method));
    return out;
  }

  /** Flows and insiders: the exchange's own rows, with the times the exchange stamped them. Display only. */
  private filingsBox(d: SentimentDoc): HTMLElement[] {
    const f = d.filings;
    const out: HTMLElement[] = [text('div', 'sent-section', 'Flows and insiders (NSE filings, tier A)')];
    const flows = el('table', 'stable sent-filings');
    for (const r of f.flows) flows.append(this.row([r.day, `FII/FPI net ${r.fii === null ? '-' : r.fii.toLocaleString('en-IN')} cr`, `DII net ${r.dii === null ? '-' : r.dii.toLocaleString('en-IN')} cr`]));
    if (f.flows.length) {
      out.push(text('div', 'note', 'Market-wide institutional flows by day, rupees crore; positive is net buying.'), flows);
    }
    out.push(text('div', 'note', insiderReachNote(f.insiderReach, d.asOf)));
    const c = f.insiderCounts;
    if (f.insiders.length) out.push(text('div', 'note', `Promoter and promoter-group filings in 180 days: ${c.Buy ?? 0} buys, ${c.Sell ?? 0} sells, ${c.Pledge ?? 0} pledges, ${c['Pledge Revoke'] ?? 0} pledge releases.`));
    const insiders = el('table', 'stable sent-filings');
    for (const r of f.insiders) {
      const tr = this.row([r.filed.slice(0, 10), r.category, r.person, `${r.txn !== '-' ? r.txn : r.mode}${r.shares !== null ? ` ${r.shares.toLocaleString('en-IN')} sh` : ''}`, rupees(r.value), r.afterPct === null ? '' : `holds ${r.afterPct}%`]);
      if (r.link) tr.title = `Filing: ${r.link}`;
      insiders.append(tr);
    }
    if (f.insiders.length) out.push(insiders);
    if (f.board.length) {
      out.push(text('div', 'note', 'Board meetings notified to the exchange (30 days back, 60 ahead):'));
      const board = el('table', 'stable sent-filings');
      for (const b of f.board) board.append(this.row([b.date, b.purpose, b.description.slice(0, 160)]));
      out.push(board);
    }
    return out;
  }

  private row(cells: string[]): HTMLElement {
    const tr = el('tr');
    for (const c of cells) tr.append(el('td', '', c));
    return tr;
  }

  private paint(): void {
    const d = this.doc;
    if (!d) return;
    const out: HTMLElement[] = [];
    const c = d.news.counts;
    out.push(text('div', 'sent-head', `${d.symbol}${d.company && d.company !== d.symbol ? ` · ${d.company}` : ''} · last ${d.windowDays} days to ${d.asOf}`));
    out.push(text('div', 'note sent-counts', `${c.all} news items (${c.press} press, ${c.all - c.press} exchange filings); ${c.used} would be used by an analyst, ${c.routine} are routine filings. Reddit: ${d.social.enabled ? `${d.social.items.length} posts` : 'off'}.`));
    for (const n of d.news.notes) out.push(text('div', 'flagline', n));

    if (d.tone) {
      const box = el('div', 'sent-tone');
      box.append(el('span', `chip ${toneClass(d.tone.signal)}`, d.tone.signal), el('span', 'vconv', `confidence ${d.tone.confidence}`), el('div', 'note', d.tone.reasoning));
      box.append(el('div', 'note', `The news analyst's reading of the news only, from what is listed below. ${d.tone.coverage === 'insufficient' ? 'There was too little to read. ' : ''}Display only.`));
      out.push(box);
    }

    out.push(text('div', 'sent-section', 'News and exchange filings'));
    if (d.sources.length) out.push(this.sourcesBox(d));
    const rows = visibleNews(d.news.items, this.onlyUsed.checked, this.sourceId);
    if (this.sourceId) out.push(text('div', 'note', `Showing only ${this.sourceId}. Choose it again in the list above to show everything.`));
    if (rows.length === 0) out.push(text('div', 'note', 'Nothing in this window.'));
    const table = el('table', 'stable sent-news');
    for (const r of rows) {
      const tr = el('tr');
      tr.dataset.source = r.source;
      tr.append(el('td', '', r.date), el('td', '', r.source === 'nse' ? 'filing' : r.source === 'regulator' ? 'regulator' : 'press'), el('td', '', r.publisher), el('td', `tier tier-${r.tier}`, r.tier));
      tr.title = citation(r);
      const title = el('td', 'sent-title');
      if (r.link) {
        const a = el('a', '', r.title);
        a.href = r.link;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        title.append(a);
      } else title.textContent = r.title;
      tr.append(title, el('td', 'note', r.routine ? 'routine' : r.used ? '' : 'not about this company'));
      if (r.claim) {
        const tag = el('td', '');
        const chip = el('span', `chip ${outcomeClass(r.claim.outcome)}`, outcomeLabel(r.claim.outcome));
        chip.title = r.claim.detail;
        tag.append(chip);
        tr.append(tag);
      }
      table.append(tr);
    }
    if (rows.length) out.push(table);

    if (d.market.length) {
      out.push(text('div', 'sent-section', 'Market and policy (SEBI, RBI): about the market, not matched to this company'));
      const market = el('table', 'stable sent-news');
      for (const m of d.market) {
        const tr = el('tr');
        tr.append(el('td', '', m.date), el('td', '', m.publisher), el('td', `tier tier-${m.tier}`, m.tier));
        const title = el('td', 'sent-title');
        if (m.link) {
          const a = el('a', '', m.title);
          a.href = m.link;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          title.append(a);
        } else title.textContent = m.title;
        tr.append(title);
        market.append(tr);
      }
      out.push(market);
    }

    out.push(...this.claimsBox(d));
    out.push(...this.filingsBox(d));

    out.push(text('div', 'sent-section', 'Reddit'));
    for (const n of d.social.notes) out.push(text('div', 'note', n));
    if (d.social.enabled && d.social.items.length === 0 && d.social.notes.length === 0) out.push(text('div', 'note', 'No post naming this company in the chosen subreddits in the last month.'));
    const posts = el('div', 'sent-posts');
    for (const p of d.social.items) {
      const box = el('div', 'sent-post');
      const head = el('div', 'note', `${p.date} · r/${p.subreddit} · ${p.score} points · ${p.comments} comments`);
      const title = el('div', 'sent-title');
      if (p.link) {
        const a = el('a', '', p.title);
        a.href = p.link;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        title.append(a);
      } else title.textContent = p.title;
      box.append(head, title);
      if (p.snippet) box.append(el('div', 'note', p.snippet));
      posts.append(box);
    }
    out.push(posts);
    out.push(text('div', 'note sent-caveat', d.caveat));
    this.body.replaceChildren(...out);
  }
}
