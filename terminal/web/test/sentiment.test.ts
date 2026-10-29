// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { parse as parseCommand } from '../src/command';
import { SentimentView } from '../src/sentiment';
import { citation, insiderReachNote, readSentiment, rupees, safeLink, sentimentProblem, statusClass, toneClass, visibleNews } from '../src/sentimentlogic';

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

describe('sources, tiers and citations', () => {
  const withEvidence = () =>
    doc({
      news: {
        items: [
          { date: '2026-10-22', published: '2026-10-22T06:30:00+00:00', first_seen: '2026-10-22T07:00:00+00:00', publisher: 'Mint', source: 'publisher feed', source_id: 'mint_markets', tier: 'B', title: 'Deal', link: 'https://x/1', routine: false, used: true },
          { date: '2026-10-22', publisher: 'Blog', source: 'publisher feed', source_id: 'blog', tier: 'Z', title: 'Odd tier', link: '', routine: false, used: true },
        ],
        notes: [], counts: { all: 2, used: 2, routine: 0, press: 2 },
      },
      market: [{ date: '2026-10-22', publisher: 'SEBI', source_id: 'sebi', tier: 'A', title: 'Order', link: 'javascript:1' }],
      sources: [
        { id: 'mint_markets', name: 'Mint', kind: 'press', tier: 'B', status: 'ok', reason: '', held: 35, newest: '2026-10-22T06:30:00', rows_here: 1 },
        { id: 'moneycontrol', name: 'Moneycontrol', kind: 'press', tier: 'B', status: 'off', reason: 'stopped updating on 2024-04-23', held: 0, newest: '', rows_here: 0 },
      ],
    });

  it('reads the citation fields, treats an unknown tier as the least trusted and drops an unsafe market link', () => {
    const got = readSentiment(withEvidence())!;
    expect(got.news.items[0]).toMatchObject({ sourceId: 'mint_markets', tier: 'B', published: '2026-10-22T06:30:00+00:00', firstSeen: '2026-10-22T07:00:00+00:00' });
    expect(got.news.items[1].tier).toBe('C');
    expect(got.market[0]).toMatchObject({ tier: 'A', link: '' });
    expect(got.sources[1]).toMatchObject({ status: 'off', reason: 'stopped updating on 2024-04-23' });
  });

  it('is still readable from a server that sends none of it', () => {
    const got = readSentiment(doc())!;
    expect(got.market).toEqual([]);
    expect(got.sources).toEqual([]);
    expect(got.news.items[0].tier).toBe('C');
  });

  it('filters rows to one source and describes a row by its citation', () => {
    const got = readSentiment(withEvidence())!;
    expect(visibleNews(got.news.items, false, 'mint_markets').map((r) => r.title)).toEqual(['Deal']);
    expect(visibleNews(got.news.items, false, '').length).toBe(2);
    expect(citation(got.news.items[0])).toContain('Mint, tier B (a starting estimate, not a measured record); published 2026-10-22 06:30 UTC; first seen 2026-10-22 07:00 UTC');
    expect(statusClass('ok')).toBe('up');
    expect(statusClass('off')).toBe('flat');
    expect(statusClass('failed')).toBe('dn');
    expect(statusClass('stale')).toBe('dn');
  });

  it('shows each source as a chip, says why one is off, and filters when a chip is chosen', async () => {
    const view = new SentimentView({ onSymbol() {} }, (async () => ({ ok: true, status: 200, json: async () => withEvidence() })) as unknown as typeof fetch);
    document.body.append(view.element);
    view.show('TCS');
    await new Promise((r) => setTimeout(r, 0));
    const chips = [...view.element.querySelectorAll('.sent-sources .chip')] as HTMLButtonElement[];
    expect(chips.map((c) => c.textContent)).toEqual(['Mint · B · 1', 'Moneycontrol · B · 0']);
    expect(chips[1].disabled).toBe(true);
    expect(view.element.textContent).toContain('stopped updating on 2024-04-23');
    expect(view.element.querySelectorAll('.sent-news tr').length).toBe(3); // two news rows and one market row
    chips[0].click();
    expect(view.element.querySelectorAll('.sent-news tr').length).toBe(2); // one news row and the market row
  });
});

describe('flows and insiders', () => {
  const filings = {
    insiders: [{ filed: '2026-04-06T10:37:00+00:00', person: 'A Promoter', category: 'Promoters', txn: 'Sell', mode: 'Market Sale', shares: 2320, value: 3294168, after_pct: 0.5, link: 'javascript:x' }, { person: 'Bad', shares: 'x', value: null }],
    insider_counts: { Buy: 2, Sell: 1 }, insider_reach: '2026-05-01T00:00:00+00:00',
    board: [{ date: '2026-11-05', purpose: 'Board Meeting Intimation', description: 'to consider results', announced: '2026-10-28T12:25:00+00:00', link: 'https://x.test/b' }],
    flows: [{ day: '2026-10-28', 'FII/FPI': -3500.5, DII: 643.35 }, { day: '2026-10-27', DII: 10 }],
  };

  it('is read defensively: bad numbers become missing, unsafe links are dropped, a missing block is empty', () => {
    const got = readSentiment(doc({ filings }))!.filings;
    expect(got.insiders[0]).toMatchObject({ person: 'A Promoter', shares: 2320, value: 3294168, afterPct: 0.5, link: '' });
    expect(got.insiders[1]).toMatchObject({ shares: null, value: null });
    expect(got.flows).toEqual([{ day: '2026-10-28', fii: -3500.5, dii: 643.35 }, { day: '2026-10-27', fii: null, dii: 10 }]);
    expect(got.insiderCounts).toEqual({ Buy: 2, Sell: 1 });
    expect(readSentiment(doc())!.filings).toEqual({ insiders: [], insiderCounts: {}, insiderReach: '', board: [], flows: [] });
  });

  it('says rupees in a reader\'s units and never shows a missing value as zero', () => {
    expect(rupees(3294168)).toBe('Rs 32.94 lakh');
    expect(rupees(-25_000_000)).toBe('-Rs 2.50 cr');
    expect(rupees(999)).toBe('Rs 999');
    expect(rupees(null)).toBe('-');
  });

  it('warns when the insider data stops long before the page, so an empty list is not read as a quiet company', () => {
    expect(insiderReachNote('', '2026-10-29')).toContain('No insider filings');
    expect(insiderReachNote('2026-10-20T00:00:00+00:00', '2026-10-29')).toBe('The newest insider filing held was filed 2026-10-20.');
    expect(insiderReachNote('2026-05-01T00:00:00+00:00', '2026-10-29')).toContain('does not mean nothing happened');
  });

  it('is drawn in the page with the flows, the insider counts, the reach warning and the board meetings', async () => {
    const fetcher = (async () => new Response(JSON.stringify(doc({ filings })), { status: 200 })) as unknown as typeof fetch;
    const view = new SentimentView({ onSymbol: () => {} }, fetcher);
    view.show('TCS');
    await new Promise((r) => setTimeout(r));
    const text = view.element.textContent ?? '';
    expect(text).toContain('Flows and insiders');
    expect(text).toContain('FII/FPI net -3,500.5 cr');
    expect(text).toContain('DII net 643.35 cr');
    expect(text).toContain('2 buys, 1 sells');
    expect(text).toContain('does not mean nothing happened');
    expect(text).toContain('Sell 2,320 sh');
    expect(text).toContain('Rs 32.94 lakh');
    expect(text).toContain('Board Meeting Intimation');
    expect(view.element.querySelectorAll('.sent-news tr').length).toBe(3); // the news table is unchanged
  });
});
