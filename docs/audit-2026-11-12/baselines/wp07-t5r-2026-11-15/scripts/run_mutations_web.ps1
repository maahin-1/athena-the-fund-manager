# Web mutation checks: fills the spec template with the vitest config of a finished web run (-ConfigRun <run dir name>), points the guard v3 tripwire's hits at a scratch directory of its own, runs mutate.py,
# and copies the guard's hits beside the mutation results. Source files are restored by mutate.py (hash-verified). usage: run_mutations_web.ps1 -ConfigRun 20261115-175308-web-green-2
param([Parameter(Mandatory = $true)][string]$ConfigRun)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runs = (Resolve-Path "$here\..").Path + '\runs'
$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$cfg = ("$runs\$ConfigRun\vitest.config.mjs") -replace '\\', '/'
if (-not (Test-Path "$runs\$ConfigRun\vitest.config.mjs")) { throw "no vitest config in $ConfigRun" }
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-guard-for-mutation-web'
New-Item -ItemType Directory -Force "$base\runs\$id" | Out-Null
Set-Content "$base\current-run.txt" $id
$spec = "$base\mutations-web.json"
$utf8 = New-Object System.Text.UTF8Encoding $false   # explicit UTF-8: Windows PowerShell 5.1's defaults would mangle the '●' of the badge text in the patterns
[System.IO.File]::WriteAllText($spec, [System.IO.File]::ReadAllText("$here\mutations-web.template.json", $utf8).Replace('@@CONFIG@@', $cfg), $utf8)
python "$here\mutate.py" $spec mutation-web
$code = $LASTEXITCODE
$newest = Get-ChildItem $runs -Directory | Where-Object { $_.Name -like '*mutation-web' } | Sort-Object Name | Select-Object -Last 1
if (Test-Path "$base\runs\$id\guard-hits.jsonl") { Copy-Item "$base\runs\$id\guard-hits.jsonl" "$($newest.FullName)\guard-hits.jsonl" }
Copy-Item $spec "$($newest.FullName)\spec.resolved.json"
"mutate.py exit $code"
