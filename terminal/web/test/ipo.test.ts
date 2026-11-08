// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { parse as parseCommand } from '../src/command';
import { IpoView } from '../src/ipo';
import { bandText, gainClass, gainText, ipoProblem, readIpo, safeLink, sizeText, sourceClass, timesText } from '../src/ipologic';

const filing = { company: 'Oravel Stays Limited', kind: 'DRHP', filed: '2026-10-26', title: 'Oravel Stays Limited - DRHP', page: 'https://www.sebi.gov.in/filings/x.html', pdf: 'https://www.sebi.gov.in/sebi_data/x.pdf' };
const issue = (over: Record<string, unknown> = {}) => ({
  symbol: 'BCSSL', name: 'Bombay Coated and Special Steels Limited', kind: 'mainboard', status: 'Active', start: '2026-10-29', end: '2026-11-02', price_low: 78, price_high: 82, shares: 16533012, lot: null, size_cr: 135.6, min_application: null,
  subscription: { taken_at: '2026-11-03T09:00:00+00:00', snapshots: 2, total_times: 1.1, rows: [{ category: 'Qualified Institutional Buyers(QIBs)', offered: 1, bid: 2, times: 2.5, level: 0 }, { category: 'Non Institutional (over ten lakh)', offered: 1, bid: 1, times: 0.18, level: 1 }] },
  filings: [filing], ...over,
});
const doc = (over: Record<string, unknown> = {}) => ({
  as_of: '2026-11-03', open: [issue()], upcoming: [issue({ symbol: 'VPFL', kind: 'sme', status: 'Forthcoming', price_low: 88, price_high: 94, lot: 1200, min_application: 112800, subscription: null, filings: [] })], closed: [],
  listed: [{ symbol: 'PARASHLTH', name: 'Paras Healthcare Limited', kind: 'mainboard', issue_price: 693, listing: '2026-10-28', listing_open: 800, listing_pct: 15.4, first_close_pct: 9.2, last_close: 640, last_pct: -7.6, filings: [] }],
  filed: [{ ...filing, company: 'Carlsberg India Limited', kind: 'UDRHP', pdf: 'javascript:alert(1)' }],
  sources: [{ id: 'nse_upcoming', name: 'NSE issue calendar', status: 'ok', reason: '', last_ok: '2026-11-03T09:00:00+00:00' }, { id: 'sebi_drafts', name: 'SEBI draft offer documents', status: 'failed', reason: 'HTTP 503', last_ok: '' }],
  caveat: 'Facts from the exchange and SEBI, display only.', ...over,
});

describe('the IPO document', () => {
  it('is read defensively: bad numbers become missing, unsafe links are dropped, and a non-document is refused', () => {
    const got = readIpo(doc({ filed: [{ ...filing, pdf: 'javascript:alert(1)', page: 'http://plain.test' }], open: [issue({ price_high: 'x', shares: null })] }))!;
    expect(got.filed[0]).toMatchObject({ pdf: '', page: '' }); // only https links are kept
    expect(got.open[0]).toMatchObject({ priceHigh: null, shares: null, symbol: 'BCSSL' });
    expect(got.open[0].subscription?.rows.map((r) => r.level)).toEqual([0, 1]);
    expect(readIpo(null)).toBeNull();
    expect(readIpo({ as_of: '2026-11-03' })).toBeNull();
    expect(readIpo({ as_of: '2026-11-03', open: [], listed: [] })!.upcoming).toEqual([]);
    expect(safeLink('https://a.test')).toBe('https://a.test');
    expect(safeLink('http://a.test')).toBe('');
  });

  it('says prices, sizes, multiples and gains the way a reader writes them and never shows a missing one as zero', () => {
    expect([bandText(78, 82), bandText(693, 693), bandText(null, 150), bandText(null, null)]).toEqual(['Rs 78 to 82', 'Rs 693', 'Rs 150', '-']);
    expect([timesText(2.5), timesText(125.43), timesText(null)]).toEqual(['2.50x', '125.4x', '-']);
    expect([gainText(15.4), gainText(-7.6), gainText(0), gainText(null)]).toEqual(['+15.4%', '-7.6%', '0.0%', '-']);
    expect([gainClass(1), gainClass(-1), gainClass(0), gainClass(null)]).toEqual(['up', 'dn', 'flat', 'flat']);
    expect(sizeText(135.6)).toBe('up to Rs 135.6 cr');
    expect(sizeText(null)).toBe('-');
    expect([sourceClass('ok'), sourceClass('failed'), sourceClass('never tried')]).toEqual(['up', 'dn', 'flat']);
    expect(ipoProblem(429)).toContain('already being built');
    expect(ipoProblem(502)).toContain('could not be built');
  });
});

describe('the command', () => {
  it('opens the IPO tab', () => {
    expect(parseCommand('IPO', { layouts: [] })).toEqual({ kind: 'ipo' });
    expect(parseCommand('ipos', { layouts: [] })).toEqual({ kind: 'ipo' });
  });
});

describe('the IPO view', () => {
  const answer = (body: unknown, status = 200) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
  const pause = () => new Promise((r) => setTimeout(r));

  it('shows each list with its facts, the subscription by category, the gains, and links to the documents', async () => {
    const symbols: string[] = [];
    const view = new IpoView({ onSymbol: (s) => symbols.push(s) }, answer(doc()));
    view.show();
    await pause();
    const text = view.element.textContent ?? '';
    expect(text).toContain('Open now (1)');
    expect(text).toContain('Bombay Coated and Special Steels Limited');
    expect(text).toContain('Rs 78 to 82');
    expect(text).toContain('up to Rs 135.6 cr');
    expect(text).toContain('1.10x');
    expect(text).toContain('Qualified Institutional Buyers(QIBs): 2.50x');
    expect(text).toContain('2 snapshots kept');
    expect(text).toContain('Coming (1)');
    expect(text).toContain('SME');
    expect(text).toContain('1200 (Rs 1,12,800)'); // Indian digit grouping
    expect(text).toContain('SME issue: its offer document is filed with the exchange, not SEBI, and is not read here'); // an SME issue has no SEBI draft, and the page says why instead of "no draft found"
    expect(text.match(/no draft found/g)).toHaveLength(1); // only the listed main-board issue with no matched draft; the SME issue now says why
    expect(text).toContain('Listed in the last 60 days (1)');
    expect(text).toContain('+15.4%');
    expect(text).toContain('-7.6%');
    expect(text).toContain('Draft offer documents filed with SEBI');
    expect(text).toContain('Carlsberg India Limited');
    expect(text).toContain('no document found yet'); // its only PDF link was unsafe and dropped
    expect(text).toContain('SEBI draft offer documents · failed');
    const links = [...view.element.querySelectorAll('a')] as HTMLAnchorElement[];
    expect(links.length).toBeGreaterThan(2);
    expect(links.every((a) => a.href.startsWith('https://') && a.rel === 'noopener noreferrer' && a.target === '_blank')).toBe(true);
    (view.element.querySelector('.ipo-name button') as HTMLButtonElement).click();
    expect(symbols).toEqual(['PARASHLTH']);
  });

  it('says what an empty page means and what went wrong, and a second show does not fetch again', async () => {
    const empty = new IpoView({ onSymbol: () => {} }, answer(doc({ open: [], upcoming: [], listed: [], filed: [] })));
    empty.show();
    await pause();
    expect(empty.element.textContent).toContain('No issue is open for bidding.');
    expect(empty.element.textContent).toContain('No issue is announced as coming.');
    const down = new IpoView({ onSymbol: () => {} }, answer('x', 502));
    down.show();
    await pause();
    expect(down.element.textContent).toContain('could not be built');
    const garbled = new IpoView({ onSymbol: () => {} }, answer({ nonsense: true }));
    garbled.show();
    await pause();
    expect(garbled.element.textContent).toContain('could not be built');
    let calls = 0;
    const counting = (async () => {
      calls++;
      return new Response(JSON.stringify(doc()), { status: 200 });
    }) as unknown as typeof fetch;
    const view = new IpoView({ onSymbol: () => {} }, counting);
    view.show();
    await pause();
    view.hide();
    view.show();
    await pause();
    expect(calls).toBe(1);
    (view.element.querySelector('button.btn') as HTMLButtonElement).click();
    await pause();
    expect(calls).toBe(2);
  });
});
