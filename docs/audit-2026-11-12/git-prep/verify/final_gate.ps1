# Final verification of a branch from a CLEAN, access-safe checkout. Never run against the working directory: the candidate is built here from the commits, in scratch space that must not exist yet.
#
#   1 build_candidate   sparse checkout of <Ref>; the protected inherited entry is NOT materialized (explicit exception, compared with the ORIGINAL baseline)        -> gates everything below
#   2 compare_manifest  allowlisted file manifest vs the verified source files (--source-root, later backup first), declared line-ending policy only; additions, changes AND removals are checked against
#                       -BaselineRef: the original baseline for the integration branch, the integration TIP for the maintenance branch (whose manifest lists only maintenance changes)
#                                                                                                                                                                    -> gates 3-8
#   3 origin_probe      Python (in-process, subprocess, -m) imports the CANDIDATE's code, not the original editable checkout; negative control
#   4 python_gate       broad offline suite: nine named exclusions, collected IDs, skip reasons, strict counts, import origin
#   5 formula_golden    python scripts/formula_golden.py --check (with the same import-origin guard)
#   6 rust_gate         cargo test --locked: strict counts, sh and Python required, the interpreter the tests invoke validated, every recorded Python-backed test run alone and seen to start Python
#   7 crosslang         the four-stage Python -> Rust -> Python snapshot contract with synthetic directories, controls, no unexpected skip
#   8 web_gate          approved isolation guard v3 with an empty variable-file directory, tsc, Vitest with all test IDs, production build
#   9 hygiene           git status clean, `git diff --check` against the reference, no runtime-data or build-output path tracked                                      (needs a valid candidate only)
#  10 policy            (maintenance branch only) repository policy and workflow policy checks with the real YAML parser
#
# If the candidate cannot be built or is refused (stage 1), nothing else runs: those stages are recorded as "not_run" and the gate fails. If the candidate is not what the manifest says (stage 2),
# stages 3-8 are "not_run" too (gates on the wrong content prove nothing). Independent later stages still all run when one of them fails, and the gate exits non-zero if any stage failed or did not run.
# Everything runs offline against synthetic data: no live LLM, broker or data call; no production store is opened; no environment file is read, copied or created.
param(
    [Parameter(Mandatory)][string]$Source,          # the repository with the commits (never modified)
    [Parameter(Mandatory)][string]$Ref,             # integration/audit-2026-11-12, maintenance/repo-controls, or a commit
    [Parameter(Mandatory)][string]$Python,          # an existing interpreter with the project's dependencies (read use only; never reinstalled or retargeted)
    [Parameter(Mandatory)][string]$NodeModules,     # an existing terminal\web\node_modules (linked read-only)
    [Parameter(Mandatory)][string]$Work,            # scratch parent, must NOT exist: candidate, cargo target, fixtures, caches
    [Parameter(Mandatory)][string]$Out,             # result folder, must NOT exist
    [Parameter(Mandatory)][string]$Expected,        # expected-integration.json or expected-maintenance.json
    [Parameter(Mandatory)][string]$SourceRoots,     # ';'-separated backup "tree" folders holding the verified source files, a later backup first
    [ValidateSet('integration', 'maintenance')][string]$Branch = 'integration',
    [string]$BaselineRef = '37f1fc19846a178083d9ced7a7a25aeffe65b1f9',   # for maintenance: the integration tip commit
    [string]$OriginalBaseline = '37f1fc19846a178083d9ced7a7a25aeffe65b1f9',
    [string]$PyYamlPath = '',                       # maintenance only: read-only folder holding PyYAML when -Python lacks it
    [int]$PythonPassed = -1, [int]$PythonSkipped = 83,
    [int]$RustPassed = 417, [int]$RustIgnored = 2,
    [string]$VerifyDir = ''                         # the stage scripts (default: this script's folder; only the inert orchestration tests point it elsewhere)
)
$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\common.ps1"
if (-not $VerifyDir) { $VerifyDir = $PSScriptRoot }   # $PSScriptRoot is not available inside a param block in Windows PowerShell 5.1
if ($PythonPassed -lt 0) { $PythonPassed = if ($Branch -eq 'maintenance') { 3208 } else { 3093 } }   # the maintenance branch adds the 105 tests of tests/test_ci_repo_policy.py and the 10 of tests/test_ci_checkout_identity.py (ci/expected-counts.json)
$roots = @($SourceRoots -split ';' | Where-Object { $_.Trim() })
if ($roots.Count -eq 0) { throw 'no source root given: the manifest comparison cannot find the verified files' }
foreach ($r in $roots) { if (-not (Test-Path -LiteralPath $r -PathType Container)) { throw "source root not found: $r" } }
# fresh scratch space only: nothing is reused, so no earlier candidate, cache or result can leak into this run
$Out = New-RunDir $Out
$Work = New-RunDir $Work
$cand = "$Work\candidate"; $target = "$Work\cargo-target"
if ($PyYamlPath) { $env:ATHENA_VERIFY_EXTRA_PYTHONPATH = $PyYamlPath }
$results = New-Object System.Collections.Generic.List[object]
$state = @{ candidate = $true; content = $true }
function Stage([string]$name, [string]$needs, [string]$gates, [scriptblock]$body) {
    $reason = $null
    if ($needs -eq 'candidate' -and -not $state.candidate) { $reason = 'the candidate was not built or was refused (stage 1)' }
    if ($needs -eq 'content' -and -not ($state.candidate -and $state.content)) { $reason = if (-not $state.candidate) { 'the candidate was not built or was refused (stage 1)' } else { 'the candidate is not the content the manifest declares (stage 2)' } }
    if ($reason) {
        $results.Add([ordered]@{ stage = $name; status = 'not_run'; exit = $null; ok = $false; reason = $reason; seconds = 0 })
        "{0,-20} NOT RUN: {1}" -f $name, $reason
        return
    }
    $started = Get-Date
    try { $global:LASTEXITCODE = 0; $code = & $body; if ($null -eq $code) { $code = $LASTEXITCODE } } catch { $code = 99; "$name threw: $($_.Exception.Message)" | Add-Content "$Out\errors.txt" }
    $row = [ordered]@{ stage = $name; status = $(if ([int]$code -eq 0) { 'passed' } else { 'failed' }); exit = [int]$code; ok = ([int]$code -eq 0); seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1) }
    $results.Add($row)
    "{0,-20} {1} exit={2} ({3}s)" -f $name, $row.status, $code, $row.seconds
    if ($gates -eq 'candidate') { $state.candidate = $row.ok }
    if ($gates -eq 'content') { $state.content = $row.ok }
}
$V = $VerifyDir
Stage '1 build_candidate' '' 'candidate' {
    & "$V\build_candidate.ps1" -Source $Source -Ref $Ref -Dest $cand -Out "$Out\1-candidate" -Baseline $OriginalBaseline | Out-Null
    $c = $LASTEXITCODE
    if ($c -eq 0 -and -not (Test-Path -LiteralPath $cand -PathType Container)) { return 97 }   # a builder that reports success without producing a candidate is a failure
    $c
}
Stage '2 compare_manifest' 'candidate' 'content' {
    $argv = @("$V\compare_manifest.py", '--expected', $Expected, '--candidate', $cand, '--baseline-repo', $Source, '--baseline-ref', $BaselineRef, '--out', "$Out\2-compare.json")
    foreach ($r in $roots) { $argv += @('--source-root', $r) }
    & $Python @argv | Out-Null; $LASTEXITCODE
}
Stage '3 origin_probe' 'content' '' { & "$V\origin_probe.ps1" -Candidate $cand -Python $Python -Out "$Out\3-origin-probe" | Out-Null; $LASTEXITCODE }
Stage '4 python_gate' 'content' '' { & "$V\python_gate.ps1" -Candidate $cand -Python $Python -Out "$Out\4-python" -Scratch "$Work\py-scratch" -ExpectedPassed $PythonPassed -ExpectedSkipped $PythonSkipped | Out-Null; $LASTEXITCODE }
Stage '5 formula_golden' 'content' '' { & "$V\formula_golden_gate.ps1" -Candidate $cand -Python $Python -Out "$Out\5-formula-golden" | Out-Null; $LASTEXITCODE }
Stage '6 rust_gate' 'content' '' { & "$V\rust_gate.ps1" -Candidate $cand -Python $Python -Out "$Out\6-rust" -CargoTarget $target -ExpectedPassed $RustPassed -ExpectedIgnored $RustIgnored | Out-Null; $LASTEXITCODE }
Stage '7 crosslang' 'content' '' { & "$V\crosslang.ps1" -Candidate $cand -Python $Python -Work "$Work\xl-work" -Out "$Out\7-crosslang" -CargoTarget $target | Out-Null; $LASTEXITCODE }
Stage '8 web_gate' 'content' '' { & "$V\web_gate.ps1" -Candidate $cand -NodeModules $NodeModules -Out "$Out\8-web" -Work "$Work\web-work" | Out-Null; $LASTEXITCODE }
Stage '9 hygiene' 'candidate' '' { & "$V\hygiene_gate.ps1" -Candidate $cand -BaseRef $BaselineRef -Out "$Out\9-hygiene" | Out-Null; $LASTEXITCODE }
if ($Branch -eq 'maintenance') {
    Stage '10 policy' 'content' '' { & "$V\policy_gate.ps1" -Candidate $cand -Python $Python -Out "$Out\10-policy" -PyYamlPath $PyYamlPath | Out-Null; $LASTEXITCODE }
}
Remove-Item Env:ATHENA_VERIFY_EXTRA_PYTHONPATH -ErrorAction SilentlyContinue
$all = -not @($results | Where-Object { -not $_.ok }).Count
$notRun = @($results | Where-Object { $_.status -eq 'not_run' }).Count
Write-Json ([ordered]@{ ref = $Ref; branch = $Branch; baseline_ref = $BaselineRef; source_roots = $roots; candidate = $cand; stages = $results.ToArray(); stages_not_run = $notRun; ok = $all }) "$Out\summary.json"
"final gate ok=$all (not run: $notRun)"
if (-not $all) { exit 1 }
exit 0
