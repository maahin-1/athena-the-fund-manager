# Records a genuine RED web run: the pre-fix provenance.ts (the T5 version, reproduced) and chartlab/view.ts (HEAD) are swapped in, the new tests are run, and the current files are ALWAYS restored (try/finally,
# verified by hash). Nothing else is touched.
$repo = 'd:\projects\learn-project\athena-the-fund-manager'
$sp = 'C:\Users\LEGION\AppData\Local\Temp\claude\ed5e936a5cc19168\608bc073-258a-4e5c-b2f1-60f2be42ba55\scratchpad'
$prov = "$repo\terminal\web\src\provenance.ts"; $view = "$repo\terminal\web\src\chartlab\view.ts"
New-Item -ItemType Directory -Force "$sp\green-copies" | Out-Null
Copy-Item $prov "$sp\green-copies\provenance.ts" -Force; Copy-Item $view "$sp\green-copies\view.ts" -Force
$h1 = (Get-FileHash $prov).Hash; $h2 = (Get-FileHash $view).Hash
try {
  Copy-Item "$sp\provenance.old.ts" $prov -Force
  git -C $repo show HEAD:terminal/web/src/chartlab/view.ts | Set-Content $view -Encoding utf8
  & "$repo\docs\audit-2026-11-12\baselines\wp07-t5r-2026-11-15\scripts\run_web.ps1" -Label web-red -Files 'test/provenance.test.ts', 'test/datasource-fixtures.test.ts', 'test/labview.test.ts' | Select-Object -Last 8
} finally {
  Copy-Item "$sp\green-copies\provenance.ts" $prov -Force; Copy-Item "$sp\green-copies\view.ts" $view -Force
  "restored provenance.ts: $((Get-FileHash $prov).Hash -eq $h1); view.ts: $((Get-FileHash $view).Hash -eq $h2)"
}
