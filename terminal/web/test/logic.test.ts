import { describe, expect, it } from 'vitest';
import { applyTick, barTime, inSession, tickFitsChart, type Bar } from '../src/bars';
import { parse } from '../src/command';
import { age, compact, pct, price, signed } from '../src/format';
import { bollinger, ema, macd, rsi, sma } from '../src/indicators';
import { deserialize, LayoutError, PRESETS, PRESET_NAMES, serialize } from '../src/layout';
import { LinkBus, nextGroup } from '../src/links';
import type { AnalysisData } from '../src/panels/analysis';
import { dataNote, filterRows, noFilters, PRESETS as SCREEN_PRESETS, sortRows, toRows, type Row } from '../src/screenlogic';
import { readVerdict } from '../src/verdictdata';
import { barLimit, RANGES } from '../src/ranges';

const ctx = { layouts: ['Mine-1', 'Day trading'] };

describe('command line', () => {
  it('reads symbols and commands', () => {
    expect(parse('SBIN', ctx)).toEqual({ kind: 'symbol', symbol: 'SBIN' });
    expect(parse('  sbin  ', ctx)).toEqual({ kind: 'symbol', symbol: 'SBIN' });
    expect(parse('sbin chart', ctx)).toEqual({ kind: 'chart', symbol: 'SBIN' });
    expect(parse('add tcs', ctx)).toEqual({ kind: 'add', symbol: 'TCS' });
    expect(parse('REMOVE TCS', ctx)).toEqual({ kind: 'remove', symbol: 'TCS' });
    expect(parse('M&M', ctx)).toEqual({ kind: 'symbol', symbol: 'M&M' });
  });
  it('switches layouts by preset or saved name', () => {
    expect(parse('layout b', ctx)).toEqual({ kind: 'layout', name: 'B' });
    expect(parse('layout d', ctx)).toEqual({ kind: 'layout', name: 'D' });
    expect(parse('layout day trading', ctx)).toEqual({ kind: 'layout', name: 'Day trading' });
    expect(parse('mine-1', ctx)).toEqual({ kind: 'layout', name: 'Mine-1' });
    expect(parse('layout nothing', ctx).kind).toBe('error');
  });
  it('takes an index name of two or three words as one symbol', () => {
    expect(parse('nifty 50', ctx)).toEqual({ kind: 'symbol', symbol: 'NIFTY 50' });
    expect(parse('nifty bank chart', ctx)).toEqual({ kind: 'chart', symbol: 'NIFTY BANK' });
    expect(parse('nifty 50 research', ctx)).toEqual({ kind: 'research', symbol: 'NIFTY 50' });
    expect(parse('add nifty 50', ctx)).toEqual({ kind: 'add', symbol: 'NIFTY 50' }); // an index may be in a watchlist
    expect(parse('add tcs to My list', ctx)).toEqual({ kind: 'add', symbol: 'TCS', list: 'My list' });
    expect(parse('add tcs to', ctx).kind).toBe('error');
    expect(parse('list Main', ctx)).toEqual({ kind: 'list', name: 'Main' });
    expect(parse('list', ctx).kind).toBe('error');
    expect(parse('remove nifty bank', ctx)).toEqual({ kind: 'remove', symbol: 'NIFTY BANK' });
  });
  it('opens the research page, on a stock when one is given', () => {
    expect(parse('research', ctx)).toEqual({ kind: 'research', symbol: null });
    expect(parse('research tcs', ctx)).toEqual({ kind: 'research', symbol: 'TCS' });
    expect(parse('tcs research', ctx)).toEqual({ kind: 'research', symbol: 'TCS' });
    expect(parse('terminal', ctx)).toEqual({ kind: 'terminal' });
    expect(parse('research @@', ctx).kind).toBe('error');
    expect(parse('research', { layouts: ['Research'] })).toEqual({ kind: 'layout', name: 'Research' }); // a saved layout of that name wins
  });
  it('rejects nonsense with a message', () => {
    const empty = parse('', ctx);
    expect(empty).toEqual({ kind: 'error', message: 'type a symbol or a command' });
    expect(parse('@@', ctx).kind).toBe('error');
    expect(parse('ADD', ctx).kind).toBe('error');
    expect(parse('ADD A B', ctx).kind).toBe('error');
    expect(parse('SBIN HELLO', ctx).kind).toBe('error');
  });
});

describe('options command', () => {
  it('opens the Options page on an underlying, either word order, and on no underlying', () => {
    expect(parse('OPTIONS', ctx)).toEqual({ kind: 'options', symbol: null });
    expect(parse('chain', ctx)).toEqual({ kind: 'options', symbol: null });
    expect(parse('options banknifty', ctx)).toEqual({ kind: 'options', symbol: 'BANKNIFTY' });
    expect(parse('GOLD OPTIONS', ctx)).toEqual({ kind: 'options', symbol: 'GOLD' });
    expect(parse('NIFTY OPTIONS', ctx)).toEqual({ kind: 'options', symbol: 'NIFTY' });
    expect(parse('NIFTY chain', ctx)).toEqual({ kind: 'options', symbol: 'NIFTY' });
    expect(parse('OPTIONS NIFTY', ctx)).toEqual({ kind: 'options', symbol: 'NIFTY' });
  });
  it('still reads an index name of several words as an index', () => {
    expect(parse('NIFTY BANK', ctx)).toEqual({ kind: 'symbol', symbol: 'NIFTY BANK' });
    expect(parse('NIFTY 50 CHART', ctx)).toEqual({ kind: 'chart', symbol: 'NIFTY 50' });
  });
  it('refuses a word that cannot be an underlying', () => {
    expect(parse('OPTIONS a$b', ctx).kind).toBe('error');
  });
});

describe('details command', () => {
  const ctx = { layouts: [] };
  it('opens the Details panel with or without a stock', () => {
    expect(parse('details', ctx)).toEqual({ kind: 'details', symbol: null });
    expect(parse('DET', ctx)).toEqual({ kind: 'details', symbol: null });
    expect(parse('details sbin', ctx)).toEqual({ kind: 'details', symbol: 'SBIN' });
    expect(parse('sbin details', ctx)).toEqual({ kind: 'details', symbol: 'SBIN' });
    expect(parse('nifty 50 details', ctx)).toEqual({ kind: 'details', symbol: 'NIFTY 50' });
    expect(parse('details nifty bank', ctx)).toEqual({ kind: 'details', symbol: 'NIFTY BANK' });
    expect(parse('details bad symbol!', ctx).kind).toBe('error');
  });
});

describe('link groups', () => {
  it('moves panels in the same colour and leaves the rest', () => {
    const bus = new LinkBus();
    const seen: string[] = [];
    bus.subscribe('blue', (s) => seen.push(`blue:${s}`));
    bus.subscribe('red', (s) => seen.push(`red:${s}`));
    bus.setSymbol('blue', 'SBIN');
    expect(seen).toEqual(['blue:SBIN']);
    expect(bus.symbolOf('blue')).toBe('SBIN');
    expect(bus.symbolOf('red')).toBeNull();
  });
  it('treats none as private', () => {
    const bus = new LinkBus();
    let called = false;
    bus.subscribe('none', () => (called = true));
    bus.setSymbol('none', 'SBIN');
    expect(called).toBe(false);
    expect(bus.symbolOf('none')).toBeNull();
  });
  it('unsubscribes and cycles', () => {
    const bus = new LinkBus();
    let n = 0;
    const off = bus.subscribe('green', () => n++);
    bus.setSymbol('green', 'A');
    off();
    bus.setSymbol('green', 'B');
    expect(n).toBe(1);
    expect(nextGroup('none')).toBe('red');
    expect(nextGroup('blue')).toBe('none');
  });
});

describe('layouts', () => {
  it('round-trips every preset', () => {
    for (const name of PRESET_NAMES) {
      const m = PRESETS[name]();
      expect(deserialize(serialize(m))).toEqual(m);
    }
  });
  it('presets hold the panels the spec lists', () => {
    const types = (n: 'A' | 'B' | 'C') => Object.values(PRESETS[n]().panels).map((p) => p.type).sort();
    expect(types('A')).toEqual(['chart', 'depth', 'details', 'quote', 'tape', 'watchlist']);
    expect(types('B')).toEqual(['chart', 'chart', 'chart', 'chart', 'tape']);
    expect(types('C')).toEqual(['chart', 'depth', 'quote', 'tape', 'watchlist']);
    expect(PRESETS.D().arrangement?.length).toBe(5);
    expect(Object.values(PRESETS.D().panels).map((p) => p.type).sort()).toEqual(['analysis', 'chart', 'tape', 'verdict', 'watchlist']);
  });
  it('every placement refers to a panel that exists and to an earlier one', () => {
    for (const name of PRESET_NAMES) {
      const m = PRESETS[name]();
      const seen = new Set<string>();
      for (const p of m.arrangement ?? []) {
        expect(m.panels[p.id]).toBeDefined();
        if (p.ref) expect(seen.has(p.ref)).toBe(true);
        seen.add(p.id);
      }
      expect(seen.size).toBe(Object.keys(m.panels).length);
    }
  });
  it('rejects a wrong version, an unknown panel type, a bad group and bad JSON', () => {
    const good = PRESETS.A();
    expect(() => deserialize(JSON.stringify({ ...good, version: 2 }))).toThrow(LayoutError);
    expect(() => deserialize(JSON.stringify({ ...good, panels: { x: { type: 'bogus', group: 'none' } } }))).toThrow(LayoutError);
    expect(() => deserialize(JSON.stringify({ ...good, panels: { x: { type: 'chart', group: 'pink' } } }))).toThrow(LayoutError);
    expect(() => deserialize('{nope')).toThrow(LayoutError);
  });
});

describe('ranges', () => {
  it('maps each label to a bar size', () => {
    const tf = Object.fromEntries(RANGES.map((r) => [r.label, r.tf]));
    expect(tf).toEqual({ '1D': '1m', '5D': '5m', '1M': '1D', '3M': '1D', '6M': '1D', YTD: '1D', '1Y': '1D', '5Y': '1D', All: '1D' });
  });
  it('computes year-to-date bars from the date', () => {
    const ytd = RANGES.find((r) => r.label === 'YTD')!;
    expect(barLimit(ytd, new Date(Date.UTC(2026, 0, 1)))).toBe(1);
    const october = barLimit(ytd, new Date(Date.UTC(2026, 9, 14)))!;
    expect(october).toBeGreaterThan(170);
    expect(october).toBeLessThan(200);
    expect(barLimit(RANGES.find((r) => r.label === 'All')!, new Date())).toBeNull();
  });
});

describe('indicators', () => {
  it('sma and ema', () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
    expect(ema([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });
  it('bollinger bands are symmetric around the mean', () => {
    const b = bollinger([1, 2, 3, 4, 5], 3, 2);
    expect(b.mid[2]).toBe(2);
    const sd = Math.sqrt(2 / 3);
    expect(b.upper[2]).toBeCloseTo(2 + 2 * sd, 10);
    expect(b.lower[2]).toBeCloseTo(2 - 2 * sd, 10);
    expect(b.upper[1]).toBeNull();
  });
  it('rsi is 100 on a rising series and 0 on a falling one', () => {
    const up = rsi([1, 2, 3, 4, 5, 6, 7, 8], 3);
    expect(up.slice(0, 3)).toEqual([null, null, null]);
    expect(up[3]).toBe(100);
    expect(up[7]).toBe(100);
    expect(rsi([8, 7, 6, 5, 4, 3, 2, 1], 3)[5]).toBe(0);
  });
  it('macd histogram is macd minus signal', () => {
    const values = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 3) * 5 + i * 0.1);
    const m = macd(values, 12, 26, 9);
    const i = 50;
    expect(m.hist[i]).toBeCloseTo((m.macd[i] as number) - (m.signal[i] as number), 10);
    expect(m.macd[24]).toBeNull();
    expect(m.macd[25]).not.toBeNull();
    expect(m.signal[32]).toBeNull();
    expect(m.signal[33]).not.toBeNull();
  });
});

describe('bar times and ticks', () => {
  it('floors to the bar start', () => {
    expect(barTime(1_000_030, '1m')).toBe(1_000_020);
    expect(barTime(1_000_030, '5m')).toBe(999_900);
    expect(barTime(1_000_030, '15m')).toBe(999_900);
    // 2026-10-13 21:00 UTC is 2026-10-14 02:30 IST, so the daily bar is the 14th at midnight UTC
    expect(barTime(1_791_849_600 + 21 * 3600, '1D')).toBe(1_791_936_000);
    expect(barTime(1_791_849_600 + 4 * 3600, '1D')).toBe(1_791_849_600);
  });
  const bar: Bar = { t: 960, o: 10, h: 12, l: 9, c: 11, v: 100 };
  it('updates the bar a tick falls in', () => {
    expect(applyTick(bar, 13, 1000, '1m', 5)).toEqual({ t: 960, o: 10, h: 13, l: 9, c: 13, v: 105 });
    expect(applyTick(bar, 8, 1000, '1m')).toEqual({ t: 960, o: 10, h: 12, l: 8, c: 8, v: 100 });
  });
  it('opens a new bar across the boundary and ignores a late tick', () => {
    expect(applyTick(bar, 14, 1030 + 60, '1m')).toEqual({ t: 1080, o: 14, h: 14, l: 14, c: 14, v: 0 });
    expect(applyTick(bar, 14, 100, '1m')).toBe(bar);
    expect(applyTick(undefined, 5, 1000, '1m').t).toBe(960);
  });
});

describe('format', () => {
  it('formats prices, changes and ages', () => {
    expect(price(1234567.5)).toBe('12,34,567.50');
    expect(price(812.4)).toBe('812.40');
    expect(signed(1.2)).toBe('+1.20');
    expect(signed(-0.3)).toBe('-0.30');
    expect(signed(0)).toBe('0.00');
    expect(pct(0.094)).toBe('+0.09%');
    expect(pct(-1.5)).toBe('-1.50%');
    expect(age(2000)).toBe('2s');
    expect(age(180_000)).toBe('3m');
    expect(compact(4_200_000)).toBe('42.00L');
    expect(compact(980)).toBe('980');
  });
});

describe('market state', async () => {
  const { marketState } = await import('../src/market');
  const at = (iso: string) => marketState(new Date(iso));
  it('is open on weekday trading hours (IST)', () => {
    expect(at('2026-10-14T04:00:00Z').open).toBe(true); // Wed 09:30 IST
    expect(at('2026-10-14T03:45:00Z').open).toBe(true); // 09:15 IST
    expect(at('2026-10-14T10:00:00Z').open).toBe(true); // 15:30 IST
  });
  it('says when it reopens when shut', () => {
    expect(at('2026-10-14T10:01:00Z').text).toContain('opens Thu 09:15 IST'); // after close
    expect(at('2026-10-14T03:00:00Z').text).toContain('opens today 09:15 IST'); // before open
    expect(at('2026-10-16T12:00:00Z').text).toContain('opens Mon'); // Friday evening
    expect(at('2026-10-17T06:00:00Z').text).toContain('opens Mon'); // Saturday
    expect(at('2026-10-18T06:00:00Z').open).toBe(false); // Sunday
  });
});

describe('stored verdict', () => {
  const data = (payload: Record<string, unknown>, marketDay = '2026-10-16'): AnalysisData => ({
    symbol: 'X', name: null, kind: 'stock', market_day: marketDay, screen: null, technical: null, fundamentals: null,
    verdict: { made_at: '2026-10-13 08:34:00', data_as_of: '2026-10-12', close: 1, payload },
  });
  it('reads the band, conviction, analysts, risks and what did not run', () => {
    const v = readVerdict(
      data({
        verdict: { verdict: 'Underweight', conviction: 5, raw_conviction: 23, calibration: 'uncalibrated', trust: 0.229, key_risks: ['a risk'] },
        specialists: {
          valuation: { signal: 'bullish', confidence: 70, data_coverage: 'full', reasoning: 'cheap', missing: [] },
          moat_quality: { signal: 'bearish', confidence: 60, data_coverage: 'full', reasoning: 'weak roe', missing: ['leverage'] },
        },
        skipped: { news_sentiment: 'not built yet' },
        conflict: { bullish: ['valuation'], bearish: ['moat_quality'] },
        notes: ['only 2 ran'],
      }),
    )!;
    expect(v.band).toBe('Underweight');
    expect(v.tone).toBe('dn');
    expect([v.conviction, v.raw, v.trust]).toEqual([5, 23, 0.229]);
    expect(v.analysts.map((a) => [a.name, a.tone, a.confidence])).toEqual([['valuation', 'up', 70], ['moat quality', 'dn', 60]]);
    expect(v.analysts[1].missing).toEqual(['leverage']);
    expect(v.skipped).toEqual([{ name: 'news sentiment', reason: 'not built yet' }]);
    expect(v.disagree).toEqual(['bullish: valuation', 'bearish: moat quality']);
    expect(v.newerDays).toBe(4);
  });
  it('copes with a bare list for skipped and a missing verdict block', () => {
    const v = readVerdict(data({ skipped: ['news_sentiment'], specialists: {} }))!;
    expect(v.band).toBe('no view');
    expect(v.skipped).toEqual([{ name: 'news sentiment', reason: '' }]);
    expect(v.analysts).toEqual([]);
    expect(readVerdict({ ...data({}), verdict: null })).toBeNull();
  });
});

describe('which ticks may move the chart', () => {
  const utc = (iso: string) => Date.parse(iso) / 1000;
  const bar: Bar = { t: utc('2026-10-16T09:58:00Z'), o: 962, h: 963, l: 961, c: 962.6, v: 10 };
  it('knows the session in IST', () => {
    expect(inSession(utc('2026-10-16T03:45:00Z'))).toBe(true); // 09:15 IST, a Friday
    expect(inSession(utc('2026-10-16T03:44:59Z'))).toBe(false);
    expect(inSession(utc('2026-10-16T09:59:59Z'))).toBe(true); // 15:29:59
    expect(inSession(utc('2026-10-16T10:00:00Z'))).toBe(false); // 15:30
    expect(inSession(utc('2026-10-17T05:00:00Z'))).toBe(false); // Saturday
  });
  it('ignores a snapshot stamped after the close and a price that is not this stock', () => {
    expect(tickFitsChart(bar, 962.8, utc('2026-10-16T09:58:30Z'), '1m')).toBe(true);
    expect(tickFitsChart(bar, 962.6, utc('2026-10-16T10:38:10Z'), '1m')).toBe(false); // 16:08 IST
    expect(tickFitsChart(bar, 2230, utc('2026-10-16T09:58:30Z'), '1m')).toBe(false);
    expect(tickFitsChart({ ...bar, t: utc('2026-10-16T00:00:00Z') }, 962.6, utc('2026-10-16T10:38:10Z'), '1D')).toBe(true); // a daily bar may close up after the session
    expect(tickFitsChart(undefined, 100, utc('2026-10-16T05:00:00Z'), '1m')).toBe(true);
    expect(tickFitsChart(bar, 0, utc('2026-10-16T05:00:00Z'), '1m')).toBe(false);
  });
});

describe('market screen rules', () => {
  const cols = ['symbol', 'kind', 'name', 'sector', 'pe', 'pb', 'roe', 'trend', 'ret_1y', 'from_52w_high', 'market_cap_cr', 'flags'];
  const rows: Row[] = toRows(cols, [
    ['CHEAP', 'stock', 'Cheap Ltd', 'Banks', 9, 1.2, 0.18, 'aligned_up', 0.3, -0.05, 60000, 0],
    ['DEAR', 'stock', 'Dear Ltd', 'IT', 40, 9, 0.3, 'aligned_down', 0.1, -0.5, 9000, 2],
    ['NOEPS', 'stock', 'No Earnings Ltd', 'IT', null, null, null, null, 0.25, -0.02, 2000, 0],
    ['FUND', 'etf', 'Some ETF', null, null, null, null, 'not_aligned', 0.12, -0.01, null, 0],
  ]);
  const preset = (label: string) => {
    const p = SCREEN_PRESETS.find((x) => x.label === label)!;
    return { ...noFilters(), ranges: p.ranges, kinds: p.kinds, trends: p.trends };
  };
  it('builds rows from the server form and keeps blanks as null', () => {
    expect(rows[2].pe).toBeNull();
    expect(rows[0].symbol).toBe('CHEAP');
  });
  it('a missing figure never passes a limit, and the Value preset keeps only cheap, profitable stocks', () => {
    expect(filterRows(rows, preset('Value')).map((r) => r.symbol)).toEqual(['CHEAP']);
  });
  it('presets combine ranges, trend and kind', () => {
    expect(filterRows(rows, preset('Momentum')).map((r) => r.symbol)).toEqual(['CHEAP']); // up over 20%, near the high, aligned up
    expect(filterRows(rows, preset('Beaten down')).map((r) => r.symbol)).toEqual([]); // needs traded value, which these rows lack
    expect(filterRows(rows, preset('Liquid large caps')).map((r) => r.symbol)).toEqual(['CHEAP']);
    expect(filterRows(rows, { ...noFilters(), kinds: [] }).length).toBe(4); // stocks and ETFs
  });
  it('text, sector, flags and trend filters', () => {
    expect(filterRows(rows, { ...noFilters(), text: 'dear' }).map((r) => r.symbol)).toEqual(['DEAR']);
    expect(filterRows(rows, { ...noFilters(), sector: 'IT' }).length).toBe(2);
    expect(filterRows(rows, { ...noFilters(), cleanOnly: true }).map((r) => r.symbol)).toEqual(['CHEAP', 'NOEPS']);
    expect(filterRows(rows, { ...noFilters(), trends: ['aligned_down'] }).map((r) => r.symbol)).toEqual(['DEAR']);
  });
  it('sorts with missing figures last whichever way it runs', () => {
    expect(sortRows(rows, 'pe', true).map((r) => r.symbol)).toEqual(['DEAR', 'CHEAP', 'NOEPS', 'FUND']);
    expect(sortRows(rows, 'pe', false).map((r) => r.symbol)).toEqual(['CHEAP', 'DEAR', 'NOEPS', 'FUND']);
    expect(sortRows(rows, 'symbol', false).map((r) => r.symbol)).toEqual(['CHEAP', 'DEAR', 'FUND', 'NOEPS']);
  });
  it('says how old the data is and what is missing', () => {
    const note = dataNote(rows, '2026-10-16', new Date(Date.UTC(2026, 9, 18)));
    expect(note).toContain('2 days old');
    expect(note).toContain('2 of 3 stocks have statements');
    expect(note).toContain('1 symbols carry a data flag');
    expect(dataNote([], null, new Date())).toContain('No prices');
  });
});
