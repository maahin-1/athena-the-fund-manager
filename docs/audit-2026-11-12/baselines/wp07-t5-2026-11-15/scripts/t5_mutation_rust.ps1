# Mutation checks of the Rust snapshot reader, applied to the isolated COPY only (the repo source is never touched), each restored afterwards.
$work = 'C:\Users\LEGION\ath-wp07\w\terminal'
$env:CARGO_TARGET_DIR = 'C:\Users\LEGION\ath-wp07\t'; $env:CARGO_NET_OFFLINE = 'true'
Set-Location $work
$snap = "$work\src\snapshot.rs"; $srv = "$work\src\server.rs"
$origSnap = [System.IO.File]::ReadAllText($snap); $origSrv = [System.IO.File]::ReadAllText($srv)
function Mut($label, $file, $orig, $old, $new, $filter) {
  if (-not $orig.Contains($old)) { "$label : PATTERN MISSING"; return }
  [System.IO.File]::WriteAllText($file, $orig.Replace($old, $new))
  try {
    $r = cargo test --offline --locked $filter 2>&1 | Where-Object { $_ -match '^test result:|^error(\[|:)' } | Select-Object -Last 2
    "$label : $($r -join ' | ')"
  } finally { [System.IO.File]::WriteAllText($file, $orig) }
}
Mut 'hash never verified'                      $snap $origSnap 'if verify_hash {' 'if false {' 'snapshot::'
Mut 'no second read after a vanished file'      $snap $origSnap 'for attempt in 0..2 {
        match once(' 'for attempt in 0..1 {
        match once(' 'snapshot::'
Mut 'manifest identity not compared'            $snap $origSnap 'if str_field(&manifest, "generation_id") != Some(gen) || str_field(&manifest, "file") != Some(file) {' 'if false {' 'snapshot::'
Mut 'size not compared with the file'           $snap $origSnap 'if meta.len() != m_size {' 'if false {' 'snapshot::'
Mut 'pointer and manifest need not agree'       $snap $origSnap 'if m_size != size || m_sha1 != sha1 {' 'if false {' 'snapshot::'
Mut 'any generation id accepted'                $snap $origSnap 'if str_field(&pointer, "schema") != Some(POINTER_SCHEMA) || !generation_id_ok(gen) {' 'if str_field(&pointer, "schema") != Some(POINTER_SCHEMA) {' 'snapshot::'
Mut 'every main failure called a lock'          $snap $origSnap '"locked_by_writer"
    } else {
        "main_unreadable"' '"locked_by_writer"
    } else {
        "locked_by_writer"' 'snapshot::'
Mut 'the mixed window is ignored'               $snap $origSnap '.filter(|(at, _)| now.saturating_sub(*at) <= MIXED_WINDOW_SECS)' '.filter(|(_at, _)| true)' 'snapshot::'
Mut 'snapshot opened without validation'        $snap $origSnap 'let v = resolve(main)?;' 'let v = resolve_with(main, false, None).or_else(|_| resolve(main))?; let _unused = 0;' 'snapshot::'
Mut 'a snapshot answer claims the main identity' $snap $origSnap 'generation_id: v.generation_id.clone(),' 'generation_id: "main:0:0".to_string(),' 'snapshot::'
Mut 'creation date used as the data date'       $srv $origSrv 'let day = last_t.and_then(|t| chrono::DateTime::from_timestamp(t, 0)).map(|d| d.date_naive().to_string());' 'let day = o.get("snapshot_created_at").and_then(|v| v.as_str()).map(|s| s[..10].to_string());' 'server::tests::a_daily'
Mut 'bars response drops the provenance'        $srv $origSrv '"note": note, "provenance": provenance})).into_response()
}' '"note": note})).into_response()
}' 'server::tests::a_daily'
[System.IO.File]::WriteAllText($snap, $origSnap); [System.IO.File]::WriteAllText($srv, $origSrv)
"restored: " + (([System.IO.File]::ReadAllText($snap) -eq $origSnap) -and ([System.IO.File]::ReadAllText($srv) -eq $origSrv))
