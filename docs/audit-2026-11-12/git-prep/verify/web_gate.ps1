# The web gate on a candidate: the pinned isolation guard v4 (a TRIPWIRE and not a sandbox: see guard\GUARD-V4.md and baselines/wp07-2026-11-15/GUARD-LIMITS.md) with an empty variable-file directory,
# TypeScript check, the guard's self-test and its multi-process regressions, the Vitest suite with every test ID saved, and the PRODUCTION BUILD. Offline (npm_config_offline); dependencies come from an
# existing node_modules (linked, never installed here; in CI `npm ci` runs first and no link is made). Playwright/browser checks are NOT part of this gate (the e2e harness is not maintained yet: listed as
# not established).
#
# Guard v4 replaces v3 for this gate; v3 (baselines/wp07-2026-11-15/scripts/guard.setup.v3.mjs) stays in the repository unchanged as the historical approved file and the gate checks that it still is.
# The four v4 files (guard, self-test, regressions, log judge) are PINNED by SHA-256 and used byte for byte: nothing in them is patched per machine. The machine-specific facts (log file, allowed roots)
# are written to a JSON configuration that the guard reads (ATHENA_GUARD_CONFIG), together with what this script saw of the temp folders, so the log records both. The allowed roots are the same as before:
# the candidate's web folder, its node_modules, the scratch folder, the node install and the temp folder as node sees it; they are judged by canonical path, not by spelling.
# A guard hit is recorded, never hidden: the baseline suite had 130 DENIED relative fetches (unstubbed default collaborators) and zero other violations, so "no hits" is not required; any absolute-URL
# fetch, any other denied call, any filesystem call outside the candidate, and ANY loss in the log itself (overflow, a failed write, a re-entry, an unparseable line) FAILS the gate.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$NodeModules,      # an existing terminal\web\node_modules (linked when the candidate has none)
    [Parameter(Mandatory)][string]$Out,
    [Parameter(Mandatory)][string]$Work,             # scratch folder outside the repository: guard run folders, empty variable-file directory, Vite cache, build output
    [string]$ApprovedGuardSha256 = '600924933BC19692A4B5F40CB114708165712F4823FA5132976F48DA29F342C8',
    [string]$ApprovedSelftestSha256 = '6BF12D2E4F18BC6E72B231E1893C3F02FEEDD69B5EE4C412EA8AB4B5A2A5AF7D',
    [string]$ApprovedRegressionsSha256 = 'DE6EEB2F91B492DDBB7A77AFF10B7721909A451BD8E83DED3C8C1076C9E3334A',
    [string]$ApprovedJudgeSha256 = 'B981D8AE8D0C39C73127774F564533AAE28CAC28F6139D04E66EAFE64A8E3AE9',
    [string]$HistoricalGuardV3Sha256 = '08B7B5A3DBAB4834E9A63BD000ADF9A00547050707C550646AA8C693D3548BBF'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
$Work = New-RunDir $Work
$web = "$Candidate\terminal\web"
$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue); if (-not $npm) { throw 'preflight failed: npm.cmd not found' }; $npm = $npm.Source
$guardDir = "$Candidate\docs\audit-2026-11-12\git-prep\verify\guard"
$pins = [ordered]@{ 'guard.setup.v4.mjs' = $ApprovedGuardSha256; 'guard_selftest_v4.mjs' = $ApprovedSelftestSha256; 'guard_regressions_v4.mjs' = $ApprovedRegressionsSha256; 'guard_judge_v4.mjs' = $ApprovedJudgeSha256 }
$pinFacts = [ordered]@{}
foreach ($f in $pins.Keys) {
    if (-not (Test-Path "$guardDir\$f")) { throw "the pinned guard file is missing from the candidate: $f" }
    $h = (Get-FileHash "$guardDir\$f" -Algorithm SHA256).Hash
    $pinFacts[$f] = [ordered]@{ pinned = $pins[$f]; actual = $h; matches = ($h -eq $pins[$f]) }
    if ($h -ne $pins[$f]) { throw "$f is not the pinned file (hash differs)" }
}
$pinsOk = $true
$v3Path = "$Candidate\docs\audit-2026-11-12\baselines\wp07-2026-11-15\scripts\guard.setup.v3.mjs"
$v3Unchanged = ((Test-Path $v3Path) -and ((Get-FileHash $v3Path -Algorithm SHA256).Hash -eq $HistoricalGuardV3Sha256))

New-Item -ItemType Directory -Force "$Work\no-dotfiles", "$Work\vite-cache", "$Work\guard-run\runs\selftest", "$Work\guard-run\runs\web" | Out-Null
$nmReal = (Get-Item -LiteralPath $NodeModules).Target; if ($nmReal) { $nmReal = @($nmReal)[0] } else { $nmReal = (Resolve-Path -LiteralPath $NodeModules).Path }
$nodeDir = Split-Path -Parent (Get-Command node).Source

# what PowerShell and node each believe the temp folder is, and their canonical forms (recorded, never assumed equal)
$psTemp = [IO.Path]::GetTempPath()
$probeFile = "$Work\guard-run\probe-temp.js"
@'
const fs = require('node:fs'), os = require('node:os');
const canon = (p) => { try { return fs.realpathSync.native(p); } catch { return null; } };
console.log(JSON.stringify({ node_tmpdir: os.tmpdir(), node_tmpdir_canonical: canon(os.tmpdir()), powershell_temp_canonical: canon(process.argv[2]) }));
'@ | Set-Content $probeFile -Encoding ascii
$nodeTemp = (& node $probeFile $psTemp) | ConvertFrom-Json
if (-not $nodeTemp -or -not $nodeTemp.node_tmpdir) { throw 'could not read the temp folder as node sees it (the guard roots would be incomplete)' }
$canonFile = "$Work\guard-run\canon-path.js"
@'
const fs = require('node:fs');
let c = '';
try { c = fs.realpathSync.native(process.argv[2]).split('\\').join('/').toLowerCase(); } catch { /* the log does not exist */ }
console.log(c);
'@ | Set-Content $canonFile -Encoding ascii
$roots = @($web, $nmReal, $Work, $nodeDir, $nodeTemp.node_tmpdir)
$recorded = [ordered]@{ powershell_temp_raw = $psTemp; powershell_temp_canonical_seen_by_node = $nodeTemp.powershell_temp_canonical; node_tmpdir_raw = $nodeTemp.node_tmpdir; node_tmpdir_canonical = $nodeTemp.node_tmpdir_canonical; gate_work = $Work; candidate = $Candidate }
function New-GuardConfig([string]$name) {
    $cfg = [ordered]@{ version = 'v4'; run_id = $name; log_file = "$Work\guard-run\runs\$name\guard-hits.jsonl"; allowed_roots = $roots; max_records = 2000; recorded = $recorded }
    $p = "$Work\guard-run\config-$name.json"
    [IO.File]::WriteAllText($p, ($cfg | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
    return $p
}
$cfgSelftest = New-GuardConfig 'selftest'
$cfgWeb = New-GuardConfig 'web'
$guardFacts = [ordered]@{ version = 'v4'; pinned_files = $pinFacts; historical_v3_sha256 = $HistoricalGuardV3Sha256; historical_v3_file_unchanged = $v3Unchanged; allowed_roots_given = $roots; temp = $recorded
    configuration = 'written per run by this script (log file, roots, limits); the guard files themselves are used byte for byte (no line is patched)' }

# node_modules: link only when the candidate has none
$linked = $false
if (-not (Test-Path "$web\node_modules")) { New-Item -ItemType Junction -Path "$web\node_modules" -Target $NodeModules | Out-Null; $linked = $true }
try {
    foreach ($k in 'npm_config_update_notifier', 'npm_config_audit', 'npm_config_fund') { Set-Item "Env:$k" 'false' }
    Set-Item 'Env:npm_config_offline' 'true'
    $facts = [ordered]@{ node = (node --version); npm = (& $npm --version); typescript = (Get-Content "$web\node_modules\typescript\package.json" -Raw | ConvertFrom-Json).version; vitest = (Get-Content "$web\node_modules\vitest\package.json" -Raw | ConvertFrom-Json).version; vite = (Get-Content "$web\node_modules\vite\package.json" -Raw | ConvertFrom-Json).version }
    Write-Json $facts "$Out\toolchain-versions.json"

    $tsc = Invoke-Stage 'tsc' $web $npm @('exec', '--', 'tsc', '--noEmit') @{} "$Out\tsc.log"

    # guard self-test (it must report no FAIL line) and the multi-process regressions (their own configurations and logs under the temp folder)
    $st = Invoke-Stage 'guard self-test' $guardDir 'node' @("$guardDir\guard_selftest_v4.mjs") @{ ATHENA_GUARD_CONFIG = $cfgSelftest } "$Out\guard-selftest.log"
    $stFails = @($st.text -split "`n" | Where-Object { $_ -match '^FAIL' }).Count
    $stPass = @($st.text -split "`n" | Where-Object { $_ -match '^PASS' }).Count
    $rg = Invoke-Stage 'guard regressions' $guardDir 'node' @("$guardDir\guard_regressions_v4.mjs") @{} "$Out\guard-regressions.log"
    $rgFails = @($rg.text -split "`n" | Where-Object { $_ -match '^FAIL' }).Count
    $rgPass = @($rg.text -split "`n" | Where-Object { $_ -match '^PASS' }).Count

    # Vitest under the guard, envDir = an empty directory
    $root = ($web -replace '\\', '/'); $root = $root.Substring(0, 1).ToUpper() + $root.Substring(1)
    $gd = ($guardDir -replace '\\', '/'); $gd = $gd.Substring(0, 1).ToUpper() + $gd.Substring(1)
    $cfg = "$Out\vitest.config.mjs"
    @"
export default {
  root: '$root',
  envDir: '$($Work -replace '\\','/')/no-dotfiles',
  cacheDir: '$($Work -replace '\\','/')/vite-cache',
  server: { fs: { allow: ['$root', '$gd'] } },
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['$gd/guard.setup.v4.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
    $vt = Invoke-Stage 'vitest' $web $npm @('test', '--', '--config', $cfg, '--reporter=default', '--reporter=json', "--outputFile.json=$Out\web-results.json") @{ ATHENA_GUARD_CONFIG = $cfgWeb } "$Out\web-test.log"

    # production build: the project's own build options (checked below), written to the run folder, with the same empty variable-file directory
    $viteCfg = Get-Content "$web\vite.config.ts" -Raw
    $drift = -not ($viteCfg -match "build:\s*\{\s*outDir:\s*'dist',\s*target:\s*'es2022',\s*sourcemap:\s*false\s*\}")
    $bcfg = "$Out\build.config.mjs"
    @"
export default {
  root: '$root',
  envDir: '$($Work -replace '\\','/')/no-dotfiles',
  cacheDir: '$($Work -replace '\\','/')/vite-cache',
  build: { outDir: '$($Work -replace '\\','/')/dist', emptyOutDir: true, target: 'es2022', sourcemap: false },
};
"@ | Set-Content $bcfg -Encoding utf8
    $bd = Invoke-Stage 'production build' $web $npm @('exec', '--', 'vite', 'build', '--config', $bcfg) @{} "$Out\build.log"
} finally {
    if ($linked) { cmd /c rmdir "$web\node_modules" | Out-Null }   # the junction only, never the target
}

$w = if (Test-Path "$Out\web-results.json") { Get-Content "$Out\web-results.json" -Raw | ConvertFrom-Json } else { $null }
$ids = @()
if ($w) {
    $ids = @($w.testResults | ForEach-Object { $f = ($_.name -replace '^.*[\\/]terminal[\\/]web[\\/]', '') -replace '\\', '/'; $_.assertionResults | ForEach-Object { "$f :: $($_.fullName)  [$($_.status)]" } } | Sort-Object)
    Set-Content "$Out\web-test-ids.txt" $ids
}
# the hit logs are judged by the pinned judge (what the log contains, and whether the log itself is complete); the logs are kept with the run
function Get-Judgement([string]$logFile) {
    if (-not (Test-Path $logFile)) { return [pscustomobject]@{ exists = $false; complete = $false; fs_outside = 0; other_denied = 0; fetch_denied_absolute = 0; fetch_denied_relative = 0; lines = 0; overflow_records = 0; write_failure_records = 0; reentry_records = 0; unparseable_lines = 0; configs = 0; summaries = 0; first_config = $null; summary_dropped_total = 0; summary_write_failures_total = 0; summary_reentered_total = 0; fs_outside_examples = @() } }
    $canon = (& node $canonFile $logFile)
    return ((& node "$guardDir\guard_judge_v4.mjs" $logFile $canon) | ConvertFrom-Json)
}
$webLog = "$Work\guard-run\runs\web\guard-hits.jsonl"
$stLog = "$Work\guard-run\runs\selftest\guard-hits.jsonl"
if (Test-Path $webLog) { Copy-Item $webLog "$Out\guard-hits.jsonl" }
if (Test-Path $stLog) { Copy-Item $stLog "$Out\guard-selftest-hits.jsonl" }
$j = Get-Judgement $webLog
$jst = Get-Judgement $stLog
Write-Json $j "$Out\guard-judgement-web.json"
Write-Json $jst "$Out\guard-judgement-selftest.json"
Write-Json ([ordered]@{ powershell_temp_raw = $psTemp; seen_by_node = $nodeTemp; allowed_roots_given = $roots; guard_recorded_first_config = $j.first_config; guard_recorded_first_config_selftest = $jst.first_config; environment_names_seen = @('TEMP', 'TMP', 'RUNNER_TEMP') | ForEach-Object { [ordered]@{ name = $_; value = [Environment]::GetEnvironmentVariable($_) } } }) "$Out\temp-paths.json"
$markers = @(Get-Content "$Out\web-test.log" -ErrorAction SilentlyContinue | Where-Object { $_ -match 'ATHENA-GUARD-V4 ' })
$distFiles = if (Test-Path "$Work\dist") { @(Get-ChildItem "$Work\dist" -Recurse -File) } else { @() }
$checks = [ordered]@{
    approved_guard_file = $pinsOk
    historical_guard_v3_unchanged = $v3Unchanged
    guard_selftest_all_pass = ($st.exit -eq 0 -and $stFails -eq 0 -and $stPass -gt 0)
    guard_regressions_all_pass = ($rg.exit -eq 0 -and $rgFails -eq 0 -and $rgPass -gt 0)
    tsc_exit_zero = ($tsc.exit -eq 0)
    vitest_exit_zero = ($vt.exit -eq 0)
    results_file_written = [bool]$w
    no_failed_tests_or_suites = ($w -and $w.numFailedTests -eq 0 -and $w.numFailedTestSuites -eq 0 -and @($w.testResults | Where-Object { $_.status -ne 'passed' }).Count -eq 0)
    tests_ran = ($w -and $w.numTotalTests -gt 0 -and $ids.Count -eq $w.numTotalTests)
    guard_config_recorded = ($j.configs -ge 1 -and $null -ne $j.first_config -and @($j.first_config.allowed_roots).Count -ge 1 -and $null -ne $j.first_config.node_tmpdir)
    no_log_overflow = ($j.overflow_records -eq 0 -and $j.summary_dropped_total -eq 0)
    no_log_write_failures = ($j.write_failure_records -eq 0 -and $j.summary_write_failures_total -eq 0 -and $markers.Count -eq 0)
    no_logger_reentry = ($j.reentry_records -eq 0 -and $j.summary_reentered_total -eq 0)
    hit_log_complete = ($j.exists -and $j.unparseable_lines -eq 0)
    guard_selftest_log_complete = ($jst.exists -and $jst.complete)
    no_absolute_url_fetch = ($j.fetch_denied_absolute -eq 0)
    no_other_denied_call = ($j.other_denied -eq 0)
    no_fs_outside_project = ($j.fs_outside -eq 0)
    build_options_not_drifted = (-not $drift)
    build_exit_zero = ($bd.exit -eq 0)
    build_wrote_index_and_js = ((@($distFiles | Where-Object Name -eq 'index.html').Count -ge 1) -and (@($distFiles | Where-Object Extension -eq '.js').Count -ge 1))
}
$ok = -not @($checks.GetEnumerator() | Where-Object { -not $_.Value }).Count
$sum = [ordered]@{
    candidate = $Candidate; toolchain = $facts; guard = $guardFacts; empty_variable_file_dir = "$Work\no-dotfiles"
    tests = if ($w) { [ordered]@{ files = @($w.testResults).Count; total = $w.numTotalTests; passed = $w.numPassedTests; failed = $w.numFailedTests; pending = $w.numPendingTests } } else { $null }
    guard_selftest = [ordered]@{ pass_lines = $stPass; fail_lines = $stFails }
    guard_regressions = [ordered]@{ pass_lines = $rgPass; fail_lines = $rgFails }
    guard_hits = [ordered]@{ total_records = $j.lines; denied_relative_fetch = $j.fetch_denied_relative; denied_absolute_fetch = $j.fetch_denied_absolute; other_denied = $j.other_denied; fs_outside_project = $j.fs_outside; fs_outside_examples = @($j.fs_outside_examples)
        log_complete = $j.complete; overflow_records = $j.overflow_records; write_failure_records = $j.write_failure_records; reentry_records = $j.reentry_records; summaries = $j.summaries; configs = $j.configs; stderr_markers = $markers.Count
        baseline_reference = 'baseline run: 895 tests, 130 denied relative fetches, no other kind' }
    production_build = [ordered]@{ files = $distFiles.Count; bytes = ($distFiles | Measure-Object Length -Sum).Sum; log = "$Out\build.log" }
    not_covered = 'Playwright/browser checks; worker threads and native addons are outside the guard (GUARD-LIMITS.md)'
    checks = $checks; ok = [bool]$ok
}
Write-Json $sum "$Out\summary.json"
"web gate ok=$ok tests=$($sum.tests.passed)/$($sum.tests.total) denied relative fetch=$($sum.guard_hits.denied_relative_fetch) abs=$($j.fetch_denied_absolute) other=$($j.other_denied) outside=$($j.fs_outside) log_complete=$($j.complete) selftest=$stPass/$stFails regressions=$rgPass/$rgFails build files=$($distFiles.Count)"
if (-not $ok) { exit 1 }
exit 0
