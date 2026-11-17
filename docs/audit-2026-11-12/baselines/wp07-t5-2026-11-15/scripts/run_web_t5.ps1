# Web stage for T5, in a uniquely named run directory: a TypeScript check, then the existing `npm test` script under guard v3 with a scratch vitest config.
# A run with any failed test FILE (a suite that did not load) or a non-zero exit is marked INVALID in its summary; it is never summarised as a pass.
param([string]$Label = 't5-web')
$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$b = $base -replace '\\', '/'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$Label"
$out = "$base\runs\$id"
New-Item -ItemType Directory -Force $out, "$base\no-dotfiles" | Out-Null
Set-Content "$base\current-run.txt" $id   # guard v3 writes runs/<this id>/guard-hits.jsonl
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content "$out\timeline.txt" }
Stamp "run $id"
git -C $repo status --short | Set-Content "$out\git-status.before.txt"
@("node: $(node --version)", "npm: $(& 'C:\Program Files\nodejs\npm.cmd' --version)", "vitest: $((Get-Content "$repo\terminal\web\node_modules\vitest\package.json" -Raw | ConvertFrom-Json).version)", "typescript: $((Get-Content "$repo\terminal\web\node_modules\typescript\package.json" -Raw | ConvertFrom-Json).version)") | Set-Content "$out\toolchain-versions.txt"
$env:npm_config_update_notifier = 'false'; $env:npm_config_audit = 'false'; $env:npm_config_fund = 'false'; $env:npm_config_offline = 'true'
Set-Location "$repo\terminal\web"
Stamp 'tsc --noEmit (no build output is written)'
& 'C:\Program Files\nodejs\npm.cmd' exec -- tsc --noEmit *> "$out\tsc.log"
$tscExit = $LASTEXITCODE; Stamp "tsc exit $tscExit"
$cfg = "$out\vitest.config.mjs"
@"
export default {
  root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web',
  envDir: '$b/no-dotfiles',
  cacheDir: '$b/vite-cache',
  server: { fs: { allow: ['d:/projects/learn-project/athena-the-fund-manager/terminal/web', '$b'] } },
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['$b/guard.setup.v3.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
Stamp 'npm test -- --config <run dir> --reporter=default --reporter=json'
& 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg --reporter=default --reporter=json --outputFile.json="$out\web-results.json" *> "$out\web-test.log"
$testExit = $LASTEXITCODE; Stamp "npm test exit $testExit"
$w = Get-Content "$out\web-results.json" -Raw | ConvertFrom-Json
$failedFiles = @($w.testResults | Where-Object { $_.status -ne 'passed' }).Count
$valid = ($testExit -eq 0) -and ($failedFiles -eq 0) -and ($w.numFailedTests -eq 0) -and ($w.numFailedTestSuites -eq 0)
$summary = @("RUN $(if ($valid) { 'VALID' } else { 'INVALID: a suite did not load, a test failed, or npm test exited non-zero' })",
             "tsc exit=$tscExit; npm test exit=$testExit; files=$(@($w.testResults).Count) files_not_passed=$failedFiles tests=$($w.numTotalTests) passed=$($w.numPassedTests) failed=$($w.numFailedTests) failed_suites=$($w.numFailedTestSuites) pending=$($w.numPendingTests)")
$w.testResults | ForEach-Object { $f = ($_.name -replace '^.*[\\/]terminal[\\/]web[\\/]', '') -replace '\\', '/'; $_.assertionResults | ForEach-Object { "$f :: $($_.fullName)  [$($_.status)]" } } | Sort-Object | Set-Content "$out\web-test-ids.txt"
$hits = if (Test-Path "$out\guard-hits.jsonl") { @(Get-Content "$out\guard-hits.jsonl" | ForEach-Object { $_ | ConvertFrom-Json }) } else { @() }
$summary += "guard v3 records: " + (($hits | Group-Object kind, denied | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join '; ')
$summary += "denied fetch: $(@($hits | Where-Object { $_.kind -eq 'fetch' }).Count); absolute: $(@($hits | Where-Object { $_.kind -eq 'fetch' -and $_.detail -match '^[a-z]+://' }).Count); other denied: $(@($hits | Where-Object { $_.denied -and $_.kind -ne 'fetch' }).Count); fs outside: $(@($hits | Where-Object { $_.kind -like 'fs.*.outside' }).Count)"
$summary | Set-Content "$out\web-summary.txt"
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
Stamp 'done'
$summary
$out
