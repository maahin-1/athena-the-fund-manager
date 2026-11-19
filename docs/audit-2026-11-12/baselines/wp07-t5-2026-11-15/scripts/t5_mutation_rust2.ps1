$work = 'C:\Users\LEGION\ath-wp07\w\terminal'
$env:CARGO_TARGET_DIR = 'C:\Users\LEGION\ath-wp07\t'; $env:CARGO_NET_OFFLINE = 'true'
Set-Location $work
$srv = "$work\src\server.rs"
$orig = [System.IO.File]::ReadAllText($srv)
$old = '"note": note, "provenance": provenance})).into_response()'
if (-not $orig.Contains($old)) { 'PATTERN MISSING'; return }
[System.IO.File]::WriteAllText($srv, $orig.Replace($old, '"note": note})).into_response()'))
try { $r = cargo test --offline --locked server::tests::a_daily 2>&1 | Where-Object { $_ -match '^test result:|^error(\[|:)' } | Select-Object -Last 2; "bars response drops the provenance : $($r -join ' | ')" }
finally { [System.IO.File]::WriteAllText($srv, $orig) }
"restored: " + ([System.IO.File]::ReadAllText($srv) -eq $orig)
