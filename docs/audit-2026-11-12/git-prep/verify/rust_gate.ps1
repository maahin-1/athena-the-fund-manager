# The Rust gate on a candidate: `cargo test --locked` (offline unless -Online) with the intended capabilities present, the test list saved, strict count checks, AND evidence that the Python-backed tests ran.
#
# The Rust tests start the project's Python through the RELATIVE path `../.venv/Scripts/python.exe` (cargo runs them from `terminal`) and need a POSIX `sh`: both are REQUIRED here (a missing one is a failed
# preflight, never a skipped test). Many of those tests return early and PASS when the interpreter file is missing, so a green count is not evidence. The gate therefore:
#   1. runs that exact path from `terminal` and requires: it runs, it is the candidate's `.venv` python, its environment is the intended one (the interpreter given as -Python), `athena` imports from the candidate;
#   2. runs the whole suite under the import-origin recorder (strict counts; at least the recorded number of Python processes; none on another interpreter; no `athena` from outside the candidate);
#   3. runs every test on the recorded list (rust-python-tests.json) ALONE with its own log: each must pass exactly once and start at least one Python process on the intended interpreter. Zero observed
#      Python children is never counted as coverage, and an empty list fails. rust_evidence.py judges the facts.
# The Python for those tests is reached through a junction named .venv in the candidate root pointing at the interpreter's environment (read use only; removed afterwards; an existing .venv, as on CI, is used as it is).
# A compile or load failure is a failure ("INVALID"), not a pass. The 2 ignored tests are the cross-language ones; crosslang.ps1 runs them.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out,
    [Parameter(Mandatory)][string]$CargoTarget,    # outside the candidate
    [int]$ExpectedPassed = 417,
    [int]$ExpectedIgnored = 2,
    [string]$PythonTests = '',                   # default: rust-python-tests.json beside this script ($PSScriptRoot is not available inside a param block in Windows PowerShell 5.1)
    [switch]$Online
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
if (-not $PythonTests) { $PythonTests = Join-Path $PSScriptRoot 'rust-python-tests.json' }
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
$tools = Get-ToolFacts $Python; Write-Json $tools "$Out\tools.json"
$missing = @($tools.GetEnumerator() | Where-Object { $_.Value -eq 'MISSING' } | ForEach-Object { $_.Key })
if ($missing.Count) { Write-Json ([ordered]@{ ok = $false; reason = "required tools missing: $($missing -join ', ')" }) "$Out\summary.json"; throw "preflight failed: $($missing -join ', ')" }
if (-not (Test-Path -LiteralPath $PythonTests)) { Write-Json ([ordered]@{ ok = $false; reason = 'the recorded list of Python-backed tests is missing' }) "$Out\summary.json"; throw "missing: $PythonTests" }
$venvRoot = Split-Path -Parent (Split-Path -Parent $Python)
$link = Join-Path $Candidate '.venv'
$madeLink = $false
$pathAdd = ''
if (-not (Get-Command sh -ErrorAction SilentlyContinue)) { $pathAdd = 'C:\Program Files\Git\bin'; $env:Path = "$pathAdd;" + $env:Path }
if (-not (Test-Path -LiteralPath $link)) { New-Item -ItemType Junction -Path $link -Target $venvRoot | Out-Null; $madeLink = $true }   # an existing .venv (CI) is used as it is and never removed
$isolated = New-Object System.Collections.Generic.List[object]
try {
    $e = @{ CARGO_TARGET_DIR = $CargoTarget; CARGO_NET_OFFLINE = $(if ($Online) { 'false' } else { 'true' }) }
    $net = if ($Online) { @() } else { @('--offline') }
    $term = "$Candidate\terminal"

    # 1. the interpreter path the tests invoke, run exactly as they do (relative to `terminal`)
    $probe = "$Out\interpreter_probe.py"
    [IO.File]::WriteAllText($probe, "import json, os, sys`nimport athena`nprint(json.dumps({'executable': sys.executable, 'prefix': os.path.realpath(sys.prefix), 'version': sys.version.split()[0], 'athena': os.path.realpath(athena.__file__)}))`n", (New-Object System.Text.UTF8Encoding($false)))
    $pe = @{ PYTHONPATH = ((Join-Path $Candidate 'src')); PYTHONDONTWRITEBYTECODE = '1' }
    $ip = Invoke-Stage 'interpreter' $term '..\.venv\Scripts\python.exe' @($probe) $pe "$Out\interpreter.log"
    $intended = (& $Python -c "import os, sys; print(os.path.realpath(sys.prefix))" | Select-Object -First 1)
    $facts = [ordered]@{ ran = $false; is_candidate_venv_path = $false; same_environment_as_intended = $false; athena_from_candidate = $false }
    if ($ip.exit -eq 0) {
        $j = ($ip.text -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1) | ConvertFrom-Json
        $facts.ran = $true
        $facts.is_candidate_venv_path = ([IO.Path]::GetFullPath($j.executable) -ieq [IO.Path]::GetFullPath((Join-Path $Candidate '.venv\Scripts\python.exe')))
        $facts.same_environment_as_intended = ([IO.Path]::GetFullPath($j.prefix) -ieq [IO.Path]::GetFullPath($intended))
        $facts.athena_from_candidate = (([IO.Path]::GetFullPath($j.athena) + '\') -like ([IO.Path]::GetFullPath((Join-Path $Candidate 'src')) + '\*'))
        $facts.executable = $j.executable; $facts.version = $j.version; $facts.prefix = $j.prefix
    }
    Write-Json $facts "$Out\interpreter.json"

    # 2. the whole suite under the import-origin recorder
    $log = "$Out\origin.jsonl"
    Enable-OriginGuard $Candidate $log
    try {
        $build = Invoke-Stage 'build' $term 'cargo' (@('test') + $net + @('--locked', '--no-run')) $e "$Out\build.log"
        if ($build.exit -ne 0) { Write-Json ([ordered]@{ ok = $false; invalid = $true; reason = 'the build failed: no test ran' }) "$Out\summary.json"; throw 'build failed (INVALID: no test ran)' }
        $list = Invoke-Stage 'list' $term 'cargo' (@('test') + $net + @('--locked', '--', '--list')) $e "$Out\list.log"
        $ids = @($list.text -split "`n" | Where-Object { $_ -match ': test$' } | ForEach-Object { ($_ -replace ': test$', '').Trim() } | Sort-Object)
        Set-Content "$Out\rust-test-ids.txt" $ids
        $run = Invoke-Stage 'test' $term 'cargo' (@('test') + $net + @('--locked')) $e "$Out\test.log"
    } finally { Disable-OriginGuard }

    # 3. each recorded Python-backed test alone
    $recorded = Get-Content -LiteralPath $PythonTests -Raw | ConvertFrom-Json
    $isoDir = New-RunDir "$Out\isolated"
    $i = 0
    foreach ($t in @($recorded.tests)) { $i++; $isolated.Add((Invoke-RustTestIsolated $t.id $i $term $net $e $Candidate $isoDir)) }
} finally {
    if ($madeLink) { cmd /c rmdir "$link" | Out-Null }          # removes the junction only, never the target
}
Write-Json $isolated.ToArray() "$Out\isolated.json"
$c = Get-CargoCounts $run.text
$lines = @(Get-CargoTestLines $run.text)
Set-Content "$Out\rust-test-outcomes.txt" ($lines | ForEach-Object { "$($_.id) $($_.outcome)" })
$want = [IO.Path]::GetFullPath((Join-Path $Candidate '.venv\Scripts\python.exe'))
$origin = (& $Python "$PSScriptRoot\origin_report.py" "$Out\origin.jsonl" --min-athena-processes 0 --min-processes 1 --executable $want --out "$Out\origin-report.json") | ConvertFrom-Json
& $Python "$PSScriptRoot\rust_evidence.py" --expected $PythonTests --isolated "$Out\isolated.json" --full-origin "$Out\origin-report.json" --interpreter "$Out\interpreter.json" --out "$Out\python-backed-evidence.json" | Out-Null
$evidenceExit = $LASTEXITCODE
$evidence = Get-Content "$Out\python-backed-evidence.json" -Raw | ConvertFrom-Json
$checks = [ordered]@{
    exit_zero = ($run.exit -eq 0)
    no_failures = ($c.failed -eq 0)
    passed_as_expected = ($c.passed -eq $ExpectedPassed)
    ignored_as_expected = ($c.ignored -eq $ExpectedIgnored)
    listed_ids_match_outcomes = ($ids.Count -eq ($c.passed + $c.ignored + $c.failed))
    no_python_import_from_outside_candidate = ($origin.violations -eq 0)
    interpreter_invoked_by_the_tests_is_the_intended_one = ($facts.ran -and $facts.is_candidate_venv_path -and $facts.same_environment_as_intended -and $facts.athena_from_candidate)
    python_backed_tests_ran_and_started_python = ($evidenceExit -eq 0)
}
$ok = -not @($checks.GetEnumerator() | Where-Object { -not $_.Value }).Count
Write-Json ([ordered]@{ candidate = $Candidate; tools = $tools; sh_path_added = $pathAdd; counts = $c; listed = $ids.Count; seconds = $run.seconds; checks = $checks; interpreter = $facts
    python_processes_observed_full_run = $origin.processes_observed; python_backed_tests_listed = $evidence.listed_tests; python_backed_tests_verified = $evidence.tests_verified; evidence_problems = $evidence.reasons; ok = [bool]$ok }) "$Out\summary.json"
"rust gate ok=$ok passed=$($c.passed) failed=$($c.failed) ignored=$($c.ignored) listed=$($ids.Count) ($($run.seconds)s); Python processes in the full run: $($origin.processes_observed); Python-backed tests verified alone: $($evidence.tests_verified)/$($evidence.listed_tests)"
if (-not $ok) { exit 1 }
exit 0
