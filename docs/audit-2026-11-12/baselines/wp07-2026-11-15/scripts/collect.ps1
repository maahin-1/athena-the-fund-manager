$o = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$d = 'd:\projects\learn-project\athena-the-fund-manager\docs\audit-2026-11-12\baselines\wp07-2026-11-15'
New-Item -ItemType Directory -Force "$d\scripts", "$d\results", "$d\historical-v1" | Out-Null
$scripts = 'run_baseline3.ps1', 'watch_stop_after_A.ps1', 'run_baseline4.ps1', 'check_names.ps1', 'closure_scan.mjs', 'guard.setup.v2.mjs', 'guard_selftest.mjs', 'vitest.review.config.v2.mjs', 'parse_rust.ps1', 'post.ps1', 'fetch_summary.ps1', 'collect.ps1'
$results = 'timeline3.txt', 'watcher.txt', 'toolchain-versions.txt', 'git-head.txt', 'git-status.before.txt', 'git-status.after.txt', 'git-status.compare.txt', 'helper-log.before.txt', 'helper-log.after.txt', 'repo-touched.txt',
           'isolated-copy-audit.txt', 'isolated-copy-name-check.txt', 'rust-source-manifest.txt', 'rust-source-tree-hash.txt', 'rust-preconditions.txt', 'rust-list.raw.txt', 'rust-test-ids.txt', 'rust-results-summary.txt',
           'rust-test.A-no-sh.log', 'rust-test.B-with-sh.log', 'web-closure-scan.txt', 'web-closure-files.json', 'web-results.json', 'web-test.log', 'web-test-ids.txt', 'guard-hits.jsonl', 'post-summary.txt'
$historical = 'run_baseline.ps1', 'run_baseline2.ps1', 'guard.setup.mjs', 'vitest.review.config.mjs', 'v1-web-results.json', 'v1-web-test.log', 'v1-guard-hits.jsonl', 'timeline.txt', 'timeline2.txt'
foreach ($f in $scripts) { Copy-Item "$o\$f" "$d\scripts\$f" -Force }
foreach ($f in $results) { Copy-Item "$o\$f" "$d\results\$f" -Force }
foreach ($f in $historical) { Copy-Item "$o\$f" "$d\historical-v1\$f" -Force }
# the guard self-test output (the self-test appends its own hits to guard-hits.jsonl in the scratch folder, so the copy above was taken first and the scratch file is restored afterwards)
Set-Location $o
node "$o\guard_selftest.mjs" *> "$d\results\guard-selftest-output.txt"
Copy-Item "$d\results\guard-hits.jsonl" "$o\guard-hits.jsonl" -Force
"collected: scripts=$($scripts.Count) results=$($results.Count + 1) historical=$($historical.Count)"
