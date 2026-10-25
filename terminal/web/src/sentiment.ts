import { el } from './panels/base';
import { readSentiment, sentimentProblem, toneClass, visibleNews, type SentimentDoc } from './sentimentlogic';

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
    const rows = visibleNews(d.news.items, this.onlyUsed.checked);
    if (rows.length === 0) out.push(text('div', 'note', 'Nothing in this window.'));
    const table = el('table', 'stable sent-news');
    for (const r of rows) {
      const tr = el('tr');
      tr.dataset.source = r.source;
      tr.append(el('td', '', r.date), el('td', '', r.source === 'nse' ? 'filing' : 'press'), el('td', '', r.publisher));
      const title = el('td', 'sent-title');
      if (r.link) {
        const a = el('a', '', r.title);
        a.href = r.link;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        title.append(a);
      } else title.textContent = r.title;
      tr.append(title, el('td', 'note', r.routine ? 'routine' : r.used ? '' : 'not about this company'));
      table.append(tr);
    }
    if (rows.length) out.push(table);

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
