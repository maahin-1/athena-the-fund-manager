// Self-test of guard.setup.v2.mjs. Creates no protected file and reads none: protected paths are refused before the filesystem is consulted, so a missing-file error (ENOENT) instead of the guard's
// message would show the guard did NOT intercept. Also demonstrates two known limits (worker threads, descriptors) without touching anything protected.
import './guard.setup.v2.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import http from 'node:http';
import dns from 'node:dns';
import cp from 'node:child_process';
import { Worker } from 'node:worker_threads';
import { protectedPath } from './guard.setup.v2.mjs';

const results = [];
const check = (name, ok, extra = '') => { results.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`); };
const guarded = (e) => String(e?.message ?? e).includes('blocked by the offline guard');
const throwsGuard = (fn) => { try { fn(); return false; } catch (e) { return guarded(e); } };
const rejectsGuard = async (p) => { try { await p; return false; } catch (e) { return guarded(e); } };
const tmp = os.tmpdir();

// network and processes
check('fetch refused', await rejectsGuard(Promise.resolve().then(() => fetch('http://example.invalid/'))));
check('WebSocket refused', throwsGuard(() => new WebSocket('ws://127.0.0.1:1/')));
check('XMLHttpRequest.open refused', throwsGuard(() => new XMLHttpRequest().open('GET', 'http://example.invalid/')));
check('net.connect (tcp) refused', throwsGuard(() => net.connect({ port: 1, host: '127.0.0.1' })));
check('http.get refused', throwsGuard(() => http.get('http://127.0.0.1:1/')));
check('dns.lookup refused', throwsGuard(() => dns.lookup('example.invalid', () => {})));
check('child_process.spawn refused', throwsGuard(() => cp.spawn('cmd', ['/c', 'exit'])));

// protected paths: each must be refused by the guard before any filesystem call
const names = ['.env', '.env.local', '.env.production', '.env.example', 'prod.env', 'a.b.env', '.envrc', 'x/y/.env', '.athena/warehouse.duckdb', '.venv/Scripts/python.exe', '.git/config', '.hidden'];
for (const n of names) {
  const p = path.join(tmp, 'guard-selftest-nonexistent', n);
  check(`readFileSync refuses ${n}`, throwsGuard(() => fs.readFileSync(p)) && protectedPath(p));
}
check('existsSync refuses a protected name', throwsGuard(() => fs.existsSync(path.join(tmp, 'guard-selftest-nonexistent', '.env'))));
check('statSync refuses a protected name', throwsGuard(() => fs.statSync(path.join(tmp, 'guard-selftest-nonexistent', '.envrc'))));
check('fs.promises.readFile refuses', await rejectsGuard(fs.promises.readFile(path.join(tmp, 'guard-selftest-nonexistent', '.env.local'))));
const named = (await import('node:fs')).readFileSync;
check('a named import of readFileSync is guarded (live binding synced)', throwsGuard(() => named(path.join(tmp, 'guard-selftest-nonexistent', '.env'))));
const { readFile } = await import('node:fs/promises');
check('a named import from node:fs/promises is guarded', await rejectsGuard(readFile(path.join(tmp, 'guard-selftest-nonexistent', 'prod.env'))));
check('protectedPath does not flag an ordinary name', !protectedPath(path.join(tmp, 'environment.txt')) && !protectedPath('d:/x/src/env.ts') && !protectedPath('d:/x/src/environment.ts'));

// ordinary use still works
const ok = path.join(tmp, `guard-selftest-${process.pid}.txt`);
fs.writeFileSync(ok, 'hello');
check('an ordinary temp file can still be written and read', fs.readFileSync(ok, 'utf8') === 'hello');
fs.unlinkSync(ok);

// known limits, shown without touching anything protected
const workerNative = await new Promise((resolve) => {
  const w = new Worker('const { parentPort } = require("node:worker_threads"); parentPort.postMessage(String(globalThis.fetch).includes("[native code]") || String(globalThis.fetch).includes("function fetch"));', { eval: true });
  w.on('message', resolve); w.on('error', () => resolve(null));
});
results.push(`LIMIT  a worker thread started by code gets its own unguarded globals (fetch there is the native one: ${workerNative})`);
results.push('LIMIT  not covered by design: native addons, process.binding / process.dlopen, WASI, reads through a descriptor obtained before the guard, the vitest/vite main process, and any process the code starts outside child_process');

console.log(results.join('\n'));
console.log(`\n${results.filter((r) => r.startsWith('PASS')).length} passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
process.exit(results.some((r) => r.startsWith('FAIL')) ? 1 : 0);
