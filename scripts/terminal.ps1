# Build what is out of date and start the Athena terminal on http://127.0.0.1:8700 (Ctrl+C stops it).
#   scripts\terminal.ps1                 real feed (needs the Angel One settings in the settings file)
#   scripts\terminal.ps1 -Fake           deterministic fake prices, no broker
#   scripts\terminal.ps1 -Port 8701
# A failed npm or cargo step stops the script with that step's name and exit code; the browser and the terminal are not started, and a build left over from an earlier run is not launched in its place.
param([switch]$Fake, [int]$Port = 8700)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# In Windows PowerShell a native command that fails does not raise an error, whatever $ErrorActionPreference says: it only sets $LASTEXITCODE. Every npm and cargo call goes through
# here so a nonzero exit code stops the script.
function Invoke-Native {
    param([Parameter(Mandatory)][string]$What, [Parameter(Mandatory)][scriptblock]$Command)
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE); the terminal was not started." }
}

$web = Join-Path $root 'terminal\web'
$dist = Join-Path $web 'dist\index.html'
# What the web build reads: the sources and page, and the package, lock and tool configuration files (a changed dependency or compiler setting changes the build too).
$inputs = @(Get-ChildItem (Join-Path $web 'src') -Recurse -File)
foreach ($name in 'index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json') {
    $file = Join-Path $web $name
    if (Test-Path $file) { $inputs += Get-Item $file }
}
$newestInput = $inputs | Sort-Object LastWriteTime -Descending | Select-Object -First 1

$modules = Join-Path $web 'node_modules'
if (-not (Test-Path $modules)) {
    Push-Location $web
    try { Invoke-Native 'npm install' { npm install } } finally { Pop-Location }
}
if (-not (Test-Path $dist) -or ($newestInput -and (Get-Item $dist).LastWriteTime -lt $newestInput.LastWriteTime)) {
    Push-Location $web
    try { Invoke-Native 'npm run build' { npm run build } } finally { Pop-Location }
    if (-not (Test-Path $dist)) { throw "npm run build reported success but $dist was not written; the terminal was not started." }
}

# Always run cargo: it does nothing when the binary is current, and its exit code is what says the binary on disk matches the source.
Invoke-Native 'cargo build' { cargo build --release --manifest-path (Join-Path $root 'terminal\Cargo.toml') }
$exe = Join-Path $root 'terminal\target\release\athena-terminal.exe'
if (-not (Test-Path $exe)) { throw "cargo build reported success but $exe was not written; the terminal was not started." }

$arguments = @('--port', $Port, '--web-dir', $web + '\dist')
if ($Fake) { $arguments += '--fake-feed' }
Start-Process "http://127.0.0.1:$Port"
& $exe @arguments
