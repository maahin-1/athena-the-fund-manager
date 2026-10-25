from datetime import date, datetime, timedelta

import pytest

from athena.agents.base import Specialist
from athena.agents.ownership import OWNERSHIP
from athena.ownership import study
from athena.ownership.packet import build_ownership_packet, is_quarter_end, quarters


def pattern(total=1000.0, promoters=500.0, fii=100.0, dii=150.0, mf=60.0, holders=1000.0, **extra):
    """One quarter: share counts by group, (shares, holders). The public is whatever the promoters do not hold."""
    groups = {
        "total": (total, holders), "promoters": (promoters, 5.0), "public_total": (total - promoters, holders - 5.0), "fii": (fii, 40.0), "dii": (dii, 60.0), "mutual_funds": (mf, 30.0),
        "retail": (200.0, holders * 0.9),
    }
    groups.update(extra)
    return groups


QUARTERS = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


def history(**last):
    """Six quarters; promoters 50.0, 50.0, 50.0, 49.0, 49.0, 49.0 percent so the last year saw them selling; institutions rising."""
    out = {}
    for i, end in enumerate(QUARTERS):
        out[end] = pattern(promoters=[500, 500, 500, 490, 490, 490][i], fii=100 + 10 * i, dii=150 + 5 * i, holders=1000 + 100 * i)
    out.update(last)
    return out


def filed(days_after=20):
    return {end: datetime(end.year, end.month, end.day) + timedelta(days=days_after) for end in QUARTERS}


def test_a_share_is_shares_held_over_total_shares_in_percentage_points():
    rows = quarters(history(), filed(), date(2026, 8, 1))
    assert [q.end for q in rows] == QUARTERS
    assert rows[-1].pct["promoters"] == pytest.approx(49.0)
    assert rows[-1].pct["fii"] == pytest.approx(15.0)


def test_one_off_patterns_on_other_dates_are_not_quarters():
    odd = history()
    odd[date(2026, 5, 12)] = pattern()
    assert not is_quarter_end(date(2026, 5, 12))
    assert date(2026, 5, 12) not in [q.end for q in quarters(odd)]


def test_a_quarter_is_known_only_after_it_was_filed():
    f = filed()
    rows = quarters(history(), f, date(2026, 7, 10))  # the June quarter was filed on 20 July
    assert rows[-1].end == date(2026, 3, 31)
    assert quarters(history(), f, date(2026, 7, 20))[-1].end == date(2026, 6, 30)


def test_a_pattern_whose_groups_do_not_add_up_is_left_out_not_repaired():
    bad = history()
    bad[QUARTERS[-1]] = pattern(promoters=500.0, public_total=(300.0, 10.0))  # 500 + 300 against a total of 1000
    assert QUARTERS[-1] not in [q.end for q in quarters(bad)]


def test_the_packet_has_the_holdings_and_the_changes_over_one_and_four_quarters():
    p = build_ownership_packet("ABC", date(2026, 8, 1), history(), filed())
    m = p["metrics"]
    assert m["promoter_holding"]["value"] == pytest.approx(49.0) and m["promoter_holding"]["unit"] == "percentage points"
    assert m["promoter_change_1q"]["value"] == pytest.approx(0.0)
    assert m["promoter_change_4q"]["value"] == pytest.approx(-1.0)  # 49.0 now against 50.0 four quarters before (June 2025)
    assert m["fii_change_1q"]["value"] == pytest.approx(1.0)  # 10 more shares in 1,000 is one percentage point
    assert m["fii_change_4q"]["value"] == pytest.approx(4.0)
    assert m["shareholders_change_4q"]["value"] == pytest.approx(1500 / 1100 - 1, abs=1e-4) and m["shareholders_change_4q"]["unit"] == "fraction"
    assert p["pledges"] == "not held" and p["latest_quarter"] == "2026-06-30"
    assert p["missing"] == []


def test_a_missing_quarter_leaves_the_change_out_with_a_reason():
    short = history()
    del short[date(2026, 3, 31)]
    m = build_ownership_packet("ABC", date(2026, 8, 1), short, filed())
    assert "fii_change_1q" in m["missing"] and "fii_change_1q" not in m["metrics"]
    assert "missing" in m["missing_reasons"]["fii_change_1q"]
    assert "fii_change_4q" in m["metrics"]  # the quarter a year earlier is still there


def test_a_group_the_company_does_not_report_is_missing_not_zero():
    h = history()
    for end in QUARTERS:
        del h[end]["promoters"]
    p = build_ownership_packet("ABC", date(2026, 8, 1), h, filed())
    assert "promoter_holding" in p["missing"] and "promoter_holding" not in p["metrics"]
    assert "fii_holding" in p["metrics"]


def test_a_pattern_that_is_too_old_gives_nothing():
    p = build_ownership_packet("ABC", date(2027, 6, 1), history(), filed())
    assert p["metrics"] == {} and "fii_holding" in p["missing"]
    assert "more than 200 days" in p["missing_reasons"]["fii_holding"]
    none = build_ownership_packet("ABC", date(2026, 8, 1), {}, {})
    assert none["metrics"] == {} and "no shareholding pattern" in none["missing_reasons"]["fii_holding"]


class Replies:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def complete(self, system, user):
        self.calls += 1
        return self.text


def test_the_analyst_abstains_without_institutional_holdings_and_does_not_call_the_model():
    llm = Replies("{}")
    out = Specialist(OWNERSHIP, llm).analyze(build_ownership_packet("ABC", date(2027, 6, 1), history(), filed()))
    assert out["signal"] == "neutral" and out["confidence"] == 0 and llm.calls == 0


def test_the_analyst_reads_the_packet_and_its_figures_are_checked_against_it():
    good = '{"signal": "bullish", "confidence": 40, "reasoning": "Foreign institutions added 4.0 percentage points over four quarters while promoters gave up 1.0."}'
    out = Specialist(OWNERSHIP, Replies(good)).analyze(build_ownership_packet("ABC", date(2026, 8, 1), history(), filed()))
    assert out["signal"] == "bullish" and out["data_coverage"] == "full"
    invented = '{"signal": "bullish", "confidence": 40, "reasoning": "Foreign institutions added 9.7 percentage points."}'
    from athena.agents.base import SpecialistError

    with pytest.raises(SpecialistError):
        Specialist(OWNERSHIP, Replies(invented)).analyze(build_ownership_packet("ABC", date(2026, 8, 1), history(), filed()))


def test_scores_are_changes_since_the_quarter_before_or_a_year_before():
    rows = {"ABC": quarters(history()), "XYZ": quarters(history())}
    one = study.scores_by_quarter(rows, 1)
    assert one[date(2026, 6, 30)]["fii"]["ABC"] == pytest.approx(1.0)
    assert one[date(2026, 6, 30)]["shareholders"]["ABC"] == pytest.approx(1500 / 1400 - 1)
    four = study.scores_by_quarter(rows, 4)
    assert set(four) == {date(2026, 3, 31), date(2026, 6, 30)}  # only quarters with a quarter four back
    assert four[date(2026, 6, 30)]["promoters"]["ABC"] == pytest.approx(-1.0)


def test_the_evaluation_measures_from_forty_five_days_after_the_quarter_and_ranks_stocks():
    import numpy as np

    from athena.evaluation.walkforward import Panel

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(300)]
    names = [f"S{i}" for i in range(120)]
    px = {}
    for k, s in enumerate(names):
        px[s] = np.array([100.0 * (1 + 0.00005 * (k - 60) * i) for i in range(300)])  # a higher k drifts up faster
    panel = Panel(days, px)
    scores = {date(2026, 3, 31): {"fii": {s: float(k) for k, s in enumerate(names)}}}  # the score is the drift rank
    rows = study.evaluate(panel, scores, 21)["fii"]
    assert len(rows) == 1 and rows[0]["ic"] == pytest.approx(1.0, abs=1e-6)
    assert rows[0]["entry"] == date(2026, 3, 31) + timedelta(days=45)
    inverse = {date(2026, 3, 31): {"fii": {s: -float(k) for k, s in enumerate(names)}}}
    assert study.evaluate(panel, inverse, 21)["fii"][0]["ic"] == pytest.approx(-1.0, abs=1e-6)
    few = {date(2026, 3, 31): {"fii": {s: float(k) for k, s in enumerate(names[:50])}}}
    assert study.evaluate(panel, few, 21) == {}  # too few stocks for a quarter to count
