$o = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$hits = @(Get-Content "$o\guard-hits.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.kind -eq 'fetch' })
"distinct endpoints (query string removed, symbol segment folded):"
$hits | ForEach-Object { ($_.detail -replace '\?.*$', '') -replace '^(/api/valuation|/api/fund|/api/fund-fetch)/[^/]+$', '$1/<symbol>' } | Group-Object | Sort-Object Count -Descending | ForEach-Object { "{0,4}  {1}" -f $_.Count, $_.Name }
"tests that triggered an unstubbed default fetch ($(@($hits | Group-Object test).Count) distinct test names), first 12:"
$hits | Group-Object test | Sort-Object Count -Descending | Select-Object -First 12 | ForEach-Object { "{0,4}  {1}" -f $_.Count, $_.Name }
