# Web stage for the T5 review corrections, in a uniquely named run directory under ..\runs: a TypeScript check, then the existing `npm test` script under guard v3 with a scratch vitest config.
# A run with any failed test FILE (a suite that did not load), a failed test or a non-zero exit is marked INVALID in its summary; it is never summarised as a pass.
# The guard (guard.setup.v3.mjs, a TRIPWIRE, not a sandbox: see ..\..\wp07-2026-11-15\GUARD-LIMITS.md) writes its hits where its own BASE says (the scratch run directory of the same name); they are copied here at the end.
# usage: run_web.ps1 -Label web-red [-Files test/provenance.test.ts,test/labview.test.ts]
param([string]$Label = 'web', [string[]]$Files = @())
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path "$here\..\..\..\..\..").Path
$runs = (Resolve-Path "$here\..").Path + '\runs'
$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$b = $base -replace '\\', '/'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$Label"
$out = "$runs\$id"
New-Item -ItemType Directory $out | Out-Null   # never reuse a run directory
New-Item -ItemType Directory -Force "$base\runs\$id", "$base\no-dotfiles" | Out-Null
Set-Content "$base\current-run.txt" $id
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content "$out\timeline.txt" }
Stamp "run $id"
git -C $repo status --short | Set-Content "$out\git-status.before.txt"
(git -C $repo rev-parse HEAD) | Set-Content "$out\git-head.txt"
@("node: $(node --version)", "npm: $(& 'C:\Program Files\nodejs\npm.cmd' --version)", "vitest: $((Get-Content "$repo\terminal\web\node_modules\vitest\package.json" -Raw | ConvertFrom-Json).version)", "typescript: $((Get-Content "$repo\terminal\web\node_modules\typescript\package.json" -Raw | ConvertFrom-Json).version)") | Set-Content "$out\toolchain-versions.txt"
$env:npm_config_update_notifier = 'false'; $env:npm_config_audit = 'false'; $env:npm_config_fund = 'false'; $env:npm_config_offline = 'true'
Set-Location "$repo\terminal\web"
Stamp 'tsc --noEmit (no build output is written)'
& 'C:\Program Files\nodejs\npm.cmd' exec -- tsc --noEmit *> "$out\tsc.log"
$tscExit = $LASTEXITCODE; Stamp "tsc exit $tscExit"
$cfg = "$out\vitest.config.mjs"
$include = if ($Files.Count) { ($Files | ForEach-Object { "'$_'" }) -join ', ' } else { "'test/**/*.test.ts'" }
@"
export default {
  root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web',
  envDir: '$b/no-dotfiles',
  cacheDir: '$b/vite-cache',
  server: { fs: { allow: ['d:/projects/learn-project/athena-the-fund-manager/terminal/web', '$b'] } },
  test: { environment: 'node', include: [$include], setupFiles: ['$b/guard.setup.v3.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
Set-Content "$out\command.txt" "cwd=$repo\terminal\web npm_config_offline=true`nnpm exec -- tsc --noEmit`nnpm test -- --config $cfg --reporter=default --reporter=json --outputFile.json=$out\web-results.json"
Stamp 'npm test'
& 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg --reporter=default --reporter=json --outputFile.json="$out\web-results.json" *> "$out\web-test.log"
$testExit = $LASTEXITCODE; Stamp "npm test exit $testExit"
$w = if (Test-Path "$out\web-results.json") { Get-Content "$out\web-results.json" -Raw | ConvertFrom-Json } else { $null }
if ($null -eq $w) { Set-Content "$out\INVALID.txt" 'no results file was written'; $summary = @('RUN INVALID: no results file') }
else {
  $failedFiles = @($w.testResults | Where-Object { $_.status -ne 'passed' }).Count
  $valid = ($testExit -eq 0) -and ($failedFiles -eq 0) -and ($w.numFailedTests -eq 0) -and ($w.numFailedTestSuites -eq 0) -and ($tscExit -eq 0)
  $summary = @("RUN $(if ($valid) { 'VALID (all green)' } else { 'NOT GREEN: a suite did not load, a test failed, tsc failed or npm test exited non-zero (a red run is evidence, never a pass)' })",
               "tsc exit=$tscExit; npm test exit=$testExit; files=$(@($w.testResults).Count) files_not_passed=$failedFiles tests=$($w.numTotalTests) passed=$($w.numPassedTests) failed=$($w.numFailedTests) failed_suites=$($w.numFailedTestSuites) pending=$($w.numPendingTests)")
  $w.testResults | ForEach-Object { $f = ($_.name -replace '^.*[\\/]terminal[\\/]web[\\/]', '') -replace '\\', '/'; $_.assertionResults | ForEach-Object { "$f :: $($_.fullName)  [$($_.status)]" } } | Sort-Object | Set-Content "$out\web-test-ids.txt"
}
if (Test-Path "$base\runs\$id\guard-hits.jsonl") { Copy-Item "$base\runs\$id\guard-hits.jsonl" "$out\guard-hits.jsonl" }
$hits = if (Test-Path "$out\guard-hits.jsonl") { @(Get-Content "$out\guard-hits.jsonl" | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() }
$summary += "guard v3 records: " + (($hits | Group-Object kind, denied | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join '; ')
$summary += "denied fetch: $(@($hits | Where-Object { $_.kind -eq 'fetch' }).Count); absolute: $(@($hits | Where-Object { $_.kind -eq 'fetch' -and $_.detail -match '^[a-z]+://' }).Count); other denied: $(@($hits | Where-Object { $_.denied -and $_.kind -ne 'fetch' }).Count); fs outside: $(@($hits | Where-Object { $_.kind -like 'fs.*.outside' }).Count)"
$summary | Set-Content "$out\web-summary.txt"
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
Stamp 'done'
$summary
$out
