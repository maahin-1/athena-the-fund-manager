$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$out = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$null = New-Item -ItemType Directory -Force -Path "$out\review-target", "$out\vite-cache", "$out\no-dotfiles"
$log = "$out\timeline.txt"
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content $log }

# helper.log is appended to by the helper tests (cwd = terminal): keep a copy, restore afterwards
$helperLog = "$repo\terminal\.athena\terminal\helper.log"
$helperBackup = "$out\helper.log.before"
if (Test-Path $helperLog) { Copy-Item $helperLog $helperBackup -Force; (Get-FileHash $helperLog).Hash | Set-Content "$out\helper.log.before.sha256" }

# ---- Rust: offline, locked, separate target directory
$env:CARGO_TARGET_DIR = "$out\review-target"
$env:CARGO_NET_OFFLINE = 'true'
Set-Location "$repo\terminal"
Stamp 'rust: build tests (cargo test --offline --locked --no-run)'
cargo test --offline --locked --no-run *> "$out\rust-build.log"
Stamp "rust: build exit $LASTEXITCODE"
if ($LASTEXITCODE -eq 0) {
  Stamp 'rust: list tests'
  cargo test --offline --locked -- --list 2> "$out\rust-list.err" | Set-Content "$out\rust-list.raw.txt"
  Stamp "rust: list exit $LASTEXITCODE"
  Stamp 'rust: run tests (cargo test --offline --locked)'
  cargo test --offline --locked *> "$out\rust-test.log"
  Stamp "rust: test exit $LASTEXITCODE"
}

# ---- web: existing npm test script (vitest run) with a scratch config: separate cache dir, and a config directory with no dotfiles so no variable file is loaded
$cfg = "$out\vitest.review.config.mjs"
@"
export default {
  root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web',
  envDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/no-dotfiles',
  cacheDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/vite-cache',
  build: { outDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/dist', target: 'es2022', sourcemap: false },
  test: { environment: 'node', include: ['test/**/*.test.ts'] },
};
"@ | Set-Content $cfg -Encoding utf8
$env:npm_config_update_notifier = 'false'
$env:npm_config_audit = 'false'
$env:npm_config_fund = 'false'
$env:npm_config_offline = 'true'
Set-Location "$repo\terminal\web"
Stamp 'web: npm test (vitest run) with scratch config'
& 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg --reporter=default --reporter=json --outputFile.json="$out\web-results.json" *> "$out\web-test.log"
Stamp "web: npm test exit $LASTEXITCODE"

# ---- restore the helper log to what it was
if (Test-Path $helperBackup) {
  $after = (Get-FileHash $helperLog).Hash
  $before = Get-Content "$out\helper.log.before.sha256"
  Add-Content "$out\helper.log.note.txt" "helper.log hash before=$before after=$after"
  if ($after -ne $before) { Copy-Item $helperBackup $helperLog -Force; Add-Content "$out\helper.log.note.txt" 'restored to the copy taken before the run' }
}
Stamp 'done'
