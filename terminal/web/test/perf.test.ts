import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { minusMonths, performance } from '../src/details/perf';

const day = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / 1000;
/** One bar per calendar day from `from` to `to`, the close rising 1 per day starting at 100. */
function daily(from: string, to: string): Bar[] {
  const out: Bar[] = [];
  for (let t = day(from), c = 100; t <= day(to); t += 86_400, c += 1) out.push({ t, o: c, h: c, l: c, c, v: 1 });
  return out;
}
const get = (rows: ReturnType<typeof performance>, key: string) => rows.find((r) => r.key === key)!.pct;

describe('minusMonths', () => {
  it('clamps to the end of a shorter month and crosses years', () => {
    expect(minusMonths(day('2026-03-31'), 1)).toBe(day('2026-02-28'));
    expect(minusMonths(day('2024-03-31'), 1)).toBe(day('2024-02-29'));
    expect(minusMonths(day('2026-01-15'), 3)).toBe(day('2025-10-15'));
    expect(minusMonths(day('2026-10-16'), 60)).toBe(day('2021-10-16'));
  });
});

describe('performance', () => {
  const bars = daily('2020-01-01', '2026-10-16');
  const rows = performance(bars);
  const close = (iso: string) => bars.find((b) => b.t === day(iso))!.c;
  const last = bars[bars.length - 1].c;

  it('measures from the close on the same calendar date back', () => {
    expect(get(rows, '1W')).toBeCloseTo((last / close('2026-10-09') - 1) * 100, 9);
    expect(get(rows, '1M')).toBeCloseTo((last / close('2026-09-16') - 1) * 100, 9);
    expect(get(rows, '1Y')).toBeCloseTo((last / close('2025-10-16') - 1) * 100, 9);
    expect(get(rows, '5Y')).toBeCloseTo((last / close('2021-10-16') - 1) * 100, 9);
  });

  it('measures the year to date from the previous year last close', () => {
    expect(get(rows, 'YTD')).toBeCloseTo((last / close('2025-12-31') - 1) * 100, 9);
  });

  it('uses the last bar on or before the date when the date has no bar', () => {
    const gappy = bars.filter((b) => b.t !== day('2026-09-16'));
    expect(get(performance(gappy), '1M')).toBeCloseTo((last / close('2026-09-15') - 1) * 100, 9);
  });

  it('is null where the stored bars begin after the period starts', () => {
    const short = performance(daily('2026-06-01', '2026-10-16'));
    expect(get(short, '1M')).not.toBeNull();
    expect(get(short, '3M')).not.toBeNull();
    expect(get(short, '6M')).toBeNull();
    expect(get(short, '1Y')).toBeNull();
    expect(get(short, 'YTD')).toBeNull();
  });

  it('takes a live price in place of the last close', () => {
    expect(get(performance(bars, 2 * last), '1W')).toBeCloseTo(((2 * last) / close('2026-10-09') - 1) * 100, 9);
    expect(get(performance(bars, 0), '1W')).toBeCloseTo((last / close('2026-10-09') - 1) * 100, 9);
  });

  it('is all null with fewer than two bars', () => {
    expect(performance([]).every((r) => r.pct === null)).toBe(true);
    expect(performance(bars.slice(0, 1)).every((r) => r.pct === null)).toBe(true);
  });
});
