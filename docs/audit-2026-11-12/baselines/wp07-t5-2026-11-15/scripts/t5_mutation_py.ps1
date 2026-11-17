$root = 'd:\projects\learn-project\athena-the-fund-manager'
Set-Location $root
$file = "$root\src\athena\warehouse\generations.py"
$tests = 'tests/test_snapshot_generations.py'
$orig = [System.IO.File]::ReadAllText($file)
function Mut($label, $old, $new) {
  if (-not $orig.Contains($old)) { "$label : PATTERN MISSING"; return }
  [System.IO.File]::WriteAllText($file, $orig.Replace($old, $new))
  try { $o = python -m pytest $tests -q -p no:cacheprovider 2>&1 | Where-Object { $_ -match 'passed|failed' } | Select-Object -Last 1; "$label : $o" }
  finally { [System.IO.File]::WriteAllText($file, $orig) }
}
"--- stress test x5"
1..5 | ForEach-Object { python -m pytest "$tests::test_a_writer_publishing_while_readers_read_never_yields_anything_but_a_validated_generation_or_an_explicit_failure" -q -p no:cacheprovider 2>&1 | Where-Object { $_ -match 'passed|failed' } }
"--- mutations"
Mut 'no read retry (PermissionError becomes unreadable at once)'        'for attempt in range(READ_RETRIES):' 'for attempt in range(1):'
Mut 'hash never verified'                                                'if verify_hash:' 'if False:'
Mut 'cleanup removes the current generation too'                         'if gen in keep:' 'if False:'
Mut 'manifest agreement not required (generation id)'                    'for key, want in (("schema", MANIFEST_SCHEMA), ("generation_id", gen), ("file", file)):' 'for key, want in (("schema", MANIFEST_SCHEMA),):'
Mut 'size not compared with the file'                                    'if st.st_size != m_size:' 'if False:'
Mut 'pointer and manifest need not agree'                                'if m_size != size or m_sha1 != sha1:' 'if False:'
Mut 'no retry after a vanished file'                                     'for attempt in range(2):' 'for attempt in range(1):'
Mut 'failed attempt leaves its files behind'                             "        for p in a.made:
            try:
                p.unlink(missing_ok=True)" "        for p in []:
            try:
                p.unlink(missing_ok=True)"
Mut 'pointer written before the manifest'                                'stage = "manifest"' 'stage = "pointer"'
Mut 'cleanup failure fails the publication'                              '    except Exception:  # noqa: BLE001 (cleanup runs after the pointer moved and can never fail the publication)
        deferred = ["cleanup"]' '    except ZeroDivisionError:
        deferred = ["cleanup"]'
Mut 'a failed publish rewrites the previous status as success'           '"outcome": "failed", "stage": stage' '"outcome": "published", "stage": stage'
Mut 'unchanged source is republished'                                    'if current is not None and current.manifest.get("source") == identity:' 'if False:'
[System.IO.File]::WriteAllText($file, $orig)
"restored: " + ([System.IO.File]::ReadAllText($file) -eq $orig)
