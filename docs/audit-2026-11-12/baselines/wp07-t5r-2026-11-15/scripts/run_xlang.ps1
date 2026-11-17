# Cross-language snapshot contract, both directions, in a uniquely named run directory under ..\runs (see tests/test_snapshot_crosslang.py for the sequence):
# Python writes generations -> Rust reads them; Rust writes generations -> Python reads them. Real files in a run-local directory; no store, environment file or network is touched.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path "$here\..\..\..\..\..").Path
$runs = (Resolve-Path "$here\..").Path + '\runs'
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-xlang'
$out = "$runs\$id"
New-Item -ItemType Directory $out, "$out\py-written", "$out\rust-written" | Out-Null
function Stamp($m) { "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m | Add-Content "$out\timeline.txt" }
# 0: a regular full Rust run (fresh isolated copy, forced rebuild), so the binary under test is the current source
$full = & "$here\run_rust.ps1" -Label 'xlang-rust-full' | Select-Object -Last 1
Stamp "regular Rust run: $full"
Get-Content "$full\rust-test.log" | Where-Object { $_ -match '^test result:' } | Add-Content "$out\timeline.txt"
$work = 'C:\Users\LEGION\ath-wp07\w\terminal'
$env:CARGO_TARGET_DIR = 'C:\Users\LEGION\ath-wp07\t'; $env:CARGO_NET_OFFLINE = 'true'
Set-Location $repo
$env:ATHENA_SNAPSHOT_PY_OUT = "$out\py-written"
python -m pytest tests/test_snapshot_crosslang.py -k writes -q -p no:cacheprovider *> "$out\1-python-writes.log"; Stamp "1 python writes: exit $LASTEXITCODE"
Remove-Item Env:\ATHENA_SNAPSHOT_PY_OUT
Set-Location $work
$env:ATHENA_SNAPSHOT_PY_FIXTURE = "$out\py-written"
cargo test --offline --locked -- --ignored a_generation_published_by_the_python_writer *> "$out\2-rust-reads-python.log"; Stamp "2 rust reads python: exit $LASTEXITCODE"
Remove-Item Env:\ATHENA_SNAPSHOT_PY_FIXTURE
$env:ATHENA_SNAPSHOT_RUST_OUT = "$out\rust-written"
cargo test --offline --locked -- --ignored publishes_generations_for_the_python_reader *> "$out\3-rust-writes.log"; Stamp "3 rust writes: exit $LASTEXITCODE"
Set-Location $repo
python -m pytest tests/test_snapshot_crosslang.py -k reads -q -p no:cacheprovider *> "$out\4-python-reads-rust.log"; Stamp "4 python reads rust: exit $LASTEXITCODE"
Remove-Item Env:\ATHENA_SNAPSHOT_RUST_OUT
"files Python published: $((Get-ChildItem "$out\py-written" | ForEach-Object { $_.Name }) -join ', ')" | Add-Content "$out\timeline.txt"
"files Rust published: $((Get-ChildItem "$out\rust-written" | ForEach-Object { $_.Name }) -join ', ')" | Add-Content "$out\timeline.txt"
Get-Content "$out\timeline.txt"
