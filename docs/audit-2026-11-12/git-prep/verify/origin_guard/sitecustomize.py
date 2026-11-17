"""Import-origin recorder for candidate verification. Imported automatically by every Python process whose PYTHONPATH contains this folder.

Two records per process go to ATHENA_ORIGIN_LOG (one JSON line each, same `proc` id):
  * `start`, written at interpreter start-up: the process id, its command line (first arguments only) and its executable. A process that is later KILLED (a deadline, a cancelled job, a timeout test) never
    runs its exit hook, so without this record it would be invisible and a test that starts and kills Python would look as if it never started it;
  * `exit`, written when the process ends normally: how many `athena` modules it loaded and every one that was loaded from outside ATHENA_EXPECT_ROOT (the candidate's `src`).
It changes no behaviour and never raises. A process started with -I or -S, or with another interpreter, does not run this file and is therefore not observed: that is a stated limit, not a pass."""
import atexit
import json
import os
import sys
import uuid

_root = os.environ.get("ATHENA_EXPECT_ROOT")
_log = os.environ.get("ATHENA_ORIGIN_LOG")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _write(record: dict) -> None:
    try:
        with open(_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # a recorder must never break the process it observes
        pass


if _root and _log:
    _expected = _norm(_root).rstrip("\\/") + os.sep
    _proc = uuid.uuid4().hex
    _argv = [str(a)[:80] for a in sys.argv[:4]]
    _write({"event": "start", "proc": _proc, "pid": os.getpid(), "argv": _argv, "executable": sys.executable})

    def _report() -> None:
        try:
            modules, bad = 0, []
            for name, mod in list(sys.modules.items()):
                if name != "athena" and not name.startswith("athena."):
                    continue
                modules += 1
                places = []
                f = getattr(mod, "__file__", None)
                if f:
                    places.append(f)
                places += [p for p in (getattr(mod, "__path__", None) or [])]
                for p in places:
                    if not (_norm(p) + os.sep).startswith(_expected):
                        bad.append([name, p])
            _write({"event": "exit", "proc": _proc, "pid": os.getpid(), "argv": _argv, "executable": sys.executable, "athena_modules": modules, "violations": bad})
        except Exception:
            pass

    atexit.register(_report)
