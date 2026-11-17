# Proves, for a candidate checkout, that Python code is imported from the CANDIDATE and not from the owner's original (editable-installed) checkout, subprocesses included,
# and proves the recorder can detect the opposite (negative control). Nothing is installed, reinstalled or retargeted in the owner's environment.
#
#  A  in-process:       `python -c "import athena"`                                  -> athena.__file__ must be under <Candidate>\src
#  B  subprocess:       a Python process started by a Python process (inherits PYTHONPATH) -> the CHILD must also import from the candidate
#  C  `python -m`:      a package entry point started with -m                         -> imported from the candidate (the supervisor starts tasks this way)
#  N  negative control: the same import WITHOUT the candidate on PYTHONPATH            -> must come from the original checkout and must be RECORDED as a violation
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Candidate = (Resolve-Path -LiteralPath $Candidate).Path
$Out = New-RunDir $Out
$srcRoot = (Join-Path $Candidate 'src')
$log = "$Out\origin.jsonl"
function Under([string]$p, [string]$root) { return ([IO.Path]::GetFullPath($p).ToLower().StartsWith(([IO.Path]::GetFullPath($root).ToLower().TrimEnd('\') + '\'))) }

Enable-OriginGuard $Candidate $log
$a = (Invoke-Stage 'A in-process' $Candidate $Python @('-c', 'import athena, sys; print(athena.__file__)') @{} "$Out\A.log").text.Trim().Split("`n")[-1].Trim()
# the parent is a script file (quotes inside a native command line are not reliable in Windows PowerShell 5.1)
Set-Content "$Out\parent.py" -Encoding ascii @'
import subprocess, sys
r = subprocess.run([sys.executable, "-c", "import athena; print(athena.__file__)"], capture_output=True, text=True)
print(r.stdout.strip())
'@
$b = (Invoke-Stage 'B subprocess' $Candidate $Python @("$Out\parent.py") @{} "$Out\B.log").text.Trim().Split("`n")[-1].Trim()
$cRun = Invoke-Stage 'C -m' $Candidate $Python @('-m', 'athena.reference', '--help') @{} "$Out\C.log"
$cOk = ($cRun.exit -eq 0)
Disable-OriginGuard

# N: only the recorder (not the candidate's src) on PYTHONPATH
Set-Item -Path 'Env:ATHENA_EXPECT_ROOT' -Value $srcRoot; Set-Item -Path 'Env:ATHENA_ORIGIN_LOG' -Value "$Out\negative-control.jsonl"; Set-Item -Path 'Env:PYTHONPATH' -Value (Join-Path $PSScriptRoot 'origin_guard')
$n = (Invoke-Stage 'N negative control' $Candidate $Python @('-c', 'import athena; print(athena.__file__)') @{} "$Out\N.log").text.Trim().Split("`n")[-1].Trim()
Disable-OriginGuard

$report = (& $Python "$PSScriptRoot\origin_report.py" $log --min-athena-processes 3 --out "$Out\origin-report.json") | ConvertFrom-Json
$negReport = (& $Python "$PSScriptRoot\origin_report.py" "$Out\negative-control.jsonl" --min-athena-processes 1) | ConvertFrom-Json
$res = [ordered]@{
    candidate = $Candidate
    A_in_process = $a; A_from_candidate = (Under $a $srcRoot)
    B_child_process = $b; B_from_candidate = (Under $b $srcRoot)
    C_dash_m_exit = $cRun.exit; C_ok = $cOk
    recorder = $report
    N_without_candidate_path = $n; N_comes_from_candidate = (Under $n $srcRoot); N_recorder_flagged_it = ($negReport.violations -gt 0)
}
$res.ok = ($res.A_from_candidate -and $res.B_from_candidate -and $cOk -and $report.ok -and (-not $res.N_comes_from_candidate) -and $res.N_recorder_flagged_it)
Write-Json $res "$Out\summary.json"
"origin probe ok=$($res.ok)  A=$($res.A_from_candidate) B=$($res.B_from_candidate) C=$cOk  recorder: $($report.processes_that_imported_athena) processes, $($report.violations) violations  negative control flagged=$($res.N_recorder_flagged_it)"
if (-not $res.ok) { exit 1 }
exit 0
