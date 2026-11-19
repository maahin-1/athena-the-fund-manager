# Runs `python scripts/formula_golden.py --check` inside the candidate with the import-origin guard on, and requires that the check imported `athena` from the candidate (an unobserved run proves nothing).
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out          # must not exist
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
Enable-OriginGuard $Candidate "$Out\origin.jsonl"
try { $r = Invoke-Stage 'golden' $Candidate $Python @('scripts/formula_golden.py', '--check') @{} "$Out\golden.log" } finally { Disable-OriginGuard }
$o = (& $Python "$PSScriptRoot\origin_report.py" "$Out\origin.jsonl" --min-athena-processes 1 --out "$Out\origin-report.json") | ConvertFrom-Json
$ok = ($r.exit -eq 0 -and $o.ok)
Write-Json ([ordered]@{ exit = $r.exit; origin = $o; ok = [bool]$ok }) "$Out\summary.json"
"formula golden ok=$ok exit=$($r.exit) athena processes=$($o.processes_that_imported_athena) violations=$($o.violations)"
if (-not $ok) { exit 1 }
exit 0
