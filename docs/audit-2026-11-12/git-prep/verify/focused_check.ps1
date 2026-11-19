# The focused Python check of ONE commit: runs the named test files in the candidate with the candidate's interpreter arguments and the import-origin guard, and fails (never skips) on:
# a missing file, no test collected, a failure or error, any skipped test, a count that differs from the collected IDs, or `athena` imported from outside the candidate.
# Used per commit by the plan (the commit's own tests; the broad run is python_gate.ps1). The Rust, web and cross-language commits use rust_gate.ps1, web_gate.ps1 and crosslang.ps1.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out,
    [Parameter(Mandatory)][string]$Scratch,
    [Parameter(Mandatory)][string[]]$Tests          # repo-relative test files, e.g. tests/test_supervisor.py
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
New-Item -ItemType Directory -Force "$Scratch\tmp", "$Scratch\base" | Out-Null
$missing = @($Tests | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Candidate $_)) })
if ($missing.Count) { Write-Json ([ordered]@{ ok = $false; reason = "test file(s) missing in the candidate: $($missing -join ', ')" }) "$Out\summary.json"; throw "missing test files: $($missing -join ', ')" }
$log = "$Out\origin.jsonl"
Enable-OriginGuard $Candidate $log
$e = @{ TEMP = "$Scratch\tmp"; TMP = "$Scratch\tmp" }
$col = Invoke-Stage 'collect' $Candidate $Python (@('-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider') + $Tests) $e "$Out\collect.log"
$ids = @($col.text -split "`n" | Where-Object { $_ -match '^tests/.*::' } | ForEach-Object { $_.Trim() })
Set-Content "$Out\collected-ids.txt" $ids
$run = Invoke-Stage 'run' $Candidate $Python (@('-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider', "--basetemp=$Scratch\base") + $Tests) $e "$Out\pytest.log"
Disable-OriginGuard
$c = Get-PytestCounts $run.text
$origin = (& $Python "$PSScriptRoot\origin_report.py" $log --min-athena-processes 0 --out "$Out\origin-report.json") | ConvertFrom-Json
$checks = [ordered]@{ tests_collected = ($ids.Count -gt 0); exit_zero = ($run.exit -eq 0); no_failures_or_errors = ($c.failed -eq 0 -and $c.errors -eq 0); no_skips = ($c.skipped -eq 0); count_matches_collected = (($c.passed + $c.failed + $c.skipped + $c.errors) -eq $ids.Count); no_import_from_outside_candidate = [bool]$origin.ok; recorder_was_active = ($origin.processes_observed -ge 1) }   # a test file that never imports athena is fine; a recorder that saw no process at all is not
$ok = -not @($checks.GetEnumerator() | Where-Object { -not $_.Value }).Count
Write-Json ([ordered]@{ candidate = $Candidate; tests = $Tests; collected = $ids.Count; counts = $c; checks = $checks; import_origin = $origin; ok = [bool]$ok }) "$Out\summary.json"
"focused check ok=$ok collected=$($ids.Count) passed=$($c.passed) skipped=$($c.skipped) failed=$($c.failed)"
if (-not $ok) { exit 1 }
exit 0
