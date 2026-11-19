// Offline TRIPWIRE v4 for the web test run (vitest setup file). Not an OS sandbox: see GUARD-LIMITS.md (baselines/wp07-2026-11-15) and GUARD-V4.md next to this file.
// v4 REPLACES v3 for the maintenance branch's web gate; v3 is kept unchanged as the historical, approved file. What v4 changes (and what it does not): GUARD-V4.md.
//   1. The guard's own logging is NON-REENTRANT. It writes through a descriptor opened once, with the low-level writeSync captured before anything is wrapped, so a log write never passes through a
//      guarded function (v3 used appendFileSync, which calls the guarded writeFileSync: when the log folder was judged "outside" every log write logged itself, thousands of times, until the stack
//      overflowed). A re-entry from anything else (a hook that touches the file system while a record is being built) is counted and reported, never recursed into.
//   2. Logging is BOUNDED (max_records per process, default 2000) and failures are SURFACED: a log-overflow record, a stderr marker, counters in a guard-summary record. Nothing is dropped silently.
//   3. Paths are judged by their CANONICAL form (real path of the nearest existing ancestor, then the rest) and by path-segment containment, so a short-name or junction spelling of the same folder is
//      the same folder, a sibling folder that merely shares a prefix is outside, and a junction inside an allowed root that points outside is outside. The effective roots, the Node temp folder and the
//      PowerShell temp folder (recorded by the gate) are written to the log in one guard-config record.
//   4. No source line is patched per machine: the configuration (log file, allowed roots, limits) comes from the JSON file named by ATHENA_GUARD_CONFIG, so the file that runs is the pinned file.
//   5. Installing twice in one process is idempotent (functions are wrapped once); a second installation with a different configuration is refused.
//   6. The protected-name rule is applied to the supplied spelling AND to the canonical form of every path argument (both arguments of a two-path call), all before the original function runs.
// Unchanged from v3 on purpose: what is refused and recorded (network from the page and from node, child processes), the protected-name rule itself, the list of guarded file-system functions, and
// the two-path rule (source AND destination of rename, copyFile, cp, link, symlink are both checked).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import dns from 'node:dns';
import dgram from 'node:dgram';
import cp from 'node:child_process';
import crypto from 'node:crypto';
import { syncBuiltinESMExports } from 'node:module';

export const GUARD_VERSION = 'v4';
const STATE_KEY = Symbol.for('athena.offline-guard.v4');

// ---- the low-level functions the guard itself needs, captured BEFORE anything is wrapped (the guard never calls a guarded function).
// A later evaluation of this module in the same process finds fs already wrapped, so it takes the originals from the first installation, never from the wrappers.
const existing = fs[STATE_KEY];
const real = existing ? existing.real : {
  readFileSync: fs.readFileSync.bind(fs),
  openSync: fs.openSync.bind(fs),
  writeSync: fs.writeSync.bind(fs),
  mkdirSync: fs.mkdirSync.bind(fs),
  realpathNative: fs.realpathSync.native.bind(fs.realpathSync),
};

// ---- configuration (fail closed: no configuration, no guard, no test run)
const configPath = process.env.ATHENA_GUARD_CONFIG;
if (!configPath) throw new Error('offline guard v4: ATHENA_GUARD_CONFIG is not set (the guard fails closed without its configuration)');
const configText = real.readFileSync(configPath, 'utf8');
const configHash = crypto.createHash('sha256').update(configText).digest('hex');
const cfg = JSON.parse(configText);
const absolute = (p) => typeof p === 'string' && path.isAbsolute(p);
if (cfg.version !== GUARD_VERSION) throw new Error(`offline guard v4: configuration is for ${cfg.version}, not ${GUARD_VERSION}`);
if (!absolute(cfg.log_file)) throw new Error('offline guard v4: log_file must be an absolute path');
if (!Array.isArray(cfg.allowed_roots) || cfg.allowed_roots.length === 0 || !cfg.allowed_roots.every(absolute)) throw new Error('offline guard v4: allowed_roots must be a non-empty list of absolute paths');
cfg.max_records = cfg.max_records === undefined ? 2000 : cfg.max_records;
if (!Number.isInteger(cfg.max_records) || cfg.max_records < 10 || cfg.max_records > 100000) throw new Error('offline guard v4: max_records must be an integer from 10 to 100000');

// ---- paths: canonical form and path-segment containment
const isWin = process.platform === 'win32';
const slash = (p) => String(p).replaceAll('\\', '/');
const stripLongPrefix = (p) => p.replace(/^\/\/[?.]\//, '');
const fold = (p) => (isWin ? p.toLowerCase() : p);
const trimEnd = (p) => (p.length > 1 && p.endsWith('/') && !/^[a-z]:\/$/i.test(p) ? p.replace(/\/+$/, '') : p);
const tidy = (p) => fold(trimEnd(slash(stripLongPrefix(slash(p)))));
const dirCache = new Map();   // only directories that exist are cached
const canonDir = (d) => {
  const known = dirCache.get(d);
  if (known !== undefined) return known;
  try {
    const r = real.realpathNative(d);
    if (dirCache.size > 20000) dirCache.clear();
    dirCache.set(d, r);
    return r;
  } catch {
    const parent = path.dirname(d);
    return parent === d ? d : path.join(canonDir(parent), path.basename(d));
  }
};
export const canonical = (p) => {
  const abs = path.resolve(String(p));   // collapses . and .., makes a relative path absolute against the current directory
  let r;
  try { r = real.realpathNative(abs); } catch { r = path.join(canonDir(path.dirname(abs)), path.basename(abs)); }   // not there (yet): the nearest existing parent decides
  return tidy(r);
};
export const contains = (root, candidate) => candidate === root || candidate.startsWith(root.endsWith('/') ? root : `${root}/`);   // both already canonical: whole segments only

// ---- v3's protected-path rule, unchanged
const toText = (p) => { try { return (typeof p === 'string' ? p : p instanceof URL ? decodeURIComponent(p.pathname.replace(/^\//, '')) : Buffer.isBuffer(p) ? p.toString() : '').replaceAll('\\', '/'); } catch { return ''; } };
export const protectedPath = (p) => {
  const n = toText(p);
  if (!n) return false;
  const segs = n.split('/').filter(Boolean);
  const base = segs[segs.length - 1] ?? '';
  if (/^\.env$/i.test(base) || /^\.env\..+/i.test(base) || /\.env$/i.test(base) || /^\.envrc$/i.test(base)) return true;   // .env, .env.*, *.env, .envrc
  return segs.some((s) => s.startsWith('.') && s !== '.' && s !== '..' && !(s === '.vite' && segs.includes('node_modules')));   // any dotfile or dot-directory
};

// ---- installation state (one per process: fs carries it, so a second evaluation of this module finds it)
const stderrNote = (text) => { try { process.stderr.write(`${text}\n`); } catch { /* nothing else to do */ } };
const install = () => {
  const s = {
    version: GUARD_VERSION, config_sha256: configHash, cfg, real, fd: null, records: 0, dropped: 0, write_failures: 0, reentered: 0, reentry_pending: [], reinstalls: 0,
    by_kind: {}, overflowed: false, inLog: false, failure_reported: false, last_error: null, roots: [],
  };
  real.mkdirSync(path.dirname(cfg.log_file), { recursive: true });
  s.fd = real.openSync(cfg.log_file, 'a');   // fails closed: a guard that cannot log must not run the tests
  s.roots = cfg.allowed_roots.map((r) => ({ raw: r, canonical: canonical(r) }));
  s.logCanonical = canonical(cfg.log_file);
  const writeRaw = (obj) => {
    const buf = Buffer.from(`${JSON.stringify(obj)}\n`);
    let off = 0;
    while (off < buf.length) off += real.writeSync(s.fd, buf, off, buf.length - off);
  };
  const testName = () => { try { return globalThis.__vitest_worker__?.current?.name ?? null; } catch { return null; } };
  s.counters = () => ({ records: s.records, dropped: s.dropped, write_failures: s.write_failures, reentered: s.reentered, reinstalls: s.reinstalls, by_kind: { ...s.by_kind }, last_error: s.last_error });
  // the internal writer: never recursive, bounded, and every loss is counted and reported
  s.write = (rec, { internal = false } = {}) => {
    try {
      writeRaw(rec);
      s.records += 1;
      if (s.write_failures > 0 && !internal && s.failure_reported === 'pending') { s.failure_reported = true; writeRaw({ kind: 'log-write-failure', detail: `${s.write_failures} earlier write(s) failed: ${s.last_error}`, denied: false }); }
      return true;
    } catch (e) {
      s.write_failures += 1;
      s.last_error = String(e?.message ?? e).slice(0, 160);
      if (!s.failure_reported) { s.failure_reported = 'pending'; stderrNote(`ATHENA-GUARD-V4 LOG-WRITE-FAILURE: ${s.last_error}`); }
      return false;
    }
  };
  s.hit = (kind, detail, denied) => {
    if (s.inLog) {   // never recurse: the event is counted and kept here, and reported (with its details) when the outer record is done
      s.reentered += 1;
      if (s.reentry_pending.length < 20) s.reentry_pending.push({ kind, detail: String(detail).slice(0, 200), denied });
      return;
    }
    s.inLog = true;
    try {
      s.by_kind[kind] = (s.by_kind[kind] ?? 0) + 1;
      if (s.records >= cfg.max_records - 3) {   // three slots stay reserved: the overflow marker, a re-entry marker, the summary
        if (!s.overflowed) {
          s.overflowed = true;
          s.write({ kind: 'log-overflow', detail: `max_records ${cfg.max_records} reached; further records are counted, not written`, denied: false }, { internal: true });
          stderrNote(`ATHENA-GUARD-V4 LOG-OVERFLOW: more than ${cfg.max_records} records in one process; the rest are counted, not written`);
        }
        s.dropped += 1;
        return;
      }
      s.write({ kind, detail: String(detail).slice(0, 200), denied, test: testName() });
      if (s.reentry_pending.length > 0) {
        s.write({ kind: 'log-reentry', detail: `${s.reentry_pending.length} event(s) arrived while a record was being written: kept, not recursed into`, denied: false, events: s.reentry_pending }, { internal: true });
        s.reentry_pending = [];
      }
    } finally { s.inLog = false; }
  };
  s.isInsideCanonical = (c) => s.roots.some((r) => contains(r.canonical, c));
  s.isInside = (p) => s.isInsideCanonical(canonical(p));
  return s;
};

const state = existing ?? install();
if (existing) {
  if (existing.config_sha256 !== configHash) throw new Error('offline guard v4: a second installation was given a different configuration; refused');
  existing.reinstalls += 1;
  existing.hit('guard-reinstall-ignored', `installation ${existing.reinstalls}: functions are already wrapped`, false);
} else {
  Object.defineProperty(fs, STATE_KEY, { value: state, enumerable: false });
  const nodeTmp = os.tmpdir();
  const psTemp = cfg.recorded?.powershell_temp_raw ?? null;
  const envNames = ['TEMP', 'TMP', 'TMPDIR', 'RUNNER_TEMP'];
  state.write({
    kind: 'guard-config', denied: false,
    config: {
      guard_version: GUARD_VERSION, config_sha256: configHash, node: process.version, platform: process.platform, pid: process.pid, cwd: process.cwd(), run_id: cfg.run_id ?? null,
      log_file: { raw: cfg.log_file, canonical: state.logCanonical }, max_records: cfg.max_records,
      node_tmpdir: { raw: nodeTmp, canonical: canonical(nodeTmp) },
      powershell_temp: psTemp ? { raw: psTemp, canonical: canonical(psTemp) } : null,
      temp_paths_same_directory: psTemp ? canonical(psTemp) === canonical(nodeTmp) : null,
      env: Object.fromEntries(envNames.filter((n) => process.env[n]).map((n) => [n, process.env[n]])),
      allowed_roots: state.roots.map((r) => ({ raw: r.raw, canonical: r.canonical })),
      log_dir_inside_allowed_roots: state.roots.some((r) => contains(r.canonical, tidy(path.dirname(state.logCanonical)))),
      recorded_by_the_gate: cfg.recorded ?? null,
    },
  }, { internal: true });
  process.on('exit', () => {
    if (!state.write({ kind: 'guard-summary', denied: false, summary: state.counters() }, { internal: true })) stderrNote('ATHENA-GUARD-V4 SUMMARY-NOT-WRITTEN');
  });

  const { hit } = state;
  const deny = (kind, detail) => { hit(kind, detail, true); throw new Error(`blocked by the offline guard: ${kind} ${String(detail).slice(0, 120)}`); };
  const refuse = (kind) => (...a) => deny(kind, a[0] && a[0].toString ? a[0].toString() : '');

  // ---- network as the page sees it
  globalThis.fetch = refuse('fetch');
  globalThis.WebSocket = class { constructor(u) { deny('WebSocket', u); } };
  globalThis.EventSource = class { constructor(u) { deny('EventSource', u); } };
  globalThis.XMLHttpRequest = class { open(m, u) { deny('XMLHttpRequest', u); } };
  if (typeof window !== 'undefined' && window !== globalThis) {
    window.fetch = globalThis.fetch; window.WebSocket = globalThis.WebSocket; window.EventSource = globalThis.EventSource; window.XMLHttpRequest = globalThis.XMLHttpRequest;
  }
  try { if (globalThis.navigator) globalThis.navigator.sendBeacon = refuse('sendBeacon'); } catch { /* read-only in some environments */ }

  // ---- network from node
  const tcp = (o) => typeof o === 'number' || typeof o === 'string' && /^\d+$/.test(o) || (o && typeof o === 'object' && !Array.isArray(o) && o.path === undefined && (o.port !== undefined || o.host !== undefined));
  const realConnect = net.Socket.prototype.connect;
  net.Socket.prototype.connect = function (...a) { const first = Array.isArray(a[0]) ? a[0][0] : a[0]; if (tcp(first)) deny('net.connect', JSON.stringify(first)); return realConnect.apply(this, a); };
  tls.connect = refuse('tls.connect');
  for (const m of ['request', 'get']) { http[m] = refuse(`http.${m}`); https[m] = refuse(`https.${m}`); }
  for (const m of ['lookup', 'resolve', 'resolve4', 'resolve6']) dns[m] = refuse(`dns.${m}`);
  dgram.createSocket = refuse('dgram.createSocket');

  // ---- processes
  for (const m of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) cp[m] = refuse(`child_process.${m}`);

  // ---- files
  // one-path calls judge argument 0; two-path calls judge arguments 0 and 1 (source and destination). Every argument is judged in full BEFORE the original function runs:
  //   1. the supplied spelling of EVERY argument is checked against the protected-name rule first (text only: a protected spelling is never resolved, so nothing touches it);
  //   2. then the CANONICAL form of every remaining argument (links and short names resolved) is checked against the same rule, so an innocent spelling that resolves to a protected
  //      path (a junction or link into a dot-directory, for instance) is refused too; resolving a path reads link metadata only, never the file's contents;
  //   3. outside-the-roots records are written for the arguments that are not protected; then, if ANY argument was protected, the call is refused and the original function is never called.
  const TWO = new Set(['rename', 'renameSync', 'copyFile', 'copyFileSync', 'cp', 'cpSync', 'link', 'linkSync', 'symlink', 'symlinkSync']);
  const checkArgs = (kind, name, args) => {
    const judged = (TWO.has(name) ? [args[0], args[1]] : [args[0]]).map((p) => { const n = toText(p); return { n, textProtected: n !== '' && protectedPath(n), c: null, canonicalProtected: false }; });
    for (const j of judged) if (j.n && !j.textProtected) { j.c = canonical(j.n); j.canonicalProtected = protectedPath(j.c); }
    for (const j of judged) if (j.n && !j.textProtected && !j.canonicalProtected && !state.isInsideCanonical(j.c)) hit(`fs.${kind}.outside`, j.n, false);
    const bad = judged.find((j) => j.textProtected || j.canonicalProtected);
    if (bad) deny(`fs.${kind}`, bad.textProtected ? bad.n : `canonical ${bad.c} <- supplied ${bad.n}`);   // the canonical path first: details are cut at 200 characters
  };
  const wrap = (kind, name, orig) => function (...a) { checkArgs(kind, name, a); return orig.apply(this, a); };
  const wrapPromise = (kind, name, orig) => function (...a) { try { checkArgs(kind, name, a); } catch (e) { return Promise.reject(e); } return orig.apply(this, a); };   // the promise API reports a refusal as a rejection
  const sync = ['readFileSync', 'openSync', 'readdirSync', 'opendirSync', 'writeFileSync', 'appendFileSync', 'mkdirSync', 'rmSync', 'rmdirSync', 'renameSync', 'copyFileSync', 'cpSync', 'linkSync', 'symlinkSync', 'unlinkSync', 'statSync', 'lstatSync', 'existsSync', 'accessSync', 'realpathSync', 'readlinkSync', 'createReadStream', 'createWriteStream'];
  const cb = ['readFile', 'open', 'readdir', 'opendir', 'writeFile', 'appendFile', 'mkdir', 'rm', 'rmdir', 'rename', 'copyFile', 'cp', 'link', 'symlink', 'unlink', 'stat', 'lstat', 'access', 'realpath', 'readlink'];
  for (const m of [...sync, ...cb]) if (typeof fs[m] === 'function') fs[m] = wrap(m, m, fs[m]);
  for (const m of cb) if (typeof fs.promises[m] === 'function') fs.promises[m] = wrapPromise(`promises.${m}`, m, fs.promises[m]);
  syncBuiltinESMExports();   // named imports now see the guarded functions too
  state.hit('guard-installed', path.basename(globalThis.__vitest_worker__?.filepath ?? ''), false);
}

export const __guardV4 = { version: GUARD_VERSION, state, canonical, contains, isInside: (p) => state.isInside(p), counters: () => state.counters() };
