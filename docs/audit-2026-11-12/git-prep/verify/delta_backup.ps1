# A versioned DELTA backup: the files that are new or different since a verified base backup, with their status. The base backup is never modified (its manifest, zips and hash files are re-hashed
# before and after and recorded). Same rules as the base: allowlist = tracked files + untracked files Git does not ignore + the git-ignored files inside the audit's evidence folders;
# protected (environment-file) names are never copied, read or printed; production runtime data (.athena, logs/, *.duckdb outside the audit's evidence folders) is never copied.
param(
    [Parameter(Mandatory)][string]$Repo,
    [Parameter(Mandatory)][string]$BaseRoot,         # e.g. ...\pre-git-20261117-final
    [Parameter(Mandatory)][string]$DeltaRoot         # must not exist, e.g. ...\pre-git-20261117-delta-1
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
if (Test-Path -LiteralPath $DeltaRoot) { throw "delta folder exists (never reused): $DeltaRoot" }
Set-Item Env:GIT_CONFIG_COUNT '1'; Set-Item Env:GIT_CONFIG_KEY_0 'core.safecrlf'; Set-Item Env:GIT_CONFIG_VALUE_0 'false'
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Sha([string]$p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }
function Category([string]$p) {
    $p = $p.Replace('\', '/')
    if ($p -like 'docs/audit-2026-11-12/baselines/*' -or $p -like 'docs/audit-2026-11-12/agent-probes/*' -or $p -like 'docs/audit-2026-11-12/git-prep/runs/*' -or $p -like 'docs/audit-2026-11-12/maintenance-proposal/runs/*') { return 'evidence' }
    if ($p -eq 'r.txt') { return 'scratch' }
    if ($p -eq '.claude/settings.json') { return 'local-config' }
    if ($p -like 'tests/*' -or $p -like 'terminal/web/test/*' -or $p -like 'terminal/web/e2e/*' -or $p -eq 'terminal/src/bounded_tests.rs') { return 'tests' }
    if ($p.EndsWith('.md') -or $p -like 'docs/*' -or $p -eq 'NOTICE' -or $p -eq 'LICENSE') { return 'documentation' }
    return 'implementation'
}
function Runtime([string]$p) { $q = $p.Replace('\', '/'); return ($q.StartsWith('.athena/') -or $q -like '*/.athena/*' -or $q.StartsWith('logs/') -or $q -like '*.duckdb.wal') }

# the base must be intact before and after
$baseFiles = @('meta\MANIFEST.json', 'meta\MANIFEST.sha256', 'meta\VERIFICATION.json', 'athena-backup.zip', 'athena-backup.zip.sha256')
$before = @{}; foreach ($f in $baseFiles) { $before[$f] = Sha "$BaseRoot\$f" }
$baseRows = Get-Content "$BaseRoot\meta\MANIFEST.json" -Raw | ConvertFrom-Json
$baseIgn = Get-Content "$BaseRoot\meta\MANIFEST-ignored-evidence.json" -Raw | ConvertFrom-Json   # not wrapped in @(): Windows PowerShell 5.1 would keep the array as ONE element
$baseMap = @{}; foreach ($r in $baseRows) { $baseMap[$r.path] = $r.sha256 }
foreach ($r in $baseIgn) { $baseMap['IGN:' + $r.path] = $r.sha256 }

Set-Location $Repo
$tree = "$DeltaRoot\tree"; $ign = "$DeltaRoot\tree-ignored-evidence"; $meta = "$DeltaRoot\meta"
New-Item -ItemType Directory -Force $tree, $ign, $meta | Out-Null
$tracked = @((git ls-files -z) -split "`0" | Where-Object { $_ })
$untracked = @((git ls-files --others --exclude-standard -z) -split "`0" | Where-Object { $_ })
$ignoredEv = @((git ls-files --others --ignored --exclude-standard -z -- docs/audit-2026-11-12) -split "`0" | Where-Object { $_ -and $_.StartsWith('docs/audit-2026-11-12/baselines/') })
$modSet = [System.Collections.Generic.HashSet[string]]::new([string[]]@((git diff --name-only -z HEAD) -split "`0" | Where-Object { $_ }))
$untSet = [System.Collections.Generic.HashSet[string]]::new([string[]]$untracked)
$all = @($tracked + $untracked + @('.claude/settings.json')) | Select-Object -Unique
$rows = New-Object System.Collections.Generic.List[object]; $unchanged = 0; $seen = [System.Collections.Generic.HashSet[string]]::new()
foreach ($p in $all) {
    if ((Test-PolicyName $p) -or (Runtime $p) -or $p -like '*.duckdb') { continue }
    $src = Join-Path $Repo $p.Replace('/', '\')
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
    [void]$seen.Add($p)
    $h = Sha $src
    if ($baseMap.ContainsKey($p) -and $baseMap[$p] -eq $h) { $unchanged++; continue }
    $status = if ($baseMap.ContainsKey($p)) { 'changed' } else { 'new' }
    $dest = Join-Path $tree $p.Replace('/', '\'); New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dest -Force
    $rows.Add([pscustomobject]@{ path = $p; status = $status; git = $(if ($untSet.Contains($p)) { 'untracked' } elseif ($modSet.Contains($p)) { 'modified' } else { 'tracked' }); category = (Category $p); size = (Get-Item -LiteralPath $dest).Length; sha256 = $h; previous_sha256 = $(if ($status -eq 'changed') { $baseMap[$p] } else { $null }) })
}
$irows = New-Object System.Collections.Generic.List[object]; $iun = 0
foreach ($p in $ignoredEv) {
    if (Test-PolicyName $p) { continue }
    $src = Join-Path $Repo $p.Replace('/', '\'); $h = Sha $src; $k = 'IGN:' + $p
    if ($baseMap.ContainsKey($k) -and $baseMap[$k] -eq $h) { $iun++; continue }
    $dest = Join-Path $ign $p.Replace('/', '\'); New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dest -Force
    $irows.Add([pscustomobject]@{ path = $p; status = $(if ($baseMap.ContainsKey($k)) { 'changed' } else { 'new' }); size = (Get-Item -LiteralPath $dest).Length; sha256 = $h })
}
$removed = @($baseRows | Where-Object { -not $seen.Contains($_.path) } | ForEach-Object { $_.path })   # in the base, gone from the working tree now
Write-Json $rows.ToArray() "$meta\MANIFEST.json"
[IO.File]::WriteAllLines("$meta\MANIFEST.sha256", @($rows | Sort-Object path | ForEach-Object { "$($_.sha256) *$($_.path)" }), $utf8)
Write-Json $irows.ToArray() "$meta\MANIFEST-ignored-evidence.json"
[IO.File]::WriteAllLines("$meta\MANIFEST-ignored-evidence.sha256", @($irows | Sort-Object path | ForEach-Object { "$($_.sha256) *$($_.path)" }), $utf8)
git diff HEAD --binary "--output=$meta\tracked-changes.patch" -- .
[IO.File]::WriteAllText("$meta\git-head.txt", ((git rev-parse HEAD) + "`n" + (git branch --show-current) + "`n"), $utf8)

# verification: every copy re-hashed (against the manifest and the source), both zips restored and compared, base untouched
$bad = 0
foreach ($r in $rows) { $c = Join-Path $tree $r.path.Replace('/', '\'); if ((Sha $c) -ne $r.sha256 -or (Sha (Join-Path $Repo $r.path.Replace('/', '\'))) -ne $r.sha256) { $bad++ } }
foreach ($r in $irows) { $c = Join-Path $ign $r.path.Replace('/', '\'); if ((Sha $c) -ne $r.sha256) { $bad++ } }
$zip1 = "$DeltaRoot\athena-backup-delta.zip"; $zip2 = "$DeltaRoot\athena-backup-delta-ignored-evidence.zip"
Compress-Archive -Path "$tree\*" -DestinationPath $zip1 -CompressionLevel Optimal
if ($irows.Count) { Compress-Archive -Path "$ign\*" -DestinationPath $zip2 -CompressionLevel Optimal }
$restore = Join-Path ([IO.Path]::GetTempPath()) ('athena-restore-delta-' + [guid]::NewGuid().ToString('N'))
Expand-Archive -Path $zip1 -DestinationPath $restore
$badR = 0; foreach ($r in $rows) { $c = Join-Path $restore $r.path.Replace('/', '\'); if (-not (Test-Path -LiteralPath $c) -or (Sha $c) -ne $r.sha256) { $badR++ } }
Remove-Item $restore -Recurse -Force
if ($irows.Count) { $restore = Join-Path ([IO.Path]::GetTempPath()) ('athena-restore-delta-i-' + [guid]::NewGuid().ToString('N')); Expand-Archive -Path $zip2 -DestinationPath $restore; foreach ($r in $irows) { $c = Join-Path $restore $r.path.Replace('/', '\'); if (-not (Test-Path -LiteralPath $c) -or (Sha $c) -ne $r.sha256) { $badR++ } }; Remove-Item $restore -Recurse -Force }
$after = @{}; foreach ($f in $baseFiles) { $after[$f] = Sha "$BaseRoot\$f" }
$baseIntact = -not @($baseFiles | Where-Object { $before[$_] -ne $after[$_] }).Count
$zipActual = Sha "$BaseRoot\athena-backup.zip"; $zipRecorded = ((Get-Content "$BaseRoot\athena-backup.zip.sha256" -Raw).Trim() -split '\s+')[0]
$res = [ordered]@{
    base = $BaseRoot; base_zip_sha256 = $zipActual; base_zip_matches_its_recorded_hash = ($zipActual -eq $zipRecorded); base_files_unmodified_during_this_run = $baseIntact
    base_manifest_rows = $baseRows.Count; base_rows_unchanged_in_working_tree = $unchanged; base_rows_no_longer_in_working_tree = $removed.Count
    delta_new = @($rows | Where-Object status -eq 'new').Count; delta_changed = @($rows | Where-Object status -eq 'changed').Count
    ignored_evidence_unchanged = $iun; ignored_evidence_new_or_changed = $irows.Count
    delta_bytes = (($rows | Measure-Object size -Sum).Sum) + (($irows | Measure-Object size -Sum).Sum)
    hash_mismatches = $bad; restore_mismatches = $badR
    delta_zip_sha256 = (Sha $zip1); delta_ignored_zip_sha256 = $(if ($irows.Count) { Sha $zip2 } else { $null })
}
$res.ok = ($bad -eq 0 -and $badR -eq 0 -and $baseIntact -and $res.base_zip_matches_its_recorded_hash)
Write-Json $res "$meta\VERIFICATION.json"
Write-Json @($removed) "$meta\REMOVED-SINCE-BASE.json"
[IO.File]::WriteAllText("$DeltaRoot\README.txt", "Delta backup ($(Split-Path -Leaf $DeltaRoot)) of the Athena working tree: the files that are NEW or CHANGED since the base backup (see meta\VERIFICATION.json for the base). tree\ and tree-ignored-evidence\ hold them at their repository paths; meta\MANIFEST*.json list status, size and SHA-256 (previous_sha256 for changed files).`nRestore = restore the base backup first, then copy this tree over it. The base backup was not modified.`nNot copied, by rule: environment files of any name, production runtime data, build output and dependencies.`n", $utf8)
$res | ConvertTo-Json
if (-not $res.ok) { exit 1 }
exit 0
