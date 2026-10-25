// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { parse as parseCommand } from '../src/command';
import { SentimentView } from '../src/sentiment';
import { readSentiment, safeLink, sentimentProblem, toneClass, visibleNews } from '../src/sentimentlogic';

const doc = (over: Record<string, unknown> = {}) => ({
  symbol: 'TCS', company: 'Tata Consultancy Services Limited', as_of: '2026-10-23', window_days: 30,
  news: {
    items: [
      { date: '2026-10-22', publisher: 'NSE', source: 'nse', title: 'Outcome of Board Meeting', link: 'https://example.test/a', routine: false, used: true },
      { date: '2026-10-21', publisher: 'NSE', source: 'nse', title: 'Trading window closure', link: 'javascript:alert(1)', routine: true, used: false },
      { date: '2026-10-20', publisher: 'Mint', source: 'publisher', title: 'Sector round-up', link: '', routine: false, used: false },
    ],
    notes: ['a feed was down'], counts: { all: 3, used: 1, routine: 1, press: 1 },
  },
  social: { enabled: true, notes: [], by_subreddit: { IndiaInvestments: 1 }, items: [{ date: '2026-10-22', subreddit: 'IndiaInvestments', title: 'TCS Q2 thread', snippet: 'a snippet', score: 80, comments: 12, link: 'https://www.reddit.com/r/x/1' }] },
  tone: null, caveat: 'Whether the tone of news or posts says anything about later returns has not been tested here.', ...over,
});

describe('the sentiment document', () => {
  it('is read with unsafe links dropped and counts defaulted, and refused when it is not one', () => {
    const got = readSentiment(doc())!;
    expect(got.symbol).toBe('TCS');
    expect(got.news.items[1].link).toBe(''); // a javascript: link is never kept
    expect(got.news.counts).toEqual({ all: 3, used: 1, routine: 1, press: 1 });
    expect(got.social.items[0]).toMatchObject({ subreddit: 'IndiaInvestments', score: 80, link: 'https://www.reddit.com/r/x/1' });
    expect(got.tone).toBeNull();
    expect(readSentiment({ ...doc(), tone: { signal: 'bullish', confidence: 40, reasoning: 'Several reports.', data_coverage: 'full' } })!.tone).toEqual({ signal: 'bullish', confidence: 40, reasoning: 'Several reports.', coverage: 'full' });
    expect(readSentiment(null)).toBeNull();
    expect(readSentiment({ symbol: 'X' })).toBeNull();
    expect(safeLink('http://a.test')).toBe('http://a.test');
    expect(safeLink('data:text/html,<b>')).toBe('');
    expect(safeLink(5)).toBe('');
  });

  it('shows all the news or only what an analyst would use, and names the tone and the problems', () => {
    const got = readSentiment(doc())!;
    expect(visibleNews(got.news.items, false)).toHaveLength(3);
    expect(visibleNews(got.news.items, true).map((r) => r.title)).toEqual(['Outcome of Board Meeting']);
    expect([toneClass('bullish'), toneClass('bearish'), toneClass('neutral')]).toEqual(['up', 'dn', 'flat']);
    expect(sentimentProblem(429)).toContain('already being built');
    expect(sentimentProblem(400)).toContain('not a symbol');
    expect(sentimentProblem(502)).toContain('could not be built');
  });
});

describe('the command', () => {
  const ctx = { layouts: [] as string[] };
  it('opens the page on a stock or on its own', () => {
    expect(parseCommand('SENTIMENT', ctx as never)).toEqual({ kind: 'sentiment', symbol: null });
    expect(parseCommand('sent', ctx as never)).toEqual({ kind: 'sentiment', symbol: null });
    expect(parseCommand('SENT tcs', ctx as never)).toEqual({ kind: 'sentiment', symbol: 'TCS' });
    expect(parseCommand('sentiment infy', ctx as never)).toEqual({ kind: 'sentiment', symbol: 'INFY' });
  });
});

describe('the Sentiment view', () => {
  const answer = (body: unknown, status = 200) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it('shows the news, the posts and the caveat, marks routine and unused items, and filters', async () => {
    const view = new SentimentView({ onSymbol: () => {} }, answer(doc()));
    view.show('tcs');
    await new Promise((r) => setTimeout(r));
    const text = view.element.textContent ?? '';
    expect(text).toContain('Tata Consultancy Services Limited');
    expect(text).toContain('3 news items (1 press, 2 exchange filings); 1 would be used by an analyst, 1 are routine filings. Reddit: 1 posts.');
    expect(text).toContain('a feed was down');
    expect(text).toContain('routine');
    expect(text).toContain('not about this company');
    expect(text).toContain('TCS Q2 thread');
    expect(text).toContain('has not been tested');
    expect(view.element.querySelectorAll('.sent-news tr').length).toBe(3);
    expect(view.element.querySelector('.sent-news a')?.getAttribute('rel')).toBe('noopener noreferrer');
    const box = view.element.querySelector('input[type="checkbox"]') as HTMLInputElement;
    box.checked = true;
    box.dispatchEvent(new Event('change')); // a click on an element outside the document does not fire it
    expect(view.element.querySelectorAll('.sent-news tr').length).toBe(1);
  });

  it('asks for the reading only when the button is pressed, and keeps it when the page is reloaded without one', async () => {
    const calls: string[] = [];
    const reading = { signal: 'bullish', confidence: 35, reasoning: 'Results and a large order.', data_coverage: 'full' };
    const fetcher = (async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify(doc({ symbol: url.includes('INFY') ? 'INFY' : 'TCS', ...(url.includes('tone=1') ? { tone: reading } : {}) })), { status: 200 });
    }) as unknown as typeof fetch;
    const view = new SentimentView({ onSymbol: () => {} }, fetcher);
    view.show('TCS');
    await new Promise((r) => setTimeout(r));
    expect(calls).toEqual(['/api/sentiment/TCS']);
    expect(view.element.querySelector('.sent-tone')).toBeNull();
    (view.element.querySelector('button.btn') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r));
    expect(calls[1]).toBe('/api/sentiment/TCS?tone=1');
    expect(view.element.querySelector('.sent-tone')?.textContent).toContain('Results and a large order.');
    expect(view.element.querySelector('.sent-tone .chip')?.classList.contains('up')).toBe(true);
    await view.load('TCS', false); // the same stock again: the reading is kept
    expect(view.element.querySelector('.sent-tone')).not.toBeNull();
    await view.load('INFY', false);
    expect(view.element.querySelector('.sent-tone')).toBeNull(); // another stock: no reading
  });

  it('says what went wrong, and a newer request wins over an older one', async () => {
    const down = new SentimentView({ onSymbol: () => {} }, answer('x', 502));
    down.show('TCS');
    await new Promise((r) => setTimeout(r));
    expect(down.element.textContent).toContain('could not be built');
    const garbled = new SentimentView({ onSymbol: () => {} }, answer({ nonsense: true }));
    garbled.show('TCS');
    await new Promise((r) => setTimeout(r));
    expect(garbled.element.textContent).toContain('could not be built');
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const slowThenFast = (async (url: string) => {
      if (url.endsWith('/SLOW')) await gate;
      return new Response(JSON.stringify(doc({ symbol: url.endsWith('/SLOW') ? 'SLOW' : 'FAST', company: '' })), { status: 200 });
    }) as unknown as typeof fetch;
    const view = new SentimentView({ onSymbol: () => {} }, slowThenFast);
    const first = view.load('SLOW', false);
    await view.load('FAST', false);
    release();
    await first;
    expect(view.element.textContent).toContain('FAST');
    expect(view.element.textContent).not.toContain('SLOW ·');
  });

  it('with no symbol says to type one, and choosing one tells the host', async () => {
    const chosen: string[] = [];
    const view = new SentimentView({ onSymbol: (s) => chosen.push(s) }, answer(doc()));
    view.show();
    expect(view.element.textContent).toContain('Type a symbol');
    const input = view.element.querySelector('input.sent-symbol') as HTMLInputElement;
    input.value = ' sbin ';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await new Promise((r) => setTimeout(r));
    expect(chosen).toEqual(['SBIN']);
  });
});
