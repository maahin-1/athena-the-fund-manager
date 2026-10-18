import { describe, expect, it } from 'vitest';
import { barEnd, momentOfDate, Player, revealedCount, SPEEDS } from '../src/chartlab/replay';

const IST = 19_800;
const utc = (y: number, m: number, d: number, hh = 0, mm = 0): number => Date.UTC(y, m - 1, d, hh, mm) / 1000;
const ist = (y: number, m: number, d: number, hh: number, mm: number): number => utc(y, m, d, hh, mm) - IST;
const quarter = (y: number, m: number, d: number) => Array.from({ length: 25 }, (_, i) => ({ t: ist(y, m, d, 9, 15) + i * 900 })); // 09:15 to 15:15
const days = [12, 13, 14, 15, 16].map((d) => ({ t: utc(2026, 10, d) }));

describe('when a bar has ended', () => {
  it('is a bar-length after the start for an intraday bar, the last one of a day included', () => {
    const bars = quarter(2026, 10, 14);
    expect(barEnd(bars, 0, '15m')).toBe(ist(2026, 10, 14, 9, 30));
    expect(barEnd(bars, 24, '15m')).toBe(ist(2026, 10, 14, 15, 30)); // 15:15 bar ends at the close
    expect(barEnd([{ t: ist(2026, 10, 14, 9, 15) }], 0, '2h')).toBe(ist(2026, 10, 14, 11, 15));
    expect(barEnd([{ t: ist(2026, 10, 14, 9, 15) }], 0, '7m')).toBe(ist(2026, 10, 14, 9, 22));
  });

  it('is 15:30 IST of the date for a daily bar', () => {
    expect(barEnd(days, 2, '1D')).toBe(ist(2026, 10, 14, 15, 30));
  });

  it('is the start of the next bar for a bar of several days, a week or a month, and a bar-length for the last one', () => {
    const three = [{ t: utc(2026, 10, 12) }, { t: utc(2026, 10, 15) }];
    expect(barEnd(three, 0, '3D')).toBe(utc(2026, 10, 15));
    expect(barEnd(three, 1, '3D')).toBe(utc(2026, 10, 15) + 3 * 86_400);
    const weeks = [{ t: utc(2026, 10, 5) }, { t: utc(2026, 10, 12) }];
    expect(barEnd(weeks, 0, '1W')).toBe(utc(2026, 10, 12));
    expect(barEnd(weeks, 1, '1W')).toBe(utc(2026, 10, 19));
    expect(barEnd([{ t: utc(2026, 10, 1) }], 0, '1M')).toBe(utc(2026, 10, 31));
  });

  it('is a day after its start for something that is not an interval, rather than failing', () => {
    expect(barEnd([{ t: 100 }], 0, 'nonsense')).toBe(100 + 86_400);
  });
});

describe('the bars a moment reveals', () => {
  it('reveals the intraday bars that have ended, and none before the first has', () => {
    const bars = quarter(2026, 10, 14);
    expect(revealedCount(bars, ist(2026, 10, 14, 9, 29), '15m')).toBe(0);
    expect(revealedCount(bars, ist(2026, 10, 14, 9, 30), '15m')).toBe(1); // exactly at the end: complete
    expect(revealedCount(bars, ist(2026, 10, 14, 11, 0), '15m')).toBe(7);
    expect(revealedCount(bars, ist(2026, 10, 14, 15, 30), '15m')).toBe(25);
    expect(revealedCount(bars, ist(2026, 10, 20, 10, 0), '15m')).toBe(25);
  });

  it('reveals daily bars through the last complete day, and not today\'s while it runs', () => {
    expect(revealedCount(days, ist(2026, 10, 14, 11, 0), '1D')).toBe(2); // the 14th is still running
    expect(revealedCount(days, ist(2026, 10, 14, 15, 30), '1D')).toBe(3); // closed
    expect(revealedCount(days, ist(2026, 10, 15, 9, 15), '1D')).toBe(3);
    expect(revealedCount(days, utc(2026, 10, 1), '1D')).toBe(0);
  });

  it('shows a coarser chart no further than the driving chart has got, and lags it', () => {
    const weeks = [{ t: utc(2026, 10, 5) }, { t: utc(2026, 10, 12) }, { t: utc(2026, 10, 19) }];
    const m = ist(2026, 10, 14, 11, 0); // the middle of the week of the 12th
    expect(revealedCount(weeks, m, '1W')).toBe(1); // the week of the 5th only; the 12th is not complete
    expect(revealedCount(weeks, ist(2026, 10, 19, 9, 30), '1W')).toBe(2);
  });

  it('never reveals more with an earlier moment', () => {
    const bars = quarter(2026, 10, 14);
    let last = 0;
    for (let m = ist(2026, 10, 14, 9, 0); m < ist(2026, 10, 14, 16, 0); m += 60) {
      const n = revealedCount(bars, m, '15m');
      expect(n).toBeGreaterThanOrEqual(last);
      last = n;
    }
  });

  it('is zero for no bars', () => {
    expect(revealedCount([], 1e12, '1D')).toBe(0);
  });
});

describe('a typed date', () => {
  it('stands for the end of that trading day', () => {
    expect(momentOfDate('2026-10-14')).toBe(ist(2026, 10, 14, 15, 30));
    expect(momentOfDate(' 2026-10-14 ')).toBe(ist(2026, 10, 14, 15, 30));
  });

  it('is null for text that is not a real date', () => {
    for (const t of ['', 'tomorrow', '14-10-2026', '2026-13-01', '2026-02-30', '2026-10-1']) expect(momentOfDate(t), t).toBeNull();
  });
});

describe('the player', () => {
  it('starts with the bars up to the chosen one and never outside 1 to the total', () => {
    expect(new Player(100, 40).shown).toBe(40);
    expect(new Player(100, 0).shown).toBe(1);
    expect(new Player(100, 500).shown).toBe(100);
    expect(new Player(0, 5).shown).toBe(0);
  });

  it('steps forward and back and says when it cannot', () => {
    const p = new Player(3, 1);
    expect(p.back()).toBe(false);
    expect(p.next()).toBe(true);
    expect(p.next()).toBe(true);
    expect(p.shown).toBe(3);
    expect(p.atEnd).toBe(true);
    expect(p.next()).toBe(false);
    expect(p.back()).toBe(true);
    expect(p.shown).toBe(2);
  });

  it('plays until the last bar and stops there, saying so once', () => {
    const p = new Player(4, 2);
    p.play();
    expect(p.tick()).toBe('stepped');
    expect(p.shown).toBe(3);
    expect(p.tick()).toBe('ended');
    expect(p.shown).toBe(4);
    expect(p.playing).toBe(false);
    expect(p.tick()).toBe('idle');
  });

  it('does not play from the end, and pausing keeps the place', () => {
    const end = new Player(3, 3);
    end.play();
    expect(end.playing).toBe(false);
    const p = new Player(10, 2);
    p.toggle();
    expect(p.playing).toBe(true);
    p.tick();
    p.toggle();
    expect(p.playing).toBe(false);
    expect(p.tick()).toBe('idle');
    expect(p.shown).toBe(3);
  });

  it('stops playing when stepped back, and when a jump reaches the end', () => {
    const p = new Player(10, 5);
    p.play();
    p.back();
    expect(p.playing).toBe(false);
    p.play();
    p.jump(99);
    expect(p.shown).toBe(10);
    expect(p.playing).toBe(false);
    p.jump(0);
    expect(p.shown).toBe(1);
  });

  it('has nine speeds, takes only those, and beats at their rate', () => {
    expect(SPEEDS).toHaveLength(9);
    const p = new Player(10, 1);
    expect(p.speed).toBe(1);
    expect(p.intervalMs).toBe(1000);
    expect(p.setSpeed(10)).toBe(true);
    expect(p.intervalMs).toBe(100);
    expect(p.setSpeed(7)).toBe(false);
    expect(p.speed).toBe(10);
    p.setSpeed(0.1);
    expect(p.intervalMs).toBe(10_000);
  });
});
