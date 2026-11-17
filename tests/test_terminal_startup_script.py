"""scripts/terminal.ps1 must stop when a native build fails (T7).

In Windows PowerShell a failing native command (npm, cargo) does not stop a script that sets `$ErrorActionPreference = 'Stop'`; the script went on and started the previous build.
These tests run a COPY of the script in a throwaway project tree. `npm` and `cargo` are stub .cmd files first on PATH that log each call and exit with a chosen code; the terminal
"executable" is a tiny real console program that only logs that it was started; `Start-Process` (the browser) is shadowed by a function so nothing opens. The live terminal is never
started, no network or credential is touched. Skipped off Windows or without Windows PowerShell."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "terminal.ps1"
POWERSHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not POWERSHELL, reason="needs Windows PowerShell")

STUB_EXE_SOURCE = r"""
using System;
using System.IO;
public static class Stub { public static int Main(string[] args) {
    File.AppendAllText(Environment.GetEnvironmentVariable("STUB_LOG"), "launch " + string.Join(" ", args) + "\n");
    return 0; } }
"""

HARNESS = r"""
param([string]$Script, [string]$Log)
# The browser: shadow Start-Process so no window opens (a function in the calling scope wins over the cmdlet).
function Start-Process { param([string]$FilePath) Add-Content -Path $Log -Value "browser $FilePath" }
& $Script -Fake -Port 8799
"""


@pytest.fixture(scope="module")
def stub_exe(tmp_path_factory) -> Path:
    """A real console program that logs its own start; built once with the compiler that ships with Windows PowerShell."""
    folder = tmp_path_factory.mktemp("stub_exe")
    source, exe = folder / "stub.cs", folder / "athena-terminal.exe"
    source.write_text(STUB_EXE_SOURCE, encoding="utf-8")
    build = f"Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath '{source}') -OutputAssembly '{exe}' -OutputType ConsoleApplication"
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", build], capture_output=True, text=True, timeout=120)
    if done.returncode != 0 or not exe.exists():
        pytest.skip(f"could not build a stub executable: {done.stderr[-200:]}")
    return exe


def project(tmp_path: Path, stub_exe: Path, *, dist: bool, node_modules: bool = True) -> Path:
    """A minimal copy of the repository layout the script expects."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "terminal.ps1")
    web = root / "terminal" / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "main.ts").write_text("export {}", encoding="utf-8")
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    (web / "package.json").write_text("{}", encoding="utf-8")
    (web / "package-lock.json").write_text("{}", encoding="utf-8")
    if node_modules:
        (web / "node_modules").mkdir()
    if dist:
        (web / "dist").mkdir()
        (web / "dist" / "index.html").write_text("<html>old build</html>", encoding="utf-8")
    (root / "terminal" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    release = root / "terminal" / "target" / "release"
    release.mkdir(parents=True)
    shutil.copy(stub_exe, release / "athena-terminal.exe")  # the PREVIOUS build, which a failed build must not fall back to launching
    return root


def stubs(tmp_path: Path, *, npm_exit: int = 0, cargo_exit: int = 0, makes_dist: bool = True, name: str = "bin") -> Path:
    """npm.cmd / cargo.cmd that log their arguments and exit with the given codes. A successful `npm run build` writes a fresh dist/index.html."""
    folder = tmp_path / name
    folder.mkdir()
    build_dist = r'if "%1 %2"=="run build" (if not exist dist mkdir dist & echo new build> dist\index.html)' if makes_dist else "rem"
    (folder / "npm.cmd").write_text(f'@echo off\r\necho npm %* >> "%STUB_LOG%"\r\n{build_dist}\r\nexit /b {npm_exit}\r\n', encoding="ascii")
    (folder / "cargo.cmd").write_text(f'@echo off\r\necho cargo %* >> "%STUB_LOG%"\r\nexit /b {cargo_exit}\r\n', encoding="ascii")
    return folder


def run(tmp_path: Path, root: Path, bin_dir: Path) -> tuple[int, list[str], str]:
    log = tmp_path / "log.txt"
    log.write_text("", encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(log)}
    done = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness), "-Script", str(root / "scripts" / "terminal.ps1"), "-Log", str(log)],
        capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path,
    )
    lines = [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return done.returncode, lines, done.stdout + done.stderr


def launches(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(("launch", "browser"))]


def test_a_failed_web_build_stops_the_script_before_cargo_the_browser_or_the_old_terminal(tmp_path, stub_exe):
    root = project(tmp_path, stub_exe, dist=False)  # no dist yet, so `npm run build` is needed
    code, lines, text = run(tmp_path, root, stubs(tmp_path, npm_exit=1))
    assert code != 0, text
    assert any(line.startswith("npm run build") for line in lines)
    assert not any(line.startswith("cargo") for line in lines) and launches(lines) == []
    assert "npm run build failed" in text and "exit code 1" in text  # says which step failed


def test_a_failed_npm_install_stops_the_script(tmp_path, stub_exe):
    root = project(tmp_path, stub_exe, dist=True, node_modules=False)
    code, lines, text = run(tmp_path, root, stubs(tmp_path, npm_exit=1))
    assert code != 0 and launches(lines) == [] and not any(line.startswith("cargo") for line in lines), text
    assert "npm install failed" in text


def test_a_failed_cargo_build_stops_the_script_and_does_not_launch_the_previous_executable(tmp_path, stub_exe):
    root = project(tmp_path, stub_exe, dist=False)
    code, lines, text = run(tmp_path, root, stubs(tmp_path, cargo_exit=101))
    assert code != 0, text
    assert any(line.startswith("cargo build --release") for line in lines)
    assert launches(lines) == [] and "cargo build failed" in text and "exit code 101" in text  # an old athena-terminal.exe is on disk and must not be started


def test_successful_builds_reach_the_launch_exactly_once(tmp_path, stub_exe):
    root = project(tmp_path, stub_exe, dist=False)
    code, lines, text = run(tmp_path, root, stubs(tmp_path))
    assert code == 0, text
    started = launches(lines)
    assert [s.split()[0] for s in started] == ["browser", "launch"]  # one browser open, one terminal start, in that order
    assert started[0] == "browser http://127.0.0.1:8799" and "--port 8799" in started[1] and "--fake-feed" in started[1] and "--web-dir" in started[1]
    order = [line.split()[0] for line in lines]
    assert order.index("npm") < order.index("cargo") < order.index("browser") < order.index("launch")


def test_an_up_to_date_build_is_not_repeated_but_the_native_build_still_runs_and_is_checked(tmp_path, stub_exe):
    root = project(tmp_path, stub_exe, dist=True)
    web = root / "terminal" / "web"
    future = (web / "dist" / "index.html").stat().st_mtime + 100  # dist newer than every source and config input
    os.utime(web / "dist" / "index.html", (future, future))
    code, lines, text = run(tmp_path, root, stubs(tmp_path))
    assert code == 0, text
    assert not any(line.startswith("npm run build") for line in lines) and any(line.startswith("cargo build") for line in lines)


@pytest.mark.parametrize("touched", ["package.json", "package-lock.json", "vite.config.ts", "tsconfig.json"])
def test_a_changed_package_or_config_file_makes_the_web_build_stale(tmp_path, stub_exe, touched):
    root = project(tmp_path, stub_exe, dist=True)
    web = root / "terminal" / "web"
    for extra in ("vite.config.ts", "tsconfig.json"):
        (web / extra).write_text("{}", encoding="utf-8")
    base = (web / "dist" / "index.html").stat().st_mtime
    for path in [web / "src" / "main.ts", web / "index.html", web / "package.json", web / "package-lock.json", web / "vite.config.ts", web / "tsconfig.json"]:
        os.utime(path, (base - 50, base - 50))  # everything older than the build
    code, lines, text = run(tmp_path, root, stubs(tmp_path))
    assert code == 0 and not any(line.startswith("npm run build") for line in lines), text  # nothing newer than dist: not stale
    os.utime(web / touched, (base + 10, base + 10))  # now a package or config file is newer than the build
    code, lines, text = run(tmp_path, root, stubs(tmp_path, name="bin2"))
    assert code == 0, text
    assert any(line.startswith("npm run build") for line in lines), f"{touched} changed but the web build was not redone"
