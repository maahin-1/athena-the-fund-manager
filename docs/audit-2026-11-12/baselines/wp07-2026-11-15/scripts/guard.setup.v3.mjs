// Offline TRIPWIRE v3 for the web test run (vitest setup file). Not an OS sandbox: see GUARD-LIMITS.md next to this file.
// Differences from v2: (1) EVERY path argument of two-path calls is checked (rename, copyFile, cp, link, symlink: source AND destination; a symlink's target too),
// v2 checked only the first; (2) the hits log is run-scoped (runs/<run id>/guard-hits.jsonl), never a shared file that a later run can overwrite.
// Refuses and records: real network from the page (fetch, WebSocket, EventSource, XMLHttpRequest, sendBeacon), TCP/TLS/HTTP/DNS/UDP from node, child processes, and any filesystem call
// naming a protected path: basename .env, .env.*, *.env, .envrc, any dot-directory segment, any basename starting with a dot.
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
import { syncBuiltinESMExports } from 'node:module';

const BASE = 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline';
const runId = (() => { try { return fs.readFileSync(`${BASE}/current-run.txt`, 'utf8').trim() || 'unnamed'; } catch { return 'unnamed'; } })();
const LOG = `${BASE}/runs/${runId}/guard-hits.jsonl`;
try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); } catch { /* the run script normally creates it */ }
const append = fs.appendFileSync.bind(fs);
const hit = (kind, detail, denied) => {
  try { append(LOG, JSON.stringify({ kind, detail: String(detail).slice(0, 200), denied, test: globalThis.__vitest_worker__?.current?.name ?? null }) + '\n'); } catch { /* best effort */ }
};
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
const toText = (p) => { try { return (typeof p === 'string' ? p : p instanceof URL ? decodeURIComponent(p.pathname.replace(/^\//, '')) : Buffer.isBuffer(p) ? p.toString() : '').replaceAll('\\', '/'); } catch { return ''; } };
export const protectedPath = (p) => {
  const n = toText(p);
  if (!n) return false;
  const segs = n.split('/').filter(Boolean);
  const base = segs[segs.length - 1] ?? '';
  if (/^\.env$/i.test(base) || /^\.env\..+/i.test(base) || /\.env$/i.test(base) || /^\.envrc$/i.test(base)) return true;   // .env, .env.*, *.env, .envrc
  return segs.some((s) => s.startsWith('.') && s !== '.' && s !== '..' && !(s === '.vite' && segs.includes('node_modules')));   // any dotfile or dot-directory
};
const allowed = ['d:/projects/learn-project/athena-the-fund-manager/terminal/web', 'c:/users/legion/appdata/local/temp', os.tmpdir().replaceAll('\\', '/').toLowerCase(), 'c:/program files/nodejs'];
const check = (kind, p) => {
  const n = toText(p);
  if (protectedPath(p)) deny(`fs.${kind}`, n);
  if (n && !allowed.some((a) => n.toLowerCase().startsWith(a))) hit(`fs.${kind}.outside`, n, false);
};
// one-path calls check argument 0; two-path calls check arguments 0 and 1 (source and destination)
const TWO = new Set(['rename', 'renameSync', 'copyFile', 'copyFileSync', 'cp', 'cpSync', 'link', 'linkSync', 'symlink', 'symlinkSync']);
const checkArgs = (kind, name, args) => { check(kind, args[0]); if (TWO.has(name)) check(kind, args[1]); };
const wrap = (kind, name, orig) => function (...a) { checkArgs(kind, name, a); return orig.apply(this, a); };
const wrapPromise = (kind, name, orig) => function (...a) { try { checkArgs(kind, name, a); } catch (e) { return Promise.reject(e); } return orig.apply(this, a); };   // the promise API reports a refusal as a rejection
const sync = ['readFileSync', 'openSync', 'readdirSync', 'opendirSync', 'writeFileSync', 'appendFileSync', 'mkdirSync', 'rmSync', 'rmdirSync', 'renameSync', 'copyFileSync', 'cpSync', 'linkSync', 'symlinkSync', 'unlinkSync', 'statSync', 'lstatSync', 'existsSync', 'accessSync', 'realpathSync', 'readlinkSync', 'createReadStream', 'createWriteStream'];
const cb = ['readFile', 'open', 'readdir', 'opendir', 'writeFile', 'appendFile', 'mkdir', 'rm', 'rmdir', 'rename', 'copyFile', 'cp', 'link', 'symlink', 'unlink', 'stat', 'lstat', 'access', 'realpath', 'readlink'];
for (const m of [...sync, ...cb]) if (typeof fs[m] === 'function') fs[m] = wrap(m, m, fs[m]);
for (const m of cb) if (typeof fs.promises[m] === 'function') fs.promises[m] = wrapPromise(`promises.${m}`, m, fs.promises[m]);
syncBuiltinESMExports();   // named imports now see the guarded functions too
hit('guard-installed', path.basename(globalThis.__vitest_worker__?.filepath ?? ''), false);
