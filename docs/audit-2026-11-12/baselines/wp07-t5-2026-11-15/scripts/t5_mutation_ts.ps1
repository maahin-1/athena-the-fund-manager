$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$file = "$repo\terminal\web\src\provenance.ts"
$cfg = (Get-ChildItem 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline\runs' -Directory | Where-Object { $_.Name -like '*t5-web-2' }).FullName + '\vitest.config.mjs'
Set-Location "$repo\terminal\web"
$env:npm_config_offline = 'true'
Set-Content 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline\current-run.txt' '99999999-000003-ts-mutation'
New-Item -ItemType Directory -Force 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline\runs\99999999-000003-ts-mutation' | Out-Null
$orig = [System.IO.File]::ReadAllText($file)
function Mut($label, $old, $new) {
  if (-not $orig.Contains($old)) { "$label : PATTERN MISSING"; return }
  [System.IO.File]::WriteAllText($file, $orig.Replace($old, $new))
  try { $r = & 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg test/provenance.test.ts 2>&1 | Where-Object { $_ -match 'Tests ' }; "$label : $($r -join ' ')" }
  finally { [System.IO.File]::WriteAllText($file, $orig) }
}
Mut 'a failure is not mentioned in the note'       "  if (r.kind === 'failure') {
    return ``No warehouse data" "  if (r.kind === 'never') {
    return ``No warehouse data"
Mut 'mixed generations are not flagged'            'if (sources.mixed) return' 'if (false) return'
Mut 'creation date shown as the data date'         'data to ${day(p.data_as_of)}' 'data to ${day(p.snapshot_created_at)}'
Mut 'a fault does not outrank the other states'    'if (failed.length) return' 'if (false) return'
Mut 'any source string is accepted'                "if ((source !== 'main' && source !== 'snapshot' && source !== 'memory') || !id) return { kind: 'none' };" "if (!id) return { kind: 'none' };"
Mut 'a failed newer copy is not mentioned'         "p.snapshot_status?.last_attempt?.outcome === 'failed'" 'false'
Mut 'the real file is announced as a snapshot'     "if (r.kind !== 'ok' || r.value.source !== 'snapshot') return '';" "if (r.kind !== 'ok') return '';"
[System.IO.File]::WriteAllText($file, $orig)
"restored: " + ([System.IO.File]::ReadAllText($file) -eq $orig)
