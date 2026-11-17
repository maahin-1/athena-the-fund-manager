# Inert orchestration tests of final_gate.ps1. The stage scripts are replaced by STUBS in a throwaway folder (-VerifyDir): they only record how they were called and exit as told. No candidate is built,
# no test, cargo, node or git command of the project runs, and nothing outside the temporary folder is touched. What is tested is the wiring: arguments passed on, fresh scratch space, stopping
# dependent stages after an invalid candidate, the maintenance branch's different reference.
#
#   powershell -NoProfile -File test_final_gate.ps1 -Python <any python> -Out <new folder>
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Out          # must not exist
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\common.ps1"
$Out = New-RunDir $Out
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('fg-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tmp | Out-Null
$stubs = "$tmp\stubs"; New-Item -ItemType Directory $stubs | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)

# every stub appends one line "<name> <arguments>" to $env:STUB_LOG and fails when its name is in $env:STUB_FAIL (comma separated)
$tail = @'
$name = [IO.Path]::GetFileNameWithoutExtension($MyInvocation.MyCommand.Path)
Add-Content -LiteralPath $env:STUB_LOG -Value ("$name " + ($rest -join ' '))
if (($env:STUB_FAIL -split ',') -contains $name) { exit 1 }
'@
foreach ($n in 'origin_probe', 'python_gate', 'formula_golden_gate', 'rust_gate', 'crosslang', 'web_gate', 'hygiene_gate', 'policy_gate') {
    [IO.File]::WriteAllText("$stubs\$n.ps1", "`$rest = `$args`n$tail`nexit 0`n", $utf8)
}
# the candidate builder also creates its destination (-Dest) so later stages have something to point at
[IO.File]::WriteAllText("$stubs\build_candidate.ps1", "`$rest = `$args`n$tail`nif (`$env:STUB_NO_CANDIDATE -ne '1') { `$d = `$rest[(`$rest.IndexOf('-Dest')) + 1]; New-Item -ItemType Directory -Force `$d | Out-Null }`nexit 0`n", $utf8)
[IO.File]::WriteAllText("$stubs\compare_manifest.py", "import os, sys`nopen(os.environ['STUB_LOG'], 'a').write('compare_manifest ' + ' '.join(sys.argv[1:]) + '\n')`nsys.exit(1 if 'compare_manifest' in os.environ.get('STUB_FAIL', '').split(',') else 0)`n", $utf8)
# the stage scripts that run real code are not stubbed away from the repository: common.ps1 is dot-sourced from the real verify folder by final_gate itself.

$tests = New-Object System.Collections.Generic.List[object]
$src = "$tmp\source"; New-Item -ItemType Directory $src | Out-Null
$rootA = "$tmp\later-backup-tree"; $rootB = "$tmp\earlier-backup-tree"; New-Item -ItemType Directory $rootA, $rootB | Out-Null
$exp = "$tmp\expected.json"; Set-Content $exp '{"files": []}'
$tip = 'a' * 40
$orig = '37f1fc19846a178083d9ced7a7a25aeffe65b1f9'
$n = 0

function Run-Gate([string]$Label, [hashtable]$Extra, [string]$Fail = '', [string]$NoCandidate = '0', [switch]$PreCreateWork, [switch]$PreCreateOut) {
    $ErrorActionPreference = 'Continue'   # a child's stderr is data here, not a failure of this script
    $script:n++
    $work = "$tmp\w$($script:n)"; $o = "$tmp\o$($script:n)"; $log = "$tmp\log$($script:n).txt"
    if ($PreCreateWork) { New-Item -ItemType Directory $work | Out-Null }
    if ($PreCreateOut) { New-Item -ItemType Directory $o | Out-Null }
    $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$PSScriptRoot\final_gate.ps1", '-Source', $src, '-Ref', 'HEAD', '-Python', $Python, '-NodeModules', "$tmp\nm", '-Work', $work, '-Out', $o,
        '-Expected', $exp, '-SourceRoots', "$rootA;$rootB", '-VerifyDir', $stubs)
    foreach ($k in $Extra.Keys) { $argv += @("-$k", [string]$Extra[$k]) }
    $env:STUB_LOG = $log; $env:STUB_FAIL = $Fail; $env:STUB_NO_CANDIDATE = $NoCandidate
    New-Item -ItemType File -Force $log | Out-Null
    $gateText = & powershell.exe @argv 2>&1 | Out-String
    $code = $LASTEXITCODE
    Remove-Item Env:STUB_LOG, Env:STUB_FAIL, Env:STUB_NO_CANDIDATE -ErrorAction SilentlyContinue
    $calls = @(Get-Content $log | Where-Object { $_.Trim() })
    $summary = if (Test-Path "$o\summary.json") { Get-Content "$o\summary.json" -Raw | ConvertFrom-Json } else { $null }
    [IO.File]::WriteAllText("$Out\run-$($script:n).txt", "# $Label
# exit=$code
# calls:
$($calls -join "`n")
# output:
$gateText", $utf8)
    return [pscustomobject]@{ label = $Label; exit = $code; calls = $calls; summary = $summary; text = $gateText; work = $work; out = $o }
}
function Check([string]$Name, [bool]$Cond, [string]$Detail = '') { $tests.Add([ordered]@{ test = $Name; ok = $Cond; detail = $Detail }); "{0} {1}" -f $(if ($Cond) { 'PASS' } else { 'FAIL' }), $Name }
function Called($r, [string]$stage) { @($r.calls | Where-Object { $_ -like "$stage *" }).Count -gt 0 }
function Status($r, [string]$prefix) { ($r.summary.stages | Where-Object { $_.stage -like "$prefix*" }).status }

# 1. everything passes: arguments are passed on (this is the wiring defect: the comparison had no --source-root)
$r = Run-Gate 'all stubs pass' @{}
Check 'all nine integration stages run and the gate passes' ($r.exit -eq 0 -and $r.summary.ok -and @($r.summary.stages).Count -eq 9 -and -not (Called $r 'policy_gate')) "exit=$($r.exit)"
$cmp = $r.calls | Where-Object { $_ -like 'compare_manifest *' } | Select-Object -First 1
Check 'the comparison gets one --source-root per backup tree, later backup first' ($cmp -match [regex]::Escape("--source-root $rootA") -and $cmp -match [regex]::Escape("--source-root $rootB") -and $cmp.IndexOf($rootA) -lt $cmp.IndexOf($rootB)) $cmp
Check 'the comparison is told the baseline repository and reference (removals are checked)' ($cmp -match '--baseline-repo' -and $cmp -match "--baseline-ref $orig") $cmp
Check 'the comparison is never run with the removal check disabled' ($cmp -notmatch 'no-removal-check') $cmp
$bc = $r.calls | Where-Object { $_ -like 'build_candidate *' } | Select-Object -First 1
Check 'the candidate is built inside the fresh scratch folder' ($bc -match [regex]::Escape("$($r.work)\candidate")) $bc

# 2. fresh scratch space only
$r = Run-Gate 'existing work dir' @{} -PreCreateWork
Check 'an existing -Work folder is refused and no stage runs' ($r.exit -ne 0 -and $r.calls.Count -eq 0) "exit=$($r.exit) calls=$($r.calls.Count)"
$r = Run-Gate 'existing out dir' @{} -PreCreateOut
Check 'an existing -Out folder is refused and no stage runs' ($r.exit -ne 0 -and $r.calls.Count -eq 0) "exit=$($r.exit) calls=$($r.calls.Count)"
$n++; $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$PSScriptRoot\final_gate.ps1", '-Source', $src, '-Ref', 'HEAD', '-Python', $Python, '-NodeModules', "$tmp\nm", '-Work', "$tmp\w$n", '-Out', "$tmp\o$n", '-Expected', $exp, '-SourceRoots', ' ; ', '-VerifyDir', $stubs)
$ErrorActionPreference = 'Continue'; $null = & powershell.exe @argv 2>&1; $c = $LASTEXITCODE; $ErrorActionPreference = 'Stop'
Check 'an empty source-root list is refused' ($c -ne 0 -and -not (Test-Path "$tmp\w$n"))

# 2b. the default stage-script folder is the script's own (a default written as $PSScriptRoot inside the param block is empty in Windows PowerShell 5.1: that defect was found by the first real use)
$n++; $w = "$tmp\w$n"; $o = "$tmp\o$n"
$argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$PSScriptRoot\final_gate.ps1", '-Source', $src, '-Ref', 'HEAD', '-Python', $Python, '-NodeModules', "$tmp\nm", '-Work', $w, '-Out', $o, '-Expected', $exp, '-SourceRoots', "$rootA;$rootB")
$ErrorActionPreference = 'Continue'; $null = & powershell.exe @argv 2>&1; $c = $LASTEXITCODE; $ErrorActionPreference = 'Stop'
$errs = if (Test-Path "$o\errors.txt") { Get-Content "$o\errors.txt" -Raw } else { '' }
$s = if (Test-Path "$o\summary.json") { Get-Content "$o\summary.json" -Raw | ConvertFrom-Json } else { $null }
Check 'without -VerifyDir the real stage scripts are found (the real builder runs and refuses the empty source; nothing is "not recognized")' ($c -ne 0 -and $s -and $s.stages[0].status -eq 'failed' -and $errs -notmatch 'not recognized|cannot find|Cannot find path') $errs

# 3. an invalid candidate stops everything that depends on it
$r = Run-Gate 'candidate build fails' @{} -Fail 'build_candidate'
$st = @($r.summary.stages)
Check 'stage 1 failing: stages 2-9 are not run, nothing but the builder was called' ($r.exit -ne 0 -and -not $r.summary.ok -and $r.calls.Count -eq 1 -and (@($st | Where-Object { $_.status -eq 'not_run' }).Count -eq 8)) ($r.calls -join ' | ')
Check 'stage 1 failing: hygiene is not run either (there is no candidate)' ((Status $r '9') -eq 'not_run')
$r = Run-Gate 'candidate not created' @{} -NoCandidate '1'
Check 'a builder that reports success but creates no candidate is a failed stage 1 and stops the rest' ($r.exit -ne 0 -and (Status $r '1') -eq 'failed' -and (Status $r '2') -eq 'not_run' -and $r.calls.Count -eq 1) "stage1=$(Status $r '1')"
$r = Run-Gate 'manifest comparison fails' @{} -Fail 'compare_manifest'
Check 'stage 2 failing: stages 3-8 are not run, hygiene still runs' ($r.exit -ne 0 -and (Status $r '2') -eq 'failed' -and -not (Called $r 'origin_probe') -and -not (Called $r 'python_gate') -and -not (Called $r 'rust_gate') -and -not (Called $r 'web_gate') -and (Called $r 'hygiene_gate')) ($r.calls -join ' | ')

# 4. independent stages all run after an ordinary failure
$r = Run-Gate 'python gate fails' @{} -Fail 'python_gate'
Check 'stage 4 failing does not stop the independent stages, and the gate still fails' ($r.exit -ne 0 -and (Status $r '4') -eq 'failed' -and (Called $r 'rust_gate') -and (Called $r 'web_gate') -and (Called $r 'crosslang') -and (Called $r 'hygiene_gate')) ($r.calls -join ' | ')

# 5. the maintenance branch is compared with the integration tip and gets its extra stage and count
$r = Run-Gate 'maintenance' @{ Branch = 'maintenance'; BaselineRef = $tip }
$cmp = $r.calls | Where-Object { $_ -like 'compare_manifest *' } | Select-Object -First 1
$py = $r.calls | Where-Object { $_ -like 'python_gate *' } | Select-Object -First 1
$hy = $r.calls | Where-Object { $_ -like 'hygiene_gate *' } | Select-Object -First 1
$bcm = $r.calls | Where-Object { $_ -like 'build_candidate *' } | Select-Object -First 1
Check 'maintenance: ten stages including policy, gate passes' ($r.exit -eq 0 -and @($r.summary.stages).Count -eq 10 -and (Called $r 'policy_gate')) "stages=$(@($r.summary.stages).Count)"
Check 'maintenance: the comparison reference is the integration tip, not the original baseline' ($cmp -match "--baseline-ref $tip" -and $cmp -notmatch "--baseline-ref $orig") $cmp
Check 'maintenance: hygiene compares with the integration tip' ($hy -match [regex]::Escape($tip)) $hy
Check 'maintenance: protected entries are still compared with the ORIGINAL baseline' ($bcm -match "-Baseline $orig") $bcm
Check 'maintenance: the Python count includes the 105 policy tests (3198)' ($py -match '-ExpectedPassed 3198') $py
$r = Run-Gate 'integration counts' @{}
$py = $r.calls | Where-Object { $_ -like 'python_gate *' } | Select-Object -First 1
Check 'integration: the Python count passed to the gate is 3093' ($py -match '-ExpectedPassed 3093') $py

$ok = -not @($tests | Where-Object { -not $_.ok }).Count
Write-Json ([ordered]@{ tests = $tests.ToArray(); passed = @($tests | Where-Object { $_.ok }).Count; failed = @($tests | Where-Object { -not $_.ok }).Count; ok = [bool]$ok }) "$Out\summary.json"
Remove-Item $tmp -Recurse -Force
"orchestration tests: $(@($tests | Where-Object { $_.ok }).Count) passed, $(@($tests | Where-Object { -not $_.ok }).Count) failed"
if (-not $ok) { exit 1 }
exit 0
