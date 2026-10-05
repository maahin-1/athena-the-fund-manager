from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from athena.contracts import AthenaError, Coverage
from athena.coverage import derive_coverage
from athena.evaluation.grounding import check_grounded
from athena.evaluation.schema import validate_specialist_output

MODEL_KEYS = ("signal", "confidence", "reasoning")
OUTPUT_INSTRUCTIONS = (
    'Reply with one JSON object and nothing else: {"signal": "bullish"|"bearish"|"neutral", '
    '"confidence": 0-100, "reasoning": "2-4 sentences citing specific figures from the data"}. '
    "Cite only figures that appear in the data. Every figure carries a unit: a \"fraction\" is a decimal share (0.25 means 25 percent, so a return on equity of 0.487 is a very high 48.7 percent), \"percentage points\" are already in percent, and a \"ratio\" is a plain multiple."
)


class LLMClient(Protocol):
    """Anything that turns a system prompt and a user message into text. Provider choice lives behind this."""

    def complete(self, system: str, user: str) -> str: ...


class SpecialistError(AthenaError):
    """The model never produced an output that passed validation (fail-loud mode)."""


@dataclass(frozen=True)
class SpecialistSpec:
    """A specialist is only data: a name, a persona prompt, and the metrics it must / may have."""

    name: str
    persona: str
    critical: tuple[str, ...]
    optional: tuple[str, ...] = ()
    shows: tuple[str, ...] | None = None  # the only figures the model is shown; None means the whole packet


def abstention(missing: Sequence[str], reason: str) -> dict[str, Any]:
    return {
        "signal": "neutral",
        "confidence": 0,
        "reasoning": reason,
        "data_coverage": Coverage.INSUFFICIENT.value,
        "missing": list(missing),
    }


def parse_model_json(text: str) -> dict[str, Any]:
    """The first JSON object in `text`, tolerating a markdown fence or a sentence around it."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1] if "{" in text else ""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"no JSON object found in the reply ({exc.msg})") from exc
    if not isinstance(parsed, dict):
        raise ValueError("the reply is not a JSON object")
    return parsed


class Specialist:
    """Shared plumbing for every specialist (TRD 2.2): coverage from declared inputs, abstention
    without calling the model, JSON parsing, contract validation, number grounding, retry with
    feedback, response caching, and fail-loud / fail-quiet handling."""

    def __init__(self, spec: SpecialistSpec, llm: LLMClient, fail_quiet: bool = False, max_attempts: int = 2):
        self.spec = spec
        self.llm = llm
        self.fail_quiet = fail_quiet
        self.max_attempts = max_attempts
        self._cache: dict[str, dict[str, Any]] = {}
        self.model_calls = 0

    def analyze(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        not_applicable = set(packet.get("not_applicable", ()))  # meaningless for this instrument, so not a data gap
        critical = tuple(name for name in self.spec.critical if name not in not_applicable)
        optional = tuple(name for name in self.spec.optional if name not in not_applicable)
        coverage, missing = derive_coverage(critical, optional, packet.get("metrics", {}))
        if coverage is Coverage.INSUFFICIENT:
            critical_gaps = [name for name in missing if name in critical]
            return abstention(missing, f"No view formed: required inputs are missing ({', '.join(critical_gaps)}).")
        shown = self._visible(packet)

        system = f"{self.spec.persona}\n\n{OUTPUT_INSTRUCTIONS}"
        user = self._render(shown, coverage, missing)
        key = hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()
        if key in self._cache:
            return json.loads(json.dumps(self._cache[key]))

        feedback = ""
        errors: list[str] = []
        for _ in range(self.max_attempts):
            self.model_calls += 1
            reply = self.llm.complete(system, user + feedback)
            output, errors = self._check(reply, shown, coverage, missing)
            if not errors:
                self._cache[key] = output
                return json.loads(json.dumps(output))
            feedback = "\n\nYour previous reply was rejected: " + "; ".join(errors) + ". Reply again, fixing this."

        if self.fail_quiet:
            return abstention(["valid_model_output"], "No view formed: the model output failed validation.")
        raise SpecialistError(f"{self.spec.name}: no valid output after {self.max_attempts} attempts: {errors}")

    def _visible(self, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        """The packet as the model sees it. A specialist that declares `shows` gets only those figures, and the
        number-grounding check runs against the same view, so it cannot cite a figure it was never shown."""
        if self.spec.shows is None:
            return packet
        names = set(self.spec.shows)
        listed = ("metrics", "missing", "missing_reasons", "groups", "not_applicable")
        view = {key: value for key, value in packet.items() if key not in listed}
        view["metrics"] = {name: metric for name, metric in packet.get("metrics", {}).items() if name in names}
        view["missing"] = [name for name in packet.get("missing", []) if name in names]
        view["missing_reasons"] = {n: why for n, why in packet.get("missing_reasons", {}).items() if n in names}
        view["not_applicable"] = [name for name in packet.get("not_applicable", []) if name in names]
        return view

    def _render(self, packet: Mapping[str, Any], coverage: Coverage, missing: Sequence[str]) -> str:
        lines = [f"Specialist: {self.spec.name}", "Data (metrics packet):", json.dumps(packet, indent=2, sort_keys=True)]
        if packet.get("not_applicable"):
            lines.append(
                f"Not applicable to this instrument (this is not a data gap; do not call it missing): "
                f"{', '.join(packet['not_applicable'])}."
            )
        if coverage is Coverage.PARTIAL:
            lines.append(
                f"Data coverage is PARTIAL. Missing: {', '.join(missing)}. "
                "Say which of your conclusions this gap affects, and qualify them."
            )
        return "\n".join(lines)

    def _check(
        self, reply: str, packet: Mapping[str, Any], coverage: Coverage, missing: Sequence[str]
    ) -> tuple[dict[str, Any], list[str]]:
        try:
            parsed = parse_model_json(reply)
        except ValueError as exc:
            return {}, [str(exc)]
        extra = sorted(parsed.keys() - set(MODEL_KEYS))
        if extra:
            return {}, [f"reply has keys the contract does not allow: {extra}"]
        output = {**parsed, "data_coverage": coverage.value, "missing": list(missing)}
        errors = validate_specialist_output(output)
        if errors:
            return {}, errors
        grounding = check_grounded(output["reasoning"], packet)
        if not grounding.ok:
            return {}, [f"figures not found in the data: {list(grounding.ungrounded)}"]
        return output, []
