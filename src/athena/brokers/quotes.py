"""A live quote for the paper broker, from the running terminal's own stream (so it needs the terminal up and the market open).

It opens the terminal's socket, asks to watch one symbol and takes the first quote frame for it: the last price and the best bid and ask. Cash-market NSE symbols only; anything else, or no
frame within the timeout, is `None`, and the desk then refuses the order because it cannot check the price."""
from __future__ import annotations

import json
import time
import urllib.request

from athena.brokers.model import Price

TERMINAL_WS = "ws://127.0.0.1:8700/ws"
TERMINAL_ORIGIN = "http://127.0.0.1:8700"
TERMINAL_STATUS = "http://127.0.0.1:8700/api/status"


def frame_price(frame: dict, symbol: str) -> Price | None:
    """The price in a terminal `q` frame for `symbol`, or None when the frame does not carry it."""
    for row in frame.get("q", []) if frame.get("type") == "q" else []:
        if row.get("s") == symbol and isinstance(row.get("l"), (int, float)) and row["l"] > 0:
            bids, asks = row.get("b") or [], row.get("a") or []
            age = (time.time() * 1000 - row["t"]) / 1000 if isinstance(row.get("t"), (int, float)) and row["t"] > 0 else None
            return Price(float(row["l"]), float(bids[0][0]) if bids else None, float(asks[0][0]) if asks else None, str(row.get("src", "")), age)
    return None


def feed_is_live(status_url: str = TERMINAL_STATUS) -> bool:
    """Whether the terminal says its feed is the real broker stream (`/api/status`: feed on, not the fake feed). A terminal that cannot be asked is not live."""
    try:
        with urllib.request.urlopen(status_url, timeout=3) as response:  # noqa: S310 - a fixed loopback address
            status = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return status.get("feed") == "on" and status.get("live", True) is True


def terminal_quote(symbol: str, exchange: str = "NSE", timeout: float = 6.0, url: str = TERMINAL_WS) -> Price | None:
    if exchange != "NSE" or not feed_is_live():
        return None
    try:
        from websockets.sync.client import connect

        with connect(url, origin=TERMINAL_ORIGIN, open_timeout=3, max_size=2**24) as ws:
            ws.send(json.dumps({"type": "watch", "symbols": [symbol]}))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    raw = ws.recv(timeout=max(0.1, end - time.monotonic()))
                except TimeoutError:
                    return None
                price = frame_price(json.loads(raw), symbol)
                if price is not None:
                    return price
    except Exception:  # the terminal is not running, or refused the socket
        return None
    return None
