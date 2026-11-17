# Names-only check of the isolated copy's file list against every protected pattern. No file is opened except this list of names.
$out = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$names = Get-Content "$out\isolated-copy-audit.txt" | Select-Object -Skip 1
$checks = [ordered]@{
  'basename .env'                    = '(^|\\)\.env$'
  'basename .env.*'                  = '(^|\\)\.env\..+$'
  'basename *.env (non-dot included)' = '\.env$'
  'basename .envrc'                  = '(^|\\)\.envrc$'
  'any dot-directory segment'        = '(^|\\)\.[^\\]+\\'
  'any dot-named file'               = '(^|\\)\.[^\\]+$'
  'not .rs/Cargo/runner (allowlist)' = '^(?!.*\.rs$)(?!terminal\\Cargo\.(toml|lock)$)(?!src\\athena\\indicators_user\\(runner|__init__)\.py$)'
}
$report = @("names checked: $(@($names).Count)")
foreach ($k in $checks.Keys) { $hits = @($names | Where-Object { $_ -match $checks[$k] }); $report += ("{0,-38} matches: {1}" -f $k, $hits.Count); $hits | ForEach-Object { $report += "    $_" } }
# self-check of the patterns on synthetic names, so a pattern that matches nothing cannot pass silently
$synthetic = 'a\.env', 'a\.env.local', 'a\prod.env', 'a\.envrc', 'a\.athena\x', 'a\x.txt'
$report += "pattern self-check on synthetic names: " + (($checks.Keys | ForEach-Object { $p = $checks[$_]; "$_ -> $(@($synthetic | Where-Object { $_ -match $p }).Count)" }) -join '; ')
$report | Set-Content "$out\isolated-copy-name-check.txt"
$report
