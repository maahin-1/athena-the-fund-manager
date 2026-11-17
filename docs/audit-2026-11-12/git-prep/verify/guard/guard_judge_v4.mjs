// Reads a guard v4 hit log (JSON lines) and prints ONE JSON object: the facts the web gate turns into checks. It judges nothing about the tests themselves; it only says what the log contains and
// whether the log is complete (no overflow, no write failure, no re-entry, nothing unparseable). usage: node guard_judge_v4.mjs <guard-hits.jsonl> [<the log file's own canonical path, lower-case, forward slashes>]
import fs from 'node:fs';

const file = process.argv[2];
const selfPath = process.argv[3] ? process.argv[3].toLowerCase() : null;
if (!file) { console.error('usage: node guard_judge_v4.mjs <guard-hits.jsonl> [<own log path>]'); process.exit(2); }
const INTERNAL = new Set(['guard-config', 'guard-installed', 'guard-summary', 'guard-reinstall-ignored', 'log-overflow', 'log-write-failure', 'log-reentry']);
const out = {
  file, exists: fs.existsSync(file), lines: 0, unparseable_lines: 0, by_kind: {},
  configs: 0, installed: 0, summaries: 0, reinstall_ignored: 0, overflow_records: 0, write_failure_records: 0, reentry_records: 0,
  summary_dropped_total: 0, summary_write_failures_total: 0, summary_reentered_total: 0,
  fetch_denied_relative: 0, fetch_denied_absolute: 0, other_denied: 0, fs_outside: 0, fs_outside_examples: [], records_naming_the_log_itself: 0,
  first_config: null,
};
if (out.exists) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line) continue;
    out.lines += 1;
    let r;
    try { r = JSON.parse(line); } catch { out.unparseable_lines += 1; continue; }
    out.by_kind[r.kind] = (out.by_kind[r.kind] ?? 0) + 1;
    if (r.kind === 'guard-config') { out.configs += 1; out.first_config ??= r.config; }
    else if (r.kind === 'guard-installed') out.installed += 1;
    else if (r.kind === 'guard-summary') {
      out.summaries += 1;
      out.summary_dropped_total += r.summary?.dropped ?? 0;
      out.summary_write_failures_total += r.summary?.write_failures ?? 0;
      out.summary_reentered_total += r.summary?.reentered ?? 0;
    }
    else if (r.kind === 'guard-reinstall-ignored') out.reinstall_ignored += 1;
    else if (r.kind === 'log-overflow') out.overflow_records += 1;
    else if (r.kind === 'log-write-failure') out.write_failure_records += 1;
    else if (r.kind === 'log-reentry') out.reentry_records += 1;
    if (INTERNAL.has(r.kind)) continue;
    if (selfPath && String(r.detail ?? '').replaceAll('\\', '/').toLowerCase() === selfPath) out.records_naming_the_log_itself += 1;
    if (r.kind === 'fetch') { if (/^[a-z]+:\/\//i.test(r.detail ?? '')) out.fetch_denied_absolute += 1; else out.fetch_denied_relative += 1; }
    else if (r.denied) out.other_denied += 1;
    if (/^fs\..*\.outside$/.test(r.kind)) { out.fs_outside += 1; if (out.fs_outside_examples.length < 5) out.fs_outside_examples.push(`${r.kind} ${r.detail}`); }
  }
}
out.complete = out.exists && out.unparseable_lines === 0 && out.overflow_records === 0 && out.write_failure_records === 0 && out.reentry_records === 0
  && out.summary_dropped_total === 0 && out.summary_write_failures_total === 0 && out.summary_reentered_total === 0;
console.log(JSON.stringify(out));
