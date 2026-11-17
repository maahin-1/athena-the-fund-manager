import { describe, expect, it } from 'vitest';
import { badge, day, provenanceNote, readDataSources, readProvenance, when } from '../src/provenance';

const MAIN = { source: 'main', generation_id: 'main:1000:5', snapshot_created_at: null, data_as_of: '2026-11-13', fallback_reason: null, snapshot_status: null };
const SNAP = {
  source: 'snapshot', generation_id: '20261115T101500Z-a1b2c3', snapshot_created_at: '2026-11-15T10:15:00Z', data_as_of: '2026-11-13', fallback_reason: 'locked_by_writer',
  snapshot_status: { last_attempt: { outcome: 'published' }, current_generation: '20261115T101500Z-a1b2c3' },
};

describe('what the server reported', () => {
  it('reads a real-file answer, a snapshot answer and a failure, and says none for anything else', () => {
    expect(readProvenance(MAIN)).toMatchObject({ kind: 'ok', value: { source: 'main' } });
    expect(readProvenance(SNAP)).toMatchObject({ kind: 'ok', value: { source: 'snapshot', fallback_reason: 'locked_by_writer' } });
    // (intentional change, T5-R3/R4: a failure also says whether it is recent, so a failure nothing has repeated for longer than the server's window is not shown as current)
    expect(readProvenance({ failure: { main_reason: 'main_unreadable', snapshot_reason: 'absent' } })).toEqual({ kind: 'failure', value: { main_reason: 'main_unreadable', snapshot_reason: 'absent', recent: true } });
    expect(readProvenance({ failure: { main_reason: 'main_unreadable', snapshot_reason: 'absent' }, recent: false })).toMatchObject({ kind: 'failure', value: { recent: false } });
    for (const bad of [null, undefined, 3, 'x', [], {}, { source: 'elsewhere', generation_id: 'x' }, { source: 'main' }]) expect(readProvenance(bad)).toEqual({ kind: 'none' });
  });

  it('formats times and dates without guessing', () => {
    expect(when('2026-11-15T10:15:00Z')).toBe('15 Nov 10:15 UTC');
    expect(day('2026-11-13')).toBe('13 Nov 2026');
    expect(when(null)).toBe('an unknown time');
    expect(day('whenever')).toBe('whenever');
  });
});

describe('the note a panel shows', () => {
  it('is empty when the real file answered and when the server said nothing', () => {
    expect(provenanceNote(MAIN)).toBe('');
    expect(provenanceNote(undefined)).toBe('');
    expect(provenanceNote(null)).toBe('');
  });

  it('names the reason, when the copy was taken and where its data stops, as two different facts', () => {
    const note = provenanceNote(SNAP);
    expect(note).toBe('Snapshot, because a build is updating the warehouse: copy taken 15 Nov 10:15 UTC, data to 13 Nov 2026.');
  });

  it('says other reasons in their own words and a newer failed copy after the date', () => {
    expect(provenanceNote({ ...SNAP, fallback_reason: 'main_unreadable' })).toContain('because the warehouse file could not be read');
    expect(provenanceNote({ ...SNAP, fallback_reason: 'main_missing' })).toContain('because the warehouse file is missing');
    const failed = provenanceNote({ ...SNAP, snapshot_status: { last_attempt: { outcome: 'failed', stage: 'validate' } } });
    expect(failed).toContain('A newer copy failed (validate).');
  });

  it('says when no data could be used, and why, instead of naming a source', () => {
    const note = provenanceNote({ failure: { main_reason: 'locked_by_writer', snapshot_reason: 'hash_mismatch' } });
    expect(note).toBe('No warehouse data: a build is updating the warehouse, and no validated snapshot is available (hash mismatch).');
  });
});

describe('the data badge of the shell', () => {
  const sources = (readers: Record<string, unknown>, mixed = false, generations: string[] = []) => readDataSources({ readers, mixed_generations: mixed, recent_generations: generations, window_seconds: 600 });

  it('is empty while every reader has the real file, when nothing is known, and for a malformed answer', () => {
    expect(badge(sources({ bars: MAIN, calendar: MAIN })).text).toBe('');
    expect(badge(null).text).toBe('');
    expect(readDataSources({ readers: [] })).toBeNull();
    expect(readDataSources('nope')).toBeNull();
  });

  it('warns that a snapshot is answering, with the two dates in its title', () => {
    const b = badge(sources({ bars: SNAP, calendar: SNAP }, false, [SNAP.generation_id]));
    expect(b).toMatchObject({ text: '● snapshot while updating', cls: 'warn' });
    expect(b.title).toContain('Copy taken 15 Nov 10:15 UTC, data to 13 Nov 2026.');
    expect(b.title).toContain('bars: snapshot 20261115T101500Z-a1b2c3');
  });

  it('says so when panels read different generations, naming them', () => {
    const b = badge(sources({ bars: MAIN, calendar: SNAP }, true, ['main:1000:5', SNAP.generation_id]));
    expect(b).toMatchObject({ text: '● mixed data generations', cls: 'warn' });
    expect(b.title).toContain('main:1000:5');
    expect(b.title).toContain(SNAP.generation_id);
  });

  it('reports a fault, above the other states, when a reader could get no data', () => {
    const b = badge(sources({ bars: SNAP, search: { failure: { main_reason: 'main_unreadable', snapshot_reason: 'absent' } } }, true, ['a', 'b']));
    expect(b).toMatchObject({ text: '● warehouse unavailable', cls: 'bad' });
    expect(b.title).toContain('search: no data (absent)');
  });

  it('does not claim anything for a reader that reported nothing', () => {
    const b = badge(sources({ bars: MAIN, odd: { unexpected: true } }));
    expect(b.text).toBe('');
  });
});

// ---- T5-R4: the shape the registry really has (a per-dataset object of dates), and wording that does not claim more than is known ----
const REG = {
  source: 'snapshot', source_path: 'w.snap.20261115T101500Z-a1b2c3.duckdb', generation_id: '20261115T101500Z-a1b2c3', generation_basis: 'manifest', snapshot_created_at: '2026-11-15T10:15:00Z',
  data_as_of: { daily_bars: '2026-11-13', index_bars: '2026-11-12' }, fallback_reason: 'locked_by_writer', validated: true,
  snapshot_status: { last_attempt: { outcome: 'published' }, current_generation: '20261115T101500Z-a1b2c3' }, main_error: 'IO Error', reader: 'calendar', subject: null, at: 1_794_000_000, age_seconds: 3, recent: true,
};
const reg = (extra: Record<string, unknown> = {}) => ({ ...REG, ...extra });
const payload = (readers: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  readDataSources({ readers, mixed_generations: false, recent_generations: [REG.generation_id], window_seconds: 600, refresh_failures: {}, snapshot_publication: null, ...extra });

describe('registry-shaped payloads (data_as_of is an object of dates per dataset)', () => {
  it('reads the per-dataset dates and states each one, never a single invented date', () => {
    const r = readProvenance(REG);
    expect(r).toMatchObject({ kind: 'ok', value: { source: 'snapshot' } });
    const b = badge(payload({ calendar: REG }));
    expect(b.title).toContain('daily bars to 13 Nov 2026');
    expect(b.title).toContain('index bars to 12 Nov 2026');
    expect(b.title).not.toContain('unknown date');
    expect(b.title).toContain('Copy taken 15 Nov 10:15 UTC');
  });

  it('says a dataset with no recorded date has none, instead of leaving it out or filling it in', () => {
    const b = badge(payload({ calendar: reg({ data_as_of: { daily_bars: '2026-11-13', index_bars: null } }) }));
    expect(b.title).toContain('daily bars to 13 Nov 2026');
    expect(b.title).toContain('index bars: no date recorded');
  });

  it('a daily answer states its own date and keeps the manifest dates apart', () => {
    const note = provenanceNote(reg({ data_as_of: '2026-11-13', snapshot_data_as_of: { daily_bars: '2026-11-12', index_bars: null } }));
    expect(note).toContain('data to 13 Nov 2026');
    expect(note).not.toContain('unknown');
  });

  it('a registry entry with no dates at all says the date is unknown', () => {
    expect(badge(payload({ calendar: reg({ data_as_of: null }) })).title).toContain('data date unknown');
  });
});

describe('fallback wording', () => {
  const text = (extra: Record<string, unknown>) => badge(payload({ calendar: reg(extra) }));

  it('says updating only for an identified writer lock', () => {
    expect(text({ fallback_reason: 'locked_by_writer' }).text).toBe('● snapshot while updating');
    for (const reason of ['main_unreadable', 'main_missing', null, 'something_new']) expect(text({ fallback_reason: reason }).text, String(reason)).not.toContain('updating');
  });

  it('says the file is unreadable or missing, as a fault, when that is the reason', () => {
    expect(text({ fallback_reason: 'main_unreadable' })).toMatchObject({ text: '● snapshot: warehouse file unreadable', cls: 'bad' });
    expect(text({ fallback_reason: 'main_missing' })).toMatchObject({ text: '● snapshot: warehouse file missing', cls: 'bad' });
    expect(text({ fallback_reason: null })).toMatchObject({ text: '● snapshot in use', cls: 'warn' });
  });

  it('names the worst reason when readers differ', () => {
    const b = badge(payload({ calendar: reg({ fallback_reason: 'locked_by_writer' }), screen: reg({ fallback_reason: 'main_unreadable', reader: 'screen' }) }));
    expect(b.text).toBe('● snapshot: warehouse file unreadable');
    expect(b.title).toContain('calendar');
    expect(b.title).toContain('screen');
  });
});

describe('a failed replacement copy stays visible in the shell', () => {
  const failed = { last_attempt: { outcome: 'failed', stage: 'validate', error: 'OSError: copy would not open', at: '2026-11-15T10:30:00Z', current_unusable: 'hash_mismatch' }, last_success: { generation_id: REG.generation_id }, current_generation: null };

  it('is in the title while a snapshot answers', () => {
    const b = badge(payload({ calendar: reg({ snapshot_status: failed }) }));
    expect(b.title).toContain('A newer copy failed (validate)');
  });

  it('is a warning of its own while the real file answers, with the damaged generation named', () => {
    const b = badge(payload({ calendar: MAIN }, { snapshot_publication: failed }));
    expect(b).toMatchObject({ text: '● snapshot copy failed', cls: 'warn' });
    expect(b.title).toContain('validate');
    expect(b.title).toContain('hash mismatch');
  });

  it('stays quiet when the last copy succeeded', () => {
    expect(badge(payload({ calendar: MAIN }, { snapshot_publication: { last_attempt: { outcome: 'published' } } })).text).toBe('');
  });
});

// ---- T5-F2: refresh failures have an age and a recent flag; "still serving earlier data" needs a matching recent served answer ----
const FAILED = { failure: { main_reason: 'main_unreadable', snapshot_reason: 'absent' }, reader: 'search', at: 1_794_000_000, age_seconds: 5, recent: true };
const SERVED_MAIN = reg({ reader: 'search', source: 'main', generation_id: 'main:1000:5', fallback_reason: null, data_as_of: '2026-11-13', at: 1_794_000_001 });
const failing = (failure: Record<string, unknown>, readers: Record<string, unknown>) => badge(payload(readers, { refresh_failures: { search: failure } }));

describe('refresh failures: current or history', () => {
  it('a failure the server did not mark recent is not current (a missing flag is not read as recent)', () => {
    const { recent: _recent, age_seconds: _age, ...bare } = FAILED;
    expect(failing(bare, { search: SERVED_MAIN }).text).toBe('');
    // the pure formatter probe of the review: an ancient failure, nothing served recently, an empty list of recent generations
    const probe = readDataSources({ readers: {}, mixed_generations: false, recent_generations: [], window_seconds: 600, refresh_failures: { search: { failure: FAILED.failure, at: 1 } }, snapshot_publication: null });
    expect(badge(probe).text).toBe('');
  });

  it('an old failure (recent: false) is history even while the cache keeps being served', () => {
    expect(failing({ ...FAILED, age_seconds: 4_000, recent: false }, { search: SERVED_MAIN }).text).toBe('');
    expect(failing({ ...FAILED, age_seconds: 4_000, recent: false }, {}).text).toBe('');
  });

  it('a current failure with a matching recent served answer says which earlier data is being served', () => {
    const b = failing(FAILED, { search: reg({ ...SERVED_MAIN, cache: { hit: true, age_seconds: 42 } }) });
    expect(b).toMatchObject({ text: '● data refresh failed', cls: 'warn' });
    expect(b.title).toContain('still serving earlier data');
    expect(b.title).toContain('cached, computed 42s before');
  });

  // (T3 follow-up, the survivor of the T5-F mutation rerun `ts-retained-data-not-described`: the title repeats every answer's cache age in its general list of lines, so a title that merely contains
  // "cached, computed 42s before" proves nothing about the retained-data clause. The clause is the first line, so it is compared on its own, with cache ages that are set by the test.)
  it('the retained-data clause names each served answer with its own cache age, and names none where the answer was not from the cache', () => {
    const clause = (readers: Record<string, unknown>) => failing(FAILED, readers).title!.split('\n')[0];
    const lead = /^search: could not refresh \(.*\); still serving earlier data \(/;
    const hit = (age: number) => reg({ ...SERVED_MAIN, cache: { hit: true, age_seconds: age } });
    // one served answer from the cache, computed 42 s before
    expect(clause({ search: hit(42) })).toMatch(new RegExp(`${lead.source}search: the warehouse file \\(cached, computed 42s before\\)\\)$`));
    // the age is the one the test set, not another number
    expect(clause({ search: hit(7) })).toMatch(/search: the warehouse file \(cached, computed 7s before\)\)$/);
    // an answer that was not served from the cache says nothing of a cache, whatever age the field carries
    expect(clause({ search: reg({ ...SERVED_MAIN, cache: { hit: false, age_seconds: 42 } }) })).toMatch(new RegExp(`${lead.source}search: the warehouse file\\)$`));
    expect(clause({ search: SERVED_MAIN })).toMatch(new RegExp(`${lead.source}search: the warehouse file\\)$`));
    expect(failing(FAILED, { search: SERVED_MAIN }).title).not.toContain('cached');
    // two answers of the same reader, each with its own age, in the order the server listed them
    expect(clause({ search: hit(7), 'search:extra': hit(90) })).toMatch(/still serving earlier data \(search: the warehouse file \(cached, computed 7s before\); search:extra: the warehouse file \(cached, computed 90s before\)\)$/);
  });

  it('a current failure with no matching recent served answer does not claim retained data is served', () => {
    const other = (reader: string) => ({ ...SERVED_MAIN, reader }); // another reader answering from the real file
    for (const readers of [{}, { calendar: other('calendar') }, { search: { ...SERVED_MAIN, recent: false } }, { 'bars:SBIN:adjusted': other('bars') }]) {
      const b = failing(FAILED, readers);
      expect(b.text).toBe('● data refresh failed');
      expect(b.title).not.toContain('still serving');
      expect(b.title).toContain('no recent answer from earlier data is on record');
    }
  });

  it('a served answer older than the failure does not support the claim, one at the same second does', () => {
    expect(failing(FAILED, { search: { ...SERVED_MAIN, at: FAILED.at - 1 } }).title).not.toContain('still serving');
    expect(failing(FAILED, { search: { ...SERVED_MAIN, at: FAILED.at } }).title).toContain('still serving earlier data');
  });

  it('the matching answer is found by its reader name, from the key when an older payload has no reader field', () => {
    const { reader: _r, ...noReader } = SERVED_MAIN;
    expect(failing(FAILED, { search: noReader }).title).toContain('still serving earlier data');
    expect(failing(FAILED, { 'search:extra': noReader }).title).toContain('still serving earlier data');
    expect(failing(FAILED, { searching: noReader }).title).not.toContain('still serving');
  });

  it('a served answer that is itself a failure is not retained data', () => {
    const b = failing(FAILED, { search: { failure: { main_reason: 'main_unreadable', snapshot_reason: 'absent' }, reader: 'search', at: 1_794_000_001, recent: true } });
    expect(b.text).toBe('● warehouse unavailable');
  });

  it('reads the age of a refresh failure', () => {
    const s = readDataSources({ readers: {}, mixed_generations: false, recent_generations: [], window_seconds: 600, refresh_failures: { search: FAILED }, snapshot_publication: null });
    expect(s?.refreshFailures.search).toMatchObject({ recent: true, at: 1_794_000_000, ageSeconds: 5 });
  });
});

describe('a cache that kept serving, a refresh that failed, and entries that are no longer recent', () => {
  it('shows a current refresh failure beside the data that is still being served', () => {
    // (intentional change, T5-F2: the failure carries its own age and recent, as the registry now sends them; a failure with neither says nothing about being current)
    const b = badge(payload({ search: reg({ reader: 'search', source: 'main', fallback_reason: null, data_as_of: '2026-11-13' }) }, { refresh_failures: { search: FAILED } }));
    expect(b).toMatchObject({ text: '● data refresh failed', cls: 'warn' });
    expect(b.title).toContain('search');
    expect(b.title).toContain('main unreadable');
    expect(b.title).toContain('still serving earlier data');
  });

  it('ignores an entry the server marks as no longer recent', () => {
    expect(badge(payload({ calendar: reg({ recent: false }) })).text).toBe('');
    expect(badge(payload({ calendar: reg({ recent: false }), screen: MAIN })).text).toBe('');
  });

  it('names each generation with the readers on it when they are mixed', () => {
    const other = '20261115T102000Z-bbbbbb';
    const b = badge(payload({ 'bars:SBIN:adjusted': reg({ reader: 'bars', subject: 'SBIN:adjusted' }), 'bars:TCS:adjusted': reg({ reader: 'bars', subject: 'TCS:adjusted', generation_id: other }) }, { mixed_generations: true, recent_generations: [REG.generation_id, other] }));
    expect(b.text).toBe('● mixed data generations');
    expect(b.title).toContain(`${REG.generation_id}: bars:SBIN:adjusted`);
    expect(b.title).toContain(`${other}: bars:TCS:adjusted`);
  });
});
