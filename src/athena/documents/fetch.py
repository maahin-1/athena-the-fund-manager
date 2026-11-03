"""Download one long PDF (a draft offer document is 8 to 15 MB) reliably and politely.

The regulator's file server drops a long transfer now and then ("connection broken, 650 KB of 9 MB read"), so a transfer is checked against the length the server announced and tried again a few
times with a pause. The honest user agent is the one every other collector uses; nothing is fetched twice for the same address because the caller keeps the content hash."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from athena.contracts import AthenaError
from athena.news.publisher_feeds import USER_AGENT

MAX_BYTES = 80_000_000
ATTEMPTS = 3
PAUSE_SECONDS = 3.0
TIMEOUT_SECONDS = 180


def fetch_pdf(
    url: str,
    *,
    get: Callable[..., Any] | None = None,
    attempts: int = ATTEMPTS,
    pause: float = PAUSE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    """The PDF at `url`. A transfer shorter than announced, a server error, a file that is not a PDF or one over `max_bytes` is an `AthenaError` once `attempts` tries are used up (a file that is not
    a PDF or is too big is not tried again: asking twice does not change it)."""
    if get is None:
        import requests

        get = requests.get
    last = "no attempt was made"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"}, timeout=TIMEOUT_SECONDS)
            status = int(getattr(response, "status_code", 200))
            if status != 200:
                last = f"the server answered {status}"
            else:
                body = bytes(response.content)
                announced = response.headers.get("content-length")
                if len(body) > max_bytes:
                    raise AthenaError(f"the file is {len(body) // 1_000_000} MB, over the {max_bytes // 1_000_000} MB limit")
                if announced and announced.isdigit() and int(announced) != len(body):
                    last = f"the transfer stopped at {len(body)} of {announced} bytes"
                elif body[:4] != b"%PDF":
                    raise AthenaError("the address did not give a PDF")
                else:
                    return body
        except AthenaError:
            raise
        except Exception as exc:  # a dropped connection, a timeout
            last = f"{type(exc).__name__}: {exc}"[:200]
        if attempt < attempts:
            sleep(pause * attempt)
    raise AthenaError(f"could not download {url}: {last}")
