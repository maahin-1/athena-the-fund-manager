"""Which flags stand, and the concern level. Both are rules in code; no model's wording can change them.

A flag's status says what was checked, no more:

- `established`: the code's own invariant is violated by the cited numbers (only the `checks` lane states those), or a person recorded their verification (`verify_by_human`). Nothing a model writes can
  produce this status.
- `corroborated`: another model lane read an *independent source* and wrote an observation or answer, with a quote or numbers found in its own evidence, that names the flag in `supports`. This is model-supported
  and not human-reviewed: whether the observation entails the flag is not checked, except that when the flag states the numbers it compares, the support must share one of them. A second reading of the
  same source (another tool, another lane, another evidence id for one document) is not a second source and corroborates nothing.
- `single_source`: neither. Shown last and not counted in the low end.

Two flags citing the same evidence are one flag. The concern level adds the weight of the flags and is shown as a range: the low end counts only established flags, the high end also counts corroborated ones in
full and half the weight of the single-source ones, so uncertainty is visible and not folded in.

The weights and the bands are provisional; step 3 of the build calibrates them on the test set of past failures and on healthy controls."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from athena.contracts import AthenaError
from athena.evaluation.grounding import numbers_in_text
from athena.forensic import lineage
from athena.forensic.model import (
    ANOMALY_VS_PEERS, ANSWER, ARITHMETIC, CLAIM_VS_OUTSIDE, CORROBORATED, DOC_VS_DOC, DUPLICATE, ESTABLISHED, FLAG, OBSERVATION, REJECTED, SINGLE_SOURCE, TEXT_VS_DATA, Entry,
)
from athena.forensic.store import ForensicStore
from athena.forensic.verify import Lookup, contradiction_paths, evaluate_contradiction, holds_number, holds_piece, number_in, pick, quote_pieces

WEIGHTS = {ARITHMETIC: 3.0, DOC_VS_DOC: 2.0, TEXT_VS_DATA: 2.0, CLAIM_VS_OUTSIDE: 2.0, ANOMALY_VS_PEERS: 1.0}
SINGLE_SOURCE_SHARE = 0.5
BANDS = ((0.0, "none established"), (2.0, "low"), (5.0, "moderate"), (9.0, "elevated"))  # score above the last bound is "high"
LEVELS = [name for _, name in BANDS] + ["high"]
STATUS_KEY = {
    ESTABLISHED: "a code-owned invariant is violated by the cited numbers, or a person recorded their verification",
    CORROBORATED: "another model lane backed it from an independent source: model-supported, not human-reviewed",
    SINGLE_SOURCE: "one source only, or support that is a second reading of the same source, unrelated, or of unknown source",
}


def level(score: float) -> str:
    if score <= 0:
        return BANDS[0][1]
    for bound, name in BANDS[1:]:
        if score <= bound:
            return name
    return "high"


CODE_AUTHOR = "checks"  # the lane that is code: its contradictions state invariants the registry owns, so a violation is proof


@dataclass(frozen=True)
class Support:
    """How one supporting entry relates to a flag. `kind` is `independent` (counts), `repeated` (reads a source the flag reads), `unknown` (a source is not known), `unrelated` (shares no number with the numbers
    the flag compares) or `none` (not support at all: the author's own flag, no evidence of its own, nothing checkable)."""

    kind: str
    reason: str
    sources: tuple[str, ...] = ()
    relevance: str = ""  # "shared number" when the flag states numbers and the support shares one, "not checked" when the flag states none


def _sources(rows: Sequence[dict[str, Any] | None], target: str) -> set[str]:
    return {key for row in rows if row is not None for key in lineage.keys(lineage.of_row(target, row))}


def _compared(flag: Entry, lookup: Lookup) -> list[float]:
    """The numbers a flag states it compares (the values its contradiction refers to), empty when it states none."""
    out: list[float] = []
    for ev, path in sorted(contradiction_paths(flag.body.get("contradiction"))):
        row = lookup(ev)
        value = pick(row["result"], path) if row else None
        if value is not None:
            out.append(value)
    return out


def assess_support(entry: Entry, flag: Entry, lookup: Lookup, target: str) -> Support:
    """Whether `entry` independently backs `flag`, and if not, why. It must be another lane's observation or answer that says something (a quote or numbers; "cannot determine" backs nothing), rest on evidence
    the supporting lane itself fetched and the flag does not cite, and read a source the flag's evidence does not read; a source that is not known is never called independent. When the flag states the numbers it
    compares, the support must contain one of them: a quote or number from elsewhere is not support for this difference."""
    if entry.kind not in (OBSERVATION, ANSWER) or entry.status == REJECTED or entry.body.get("supports") != flag.id or entry.author == flag.author:
        return Support("none", "not another lane's support for this flag")
    if entry.body.get("cannot_determine") or not (entry.body.get("quote") or entry.body.get("values")):
        return Support("none", "it states nothing checkable")
    own_rows = [row for ev in entry.evidence if ev not in flag.evidence and (row := lookup(ev)) is not None and row.get("lane") == entry.author]
    if not own_rows:
        return Support("none", "it rests on no evidence of its own")
    flag_rows = [row for ev in sorted({*flag.evidence, *(ev for ev, _ in contradiction_paths(flag.body.get("contradiction")))}) if (row := lookup(ev)) is not None]
    flags_sources = _sources(flag_rows, target)
    if not flags_sources or any(not lineage.keys(lineage.of_row(target, row)) for row in flag_rows):
        return Support("unknown", "the source of the flag's evidence is not known, so independence cannot be shown")  # one unresolved row of the flag could be the very source the support reads
    compared = _compared(flag, lookup)
    held_quote = quote_pieces(str(entry.body.get("quote") or ""))
    values = [float(v) for v in (entry.body.get("values") or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    quoted = [abs(float(t.replace(",", "").rstrip("%"))) for t in numbers_in_text(str(entry.body.get("quote") or ""))]
    if compared:
        # only the figures that are the flag's numbers count, and each one counts through the rows that hold it, not through every row the entry cites
        relevant = [f for f in values + quoted if any(number_in(f, [c]) for c in compared)]
        if not relevant:
            return Support("unrelated", f"unrelated: {entry.author}'s figures share none of the numbers the flag compares", tuple(sorted(_sources(own_rows, target))))
        checks = [(f"the figure {f:g}", lambda row, f=f: holds_number(f, row["result"])) for f in relevant]
        relevance, need_all = "shared number", False
    else:
        checks = [(f"the figure {v:g}", lambda row, v=v: holds_number(v, row["result"])) for v in values]
        checks += [(f"the quoted passage {p[:24]}...", lambda row, p=p: holds_piece(p, row["result"])) for p in held_quote]
        relevance, need_all = "not checked", True
    results = [_binding(label, test, own_rows, target, flags_sources) for label, test in checks]
    if not results:
        return Support("none", "it states nothing checkable")
    good = [r for r in results if r[0] == "independent"]
    if (len(good) == len(results)) if need_all else bool(good):
        return Support("independent", "", tuple(sorted(set().union(*(r[2] for r in good)))), relevance)
    kind, label, shared = next((r for k in ("repeated", "none", "unknown") for r in results if r[0] == k))
    reason = {"repeated": f"{entry.author} reads the same source as the flag ({', '.join(sorted(shared))}): a second reading of one source is not corroboration",
              "unknown": "the source of the evidence that holds it is not known, so independence cannot be shown",
              "none": "no evidence of the supporting lane's own holds it"}[kind]
    return Support(kind, f"{label}: {reason}", tuple(sorted(shared)))


def _binding(label: str, test: Callable[[dict[str, Any]], bool], own_rows: list[dict[str, Any]], target: str, flags_sources: set[str]) -> tuple[str, str, set[str]]:
    """How one figure or quoted passage of a supporting entry is backed: through which of the supporting lane's own rows, and whether those rows are known to be another source than the flag's.
    `independent` needs a row that holds it, whose source is known and is not one the flag reads. Otherwise `repeated` (a row that holds it reads a source the flag reads), `unknown` (every row that holds
    it has no known source) or `none` (no row of the lane's own holds it). Rows that do not hold it are not consulted: another row's known source lends nothing to it."""
    holding = [lineage.keys(lineage.of_row(target, row)) for row in own_rows if test(row)]
    if not holding:
        return "none", label, set()
    independent = [keys for keys in holding if keys and not keys & flags_sources]
    if independent:
        return "independent", label, set().union(*independent)
    shared = set().union(*(keys & flags_sources for keys in holding))
    if shared:
        return "repeated", label, shared
    return "unknown", label, set()


def _proof(flag: Entry, lookup: Lookup) -> bool:
    """A violated relation proves a flag only when the relation is the code's own (an invariant the `checks` lane states). A relation a model chose is a claim about which numbers must agree, and the
    model may be wrong about that; such a flag stays single-source unless a second lane backs it, and says that the stated numbers do differ."""
    return flag.author == CODE_AUTHOR and evaluate_contradiction(flag.body.get("contradiction"), lookup) is True


def verify_by_human(store: ForensicStore, case_id: str, entry_id: str, reviewer: str, note: str) -> None:
    """Record a person's verification of a flag: who looked and what they did. The next `classify` marks the flag `established` and keeps that. The reviewer and the note are required and are shown with the
    flag; only a kept flag can be verified."""
    if not str(reviewer).strip() or not str(note).strip():
        raise AthenaError("a verification needs the name of the reviewer and a note of what was checked")
    flag = store.entry(case_id, entry_id)
    if flag is None or flag.kind != FLAG or flag.status in (REJECTED, DUPLICATE):
        raise AthenaError(f"{entry_id} is not a kept flag in case {case_id}")
    store.record_verification(case_id, entry_id, str(reviewer).strip(), str(note).strip())


def classify(store: ForensicStore, case_id: str, lookup: Lookup) -> None:
    """Set the status of every kept flag: established, corroborated, single_source or duplicate. Flags with the same type, evidence and compared numbers are one flag; when one of them is proven, that one is kept."""
    entries = store.entries(case_id)
    case = store.case(case_id)
    target = case.target if case else ""
    verified = {v["entry"]: v for v in store.verifications(case_id)}
    flags = [e for e in entries if e.kind == FLAG and e.status != REJECTED]
    groups: dict[tuple[str, frozenset[str], frozenset[tuple[str, str]]], list[Entry]] = {}
    for flag in flags:
        groups.setdefault((str(flag.body.get("flag_type")), frozenset(flag.evidence), frozenset(contradiction_paths(flag.body.get("contradiction")))), []).append(flag)
    for group in groups.values():
        keep = next((f for f in group if _proof(f, lookup)), next((f for f in group if f.id in verified), group[0]))
        for flag in group:
            if flag is not keep:
                store.set_status(case_id, flag.id, DUPLICATE, f"same evidence as {keep.id}")
        assessed = [(e, assess_support(e, keep, lookup, target)) for e in entries]
        independent = [(e, s) for e, s in assessed if s.kind == "independent"]
        stated = evaluate_contradiction(keep.body.get("contradiction"), lookup) is True
        if _proof(keep, lookup):
            store.set_status(case_id, keep.id, ESTABLISHED, "the code's own invariant is violated by the cited numbers")
        elif keep.id in verified:
            v = verified[keep.id]
            store.set_status(case_id, keep.id, ESTABLISHED, f"human verification recorded by {v['reviewer']}: {v['note'][:160]}")
        elif independent:
            lanes = ", ".join(sorted({e.author for e, _ in independent}))
            where = ", ".join(sorted({k for _, s in independent for k in s.sources}))
            relevance = "" if all(s.relevance == "shared number" for _, s in independent) else "; relevance to the flag not checked (it states no numbers)"
            store.set_status(case_id, keep.id, CORROBORATED, f"corroborated by model lane {lanes} from an independent source ({where}); model-supported, not human-reviewed{relevance}")
        else:
            why = [f"{e.id}: {s.reason}" for e, s in assessed if s.kind in ("repeated", "unknown", "unrelated")]
            store.set_status(case_id, keep.id, SINGLE_SOURCE,
                             "one source only" + ("; the numbers it compares do differ as it states, but the relation is its own claim" if stated else "") + (" (" + "; ".join(why) + ")" if why else ""))


def concern(flags: Sequence[Entry]) -> dict[str, Any]:
    """The concern level as a range, from the flags' final statuses."""
    def weight(status: str) -> float:
        return sum(WEIGHTS.get(str(f.body.get("flag_type")), 1.0) for f in flags if f.status == status)

    established, corroborated, single = weight(ESTABLISHED), weight(CORROBORATED), weight(SINGLE_SOURCE)
    low, high = established, established + corroborated + SINGLE_SOURCE_SHARE * single
    return {
        "low": level(low), "high": level(high), "score_low": round(low, 2), "score_high": round(high, 2), "established": sum(1 for f in flags if f.status == ESTABLISHED),
        "corroborated": sum(1 for f in flags if f.status == CORROBORATED), "single_source": sum(1 for f in flags if f.status == SINGLE_SOURCE),
    }
