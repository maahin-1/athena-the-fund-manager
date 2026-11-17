# Derives the list of Python-backed Rust tests (rust-python-tests.json) EMPIRICALLY: every non-ignored test is run alone, with its own import-origin log, and the tests that started at least one Python
# process on the intended interpreter (<candidate>\terminal\..\.venv\Scripts\python.exe) are recorded. A test that does not start Python is not on the list.
# Run this when Rust tests are added or changed; rust_gate.ps1 then re-runs exactly the listed tests and fails when one of them stops starting Python (an early return) or when the list is empty.
# Each listed test needs at least ONE process (the count seen when derived is kept for information only: kill and cancellation timing can change it by one). The whole run must observe at least half of
# the derived total: a sanity floor, because concurrency changes the exact number by a few. -FromIsolated rebuilds the list from the saved isolated-all.json of an earlier derivation run (no cargo is started).
#
# Offline, synthetic data only (the Rust tests use fake data and inline Python snippets); no environment file is read; the junction .venv -> the interpreter's environment is read-only use and is removed afterwards.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out,            # must not exist
    [Parameter(Mandatory)][string]$CargoTarget,
    [Parameter(Mandatory)][string]$ListOut,        # where the derived list is written (must not exist)
    [string]$FromIsolated = '',                    # an earlier run's isolated-all.json: recompute the list from it instead of running the tests
    [switch]$Online
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
if (Test-Path -LiteralPath $ListOut) { throw "list file exists (never overwritten): $ListOut" }
$results = New-Object System.Collections.Generic.List[object]
$source = $Out
if ($FromIsolated) {
    foreach ($row in (Get-Content -LiteralPath $FromIsolated -Raw | ConvertFrom-Json)) { $results.Add($row) }
    $source = Split-Path -Parent $FromIsolated
} else {
    $Candidate = (Resolve-Path -LiteralPath $Candidate).Path
    $tools = Get-ToolFacts $Python
    $missing = @($tools.GetEnumerator() | Where-Object { $_.Value -eq 'MISSING' } | ForEach-Object { $_.Key })
    if ($missing.Count) { throw "preflight failed: $($missing -join ', ')" }
    $venvRoot = Split-Path -Parent (Split-Path -Parent $Python)
    $link = Join-Path $Candidate '.venv'
    $madeLink = $false
    if (-not (Get-Command sh -ErrorAction SilentlyContinue)) { $env:Path = 'C:\Program Files\Git\bin;' + $env:Path }
    if (-not (Test-Path -LiteralPath $link)) { New-Item -ItemType Junction -Path $link -Target $venvRoot | Out-Null; $madeLink = $true }
    try {
        $e = @{ CARGO_TARGET_DIR = $CargoTarget; CARGO_NET_OFFLINE = $(if ($Online) { 'false' } else { 'true' }) }
        $net = if ($Online) { @() } else { @('--offline') }
        $term = "$Candidate\terminal"
        $build = Invoke-Stage 'build' $term 'cargo' (@('test') + $net + @('--locked', '--no-run')) $e "$Out\build.log"
        if ($build.exit -ne 0) { throw 'build failed' }
        $all = Invoke-Stage 'list' $term 'cargo' (@('test') + $net + @('--locked', '--', '--list')) $e "$Out\list.log"
        $ign = Invoke-Stage 'list-ignored' $term 'cargo' (@('test') + $net + @('--locked', '--', '--list', '--ignored')) $e "$Out\list-ignored.log"
        $idsOf = { param($t) @($t -split "`n" | Where-Object { $_ -match ': test$' } | ForEach-Object { ($_ -replace ': test$', '').Trim() }) }
        $ignored = @(& $idsOf $ign.text)
        $ids = @(& $idsOf $all.text | Where-Object { $ignored -notcontains $_ } | Sort-Object)
        $i = 0
        foreach ($id in $ids) {
            $i++
            $results.Add((Invoke-RustTestIsolated $id $i $term $net $e $Candidate $Out))
            if ($i % 25 -eq 0) { "derive: $i / $($ids.Count)" }
        }
    } finally {
        if ($madeLink) { cmd /c rmdir "$link" | Out-Null }
    }
    Write-Json $results.ToArray() "$Out\isolated-all.json"
}
# a test that does not pass when run ALONE depends on another test's side effects (shared process state): reported, because it is a finding, but it is not Python-backed evidence either way
$alone = @($results | Where-Object { $_.exit -ne 0 -or $_.passed -ne 1 -or $_.failed -ne 0 })
$other = @($results | Where-Object { $_.processes_on_other_executable -gt 0 })
$backed = @($results | Where-Object { $_.processes_on_intended_executable -gt 0 } | Sort-Object { $_.id })
$backedFailing = @($backed | Where-Object { $_.exit -ne 0 -or $_.passed -ne 1 -or $_.failed -ne 0 })
$total = 0; foreach ($b in $backed) { $total += [int]$b.processes_on_intended_executable }
$floor = [int][math]::Floor($total / 2)
$list = [ordered]@{
    note = 'Python-backed Rust tests: each, run alone, started at least one Python process on the intended interpreter. Derived by derive_rust_python_tests.ps1 (re-derive when Rust tests change). Each needs at least min_python_processes (1) when the gate re-runs it alone; processes_when_derived is information (kill and cancellation timing can change it by one). full_run_min_processes is a sanity floor for the whole run: half of the derived total.'
    derived_from_run = $source
    tests_run_alone = $results.Count
    not_exactly_one_pass_when_run_alone = @($alone | ForEach-Object { $_.id })
    tests = @($backed | ForEach-Object { [ordered]@{ id = $_.id; min_python_processes = 1; processes_when_derived = [int]$_.processes_on_intended_executable } })
    derived_total_processes = [int]$total
    full_run_min_processes = $floor
}
Write-Json $list $ListOut
$sum = [ordered]@{ tests_run_alone = $results.Count; not_exactly_one_pass_when_run_alone = $alone.Count; python_backed_failing_alone = $backedFailing.Count; on_another_interpreter = $other.Count; python_backed = $backed.Count; total_python_processes = [int]$total; full_run_min_processes = $floor; rebuilt_from = $FromIsolated; list = $ListOut }
$sum.ok = ($backedFailing.Count -eq 0 -and $other.Count -eq 0 -and $backed.Count -gt 0)
Write-Json $sum "$Out\summary.json"
$sum | ConvertTo-Json
if (-not $sum.ok) { exit 1 }
exit 0
