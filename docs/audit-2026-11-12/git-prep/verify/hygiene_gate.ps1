# Repository hygiene of a candidate checkout (names, modes and Git metadata only; no file content is read): a clean status, `git diff --check` over the branch against its reference, and no runtime-data
# or build-output path tracked. The protected inherited entry is not a runtime path and is judged by build_candidate.ps1 and ci/check_repo_policy.py, not here.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$BaseRef,       # the commit the branch is compared with (the baseline for integration, the integration tip for maintenance)
    [Parameter(Mandatory)][string]$Out            # must not exist
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
$st = @(git -C $Candidate status --porcelain).Count
$chk = (git -C $Candidate diff --check $BaseRef HEAD 2>&1 | Out-String).Trim()
$tracked = @((git -C $Candidate ls-files -z) -join '' -split "`0" | Where-Object { $_ })
$bad = @($tracked | Where-Object { $_ -match '^(\.athena/|logs/|terminal/target/|terminal/web/dist/|terminal/web/node_modules/)|\.duckdb(\.wal)?$' }).Count
$res = [ordered]@{ base_ref = $BaseRef; status_entries = $st; diff_check_output = $chk; tracked_files = $tracked.Count; runtime_or_build_paths_tracked = $bad }
$res.ok = ($st -eq 0 -and -not $chk -and $bad -eq 0)
Write-Json $res "$Out\summary.json"
"hygiene ok=$($res.ok) status_entries=$st diff_check_clean=$(-not $chk) runtime_or_build_paths_tracked=$bad"
if (-not $res.ok) { exit 1 }
exit 0
