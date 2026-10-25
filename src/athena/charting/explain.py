"""Explain a chart: the figures are worked out in code, a model only puts them into plain sentences.

    python -m athena.charting.explain < request.json         one JSON document on stdout

The request is what the page shows: `{"symbol", "interval", "bars": [{"t", "o", "h", "l", "c", "v"}], "drawings": [{"tool", "points": [{"t", "p"}], "text"}]}`, the bars being the
visible window. From it the code builds a numbered **facts sheet** (F1, F2 ...): the window and its return, the high and low and when, the close against both, the averages, the
RSI and ATR, the volume against its usual, the nearest swing support and resistance, and, for each line the person drew, where it stands at the last bar. A model is then asked to write
three to five sentences from that sheet, citing facts as [F3]; its reply is accepted only when it cites facts that exist and every number in it is on the sheet (the same check the
analysts' reasoning passes). A reply that fails twice, or no model at all, gives the facts sheet alone, which is the answer the model would have rephrased.

`ATHENA_EXPLAIN_MODEL=off` in the environment skips the model altogether. Nothing here predicts: no price target, no buy or sell, and the reading says what the picture shows and what level would change it."""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from athena.evaluation.grounding import numbers_in_text

MAX_BARS = 2000
MIN_BARS = 20
RSI_LENGTH, ATR_LENGTH, SWING = 14, 14, 3
IST = timezone(timedelta(hours=5, minutes=30))
SYSTEM = (
    "You explain what a price chart shows, in plain language, for its owner. This is a stylized reading, not financial advice. You are given a numbered facts sheet worked out in code; "
    "write three to five sentences from it and cite the facts you use as [F3]. Rules: use only figures that appear on the sheet, copied as written; do no arithmetic; give no price target "
    "and no instruction to buy or sell; say where the picture is strong and where it is weak; end with the level on the sheet that would change the picture. "
    'Reply with one JSON object and nothing else: {"read": "the sentences with [F#] citations", "cited": ["F1", "F3"]}.'
)


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class Fact:
    id: str
    name: str
    value: float | str
    unit: str = ""
    window: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "value": self.value, "unit": self.unit, "window": self.window}


def _day(t: float, intraday: bool) -> str:
    moment = datetime.fromtimestamp(t, tz=IST)
    return moment.strftime("%Y-%m-%d %H:%M") if intraday else moment.strftime("%Y-%m-%d")


def _sma(values: Sequence[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _rsi(closes: Sequence[float], n: int = RSI_LENGTH) -> float | None:
    """Wilder's relative strength index over the whole window."""
    if len(closes) <= n:
        return None
    gains = [max(b - a, 0.0) for a, b in zip(closes, closes[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(closes, closes[1:])]
    up, down = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        up, down = (up * (n - 1) + g) / n, (down * (n - 1) + l) / n
    return 100.0 if down == 0 else 100.0 - 100.0 / (1 + up / down)


def _atr(bars: Sequence[Mapping[str, float]], n: int = ATR_LENGTH) -> float | None:
    if len(bars) <= n:
        return None
    ranges = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(bars, bars[1:])]
    atr = sum(ranges[:n]) / n
    for r in ranges[n:]:
        atr = (atr * (n - 1) + r) / n
    return atr


def _pivots(bars: Sequence[Mapping[str, float]], width: int = SWING) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Swing highs and lows: a bar whose high (low) is the highest (lowest) of the `width` bars on each side."""
    highs, lows = [], []
    for i in range(width, len(bars) - width):
        around = bars[i - width : i + width + 1]
        if bars[i]["h"] >= max(b["h"] for b in around):
            highs.append((i, bars[i]["h"]))
        if bars[i]["l"] <= min(b["l"] for b in around):
            lows.append((i, bars[i]["l"]))
    return highs, lows


def _line_at(points: Sequence[Mapping[str, float]], t: float) -> float | None:
    """The price of the straight line through the first two points at time `t`."""
    if len(points) < 2 or points[1]["t"] == points[0]["t"]:
        return None
    slope = (points[1]["p"] - points[0]["p"]) / (points[1]["t"] - points[0]["t"])
    return points[0]["p"] + slope * (t - points[0]["t"])


LINE_TOOLS = {"trend": "trend line", "ray": "ray", "extended": "extended line", "info": "info line", "angle": "trend angle", "arrow": "arrow"}


def build_facts(request: Mapping[str, Any]) -> list[Fact]:
    """The facts sheet for a request; empty when there are too few bars to say anything."""
    bars = [b for b in request.get("bars", [])[-MAX_BARS:] if all(isinstance(b.get(k), (int, float)) for k in ("t", "o", "h", "l", "c"))]
    if len(bars) < MIN_BARS:
        return []
    interval = str(request.get("interval", "1D"))
    intraday = interval not in ("1D", "1W", "1M")
    closes = [b["c"] for b in bars]
    last = bars[-1]
    facts: list[Fact] = []

    def add(name: str, value: float | str, unit: str = "", window: str = "") -> Fact:
        facts.append(Fact(f"F{len(facts) + 1}", name, round(value, 4) if isinstance(value, float) else value, unit, window))
        return facts[-1]

    span = f"{_day(bars[0]['t'], intraday)} to {_day(last['t'], intraday)}"
    add("bars shown", float(len(bars)), "count", f"{interval} bars, {span}")
    add("last close", last["c"], "rupees", _day(last["t"], intraday))
    if closes[0] > 0:
        add("return over the window", (closes[-1] / closes[0] - 1) * 100, "percent", span)
    hi = max(range(len(bars)), key=lambda i: bars[i]["h"])
    lo = min(range(len(bars)), key=lambda i: bars[i]["l"])
    add("window high", bars[hi]["h"], "rupees", _day(bars[hi]["t"], intraday))
    add("window low", bars[lo]["l"], "rupees", _day(bars[lo]["t"], intraday))
    if bars[hi]["h"] > 0:
        add("last close below the window high", (1 - closes[-1] / bars[hi]["h"]) * 100, "percent")
    if bars[lo]["l"] > 0:
        add("last close above the window low", (closes[-1] / bars[lo]["l"] - 1) * 100, "percent")
    for n in (20, 50, 200):
        avg = _sma(closes, n)
        if avg:
            add(f"{n}-bar average", avg, "rupees", f"last {n} bars")
            add(f"last close against the {n}-bar average", (closes[-1] / avg - 1) * 100, "percent")
    rsi = _rsi(closes)
    if rsi is not None:
        add("RSI (14)", rsi, "index", "Wilder, over the window")
    atr = _atr(bars)
    if atr is not None and closes[-1] > 0:
        add("average true range (14) as a share of the close", atr / closes[-1] * 100, "percent")
    volumes = [b.get("v") for b in bars if isinstance(b.get("v"), (int, float)) and b["v"] > 0]
    if len(volumes) >= MIN_BARS and isinstance(last.get("v"), (int, float)) and last["v"] > 0:
        usual = sum(volumes[-21:-1]) / len(volumes[-21:-1])
        if usual > 0:
            add("last bar's volume against its usual", last["v"] / usual, "ratio", "average of the 20 bars before")
    highs, lows = _pivots(bars)
    above = [p for _, p in highs if p > closes[-1]]
    below = [p for _, p in lows if p < closes[-1]]
    if above:
        level = min(above)
        add("nearest swing high above the close", level, "rupees", "swing highs of the window")
        add("distance to it", (level / closes[-1] - 1) * 100, "percent")
    if below:
        level = max(below)
        add("nearest swing low below the close", level, "rupees", "swing lows of the window")
        add("distance to it", (1 - level / closes[-1]) * 100, "percent")
    for d in request.get("drawings", [])[:12]:
        tool, points = d.get("tool"), d.get("points", [])
        label = d.get("text") or LINE_TOOLS.get(tool, str(tool))
        if tool in ("hline", "hray") and points:
            level = points[0]["p"]
            add(f"the horizontal line you drew ({label})", level, "rupees")
            add("last close against that line", (closes[-1] / level - 1) * 100, "percent") if level > 0 else None
        elif tool in LINE_TOOLS:
            level = _line_at(points, last["t"])
            if level is not None and level > 0:
                add(f"the {LINE_TOOLS[tool]} you drew, at the last bar", level, "rupees")
                add("last close against that line", (closes[-1] / level - 1) * 100, "percent")
        elif tool == "fib" and len(points) >= 2:
            a, b = points[0]["p"], points[1]["p"]
            for ratio in (0.382, 0.5, 0.618):
                add(f"Fibonacci {ratio} level you drew", b + (a - b) * ratio, "rupees")
    return facts


def _parse(reply: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
    text = fenced.group(1) if fenced else reply[reply.find("{") : reply.rfind("}") + 1] if "{" in reply else ""
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("not an object")
    return parsed


def _ungrounded(text: str, facts: Sequence[Fact]) -> list[str]:
    """The numbers in `text` that are not a fact's value (to the digits cited, a negative one by its magnitude) or a number written in a fact's name or window (a date, a length).
    Unlike the analysts' check a percentage is not matched to a fraction a hundred times smaller: on a sheet this wide that would let nearly any number through."""
    allowed: list[float] = [abs(float(f.value)) for f in facts if isinstance(f.value, (int, float))]
    for f in facts:
        allowed += [float(x) for x in re.findall(r"\d+(?:\.\d+)?", f"{f.name} {f.window}")]
    bad: list[str] = []
    for token in numbers_in_text(text):
        bare = token.replace(",", "").rstrip("%").lstrip("+-")
        if bare.isdigit() and int(bare) <= 10:
            continue
        digits = len(bare.split(".")[1]) if "." in bare else 0
        tolerance = 0.5 * 10 ** (-digits) + 1e-9
        if not any(abs(float(bare) - v) <= tolerance for v in allowed):
            bad.append(token)
    return bad


def check_reply(parsed: Mapping[str, Any], facts: Sequence[Fact]) -> tuple[str | None, list[str]]:
    """The reading and the facts it cites when the reply is acceptable, else None and the reasons it is not."""
    read, cited = parsed.get("read"), parsed.get("cited")
    errors: list[str] = []
    if not isinstance(read, str) or not read.strip():
        return None, ["the reply has no reading"]
    known = {f.id for f in facts}
    in_text = set(re.findall(r"\[(F\d+)\]", read))
    named = set(cited) if isinstance(cited, list) else set()
    missing = sorted((in_text | named) - known)
    if missing:
        errors.append(f"it cites facts that are not on the sheet: {missing}")
    if not in_text:
        errors.append("it cites no fact in the text as [F#]")
    ungrounded = _ungrounded(re.sub(r"\[F\d+\]", "", read), facts)
    if ungrounded:
        errors.append(f"figures not on the sheet: {ungrounded}")
    return (read.strip() if not errors else None), errors


def explain(request: Mapping[str, Any], llm: LLMClient | None, attempts: int = 2) -> dict[str, Any]:
    """The facts sheet and, when a model is given and its reply passes, the reading."""
    facts = build_facts(request)
    out: dict[str, Any] = {"symbol": str(request.get("symbol", "")), "interval": str(request.get("interval", "")), "facts": [f.as_dict() for f in facts], "read": None, "cited": [], "model": None, "notes": []}
    if not facts:
        out["notes"].append(f"too few bars to say anything: at least {MIN_BARS} are needed")
        return out
    if llm is None:
        out["notes"].append("no model is available: the facts sheet is the answer")
        return out
    user = "Facts sheet:\n" + "\n".join(f"[{f.id}] {f.name}: {f.value}{(' ' + f.unit) if f.unit else ''}{(' (' + f.window + ')') if f.window else ''}" for f in facts)
    feedback, errors = "", []
    for _ in range(attempts):
        try:
            parsed = _parse(llm.complete(SYSTEM, user + feedback))
        except Exception as exc:  # a model that errors or answers in the wrong shape is tried once more, then left out
            errors = [f"the reply could not be read ({type(exc).__name__})"]
            feedback = "\n\nYour previous reply could not be read as the JSON object asked for. Reply again with only that object."
            continue
        read, errors = check_reply(parsed, facts)
        if read is not None:
            out["read"] = read
            out["cited"] = sorted(set(re.findall(r"\[(F\d+)\]", read)), key=lambda x: int(x[1:]))
            out["model"] = ", ".join(getattr(llm, "names", [])) or "a model"
            return out
        feedback = "\n\nYour previous reply was rejected: " + "; ".join(errors) + ". Reply again, fixing this."
    out["notes"].append("the model's reading did not pass the checks (" + "; ".join(errors) + "): the facts sheet is the answer")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise ValueError("not an object")
    except ValueError:
        print(json.dumps({"error": "the request is not a JSON object"}))
        return 0
    llm: LLMClient | None = None
    try:
        if os.environ.get("ATHENA_EXPLAIN_MODEL", "").lower() == "off":  # the end-to-end tests: no model call, the facts sheet only
            raise RuntimeError("model off")
        from athena.llm.budget import Meter
        from athena.llm.cache import ReplyCache
        from athena.llm.router import build_router

        llm = build_router(meter=Meter(), cache=ReplyCache()).client_for("specialist")
    except Exception:  # no keys, or the monthly ceiling: the facts sheet still answers
        llm = None
    sys.stdout.write(json.dumps(explain(request, llm), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
