$o = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$report = @()
function Summarise($label, $file) {
  $lines = Get-Content $file
  $script:report += "=== run $label  ($file)"
  $script:report += @($lines | Where-Object { $_ -match '^test result:' })
  $tests = @($lines | Where-Object { $_ -match '^test .+ \.\.\. ' })
  $script:report += "per-test lines: $($tests.Count); ok=$(@($tests | Where-Object { $_ -match '\.\.\. ok$' }).Count) FAILED=$(@($tests | Where-Object { $_ -match 'FAILED$' }).Count) ignored=$(@($tests | Where-Object { $_ -match 'ignored' }).Count)"
  $script:report += 'the eight sh-dependent tests:'
  $script:report += @($tests | Where-Object { $_ -match '^test (chart_explain|sentiment|ipo|flow_board)::tests::' } | ForEach-Object { "  $_" })
}
Summarise 'A (no sh on PATH)' "$o\rust-test.A-no-sh.log"
Summarise 'B (Git for Windows sh on PATH)' "$o\rust-test.B-with-sh.log"
$ids = @(Get-Content "$o\rust-list.raw.txt" | Where-Object { $_ -match ': test$' })
$report += "=== list (cargo test -- --list): $($ids.Count) tests"
$report += @($ids | Where-Object { $_ -match '^(chart_explain|sentiment|ipo|flow_board)::tests::' } | ForEach-Object { "  $_" })
$ids | ForEach-Object { $_ -replace ': test$', '' } | Sort-Object | Set-Content "$o\rust-test-ids.txt"
$report += '=== problems in B (first 5)'
$report += @(Get-Content "$o\rust-test.B-with-sh.log" | Where-Object { $_ -match 'panicked|FAILED' } | Select-Object -First 5)
$report | Set-Content "$o\rust-results-summary.txt"
$report
