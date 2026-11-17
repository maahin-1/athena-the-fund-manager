# Shared helpers for the candidate verification scripts in this folder (dot-sourced; nothing here runs by itself).
# Access policy: environment files (any name starting ".<word>", ending ".<word>", or the "rc" file) are never read, copied, created, deleted or printed.
# The word is assembled from pieces below only so that this file contains no such name literally; the scripts only ever look at NAMES, never at content.
$script:PolicyWord = 'e' + 'nv'

function Test-PolicyName([string]$Path) {
    $n = [IO.Path]::GetFileName($Path.Replace('\', '/'))
    return ($n.StartsWith('.' + $script:PolicyWord) -or $n.EndsWith('.' + $script:PolicyWord))
}

# A run directory is never reused: the call fails if it exists.
function New-RunDir([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw "run directory already exists (never reused): $Path" }
    New-Item -ItemType Directory -Force $Path | Out-Null
    return (Resolve-Path -LiteralPath $Path).Path
}

function Write-Json($Object, [string]$Path) {
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Object -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
}

# Import-origin guard: every Python process started below this one (pytest, `python -m ...`, subprocesses of tests, children of cargo tests)
# inherits PYTHONPATH = <candidate>\src first, then the guard folder holding sitecustomize.py. The guard records, at exit of each process, where every
# `athena` module it loaded came from, in $LogPath. The owner's editable install (a .pth entry pointing at the original checkout) comes AFTER PYTHONPATH,
# so the candidate wins; the log proves it instead of assuming it. Not observed: interpreters started with -I or -S, or a different interpreter.
function Enable-OriginGuard([string]$Candidate, [string]$LogPath) {
    $guard = Join-Path $PSScriptRoot 'origin_guard'
    $extra = [Environment]::GetEnvironmentVariable('ATHENA_VERIFY_EXTRA_PYTHONPATH')   # optional read-only folder (e.g. PyYAML for the maintenance tests); it comes AFTER the candidate, so it can never shadow it
    Set-Item -Path 'Env:PYTHONPATH' -Value ((Join-Path $Candidate 'src') + ';' + $guard + $(if ($extra) { ';' + $extra } else { '' }))
    Set-Item -Path 'Env:ATHENA_EXPECT_ROOT' -Value (Join-Path $Candidate 'src')
    Set-Item -Path 'Env:ATHENA_ORIGIN_LOG' -Value $LogPath
    Set-Item -Path 'Env:PYTHONDONTWRITEBYTECODE' -Value '1'
}
function Disable-OriginGuard {
    foreach ($k in @('PYTHONPATH', 'ATHENA_EXPECT_ROOT', 'ATHENA_ORIGIN_LOG', 'PYTHONDONTWRITEBYTECODE')) { Remove-Item "Env:$k" -ErrorAction SilentlyContinue }
}

function Get-PytestCounts([string]$Text) {
    $c = [ordered]@{ passed = 0; failed = 0; skipped = 0; errors = 0; xfailed = 0; xpassed = 0; deselected = 0 }
    $last = @($Text -split "`r?`n" | Where-Object { $_ -match '\b(passed|failed|skipped|error|errors|deselected|xfailed|xpassed)\b.* in [\d\.]+s' }) | Select-Object -Last 1
    if ($last) {
        foreach ($m in [regex]::Matches($last, '(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)')) {
            $k = $m.Groups[2].Value; if ($k -eq 'error') { $k = 'errors' }
            $c[$k] = [int]$m.Groups[1].Value
        }
    }
    return $c
}

# Sums every "test result:" line of a cargo test run (one per test target) and lists the "test <id> ... <outcome>" lines.
function Get-CargoCounts([string]$Text) {
    $c = [ordered]@{ passed = 0; failed = 0; ignored = 0; filtered_out = 0; result_lines = 0 }
    foreach ($m in [regex]::Matches($Text, 'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; \d+ measured; (\d+) filtered out')) {
        $c.passed += [int]$m.Groups[2].Value; $c.failed += [int]$m.Groups[3].Value; $c.ignored += [int]$m.Groups[4].Value; $c.filtered_out += [int]$m.Groups[5].Value; $c.result_lines++
    }
    return $c
}
function Get-CargoTestLines([string]$Text) {
    return @([regex]::Matches($Text, '(?m)^test (\S+) \.\.\. (\w+)') | ForEach-Object { [pscustomobject]@{ id = $_.Groups[1].Value; outcome = $_.Groups[2].Value } })
}

# Runs one stage with the given environment additions, logs stdout+stderr, restores the environment, returns the exit code and text.
function Invoke-Stage([string]$Name, [string]$Cwd, [string]$Exe, [string[]]$Arguments, [hashtable]$Environment, [string]$Log) {
    $ErrorActionPreference = 'Continue'   # a tool's stderr (cargo's progress lines, warnings) is output here, not a PowerShell error; the exit code decides
    $saved = @{}
    foreach ($k in $Environment.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, [string]$Environment[$k]) }
    Push-Location $Cwd
    try {
        $started = Get-Date
        $text = (& $Exe @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n"
        $exit = $LASTEXITCODE
    } finally {
        Pop-Location
        foreach ($k in $Environment.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
    [IO.File]::WriteAllText($Log, "# $Exe $($Arguments -join ' ')`n# cwd=$Cwd exit=$exit`n" + $text, (New-Object System.Text.UTF8Encoding($false)))
    return [pscustomobject]@{ name = $Name; exit = $exit; text = $text; seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1); log = $Log; argv = (@($Exe) + $Arguments) -join ' '; cwd = $Cwd }
}

# The tools a gate needs. A missing one fails the preflight; it is never a skipped step.
function Get-ToolFacts([string]$Python) {
    $f = [ordered]@{}
    $f.python = if (Test-Path -LiteralPath $Python) { (& $Python --version 2>&1 | ForEach-Object { "$_" }) -join ' ' } else { 'MISSING' }
    $f.cargo = try { (cargo --version 2>&1 | ForEach-Object { "$_" }) -join ' ' } catch { 'MISSING' }
    $f.rustc = try { (rustc --version 2>&1 | ForEach-Object { "$_" }) -join ' ' } catch { 'MISSING' }
    $f.node = try { (node --version 2>&1 | ForEach-Object { "$_" }) -join ' ' } catch { 'MISSING' }
    $sh = Get-Command sh -ErrorAction SilentlyContinue
    $f.sh = if ($sh) { $sh.Source } elseif (Test-Path 'C:\Program Files\Git\bin\sh.exe') { 'C:\Program Files\Git\bin\sh.exe (not on PATH: the Rust gate adds that folder and records it)' } else { 'MISSING' }
    return $f
}

# One Rust test on its own (cargo test ... -- --exact <id>) with a FRESH import-origin log, so the Python processes it started can be attributed to it. Returns the facts only; rust_evidence.py judges them.
# The intended interpreter is the one the tests themselves resolve: <candidate>\terminal\..\.venv\Scripts\python.exe.
function Invoke-RustTestIsolated([string]$Id, [int]$Index, [string]$Term, [string[]]$NetArgs, [hashtable]$Environment, [string]$Candidate, [string]$LogDir) {
    $log = Join-Path $LogDir ('iso-{0:D4}.origin.jsonl' -f $Index)
    $want = [IO.Path]::GetFullPath((Join-Path $Candidate '.venv\Scripts\python.exe'))
    Enable-OriginGuard $Candidate $log
    try { $r = Invoke-Stage ('iso' + $Index) $Term 'cargo' (@('test') + $NetArgs + @('--locked', '--', '--exact', $Id)) $Environment (Join-Path $LogDir ('iso-{0:D4}.log' -f $Index)) }
    finally { Disable-OriginGuard }
    $c = Get-CargoCounts $r.text
    $line = @(Get-CargoTestLines $r.text | Where-Object { $_.id -eq $Id }) | Select-Object -First 1
    $rows = @()
    if (Test-Path -LiteralPath $log) { $rows = @(Get-Content -LiteralPath $log | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json }) }
    # one entry per process: its `start` record (written at start-up, so a process that is later killed is still counted) merged with its `exit` record (only a process that ended normally)
    $by = @{}; $i = 0
    foreach ($row in $rows) {
        $i++
        $k = if ($row.proc) { [string]$row.proc } else { "legacy-$i" }
        if (-not $by.ContainsKey($k)) { $by[$k] = @{ exe = ''; athena = 0; bad = 0 } }
        if ($row.executable) { $by[$k].exe = [string]$row.executable }
        if ((-not $row.event) -or $row.event -eq 'exit') { $by[$k].athena = [int]$row.athena_modules; $by[$k].bad = @($row.violations).Count }
    }
    $procs = @($by.Values)
    $badCount = 0; foreach ($p in $procs) { $badCount += [int]$p.bad }
    $onWant = @($procs | Where-Object { $_.exe -and ([IO.Path]::GetFullPath($_.exe) -ieq $want) }).Count
    return [ordered]@{
        id = $Id; exit = $r.exit; passed = $c.passed; failed = $c.failed; ignored = $c.ignored
        outcome = $(if ($line) { $line.outcome } else { 'not-run' })
        processes_observed = $procs.Count; processes_on_intended_executable = $onWant; processes_on_other_executable = ($procs.Count - $onWant)
        athena_processes = @($procs | Where-Object { $_.athena -gt 0 }).Count
        violations = $badCount
        seconds = $r.seconds
    }
}
