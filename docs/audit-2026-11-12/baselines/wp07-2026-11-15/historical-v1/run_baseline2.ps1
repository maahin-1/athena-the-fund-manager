$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$out  = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$log  = "$out\timeline2.txt"
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content $log }
$started = Get-Date
Remove-Item "$out\guard-hits.jsonl" -ErrorAction SilentlyContinue

# ---- state before
git -C $repo status --short | Set-Content "$out\git-status.before.txt"
(git -C $repo rev-parse HEAD) | Set-Content "$out\git-head.txt"
$vers = @("cargo: $(cargo --version)", "rustc: $(rustc --version)", "node: $(node --version)", "npm: $(& 'C:\Program Files\nodejs\npm.cmd' --version)",
          "vitest: $(Get-Content "$repo\terminal\web\node_modules\vitest\package.json" -Raw | ConvertFrom-Json | ForEach-Object { $_.version })",
          "jsdom: $(Get-Content "$repo\terminal\web\node_modules\jsdom\package.json" -Raw | ConvertFrom-Json | ForEach-Object { $_.version })",
          "venv python: $(& "$repo\.venv\Scripts\python.exe" --version)")
$vers | Set-Content "$out\toolchain-versions.txt"
$helper = "$repo\terminal\.athena\terminal\helper.log"
if (Test-Path $helper) { $hb = Get-Item $helper; "helper.log before: size=$($hb.Length) mtime=$($hb.LastWriteTime.ToString('s')) sha256=$((Get-FileHash $helper).Hash)" | Set-Content "$out\helper-log.before.txt" }
$viteDir = "$repo\terminal\web\node_modules\.vite"
"web node_modules/.vite exists=$(Test-Path $viteDir) mtime=$(if (Test-Path $viteDir) { (Get-Item $viteDir).LastWriteTime.ToString('s') })" | Set-Content "$out\vite-dir.before.txt"

# ---- isolated copy of terminal/ for the Rust tests (own cwd, so relative .athena paths cannot reach any runtime file); the project Python is reached through a junction, the runner is copied
$work = "$out\work"
if (Test-Path $work) { cmd /c rmdir "$work\.venv" 2>$null | Out-Null; Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force "$work\terminal", "$work\src\athena\indicators_user" | Out-Null
Copy-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock" "$work\terminal\"
Copy-Item "$repo\terminal\src" "$work\terminal\src" -Recurse
Copy-Item "$repo\src\athena\indicators_user\runner.py", "$repo\src\athena\indicators_user\__init__.py" "$work\src\athena\indicators_user\"
New-Item -ItemType Junction -Path "$work\.venv" -Target "$repo\.venv" | Out-Null
$pairs = @()
foreach ($f in (Get-ChildItem "$repo\terminal\src" -Recurse -File) + (Get-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock")) {
  $rel = $f.FullName.Substring("$repo\terminal\".Length)
  $a = (Get-FileHash $f.FullName).Hash; $b = (Get-FileHash "$work\terminal\$rel").Hash
  $pairs += "{0}  {1}  {2}" -f $(if ($a -eq $b) { 'same' } else { 'DIFFERENT' }), $a.Substring(0, 16), $rel
}
$pairs | Set-Content "$out\rust-source-manifest.txt"
$treeHash = (($pairs | ForEach-Object { ($_ -split '\s+')[1] + ($_ -split '\s+')[2] } | Sort-Object) -join '') | ForEach-Object { [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($_))).Replace('-', '').Substring(0, 16) }
"terminal source+lock tree hash (16 hex of sha256 over per-file hashes): $treeHash" | Set-Content "$out\rust-source-tree-hash.txt"
"preconditions seen from the work dir: python=$(Test-Path "$work\terminal\..\.venv\Scripts\python.exe") runner=$(Test-Path "$work\terminal\..\src\athena\indicators_user\runner.py")" | Set-Content "$out\rust-preconditions.txt"

# ---- Rust: offline, locked, separate target directory, isolated cwd
$env:CARGO_TARGET_DIR = "$out\review-target"
$env:CARGO_NET_OFFLINE = 'true'
Set-Location "$work\terminal"
Stamp 'rust: cargo test --offline --locked --no-run'
cargo test --offline --locked --no-run *> "$out\rust-build.log"
Stamp "rust: build exit $LASTEXITCODE"
if ($LASTEXITCODE -eq 0) {
  cargo test --offline --locked -- --list 2> "$out\rust-list.err" | Set-Content "$out\rust-list.raw.txt"
  Stamp "rust: list exit $LASTEXITCODE"
  Stamp 'rust: cargo test --offline --locked'
  cargo test --offline --locked *> "$out\rust-test.log"
  Stamp "rust: test exit $LASTEXITCODE"
}

# ---- web: existing npm test script with a scratch config (separate cache dir, no variable files, offline guard as a setup file)
$cfg = "$out\vitest.review.config.mjs"
@"
export default {
  root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web',
  envDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/no-dotfiles',
  cacheDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/vite-cache',
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/guard.setup.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
$env:npm_config_update_notifier = 'false'; $env:npm_config_audit = 'false'; $env:npm_config_fund = 'false'; $env:npm_config_offline = 'true'
Set-Location "$repo\terminal\web"
Stamp 'web: npm test -- --config <scratch> --reporter=default --reporter=json'
& 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg --reporter=default --reporter=json --outputFile.json="$out\web-results.json" *> "$out\web-test.log"
Stamp "web: npm test exit $LASTEXITCODE"

# ---- state after: nothing in the repo, the runtime file or the node_modules cache may have changed
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
"git status identical before/after: $((Get-Content "$out\git-status.before.txt" -Raw) -eq (Get-Content "$out\git-status.after.txt" -Raw))" | Set-Content "$out\git-status.compare.txt"
if (Test-Path $helper) { $ha = Get-Item $helper; "helper.log after: size=$($ha.Length) mtime=$($ha.LastWriteTime.ToString('s')) sha256=$((Get-FileHash $helper).Hash)" | Set-Content "$out\helper-log.after.txt" }
"web node_modules/.vite exists=$(Test-Path $viteDir) mtime=$(if (Test-Path $viteDir) { (Get-Item $viteDir).LastWriteTime.ToString('s') })" | Set-Content "$out\vite-dir.after.txt"
$touched = Get-ChildItem "$repo\terminal" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $started -and $_.FullName -notmatch '\\(target|node_modules)\\' }
"files under terminal/ (outside target and node_modules) modified since the run began: $(@($touched).Count)" | Set-Content "$out\repo-touched.txt"
$touched | ForEach-Object { $_.FullName } | Add-Content "$out\repo-touched.txt"
cmd /c rmdir "$work\.venv" 2>$null | Out-Null
Stamp 'done'
