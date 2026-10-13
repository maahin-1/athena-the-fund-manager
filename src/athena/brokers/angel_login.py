"""Log in to Angel One's SmartAPI for market data.

    python -m athena.brokers.angel_login --check     # logs in and says whether it worked; prints no secret and no token

Reads ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and ANGEL_TOTP_SECRET from the settings file. The six-digit code is made from the TOTP
secret each time, so the login needs no browser. This is for reading prices only: nothing here places an order."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError
from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting

NAMES = ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")


@dataclass(frozen=True)
class Session:
    api_key: str
    client_code: str
    jwt_token: str = ""
    feed_token: str = ""
    refresh_token: str = ""

    def __repr__(self) -> str:  # never show the tokens, wherever a session ends up in a log
        return f"Session(client_code={self.client_code!r})"


def settings(env_file: Path | str = DEFAULT_ENV_FILE) -> dict[str, str]:
    values = {name: read_setting(name, env_file).strip() for name in NAMES}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise AthenaError("missing in the settings file: " + ", ".join(missing))
    return values


def login(
    api_key: str, client_code: str, pin: str, totp_secret: str,
    connect: Callable[[str], Any] | None = None, totp: Callable[[str], str] | None = None,
) -> Session:
    """A logged-in `Session`, or an `AthenaError` with the broker's message (never the PIN, the secret or a token)."""
    if connect is None:
        from SmartApi import SmartConnect

        connect = SmartConnect
    if totp is None:
        import pyotp

        totp = lambda secret: pyotp.TOTP(secret).now()
    try:
        code = totp(totp_secret)
    except Exception:  # pyotp raises on a secret that is not base32
        raise AthenaError("ANGEL_TOTP_SECRET is not a valid TOTP secret (it should be the text key beside the QR code)") from None
    client = connect(api_key)
    try:
        reply = client.generateSession(client_code, pin, code)
    except Exception as exc:
        raise AthenaError(f"the login request failed: {type(exc).__name__}") from None
    if not reply or not reply.get("status"):
        message = str((reply or {}).get("message") or "no reason given")[:120]
        raise AthenaError(f"Angel One refused the login: {message}")
    data = reply.get("data") or {}
    try:
        feed = client.getfeedToken() or ""
    except Exception:
        feed = ""
    return Session(api_key, client_code, str(data.get("jwtToken", "")), str(feed), str(data.get("refreshToken", "")))


def main(argv: Sequence[str] | None = None, connect: Callable[[str], Any] | None = None, totp: Callable[[str], str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.brokers.angel_login", description=__doc__.split("\n")[0])
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        values = settings(args.env_file)
        session = login(values["ANGEL_API_KEY"], values["ANGEL_CLIENT_CODE"], values["ANGEL_PIN"], values["ANGEL_TOTP_SECRET"], connect, totp)
    except AthenaError as exc:
        print(f"error: {exc}")
        return 1
    print(f"login works for {session.client_code}; feed token {'received' if session.feed_token else 'NOT received'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
