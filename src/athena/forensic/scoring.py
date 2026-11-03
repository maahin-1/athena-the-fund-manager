"""Which flags stand, and the concern level. Both are rules in code; no model's wording can change them.

A flag is *established* when code proves its stated contradiction, or when a second lane has independently supported it with verified evidence (an observation or answer that names the flag in
`supports`). Otherwise it is *single-source*, shown last and not counted. Two flags citing the same evidence are one flag. The concern level adds the weight of established flags and is shown as a range:
the low end counts only established flags, the high end also counts half the weight of the single-source ones, so uncertainty is visible and not folded in.

The weights and the bands are provisional; step 3 of the build calibrates them on the test set of past failures and on healthy controls."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from athena.forensic.model import (
    ANOMALY_VS_PEERS, ANSWER, ARITHMETIC, CLAIM_VS_OUTSIDE, DOC_VS_DOC, DUPLICATE, ESTABLISHED, FLAG, OBSERVATION, REJECTED, SINGLE_SOURCE, TEXT_VS_DATA, Entry,
)
from athena.forensic.store import ForensicStore
from athena.forensic.verify import Lookup, evaluate_contradiction

WEIGHTS = {ARITHMETIC: 3.0, DOC_VS_DOC: 2.0, TEXT_VS_DATA: 2.0, CLAIM_VS_OUTSIDE: 2.0, ANOMALY_VS_PEERS: 1.0}
SINGLE_SOURCE_SHARE = 0.5
BANDS = ((0.0, "none established"), (2.0, "low"), (5.0, "moderate"), (9.0, "elevated"))  # score above the last bound is "high"
LEVELS = [name for _, name in BANDS] + ["high"]


def level(score: float) -> str:
    if score <= 0:
        return BANDS[0][1]
    for bound, name in BANDS[1:]:
        if score <= bound:
            return name
    return "high"


def classify(store: ForensicStore, case_id: str, lookup: Lookup) -> None:
    """Set the status of every kept flag: established, single_source or duplicate."""
    entries = store.entries(case_id)
    flags = [e for e in entries if e.kind == FLAG and e.status != REJECTED]
    seen: dict[tuple[str, frozenset[str]], str] = {}
    for flag in flags:
        key = (str(flag.body.get("flag_type")), frozenset(flag.evidence))
        if key in seen:
            store.set_status(case_id, flag.id, DUPLICATE, f"same evidence as {seen[key]}")
            continue
        seen[key] = flag.id
        proven = evaluate_contradiction(flag.body.get("contradiction"), lookup) is True
        supporters = {e.author for e in entries if e.kind in (OBSERVATION, ANSWER) and e.status != REJECTED and e.body.get("supports") == flag.id and e.author != flag.author}
        if proven:
            store.set_status(case_id, flag.id, ESTABLISHED, "the stated relation between the cited numbers does not hold")
        elif supporters:
            store.set_status(case_id, flag.id, ESTABLISHED, f"independently supported by {', '.join(sorted(supporters))}")
        else:
            store.set_status(case_id, flag.id, SINGLE_SOURCE, "one source only")


def concern(flags: Sequence[Entry]) -> dict[str, Any]:
    """The concern level as a range, from the flags' final statuses."""
    established = sum(WEIGHTS.get(str(f.body.get("flag_type")), 1.0) for f in flags if f.status == ESTABLISHED)
    single = sum(WEIGHTS.get(str(f.body.get("flag_type")), 1.0) for f in flags if f.status == SINGLE_SOURCE)
    low, high = established, established + SINGLE_SOURCE_SHARE * single
    return {"low": level(low), "high": level(high), "score_low": round(low, 2), "score_high": round(high, 2), "established": sum(1 for f in flags if f.status == ESTABLISHED), "single_source": sum(1 for f in flags if f.status == SINGLE_SOURCE)}
