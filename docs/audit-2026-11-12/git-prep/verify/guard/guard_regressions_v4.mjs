// Regressions for guard.setup.v4.mjs: each scenario runs in its OWN node process (the guard wraps child_process, so this runner never installs the guard itself), with its own configuration, log and
// folders under a temporary directory. Every directory link is made here, before the child starts. No environment file is created, read or named; the only protected-looking things are one EMPTY
// dot-folder (scenario 11) and VIRTUAL link targets that exist only in an intercepted realpath (scenario 10). Output: PASS / FAIL lines and a final count (exit 1 on any FAIL).
// Covered: (1) the logger cannot re-enter itself, whatever triggers it; (2) a log folder that is NOT under an allowed root (the hosted-runner failure of the first run) neither loops nor hides events;
// (3) a hook that touches the file system while a record is built is counted and reported; (4) overflow is bounded and surfaced; (5) a failed log write is surfaced while refusals still work;
// (6) the guard fails closed (no, wrong or unusable configuration); (7) repeated installation wraps once and refuses a different configuration; (8) path containment: sibling prefix, .., junction out of
// a root, alias spelling of a root, case, relative paths; (9) Node and PowerShell temp folders that name the same directory in different spellings; (10) genuine outside writes are still recorded;
// (11) a protected TARGET behind an innocent spelling (link into a dot-folder) is refused for both arguments of two-path calls before the original function runs (intercepted and real junction).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const GUARD = path.join(here, 'guard.setup.v4.mjs');
const JUDGE = path.join(here, 'guard_judge_v4.mjs');
const isWin = process.platform === 'win32';
const results = [];
const check = (name, ok, extra = '') => { results.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !extra ? '' : '  ' + extra}`); };
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-reg-v4-'));
let counter = 0;
const PRELUDE = "import fs from 'node:fs'; import path from 'node:path'; import os from 'node:os'; const A = process.env.SC_A, B = process.env.SC_B, DIR = process.env.SC_DIR; const G = await import(process.env.GUARD_URL);\n";

// one scenario = one child process; returns what it did and what its log holds
const scenario = ({ cfg = {}, roots, env = {}, code, prelude = true, pre }) => {
  counter += 1;
  const dir = path.join(work, `s${counter}`);
  const A = path.join(dir, 'allowed'); const B = path.join(dir, 'outside'); const L = path.join(dir, 'logs');
  for (const d of [dir, A, B, L, `${A}-evil`]) fs.mkdirSync(d, { recursive: true });
  if (pre) pre({ dir, A, B, L });
  const log = path.join(L, 'guard-hits.jsonl');
  const ctx = { dir, A, B, L };
  const config = { version: 'v4', run_id: `s${counter}`, log_file: log, allowed_roots: roots ? roots(ctx) : [A], ...(typeof cfg === 'function' ? cfg(ctx) : cfg) };
  const configPath = path.join(dir, 'config.json');
  fs.writeFileSync(configPath, JSON.stringify(config));
  fs.writeFileSync(path.join(dir, 'other-config.json'), JSON.stringify({ ...config, max_records: 77 }));
  const childEnv = { ...process.env, ATHENA_GUARD_CONFIG: configPath, GUARD_URL: pathToFileURL(GUARD).href, SC_A: A, SC_B: B, SC_DIR: dir, SC_OTHER_CONFIG: path.join(dir, 'other-config.json'), ...(typeof env === 'function' ? env(ctx) : env) };
  for (const k of Object.keys(childEnv)) if (childEnv[k] === undefined) delete childEnv[k];
  const t0 = Date.now();
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', (prelude ? PRELUDE : '') + code], { env: childEnv, encoding: 'utf8', timeout: 60000 });
  const recs = fs.existsSync(log) ? fs.readFileSync(log, 'utf8').split('\n').filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return { kind: '?unparseable' }; } }) : [];
  let out = null;
  try { out = JSON.parse((r.stdout ?? '').trim().split('\n').pop()); } catch { /* not every scenario prints JSON */ }
  return { dir, A, B, L, log, r, recs, out, ms: Date.now() - t0, config, kinds: (k) => recs.filter((x) => x.kind === k) };
};
const judge = (log, self) => JSON.parse(spawnSync(process.execPath, [JUDGE, log, ...(self ? [self] : [])], { encoding: 'utf8' }).stdout);
const tail = (s) => String(s.r.stderr ?? '').trim().split('\n').slice(-2).join(' | ').slice(0, 200);

// ---- 1. a log folder that is NOT under an allowed root: exact events, no self-records, no loop
{
  const s = scenario({ code: "for (let i = 0; i < 25; i++) fs.writeFileSync(path.join(B, 'f' + i + '.txt'), 'x'); for (let i = 0; i < 3; i++) fs.writeFileSync(path.join(A, 'in' + i + '.txt'), 'x'); console.log(JSON.stringify(G.__guardV4.counters()));" });
  const outside = s.kinds('fs.writeFileSync.outside');
  check('mismatched log folder: the child finishes (exit 0, within 20 s)', s.r.status === 0 && s.ms < 20000, tail(s));
  check('mismatched log folder: exactly the 25 genuine outside writes are recorded, none denied', outside.length === 25 && outside.every((x) => x.denied === false && path.basename(path.dirname(x.detail)) === 'outside'));
  check('mismatched log folder: no record names the log file itself', s.recs.every((x) => !String(x.detail ?? '').endsWith('guard-hits.jsonl')));
  check('mismatched log folder: the log holds exactly config + installed + 25 + summary records', s.recs.length === 28, `got ${s.recs.length}`);
  check('mismatched log folder: the config record says the log folder is outside the roots', s.kinds('guard-config')[0]?.config.log_dir_inside_allowed_roots === false);
  check('mismatched log folder: the three inside writes are not recorded', !s.recs.some((x) => String(x.detail ?? '').includes(`${path.sep}allowed${path.sep}in`.replaceAll('\\', '/')) || String(x.detail ?? '').replaceAll('\\', '/').includes('/allowed/in')));
  check('mismatched log folder: the judge sees a complete log', judge(s.log).complete === true && judge(s.log).fs_outside === 25);
}

// ---- 2. a hook that touches the file system while a record is being built (re-entry): counted, reported with its details, never recursed
{
  const s = scenario({ code: "globalThis.__vitest_worker__ = { get current() { fs.writeFileSync(path.join(B, 'inner.txt'), 'y'); return { name: 't' }; } }; fs.writeFileSync(path.join(B, 'outer.txt'), 'x'); delete globalThis.__vitest_worker__; console.log(JSON.stringify(G.__guardV4.counters()));" });
  const outer = s.kinds('fs.writeFileSync.outside');
  const re = s.kinds('log-reentry');
  check('logger re-entry: the child finishes quickly with a handful of records, not thousands', s.r.status === 0 && s.recs.length <= 6 && s.ms < 20000, `records ${s.recs.length} ${tail(s)}`);
  check('logger re-entry: the outer event is recorded once', outer.length === 1 && outer[0].detail.endsWith('outer.txt'));
  check('logger re-entry: the inner event is kept with its details in a log-reentry record', re.length === 1 && re[0].events?.length === 1 && re[0].events[0].detail.endsWith('inner.txt'));
  check('logger re-entry: the counters say so', s.out?.reentered === 1 && s.kinds('guard-summary')[0]?.summary.reentered === 1);
  check('logger re-entry: the judge calls the log incomplete', judge(s.log).complete === false && judge(s.log).reentry_records === 1);
}

// ---- 3. overflow is bounded and surfaced
{
  const s = scenario({ cfg: { max_records: 40 }, code: "for (let i = 0; i < 300; i++) fs.writeFileSync(path.join(B, 'f' + i + '.txt'), 'x'); console.log(JSON.stringify(G.__guardV4.counters()));" });
  const j = judge(s.log);
  check('overflow: the log is bounded by max_records', s.r.status === 0 && s.recs.length <= 40, `records ${s.recs.length}`);
  check('overflow: exactly one log-overflow record and a stderr marker', s.kinds('log-overflow').length === 1 && String(s.r.stderr).includes('ATHENA-GUARD-V4 LOG-OVERFLOW'));
  check('overflow: the summary counts what was not written (nothing is lost silently)', (s.kinds('guard-summary')[0]?.summary.dropped ?? 0) >= 250 && s.out?.dropped >= 250);
  check('overflow: the judge reports the overflow and calls the log incomplete', j.overflow_records === 1 && j.summary_dropped_total >= 250 && j.complete === false);
}

// ---- 4. a failed log write is surfaced, and refusals still work
{
  const s = scenario({ code: "fs.closeSync(G.__guardV4.state.fd); let refused = false; try { await fetch('http://example.invalid/'); } catch (e) { refused = String(e.message).includes('blocked by the offline guard'); } fs.writeFileSync(path.join(B, 'after.txt'), 'x'); console.log(JSON.stringify({ refused, c: G.__guardV4.counters() }));" });
  check('write failure: the child survives and the fetch is still refused', s.r.status === 0 && s.out?.refused === true, tail(s));
  check('write failure: it is counted and reported on stderr', (s.out?.c.write_failures ?? 0) >= 2 && String(s.r.stderr).includes('ATHENA-GUARD-V4 LOG-WRITE-FAILURE'));
  check('write failure: the missing summary is reported too', String(s.r.stderr).includes('ATHENA-GUARD-V4 SUMMARY-NOT-WRITTEN'));
}

// ---- 5. the guard fails closed
{
  // the log is placed under a regular file, so it cannot be opened
  const dir = path.join(work, 'closed'); fs.mkdirSync(dir); fs.writeFileSync(path.join(dir, 'afile'), 'x');
  const bad = path.join(dir, 'bad.json'); fs.writeFileSync(bad, JSON.stringify({ version: 'v4', log_file: path.join(dir, 'afile', 'x.jsonl'), allowed_roots: [dir] }));
  const run = (envOver, code = "await import(process.env.GUARD_URL); console.log('TESTS-RAN');") => spawnSync(process.execPath, ['--input-type=module', '-e', code], { env: Object.fromEntries(Object.entries({ ...process.env, GUARD_URL: pathToFileURL(GUARD).href, ...envOver }).filter(([, v]) => v !== undefined)), encoding: 'utf8', timeout: 60000 });
  const noCfg = run({ ATHENA_GUARD_CONFIG: undefined });
  const wrongVer = (() => { const p = path.join(dir, 'v3.json'); fs.writeFileSync(p, JSON.stringify({ version: 'v3', log_file: path.join(dir, 'l.jsonl'), allowed_roots: [dir] })); return run({ ATHENA_GUARD_CONFIG: p }); })();
  const noRoots = (() => { const p = path.join(dir, 'noroots.json'); fs.writeFileSync(p, JSON.stringify({ version: 'v4', log_file: path.join(dir, 'l.jsonl'), allowed_roots: [] })); return run({ ATHENA_GUARD_CONFIG: p }); })();
  const unwritable = run({ ATHENA_GUARD_CONFIG: bad });
  check('fails closed: no configuration', noCfg.status !== 0 && String(noCfg.stderr).includes('ATHENA_GUARD_CONFIG is not set') && !String(noCfg.stdout).includes('TESTS-RAN'));
  check('fails closed: a configuration for another version', wrongVer.status !== 0 && String(wrongVer.stderr).includes('configuration is for v3') && !String(wrongVer.stdout).includes('TESTS-RAN'));
  check('fails closed: no allowed roots', noRoots.status !== 0 && String(noRoots.stderr).includes('allowed_roots') && !String(noRoots.stdout).includes('TESTS-RAN'));
  check('fails closed: a log that cannot be opened', unwritable.status !== 0 && !String(unwritable.stdout).includes('TESTS-RAN'));
}

// ---- 6. repeated installation: wrapped once, a different configuration refused
{
  const s = scenario({
    pre: ({ B }) => fs.writeFileSync(path.join(B, 'x.txt'), 'x'),
    code: "const u = process.env.GUARD_URL; await import(u + '?n=1'); await import(u + '?n=2'); fs.readFileSync(path.join(B, 'x.txt')); process.env.ATHENA_GUARD_CONFIG = process.env.SC_OTHER_CONFIG; let refused = false; try { await import(u + '?n=3'); } catch (e) { refused = String(e.message).includes('different configuration'); } console.log(JSON.stringify({ refused, c: G.__guardV4.counters() }));",
  });
  const ownReads = s.kinds('fs.readFileSync.outside').filter((x) => x.detail.endsWith('/x.txt'));
  check('repeated installation: one outside read of the file under test is recorded once (functions are wrapped once, not three times)', s.r.status === 0 && ownReads.length === 1, `${ownReads.length} ${tail(s)}`);
  check('repeated installation: one config record, two reinstall notes', s.kinds('guard-config').length === 1 && s.kinds('guard-reinstall-ignored').length === 2 && s.out?.c.reinstalls === 2);
  check('repeated installation: a different configuration is refused', s.out?.refused === true);
}

// ---- 7. path containment, with real directory links made before the child starts
{
  const s = scenario({
    pre: ({ dir, A, B }) => {
      fs.symlinkSync(B, path.join(A, 'jlink'), 'junction');                       // inside the root, points outside
      fs.symlinkSync(A, path.join(dir, 'alias-root'), 'junction');                // another spelling of the root
    },
    code: `const I = (p) => G.__guardV4.isInside(p); const r = {};
r.root = I(A); r.child = I(path.join(A, 'a', 'b.txt')); r.dots = I(A + '/./a/../b'); r.trailing = I(A + path.sep); r.dotdot = I(path.join(A, '..', 'outside', 'x')); r.sibling = I(A + '-evil/x');
r.sibling_via_dotdot = I(A + '/../allowed-evil/x'); r.junction_out = I(path.join(A, 'jlink', 'f.txt')); r.alias_in = I(path.join(DIR, 'alias-root', 'x.txt')); r.upper = I(A.toUpperCase());
r.deep_missing_inside = I(path.join(A, 'n1', 'n2', 'n3.txt')); r.deep_missing_outside = I(path.join(B, 'n1', 'n2', 'n3.txt'));
process.chdir(A); r.rel_inside = I('rel.txt'); process.chdir(B); r.rel_outside = I('rel.txt');
fs.writeFileSync(path.join(A, 'inside.txt'), 'x'); fs.writeFileSync(path.join(A, 'jlink', 'g.txt'), 'x'); fs.writeFileSync(path.join(B, 'direct.txt'), 'x');
console.log(JSON.stringify(r));`,
  });
  const want = { root: true, child: true, dots: true, trailing: true, dotdot: false, sibling: false, sibling_via_dotdot: false, junction_out: false, alias_in: true, upper: isWin, deep_missing_inside: true, deep_missing_outside: false, rel_inside: true, rel_outside: false };
  const bad = Object.keys(want).filter((k) => s.out?.[k] !== want[k]);
  check('containment: every case judged as expected (sibling prefix, .., junction out, alias spelling, case, relative, missing paths)', s.r.status === 0 && bad.length === 0, `wrong: ${bad.join(',')} ${tail(s)}`);
  const outside = s.kinds('fs.writeFileSync.outside');
  check('genuine outside writes are still recorded: through the junction and directly, not the inside write', outside.length === 2 && outside.some((x) => x.detail.endsWith('/jlink/g.txt')) && outside.some((x) => x.detail.endsWith('/outside/direct.txt')) && outside.every((x) => x.denied === false), JSON.stringify(outside.map((x) => x.detail)));
}

// ---- 8. Node temp and PowerShell temp: the same directory in two spellings
{
  const s = scenario({
    pre: ({ dir }) => { fs.mkdirSync(path.join(dir, 'real-temp')); fs.symlinkSync(path.join(dir, 'real-temp'), path.join(dir, 'alias-temp'), 'junction'); },
    roots: ({ dir }) => [path.join(dir, 'alias-temp')],
    cfg: ({ dir }) => ({ recorded: { powershell_temp_raw: path.join(dir, 'real-temp') } }),
    env: ({ dir }) => ({ TEMP: path.join(dir, 'alias-temp'), TMP: path.join(dir, 'alias-temp'), TMPDIR: path.join(dir, 'alias-temp') }),
    code: "fs.writeFileSync(path.join(DIR, 'real-temp', 'a.txt'), 'x'); fs.writeFileSync(path.join(DIR, 'alias-temp', 'b.txt'), 'x'); fs.writeFileSync(path.join(os.tmpdir(), 'c.txt'), 'x'); console.log(JSON.stringify(G.__guardV4.counters()));",
  });
  const conf = s.kinds('guard-config')[0]?.config;
  check('temp spellings: writes through either spelling and os.tmpdir() are inside (nothing recorded as outside)', s.r.status === 0 && s.recs.filter((x) => /\.outside$/.test(x.kind)).length === 0, tail(s));
  check('temp spellings: the config record holds both temp paths, their canonical forms and says they are one directory', conf?.node_tmpdir.raw.endsWith('alias-temp') && conf?.powershell_temp.raw.endsWith('real-temp') && conf?.node_tmpdir.canonical === conf?.powershell_temp.canonical && conf?.temp_paths_same_directory === true);
  check('temp spellings: the config record lists the effective roots, raw and canonical', conf?.allowed_roots.length === 1 && conf.allowed_roots[0].raw.endsWith('alias-temp') && conf.allowed_roots[0].canonical.endsWith('real-temp'));
}

// ---- 10. protected TARGETS behind innocent spellings, with the file system INTERCEPTED and the link targets VIRTUAL: before the guard is installed the child replaces the file functions under
// test with recorders that never touch the disk, and replaces fs.realpathSync.native (which the guard captures for its canonical form) with one that maps <root>/vlink to <root>/.vault, a folder that
// does not exist. So nothing protected exists, and a refusal is proven by the recorder never being called. Both arguments of two-path calls, every API style (sync, promise), and controls.
{
  const s = scenario({
    prelude: false,
    code: `import fs from 'node:fs'; import path from 'node:path';
const A = process.env.SC_A, B = process.env.SC_B, CFG = process.env.ATHENA_GUARD_CONFIG, isWin = process.platform === 'win32';
const VLINK = path.join(A, 'vlink'), VAULT = path.join(A, '.vault');
const key = (p) => { const t = String(p).replaceAll('\\\\', '/'); return isWin ? t.toLowerCase() : t; };
const resolved = [];
const origRealpath = fs.realpathSync.native;
fs.realpathSync.native = (p, ...rest) => { const t = String(p); resolved.push(t); const k = key(t), from = key(VLINK); if (k === from || k.startsWith(from + '/')) return VAULT + t.slice(VLINK.length); return origRealpath(p, ...rest); };
// only calls that name a path in this scenario's folder are intercepted (the configuration excepted); everything else, such as the ESM loader reading the guard's own source, passes through
const ours = (p) => { const t = key(p); return t.startsWith(key(process.env.SC_DIR) + '/') && t !== key(CFG); };
const calls = [];
for (const m of ['readFileSync', 'writeFileSync', 'appendFileSync', 'renameSync', 'copyFileSync', 'cpSync', 'linkSync', 'symlinkSync', 'existsSync', 'statSync']) { const orig = fs[m]; fs[m] = (...a) => (ours(a[0]) || ours(a[1]) ? (calls.push(m), undefined) : orig(...a)); }
for (const m of ['rename', 'copyFile', 'writeFile']) { const orig = fs.promises[m]; fs.promises[m] = async (...a) => (ours(a[0]) || ours(a[1]) ? (calls.push('promises.' + m), undefined) : orig(...a)); }
const G = await import(process.env.GUARD_URL);
if (!G.__guardV4?.state) throw new Error('the guard did not install');
const r = {};
const attempt = async (label, f) => { const before = calls.length; let refused = false, other = null; try { await f(); } catch (e) { refused = String(e.message).includes('blocked by the offline guard'); if (!refused) other = String(e.message).slice(0, 120); } r[label] = { refused, invoked: calls.length - before, other }; };
await attempt('write_via_link', () => fs.writeFileSync(path.join(VLINK, 'secret.txt'), 'x'));
await attempt('read_via_link', () => fs.readFileSync(path.join(VLINK, 'secret.txt')));
await attempt('exists_via_link', () => fs.existsSync(path.join(VLINK, 'secret.txt')));
await attempt('link_itself', () => fs.statSync(VLINK));
await attempt('rename_destination_via_link', () => fs.renameSync(path.join(B, 'src.txt'), path.join(VLINK, 'moved.txt')));
await attempt('rename_source_via_link', () => fs.renameSync(path.join(VLINK, 'k.txt'), path.join(A, 'dst.txt')));
await attempt('copy_destination_via_link', () => fs.copyFileSync(path.join(A, 'a.txt'), path.join(VLINK, 'b.txt')));
await attempt('symlink_target_via_link', () => fs.symlinkSync(path.join(VLINK, 't'), path.join(A, 'newlink')));
await attempt('promise_rename_destination_via_link', () => fs.promises.rename(path.join(A, 'a.txt'), path.join(VLINK, 'c.txt')));
await attempt('promise_write_via_link', () => fs.promises.writeFile(path.join(VLINK, 'd.txt'), 'x'));
const before = resolved.length;
await attempt('supplied_protected_destination', () => fs.renameSync(path.join(A, 'a.txt'), path.join(A, '.vault2', 'x.txt')));
r.supplied_protected_never_resolved = !resolved.slice(before).some((p) => p.includes('.vault2'));
await attempt('control_inside_write', () => fs.writeFileSync(path.join(A, 'ok.txt'), 'x'));
await attempt('control_inside_rename', () => fs.renameSync(path.join(A, 'a.txt'), path.join(A, 'b.txt')));
await attempt('control_outside_write', () => fs.writeFileSync(path.join(B, 'o.txt'), 'x'));
await attempt('control_promise_copy_inside', () => fs.promises.copyFile(path.join(A, 'a.txt'), path.join(A, 'e.txt')));
console.log(JSON.stringify(r));`,
  });
  const refusedCases = ['write_via_link', 'read_via_link', 'exists_via_link', 'link_itself', 'rename_destination_via_link', 'rename_source_via_link', 'copy_destination_via_link', 'symlink_target_via_link', 'promise_rename_destination_via_link', 'promise_write_via_link', 'supplied_protected_destination'];
  const controls = ['control_inside_write', 'control_inside_rename', 'control_outside_write', 'control_promise_copy_inside'];
  const wrongRefused = refusedCases.filter((k) => !(s.out?.[k]?.refused === true && s.out[k].invoked === 0));
  const wrongControl = controls.filter((k) => !(s.out?.[k]?.refused === false && s.out[k].invoked === 1 && s.out[k].other === null));
  check('protected target behind a link (intercepted, virtual): every call is refused and the original function is never called (one-path, both arguments of two-path calls, sync and promise)', s.r.status === 0 && wrongRefused.length === 0, `wrong: ${wrongRefused.join(',')} first: ${JSON.stringify(s.out?.[wrongRefused[0]])} ${tail(s)}`);
  check('protected target behind a link: the controls (inside and outside, not protected) still reach the original function exactly once', s.r.status === 0 && wrongControl.length === 0, `wrong: ${wrongControl.join(',')} ${JSON.stringify(wrongControl.map((k) => s.out?.[k]))}`);
  check('protected spelling: refused from the text alone, never resolved (the protected path is not touched to judge it)', s.out?.supplied_protected_never_resolved === true);
  const denied = s.recs.filter((x) => x.denied === true && /^fs\./.test(x.kind));
  check('protected target behind a link: each refusal is recorded as denied, naming the supplied path and its canonical form', denied.length === refusedCases.length && denied.filter((x) => /^canonical \S*\/\.vault(\/|\s)/.test(String(x.detail).replaceAll('\\', '/').toLowerCase()) && String(x.detail).includes(' <- supplied ')).length === refusedCases.length - 1, `denied ${denied.length}: ${JSON.stringify(denied.map((x) => x.detail.slice(0, 90)))}`);
  const outside = s.kinds('fs.renameSync.outside');
  check('protected target behind a link: the outside SOURCE of a refused rename is still recorded (nothing hidden), and the outside control write too', outside.length === 1 && outside[0].detail.replaceAll('\\', '/').endsWith('/outside/src.txt') && s.kinds('fs.writeFileSync.outside').length === 1);
}

// ---- 11. the same with a REAL directory junction into an (empty) dot-folder, and the real file system: nothing is written through it, and a junction to an ordinary folder still works
{
  const s = scenario({
    pre: ({ A }) => {
      fs.mkdirSync(path.join(A, '.realvault')); fs.symlinkSync(path.join(A, '.realvault'), path.join(A, 'rlink'), 'junction');
      fs.mkdirSync(path.join(A, 'plain')); fs.symlinkSync(path.join(A, 'plain'), path.join(A, 'plink'), 'junction');
      fs.writeFileSync(path.join(A, 'src.txt'), 'x');
    },
    code: `const r = {}; const t = (k, f) => { try { f(); r[k] = 'ran'; } catch (e) { r[k] = String(e.message).includes('blocked by the offline guard') ? 'refused' : 'error: ' + e.message; } };
t('write', () => fs.writeFileSync(path.join(A, 'rlink', 'n.txt'), 'x')); t('copy_destination', () => fs.copyFileSync(path.join(A, 'src.txt'), path.join(A, 'rlink', 'c.txt')));
t('rename_destination', () => fs.renameSync(path.join(A, 'src.txt'), path.join(A, 'rlink', 'm.txt'))); t('control_plain_junction', () => fs.writeFileSync(path.join(A, 'plink', 'ok.txt'), 'x'));
console.log(JSON.stringify(r));`,
  });
  check('real junction into a dot-folder: write, copy and rename through it are refused; a junction to an ordinary folder still works', s.r.status === 0 && s.out?.write === 'refused' && s.out?.copy_destination === 'refused' && s.out?.rename_destination === 'refused' && s.out?.control_plain_junction === 'ran', `${JSON.stringify(s.out)} ${tail(s)}`);
  check('real junction into a dot-folder: nothing reached the dot-folder, the source was not moved, and the ordinary write landed', fs.readdirSync(path.join(s.A, '.realvault')).length === 0 && fs.existsSync(path.join(s.A, 'src.txt')) && fs.existsSync(path.join(s.A, 'plain', 'ok.txt')));
}

// ---- 9. the judge itself: a broken line and a write-failure record make a log incomplete
{
  const dir = path.join(work, 'judge'); fs.mkdirSync(dir);
  const log = path.join(dir, 'x.jsonl');
  fs.writeFileSync(log, ['{"kind":"guard-config","config":{}}', '{"kind":"fetch","detail":"/api/x","denied":true}', 'not json', '{"kind":"log-write-failure","detail":"1 earlier write(s) failed"}', '{"kind":"fs.writeFileSync.outside","detail":"/o/x","denied":false}'].join('\n') + '\n');
  const j = judge(log);
  check('judge: unparseable lines and write-failure records make the log incomplete, genuine events are still counted', j.unparseable_lines === 1 && j.write_failure_records === 1 && j.complete === false && j.fetch_denied_relative === 1 && j.fs_outside === 1);
}

fs.rmSync(work, { recursive: true, force: true });
console.log(results.join('\n'));
console.log(`\n${results.filter((r) => r.startsWith('PASS')).length} passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
process.exit(results.some((r) => r.startsWith('FAIL')) ? 1 : 0);
