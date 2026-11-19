import { describe, expect, it } from 'vitest';
import fixtures from './fixtures/data-source.cases.json';
import { badge, readDataSources } from '../src/provenance';

/** `fixtures/data-source.cases.json` is **not hand-written**: it is the `/api/data-source` payload as the terminal's own registry produces it (`terminal/src/snapshot.rs`, the test
 *  `the_registry_payloads_match_the_ui_fixtures`, which fails when the Rust output and this file differ and rewrites the file when `ATHENA_UPDATE_FIXTURES=1`). These tests read the same file, so
 *  the shell is checked against the real shape (per-dataset `data_as_of`, `recent`, `refresh_failures`, `snapshot_publication`), not a shape the page assumed. */
const CASES = fixtures as Record<string, unknown>;
const show = (name: string) => {
  expect(CASES[name], `fixture ${name} exists`).toBeTruthy();
  const sources = readDataSources(CASES[name]);
  expect(sources, `fixture ${name} is readable`).not.toBeNull();
  return badge(sources);
};

describe('the shell badge, from the registry payloads the terminal produces', () => {
  it('has every case the review asked for', () => {
    expect(Object.keys(CASES).sort()).toEqual([
      'failed_publication_main', 'failed_replacement_snapshot', 'locked_snapshot', 'main_missing_snapshot', 'main_only', 'main_unreadable_snapshot', 'mixed_generations', 'no_data',
      'price_cache_and_analysis_on_different_generations', 'price_cache_main', 'price_cache_no_data', 'price_cache_recovered', 'price_cache_retained_after_refresh_failure',
      'refresh_failure_at_window_boundary', 'refresh_failure_current_nothing_served_for_it', 'refresh_failure_old_no_recent_serve', 'refresh_failure_old_renewed_serving',
      'refresh_failure_past_window_boundary', 'refresh_failure_retained', 'stale_entry',
    ]);
  });

  it('is empty while the real file answers everywhere', () => {
    expect(show('main_only').text).toBe('');
  });

  it('says updating for a locked file, and states each dataset date, not one invented date', () => {
    const b = show('locked_snapshot');
    expect(b).toMatchObject({ text: '● snapshot while updating', cls: 'warn' });
    expect(b.title).toContain('daily bars to 13 Nov 2026');
    expect(b.title).toContain('index bars to 12 Nov 2026');
    expect(b.title).not.toContain('unknown date');
  });

  it('says unreadable and missing for those reasons, and not updating', () => {
    expect(show('main_unreadable_snapshot')).toMatchObject({ text: '● snapshot: warehouse file unreadable', cls: 'bad' });
    expect(show('main_missing_snapshot')).toMatchObject({ text: '● snapshot: warehouse file missing', cls: 'bad' });
  });

  it('keeps a failed replacement visible, with a snapshot answering and with the real file answering', () => {
    expect(show('failed_replacement_snapshot').title).toContain('A newer copy failed (validate)');
    const b = show('failed_publication_main');
    expect(b).toMatchObject({ text: '● snapshot copy failed', cls: 'warn' });
    expect(b.title).toContain('hash mismatch');
  });

  it('reports no data as a fault, with both reasons', () => {
    const b = show('no_data');
    expect(b).toMatchObject({ text: '● warehouse unavailable', cls: 'bad' });
    expect(b.title).toContain('absent');
  });

  it('names the generations and the readers on them when answers differ', () => {
    const b = show('mixed_generations');
    expect(b.text).toBe('● mixed data generations');
    expect(b.title).toMatch(/bars:ZMIXA:adjusted/);
    expect(b.title).toMatch(/bars:ZMIXB:adjusted/);
  });

  it('shows a failed refresh beside the cached data that is still served', () => {
    const b = show('refresh_failure_retained');
    expect(b.text).toBe('● data refresh failed');
    expect(b.title).toContain('search');
  });

  it('ignores an entry the server marks as no longer recent', () => {
    expect(show('stale_entry').text).toBe('');
  });

  it('every entry carries the fields the badge relies on', () => {
    for (const [name, payload] of Object.entries(CASES)) {
      const p = payload as { readers: Record<string, Record<string, unknown>>; refresh_failures: Record<string, Record<string, unknown>> };
      for (const [key, entry] of Object.entries(p.readers)) {
        expect(typeof entry.at, `${name}/${key} at`).toBe('number');
        expect(typeof entry.recent, `${name}/${key} recent`).toBe('boolean');
        expect(typeof entry.reader, `${name}/${key} reader`).toBe('string');
      }
      // T5-F2: a refresh failure says how old it is and whether it is recent, by the same clock and window as a served answer
      for (const [key, f] of Object.entries(p.refresh_failures)) {
        expect(typeof f.at, `${name}/${key} failure at`).toBe('number');
        expect(typeof f.age_seconds, `${name}/${key} failure age_seconds`).toBe('number');
        expect(typeof f.recent, `${name}/${key} failure recent`).toBe('boolean');
        expect(typeof f.reader, `${name}/${key} failure reader`).toBe('string');
      }
    }
  });
});

describe('T5-F2: a failed refresh is current only while it is recent, and says retained data is served only when a matching recent answer was', () => {
  it('an old failure and nothing served recently is history: no badge', () => {
    expect(show('refresh_failure_old_no_recent_serve').text).toBe('');
  });

  it('an old failure while the cache kept being served is history too: no badge, and the served entry is recent', () => {
    expect(show('refresh_failure_old_renewed_serving').text).toBe('');
    const p = CASES.refresh_failure_old_renewed_serving as { readers: { search: { recent: boolean } }; refresh_failures: { search: { recent: boolean } } };
    expect([p.readers.search.recent, p.refresh_failures.search.recent]).toEqual([true, false]);
  });

  it('expires exactly at the window boundary: still current at the window, history one second later', () => {
    const at = show('refresh_failure_at_window_boundary');
    expect(at.text).toBe('● data refresh failed');
    expect(at.title).toContain('still serving earlier data');
    expect(show('refresh_failure_past_window_boundary').text).toBe('');
  });

  it('a current failure with retained data says so, and says which data', () => {
    const b = show('refresh_failure_retained');
    expect(b.text).toBe('● data refresh failed');
    expect(b.title).toContain('search: could not refresh');
    expect(b.title).toContain('still serving earlier data');
  });

  it('a current failure with no matching recent served answer does not claim retained data is being served', () => {
    const b = show('refresh_failure_current_nothing_served_for_it');
    expect(b.text).toBe('● data refresh failed');
    expect(b.title).not.toContain('still serving');
    expect(b.title).toContain('no recent answer from earlier data is on record');
  });
});

describe('T5-F1: the Fundamentals page price cache, from the registry entries the cache code itself writes', () => {
  it('is quiet while the cache serves what it read from the real file, and after a recovery', () => {
    expect(show('price_cache_main').text).toBe('');
    expect(show('price_cache_recovered').text).toBe('');
  });

  it('names its own reader (never screen) and the retained data while a refresh is failing', () => {
    const b = show('price_cache_retained_after_refresh_failure');
    expect(b.text).toBe('● data refresh failed');
    expect(b.title).toContain('fx-price-main: could not refresh');
    expect(b.title).toContain('still serving earlier data');
    expect(b.title).toContain('cached, computed 30s before');
    expect(b.title).not.toContain('screen');
  });

  it('reports cached prices and another reader on different generations as mixed, naming both readers', () => {
    const b = show('price_cache_and_analysis_on_different_generations');
    expect(b.text).toBe('● mixed data generations');
    expect(b.title).toContain('fx-price-mixed');
    expect(b.title).toContain('analysis');
  });

  it('reports no cached price and no readable warehouse as a fault with both reasons', () => {
    const b = show('price_cache_no_data');
    expect(b).toMatchObject({ text: '● warehouse unavailable', cls: 'bad' });
    expect(b.title).toContain('fx-price-none');
    expect(b.title).toContain('absent');
  });

  it('the cached price entry carries its cache age and the date its closes stop at', () => {
    const p = CASES.price_cache_retained_after_refresh_failure as { readers: Record<string, { cache: { hit: boolean; age_seconds: number }; data_as_of: string; reader: string }> };
    const e = p.readers['fx-price-main'];
    expect([e.reader, e.cache.hit, e.cache.age_seconds, e.data_as_of]).toEqual(['fx-price-main', true, 30, '2026-10-16']);
  });
});
