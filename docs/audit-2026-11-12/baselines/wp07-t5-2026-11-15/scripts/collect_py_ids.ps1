$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$p = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline\runs\20261115-171736-t5-python'
Set-Location $repo
$ignores = Get-Content "$p\exclusions.txt"
$args2 = @('tests', '--collect-only', '-q', '-p', 'no:cacheprovider') + ($ignores | ForEach-Object { "--ignore=$_" })
"collect command: python -m pytest $($args2 -join ' ')" | Set-Content "$p\collect-ids-command.txt"
$ids = @(python -m pytest @args2 2> "$p\collect2.err" | Where-Object { $_ -match '::' })
$before = $ids.Count
$ids = @($ids | Where-Object { $_ -notlike 'tests/test_snapshot_crosslang.py::*' }) | Sort-Object
$ids | Set-Content "$p\test-ids.txt"
"collected $before ids now; $($before - $ids.Count) from test_snapshot_crosslang.py removed (the file was added after this run collected); $($ids.Count) ids saved. The run's own collect step wrote 0 ids because the shared -q collapsed the per-test lines (a defect of the script, kept as found)." | Set-Content "$p\test-ids-note.txt"
Get-Content "$p\test-ids-note.txt"
"passed+skipped from the run: 3074 + 74 = 3148"
