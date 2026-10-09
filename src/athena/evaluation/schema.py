from __future__ import annotations

from typing import Any

from athena.contracts import Coverage

SIGNALS = ("bullish", "bearish", "neutral")
VERDICTS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
RESOLUTION_PATHS = ("debate", "blend")
_SPECIALIST_KEYS = {"signal", "confidence", "reasoning", "data_coverage", "missing"}
_VERDICT_KEYS = {
    "verdict", "conviction", "key_risks", "resolution_path", "debate_transcript_ref", "panel_agreement", "contested",
    "pre_overlay_verdict", "risk_findings",
}
_VERDICT_REQUIRED = {"verdict", "conviction", "key_risks", "resolution_path"}
HELD_BACK_VERDICTS = ("Buy", "Overweight")  # the only calls a risk limit can hold back, always to Hold
FINDING_STATUSES = ("ok", "warn", "breach", "unchecked")
_FINDING_KEYS = {"check", "status", "message"}


def _is_int_in_range(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_specialist_output(output: Any) -> list[str]:
    """Errors against the specialist output contract (TRD section 3); an empty list means valid."""
    if not isinstance(output, dict):
        return ["output must be an object"]
    errors: list[str] = []
    missing_keys = sorted(_SPECIALIST_KEYS - output.keys())
    if missing_keys:
        errors.append(f"missing keys: {missing_keys}")
    unknown_keys = sorted(output.keys() - _SPECIALIST_KEYS)
    if unknown_keys:
        errors.append(f"unknown keys: {unknown_keys}")
    if "signal" in output and output["signal"] not in SIGNALS:
        errors.append(f"signal must be one of {list(SIGNALS)}, got {output['signal']!r}")
    if "confidence" in output and not _is_int_in_range(output["confidence"], 0, 100):
        errors.append(f"confidence must be an integer 0-100, got {output['confidence']!r}")
    if "reasoning" in output and not (isinstance(output["reasoning"], str) and output["reasoning"].strip()):
        errors.append("reasoning must be a non-empty string")
    coverage_values = [c.value for c in Coverage]
    if "data_coverage" in output and output["data_coverage"] not in coverage_values:
        errors.append(f"data_coverage must be one of {coverage_values}, got {output['data_coverage']!r}")
    if "missing" in output and not _is_str_list(output["missing"]):
        errors.append("missing must be a list of strings")
    if errors:
        return errors

    coverage, missing = output["data_coverage"], output["missing"]
    if coverage == Coverage.FULL.value and missing:
        errors.append("data_coverage 'full' must have an empty missing list")
    if coverage != Coverage.FULL.value and not missing:
        errors.append(f"data_coverage {coverage!r} must list what is missing")
    if coverage == Coverage.INSUFFICIENT.value and (output["signal"] != "neutral" or output["confidence"] != 0):
        errors.append("data_coverage 'insufficient' requires an abstention: signal 'neutral', confidence 0")
    return errors


def validate_judge_verdict(verdict: Any) -> list[str]:
    """Errors against the judge verdict contract (TRD section 3); an empty list means valid."""
    if not isinstance(verdict, dict):
        return ["verdict must be an object"]
    errors: list[str] = []
    missing_keys = sorted(_VERDICT_REQUIRED - verdict.keys())
    if missing_keys:
        errors.append(f"missing keys: {missing_keys}")
    unknown_keys = sorted(verdict.keys() - _VERDICT_KEYS)
    if unknown_keys:
        errors.append(f"unknown keys: {unknown_keys}")
    if "verdict" in verdict and verdict["verdict"] not in VERDICTS:
        errors.append(f"verdict must be one of {list(VERDICTS)}, got {verdict['verdict']!r}")
    if "conviction" in verdict and not _is_int_in_range(verdict["conviction"], 0, 100):
        errors.append(f"conviction must be an integer 0-100, got {verdict['conviction']!r}")
    if "key_risks" in verdict and not _is_str_list(verdict["key_risks"]):
        errors.append("key_risks must be a list of strings")
    if "resolution_path" in verdict and verdict["resolution_path"] not in RESOLUTION_PATHS:
        errors.append(f"resolution_path must be one of {list(RESOLUTION_PATHS)}, got {verdict['resolution_path']!r}")
    if verdict.get("resolution_path") == "debate" and not verdict.get("debate_transcript_ref"):
        errors.append("a debate verdict needs a debate_transcript_ref")
    if "panel_agreement" in verdict:
        agreement = verdict["panel_agreement"]
        if isinstance(agreement, bool) or not isinstance(agreement, (int, float)) or not 0 <= agreement <= 1:
            errors.append("panel_agreement must be a number between 0 and 1")
    if "contested" in verdict and not isinstance(verdict["contested"], bool):
        errors.append("contested must be a boolean")
    if "pre_overlay_verdict" in verdict:
        held = verdict["pre_overlay_verdict"]
        if held not in HELD_BACK_VERDICTS:
            errors.append(f"pre_overlay_verdict must be Buy or Overweight, got {held!r}")
        elif verdict.get("verdict") != "Hold":
            errors.append(f"a verdict held back from {held} must be Hold, got {verdict.get('verdict')!r}")
    if "risk_findings" in verdict:
        errors += _finding_errors(verdict["risk_findings"])
    return errors


def _finding_errors(findings: Any) -> list[str]:
    if not (isinstance(findings, list) and all(isinstance(item, dict) for item in findings)):
        return ["risk_findings must be a list of objects"]
    errors: list[str] = []
    for i, item in enumerate(findings):
        missing_keys = sorted(_FINDING_KEYS - item.keys())
        if missing_keys:
            errors.append(f"risk_findings[{i}] missing keys: {missing_keys}")
        if "status" in item and item["status"] not in FINDING_STATUSES:
            errors.append(f"risk_findings[{i}].status must be one of {list(FINDING_STATUSES)}, got {item['status']!r}")
    return errors
