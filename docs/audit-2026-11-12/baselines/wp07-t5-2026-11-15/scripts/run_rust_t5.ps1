# Rust stage for T5, in a uniquely named run directory (nothing from an earlier run is overwritten, a failed build included).
param([string]$Label = 't5-rust')
$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$Label"
$out = "$base\runs\$id"
New-Item -ItemType Directory -Force $out | Out-Null
$short = 'C:\Users\LEGION\ath-wp07'; $target = "$short\t"; $work = "$short\w"
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content "$out\timeline.txt" }
Stamp "run $id"
git -C $repo status --short | Set-Content "$out\git-status.before.txt"
(git -C $repo rev-parse HEAD) | Set-Content "$out\git-head.txt"
@("cargo: $(cargo --version)", "rustc: $(rustc --version)", "venv python: $(& "$repo\.venv\Scripts\python.exe" --version)") | Set-Content "$out\toolchain-versions.txt"

# isolated allowlist copy (only *.rs, Cargo.toml, Cargo.lock, runner.py, __init__.py), refreshed from the working tree
if (Test-Path "$work\.venv") { cmd /c rmdir "$work\.venv" 2>$null | Out-Null }
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force "$work\terminal\src", "$work\src\athena\indicators_user", $target | Out-Null
$srcRoot = "$repo\terminal\src\"
$allowed = @(Get-ChildItem "$repo\terminal\src" -Recurse -File -Filter *.rs)
foreach ($f in $allowed) { $rel = $f.FullName.Substring($srcRoot.Length); $dest = "$work\terminal\src\$rel"; New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null; Copy-Item $f.FullName $dest }
Copy-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock" "$work\terminal\"
Copy-Item "$repo\src\athena\indicators_user\runner.py", "$repo\src\athena\indicators_user\__init__.py" "$work\src\athena\indicators_user\"
# stamp every copy with the current time: Copy-Item keeps the source's old timestamps, and cargo would then reuse a binary built from other sources (a stale-binary trap that invalidated t5-rust-3)
Get-ChildItem $work -Recurse -File | ForEach-Object { $_.LastWriteTime = Get-Date }
$names = @(Get-ChildItem $work -Recurse -Force -File | ForEach-Object { $_.FullName.Substring($work.Length + 1) })
$bad = @($names | Where-Object { ($_ -notmatch '\.rs$') -and ($_ -notin 'terminal\Cargo.toml', 'terminal\Cargo.lock', 'src\athena\indicators_user\runner.py', 'src\athena\indicators_user\__init__.py') -or (($_ -split '\\') | Where-Object { $_.StartsWith('.') }) })
"copied files: $($names.Count); outside the allowlist or dot-named: $($bad.Count)" | Set-Content "$out\isolated-copy-audit.txt"
$names | Sort-Object | Add-Content "$out\isolated-copy-audit.txt"
if ($bad.Count) { Stamp 'ABORT: a name outside the allowlist'; return }
$pairs = foreach ($f in ($allowed + (Get-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock"))) {
  $rel = if ($f.FullName.StartsWith($srcRoot, [System.StringComparison]::OrdinalIgnoreCase)) { 'src\' + $f.FullName.Substring($srcRoot.Length) } else { $f.Name }
  "{0}  {1}  {2}" -f $(if ((Get-FileHash $f.FullName).Hash -eq (Get-FileHash "$work\terminal\$rel").Hash) { 'same' } else { 'DIFFERENT' }), (Get-FileHash $f.FullName).Hash.Substring(0, 16), $rel
}
$pairs | Set-Content "$out\rust-source-manifest.txt"
$h = [System.Security.Cryptography.SHA256]::Create()
"source+lock tree hash: $([System.BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes((($pairs | ForEach-Object { ($_ -split '\s+')[1] + ($_ -split '\s+')[2] } | Sort-Object) -join '')))).Replace('-', '').Substring(0, 16))" | Set-Content "$out\rust-source-tree-hash.txt"
New-Item -ItemType Junction -Path "$work\.venv" -Target "$repo\.venv" | Out-Null

Set-Location "$work\terminal"
$env:CARGO_TARGET_DIR = $target; $env:CARGO_NET_OFFLINE = 'true'
Stamp 'cargo test --offline --locked --no-run'
cargo test --offline --locked --no-run *> "$out\rust-build.log"
$be = $LASTEXITCODE; Stamp "build exit $be"
if ($be -eq 0) {
  cargo test --offline --locked -- --list 2> "$out\rust-list.err" | Set-Content "$out\rust-list.raw.txt"
  Get-Content "$out\rust-list.raw.txt" | Where-Object { $_ -match ': test$' } | ForEach-Object { $_ -replace ': test$', '' } | Sort-Object | Set-Content "$out\rust-test-ids.txt"
  Stamp 'cargo test --offline --locked (PATH as is: no sh)'
  cargo test --offline --locked *> "$out\rust-test.log"
  Stamp "test exit $LASTEXITCODE"
}
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
cmd /c rmdir "$work\.venv" 2>$null | Out-Null
Stamp 'done'
$out
