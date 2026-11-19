$base = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad\baseline'
$bis = "$base\bisect"; New-Item -ItemType Directory -Force $bis | Out-Null
$src = [System.IO.File]::ReadAllText("$base\guard.setup.v3.mjs")
Set-Location 'd:\projects\learn-project\athena-the-fund-manager\terminal\web'
$env:npm_config_offline = 'true'
Set-Content "$base\current-run.txt" '99999999-000001-bisect'; New-Item -ItemType Directory -Force "$base\runs\99999999-000001-bisect" | Out-Null
function Variant($label, $text) {
  $g = "$bis\guard.$label.mjs"
  [System.IO.File]::WriteAllText($g, $text)
  $gp = $g -replace '\\', '/'
  $cfg = "$bis\cfg.$label.mjs"
  "export default { root: 'd:/projects/learn-project/athena-the-fund-manager/terminal/web', envDir: '$($base -replace '\\','/')/no-dotfiles', cacheDir: '$($base -replace '\\','/')/vite-cache', test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['$gp'] } };" | Set-Content $cfg -Encoding utf8
  $r = & 'C:\Program Files\nodejs\npm.cmd' test -- --config $cfg test/heatmap.test.ts 2>&1 | Where-Object { $_ -match 'Test Files|Cannot find|Tests ' }
  "{0,-34} {1}" -f $label, ($r -join ' | ')
}
Variant 'v3-as-is' $src
Variant 'no-two-path-fns' ($src.Replace("'cpSync', 'linkSync', 'symlinkSync', ", '').Replace("'cp', 'link', 'symlink', ", ''))
Variant 'no-sync-esm-exports' ($src.Replace('syncBuiltinESMExports();', ''))
Variant 'no-window-overrides' ($src.Replace("window.fetch = globalThis.fetch; window.WebSocket = globalThis.WebSocket; window.EventSource = globalThis.EventSource; window.XMLHttpRequest = globalThis.XMLHttpRequest;", ''))
Variant 'no-fs-wrappers' ($src.Replace("for (const m of [...sync, ...cb]) if (typeof fs[m] === 'function') fs[m] = wrap(m, m, fs[m]);", '').Replace("for (const m of cb) if (typeof fs.promises[m] === 'function') fs.promises[m] = wrapPromise(`promises.${m}`, m, fs.promises[m]);", ''))
