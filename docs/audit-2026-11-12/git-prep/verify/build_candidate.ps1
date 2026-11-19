# Assembles a clean candidate checkout of a commit WITHOUT materializing any protected (environment-file) path, and proves it.
#
# Why a special procedure: the baseline commit already tracks one protected template. An ordinary clone or worktree would create that file on disk, which the access policy forbids
# (no create / copy / read). Here the exclusion is configured BEFORE the first checkout (sparse-checkout, non-cone, with one exclusion line per protected tracked path), so the file
# stays in the index as "skip-worktree" metadata and is never written. Nothing is opened, read, copied or deleted; only names and object ids are compared.
#
# Result: <Out>\candidate-access.json (counts and booleans only; no protected name or content is ever printed or saved).
# The inherited entry is an explicit exception: it must be IDENTICAL (same path and object id) in the candidate and in the baseline. Any new or changed protected entry fails the build.
param(
    [Parameter(Mandatory)][string]$Source,      # the repository holding the commits (never modified)
    [Parameter(Mandatory)][string]$Ref,         # branch or commit to check out
    [Parameter(Mandatory)][string]$Dest,        # must not exist
    [Parameter(Mandatory)][string]$Out,         # run directory, must not exist
    [string]$Baseline = '37f1fc19846a178083d9ced7a7a25aeffe65b1f9',
    [ValidateSet('false', 'true')][string]$Autocrlf = 'false'   # the gate runs with 'false' (the attributes decide); 'true' reproduces the owner's Windows setting
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
if (Test-Path -LiteralPath $Dest) { throw "destination exists: $Dest" }
$Out = New-RunDir $Out
$sha = (git -C $Source rev-parse --verify "$Ref^{commit}").Trim()
$baseSha = (git -C $Source rev-parse --verify "$Baseline^{commit}").Trim()

function Get-Entries([string]$Commit) {
    $raw = git -C $Source ls-tree -r -z $Commit
    return @(($raw -join '') -split "`0" | Where-Object { $_ } | ForEach-Object {
        $tab = $_.IndexOf("`t"); $meta = $_.Substring(0, $tab) -split ' '
        [pscustomobject]@{ mode = $meta[0]; object = $meta[2]; path = $_.Substring($tab + 1) }
    })
}
$candProtected = @(Get-Entries $sha | Where-Object { Test-PolicyName $_.path })
$baseProtected = @(Get-Entries $baseSha | Where-Object { Test-PolicyName $_.path })
$sig = { param($e) (@($e | Sort-Object path | ForEach-Object { "$($_.mode) $($_.object) $($_.path)" }) -join "`n") }
$identical = ((& $sig $candProtected) -eq (& $sig $baseProtected))
if (-not $identical) { throw "protected tracked entries differ from the baseline (new, changed or removed): the candidate is refused. Counts: candidate $($candProtected.Count), baseline $($baseProtected.Count)." }

# sparse-checkout patterns: everything, minus each protected tracked path (names are written to the pattern file only; nothing prints them)
$patterns = New-Object System.Collections.Generic.List[string]
$patterns.Add('/*')
foreach ($e in $candProtected) { $patterns.Add('!/' + ($e.path -replace '([\\\[\]\*\?])', '\$1')) }

git clone --quiet --no-checkout --config core.autocrlf=$Autocrlf --config core.safecrlf=false "$Source" "$Dest"
if ($LASTEXITCODE -ne 0) { throw 'clone failed' }
($patterns -join "`n") | git -C $Dest sparse-checkout set --no-cone --stdin
if ($LASTEXITCODE -ne 0) { throw 'sparse-checkout configuration failed (checkout NOT done)' }
git -C $Dest checkout --quiet --detach $sha
if ($LASTEXITCODE -ne 0) { throw 'checkout failed' }

# proof 1: nothing with a protected name exists in the working directory (names of files only; .git is not entered)
$onDisk = @(Get-ChildItem -LiteralPath $Dest -Force | Where-Object { $_.Name -ne '.git' } | ForEach-Object {
    if ($_.PSIsContainer) { Get-ChildItem -LiteralPath $_.FullName -Recurse -Force -File | ForEach-Object { $_.FullName } } else { $_.FullName }
})
$materialized = @($onDisk | Where-Object { Test-PolicyName $_ }).Count
# proof 2: the index still holds the inherited entries, flagged skip-worktree ("S"), i.e. metadata only
$skipFlagged = @((git -C $Dest ls-files -v -z) -join '' -split "`0" | Where-Object { $_ -and $_.StartsWith('S ') -and (Test-PolicyName $_.Substring(2)) }).Count
$status = @(git -C $Dest status --porcelain).Count

$res = [ordered]@{
    ref = $Ref; candidate_commit = $sha; baseline_commit = $baseSha
    protected_tracked_entries_in_candidate = $candProtected.Count
    protected_tracked_entries_in_baseline = $baseProtected.Count
    inherited_entries_identical_to_baseline = $identical
    new_or_changed_protected_entries = 0
    protected_paths_materialized_on_disk = $materialized
    protected_entries_skip_worktree = $skipFlagged
    files_on_disk = $onDisk.Count
    git_status_entries = $status
    note = 'exception: the baseline already tracks the entries counted above; they are kept unchanged as metadata only and are not materialized. A zero-protected-filename check cannot pass and is not claimed.'
}
$res.ok = ($identical -and $materialized -eq 0 -and $skipFlagged -eq $candProtected.Count -and $status -eq 0)
Write-Json $res "$Out\candidate-access.json"
$res | ConvertTo-Json
if (-not $res.ok) { exit 1 }
exit 0
