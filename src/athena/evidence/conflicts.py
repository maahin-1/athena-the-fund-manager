"""What to show when sources disagree, and how far to trust a source. All of it is arithmetic on stored claims and checks; none of it asks a model.

*Trust.* A source starts at the prior of its tier (A 0.90, B 0.75, C 0.55: judgement, not measurement) and moves toward its own record as claims are settled:

    trust = (confirmed + 0.5 * partly + K * prior) / (settled + K)        K = 10

so a source with six settled claims cannot be called perfect. Until `MEASURED_AT` claims are settled the page shows the number as a prior.

*Three kinds of statement.* A checked claim is shown with what our data said. A report we cannot check (a plan, talks) is shown with how many INDEPENDENT stories carry it (copies of one story count once)
and the best tier among them, and is never called confirmed. Two sources that say opposite things about the same company on the same day are shown side by side, each with its check, so the person
sees which one our data supports. A majority never settles a fact."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from athena.evidence.store import headline_words

PRIOR = {"A": 0.90, "B": 0.75, "C": 0.55}
PRIOR_WEIGHT = 10
MEASURED_AT = 30
RUMOUR_SIMILARITY = 0.4
_RANK = {"A": 0, "B": 1, "C": 2}
SIGNED = ("price_move", "results")


def trust(tier: str, confirmed: int, partly: int, contradicted: int) -> dict[str, Any]:
    """The source's trust now: its tier's prior shrunk toward its settled record. `basis` is `prior` until the record is long enough to speak."""
    prior = PRIOR.get(tier, PRIOR["C"])
    settled = confirmed + partly + contradicted
    value = (confirmed + 0.5 * partly + PRIOR_WEIGHT * prior) / (settled + PRIOR_WEIGHT)
    return {"value": round(value, 3), "settled": settled, "confirmed": confirmed, "partly": partly, "contradicted": contradicted, "basis": "measured" if settled >= MEASURED_AT else "prior"}


def best_tier(tiers: Sequence[str]) -> str:
    return min(tiers, key=lambda t: _RANK.get(t, 2)) if tiers else "C"


def rumour_groups(rows: Sequence[dict[str, Any]], tier_of_source: dict[str, str]) -> list[dict[str, Any]]:
    """Rumour claims grouped by what they say (headline words overlap), each with the number of independent stories behind it. Stories are counted by `story_id`: ten feeds printing one agency
    story are one. Newest group first."""
    groups: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: x["published_at"]):
        if r["kind"] != "rumour":
            continue
        words = headline_words(r["text"])
        home = next((g for g in groups if len(words & g["words"]) / max(len(words | g["words"]), 1) >= RUMOUR_SIMILARITY and len(words) >= 3), None)
        if home is None:
            home = {"words": set(words), "rows": []}
            groups.append(home)
        else:
            home["words"] |= words
        home["rows"].append(r)
    out = []
    for g in groups:
        rows_g = g["rows"]
        stories = {r["story_id"] or r["claim_id"] for r in rows_g}
        tiers = [tier_of_source.get(r["source_id"], "C") for r in rows_g]
        newest = max(rows_g, key=lambda r: r["published_at"])
        out.append({
            "text": newest["text"], "stories": len(stories), "best_tier": best_tier(tiers), "first": min(r["published_at"] for r in rows_g).isoformat(), "last": newest["published_at"].isoformat(),
            "sources": sorted({r["publisher"] for r in rows_g}), "link": newest["link"],
            "note": f"reported by {len(stories)} independent {'story' if len(stories) == 1 else 'stories'}, best source tier {best_tier(tiers)}; no filing or price in our data confirms it",
        })
    return sorted(out, key=lambda x: x["last"], reverse=True)


def _sign_of(row: dict[str, Any]) -> int | None:
    f = row["fields"]
    if row["kind"] in SIGNED:
        return int(f["direction"])
    if row["kind"] == "insider":
        return {"Buy": 1, "Sell": -1}.get(f.get("action"))
    return None


def disagreements(rows: Sequence[dict[str, Any]], tier_of_source: dict[str, str]) -> list[dict[str, Any]]:
    """Pairs of claims about one company, of one kind and from one day, that point opposite ways and come from different stories. Each side is given with its own check, because that is what
    tells the person which one the data supports."""
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["kind"] in SIGNED + ("insider",) and _sign_of(r) is not None:
            by_key[(r["kind"], r["fields"].get("metric", ""), r["published_at"].date().isoformat())].append(r)
    out = []
    for (kind, _metric, day), group in by_key.items():
        up = [r for r in group if _sign_of(r) == 1]
        down = [r for r in group if _sign_of(r) == -1]
        if not up or not down:
            continue
        side = lambda r: {  # noqa: E731
            "publisher": r["publisher"], "tier": tier_of_source.get(r["source_id"], "C"), "text": r["text"], "link": r["link"], "outcome": r["outcome"], "detail": r["detail"], "published": r["published_at"].isoformat(),
        }
        out.append({"kind": kind, "day": day, "sides": [side(max(up, key=lambda r: r["published_at"])), side(max(down, key=lambda r: r["published_at"]))]})
    return sorted(out, key=lambda d: d["day"], reverse=True)


def claim_view(r: dict[str, Any], tier_of_source: dict[str, str]) -> dict[str, Any]:
    """A claim as the page shows it: what was said, by whom, what our data said."""
    return {
        "kind": r["kind"], "text": r["text"], "outcome": r["outcome"], "detail": r["detail"], "publisher": r["publisher"], "source_id": r["source_id"], "tier": tier_of_source.get(r["source_id"], "C"),
        "published": r["published_at"].isoformat(), "first_seen": r["first_seen_at"].isoformat() if r.get("first_seen_at") else "", "link": r["link"], "item_id": r.get("item_id", ""), "title": r.get("title", ""),
    }
