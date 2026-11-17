# Rehearses the allowlist manifest comparison WITHOUT creating any commit: the non-evidence files of the integration plan are staged into the INDEX of a throwaway repository (under the owner's core.autocrlf=true,
# with the proposed .gitattributes in force, so Git itself applies its conversion on add), then written out with `git checkout-index` (Git applies the attributes again on checkout, with core.autocrlf=false),
# exactly what a commit followed by a checkout would produce for the same bytes. compare_manifest.py then compares that tree with the verified sources.
# The removal check is switched off explicitly (--no-removal-check): the throwaway repository holds only the non-evidence files, so a baseline comparison would be meaningless; the report records that it was skipped.
# Control: one byte of one file is changed in the written tree and the comparison must fail on exactly that file. No protected file is involved (none is in the plan); nothing is committed anywhere.
param(
    [Parameter(Mandatory)][string]$Repo,       # the working repository (read only: source of the files)
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Work,       # must not exist
    [Parameter(Mandatory)][string]$Out         # must not exist
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
$Work = New-RunDir $Work
$plan = Get-Content "$Repo\docs\audit-2026-11-12\git-prep\commit-plan.json" -Raw | ConvertFrom-Json
$r = "$Work\throwaway"; $cand = "$Work\candidate"
git init -q $r
git -C $r config core.autocrlf true; git -C $r config core.safecrlf false
Copy-Item "$Repo\docs\audit-2026-11-12\git-prep\proposed\gitattributes.proposed" "$r\.gitattributes"
$paths = New-Object System.Collections.Generic.List[string]
foreach ($g in $plan.groups) {
    if ($g.branch -ne 'integration' -or $g.id -like 'E*') { continue }   # code, documents and tooling; evidence is large and byte-exact by rule
    foreach ($p in $g.files) {
        if (Test-PolicyName $p) { continue }
        $srcRel = $p; if ($g.source_paths -and $g.source_paths.PSObject.Properties.Name -contains $p) { $srcRel = $g.source_paths.$p }
        if ($p -eq '.gitattributes') { $paths.Add($p); continue }
        $s = Join-Path $Repo $srcRel.Replace('/', '\'); $d = Join-Path $r $p.Replace('/', '\')
        New-Item -ItemType Directory -Force (Split-Path $d) | Out-Null
        Copy-Item -LiteralPath $s -Destination $d
        $paths.Add($p)
    }
}
git -C $r add -A
git -C $r config core.autocrlf false
New-Item -ItemType Directory -Force $cand | Out-Null
git -C $r checkout-index -a -f "--prefix=$cand\"
# expected manifest for exactly these files
$py = @"
import hashlib, json, sys
plan = json.load(open(sys.argv[1], encoding='utf-8'))
repo = sys.argv[2]
files = []
for g in plan['groups']:
    if g['branch'] != 'integration' or g['id'][0] == 'E':
        continue
    for p in g['files']:
        src = g.get('source_paths', {}).get(p, p)
        e = {'path': p, 'sha256': hashlib.sha256(open(repo + '/' + src, 'rb').read()).hexdigest()}
        if src != p:
            e['source_path'] = src
        files.append(e)
json.dump({'files': files, 'self_exempt': []}, open(sys.argv[3], 'w'))
print(len(files))
"@
Set-Content "$Work\mk.py" $py -Encoding ascii
$n = (& $Python "$Work\mk.py" "$Repo\docs\audit-2026-11-12\git-prep\commit-plan.json" ($Repo -replace '\\', '/') "$Out\expected-rehearsal.json")
$tracked = @(git -C $r ls-files)
Set-Content "$Out\candidate-list.txt" $tracked
$res1 = & $Python "$PSScriptRoot\compare_manifest.py" --expected "$Out\expected-rehearsal.json" --candidate $cand --attrs-from "$r\.gitattributes" --candidate-list "$Out\candidate-list.txt" --source-root $Repo --no-removal-check --out "$Out\compare.json"
$e1 = $LASTEXITCODE
# control: change one byte of one written file
$victim = Get-ChildItem "$cand\src\athena" -Filter *.py -File | Select-Object -First 1
$fs = [IO.File]::Open($victim.FullName, 'Append'); $fs.WriteByte(32); $fs.Close()
$res2 = & $Python "$PSScriptRoot\compare_manifest.py" --expected "$Out\expected-rehearsal.json" --candidate $cand --attrs-from "$r\.gitattributes" --candidate-list "$Out\candidate-list.txt" --source-root $Repo --no-removal-check --out "$Out\compare-control.json"
$e2 = $LASTEXITCODE
$c1 = Get-Content "$Out\compare.json" -Raw | ConvertFrom-Json; $c2 = Get-Content "$Out\compare-control.json" -Raw | ConvertFrom-Json
$sum = [ordered]@{ staged_files = $tracked.Count; expected = $c1.expected_files; accepted = $c1.accepted; accepted_by_class = $c1.accepted_by_class; mismatched = @($c1.mismatched).Count; missing = @($c1.missing).Count; unexpected_tracked = @($c1.unexpected_tracked).Count; removal_check = $c1.removal_check; exit = $e1
                   control_exit = $e2; control_mismatched = @($c2.mismatched); control_names_the_changed_file = (@($c2.mismatched).Count -eq 1 -and @($c2.mismatched)[0] -like '*src/athena/*') }
$sum.ok = ($e1 -eq 0 -and $e2 -eq 1 -and $sum.control_names_the_changed_file -and $c1.accepted -eq $c1.expected_files)
Write-Json $sum "$Out\summary.json"
Remove-Item $Work -Recurse -Force
$sum | ConvertTo-Json -Depth 4
if (-not $sum.ok) { exit 1 }
exit 0
