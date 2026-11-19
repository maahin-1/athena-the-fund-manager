"""Typed factual claims: what a model may say about a figure, checked against the figure itself (audit A1, WP05).

The older check (`athena.evaluation.grounding`) asks only whether a number written in the prose appears *somewhere* in the data. It binds the number to no metric, period, entity or sign, so a
reversed sign or a figure borrowed from another metric passes. It is a numeric consistency check and is named that way wherever its result is shown.

Here a factual statement is a *claim*: the fact it rests on (a metric of a packet, or a fact id on the council's sheet), the entity, the metric, the period, the signed value and the unit. Code
checks every part against the fact, allows one unit conversion only (a fraction shown as a percent), and renders the sentence itself. The model's own words stay apart, as an interpretation that
carries no figures of its own. A fact is a figure of the data; that a claim matches it says nothing about whether the figure supports the model's conclusion, which only a person can judge
(`athena.evaluation` has no judge that does). Claims verified here are `bound to a fact`, never `true of the company`."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from athena.evaluation.grounding import numbers_in_text

SCHEME = "typed_claims/1"
LEGACY_SCHEME = "numeric_consistency/1"
# the only conversion code applies, from the unit a fact is stored in to the unit a statement may show: a fraction may be shown as a percent. A ratio, an index, a price or a count never converts.
CONVERSIONS: dict[tuple[str, str], float] = {("fraction", "percent"): 100.0}
CLAIM_KEYS = frozenset({"fact", "entity", "metric", "period", "value", "unit"})
MAX_CLAIMS = 12
MAX_SHOWN = 80
_CERTAINTY = re.compile(
    r"\b(?:certain(?:ly)?\s+to|guarantee[ds]?|guaranteeing|definitely|surely|sure\s+to|cannot\s+fail|can't\s+fail|no\s+risk|risk[- ]free|will\s+(?:certainly|undoubtedly|inevitably))\b", re.IGNORECASE
)


def _norm(text: Any) -> str | None:
    """A period, entity or unit as compared: trimmed, one space between words, case ignored; None when empty."""
    if text is None:
        return None
    flat = " ".join(str(text).split()).casefold()
    return flat or None


def _clean(text: Any) -> str:
    """Source text as it may appear in a sentence code renders: no markup characters or controls, one line, cut to a size a sentence can carry."""
    flat = " ".join(re.sub(r"[<>\x00-\x1f]", " ", str(text)).split())
    return flat if len(flat) <= MAX_SHOWN else flat[: MAX_SHOWN - 1] + "…"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def decimals(value: float) -> int:
    """Digits after the point in a number as written (an integer has none): the precision a figure was stated to."""
    if isinstance(value, int):
        return 0
    exponent = Decimal(repr(float(value))).as_tuple().exponent
    return max(0, -int(exponent)) if isinstance(exponent, int) else 0


@dataclass(frozen=True)
class Fact:
    """One figure of the data as code holds it. `id` is what a claim cites: the metric name on a packet, the fact id (F3) on the council's sheet."""

    id: str
    entity: str | None
    name: str
    period: str | None
    value: float
    unit: str | None
    source: str | None

    @property
    def fingerprint(self) -> str:
        """A stable reference to exactly this figure: change any part of it and the reference changes."""
        blob = json.dumps([self.entity, self.name, self.period, self.value, self.unit, self.source], ensure_ascii=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def facts_from_packet(packet: Mapping[str, Any]) -> dict[str, Fact]:
    """The facts of a metrics packet by id (the metric's name). A metric with no value is not a fact: there is nothing to claim."""
    entity = packet.get("instrument") or packet.get("symbol")
    out: dict[str, Fact] = {}
    for name, metric in (packet.get("metrics") or {}).items():
        if isinstance(metric, Mapping) and _number(metric.get("value")):
            out[str(name)] = Fact(str(name), None if entity is None else str(entity), str(name), metric.get("window"), metric["value"], metric.get("unit"), metric.get("source"))
    return out


def facts_from_sheet(sheet: Mapping[str, Any]) -> dict[str, Fact]:
    """The facts of the council's evidence sheet by fact id (T1, F3, N2)."""
    entity = sheet.get("symbol")
    return {
        str(f["id"]): Fact(str(f["id"]), None if entity is None else str(entity), str(f["name"]), f.get("window"), f["value"], f.get("unit"), f.get("source"))
        for f in sheet.get("facts", []) if isinstance(f, Mapping) and _number(f.get("value"))
    }


@dataclass(frozen=True)
class VerifiedClaim:
    fact: Fact
    value: float
    unit: str | None
    statement: str

    def to_dict(self) -> dict[str, Any]:
        f = self.fact
        return {"fact": f.id, "entity": f.entity, "metric": f.name, "period": f.period, "value": self.value, "unit": self.unit, "statement": self.statement, "source": f.source, "fingerprint": f.fingerprint}


def shown_number(value: float, places: int) -> str:
    return f"{value:.{places}f}"


def render(fact: Fact, value: float, unit: str | None, places: int) -> str:
    """The sentence for a verified claim, written by code: entity, metric, period, signed value with its unit, and the fact it is bound to."""
    figure = shown_number(value, places)
    figure = f"{figure}%" if unit == "percent" else f"{figure} {unit}" if unit else figure
    where = f" ({_clean(fact.period)})" if fact.period else ""
    return f"{_clean(fact.entity) + ' ' if fact.entity else ''}{_clean(fact.name)}{where} = {figure} [{_clean(fact.id)}]"


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _one(raw: Any, facts: Mapping[str, Fact], at: str) -> tuple[VerifiedClaim | None, list[str]]:
    if not isinstance(raw, Mapping):
        return None, [f"{at}: a claim must be an object"]
    keys = set(raw)
    if keys != CLAIM_KEYS:
        return None, [f"{at}: a claim has exactly the keys {sorted(CLAIM_KEYS)} (missing {sorted(CLAIM_KEYS - keys)}, not allowed {sorted(keys - CLAIM_KEYS)})"]
    ident, metric, value = raw["fact"], raw["metric"], raw["value"]
    if not isinstance(ident, str) or not isinstance(metric, str):
        return None, [f"{at}: fact and metric must be text"]
    if not _number(value):
        return None, [f"{at}: value must be a finite number"]
    fact = facts.get(ident)
    if fact is None:
        return None, [f"{at}: {ident!r} is not a fact on the data (a metric with no value is not one)"]
    problems: list[str] = []
    if _norm(metric) != _norm(fact.name):
        problems.append(f"{at}: metric {metric!r} is not the metric of {fact.id} ({fact.name})")
    if fact.entity is not None and _norm(raw["entity"]) != _norm(fact.entity):
        problems.append(f"{at}: entity {raw['entity']!r} is not the entity of {fact.id} ({fact.entity})")
    if _norm(raw["period"]) != _norm(fact.period):
        problems.append(f"{at}: period {raw['period']!r} is not the period of {fact.id}" + (f" ({_clean(fact.period)})" if fact.period else " (the source states none)"))
    unit_claimed, unit_fact = _norm(raw["unit"]), _norm(fact.unit)
    factor = 1.0
    if unit_claimed != unit_fact:
        factor = CONVERSIONS.get((unit_fact or "", unit_claimed or ""), 0.0)
        if not factor:
            problems.append(f"{at}: unit {raw['unit']!r} is not the unit of {fact.id} ({fact.unit}); only a fraction may be shown as a percent")
    if factor:
        expected = fact.value * factor
        if value != 0 and expected != 0 and _sign(value) != _sign(expected):
            problems.append(f"{at}: sign reversed: {fact.id} is {expected:g} {unit_claimed or ''}".rstrip() + f" and the claim says {value:g}")
        elif abs(value - expected) > 0.5 * 10 ** (-decimals(value)) + 1e-9:
            problems.append(f"{at}: value {value:g} is not the value of {fact.id} ({expected:g}) to the digits shown")
    if problems:
        return None, problems
    return VerifiedClaim(fact, float(value), unit_claimed, render(fact, float(value), unit_claimed, decimals(value))), []


def verify_claims(raw: Any, facts: Mapping[str, Fact]) -> tuple[list[VerifiedClaim], list[str]]:
    """The claims in `raw` that match their facts and the reasons any do not. Any problem makes the whole list unverified: (the verified claims, empty when anything failed; the reasons).
    Nothing here raises on a malformed payload: a model's reply can hold anything."""
    if not isinstance(raw, list):
        return [], [f"claims must be a list, not {type(raw).__name__}"]
    if len(raw) > MAX_CLAIMS:
        return [], [f"claims lists more than {MAX_CLAIMS} claims"]
    verified: list[VerifiedClaim] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        claim, problems = _one(item, facts, f"claim {i + 1}")
        errors += problems
        if claim is not None:
            if claim.fact.id in seen:
                errors.append(f"claim {i + 1}: {claim.fact.id} is claimed twice")
            seen.add(claim.fact.id)
            verified.append(claim)
    return ([], errors) if errors else (verified, [])


def bound_figure_errors(text: str, facts: Mapping[str, Fact], cited: Any) -> list[str]:
    """Why figures written in free `text` are not bound to the facts it cites (empty when they are), for prose that cites fact ids instead of making typed claims (the council).

    A figure that is the value of some fact on the sheet must be the value of a fact the text cites (a fraction may be read as a percent; nothing else is scaled), and a figure written with a sign must
    agree with the sign of a cited fact that has that value. A figure that matches no fact (the blend's conviction, a count) is left to the sheet-wide check; a plain integer up to ten is ignored, as it
    is there. This binds a figure to an id; it does not show the cited fact supports the conclusion drawn from it."""
    ids = set(cited)
    errors: list[str] = []
    for token in numbers_in_text(text):
        bare = token.replace(",", "").rstrip("%")
        digits = bare.lstrip("+-")
        if digits.isdigit() and int(digits) <= 10:
            continue
        magnitude, tolerance = abs(float(bare)), 0.5 * 10 ** (-decimals(float(digits)) if "." in digits else 0) + 1e-9
        matching = [f for f in facts.values() if any(abs(abs(f.value * k) - magnitude) <= tolerance for k in ((1.0, 100.0) if _norm(f.unit) == "fraction" else (1.0,)))]
        if not matching:
            continue
        held = [f for f in matching if f.id in ids]
        if not held:
            errors.append(f"the figure {token} is the value of {matching[0].id} ({matching[0].name}), which this reply does not cite: cite the fact a figure comes from")
            continue
        explicit = bare[0] if bare[0] in "+-" else ""
        if explicit and not any(_sign(f.value) == (1 if explicit == "+" else -1) for f in held):
            errors.append(f"the figure {token} is written with a sign that {held[0].id} ({held[0].name} = {held[0].value:g}) does not have")
    return errors


def interpretation_errors(text: Any) -> list[str]:
    """Why `text` may not stand as a model's interpretation (empty when it may): it is non-empty, carries no figures of its own (a figure is a claim, stated through the claims and rendered by code),
    and promises no outcome. Both rules are surface checks; they say nothing about whether the words are true, which is why the interpretation stays labelled as the model's own."""
    if not isinstance(text, str) or not text.strip():
        return ["the interpretation must be non-empty text"]
    errors: list[str] = []
    tokens = numbers_in_text(text)
    if tokens:
        errors.append(f"the interpretation carries figures {tokens}: state a figure as a claim, and code will render it")
    if _CERTAINTY.search(text):
        errors.append("the interpretation promises an outcome (certain to, guaranteed, definitely, risk-free): no data in this system supports a certainty")
    return errors
