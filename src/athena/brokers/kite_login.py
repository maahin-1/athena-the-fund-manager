"""Turn the request token from the Kite login into the day's access token and save it.

    python -m athena.brokers.kite_login --url          # print the login address (needs KITE_API_KEY)
    python -m athena.brokers.kite_login REQUEST_TOKEN  # or the whole redirected address; exchanges it and saves KITE_ACCESS_TOKEN
    python -m athena.brokers.kite_login --listen       # open the login page, catch the redirect on 127.0.0.1:5000, exchange and save at once
    python -m athena.brokers.kite_login --check        # ask Kite who the saved token belongs to

The request token works once and only for a few minutes. The access token expires at 6 AM the next day, so this is a daily step. Neither
the secret nor the tokens are printed. See https://kite.trade/docs/connect/v3/user/."""
from __future__ import annotations

import argparse
import hashlib
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from athena.contracts import AthenaError
from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting

LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
SESSION_URL = "https://api.kite.trade/session/token"
PROFILE_URL = "https://api.kite.trade/user/profile"
TOKEN_NAME = "KITE_ACCESS_TOKEN"
Post = Callable[..., Any]


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()


def request_token_from(text: str) -> str:
    """The token itself, or the `request_token` parameter of the address the browser was sent to."""
    text = text.strip()
    if "request_token=" in text:
        values = parse_qs(urlparse(text).query).get("request_token") or re.findall(r"request_token=([^&\s]+)", text)
        if values:
            return values[0]
    return text


def exchange(api_key: str, api_secret: str, request_token: str, post: Post = requests.post) -> str:
    """The access token for a request token, or an `AthenaError` carrying Kite's own message."""
    response = post(
        SESSION_URL, headers={"X-Kite-Version": "3"}, timeout=30,
        data={"api_key": api_key, "request_token": request_token, "checksum": checksum(api_key, request_token, api_secret)},
    )
    try:
        body = response.json()
    except ValueError:
        raise AthenaError(f"Kite answered {response.status_code} with no JSON") from None
    token = (body.get("data") or {}).get("access_token")
    if body.get("status") != "success" or not token:
        raise AthenaError(f"Kite refused the token exchange: {body.get('error_type', '')} {str(body.get('message', ''))[:120]}".strip())
    return token


def save_setting(name: str, value: str, env_file: Path | str = DEFAULT_ENV_FILE) -> None:
    """Set `name=value` in the settings file, replacing its line or adding one; every other line is left as it is."""
    path = Path(env_file)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    line = f"{name}={value}"
    for i, existing in enumerate(lines):
        if "=" in existing and existing.split("=", 1)[0].strip() == name:
            lines[i] = line
            break
    else:
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


LISTEN_PORT = 5000  # the redirect address registered for the app is http://127.0.0.1:5000/
LISTEN_SECONDS = 300


def catch_request_token(server: HTTPServer, seconds: float = LISTEN_SECONDS) -> str:
    """Wait on a bound local server for the browser's redirect and return its request token; an `AthenaError` when none comes."""
    found: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            values = parse_qs(urlparse(self.path).query).get("request_token")
            if values:
                found.append(values[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Athena has the login. You can close this tab." if values else b"No request token in this address.")

        def log_message(self, *args: Any) -> None:
            pass

    server.RequestHandlerClass = Handler
    server.timeout = 1.0
    waited = 0.0
    while not found and waited < seconds:
        server.handle_request()
        waited += server.timeout
    if not found:
        raise AthenaError("no login arrived on the local address in time")
    return found[0]


def check(api_key: str, access_token: str, get: Callable[..., Any] = requests.get) -> dict[str, Any]:
    response = get(PROFILE_URL, headers={"X-Kite-Version": "3", "Authorization": f"token {api_key}:{access_token}"}, timeout=30)
    body = response.json()
    if body.get("status") != "success":
        raise AthenaError(f"Kite refused the token: {body.get('error_type', '')} {str(body.get('message', ''))[:120]}".strip())
    return body["data"]


def main(argv: Sequence[str] | None = None, post: Post = requests.post, get: Callable[..., Any] = requests.get) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.brokers.kite_login", description=__doc__.split("\n")[0])
    parser.add_argument("request_token", nargs="?", help="the request token, or the whole address the browser was sent to")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--url", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--listen", action="store_true", help="open the login page and catch the redirect instead of pasting it")
    args = parser.parse_args(argv)
    api_key = read_setting("KITE_API_KEY", args.env_file).strip()
    if not api_key:
        print("KITE_API_KEY is not set")
        return 1
    try:
        if args.url:
            print(LOGIN_URL.format(api_key=api_key))
            return 0
        if args.check:
            data = check(api_key, read_setting(TOKEN_NAME, args.env_file).strip(), get)
            print(f"token works for {data.get('user_id')} ({data.get('broker')}); exchanges {','.join(data.get('exchanges') or [])}")
            return 0
        secret = read_setting("KITE_API_SECRET", args.env_file).strip()
        if args.listen and secret:
            server = HTTPServer(("127.0.0.1", LISTEN_PORT), BaseHTTPRequestHandler)
            try:
                print("opening the login page; log in and this window finishes by itself", flush=True)
                webbrowser.open(LOGIN_URL.format(api_key=api_key))
                args.request_token = catch_request_token(server)
            finally:
                server.server_close()
        if not secret or not args.request_token:
            print("needs KITE_API_SECRET in the settings file and the request token as the argument")
            return 1
        token = exchange(api_key, secret, request_token_from(args.request_token), post)
        save_setting(TOKEN_NAME, token, args.env_file)
        print(f"saved {TOKEN_NAME} (valid until 6 AM tomorrow)")
        return 0
    except (AthenaError, requests.RequestException, ValueError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
