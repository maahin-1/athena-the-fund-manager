"""An append-only record of everything the order desk does, each line bound to the one before it by a hash, so an edit or a deleted line is found.

    python -m athena.brokers audit --verify

Nothing here rewrites a line. Text that looks like a credential is replaced before it is written, so a broker's error body cannot leak a token into the file."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(".athena") / "broker" / "audit.jsonl"
_TOKENISH = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+|[A-Za-z0-9_\-\.]{32,}")
GENESIS = "0" * 64


def redact(value: Any) -> Any:
    """Strings, and the strings inside lists and dicts, with anything token-like replaced. A broker's error text is untrusted and may echo a credential."""
    if isinstance(value, str):
        return _TOKENISH.sub("<redacted>", value)[:600]
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _digest(prev: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path | str = AUDIT_PATH, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), key: bytes | None = None):
        """With a `key` (kept outside the project, see `athena.brokers.human`) every new line also carries a signature of its hash, so a log rewritten by something that can edit the project's files but
        cannot read the key fails `verify(key)`. Without one the lines are chained only."""
        self.path = Path(path)
        self._clock = clock
        self.key = key

    def _last(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        seq, prev = 0, GENESIS
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    seq, prev = row["seq"], row["hash"]
        return seq, prev

    def append(self, event: str, **data: Any) -> dict[str, Any]:
        from athena.brokers.lock import exclusive

        with exclusive(self.path.with_suffix(".lock")):  # reading the tail and writing the next line are one step
            return self._append(event, data)

    def _append(self, event: str, data: dict[str, Any]) -> dict[str, Any]:
        seq, prev = self._last()
        body = {"seq": seq + 1, "at": self._clock().isoformat(timespec="seconds"), "event": event, "data": redact(data), "prev": prev}
        row = {**body, "hash": _digest(prev, {k: body[k] for k in ("seq", "at", "event", "data")})}
        if self.key:
            row["mac"] = hmac.new(self.key, row["hash"].encode(), hashlib.sha256).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        return row

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def verify(self, key: bytes | None = None) -> tuple[bool, int | None]:
        """`(True, None)` when every line follows from the one before it; else `(False, seq of the first line that does not)`.

        With the `key`, a line that carries a signature must match it, and once one line is signed every later line must be (a signed log cannot be cut back to an unsigned one and extended)."""
        prev, expected, signed = GENESIS, 1, False
        for row in self.rows():
            body = {k: row[k] for k in ("seq", "at", "event", "data")}
            if row["seq"] != expected or row["prev"] != prev or row["hash"] != _digest(prev, body):
                return False, row["seq"]
            if key is not None:
                mac = row.get("mac")
                if mac:
                    signed = True
                    if not hmac.compare_digest(mac, hmac.new(key, row["hash"].encode(), hashlib.sha256).hexdigest()):
                        return False, row["seq"]
                elif signed:
                    return False, row["seq"]
            prev, expected = row["hash"], expected + 1
        return True, None
