import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { frameBars, labelFor, MIN_BARS, RULES, summarize, technicals, type Vote } from '../src/details/technicals';

const vote = (verdict: Vote['verdict']): Vote => ({ name: 'x', value: 1, verdict });

describe('the rules', () => {
  it('RSI: oversold and rising buys, overbought and falling sells', () => {
    expect(RULES.rsi(25, 20)).toBe('buy');
    expect(RULES.rsi(25, 30)).toBe('neutral'); // oversold but still falling
    expect(RULES.rsi(75, 80)).toBe('sell');
    expect(RULES.rsi(75, 70)).toBe('neutral');
    expect(RULES.rsi(50, 40)).toBe('neutral');
  });
  it('Stochastic: both lines low with %K over %D buys, both high with %K under %D sells', () => {
    expect(RULES.stoch(15, 10)).toBe('buy');
    expect(RULES.stoch(15, 25)).toBe('neutral');
    expect(RULES.stoch(85, 90)).toBe('sell');
    expect(RULES.stoch(85, 75)).toBe('neutral');
  });
  it('CCI: below -100 and rising buys, above 100 and falling sells', () => {
    expect(RULES.cci(-150, -170)).toBe('buy');
    expect(RULES.cci(-150, -120)).toBe('neutral');
    expect(RULES.cci(150, 170)).toBe('sell');
    expect(RULES.cci(0, -5)).toBe('neutral');
  });
  it('ADX: needs a trend over 20, then the direction decides', () => {
    expect(RULES.adx(25, 30, 10)).toBe('buy');
    expect(RULES.adx(25, 10, 30)).toBe('sell');
    expect(RULES.adx(15, 30, 10)).toBe('neutral');
  });
  it('Awesome oscillator and Bull Bear Power: sign and slope agree', () => {
    expect(RULES.zeroRising(2, 1)).toBe('buy');
    expect(RULES.zeroRising(2, 3)).toBe('neutral');
    expect(RULES.zeroRising(-2, -1)).toBe('sell'); // below zero and falling
    expect(RULES.zeroRising(-1, -2)).toBe('neutral'); // below zero but rising
  });
  it('Momentum, MACD, Williams %R, Ultimate, Stochastic RSI', () => {
    expect(RULES.momentum(3, 2)).toBe('buy');
    expect(RULES.momentum(1, 2)).toBe('sell');
    expect(RULES.momentum(2, 2)).toBe('neutral');
    expect(RULES.macd(1, 0.5)).toBe('buy');
    expect(RULES.macd(0.5, 1)).toBe('sell');
    expect(RULES.williams(-90, -95)).toBe('buy');
    expect(RULES.williams(-10, -5)).toBe('sell');
    expect(RULES.williams(-50, -60)).toBe('neutral');
    expect(RULES.ultimate(75)).toBe('buy');
    expect(RULES.ultimate(25)).toBe('sell');
    expect(RULES.ultimate(50)).toBe('neutral');
    expect(RULES.stochRsi(10, 5)).toBe('buy');
    expect(RULES.stochRsi(90, 95)).toBe('sell');
    expect(RULES.stochRsi(50, 40)).toBe('neutral');
  });
  it('a moving average: price above buys, below sells, equal is neutral', () => {
    expect(RULES.average(10, 9)).toBe('buy');
    expect(RULES.average(8, 9)).toBe('sell');
    expect(RULES.average(9, 9)).toBe('neutral');
  });
});

describe('the summary', () => {
  it('labels at the stated thresholds', () => {
    expect(labelFor(-1)).toBe('Strong sell');
    expect(labelFor(-0.5)).toBe('Strong sell');
    expect(labelFor(-0.49)).toBe('Sell');
    expect(labelFor(-0.1)).toBe('Neutral');
    expect(labelFor(0)).toBe('Neutral');
    expect(labelFor(0.1)).toBe('Neutral');
    expect(labelFor(0.11)).toBe('Buy');
    expect(labelFor(0.49)).toBe('Buy');
    expect(labelFor(0.5)).toBe('Strong buy');
  });
  it('scores buys minus sells over everything counted', () => {
    const s = summarize([vote('buy'), vote('buy'), vote('buy'), vote('sell'), vote('neutral')]);
    expect(s).toMatchObject({ buy: 3, sell: 1, neutral: 1, score: 0.4, label: 'Buy' });
    expect(summarize([]).label).toBe('Neutral');
  });
});

/** `n` daily weekday bars; the close follows `f(i)`. */
function bars(n: number, f: (i: number) => number): Bar[] {
  const out: Bar[] = [];
  let t = Date.UTC(2020, 0, 6) / 1000; // a Monday
  for (let i = 0; out.length < n; i++, t += 86_400) {
    const dow = new Date(t * 1000).getUTCDay();
    if (dow === 0 || dow === 6) continue;
    const c = f(out.length);
    out.push({ t, o: c, h: c + 0.5, l: c - 0.5, c, v: 1000 + (out.length % 7) * 10 });
  }
  return out;
}

describe('technicals', () => {
  it('needs at least 30 bars', () => {
    expect(technicals(bars(MIN_BARS - 1, (i) => 100 + i))).toBeNull();
    expect(technicals(bars(MIN_BARS, (i) => 100 + i))).not.toBeNull();
  });

  it('a long steady rise has every lagging average buying', () => {
    const t = technicals(bars(400, (i) => 100 + i * 0.5 + Math.sin(i / 3) * 0.2))!;
    expect(t.averages).toHaveLength(14);
    // the Hull average has almost no lag, so on a wobbling rise it can sit above the close; the lagging averages cannot
    expect(t.averages.filter((a) => /^[ES]MA/.test(a.name)).every((a) => a.verdict === 'buy')).toBe(true);
    expect(t.averageSummary.label).toBe('Strong buy');
    expect(t.summary.score).toBeGreaterThan(0.1);
  });

  it('a long steady fall has every lagging average selling', () => {
    const t = technicals(bars(400, (i) => 400 - i * 0.5 + Math.sin(i / 3) * 0.2))!;
    expect(t.averages.filter((a) => /^[ES]MA/.test(a.name)).every((a) => a.verdict === 'sell')).toBe(true);
    expect(t.averageSummary.label).toBe('Strong sell');
    expect(t.summary.score).toBeLessThan(-0.1);
  });

  it('a flat price gives no buys and no sells', () => {
    const t = technicals(bars(300, () => 100))!;
    expect(t.summary.buy).toBe(0);
    expect(t.summary.sell).toBe(0);
    expect(t.summary.label).toBe('Neutral');
  });

  it('leaves an average out when there is not enough history for it', () => {
    const t = technicals(bars(60, (i) => 100 + i))!;
    const names = t.averages.map((a) => a.name);
    expect(names).toContain('EMA (50)');
    expect(names).not.toContain('EMA (100)');
    expect(names).not.toContain('SMA (200)');
  });

  it('counts every vote it lists', () => {
    const t = technicals(bars(400, (i) => 100 + Math.sin(i / 5) * 10 + i * 0.1))!;
    const n = t.oscillators.length + t.averages.length;
    expect(t.summary.buy + t.summary.sell + t.summary.neutral).toBe(n);
    expect(t.oscillators.length).toBeGreaterThanOrEqual(10);
    expect(n).toBeLessThanOrEqual(25);
  });
});

describe('frames', () => {
  it('weekly and monthly bars have shorter history', () => {
    const d = bars(1500, (i) => 100 + i * 0.2 + Math.sin(i / 4));
    expect(frameBars(d, '1D')).toBe(d);
    const w = frameBars(d, '1W');
    const m = frameBars(d, '1M');
    expect(w.length).toBeLessThan(d.length / 4);
    expect(m.length).toBeLessThan(w.length / 3);
    expect(technicals(m)!.averages.length).toBeLessThan(technicals(d)!.averages.length);
  });
});
