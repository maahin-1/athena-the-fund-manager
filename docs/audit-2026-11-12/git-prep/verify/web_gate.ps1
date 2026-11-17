# The web gate on a candidate: the approved isolation guard (v3, a TRIPWIRE and not a sandbox: see baselines/wp07-2026-11-15/GUARD-LIMITS.md) with an empty variable-file directory,
# TypeScript check, the guard's self-test, the Vitest suite with every test ID saved, and the PRODUCTION BUILD. Offline (npm_config_offline); dependencies come from an existing node_modules
# (linked, never installed here; in CI `npm ci` runs first and no link is made). Playwright/browser checks are NOT part of this gate (the e2e harness is not maintained yet: listed as not established).
#
# The approved guard file is used byte for byte except TWO machine-specific lines: its hits-log folder (BASE, a scratch folder of the session that wrote it) and its allowed-path list (the original
# checkout's web folder, the session's temp folder, the node install). The run-local copy changes only those two lines (the rule that records or denies is untouched); both hashes and the
# changed-line count are recorded, and the approved file's hash is checked first. The hits log must live under the temp folder (the guard's own logging is otherwise recorded as an outside write and
# loops: found when this gate was first run). A guard hit is recorded, never hidden: the baseline suite had 130 DENIED relative fetches (unstubbed default collaborators) and zero other violations,
# so "no hits" is not required; any absolute-URL fetch, any other denied call, or any filesystem call outside the candidate FAILS the gate.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$NodeModules,      # an existing terminal\web\node_modules (linked when the candidate has none)
    [Parameter(Mandatory)][string]$Out,
    [Parameter(Mandatory)][string]$Work,             # scratch folder outside the repository: guard copy, empty variable-file directory, Vite cache, build output
    [string]$ApprovedGuardSha256 = '08B7B5A3DBAB4834E9A63BD000ADF9A00547050707C550646AA8C693D3548BBF'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
$Work = New-RunDir $Work
$web = "$Candidate\terminal\web"
$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue); if (-not $npm) { throw 'preflight failed: npm.cmd not found' }; $npm = $npm.Source
$guardDir = "$Candidate\docs\audit-2026-11-12\baselines\wp07-2026-11-15\scripts"
foreach ($f in 'guard.setup.v3.mjs', 'guard_selftest_v3.mjs') { if (-not (Test-Path "$guardDir\$f")) { throw "the approved guard file is missing from the candidate: $f" } }
$approvedOk = ((Get-FileHash "$guardDir\guard.setup.v3.mjs" -Algorithm SHA256).Hash -eq $ApprovedGuardSha256)
if (-not $approvedOk) { throw 'guard.setup.v3.mjs is not the approved file (hash differs)' }

# run-local guard copy, under the TEMP folder (see above); removed at the end after its hit log is copied into the run folder
$g = Join-Path ([IO.Path]::GetTempPath()) ('athena-web-guard-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force "$g\runs\selftest", "$g\runs\web", "$Work\no-dotfiles", "$Work\vite-cache" | Out-Null
$base = ($g -replace '\\', '/')
foreach ($f in 'guard.setup.v3.mjs', 'guard_selftest_v3.mjs') { Copy-Item "$guardDir\$f" "$g\$f" }
$nmReal = (Get-Item -LiteralPath $NodeModules).Target; if ($nmReal) { $nmReal = @($nmReal)[0] } else { $nmReal = (Resolve-Path -LiteralPath $NodeModules).Path }
$low = { param($p) (($p -replace '\\', '/').TrimEnd('/')).ToLower() }
$nodeDir = Split-Path -Parent (Get-Command node).Source
$allowedJs = "const allowed = ['" + (& $low $web) + "', '" + (& $low $nmReal) + "', os.tmpdir().replaceAll('\\', '/').toLowerCase(), '" + (& $low $nodeDir) + "', '" + (& $low $Work) + "'];"
$orig = Get-Content "$guardDir\guard.setup.v3.mjs"; $text = Get-Content "$g\guard.setup.v3.mjs" -Raw
$text = [regex]::Replace($text, "(?m)^const BASE = '[^']*';", { "const BASE = '$base';" })
$text = [regex]::Replace($text, "(?m)^const allowed = \[.*\];\s*$", { $allowedJs })
[IO.File]::WriteAllText("$g\guard.setup.v3.mjs", $text, (New-Object System.Text.UTF8Encoding($false)))
$new = Get-Content "$g\guard.setup.v3.mjs"
$changedLines = @(0..([Math]::Max($orig.Count, $new.Count) - 1) | Where-Object { $orig[$_] -ne $new[$_] }).Count
$guardFacts = [ordered]@{ approved_sha256 = $ApprovedGuardSha256; approved_file_matches = $approvedOk; run_local_sha256 = (Get-FileHash "$g\guard.setup.v3.mjs" -Algorithm SHA256).Hash; lines_changed_in_run_local_copy = $changedLines; changed_lines = 'const BASE (hits-log folder) and const allowed (candidate web folder, its node_modules, the temp folder, the node install)' }
if ($changedLines -ne 2) { throw "the run-local guard copy differs from the approved file in $changedLines lines (expected exactly 2)" }

# node_modules: link only when the candidate has none
$linked = $false
if (-not (Test-Path "$web\node_modules")) { New-Item -ItemType Junction -Path "$web\node_modules" -Target $NodeModules | Out-Null; $linked = $true }
try {
    foreach ($k in 'npm_config_update_notifier', 'npm_config_audit', 'npm_config_fund') { Set-Item "Env:$k" 'false' }
    Set-Item 'Env:npm_config_offline' 'true'
    $facts = [ordered]@{ node = (node --version); npm = (& $npm --version); typescript = (Get-Content "$web\node_modules\typescript\package.json" -Raw | ConvertFrom-Json).version; vitest = (Get-Content "$web\node_modules\vitest\package.json" -Raw | ConvertFrom-Json).version; vite = (Get-Content "$web\node_modules\vite\package.json" -Raw | ConvertFrom-Json).version }
    Write-Json $facts "$Out\toolchain-versions.json"

    $tsc = Invoke-Stage 'tsc' $web $npm @('exec', '--', 'tsc', '--noEmit') @{} "$Out\tsc.log"

    # guard self-test (it must report no FAIL line)
    Set-Content "$g\current-run.txt" 'selftest'
    $st = Invoke-Stage 'guard self-test' $g 'node' @("$g\guard_selftest_v3.mjs") @{} "$Out\guard-selftest.log"
    $stFails = @($st.text -split "`n" | Where-Object { $_ -match '^FAIL' }).Count
    $stPass = @($st.text -split "`n" | Where-Object { $_ -match '^PASS' }).Count

    # Vitest under the guard, envDir = an empty directory
    Set-Content "$g\current-run.txt" 'web'
    $root = ($web -replace '\\', '/'); $root = $root.Substring(0, 1).ToUpper() + $root.Substring(1)
    $cfg = "$Out\vitest.config.mjs"
    @"
export default {
  root: '$root',
  envDir: '$($Work -replace '\\','/')/no-dotfiles',
  cacheDir: '$($Work -replace '\\','/')/vite-cache',
  server: { fs: { allow: ['$root', '$base'] } },
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['$base/guard.setup.v3.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
    $vt = Invoke-Stage 'vitest' $web $npm @('test', '--', '--config', $cfg, '--reporter=default', '--reporter=json', "--outputFile.json=$Out\web-results.json") @{} "$Out\web-test.log"

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
$hitsFile = "$g\runs\web\guard-hits.jsonl"
$hits = if (Test-Path $hitsFile) { @(Get-Content $hitsFile | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() }
if (Test-Path $hitsFile) { Copy-Item $hitsFile "$Out\guard-hits.jsonl" }
Remove-Item -LiteralPath $g -Recurse -Force -ErrorAction SilentlyContinue   # the run-local guard copy under TEMP
$deniedFetch = @($hits | Where-Object { $_.kind -eq 'fetch' }).Count
$absFetch = @($hits | Where-Object { $_.kind -eq 'fetch' -and $_.detail -match '^[a-z]+://' }).Count
$otherDenied = @($hits | Where-Object { $_.denied -and $_.kind -ne 'fetch' }).Count
$fsOutside = @($hits | Where-Object { $_.kind -like 'fs.*.outside' }).Count
$distFiles = if (Test-Path "$Work\dist") { @(Get-ChildItem "$Work\dist" -Recurse -File) } else { @() }
$checks = [ordered]@{
    approved_guard_file = $approvedOk
    guard_selftest_all_pass = ($st.exit -eq 0 -and $stFails -eq 0 -and $stPass -gt 0)
    tsc_exit_zero = ($tsc.exit -eq 0)
    vitest_exit_zero = ($vt.exit -eq 0)
    results_file_written = [bool]$w
    no_failed_tests_or_suites = ($w -and $w.numFailedTests -eq 0 -and $w.numFailedTestSuites -eq 0 -and @($w.testResults | Where-Object { $_.status -ne 'passed' }).Count -eq 0)
    tests_ran = ($w -and $w.numTotalTests -gt 0 -and $ids.Count -eq $w.numTotalTests)
    no_absolute_url_fetch = ($absFetch -eq 0)
    no_other_denied_call = ($otherDenied -eq 0)
    no_fs_outside_project = ($fsOutside -eq 0)
    build_options_not_drifted = (-not $drift)
    build_exit_zero = ($bd.exit -eq 0)
    build_wrote_index_and_js = ((@($distFiles | Where-Object Name -eq 'index.html').Count -ge 1) -and (@($distFiles | Where-Object Extension -eq '.js').Count -ge 1))
}
$ok = -not @($checks.GetEnumerator() | Where-Object { -not $_.Value }).Count
$sum = [ordered]@{
    candidate = $Candidate; toolchain = $facts; guard = $guardFacts; empty_variable_file_dir = "$Work\no-dotfiles"
    tests = if ($w) { [ordered]@{ files = @($w.testResults).Count; total = $w.numTotalTests; passed = $w.numPassedTests; failed = $w.numFailedTests; pending = $w.numPendingTests } } else { $null }
    guard_selftest = [ordered]@{ pass_lines = $stPass; fail_lines = $stFails }
    guard_hits = [ordered]@{ total_records = $hits.Count; denied_relative_fetch = ($deniedFetch - $absFetch); denied_absolute_fetch = $absFetch; other_denied = $otherDenied; fs_outside_project = $fsOutside; baseline_reference = 'baseline run: 895 tests, 130 denied relative fetches, no other kind' }
    production_build = [ordered]@{ files = $distFiles.Count; bytes = ($distFiles | Measure-Object Length -Sum).Sum; log = "$Out\build.log" }
    not_covered = 'Playwright/browser checks; worker threads and native addons are outside the guard (GUARD-LIMITS.md)'
    checks = $checks; ok = [bool]$ok
}
Write-Json $sum "$Out\summary.json"
"web gate ok=$ok tests=$($sum.tests.passed)/$($sum.tests.total) denied relative fetch=$($sum.guard_hits.denied_relative_fetch) abs=$absFetch other=$otherDenied build files=$($distFiles.Count)"
if (-not $ok) { exit 1 }
exit 0
