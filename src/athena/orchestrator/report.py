from __future__ import annotations

from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, NO_VIEW, OrchestrationResult

DISCLAIMER = "A stylized analytical framework, not financial advice; not a registered investment adviser."


def format_result(result: OrchestrationResult) -> str:
    """Plain-text summary of an orchestration result."""
    if result.status == NEEDS_CLARIFICATION and result.ambiguity:
        lines = [f"{result.query!r} could be more than one instrument ({result.ambiguity.reason}):"]
        lines += [
            f"  {i}. {c.identifier}  {c.name}  ({c.asset_class}, match {c.score:.2f})"
            for i, c in enumerate(result.ambiguity.candidates, 1)
        ]
        lines.append("Re-run with the exact symbol.")
        return "\n".join(lines)

    resolution = result.resolution
    assert resolution is not None and result.verdict is not None
    verdict = result.verdict
    lines = [
        f"{resolution.identifier}  {resolution.name}  ({resolution.asset_class}, {resolution.resolution_path} match)",
        f"Verdict: {verdict['verdict']}  conviction {verdict['conviction']}  ({verdict['resolution_path']})"
        + ("  [no specialist had enough data]" if result.status == NO_VIEW else ""),
    ]
    if result.specialists:
        lines.append("Specialists:")
        for name, out in result.specialists.items():
            lines.append(f"  {name}: {out['signal']} {out['confidence']}  coverage {out['data_coverage']}")
            lines.append(f"    {out['reasoning']}")
    if result.skipped:
        lines.append("Not run: " + "; ".join(f"{name} ({why})" for name, why in result.skipped.items()))
    if verdict["key_risks"]:
        lines.append("Key risks:")
        lines += [f"  - {risk}" for risk in verdict["key_risks"]]
    for note in result.notes:
        lines.append(f"Note: {note}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
