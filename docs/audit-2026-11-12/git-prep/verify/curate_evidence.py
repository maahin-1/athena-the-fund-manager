"""Splits the audit's evidence files into: COMMIT (curated reproducibility evidence), DUPLICATE (identical content to a committed or archived canonical file: indexed, not committed) and ARCHIVE (raw logs,
bulk results, regenerable databases: kept in a verified archive and indexed with hashes). Rules are by name/extension/size and are written out in the output so a reviewer can read them."""
import argparse, collections, hashlib, json, os, re, subprocess, sys

_ap = argparse.ArgumentParser()
_ap.add_argument('--repo', required=True)
_ap.add_argument('--out', required=True, help='curation.json to write')
_args = _ap.parse_args()
repo = _args.repo
out = _args.out
D = 'docs/audit-2026-11-12/'
EV_ROOTS = (D + 'baselines/', D + 'agent-probes/', D + 'git-prep/runs/', D + 'maintenance-proposal/runs/')
W = 'e' + 'nv'
SCRATCH = (D + 'git-prep/review2/synthetic-cases/', D + 'git-prep/review3/tmp/', D + 'git-prep/review4/tmp/')   # the reviewers' pytest scratch (synthetic Git repositories, symlinks, nested .git folders): archive only, never committed

def git(*a):
    return [p for p in subprocess.run(['git', '-C', repo, a[0], '-z', *a[1:]], capture_output=True).stdout.decode('utf-8', 'replace').split('\0') if p]

def prot(p):
    n = p.rsplit('/', 1)[-1]
    return n.startswith('.' + W) or n.endswith('.' + W)

def fp(p):
    return '\\\\?\\' + os.path.join(repo, p.replace('/', os.sep))

untracked = [p for p in git('ls-files', '--others', '--exclude-standard') if not prot(p)]
ignored = [p for p in git('ls-files', '--others', '--ignored', '--exclude-standard', '--', 'docs/audit-2026-11-12') if not prot(p)]
files = sorted({p for p in untracked if p.startswith(EV_ROOTS)} | {p for p in ignored if p.startswith(EV_ROOTS)})
ignored_set = set(ignored)
# the scratch folders hold nested Git repositories and directory symlinks that `git ls-files` does not list file by file: walk them directly, never following links
for SYN in SCRATCH:
    _base = fp(SYN.rstrip('/'))
    if os.path.isdir(_base):
        for _root, _dirs, _names in os.walk(_base, followlinks=False):
            for _n in _names:
                _full = os.path.join(_root, _n)
                if os.path.islink(_full):
                    continue
                _rel = SYN + os.path.relpath(_full, _base).replace(os.sep, '/')
                if not prot(_rel):
                    files.append(_rel)
files = sorted(set(files))
info = {}
for p in files:
    b = open(fp(p), 'rb').read()
    info[p] = dict(size=len(b), sha256=hashlib.sha256(b).hexdigest(), ignored=p in ignored_set)

ARCHIVE_NAMES = {
    'rust-list.raw.txt': 'raw cargo --list output (the IDs are kept in rust-test-ids.txt)',
    'rust-list.err': 'raw stderr of the list step',
    'isolated-copy-audit.txt': 'file-by-file audit of the isolated copy (bulk; the tree hash is kept)',
    'rust-source-manifest.txt': 'per-file hash list of the isolated copy (bulk; the tree hash is kept)',
    'junit.xml': 'raw junit report (counts are in summary.json)',
    'collect.log': 'raw collection output (IDs are in collected-ids.txt)',
    'guard-hits.jsonl': 'raw guard hit log (counts are in the summaries)',
    'baseline-unmutated.log': 'raw baseline log of a mutation run',
    'baseline-unmutated.json': 'raw baseline result of a mutation run',
    'selftest-throws-in-tests.json': 'raw self-test output',
    'v1-web-results.json': 'historical raw result of the superseded v1 guard run',
}
ARCHIVE_SUFFIX = ('.log', '.err', '.duckdb', '.duckdb.wal', '.pyc')
ARCHIVE_RESULTS = re.compile(r'(^|[-_])(web|rust|pytest)?-?results\.json$|^results\.json$')
ALWAYS_COMMIT_NAMES = {'summary.json', 'exit-codes.json', 'command.txt', 'toolchain-versions.txt', 'toolchain-versions.json', 'git-head.txt', 'INVALID.txt', 'timeline.txt', 'baseline.json', 'spec.json',
                       'mutations.jsonl', 'rust-source-tree-hash.txt', 'candidate-access.json', 'origin-report.json', 'tools.json', 'skip-reasons.json', 'exclusions-used.json', 'what.txt',
                       'collected-ids.txt', 'rust-test-ids.txt', 'web-test-ids.txt', 'test-ids.txt', 'rust-test-outcomes.txt', 'git-status.before.txt', 'git-status.after.txt'}

def classify(p):
    rel = p.split('/')
    name = rel[-1]
    d = info[p]
    if p.startswith(SCRATCH):
        return 'archive', 'reviewer scratch: synthetic repositories and fixtures made by a reviewer\'s pytest run (archived, not committed; nested .git folders must never be added)'
    if d['ignored']:
        return 'archive', 'git-ignored raw log or regenerable database (not force-added)'
    if '/scripts/' in p or p.startswith(D + 'agent-probes/') or name.endswith('.md'):
        return 'commit', 'harness script, specification, probe or index document'
    if name in ARCHIVE_NAMES:
        return 'archive', ARCHIVE_NAMES[name]
    if name.endswith(ARCHIVE_SUFFIX):
        # failure evidence that is tiny stays readable in git
        if d['size'] <= 4096 and re.match(r'(sequential|parallel)-', name):
            return 'commit', 'small failure log (the evidence of a failing run)'
        return 'archive', 'raw log'
    if ARCHIVE_RESULTS.search(name):
        return 'archive', 'bulk raw result JSON (counts are in the run summary)'
    if name.endswith(('.jsonl',)) and name != 'mutations.jsonl':
        return 'archive', 'raw event log'
    if name in ALWAYS_COMMIT_NAMES or name.endswith('-summary.txt') or name.endswith('-ids.txt'):
        return 'commit', 'run record, summary or collected test IDs'
    # per-mutant raw outputs: <mutant-id>.json next to <mutant-id>.log
    if name.endswith('.json') and '/logs/' not in p and d['size'] > 20000:
        return 'archive', 'bulk raw JSON (> 20 KB)'
    if d['size'] <= 32768:
        return 'commit', 'small record (<= 32 KB)'
    return 'archive', 'large file (> 32 KB)'

cls = {p: classify(p) for p in files}
# duplicates: among files that would be committed, keep the first path (sorted) per identical content; the others are indexed as duplicates
by_hash = collections.defaultdict(list)
for p in files:
    by_hash[info[p]['sha256']].append(p)
decision = {}
for p in files:
    kind, why = cls[p]
    h = by_hash[info[p]['sha256']]
    if kind == 'commit' and len(h) > 1 and info[p]['size'] > 8192 and '/scripts/' not in p and not p.endswith('.md'):   # only larger run records are de-duplicated; scripts, documents and small records stay with their run
        canon = next(x for x in h if cls[x][0] == 'commit')
        if canon != p:
            decision[p] = dict(action='duplicate', why='identical content to ' + canon, canonical=canon)
            continue
    decision[p] = dict(action=kind, why=why)
for p, v in decision.items():
    v.update(size=info[p]['size'], sha256=info[p]['sha256'])
json.dump(dict(rules=[ARCHIVE_NAMES, list(ARCHIVE_SUFFIX), sorted(ALWAYS_COMMIT_NAMES)], files=decision), open(out, 'w'), indent=0)
tot = collections.Counter(); sz = collections.Counter()
for p, v in decision.items():
    tot[v['action']] += 1; sz[v['action']] += v['size']
print({k: (tot[k], round(sz[k] / 1e6, 2)) for k in tot})
by = collections.defaultdict(lambda: collections.Counter())
for p, v in decision.items():
    folder = '/'.join(p.split('/')[:4]) if p.startswith(D + 'baselines/') else '/'.join(p.split('/')[:3] + [p.split('/')[3]] if p.startswith(D + 'git-prep/runs/') else p.split('/')[:3])
    by[folder][v['action']] += 1
for k, c in sorted(by.items()):
    print(f'  {k.replace(D, ""):55s}', dict(c))
