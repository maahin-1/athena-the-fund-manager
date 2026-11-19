$o = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$r = @()
# ---- the early-return condition of the eight tests: `sh -c true` fails to START. Check it exactly as the tests would, for both PATHs.
$gitBin = 'C:\Program Files\Git\bin'
$pathNow = $env:PATH
$noSh = [bool](Get-Command sh -ErrorAction SilentlyContinue)
$r += "run A PATH: sh resolvable = $noSh   (so Command::new(`"sh`").status() fails to spawn: every one of the eight returned early and Rust reported it ok)"
$env:PATH = "$gitBin;$pathNow"
$shPath = (Get-Command sh -ErrorAction SilentlyContinue).Source
$null = & sh -c true; $code = $LASTEXITCODE
$r += "run B PATH (Git for Windows bin prepended): sh resolves to $shPath ; sh -c true exit code = $code   (spawn succeeds: the early-return branch is NOT taken)"
$env:PATH = $pathNow
# ---- artifacts only the shell stand-ins can write ('calls' files), by the time window of runs A and B
$aStart = [datetime]'2026-11-15 16:42:20'; $aEnd = [datetime]'2026-11-15 16:42:26'; $bEnd = [datetime]'2026-11-15 16:42:34'
$r += 'stand-in logs written by the shell (only sh creates a `calls` file):'
foreach ($pat in 'athena-sentiment-*', 'athena-ipo-*', 'athena-flow-board-*') {
  foreach ($d in Get-ChildItem $env:TEMP -Directory -Filter $pat) {
    $c = Join-Path $d.FullName 'calls'
    if (Test-Path $c) { $i = Get-Item $c; $w = if ($i.LastWriteTime -ge $aStart -and $i.LastWriteTime -le $aEnd) { 'during run A' } elseif ($i.LastWriteTime -gt $aEnd -and $i.LastWriteTime -le $bEnd.AddSeconds(2)) { 'during run B' } else { 'other time' }
      $r += ("  {0}  lines={1}  modified={2}  -> {3}" -f $d.Name, (Get-Content $c | Measure-Object -Line).Lines, $i.LastWriteTime.ToString('HH:mm:ss'), $w) }
  }
}
# ---- web
$w = Get-Content "$o\web-results.json" -Raw | ConvertFrom-Json
$r += "web: files=$($w.numTotalTestSuites) tests=$($w.numTotalTests) passed=$($w.numPassedTests) failed=$($w.numFailedTests) pending/skipped=$($w.numPendingTests) todo=$($w.numTodoTests)"
$w.testResults | ForEach-Object { $f = ($_.name -replace '^.*[\\/]terminal[\\/]web[\\/]', '') -replace '\\', '/'; $_.assertionResults | ForEach-Object { "$f :: $($_.fullName)  [$($_.status)]" } } | Sort-Object | Set-Content "$o\web-test-ids.txt"
$r += "web ids written: $((Get-Content "$o\web-test-ids.txt" | Measure-Object -Line).Lines) lines"
$hits = @(Get-Content "$o\guard-hits.jsonl" | ForEach-Object { $_ | ConvertFrom-Json })
$r += "guard v2 records: " + (($hits | Group-Object kind, denied | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join '; ')
$f = @($hits | Where-Object { $_.kind -eq 'fetch' })
$r += "denied fetch attempts: $($f.Count); absolute http(s) targets: $(@($f | Where-Object { $_.detail -match '^[a-z]+://' }).Count); distinct relative targets: $((($f | ForEach-Object { $_.detail -replace '\?.*$', '' }) | Sort-Object -Unique).Count)"
$r += "denied non-fetch (net/tls/http/dns/dgram/child_process/fs): $(@($hits | Where-Object { $_.denied -and $_.kind -ne 'fetch' }).Count); fs reads outside the allowed roots (recorded, not denied): $(@($hits | Where-Object { $_.kind -like 'fs.*.outside' }).Count)"
($f | ForEach-Object { $_.detail -replace '\?.*$', '' -replace '/(?!api)[A-Z0-9&%^-]{2,14}(?=/|$)', '/<sym>' }) | Group-Object | Sort-Object Count -Descending | ForEach-Object { $r += ("    {0,4}  {1}" -f $_.Count, $_.Name) }
# ---- side effects
$r += Get-Content "$o\git-status.compare.txt"
$r += Get-Content "$o\helper-log.before.txt"
$r += Get-Content "$o\helper-log.after.txt"
$r += Get-Content "$o\repo-touched.txt"
$r += (Get-Content "$o\rust-source-tree-hash.txt")
$r += "HEAD: $(Get-Content "$o\git-head.txt")"
$r += "work-dir junction removed after run: $(-not (Test-Path 'C:\Users\LEGION\ath-wp07\w\.venv'))"
$r | Set-Content "$o\post-summary.txt"
$r
