// Self-test of guard.setup.v3.mjs. Creates no protected file and reads none: protected paths are refused before the filesystem is consulted (an ENOENT instead of the guard's message would show it did not intercept).
// The two-path cases use a real temporary SOURCE file and a protected DESTINATION name under a temporary directory; if the guard failed, the call would create a protected file, so each case also asserts the destination does not exist.
import './guard.setup.v3.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import http from 'node:http';
import dns from 'node:dns';
import cp from 'node:child_process';
import { Worker } from 'node:worker_threads';
import { protectedPath } from './guard.setup.v3.mjs';

const results = [];
const check = (name, ok, extra = '') => { results.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`); };
const guarded = (e) => String(e?.message ?? e).includes('blocked by the offline guard');
const throwsGuard = (fn) => { try { fn(); return false; } catch (e) { return guarded(e); } };
const rejectsGuard = async (p) => { try { await p; return false; } catch (e) { return guarded(e); } };
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-selftest-v3-'));
// "absent" is checked by listing the ordinary parent directory, so the check itself never names a protected path
const missing = (p) => { const rel = path.relative(tmp, p).split(path.sep)[0]; return !fs.readdirSync(tmp).includes(rel); };

// network and processes
check('fetch refused', await rejectsGuard(Promise.resolve().then(() => fetch('http://example.invalid/'))));
check('WebSocket refused', throwsGuard(() => new WebSocket('ws://127.0.0.1:1/')));
check('XMLHttpRequest.open refused', throwsGuard(() => new XMLHttpRequest().open('GET', 'http://example.invalid/')));
check('net.connect (tcp) refused', throwsGuard(() => net.connect({ port: 1, host: '127.0.0.1' })));
check('http.get refused', throwsGuard(() => http.get('http://127.0.0.1:1/')));
check('dns.lookup refused', throwsGuard(() => dns.lookup('example.invalid', () => {})));
check('child_process.spawn refused', throwsGuard(() => cp.spawn('cmd', ['/c', 'exit'])));

// protected paths, first-argument forms
const names = ['.env', '.env.local', '.env.production', '.env.example', 'prod.env', 'a.b.env', '.envrc', 'x/y/.env', '.athena/warehouse.duckdb', '.venv/Scripts/python.exe', '.git/config', '.hidden'];
for (const n of names) {
  const p = path.join(tmp, 'nonexistent', n);
  check(`readFileSync refuses ${n}`, throwsGuard(() => fs.readFileSync(p)) && protectedPath(p));
}
check('existsSync refuses a protected name', throwsGuard(() => fs.existsSync(path.join(tmp, 'nonexistent', '.env'))));
check('fs.promises.readFile refuses', await rejectsGuard(fs.promises.readFile(path.join(tmp, 'nonexistent', '.env.local'))));
const named = (await import('node:fs')).readFileSync;
check('a named import of readFileSync is guarded', throwsGuard(() => named(path.join(tmp, 'nonexistent', '.env'))));

// destination paths of two-path calls: the destination is protected, the source is an ordinary temp file
const src = path.join(tmp, 'source.txt');
fs.writeFileSync(src, 'x');
for (const dest of ['.env', '.env.local', 'prod.env', '.envrc', '.athena/db', '.hidden']) {
  const d = path.join(tmp, dest);
  check(`renameSync to ${dest} refused`, throwsGuard(() => fs.renameSync(src, d)) && missing(d) && fs.existsSync(src));
  check(`copyFileSync to ${dest} refused`, throwsGuard(() => fs.copyFileSync(src, d)) && missing(d));
  check(`cpSync to ${dest} refused`, throwsGuard(() => fs.cpSync(src, d)) && missing(d));
  check(`linkSync to ${dest} refused`, throwsGuard(() => fs.linkSync(src, d)) && missing(d));
  check(`symlinkSync (path ${dest}) refused`, throwsGuard(() => fs.symlinkSync(src, d)) && missing(d));
  check(`fs.promises.copyFile to ${dest} rejects`, (await rejectsGuard(fs.promises.copyFile(src, d))) && missing(d));
  check(`fs.promises.rename to ${dest} rejects`, (await rejectsGuard(fs.promises.rename(src, d))) && missing(d) && fs.existsSync(src));
}
// a protected SOURCE and a symlink whose target is protected are refused too
check('renameSync from a protected source refused', throwsGuard(() => fs.renameSync(path.join(tmp, '.env'), path.join(tmp, 'ok.txt'))));
check('symlinkSync with a protected target refused', throwsGuard(() => fs.symlinkSync(path.join(tmp, '.env'), path.join(tmp, 'link'))) && missing(path.join(tmp, 'link')));
// ordinary two-path use still works
fs.copyFileSync(src, path.join(tmp, 'copy.txt'));
fs.renameSync(path.join(tmp, 'copy.txt'), path.join(tmp, 'moved.txt'));
check('an ordinary copy and rename still work', fs.readFileSync(path.join(tmp, 'moved.txt'), 'utf8') === 'x');
check('protectedPath does not flag an ordinary name', !protectedPath(path.join(tmp, 'environment.txt')) && !protectedPath('d:/x/src/env.ts'));

// known limits, shown without touching anything protected
const workerNative = await new Promise((resolve) => {
  const w = new Worker('const { parentPort } = require("node:worker_threads"); parentPort.postMessage(String(globalThis.fetch).includes("[native code]") || String(globalThis.fetch).includes("function fetch"));', { eval: true });
  w.on('message', resolve); w.on('error', () => resolve(null));
});
results.push(`LIMIT  a worker thread started by code gets its own unguarded globals (fetch there is the native one: ${workerNative})`);
results.push('LIMIT  not covered by design: native addons, process.binding / process.dlopen, WASI, descriptors opened before the guard, the vitest/vite main process, processes started outside child_process');
results.push('LIMIT  not exercised by this self-test (implemented): tls.connect, https.*, dgram, dns.resolve*, exec/execFile/fork, EventSource, sendBeacon, the callback forms, createReadStream, opendir');

fs.rmSync(tmp, { recursive: true, force: true });
console.log(results.join('\n'));
console.log(`\n${results.filter((r) => r.startsWith('PASS')).length} passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
process.exit(results.some((r) => r.startsWith('FAIL')) ? 1 : 0);
