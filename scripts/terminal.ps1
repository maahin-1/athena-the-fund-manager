# Build what is out of date and start the Athena terminal on http://127.0.0.1:8700 (Ctrl+C stops it).
#   scripts\terminal.ps1                 real feed (needs the Angel One settings in the settings file)
#   scripts\terminal.ps1 -Fake           deterministic fake prices, no broker
#   scripts\terminal.ps1 -Port 8701
param([switch]$Fake, [int]$Port = 8700)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$web = Join-Path $root 'terminal\web'
$dist = Join-Path $web 'dist\index.html'
$newestSource = Get-ChildItem (Join-Path $web 'src'), (Join-Path $web 'index.html') -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not (Test-Path (Join-Path $web 'node_modules'))) { Push-Location $web; npm install; Pop-Location }
if (-not (Test-Path $dist) -or (Get-Item $dist).LastWriteTime -lt $newestSource.LastWriteTime) {
    Push-Location $web; npm run build; Pop-Location
}

cargo build --release --manifest-path (Join-Path $root 'terminal\Cargo.toml')
$exe = Join-Path $root 'terminal\target\release\athena-terminal.exe'

$arguments = @('--port', $Port, '--web-dir', $web + '\dist')
if ($Fake) { $arguments += '--fake-feed' }
Start-Process "http://127.0.0.1:$Port"
& $exe @arguments
