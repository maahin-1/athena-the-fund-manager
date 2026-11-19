/** Which data a panel is showing: the real warehouse file, or a snapshot generation taken while a build holds it (audit T5, WP07).
 *
 * The server says it: every daily answer carries `provenance`, and `/api/data-source` says what the terminal recently *served* (by reader and subject), whether those answers came from different
 * generations, which refreshes failed while older data kept being served, and how the last attempt to copy the warehouse ended. Nothing here decides which data is used. It states what the server
 * reported, and says so when the server reported nothing. The date a copy was taken (`snapshot_created_at`) and the date its data stops at (`data_as_of`) are different facts and are always shown as
 * two; `data_as_of` is a single date when an answer states its own (the last bar it returned) and a per-dataset object when it is a manifest's, and a dataset's date is never replaced by another's. */

export interface SnapshotStatus {
  last_attempt?: { outcome?: string; stage?: string | null; error?: string | null; at?: string; current_unusable?: string | null };
  last_success?: { generation_id?: string; at?: string } | null;
  current_generation?: string | null;
}

/** One date (an answer's own), or a date per dataset (a snapshot manifest's; a dataset with no date is `null`), or nothing known. */
export type DataAsOf = string | Record<string, string | null> | null;

export interface Provenance {
  source: 'main' | 'snapshot' | 'memory';
  generation_id: string;
  snapshot_created_at: string | null;
  /** The date(s) the data this answer carries stops at. */
  data_as_of: DataAsOf;
  /** The manifest's per-dataset dates, kept beside an answer's own date. */
  snapshot_data_as_of: DataAsOf;
  fallback_reason: string | null;
  snapshot_status: SnapshotStatus | null;
  /** Served within the server's window; false for an entry nothing has served for longer than that. Absent means recent (an answer carried by a panel is the current one). */
  recent: boolean;
  /** Present when an answer was served from a cache: how old it was when computed. */
  cache: { hit: boolean; age_seconds: number } | null;
}

export interface Failure {
  main_reason: string;
  snapshot_reason: string;
  recent: boolean;
}

/** A failed refresh of data that kept being served. `recent` is the server's own judgement (same clock and window as a served answer); it is **never** assumed when the server did not send it,
 *  because a failure nothing has repeated for longer than the window is history, not current activity. */
export interface RefreshFailure extends Failure {
  /** When the refresh last failed (seconds since the epoch), as the server recorded it. */
  at: number | null;
  ageSeconds: number | null;
}

export type Reported = { kind: 'ok'; value: Provenance } | { kind: 'failure'; value: Failure } | { kind: 'none' };

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const words = (s: string): string => s.replace(/_/g, ' ');

/** `2026-11-15T10:15:00Z` -> `15 Nov 10:15 UTC`. Anything else is shown as it came, never guessed. */
export function when(iso: string | null): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso) : null;
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]} UTC` : iso ?? 'an unknown time';
}

/** `2026-11-13` -> `13 Nov 2026`. */
export function day(iso: string | null): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null;
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : iso ?? 'an unknown date';
}

function readDataAsOf(v: unknown): DataAsOf {
  if (typeof v === 'string') return v || null;
  if (isObject(v)) {
    const out: Record<string, string | null> = {};
    for (const [k, d] of Object.entries(v)) out[k] = str(d);
    return Object.keys(out).length ? out : null;
  }
  return null;
}

/** Where the data stops: one sentence fragment. A single date for an answer that states its own; each dataset's own date (or that it has none) for a manifest's; never one date made up for several. */
export function datesText(v: DataAsOf): string {
  if (typeof v === 'string') return `data to ${day(v)}`;
  if (v && Object.keys(v).length) {
    return `data: ${Object.entries(v).map(([k, d]) => (d ? `${words(k)} to ${day(d)}` : `${words(k)}: no date recorded`)).join('; ')}`;
  }
  return 'data date unknown';
}

export function readProvenance(raw: unknown): Reported {
  if (!isObject(raw)) return { kind: 'none' };
  const recent = raw.recent !== false;
  if (isObject(raw.failure)) {
    return { kind: 'failure', value: { main_reason: str(raw.failure.main_reason) ?? 'unknown', snapshot_reason: str(raw.failure.snapshot_reason) ?? 'unknown', recent } };
  }
  const source = raw.source;
  const id = str(raw.generation_id);
  if ((source !== 'main' && source !== 'snapshot' && source !== 'memory') || !id) return { kind: 'none' };
  const status = isObject(raw.snapshot_status) ? (raw.snapshot_status as SnapshotStatus) : null;
  const cache = isObject(raw.cache) && typeof raw.cache.hit === 'boolean' ? { hit: raw.cache.hit, age_seconds: typeof raw.cache.age_seconds === 'number' ? raw.cache.age_seconds : 0 } : null;
  return {
    kind: 'ok',
    value: { source, generation_id: id, snapshot_created_at: str(raw.snapshot_created_at), data_as_of: readDataAsOf(raw.data_as_of), snapshot_data_as_of: readDataAsOf(raw.snapshot_data_as_of), fallback_reason: str(raw.fallback_reason), snapshot_status: status, recent, cache },
  };
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** A refresh failure as the registry reports it. Unlike a served entry (an answer a panel carries is the current one), a missing `recent` here means *not established*, so it is not current. */
export function readRefreshFailure(raw: unknown): RefreshFailure | null {
  const r = readProvenance(raw);
  if (r.kind !== 'failure' || !isObject(raw)) return null;
  return { ...r.value, recent: raw.recent === true, at: num(raw.at), ageSeconds: num(raw.age_seconds) };
}

const WHY: Record<string, string> = {
  locked_by_writer: 'a build is updating the warehouse',
  main_missing: 'the warehouse file is missing',
  main_unreadable: 'the warehouse file could not be read',
};

const newerFailed = (s: SnapshotStatus | null): string => (s?.last_attempt?.outcome === 'failed' ? ` A newer copy failed (${s.last_attempt.stage ?? 'unknown stage'}).` : '');

/** A sentence for a panel's note, or '' when the real file answered (nothing to say) or the server said nothing. */
export function provenanceNote(raw: unknown): string {
  const r = readProvenance(raw);
  if (r.kind === 'failure') {
    return `No warehouse data: ${WHY[r.value.main_reason] ?? r.value.main_reason}, and no validated snapshot is available (${words(r.value.snapshot_reason)}).`;
  }
  if (r.kind !== 'ok' || r.value.source !== 'snapshot') return '';
  const p = r.value;
  const why = p.fallback_reason ? WHY[p.fallback_reason] ?? p.fallback_reason : 'the warehouse could not be used';
  return `Snapshot, because ${why}: copy taken ${when(p.snapshot_created_at)}, ${datesText(p.data_as_of ?? p.snapshot_data_as_of)}.${newerFailed(p.snapshot_status)}`;
}

export interface DataSources {
  readers: Record<string, unknown>;
  mixed: boolean;
  generations: string[];
  /** Refreshes that failed while older data kept being served, by reader. */
  refreshFailures: Record<string, RefreshFailure>;
  /** How the last attempt to copy the warehouse ended (from the status file), whichever source is answering. */
  publication: SnapshotStatus | null;
}

export function readDataSources(raw: unknown): DataSources | null {
  if (!isObject(raw) || !isObject(raw.readers) || !Array.isArray(raw.recent_generations)) return null;
  const refreshFailures: Record<string, RefreshFailure> = {};
  if (isObject(raw.refresh_failures)) {
    for (const [name, v] of Object.entries(raw.refresh_failures)) {
      const f = readRefreshFailure(v);
      if (f) refreshFailures[name] = f;
    }
  }
  return {
    readers: raw.readers,
    mixed: raw.mixed_generations === true,
    generations: raw.recent_generations.filter((g): g is string => typeof g === 'string'),
    refreshFailures,
    publication: isObject(raw.snapshot_publication) ? (raw.snapshot_publication as SnapshotStatus) : null,
  };
}

export interface Badge {
  text: string;
  cls: '' | 'warn' | 'bad';
  title: string;
}

const EMPTY: Badge = { text: '', cls: '', title: '' };

// the worse a reason for leaving the real file, the higher it ranks when readers differ
const RANK: Record<string, number> = { main_unreadable: 3, main_missing: 2, locked_by_writer: 1 };

/** The badge text for a snapshot answering: "updating" only when the reason is an identified writer lock; a file that cannot be read or is missing is said to be that. */
function snapshotBadge(reason: string | null): { text: string; cls: 'warn' | 'bad' } {
  if (reason === 'locked_by_writer') return { text: '● snapshot while updating', cls: 'warn' };
  if (reason === 'main_unreadable') return { text: '● snapshot: warehouse file unreadable', cls: 'bad' };
  if (reason === 'main_missing') return { text: '● snapshot: warehouse file missing', cls: 'bad' };
  return { text: '● snapshot in use', cls: 'warn' };
}

/** The reader an entry belongs to: the server's `reader` field, else the part of the key before the subject (`bars:SBIN:adjusted` -> `bars`). */
const readerOf = (key: string, raw: unknown): string => (isObject(raw) && str(raw.reader)) || key.split(':')[0];

const cacheText =(p: Provenance): string => (p.cache?.hit ? ` (cached, computed ${p.cache.age_seconds}s before)` : '');

function publicationLine(s: SnapshotStatus): string {
  const a = s.last_attempt ?? {};
  const unusable = a.current_unusable ? `; the copy in place is unusable (${words(a.current_unusable)})` : '';
  return `The last attempt to copy the warehouse failed at ${a.stage ?? 'an unknown stage'}${a.error ? ` (${a.error})` : ''}${unusable}.`;
}

/** The shell's data badge: empty while every reader recently served the real file and agrees; a warning when a snapshot answers, readers are on different generations, a refresh failed or the last copy
 *  failed; a fault when something could not be read. Entries the server marks as no longer recent are not described. */
export function badge(sources: DataSources | null): Badge {
  if (!sources) return EMPTY;
  const entries = Object.entries(sources.readers)
    .map(([name, raw]) => ({ name, r: readProvenance(raw), reader: readerOf(name, raw), at: isObject(raw) ? num(raw.at) : null }))
    .filter((e) => e.r.kind === 'none' || e.r.value.recent);
  const lines = entries.map(({ name, r }) =>
    r.kind === 'ok' ? `${name}: ${r.value.source === 'snapshot' ? `snapshot ${r.value.generation_id}` : 'the warehouse file'}${cacheText(r.value)}` : r.kind === 'failure' ? `${name}: no data (${words(r.value.snapshot_reason)})` : `${name}: not reported`,
  );
  const snaps = entries.flatMap(({ name, r }) => (r.kind === 'ok' && r.value.source === 'snapshot' ? [{ name, p: r.value }] : []));
  const failedNewer = snaps.map((s) => s.p.snapshot_status).find((s) => s?.last_attempt?.outcome === 'failed') ?? null;
  const tail = [failedNewer ? newerFailed(failedNewer).trim() : '', sources.publication?.last_attempt?.outcome === 'failed' && !failedNewer ? publicationLine(sources.publication) : ''].filter(Boolean);
  if (entries.some(({ r }) => r.kind === 'failure')) return { text: '● warehouse unavailable', cls: 'bad', title: [...lines, ...tail].join('\n') };
  if (sources.mixed) {
    const byGeneration = new Map<string, string[]>();
    for (const { name, r } of entries) if (r.kind === 'ok') byGeneration.set(r.value.generation_id, [...(byGeneration.get(r.value.generation_id) ?? []), name]);
    const groups = [...byGeneration].map(([g, names]) => `${g}: ${names.join(', ')}`);
    return { text: '● mixed data generations', cls: 'warn', title: [`Panels recently read different data: ${sources.generations.join(', ')}`, ...groups, ...lines, ...tail].join('\n') };
  }
  if (snaps.length) {
    const worst = snaps.reduce((a, b) => ((RANK[b.p.fallback_reason ?? ''] ?? 0) > (RANK[a.p.fallback_reason ?? ''] ?? 0) ? b : a));
    const b = snapshotBadge(worst.p.fallback_reason);
    // each snapshot answer states its own reason and its own dates: the head line gives the worst one's copy time, and no entry's dates stand in for another's
    const why = snaps.map((s) => `${s.name}: ${s.p.fallback_reason ? WHY[s.p.fallback_reason] ?? s.p.fallback_reason : 'the warehouse could not be used'}; ${datesText(s.p.data_as_of ?? s.p.snapshot_data_as_of)}`);
    return { ...b, title: [`Copy taken ${when(worst.p.snapshot_created_at)}, ${datesText(worst.p.data_as_of ?? worst.p.snapshot_data_as_of)}.`, ...why, ...lines, ...tail].join('\n') };
  }
  // A refresh failure is current only while the server says it is recent (an older one is history and is not shown as activity). "Earlier data is still being served" is said only when a recent
  // answer of the same reader, given after the failure, is on record: otherwise the failure is reported without saying what, if anything, is being served.
  const refresh = Object.entries(sources.refreshFailures).filter(([, f]) => f.recent);
  if (refresh.length) {
    const what = refresh.map(([name, f]) => {
      const served = entries.filter((e) => e.reader === name && e.r.kind === 'ok' && (f.at === null || e.at === null || e.at >= f.at));
      const head = `${name}: could not refresh (${words(f.main_reason)}; no snapshot: ${words(f.snapshot_reason)})`;
      if (!served.length) return `${head}; no recent answer from earlier data is on record`;
      const from = served.map((e) => (e.r.kind === 'ok' ? `${e.name}: ${e.r.value.source === 'snapshot' ? `snapshot ${e.r.value.generation_id}` : 'the warehouse file'}${cacheText(e.r.value)}` : '')).join('; ');
      return `${head}; still serving earlier data (${from})`;
    });
    return { text: '● data refresh failed', cls: 'warn', title: [...what, ...lines].join('\n') };
  }
  if (sources.publication?.last_attempt?.outcome === 'failed') return { text: '● snapshot copy failed', cls: 'warn', title: [publicationLine(sources.publication), ...lines].join('\n') };
  return EMPTY;
}
