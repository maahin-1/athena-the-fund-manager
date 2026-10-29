"""Things only a person at the keyboard can do, kept out of reach of a script or a tool that runs commands for the person.

A live order is confirmed by typing the order back at a console. A process whose input or output is a pipe (a tool, a scheduler, a script, an assistant running a command) has no console, so `ask`
returns None and the order is refused. This stops a tool that can run `python -m athena.brokers confirm TOKEN` from placing a real order by itself; it is not a defence against a malicious program
running as the same person, which could do anything the person can.

The user-level folder (`user_dir`) holds two things the project folder does not: the key that signs the audit log and the pin on the limits file. A tool that edits files in the project cannot
reach them by editing the project."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

LIMITS_PIN = "limits.pin"
AUDIT_KEY = "audit.key"


def user_dir() -> Path:
    base = os.environ.get("ATHENA_USER_DIR")
    if base:
        return Path(base)
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "athena"


def ask(prompt: str, stdin: TextIO | None = None, stdout: TextIO | None = None) -> str | None:
    """A line typed by a person at a console, or None when there is no console to type at."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        if not (stdin.isatty() and stdout.isatty()):
            return None
        stdout.write(prompt)
        stdout.flush()
        line = stdin.readline()
    except (OSError, ValueError):
        return None
    return line.strip() if line else None


def order_phrase(side: str, quantity: int, symbol: str, price: float) -> str:
    """What the person types to confirm: the order itself, so pressing Enter or pasting a stored answer cannot place it."""
    return f"{side} {quantity} {symbol} {price:.2f}"


def console_confirm(phrase: str, summary: str, ask_fn: Callable[[str], str | None] = ask) -> bool:
    answer = ask_fn(f"\n{summary}\nThis sends a REAL order to your broker. To go ahead, type exactly: {phrase}\n> ")
    return answer is not None and answer == phrase


# --- the key that signs the audit log -------------------------------------------------------------------------------------------------------
def audit_key(directory: Path | None = None, create: bool = True) -> bytes | None:
    path = (directory or user_dir()) / AUDIT_KEY
    try:
        key = bytes.fromhex(path.read_text(encoding="utf-8").strip())
        if len(key) == 32:
            return key
        return None  # a truncated or damaged key file is not replaced behind the person's back: lines already signed with the old one could not be checked
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return None
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_text(key.hex(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


# --- the pin on the limits file -------------------------------------------------------------------------------------------------------------
def _digest(limits_path: Path) -> str:
    try:
        return hashlib.sha256(Path(limits_path).read_bytes()).hexdigest()
    except OSError:
        return hashlib.sha256(b"no limits file: the defaults").hexdigest()


def pin_limits(limits_path: Path | str, directory: Path | None = None) -> str:
    """Record the limits file as it is now as the one a person has looked at and accepted. Returns the digest."""
    pin = (directory or user_dir()) / LIMITS_PIN
    pin.parent.mkdir(parents=True, exist_ok=True)
    digest = _digest(Path(limits_path))
    pin.write_text(digest, encoding="utf-8")
    return digest


def limits_pinned(limits_path: Path | str, directory: Path | None = None) -> bool:
    """Whether the limits file is exactly the one a person pinned. Any edit, by anyone, un-pins it until a person pins it again."""
    try:
        recorded = ((directory or user_dir()) / LIMITS_PIN).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return hmac.compare_digest(recorded, _digest(Path(limits_path)))
