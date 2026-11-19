$ErrorActionPreference = 'Continue'
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$out  = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$short = 'C:\Users\LEGION\ath-wp07'; $target = "$short\t"; $work = "$short\w"
$log = "$out\timeline4.txt"
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content $log }
$started = (Get-Item "$out\git-status.before.txt").LastWriteTime.AddMinutes(-1)   # the start of run_baseline3.ps1, whose state-before files this run completes

# ---- clean up after the stopped script: any test process it left behind
$left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith("$target\debug") })
foreach ($p in $left) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
"processes from the build directory left running after the stop: $($left.Count) (stopped)" | Set-Content "$out\cleanup.txt"
"junction present: $(Test-Path "$work\.venv")" | Add-Content "$out\cleanup.txt"
Move-Item "$out\rust-test.B-with-sh.log" "$out\stopped-partial-rust-test.B-with-sh.log" -Force -ErrorAction SilentlyContinue   # a run B that was cut off is kept as a fragment, not as a result

# ---- Rust: the eight sh-dependent tests only, with Git for Windows' sh on PATH
Set-Location "$work\terminal"
$env:CARGO_TARGET_DIR = $target; $env:CARGO_NET_OFFLINE = 'true'
$gitSh = 'C:\Program Files\Git\bin'
$pathBefore = $env:PATH
$env:PATH = "$gitSh;$pathBefore"
$resolved = (Get-Command sh -ErrorAction SilentlyContinue).Source
$null = & sh -c true; $shExit = $LASTEXITCODE
@("PATH as used for the filtered run: Git for Windows 'bin' prepended",
  "sh resolves to: $resolved",
  "sh -c true exit code: $shExit   (the tests' early-return condition is exactly that this spawn FAILS; it did not)",
  "without it (run A) sh on PATH: $([bool](Get-Command sh -ErrorAction SilentlyContinue -CommandType Application | Where-Object { $_.Source -notlike '*\Git\*' }))") | Set-Content "$out\sh-precondition.txt"
$tempNames = 'athena-sentiment-*', 'athena-ipo-*', 'athena-flow-board-*'
$before = foreach ($n in $tempNames) { Get-ChildItem $env:TEMP -Directory -Filter $n -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName } }
$runStart = Get-Date
Stamp 'rust filtered run C: the eight sh-dependent tests with Git sh'
cargo test --offline --locked -- chart_explain::tests sentiment::tests ipo::tests flow_board::tests --list 2> "$out\rust-list.C.err" | Set-Content "$out\rust-list.C-eight.txt"
cargo test --offline --locked -- chart_explain::tests sentiment::tests ipo::tests flow_board::tests *> "$out\rust-test.C-eight-sh.log"
Stamp "rust filtered run C exit $LASTEXITCODE"
# evidence that the shell stand-ins really ran: the files they write (only the shell creates 'calls'), created during this run
$after = foreach ($n in $tempNames) { Get-ChildItem $env:TEMP -Directory -Filter $n -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName } }
$ev = foreach ($d in $after) { $c = Join-Path $d 'calls'; if (Test-Path $c) { $i = Get-Item $c; "{0}  lines={1}  modified={2}  written_during_run={3}" -f $c, (Get-Content $c | Measure-Object -Line).Lines, $i.LastWriteTime.ToString('HH:mm:ss'), ($i.LastWriteTime -ge $runStart) } }
@("temp stand-in logs found (name, lines, mtime, written during run C):") + @($ev) | Set-Content "$out\sh-evidence.txt"
$env:PATH = $pathBefore

# ---- collected IDs for the Rust stage (names only)
if (Test-Path "$out\rust-list.raw.txt") {
  Get-Content "$out\rust-list.raw.txt" | Where-Object { $_ -match ': test$' } | ForEach-Object { $_ -replace ': test$', '' } | Sort-Object | Set-Content "$out\rust-test-ids.txt"
}

# ---- web: existing npm test script, scratch config, guard v2
$cfg = "$out\vitest.review.config.v2.mjs"
@"
export default {
  root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web',
  envDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/no-dotfiles',
  cacheDir: 'C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/vite-cache',
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['C:/Users/LEGION/AppData/Local/Temp/claude/ed5e936a5cc19168/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/baseline/guard.setup.v2.mjs'] },
};
"@ | Set-Content $cfg -Encoding utf8
foreach ($f in 'web-results.json', 'web-test.log', 'guard-hits.jsonl') { if (Test-Path "$out\$f") { Move-Item "$out\$f" "$out\v1-$f" -Force } }   # only if a v1 file is still in place
$env:npm_config_update_notifier = 'false'; $env:npm_config_audit = 'false'; $env:npm_config_fund = 'false'; $env:npm_config_offline = 'true'
Set-Location "$repo\terminal\web"
Stamp 'web: npm test -- --config <scratch v2> --reporter=default --reporter=json'
& 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg --reporter=default --reporter=json --outputFile.json="$out\web-results.json" *> "$out\web-test.log"
Stamp "web: npm test exit $LASTEXITCODE"
$r = Get-Content "$out\web-results.json" -Raw | ConvertFrom-Json
$r.testResults | ForEach-Object { $f = ($_.name -replace '^.*[\\/]terminal[\\/]web[\\/]', '') -replace '\\', '/'; $_.assertionResults | ForEach-Object { "$f :: $($_.fullName)  [$($_.status)]" } } | Sort-Object | Set-Content "$out\web-test-ids.txt"

# ---- state after
Set-Location $repo
git -C $repo status --short | Set-Content "$out\git-status.after.txt"
"git status identical before/after: $((Get-Content "$out\git-status.before.txt" -Raw) -eq (Get-Content "$out\git-status.after.txt" -Raw))" | Set-Content "$out\git-status.compare.txt"
$helper = "$repo\terminal\.athena\terminal\helper.log"
if (Test-Path $helper) { $ha = Get-Item $helper; "helper.log (not opened) after: size=$($ha.Length) mtime=$($ha.LastWriteTime.ToString('s'))" | Set-Content "$out\helper-log.after.txt" }
$touched = Get-ChildItem "$repo\terminal" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $started -and $_.FullName -notmatch '\\(target|node_modules)\\' }
"files under terminal/ (outside target and node_modules) modified since run_baseline3.ps1 began: $(@($touched).Count)" | Set-Content "$out\repo-touched.txt"
$touched | ForEach-Object { $_.FullName } | Add-Content "$out\repo-touched.txt"
cmd /c rmdir "$work\.venv" 2>$null | Out-Null   # remove the junction only
Stamp 'done'
