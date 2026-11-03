"""The gate every ledger entry passes before it is kept. A model proposes; code checks.

- Every evidence id cited must exist in this case.
- A quote must be found, in letters and digits, in a cited result (PDF text carries stray spaces and curly quotes, a model's copy does not).
- A number written under `values` must be in a cited result, to the digits printed.
- A flag needs at least two references, and a note of the innocent explanations it considered.
- A contradiction can be stated as a relation between numbers taken from evidence; code evaluates it, and only code can say it is proven (`proven`).

An entry that fails is kept with status `rejected` and the reason, so the report can say how often an agent cited something that was not there."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from athena.forensic.model import ANSWER, FLAG, FLAG_TYPES, KINDS, NOTE, OBSERVATION, QUESTION

Lookup = Callable[[str], "dict[str, Any] | None"]  # evidence id -> the stored row (lane, tool, args, result)
MIN_FLAG_REFERENCES = 2
REL_TOL = 1e-6


def squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def _all_numbers(data: Any) -> list[float]:
    out: list[float] = []
    if isinstance(data, bool):
        return out
    if isinstance(data, (int, float)):
        out.append(float(data))
    elif isinstance(data, str):
        out.extend(float(m.replace(",", "")) for m in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", data))
    elif isinstance(data, Mapping):
        for v in data.values():
            out.extend(_all_numbers(v))
    elif isinstance(data, (list, tuple)):
        for v in data:
            out.extend(_all_numbers(v))
    return out


def number_in(value: float, numbers: list[float]) -> bool:
    return any(abs(value - n) <= max(0.005, REL_TOL * abs(n)) or abs(abs(value) - abs(n)) <= 0.005 for n in numbers)


def pick(data: Any, path: str) -> float | None:
    """The number at a dotted path ('fresh_issue.crore', 'rows.2.amount') in a result, or None."""
    node = data
    for part in path.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        elif isinstance(node, (list, tuple)) and part.lstrip("-").isdigit() and -len(node) <= int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return float(node) if isinstance(node, (int, float)) and not isinstance(node, bool) else None


def _side(spec: Any, lookup: Lookup) -> float | None:
    """One side of a relation: a reference {"ev": "E1", "path": "a.b"} or a list of them, summed."""
    refs = spec if isinstance(spec, list) else [spec]
    total = 0.0
    for ref in refs:
        row = lookup(str(ref.get("ev", ""))) if isinstance(ref, Mapping) else None
        value = pick(row["result"], str(ref.get("path", ""))) if row else None
        if value is None:
            return None
        total += value
    return total


def evaluate_contradiction(spec: Any, lookup: Lookup) -> bool | None:
    """True when a stated relation between two numbers from evidence is violated (proven by code), False when it holds, None when a number cannot be found.

    {"relation": "equal"|"at_most"|"at_least", "left": ref or [refs], "right": ref or [refs], "tol": relative tolerance}"""
    if not isinstance(spec, Mapping):
        return None
    left, right = _side(spec.get("left"), lookup), _side(spec.get("right"), lookup)
    if left is None or right is None:
        return None
    tol = float(spec.get("tol", 0.005))
    slack = tol * max(abs(left), abs(right), 1e-9)
    relation = spec.get("relation")
    if relation == "equal":
        return abs(left - right) > slack
    if relation == "at_most":
        return left > right + slack
    if relation == "at_least":
        return left < right - slack
    return None


def contradiction_paths(spec: Any) -> set[tuple[str, str]]:
    """The distinct (evidence, path) references a stated contradiction rests on."""
    if not isinstance(spec, Mapping):
        return set()
    out: set[tuple[str, str]] = set()
    for side in (spec.get("left"), spec.get("right")):
        for ref in side if isinstance(side, list) else [side]:
            if isinstance(ref, Mapping):
                out.add((str(ref.get("ev", "")), str(ref.get("path", ""))))
    return out


def verify_entry(kind: str, body: Mapping[str, Any], lookup: Lookup) -> str | None:
    """None when the entry may be kept, else the reason it may not."""
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    text = str(body.get("text", "")).strip()
    if not text:
        return "an entry needs text"
    if kind == NOTE:
        return None
    refs = [str(e) for e in body.get("evidence") or []]
    rows = {ref: lookup(ref) for ref in refs}
    missing = [ref for ref, row in rows.items() if row is None]
    if missing:
        return f"evidence {', '.join(missing)} does not exist in this case"
    held = [row for row in rows.values() if row is not None]
    quote = str(body.get("quote", "")).strip()
    if quote:
        want = squash(quote)
        if not want or not any(want in squash(json.dumps(row["result"], ensure_ascii=False, default=str)) for row in held):
            return "the quote is not in any cited evidence"
    values = body.get("values") or {}
    if values:
        numbers = [n for row in held for n in _all_numbers(row["result"])]
        for name, value in values.items():
            try:
                v = float(value)
            except (TypeError, ValueError):
                return f"value {name!r} is not a number"
            if not number_in(v, numbers):
                return f"value {name}={value} is not in any cited evidence"
    if kind == OBSERVATION and not refs:
        return "an observation must cite evidence"
    if kind == ANSWER and not body.get("answers"):
        return "an answer must say which question it answers"
    if kind == ANSWER and not refs and not body.get("cannot_determine"):
        return "an answer must cite evidence or say it cannot determine"
    if kind == QUESTION and not str(body.get("to", "")).strip():
        return "a question must say which lane it is for"
    if kind == FLAG:
        if body.get("flag_type") not in FLAG_TYPES:
            return f"flag_type must be one of {', '.join(FLAG_TYPES)}"
        stated = body.get("contradiction")
        paths = contradiction_paths(stated)
        if len(set(refs)) < MIN_FLAG_REFERENCES and len(paths) < MIN_FLAG_REFERENCES:
            return f"a flag must cite at least {MIN_FLAG_REFERENCES} pieces of evidence, or state a contradiction between two numbers"
        if stated is not None and evaluate_contradiction(stated, lookup) is None:
            gone = next((f"{ev}:{path}" for ev, path in sorted(paths) if (row := lookup(ev)) is None or pick(row["result"], path) is None), "a reference")
            return f"the contradiction names a number that is not in the evidence it cites ({gone}); a path is dotted keys and list positions, like objects.0.crore"
        if not [x for x in body.get("innocent") or [] if str(x).strip()]:
            return "a flag must list the innocent explanations considered"
    return None
