"""Run every check the project has, one after another, before a push. Nothing here is scheduled or run by itself.

    python scripts/check_all.py            Python tests, Rust tests, web unit tests, the indicator golden check
    python scripts/check_all.py --fast     skip the Rust and web steps (Python only)

There is no CI service for this repository, so this is the gate: it prints one line per step and exits non-zero if any step failed. The slow browser (Playwright) tests are not run here;
run `npm run e2e` in terminal/web when the page changed."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = [sys.executable]


def steps(fast: bool) -> list[tuple[str, list[str], Path]]:
    out: list[tuple[str, list[str], Path]] = [("python tests", [*PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], ROOT)]
    golden = ROOT / "scripts" / "formula_golden.py"
    if golden.exists():
        out.append(("indicator golden check", [*PY, str(golden), "--check"], ROOT))
    if not fast:
        out.append(("rust tests", ["cargo", "test", "--quiet"], ROOT / "terminal"))
        out.append(("web unit tests", ["npm", "test", "--silent"], ROOT / "terminal" / "web"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args(argv)
    failed = []
    for name, command, cwd in steps(args.fast):
        started = time.time()
        shell = command[0] == "npm"  # npm is a script on Windows
        result = subprocess.run(command, cwd=cwd, shell=shell, capture_output=True, text=True)
        ok = result.returncode == 0
        tail = (result.stdout or result.stderr).strip().splitlines()[-1:] or [""]
        print(f"{'ok  ' if ok else 'FAIL'} {name:<24} {time.time() - started:5.0f}s  {tail[0][:100]}")
        if not ok:
            failed.append(name)
            print((result.stdout + result.stderr)[-1500:])
    print("all checks passed" if not failed else "failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
