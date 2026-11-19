# Cross-language snapshot contract, both directions, in a uniquely named run directory (see tests/test_snapshot_crosslang.py for the sequence).
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-t5-xlang'
$out = "$base\runs\$id"
New-Item -ItemType Directory -Force $out, "$out\py-written", "$out\rust-written" | Out-Null
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m | Add-Content "$out\timeline.txt" }
# 0: a regular full Rust run (fresh copy, forced rebuild) so the binary under test is the current source
$full = & "$base\run_rust_t5.ps1" -Label 't5-rust-5' | Select-Object -Last 1
Stamp "regular Rust run: $full"
Get-Content "$full\rust-test.log" | Where-Object { $_ -match '^test result:' } | Add-Content "$out\timeline.txt"
$work = 'C:\Users\LEGION\ath-wp07\w\terminal'
$env:CARGO_TARGET_DIR = 'C:\Users\LEGION\ath-wp07\t'; $env:CARGO_NET_OFFLINE = 'true'
# 1: Python writes
Set-Location $repo
$env:ATHENA_SNAPSHOT_PY_OUT = "$out\py-written"
python -m pytest tests/test_snapshot_crosslang.py -k writes -q -p no:cacheprovider *> "$out\1-python-writes.log"; Stamp "1 python writes: exit $LASTEXITCODE"
Remove-Item Env:\ATHENA_SNAPSHOT_PY_OUT
# 2: Rust reads what Python wrote
Set-Location $work
$env:ATHENA_SNAPSHOT_PY_FIXTURE = "$out\py-written"
cargo test --offline --locked -- --ignored a_generation_published_by_the_python_writer *> "$out\2-rust-reads-python.log"; Stamp "2 rust reads python: exit $LASTEXITCODE"
Remove-Item Env:\ATHENA_SNAPSHOT_PY_FIXTURE
# 3: Rust writes
$env:ATHENA_SNAPSHOT_RUST_OUT = "$out\rust-written"
cargo test --offline --locked -- --ignored publishes_generations_for_the_python_reader *> "$out\3-rust-writes.log"; Stamp "3 rust writes: exit $LASTEXITCODE"
# 4: Python reads what Rust wrote
Set-Location $repo
python -m pytest tests/test_snapshot_crosslang.py -k reads -q -p no:cacheprovider *> "$out\4-python-reads-rust.log"; Stamp "4 python reads rust: exit $LASTEXITCODE"
Remove-Item Env:\ATHENA_SNAPSHOT_RUST_OUT
"files Python published: $((Get-ChildItem "$out\py-written" | ForEach-Object { $_.Name }) -join ', ')" | Add-Content "$out\timeline.txt"
"files Rust published: $((Get-ChildItem "$out\rust-written" | ForEach-Object { $_.Name }) -join ', ')" | Add-Content "$out\timeline.txt"
Get-Content "$out\timeline.txt"
foreach ($f in '1-python-writes.log', '2-rust-reads-python.log', '3-rust-writes.log', '4-python-reads-rust.log') { "--- $f"; Get-Content "$out\$f" | Where-Object { $_ -match 'passed|failed|test result|error' } | Select-Object -Last 3 }
