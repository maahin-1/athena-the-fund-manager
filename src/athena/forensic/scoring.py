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
from athena.forensic.verify import Lookup, contradiction_paths, evaluate_contradiction

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


CODE_AUTHOR = "checks"  # the lane that is code: its contradictions state invariants the registry owns, so a violation is proof


def _supports(entry: Entry, flag: Entry, lookup: Lookup) -> bool:
    """Whether `entry` independently backs `flag`. It must be another lane's observation or answer that says something (a quote or numbers; "cannot determine" backs nothing) and rests on evidence that the
    flag does not cite and that the supporting lane itself fetched: a second reading of the same result is not a second source."""
    if entry.kind not in (OBSERVATION, ANSWER) or entry.status == REJECTED or entry.body.get("supports") != flag.id or entry.author == flag.author:
        return False
    if entry.body.get("cannot_determine") or not (entry.body.get("quote") or entry.body.get("values")):
        return False
    own = [ev for ev in entry.evidence if ev not in flag.evidence and (row := lookup(ev)) is not None and row.get("lane") == entry.author]
    return bool(own)


def _proof(flag: Entry, lookup: Lookup) -> bool:
    """A violated relation proves a flag only when the relation is the code's own (an invariant the `checks` lane states). A relation a model chose is a claim about which numbers must agree, and the
    model may be wrong about that; such a flag stays single-source unless a second lane backs it, and says that the stated numbers do differ."""
    return flag.author == CODE_AUTHOR and evaluate_contradiction(flag.body.get("contradiction"), lookup) is True


def classify(store: ForensicStore, case_id: str, lookup: Lookup) -> None:
    """Set the status of every kept flag: established, single_source or duplicate. Flags with the same type, evidence and compared numbers are one flag; when one of them is proven, that one is kept."""
    entries = store.entries(case_id)
    flags = [e for e in entries if e.kind == FLAG and e.status != REJECTED]
    groups: dict[tuple[str, frozenset[str], frozenset[tuple[str, str]]], list[Entry]] = {}
    for flag in flags:
        groups.setdefault((str(flag.body.get("flag_type")), frozenset(flag.evidence), frozenset(contradiction_paths(flag.body.get("contradiction")))), []).append(flag)
    for group in groups.values():
        keep = next((f for f in group if _proof(f, lookup)), group[0])
        for flag in group:
            if flag is not keep:
                store.set_status(case_id, flag.id, DUPLICATE, f"same evidence as {keep.id}")
        supporters = {e.author for e in entries if _supports(e, keep, lookup)}
        stated = evaluate_contradiction(keep.body.get("contradiction"), lookup) is True
        if _proof(keep, lookup):
            store.set_status(case_id, keep.id, ESTABLISHED, "the code's own invariant is violated by the cited numbers")
        elif supporters:
            store.set_status(case_id, keep.id, ESTABLISHED, f"independently supported by {', '.join(sorted(supporters))}")
        else:
            store.set_status(case_id, keep.id, SINGLE_SOURCE, "one source only" + ("; the numbers it compares do differ as it states, but the relation is its own claim" if stated else ""))


def concern(flags: Sequence[Entry]) -> dict[str, Any]:
    """The concern level as a range, from the flags' final statuses."""
    established = sum(WEIGHTS.get(str(f.body.get("flag_type")), 1.0) for f in flags if f.status == ESTABLISHED)
    single = sum(WEIGHTS.get(str(f.body.get("flag_type")), 1.0) for f in flags if f.status == SINGLE_SOURCE)
    low, high = established, established + SINGLE_SOURCE_SHARE * single
    return {"low": level(low), "high": level(high), "score_low": round(low, 2), "score_high": round(high, 2), "established": sum(1 for f in flags if f.status == ESTABLISHED), "single_source": sum(1 for f in flags if f.status == SINGLE_SOURCE)}
