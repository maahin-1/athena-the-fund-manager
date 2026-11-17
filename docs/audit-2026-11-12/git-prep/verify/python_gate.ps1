# The broad Python gate on a candidate: the nine named exclusions, collected test IDs, skip reasons, import-origin proof, and count checks. Offline: synthetic fixtures and fakes only (no live
# LLM or broker call; the test suite's `--live` option is never passed). Tests that spawn Python (`python -m ...`, `sys.executable`) inherit the origin guard.
# Fails (never skips) when: a tool is missing, a listed exclusion does not exist (stale manifest), collection errors, zero tests, a failure, an error, a count that differs from the collected
# IDs, an expected count that is given and not met, or any `athena` module imported from outside the candidate.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out,
    [Parameter(Mandatory)][string]$Scratch,        # a writable directory outside the candidate (TEMP/TMP and pytest basetemp)
    [string]$Exclusions = "$PSScriptRoot\python-exclusions.json",
    [int]$ExpectedPassed = -1,                     # set from the recorded baseline run; -1 = record only
    [int]$ExpectedSkipped = -1
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
New-Item -ItemType Directory -Force "$Scratch\tmp", "$Scratch\base" | Out-Null
$tools = Get-ToolFacts $Python; Write-Json $tools "$Out\tools.json"
if ($tools.python -eq 'MISSING') { throw 'preflight failed: the interpreter is missing' }
$ex = (Get-Content $Exclusions -Raw | ConvertFrom-Json).exclusions
$stale = @($ex | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Candidate $_.path)) })
if ($stale.Count) { throw "stale exclusion manifest: $($stale.Count) listed module(s) do not exist in the candidate" }
$ignores = @($ex | ForEach-Object { "--ignore=$($_.path)" })
Copy-Item $Exclusions "$Out\exclusions-used.json"

$log = "$Out\origin.jsonl"
Enable-OriginGuard $Candidate $log
$tmpEnv = @{ TEMP = "$Scratch\tmp"; TMP = "$Scratch\tmp" }
$col = Invoke-Stage 'collect' $Candidate $Python (@('-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider') + $ignores) $tmpEnv "$Out\collect.log"
$ids = @($col.text -split "`n" | Where-Object { $_ -match '^tests/.*::' } | ForEach-Object { $_.Trim() })
Set-Content "$Out\collected-ids.txt" $ids
$collectErrors = @($col.text -split "`n" | Where-Object { $_ -match '^ERROR |error(s)? during collection' }).Count
$run = Invoke-Stage 'run' $Candidate $Python (@('-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider', "--junitxml=$Out\junit.xml", "--basetemp=$Scratch\base") + $ignores) $tmpEnv "$Out\pytest.log"
Disable-OriginGuard

$counts = Get-PytestCounts $run.text
$junit = $null; $skips = @()
if (Test-Path "$Out\junit.xml") {
    [xml]$junit = Get-Content "$Out\junit.xml" -Raw
    $cases = @($junit.testsuites.testsuite.testcase)
    $skips = @($cases | Where-Object { $_.skipped } | ForEach-Object { [pscustomobject]@{ id = "$($_.classname)::$($_.name)"; reason = "$($_.skipped.message)" } })
}
$skipReasons = @($skips | Group-Object reason | Sort-Object Count -Descending | ForEach-Object { [pscustomobject]@{ reason = $_.Name; count = $_.Count; first = ($_.Group | Select-Object -First 1).id } })
Write-Json $skipReasons "$Out\skip-reasons.json"
Write-Json $skips "$Out\skipped-tests.json"
$origin = (& $Python "$PSScriptRoot\origin_report.py" $log --min-athena-processes 1 --out "$Out\origin-report.json") | ConvertFrom-Json
$ran = $counts.passed + $counts.failed + $counts.skipped + $counts.errors + $counts.xfailed + $counts.xpassed
$checks = [ordered]@{
    tests_collected = ($ids.Count -gt 0)
    no_collection_errors = ($collectErrors -eq 0)
    exit_zero = ($run.exit -eq 0)
    no_failures_or_errors = ($counts.failed -eq 0 -and $counts.errors -eq 0)
    count_matches_collected_ids = ($ran -eq $ids.Count)
    expected_passed = ($ExpectedPassed -lt 0 -or $counts.passed -eq $ExpectedPassed)
    expected_skipped = ($ExpectedSkipped -lt 0 -or $counts.skipped -eq $ExpectedSkipped)
    imports_from_candidate = [bool]$origin.ok
}
$ok = -not @($checks.GetEnumerator() | Where-Object { -not $_.Value }).Count
$sum = [ordered]@{
    candidate = $Candidate; tools = $tools; exclusions = @($ex | ForEach-Object { $_.path })
    collected = $ids.Count; counts = $counts; seconds = $run.seconds
    skip_reason_groups = $skipReasons.Count; checks = $checks; import_origin = $origin
    not_covered = 'the nine excluded modules; the suite default live-test skips (--live is never used); silent early-return tests are NOT detected by this gate and need a reviewed list (see the plan)'
    ok = [bool]$ok
}
Write-Json $sum "$Out\summary.json"
"python gate ok=$ok  collected=$($ids.Count) passed=$($counts.passed) skipped=$($counts.skipped) failed=$($counts.failed) errors=$($counts.errors) ($($run.seconds)s)  origin: $($origin.processes_that_imported_athena) processes, $($origin.violations) violations"
if (-not $ok) { exit 1 }
exit 0
