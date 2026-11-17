# Stops run_baseline3.ps1 right after run A (the nominal Rust run) has finished, so the full supplementary run B is replaced by the eight-test filtered run in run_baseline4.ps1.
# Touches only the process tree of that script; every other process is left alone.
$out = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$tl = "$out\timeline3.txt"
while ($true) {
  Start-Sleep -Milliseconds 700
  $t = if (Test-Path $tl) { Get-Content $tl -Raw } else { '' }
  if ($t -match 'rust run A exit') { break }
  if ($t -match 'ABORT|build exit [1-9]') { "watcher: script ended early ($($t.Trim().Split("`n")[-1]))" | Set-Content "$out\watcher.txt"; return }
}
$me = $PID
$roots = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match 'run_baseline3\.ps1' })
foreach ($p in $roots) { & taskkill /T /F /PID $p.ProcessId *> $null }
"watcher: run A finished; stopped $(@($roots).Count) process tree(s) of run_baseline3.ps1 at $(Get-Date -Format 'HH:mm:ss')" | Set-Content "$out\watcher.txt"
