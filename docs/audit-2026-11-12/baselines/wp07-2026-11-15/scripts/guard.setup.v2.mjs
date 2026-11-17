// Offline TRIPWIRE for the web test run (vitest setup file). It is not an OS sandbox: see GUARD-LIMITS.md next to this file.
// Refuses and records: real network from the page (fetch, WebSocket, EventSource, XMLHttpRequest, sendBeacon), TCP/TLS/HTTP/DNS/UDP from node, child processes, and any filesystem call whose path names a
// protected file: .env, .env.*, *.env, .envrc (basename match, any directory), any dot-directory segment (.athena, .venv, .git, ...), or any basename starting with a dot.
// A test that injects its own fetch/socket stub replaces the guard for that call and is unaffected. Hits are appended to guard-hits.jsonl.
import fs from 'node:fs';
import fsp from 'node:fs/promises';
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

const LOG = 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/guard-hits.jsonl';
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
// Socket.prototype.connect receives either the caller's arguments or Node's normalised form [options, callback]; both are unwrapped. A path (IPC / named pipe) is not TCP and is left alone.
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
  return segs.some((s) => s.startsWith('.') && s !== '.' && s !== '..' && !(s === '.vite' && segs.includes('node_modules')));   // any dotfile or dot-directory (.athena, .venv, .git, ...)
};
const allowed = ['d:/projects/learn-project/athena-the-fund-manager/terminal/web', 'c:/users/legion/appdata/local/temp', os.tmpdir().replaceAll('\\', '/').toLowerCase(), 'c:/program files/nodejs'];
const check = (kind, p) => {
  const n = toText(p);
  if (protectedPath(p)) deny(`fs.${kind}`, n);
  if (n && !allowed.some((a) => n.toLowerCase().startsWith(a))) hit(`fs.${kind}.outside`, n, false);
};
const wrap = (kind, orig) => function (p, ...rest) { check(kind, p); return orig.call(this, p, ...rest); };
const wrapPromise = (kind, orig) => function (p, ...rest) { try { check(kind, p); } catch (e) { return Promise.reject(e); } return orig.call(this, p, ...rest); };   // the promise API reports a refusal as a rejection
const sync = ['readFileSync', 'openSync', 'readdirSync', 'opendirSync', 'writeFileSync', 'appendFileSync', 'mkdirSync', 'rmSync', 'rmdirSync', 'renameSync', 'copyFileSync', 'unlinkSync', 'statSync', 'lstatSync', 'existsSync', 'accessSync', 'realpathSync', 'readlinkSync', 'createReadStream', 'createWriteStream'];
const cb = ['readFile', 'open', 'readdir', 'opendir', 'writeFile', 'appendFile', 'mkdir', 'rm', 'rmdir', 'rename', 'copyFile', 'unlink', 'stat', 'lstat', 'access', 'realpath', 'readlink'];
for (const m of [...sync, ...cb]) if (typeof fs[m] === 'function') fs[m] = wrap(m, fs[m]);
for (const m of cb) if (typeof fs.promises[m] === 'function') fs.promises[m] = wrapPromise(`promises.${m}`, fs.promises[m]);
void fsp;
syncBuiltinESMExports();   // named imports (import { readFileSync } from 'node:fs') now see the guarded functions too
hit('guard-installed', path.basename(globalThis.__vitest_worker__?.filepath ?? ''), false);
