# Broad offline Python run for T5, in a uniquely named run directory: collected test IDs, the exact command, the exclusions, toolchain and code state are saved with the result.
param([string]$Label = 't5-python')
$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$Label"
$out = "$base\runs\$id"
New-Item -ItemType Directory -Force $out | Out-Null
Set-Location $repo
$ignores = 'tests/test_dashboard_app.py', 'tests/test_live_panel.py', 'tests/test_market.py', 'tests/test_verdict_panel.py', 'tests/test_llm_basics.py', 'tests/test_llm_probe.py', 'tests/test_llm_router.py', 'tests/test_angel_login.py', 'tests/test_kite_login.py'
$common = @('tests', '-q', '-p', 'no:cacheprovider') + ($ignores | ForEach-Object { "--ignore=$_" })
"python -m pytest $($common -join ' ')" | Set-Content "$out\command.txt"
$ignores | Set-Content "$out\exclusions.txt"
(git rev-parse HEAD) | Set-Content "$out\git-head.txt"
git status --short | Set-Content "$out\git-status.txt"
(git diff --stat | Select-Object -Last 1) | Set-Content "$out\git-diff-stat.txt"
@("python: $(python --version)", "pytest: $(python -m pytest --version 2>&1)") | Set-Content "$out\toolchain-versions.txt"
"{0}  collecting" -f (Get-Date -Format 'HH:mm:ss') | Add-Content "$out\timeline.txt"
python -m pytest @common --collect-only -q 2> "$out\collect.err" | Where-Object { $_ -match '::' } | Sort-Object | Set-Content "$out\test-ids.txt"
"{0}  collected $((Get-Content "$out\test-ids.txt" | Measure-Object -Line).Lines) ids; running" -f (Get-Date -Format 'HH:mm:ss') | Add-Content "$out\timeline.txt"
python -m pytest @common *> "$out\pytest.log"
"{0}  pytest exit $LASTEXITCODE" -f (Get-Date -Format 'HH:mm:ss') | Add-Content "$out\timeline.txt"
Get-Content "$out\pytest.log" | Select-Object -Last 3 | Set-Content "$out\result.txt"
git status --short | Set-Content "$out\git-status.after.txt"
$out
