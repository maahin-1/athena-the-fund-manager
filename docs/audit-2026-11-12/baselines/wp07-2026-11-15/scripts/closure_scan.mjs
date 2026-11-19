// Static, read-only scan: the import closure of terminal/web/test/*.test.ts and every filesystem/network/process/storage entry point in it.
// Executes no scanned code. Reads only .ts/.json files that the imports name; skips any file whose name starts with a dot.
import fs from 'node:fs';
import path from 'node:path';

const web = 'd:/projects/learn-project/athena-the-fund-manager/terminal/web';
const testDir = path.join(web, 'test');
const exts = ['', '.ts', '.js', '.json', '/index.ts', '/index.js'];
const importRe = /(?:^|\n)\s*(?:import|export)\s[^;'"]*?from\s*['"]([^'"]+)['"]|(?:^|\n)\s*import\s*['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)/g;

const seen = new Map(); // file -> text
const bare = new Map(); // package -> [importers]
const unresolved = [];
const queue = fs.readdirSync(testDir).filter((f) => f.endsWith('.test.ts') && !f.startsWith('.')).map((f) => path.join(testDir, f));
const tests = [...queue];

function resolve(from, spec) {
  const base = path.resolve(path.dirname(from), spec);
  for (const e of exts) {
    const p = base + e;
    if (fs.existsSync(p) && fs.statSync(p).isFile() && !path.basename(p).startsWith('.')) return p;
  }
  return null;
}

while (queue.length) {
  const file = queue.pop();
  if (seen.has(file)) continue;
  const text = fs.readFileSync(file, 'utf8');
  seen.set(file, text);
  if (!file.endsWith('.ts') && !file.endsWith('.js')) continue;
  for (const m of text.matchAll(importRe)) {
    const spec = m[1] ?? m[2] ?? m[3];
    if (!spec) continue;
    if (spec.startsWith('.')) {
      const r = resolve(file, spec);
      if (r) queue.push(r); else unresolved.push(`${path.relative(web, file)} -> ${spec}`);
    } else {
      (bare.get(spec) ?? bare.set(spec, []).get(spec)).push(path.relative(web, file));
    }
  }
}

const patterns = {
  network: /\bfetch\s*\(|\bfetch\b(?!\s*:|\s*=|['"])|new\s+WebSocket|XMLHttpRequest|EventSource|sendBeacon|navigator\.|window\.open|\bnew\s+Worker\b|importScripts/,
  absoluteUrl: /https?:\/\/[^\s'"`)]+/,
  nodeIO: /node:|require\(|child_process|\bprocess\.|readFile|writeFile|createReadStream|createWriteStream|readdir|\bfs\./,
  storage: /localStorage|sessionStorage|indexedDB|document\.cookie/,
  dynamicImport: /import\(\s*[^'"\s)]/,
  eval: /\beval\s*\(|new\s+Function\s*\(/,
};

const rel = (f) => path.relative(web, f).replaceAll('\\', '/');
const out = [];
out.push(`test files: ${tests.length}`);
out.push(`closure files (tests + imported): ${seen.size}`);
const nonTestSrc = [...seen.keys()].filter((f) => !f.includes(`${path.sep}test${path.sep}`));
out.push(`imported non-test files: ${nonTestSrc.length}`);
out.push(`bare (package) imports: ${[...bare.keys()].sort().join(', ')}`);
out.push(`unresolved relative imports: ${unresolved.length ? unresolved.join('; ') : 'none'}`);
out.push('');
for (const [name, re] of Object.entries(patterns)) {
  out.push(`## ${name}`);
  let n = 0;
  for (const [file, text] of seen) {
    if (!file.endsWith('.ts') && !file.endsWith('.js')) continue;
    const isTest = file.includes(`${path.sep}test${path.sep}`);
    text.split('\n').forEach((line, i) => {
      if (re.test(line)) { n++; out.push(`${isTest ? 'T' : 'S'} ${rel(file)}:${i + 1}: ${line.trim().slice(0, 150)}`); }
    });
  }
  if (!n) out.push('(none)');
  out.push('');
}
fs.writeFileSync(process.argv[2], out.join('\n'), 'utf8');
fs.writeFileSync(process.argv[3], JSON.stringify({ tests: tests.map(rel).sort(), imported: nonTestSrc.map(rel).sort() }, null, 1), 'utf8');
console.log(out.slice(0, 5).join('\n'));
