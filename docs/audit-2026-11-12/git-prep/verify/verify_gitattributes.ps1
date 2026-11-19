# Tests the proposed line-ending policy (git-prep\proposed\gitattributes.proposed) in THROWAWAY clones of the baseline commit (built by build_candidate.ps1: sparse, no protected file written).
# Nothing is committed anywhere; the clones are deleted by the caller. Checks:
#   1  the baseline's committed text blobs have no CR (so the rules cannot rewrite them)
#   2  with the policy added, a checkout made under core.autocrlf=true (the owner's Windows setting) and one under false are both CLEAN, and `git add --renormalize --dry-run` names only the new file
#   3  git resolves the attributes as intended for sample paths of every class
#   4  conversion on add behaves as declared for sample files (CRLF and mixed endings -> LF blobs; PowerShell script blob LF; evidence and *.patch bytes untouched)
param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Work,     # parent for the two throwaway clones; must not exist
    [Parameter(Mandatory)][string]$Out
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
$policy = "$PSScriptRoot\..\proposed\gitattributes.proposed"
New-Item -ItemType Directory -Force $Work | Out-Null
$res = [ordered]@{}
$cfgSafe = @('-c', 'core.safecrlf=false')

foreach ($mode in 'true', 'false') {
    $dest = "$Work\clone-autocrlf-$mode"
    $r = & "$PSScriptRoot\build_candidate.ps1" -Source $Source -Ref 'HEAD' -Dest $dest -Out "$Out\build-$mode" -Autocrlf $mode | ConvertFrom-Json
    Copy-Item $policy "$dest\.gitattributes"
    # CRLF count in the committed blobs of text files (index side), via git's own eol report; names with a protected pattern are dropped before counting
    $eol = @((git -C $dest ls-files --eol -z) -join '' -split "`0" | Where-Object { $_ } | ForEach-Object { if ($_ -match '^i/(\S+)\s+w/(\S+)\s+attr/(.*?)\s*\t(.*)$') { [pscustomobject]@{ i = $Matches[1]; path = $Matches[4] } } } | Where-Object { -not (Test-PolicyName $_.path) })
    $blobCR = @($eol | Where-Object { $_.i -in 'crlf', 'mixed' }).Count
    $porcelain = @(git -C $dest @cfgSafe status --porcelain)
    # a real renormalising add into the throwaway clone's INDEX (no commit): which paths would a blob change touch? (the dry-run form lists every path, so it cannot answer this)
    git -C $dest @cfgSafe add --renormalize --all
    $staged = @((git -C $dest @cfgSafe diff --cached --name-only -z) -join '' -split "`0" | Where-Object { $_ } | Where-Object { -not (Test-PolicyName $_) })
    $res["autocrlf_$mode"] = [ordered]@{
        tracked_text_blobs_with_CR = $blobCR; tracked_rows = $eol.Count
        status_entries_after_adding_policy = $porcelain.Count; status_lines = @($porcelain | ForEach-Object { ($_ -replace '^\s*', '') })
        renormalizing_add_changes_paths = $staged.Count; paths_changed_by_renormalizing = @($staged)
        only_the_policy_file_is_new = ($porcelain.Count -eq 1 -and $porcelain[0] -match '\.gitattributes$' -and $staged.Count -eq 0)
    }
}

# 3 + 4 in the autocrlf=true clone (index only; no commit)
$d = "$Work\clone-autocrlf-true"
$samples = [ordered]@{ 'src/athena/sample.py' = 'a'; 'terminal/src/sample.rs' = 'b'; 'scripts/sample.ps1' = 'c'; 'docs/audit-2026-11-12/baselines/x/runs/r/log.txt' = 'd'; 'docs/audit-2026-11-12/baselines/x/runs/r/tool.py' = 'e'; 'docs/audit-2026-11-12/git-prep/sample.patch' = 'f'; 'notes/unlisted.txt' = 'g'; 'scripts/ci/sample.sh' = 'h' }
$body = @{ 'src/athena/sample.py' = "a = 1`r`nb = 2`r`n"; 'terminal/src/sample.rs' = "fn a() {}`r`nfn b() {}`nfn c() {}`r`n"; 'scripts/sample.ps1' = "Write-Host 1`nWrite-Host 2`n"; 'docs/audit-2026-11-12/baselines/x/runs/r/log.txt' = "one`r`ntwo`r`n"; 'docs/audit-2026-11-12/baselines/x/runs/r/tool.py' = "x = 1`r`n"; 'docs/audit-2026-11-12/git-prep/sample.patch' = "+line`r`n context`n"; 'notes/unlisted.txt' = "u`r`nv`r`n"; 'scripts/ci/sample.sh' = "echo a`r`necho b`r`n" }
foreach ($p in $samples.Keys) {
    $f = "$d\$($p.Replace('/', '\'))"; New-Item -ItemType Directory -Force (Split-Path $f) | Out-Null
    [IO.File]::WriteAllBytes($f, [Text.Encoding]::ASCII.GetBytes($body[$p]))
}
git -C $d @cfgSafe add -- ($samples.Keys | ForEach-Object { $_ })
$rows = @{}
foreach ($line in (git -C $d @cfgSafe ls-files --eol -z -- ($samples.Keys | ForEach-Object { $_ })) -join '' -split "`0" | Where-Object { $_ }) {
    if ($line -match '^i/(\S+)\s+w/(\S+)\s+attr/(.*?)\s*\t(.*)$') { $rows[$Matches[4]] = [ordered]@{ index = $Matches[1]; worktree = $Matches[2]; attr = $Matches[3].Trim() } }
}
$expect = [ordered]@{ 'src/athena/sample.py' = 'lf'; 'terminal/src/sample.rs' = 'lf'; 'scripts/sample.ps1' = 'lf'; 'docs/audit-2026-11-12/baselines/x/runs/r/log.txt' = 'crlf'; 'docs/audit-2026-11-12/baselines/x/runs/r/tool.py' = 'crlf'; 'docs/audit-2026-11-12/git-prep/sample.patch' = 'mixed'; 'notes/unlisted.txt' = 'lf'; 'scripts/ci/sample.sh' = 'lf' }
$conv = [ordered]@{}
foreach ($p in $expect.Keys) { $conv[$p] = [ordered]@{ index_ending = $rows[$p].index; expected = $expect[$p]; attr = $rows[$p].attr; as_declared = ($rows[$p].index -eq $expect[$p]) } }
$res.samples_after_add = $conv
$res.note = 'notes/unlisted.txt matches no rule: it follows core.autocrlf (true here), so its blob is LF; with autocrlf=false it would stay CRLF. That is the unchanged behaviour for unnamed paths.'
$res.ok = ($res.autocrlf_true.tracked_text_blobs_with_CR -eq 0 -and $res.autocrlf_true.only_the_policy_file_is_new -and $res.autocrlf_false.only_the_policy_file_is_new -and -not @($conv.Values | Where-Object { -not $_.as_declared }).Count)
Write-Json $res "$Out\summary.json"
$res | ConvertTo-Json -Depth 6
if (-not $res.ok) { exit 1 }
exit 0
