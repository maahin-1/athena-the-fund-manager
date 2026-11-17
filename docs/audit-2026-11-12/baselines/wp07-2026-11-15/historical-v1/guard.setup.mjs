// Offline guard for the web test run (setup file). Any real network, socket, process or dotfile access from test or source code is refused and recorded.
// Tests that inject their own fetch/socket stubs are unaffected: the stub replaces the guard for that call. Hits are appended to guard-hits.jsonl.
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

const LOG = 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/guard-hits.jsonl';
const append = fs.appendFileSync.bind(fs);
const hit = (kind, detail, denied) => {
  try { append(LOG, JSON.stringify({ kind, detail: String(detail).slice(0, 200), denied, test: globalThis.__vitest_worker__?.current?.name ?? null }) + '\n'); } catch { /* the log is best effort */ }
};
const deny = (kind, detail) => { hit(kind, detail, true); throw new Error(`blocked by the offline guard: ${kind} ${String(detail).slice(0, 120)}`); };

// ---- network from the page's point of view
const refuse = (kind) => (...a) => deny(kind, a[0] && a[0].toString ? a[0].toString() : '');
globalThis.fetch = refuse('fetch');
globalThis.WebSocket = class { constructor(u) { deny('WebSocket', u); } };
globalThis.EventSource = class { constructor(u) { deny('EventSource', u); } };
globalThis.XMLHttpRequest = class { open(m, u) { deny('XMLHttpRequest', u); } };
if (typeof window !== 'undefined' && window !== globalThis) {
  window.fetch = globalThis.fetch; window.WebSocket = globalThis.WebSocket; window.EventSource = globalThis.EventSource; window.XMLHttpRequest = globalThis.XMLHttpRequest;
}
try { if (globalThis.navigator) globalThis.navigator.sendBeacon = refuse('sendBeacon'); } catch { /* read-only in some environments */ }

// ---- network from node
const tcp = (opts) => opts && typeof opts === 'object' && (opts.port !== undefined || opts.host !== undefined);
const realConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...a) { if (tcp(a[0]) || typeof a[0] === 'number') deny('net.connect', JSON.stringify(a[0])); return realConnect.apply(this, a); };
tls.connect = refuse('tls.connect');
for (const m of ['request', 'get']) { http[m] = refuse(`http.${m}`); https[m] = refuse(`https.${m}`); }
for (const m of ['lookup', 'resolve', 'resolve4', 'resolve6']) dns[m] = refuse(`dns.${m}`);
dgram.createSocket = refuse('dgram');

// ---- processes
for (const m of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) cp[m] = refuse(`child_process.${m}`);

// ---- files: dotfiles and dot-directories (which covers variable files, .athena, .venv, .git) are never read; other reads outside the project, the scratch area and temp are recorded
const allowed = ['d:/projects/learn-project/athena-the-fund-manager/terminal/web', 'c:/users/legion/appdata/local/temp', os.tmpdir().replaceAll('\\', '/').toLowerCase(), 'c:/program files/nodejs'];
const norm = (p) => { try { return (typeof p === 'string' ? p : p instanceof URL ? decodeURIComponent(p.pathname.replace(/^\//, '')) : Buffer.isBuffer(p) ? p.toString() : '').replaceAll('\\', '/'); } catch { return ''; } };
const dotted = (n) => n.split('/').some((seg, i, all) => seg.startsWith('.') && seg !== '.' && seg !== '..' && !(seg === '.vite' && all.includes('node_modules'))) || /\.[Ee][Nn][Vv]$/.test(n);
const guardPath = (kind) => (orig) => function (p, ...rest) {
  const n = norm(p);
  if (n && dotted(n)) deny(`fs.${kind}`, n);
  if (n && !allowed.some((a) => n.toLowerCase().startsWith(a))) hit(`fs.${kind}.outside`, n, false);
  return orig.call(this, p, ...rest);
};
for (const m of ['readFileSync', 'readFile', 'openSync', 'open', 'createReadStream', 'readdirSync', 'readdir', 'writeFileSync', 'writeFile', 'appendFile', 'createWriteStream', 'mkdirSync', 'rmSync', 'renameSync', 'copyFileSync', 'unlinkSync']) {
  if (typeof fs[m] === 'function') fs[m] = guardPath(m)(fs[m]);
}
for (const m of ['readFile', 'open', 'readdir', 'writeFile', 'appendFile']) {
  if (typeof fs.promises[m] === 'function') fs.promises[m] = guardPath(`promises.${m}`)(fs.promises[m]);
}
hit('guard-installed', path.basename(globalThis.__vitest_worker__?.filepath ?? ''), false);
