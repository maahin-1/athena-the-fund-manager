$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$out  = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$short = 'C:\Users\LEGION\ath-wp07'          # short paths: the bundled DuckDB C++ build fails on the long scratch path
$target = "$short\t"; $work = "$short\w"
$log = "$out\timeline3.txt"
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content $log }
$started = Get-Date

# ---- keep the earlier (guard v1) web results, then start clean
foreach ($f in 'web-results.json', 'web-test.log', 'guard-hits.jsonl') { if (Test-Path "$out\$f") { Move-Item "$out\$f" "$out\v1-$f" -Force } }

# ---- state before (metadata only: no protected file is opened)
git -C $repo status --short | Set-Content "$out\git-status.before.txt"
(git -C $repo rev-parse HEAD) | Set-Content "$out\git-head.txt"
$helper = "$repo\terminal\.athena\terminal\helper.log"
if (Test-Path $helper) { $hb = Get-Item $helper; "helper.log (not opened, not modified by this run) before: size=$($hb.Length) mtime=$($hb.LastWriteTime.ToString('s'))" | Set-Content "$out\helper-log.before.txt" }
$vers = @("cargo: $(cargo --version)", "rustc: $(rustc --version)", "node: $(node --version)", "npm: $(& 'C:\Program Files\nodejs\npm.cmd' --version)",
          "vitest: $((Get-Content "$repo\terminal\web\node_modules\vitest\package.json" -Raw | ConvertFrom-Json).version)",
          "jsdom: $((Get-Content "$repo\terminal\web\node_modules\jsdom\package.json" -Raw | ConvertFrom-Json).version)",
          "venv python: $(& "$repo\.venv\Scripts\python.exe" --version)",
          "os: $([System.Environment]::OSVersion.VersionString)")
$vers | Set-Content "$out\toolchain-versions.txt"

# ---- isolated copy of terminal/ : an explicit allowlist (every *.rs under src, Cargo.toml, Cargo.lock) and nothing else
if (Test-Path $work) { cmd /c rmdir "$work\.venv" 2>$null | Out-Null; Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force "$work\terminal\src", "$work\src\athena\indicators_user", $target | Out-Null
$srcRoot = "$repo\terminal\src\"
$allowed = @(Get-ChildItem "$repo\terminal\src" -Recurse -File -Filter *.rs)
foreach ($f in $allowed) { $rel = $f.FullName.Substring($srcRoot.Length); $dest = "$work\terminal\src\$rel"; New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null; Copy-Item $f.FullName $dest }
Copy-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock" "$work\terminal\"
Copy-Item "$repo\src\athena\indicators_user\runner.py", "$repo\src\athena\indicators_user\__init__.py" "$work\src\athena\indicators_user\"
# name audit of what was copied (names only; hidden files included by -Force): every file must be on the allowlist, none may start with a dot
$names = @(Get-ChildItem $work -Recurse -Force -File | ForEach-Object { $_.FullName.Substring($work.Length + 1) })
$bad = @($names | Where-Object { ($_ -notmatch '\.rs$') -and ($_ -notin 'terminal\Cargo.toml', 'terminal\Cargo.lock', 'src\athena\indicators_user\runner.py', 'src\athena\indicators_user\__init__.py') -or (($_ -split '\\') | Where-Object { $_.StartsWith('.') }) })
"copied files: $($names.Count); outside the allowlist or dot-named: $($bad.Count)" | Set-Content "$out\isolated-copy-audit.txt"
$names | Sort-Object | Add-Content "$out\isolated-copy-audit.txt"
if ($bad.Count) { Stamp 'ABORT: the isolated copy contains a name outside the allowlist'; $bad | Add-Content "$out\isolated-copy-audit.txt"; return }
# per-file hashes of the allowlisted sources, repo vs copy (the code-state identification)
$pairs = foreach ($f in ($allowed + (Get-Item "$repo\terminal\Cargo.toml", "$repo\terminal\Cargo.lock"))) {
  $rel = if ($f.FullName.StartsWith($srcRoot)) { 'src\' + $f.FullName.Substring($srcRoot.Length) } else { $f.Name }
  $a = (Get-FileHash $f.FullName).Hash; $b = (Get-FileHash "$work\terminal\$rel").Hash
  "{0}  {1}  {2}" -f $(if ($a -eq $b) { 'same' } else { 'DIFFERENT' }), $a.Substring(0, 16), $rel
}
$pairs | Set-Content "$out\rust-source-manifest.txt"
$h = [System.Security.Cryptography.SHA256]::Create()
"terminal source+lock tree hash (first 16 hex of sha256 over the sorted per-file hashes and names): $([System.BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes((($pairs | ForEach-Object { ($_ -split '\s+')[1] + ($_ -split '\s+')[2] } | Sort-Object) -join '')))).Replace('-', '').Substring(0, 16))" | Set-Content "$out\rust-source-tree-hash.txt"
# the one thing reached outside the copy: the project Python, through a junction named .venv (read use only)
New-Item -ItemType Junction -Path "$work\.venv" -Target "$repo\.venv" | Out-Null

# ---- Python and sh as the Rust tests see them (relative paths, from the isolated working directory)
Set-Location "$work\terminal"
$py = '..\.venv\Scripts\python.exe'; $rn = '..\src\athena\indicators_user\runner.py'
$pv = @("cwd: $((Get-Location).Path)",
        "../.venv/Scripts/python.exe exists: $(Test-Path $py)   resolves to: $([System.IO.Path]::GetFullPath((Join-Path (Get-Location) $py)))",
        "../.venv/bin/python exists: $(Test-Path '..\.venv\bin\python')",
        "../src/athena/indicators_user/runner.py exists: $(Test-Path $rn)",
        "python runs from there: $(& $py -c 'import sys; print(sys.version.split()[0], sys.flags.isolated)')",
        "sh on PATH: $([bool](Get-Command sh -ErrorAction SilentlyContinue))",
        "Git sh at C:\Program Files\Git\bin\sh.exe: $(Test-Path 'C:\Program Files\Git\bin\sh.exe')")
$pv | Set-Content "$out\rust-preconditions.txt"

# ---- Rust: offline, locked, short separate target directory, isolated cwd
$env:CARGO_TARGET_DIR = $target
$env:CARGO_NET_OFFLINE = 'true'
Stamp 'rust: cargo test --offline --locked --no-run'
cargo test --offline --locked --no-run *> "$out\rust-build.log"
$buildExit = $LASTEXITCODE
Stamp "rust: build exit $buildExit"
if ($buildExit -eq 0) {
  cargo test --offline --locked -- --list 2> "$out\rust-list.err" | Set-Content "$out\rust-list.raw.txt"
  Stamp 'rust run A: cargo test --offline --locked   (PATH as is: no sh)'
  cargo test --offline --locked *> "$out\rust-test.A-no-sh.log"
  Stamp "rust run A exit $LASTEXITCODE"
  $pathBefore = $env:PATH
  if (Test-Path 'C:\Program Files\Git\bin\sh.exe') {
    $env:PATH = "C:\Program Files\Git\bin;$pathBefore"
    Stamp 'rust run B: same command with Git for Windows sh on PATH (supplementary)'
    cargo test --offline --locked *> "$out\rust-test.B-with-sh.log"
    Stamp "rust run B exit $LASTEXITCODE"
    $env:PATH = $pathBefore
  }
}

# ---- web: existing npm test script, scratch config (separate cache dir, no variable files), guard v2 as setup file
$cfg = "$out\vitest.review.config.v2.mjs"
@"
export default {
  root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web',
  envDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/no-dotfiles',
  cacheDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/vite-cache',
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/guard.setup.v2.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
$env:npm_config_update_notifier = 'false'; $env:npm_config_audit = 'false'; $env:npm_config_fund = 'false'; $env:npm_config_offline = 'true'
Set-Location "$repo\terminal\web"
Stamp 'web: npm test -- --config <scratch v2> --reporter=default --reporter=json'
& 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg --reporter=default --reporter=json --outputFile.json="$out\web-results.json" *> "$out\web-test.log"
Stamp "web: npm test exit $LASTEXITCODE"

# ---- state after
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
"git status identical before/after: $((Get-Content "$out\git-status.before.txt" -Raw) -eq (Get-Content "$out\git-status.after.txt" -Raw))" | Set-Content "$out\git-status.compare.txt"
if (Test-Path $helper) { $ha = Get-Item $helper; "helper.log (not opened) after: size=$($ha.Length) mtime=$($ha.LastWriteTime.ToString('s'))" | Set-Content "$out\helper-log.after.txt" }
$touched = Get-ChildItem "$repo\terminal" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $started -and $_.FullName -notmatch '\\(target|node_modules)\\' }
"files under terminal/ (outside target and node_modules) modified since this run began: $(@($touched).Count)" | Set-Content "$out\repo-touched.txt"
$touched | ForEach-Object { $_.FullName } | Add-Content "$out\repo-touched.txt"
cmd /c rmdir "$work\.venv" 2>$null | Out-Null   # remove the junction only; the target venv is untouched
Stamp 'done'
