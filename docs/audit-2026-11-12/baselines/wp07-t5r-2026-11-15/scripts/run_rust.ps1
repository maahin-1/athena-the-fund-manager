# Rust stage for the T5 review corrections, in a uniquely named run directory under ..\runs (nothing from an earlier run is overwritten, a failed build included).
# An isolated allowlist copy (only *.rs, Cargo.toml, Cargo.lock, two Python files the helpers tests need, and the shared UI fixture) is made fresh and stamped with the current time (a stale-binary trap),
# built and tested with `cargo test --offline --locked` into a separate target directory. It is inspection-based and enforces nothing at run time: it is not a sandbox.
# usage: run_rust.ps1 -Label rust-red [-Filter <cargo test filter>] [-Ignored]
param([string]$Label = 'rust', [string]$Filter = '', [switch]$Ignored, [switch]$UpdateFixtures)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path "$here\..\..\..\..\..").Path
$runs = (Resolve-Path "$here\..").Path + '\runs'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + "-$Label"
$out = "$runs\$id"
New-Item -ItemType Directory $out | Out-Null   # fails if the name exists: never reuse a run directory
$short = 'C:\Users\LEGION\ath-wp07'; $target = "$short\t"; $work = "$short\w"
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content "$out\timeline.txt" }
Stamp "run $id"
git -C $repo status --short | Set-Content "$out\git-status.before.txt"
(git -C $repo rev-parse HEAD) | Set-Content "$out\git-head.txt"
@("cargo: $(cargo --version)", "rustc: $(rustc --version)") | Set-Content "$out\toolchain-versions.txt"
if (Test-Path "$work\.venv") { cmd /c rmdir "$work\.venv" 2>$null | Out-Null }
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force "$work\terminal\src", "$work\terminal\web\test\fixtures", "$work\src\athena\indicators_user", $target | Out-Null
$srcRoot = "$repo\terminal\src\"
$allowed = @(Get-ChildItem "$repo\terminal\src" -Recurse -File -Filter *.rs)
foreach ($f in $allowed) { $rel = $f.FullName.Substring($srcRoot.Length); $dest = "$work\terminal\src\$rel"; New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null; Copy-Item $f.FullName $dest }
Copy-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock" "$work\terminal\"
Copy-Item "$repo\src\athena\indicators_user\runner.py", "$repo\src\athena\indicators_user\__init__.py" "$work\src\athena\indicators_user\"
$fixture = 'terminal\web\test\fixtures\data-source.cases.json'
if (Test-Path "$repo\$fixture") { Copy-Item "$repo\$fixture" "$work\$fixture" }
Get-ChildItem $work -Recurse -File | ForEach-Object { $_.LastWriteTime = Get-Date }
$names = @(Get-ChildItem $work -Recurse -Force -File | ForEach-Object { $_.FullName.Substring($work.Length + 1) })
$fixed = 'terminal\Cargo.toml', 'terminal\Cargo.lock', 'src\athena\indicators_user\runner.py', 'src\athena\indicators_user\__init__.py', $fixture
$bad = @($names | Where-Object { ($_ -notmatch '\.rs$') -and ($_ -notin $fixed) -or (($_ -split '\\') | Where-Object { $_.StartsWith('.') }) })
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
if ($UpdateFixtures) { $env:ATHENA_UPDATE_FIXTURES = '1' } else { Remove-Item Env:\ATHENA_UPDATE_FIXTURES -ErrorAction SilentlyContinue }
$cmd = "cargo test --offline --locked" + $(if ($Filter) { " $Filter" } else { '' }) + $(if ($Ignored) { ' -- --ignored' } else { '' })
Set-Content "$out\command.txt" "cwd=$work\terminal CARGO_TARGET_DIR=$target CARGO_NET_OFFLINE=true`n$cmd"
Stamp 'cargo test --offline --locked --no-run'
cargo test --offline --locked --no-run *> "$out\rust-build.log"
$be = $LASTEXITCODE; Stamp "build exit $be"
if ($be -eq 0) {
  cargo test --offline --locked -- --list 2> "$out\rust-list.err" | Set-Content "$out\rust-list.raw.txt"
  Get-Content "$out\rust-list.raw.txt" | Where-Object { $_ -match ': test$' } | ForEach-Object { $_ -replace ': test$', '' } | Sort-Object | Set-Content "$out\rust-test-ids.txt"
  Stamp $cmd
  if ($Ignored) { if ($Filter) { cargo test --offline --locked $Filter -- --ignored *> "$out\rust-test.log" } else { cargo test --offline --locked -- --ignored *> "$out\rust-test.log" } }
  elseif ($Filter) { cargo test --offline --locked $Filter *> "$out\rust-test.log" }
  else { cargo test --offline --locked *> "$out\rust-test.log" }
  $te = $LASTEXITCODE; Stamp "test exit $te"
  if ($UpdateFixtures -and $te -eq 0 -and (Test-Path "$work\$fixture")) {
    # regenerated by the Rust test itself (ATHENA_UPDATE_FIXTURES=1) in the isolated copy; copied to the repository path only on request, and kept beside this run for review
    Copy-Item "$work\$fixture" "$out\data-source.cases.generated.json"
    Copy-Item "$work\$fixture" "$repo\$fixture" -Force
    Stamp "fixture regenerated from the Rust registry and copied to $fixture"
  }
  python "$here\cargo_results.py" "$out\rust-test.log" "$out\rust-results.json" | Set-Content "$out\rust-summary.txt"
  Set-Content "$out\exit-codes.json" ("{`"build`": $be, `"test`": $te}")
} else {
  Set-Content "$out\INVALID.txt" 'the build failed: no test ran (a compile error is not a test failure and not a pass)'
  Set-Content "$out\exit-codes.json" ("{`"build`": $be, `"test`": null}")
}
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
cmd /c rmdir "$work\.venv" 2>$null | Out-Null
Stamp 'done'
$out
