# Maintenance-branch policy checks run inside the candidate: repository policy (no runtime data, build output, oversized file or protected name beyond the one inherited entry) and workflow policy
# (real YAML parser; every action reference equal to its recorded repository/SHA pair; no expression in any script; access-safe checkout). PyYAML must be importable by -Python; when it is not (the owner's
# environment), -PyYamlPath names a folder that holds it (read use only). A missing parser FAILS the gate.
param(
    [Parameter(Mandatory)][string]$Candidate,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out,         # must not exist
    [string]$PyYamlPath = ''
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
$e = @{ PYTHONDONTWRITEBYTECODE = '1' }
if ($PyYamlPath) { $e.PYTHONPATH = $PyYamlPath }
$a = Invoke-Stage 'repo-policy' $Candidate $Python @('scripts/ci/check_repo_policy.py') $e "$Out\repo-policy.log"
$b = Invoke-Stage 'workflow-policy' $Candidate $Python @('scripts/ci/check_workflows.py', '.github/workflows') $e "$Out\workflow-policy.log"
$ok = ($a.exit -eq 0 -and $b.exit -eq 0)
Write-Json ([ordered]@{ repo_policy_exit = $a.exit; workflow_policy_exit = $b.exit; ok = [bool]$ok }) "$Out\summary.json"
"policy ok=$ok repo=$($a.exit) workflows=$($b.exit)"
if (-not $ok) { exit 1 }
exit 0
