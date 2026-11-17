# The Python -> Rust -> Python -> ... snapshot contract, all FOUR stages, with explicit synthetic directories, the candidate's interpreter arguments and strict outcome checks.
# (tests/test_snapshot_crosslang.py documents the sequence; a plain `pytest tests/test_snapshot_crosslang.py` SKIPS both of its tests, so a green plain run is not evidence. Control A below shows that.)
#
#   1  Python publishes a generation into <Work>\py-out                           pytest ...::test_python_writes_a_generation_for_the_rust_reader        (ATHENA_SNAPSHOT_PY_OUT)
#   2  Rust validates and opens exactly that generation                           cargo test -- --ignored --exact snapshot::tests::a_generation_published_by_the_python_writer_validates_and_opens   (ATHENA_SNAPSHOT_PY_FIXTURE)
#   3  Rust publishes two generations into <Work>\rust-out                        cargo test -- --ignored --exact snapshot::tests::publishes_generations_for_the_python_reader   (ATHENA_SNAPSHOT_RUST_OUT)
#   4  Python validates and opens what Rust published                             pytest ...::test_python_reads_what_the_rust_writer_published           (ATHENA_SNAPSHOT_RUST_OUT)
#
# Every stage must exit 0, run exactly the one named test, and report it passed; "0 tests", a filter that matched nothing, any skip, or a different count FAILS the stage.
# Controls: A) the plain command skips both tests (shows why it is not evidence); B) a generation file tampered after stage 1 makes stage 2 FAIL (the orchestration can fail).
# Python is the interpreter given in -Python (the owner's environment, read only: nothing is installed or retargeted). Every Python process, subprocesses included, is checked for importing `athena` from the candidate.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Work,       # parent of the synthetic fixture directories: must not exist or be empty
    [Parameter(Mandatory)][string]$Out,        # run directory: must not exist
    [string]$CargoTarget = ''                  # cargo target directory outside the candidate
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
if (Test-Path -LiteralPath $Work) { if (@(Get-ChildItem -LiteralPath $Work -Force).Count) { throw "work directory is not empty: $Work" } } else { New-Item -ItemType Directory -Force $Work | Out-Null }
$Work = (Resolve-Path -LiteralPath $Work).Path
$pyOut = New-RunDir "$Work\py-out"; $rustOut = New-RunDir "$Work\rust-out"
$tools = Get-ToolFacts $Python
Write-Json $tools "$Out\tools.json"
$missing = @($tools.GetEnumerator() | Where-Object { $_.Value -eq 'MISSING' } | ForEach-Object { $_.Key })
if ($missing.Count) { Write-Json ([ordered]@{ ok = $false; reason = "required tools missing: $($missing -join ', ')" }) "$Out\summary.json"; throw "preflight failed: $($missing -join ', ') missing (a missing tool is a failure, never a skip)" }

$log = "$Out\origin.jsonl"
Enable-OriginGuard $Candidate $log
$ids = @{ w = 'tests/test_snapshot_crosslang.py::test_python_writes_a_generation_for_the_rust_reader'; r = 'tests/test_snapshot_crosslang.py::test_python_reads_what_the_rust_writer_published' }
$rustPy = 'snapshot::tests::a_generation_published_by_the_python_writer_validates_and_opens'
$rustWr = 'snapshot::tests::publishes_generations_for_the_python_reader'
$envBase = @{}
if ($CargoTarget) { $envBase['CARGO_TARGET_DIR'] = $CargoTarget }
$envBase['CARGO_NET_OFFLINE'] = 'true'
$cargoTail = @('test', '--offline', '--locked', '--', '--ignored', '--exact')
$stages = New-Object System.Collections.Generic.List[object]
function Judge-Pytest($r, [string]$id, [string]$what) {
    $c = Get-PytestCounts $r.text
    $line = $r.text -match [regex]::Escape($id.Split('::')[1]) + '\s+PASSED'
    $ok = ($r.exit -eq 0) -and ($c.passed -eq 1) -and ($c.skipped -eq 0) -and ($c.failed -eq 0) -and ($c.errors -eq 0) -and $line
    return [ordered]@{ stage = $r.name; what = $what; argv = $r.argv; cwd = $r.cwd; exit = $r.exit; counts = $c; named_test_passed = [bool]$line; seconds = $r.seconds; log = $r.log; ok = [bool]$ok }
}
function Judge-Cargo($r, [string]$id, [string]$what) {
    $c = Get-CargoCounts $r.text; $lines = @(Get-CargoTestLines $r.text)
    $mine = @($lines | Where-Object { $_.id -eq $id })
    $ok = ($r.exit -eq 0) -and ($c.passed -eq 1) -and ($c.failed -eq 0) -and ($mine.Count -eq 1) -and ($mine[0].outcome -eq 'ok') -and ($lines.Count -eq 1)
    return [ordered]@{ stage = $r.name; what = $what; argv = $r.argv; cwd = $r.cwd; exit = $r.exit; counts = $c; test_lines = @($lines | ForEach-Object { "$($_.id) $($_.outcome)" }); seconds = $r.seconds; log = $r.log; ok = [bool]$ok }
}
$pytest = @('-m', 'pytest', '-v', '-rs', '-p', 'no:cacheprovider')

# collected IDs, so the intended tests are named and a rename shows up as a failure
$col = Invoke-Stage 'collect' $Candidate $Python @('-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider', 'tests/test_snapshot_crosslang.py') @{} "$Out\0-collect.log"
$collected = @($col.text -split "`n" | Where-Object { $_ -match '^tests/test_snapshot_crosslang.py::' } | ForEach-Object { $_.Trim() })
Set-Content "$Out\collected-ids.txt" $collected
$idsOk = ($collected -contains $ids.w) -and ($collected -contains $ids.r) -and ($collected.Count -eq 2)

# stage 1
$s1 = Invoke-Stage '1 python writes' $Candidate $Python ($pytest + @('--basetemp', "$Work\pytest-1", $ids.w)) @{ ATHENA_SNAPSHOT_PY_OUT = $pyOut } "$Out\1-python-writes.log"
$stages.Add((Judge-Pytest $s1 $ids.w 'Python publishes a generation of w.duckdb'))
$pyFiles = @(Get-ChildItem -LiteralPath $pyOut -Recurse -File | ForEach-Object { $_.FullName.Substring($pyOut.Length + 1) + ' ' + $_.Length })
Set-Content "$Out\1-python-published-files.txt" $pyFiles
# stage 2
$s2 = Invoke-Stage '2 rust reads python' "$Candidate\terminal" 'cargo' ($cargoTail + @($rustPy)) ($envBase + @{ ATHENA_SNAPSHOT_PY_FIXTURE = $pyOut }) "$Out\2-rust-reads-python.log"
$stages.Add((Judge-Cargo $s2 $rustPy 'Rust validates and opens the Python generation'))
# stage 3
$s3 = Invoke-Stage '3 rust writes' "$Candidate\terminal" 'cargo' ($cargoTail + @($rustWr)) ($envBase + @{ ATHENA_SNAPSHOT_RUST_OUT = $rustOut }) "$Out\3-rust-writes.log"
$stages.Add((Judge-Cargo $s3 $rustWr 'Rust publishes two generations'))
$rustFiles = @(Get-ChildItem -LiteralPath $rustOut -Recurse -File | ForEach-Object { $_.FullName.Substring($rustOut.Length + 1) + ' ' + $_.Length })
Set-Content "$Out\3-rust-published-files.txt" $rustFiles
# stage 4
$s4 = Invoke-Stage '4 python reads rust' $Candidate $Python ($pytest + @('--basetemp', "$Work\pytest-4", $ids.r)) @{ ATHENA_SNAPSHOT_RUST_OUT = $rustOut } "$Out\4-python-reads-rust.log"
$stages.Add((Judge-Pytest $s4 $ids.r 'Python validates and opens what Rust published'))

# control A: the plain command (what the earlier C12 check was): both tests skip, exit 0
$ca = Invoke-Stage 'control A: plain pytest' $Candidate $Python ($pytest + @('--basetemp', "$Work\pytest-ca", 'tests/test_snapshot_crosslang.py')) @{} "$Out\control-A-plain-pytest.log"
$caCounts = Get-PytestCounts $ca.text
$controlA = [ordered]@{ what = 'plain pytest of the file: expected to skip both tests and still exit 0 (so it proves nothing)'; exit = $ca.exit; counts = $caCounts; behaves_as_expected = ($ca.exit -eq 0 -and $caCounts.skipped -eq 2 -and $caCounts.passed -eq 0) }

# control B: tamper a copy of the Python generation; stage 2 against it must FAIL
$tam = "$Work\py-tampered"; Copy-Item -LiteralPath $pyOut -Destination $tam -Recurse
$victim = Get-ChildItem -LiteralPath $tam -Recurse -File | Where-Object { $_.Name -ne 'w.duckdb' -and $_.Extension -eq '.duckdb' } | Sort-Object Length -Descending | Select-Object -First 1
if (-not $victim) { throw 'control B: no generation file found to tamper with' }
$fs = [IO.File]::Open($victim.FullName, 'Append'); $fs.WriteByte(0); $fs.Close()
$cb = Invoke-Stage 'control B: tampered generation' "$Candidate\terminal" 'cargo' ($cargoTail + @($rustPy)) ($envBase + @{ ATHENA_SNAPSHOT_PY_FIXTURE = $tam }) "$Out\control-B-tampered.log"
$cbLines = Get-CargoTestLines $cb.text
$controlB = [ordered]@{ what = 'one byte appended to the Python-published generation file before the Rust stage: the stage must fail'; exit = $cb.exit; test_lines = @($cbLines | ForEach-Object { "$($_.id) $($_.outcome)" }); behaves_as_expected = ($cb.exit -ne 0 -and @($cbLines | Where-Object { $_.id -eq $rustPy -and $_.outcome -eq 'FAILED' }).Count -eq 1) }

Disable-OriginGuard
$origin = (& $Python "$PSScriptRoot\origin_report.py" $log --min-athena-processes 4 --out "$Out\origin-report.json") | ConvertFrom-Json
$allOk = ($idsOk) -and (@($stages | Where-Object { -not $_.ok }).Count -eq 0) -and $controlA.behaves_as_expected -and $controlB.behaves_as_expected -and $origin.ok -and ($pyFiles.Count -ge 3) -and ($rustFiles.Count -ge 3)
$sum = [ordered]@{
    candidate = $Candidate; python = $Python; work = $Work; tools = $tools
    collected_ids = $collected; collected_ids_as_intended = $idsOk
    stages = $stages; control_A = $controlA; control_B = $controlB
    python_published_files = $pyFiles.Count; rust_published_files = $rustFiles.Count
    import_origin = $origin
    ok = [bool]$allOk
}
Write-Json $sum "$Out\summary.json"
"crosslang ok=$allOk  stages: " + (($stages | ForEach-Object { "$($_.stage)=$($_.ok)" }) -join '; ') + "; controlA=$($controlA.behaves_as_expected); controlB=$($controlB.behaves_as_expected); origin=$($origin.ok) ($($origin.processes_that_imported_athena) python processes imported athena, $($origin.violations) violations)"
if (-not $allOk) { exit 1 }
exit 0
