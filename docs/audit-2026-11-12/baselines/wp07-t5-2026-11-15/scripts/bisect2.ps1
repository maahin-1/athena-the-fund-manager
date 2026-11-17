$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$b = $base -replace '\\', '/'
Set-Location 'd:\projects\learn-project\athena-the-fund-manager\terminal\web'
$env:npm_config_offline = 'true'
Set-Content "$base\current-run.txt" '99999999-000002-bisect2'; New-Item -ItemType Directory -Force "$base\runs\99999999-000002-bisect2" | Out-Null
function Run($label, $guardPath) {
  $cfg = "$base\bisect\cfg2.$label.mjs"
  "export default { root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web', envDir: '$b/no-dotfiles', cacheDir: '$b/vite-cache', test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['$guardPath'] } };" | Set-Content $cfg -Encoding utf8
  $r = & 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg test/heatmap.test.ts 2>&1 | Where-Object { $_ -match 'Test Files|Cannot find|Tests ' }
  "{0,-46} {1}" -f $label, ($r -join ' | ')
}
Copy-Item "$base\guard.setup.v3.mjs" "$base\bisect\guard.setup.v3.mjs" -Force
Copy-Item "$base\guard.setup.v3.mjs" "$base\guard.copyname.mjs" -Force
Run 'original location, original name'  "$b/guard.setup.v3.mjs"
Run 'original location, other name'     "$b/guard.copyname.mjs"
Run 'bisect folder, original name'      "$b/bisect/guard.setup.v3.mjs"
Run 'v2 at its original location'       "$b/guard.setup.v2.mjs"
Remove-Item "$base\guard.copyname.mjs" -ErrorAction SilentlyContinue
