"""The run envelope: one record of what a run was made from and what it asked of which model, so a stored verdict or report can be attributed and reproduced (audit A5's provenance gap, WP06).

A run records into a `RunRecorder` that is active for its duration (`with record_run() as rec:`). The metered and cached clients add one entry per logical request to it without any change to their
callers: the exact responding model, the attempts and how each ended, the token usage the provider reported (the string `unknown` when it reported none, never an invented number), whether the reply
came from the cache and which model first made it, digests of the prompts, and any validation failure a caller reported. The caller adds what only it knows: the packet and source hashes, the
calendar and cost policies, the schema versions, and the decision and entry times. `envelope()` assembles the record; it holds no credential and no environment value, and says `unknown` for what was not
recorded.

Nothing here reads the environment, a credential file or a header. Text that could carry a credential (an error from a provider, a header echoed back) is scrubbed on the way in and the finished record
is scrubbed again against any secret values the caller names, so a frozen manifest cannot embed one."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "athena.run-envelope/1"
UNKNOWN = "unknown"
REDACTED = "<redacted>"
_SECRET_TEXT = (
    re.compile(r"(?i)\b(authorization|proxy-authorization|x-api-key|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password|passwd)\b\s*[:=]\s*(?:bearer\s+)?[^\s,;'\"}]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|nvapi|pk|rk|ghp|gho|ghs|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
)
_SENSITIVE_KEY = re.compile(r"(?i)^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|credentials?|access[_-]?token|refresh[_-]?token|bearer|private[_-]?key|session[_-]?id)$")
_ACTIVE: ContextVar[RunRecorder | None] = ContextVar("athena_run_recorder", default=None)


def scrub(text: Any, secrets: Iterable[str] = ()) -> str:
    """`text` with credentials taken out: any value the caller names as a secret, header-like `authorization: ...`/`api_key=...` pairs, bearer tokens, and the shapes of the common key prefixes."""
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, REDACTED)
    for pattern in _SECRET_TEXT:
        out = pattern.sub(REDACTED, out)
    return out


def redact(obj: Any, secrets: Iterable[str] = ()) -> Any:
    """`obj` with the value of every sensitive key replaced and every string scrubbed, recursively. Counts such as `prompt_tokens` are not sensitive and are kept."""
    keep = tuple(secrets)
    if isinstance(obj, Mapping):
        return {str(k): (REDACTED if _SENSITIVE_KEY.match(str(k)) else redact(v, keep)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [redact(v, keep) for v in obj]
    if isinstance(obj, str):
        return scrub(obj, keep)
    return obj


def digest(text: str) -> str:
    """A short content hash of a prompt or an input, so a record can say which one without holding it."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def code_version(root: Path | str | None = None) -> str:
    """The commit the code was run from, read from the repository's `.git` files (no process is started), or `unknown`. A dirty tree is not detected; the version names a commit, not a state."""
    start = Path(root) if root is not None else Path(__file__).resolve()
    for base in [start, *start.parents][:6]:
        git = base / ".git"
        if git.is_dir():
            try:
                head = (git / "HEAD").read_text(encoding="utf-8").strip()
                if not head.startswith("ref:"):
                    return head[:12]
                ref = head.split(None, 1)[1].strip()
                if (git / ref).is_file():
                    return (git / ref).read_text(encoding="utf-8").strip()[:12]
                packed = (git / "packed-refs").read_text(encoding="utf-8")
                found = next((line.split()[0] for line in packed.splitlines() if line.endswith(" " + ref)), "")
                return found[:12] or UNKNOWN
            except OSError:
                return UNKNOWN
    return UNKNOWN


def _attempt_totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Provider attempts over a run, without turning missing telemetry into a number. Only a call whose `attempts` is a list counts (an empty list is a known zero: nothing was sent). A call whose attempts
    are the string `unknown`, or absent, or not a list, is counted apart. `provider_attempts` is the total only when every call's attempts are known; otherwise it says `unknown`, and `provider_attempts_known`
    is the subtotal over the calls that did report, which is not the run's total."""
    known = [c["attempts"] for c in calls if isinstance(c.get("attempts"), list)]
    missing = len(calls) - len(known)
    subtotal = sum(len(a) for a in known)
    return {"provider_attempts": subtotal if not missing else UNKNOWN, "provider_attempts_known": subtotal, "calls_with_unknown_attempts": missing}


class RunRecorder:
    def __init__(self, run_id: str | None = None, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.run_id = run_id or uuid.uuid4().hex[:16]
        self._clock = clock
        self.started_at = clock()
        self.calls: list[dict[str, Any]] = []
        self.validation: list[dict[str, Any]] = []
        self.inputs: dict[str, Any] = {}
        self.policies: dict[str, Any] = {}
        self.schemas: dict[str, Any] = {}

    def add_call(self, **fields: Any) -> dict[str, Any]:
        entry = {"seq": len(self.calls) + 1, **redact(fields)}
        self.calls.append(entry)
        return entry

    def add_validation(self, source: str, errors: Iterable[str]) -> None:
        self.validation.append({"seq": len(self.validation) + 1, "source": str(source), "errors": [scrub(e)[:240] for e in errors]})

    def envelope(self, *, decision_completed_at: str | None = None, entry_policy: str | None = None, code: str | None = None, secrets: Iterable[str] = ()) -> dict[str, Any]:
        """The record: identity and times, the code version, the schema versions and the inputs and policies the caller set, every logical request, and the validation failures. Credentials are scrubbed."""
        calls = self.calls
        live = [c for c in calls if c.get("origin") == "live"]
        body = {
            "schema": SCHEMA, "run_id": self.run_id, "started_at": self.started_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "finished_at": self._clock().astimezone(timezone.utc).isoformat(timespec="seconds"),
            "code": {"version": code or code_version()}, "schemas": dict(self.schemas), "inputs": dict(self.inputs), "policies": dict(self.policies),
            "times": {"decision_completed_at": decision_completed_at or UNKNOWN, "entry_policy": entry_policy or UNKNOWN},
            "calls": calls, "validation_failures": self.validation,
            "summary": {"logical_requests": len(calls), "answered_live": sum(1 for c in live if c.get("outcome") == "answered"), "from_cache": sum(1 for c in calls if c.get("origin") == "cache"),
                        **_attempt_totals(calls), "validation_failures": len(self.validation)},
        }
        return redact(json.loads(json.dumps(body, default=str)), secrets)  # type: ignore[no-any-return]


def current() -> RunRecorder | None:
    return _ACTIVE.get()


@contextmanager
def record_run(run_id: str | None = None, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Iterator[RunRecorder]:
    """Make a recorder active for the duration of a run. Nested runs record into their own recorder, and the outer one is active again afterwards."""
    rec = RunRecorder(run_id, clock)
    token = _ACTIVE.set(rec)
    try:
        yield rec
    finally:
        _ACTIVE.reset(token)


def note_call(**fields: Any) -> None:
    """Add a logical request to the active run, if there is one. Called by the clients; a run that is not being recorded costs nothing."""
    rec = _ACTIVE.get()
    if rec is not None:
        rec.add_call(**fields)


def note_validation(source: str, errors: Iterable[str]) -> None:
    rec = _ACTIVE.get()
    if rec is not None:
        rec.add_validation(source, list(errors))
