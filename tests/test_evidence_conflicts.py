from datetime import datetime, timezone

from athena.evidence import conflicts as cf

T = lambda d, h=9: datetime(2026, 10, d, h, tzinfo=timezone.utc)  # noqa: E731
TIERS = {"et": "B", "nse_like": "A", "blog": "C", "mint": "B"}


def test_a_source_starts_at_its_tiers_prior_and_a_short_record_cannot_make_it_perfect():
    assert cf.trust("B", 0, 0, 0) == {"value": 0.75, "settled": 0, "confirmed": 0, "partly": 0, "contradicted": 0, "basis": "prior"}
    six_for_six = cf.trust("B", 6, 0, 0)
    assert 0.75 < six_for_six["value"] < 0.9 and six_for_six["basis"] == "prior"  # (6 + 7.5) / 16 = 0.84, not 1.0
    assert cf.trust("A", 0, 0, 0)["value"] > cf.trust("B", 0, 0, 0)["value"] > cf.trust("C", 0, 0, 0)["value"]


def test_a_long_bad_record_pulls_a_source_below_its_prior_and_is_then_called_measured():
    bad = cf.trust("B", 10, 0, 30)
    assert bad["value"] < 0.5 and bad["basis"] == "measured" and bad["settled"] == 40
    assert cf.trust("B", 20, 0, 10)["basis"] == "measured"


def test_partly_right_counts_half_and_an_unknown_tier_is_read_as_the_least_trusted():
    assert cf.trust("B", 0, 10, 0)["value"] == (5 + 7.5) / 20
    assert cf.trust("Z", 0, 0, 0)["value"] == cf.PRIOR["C"]


def row(kind, text, source, story, day=28, outcome="unchecked", fields=None, publisher="Pub", detail=""):
    return {"claim_id": f"{source}{story}{text}", "kind": kind, "text": text, "source_id": source, "story_id": story, "published_at": T(day), "outcome": outcome, "fields": fields or {}, "publisher": publisher, "link": "https://x/a", "detail": detail}


def test_ten_copies_of_one_story_are_one_voice_and_two_different_stories_are_two():
    text = "Infosys in talks to buy a German consultancy, sources said"
    rows = [row("rumour", text, "et", "s1"), row("rumour", text, "mint", "s1"), row("rumour", text + " report", "blog", "s2"), row("rumour", "Infosys opens a campus in Pune", "et", "s3")]
    groups = cf.rumour_groups(rows, TIERS)
    top = next(g for g in groups if "German" in g["text"])
    assert top["stories"] == 2 and top["best_tier"] == "B" and "2 independent stories" in top["note"] and "no filing or price in our data confirms it" in top["note"]
    other = next(g for g in groups if "campus" in g["text"])
    assert other["stories"] == 1 and "1 independent story," in other["note"]


def test_opposite_claims_about_one_company_on_one_day_are_shown_side_by_side_with_their_own_checks():
    rows = [
        row("price_move", "Infosys shares jump 5%", "et", "s1", fields={"direction": 1, "pct": 5.0}, outcome="contradicted", detail="the furthest it rose was 1.0%"),
        row("price_move", "Infosys stock falls 3%", "mint", "s2", fields={"direction": -1, "pct": 3.0}, outcome="confirmed", detail="it fell as much as 3.4%"),
        row("price_move", "Infosys shares jump 2%", "blog", "s3", day=27, fields={"direction": 1, "pct": 2.0}),
    ]
    found = cf.disagreements(rows, TIERS)
    assert len(found) == 1 and found[0]["kind"] == "price_move" and found[0]["day"] == "2026-10-28"
    up, down = found[0]["sides"]
    assert (up["outcome"], down["outcome"]) == ("contradicted", "confirmed") and up["tier"] == "B" and "rose" in up["detail"]


def test_same_direction_claims_and_claims_on_different_days_are_not_a_disagreement():
    same = [row("price_move", "a", "et", "s1", fields={"direction": 1, "pct": 5.0}), row("price_move", "b", "mint", "s2", fields={"direction": 1, "pct": 6.0})]
    assert cf.disagreements(same, TIERS) == []
    split_days = [row("price_move", "a", "et", "s1", day=27, fields={"direction": 1, "pct": 5.0}), row("price_move", "b", "mint", "s2", day=28, fields={"direction": -1, "pct": 6.0})]
    assert cf.disagreements(split_days, TIERS) == []
    insiders = [row("insider", "Promoter buys", "et", "s1", fields={"action": "Buy"}), row("insider", "Promoter sells", "mint", "s2", fields={"action": "Sell"})]
    assert len(cf.disagreements(insiders, TIERS)) == 1
    assert cf.disagreements([row("insider", "Promoter pledges", "et", "s1", fields={"action": "Pledge"}), row("insider", "Promoter buys", "mint", "s2", fields={"action": "Buy"})], TIERS) == []


def test_a_claim_view_carries_the_citation():
    r = row("price_move", "Infosys shares jump 5%", "et", "s1", outcome="confirmed", detail="ok", publisher="Economic Times")
    r["first_seen_at"] = T(29)
    v = cf.claim_view(r, TIERS)
    assert v["tier"] == "B" and v["publisher"] == "Economic Times" and v["published"].startswith("2026-10-28") and v["first_seen"].startswith("2026-10-29") and v["link"] == "https://x/a"
