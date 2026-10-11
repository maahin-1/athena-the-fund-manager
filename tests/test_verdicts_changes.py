from datetime import date, datetime, timezone

from athena.verdicts.changes import Facts, change_reasons, facts_from

DAY = date(2026, 10, 9)
THEN = Facts(DAY, 100.0, date(2026, 3, 31), date(2026, 6, 30))


def stored(close=100.0, day=DAY, annual="2026-03-31", quarter="2026-06-30", fundamentals=True):
    technical = {"data_as_of": day, "built_at": datetime(2026, 10, 10, tzinfo=timezone.utc), "flags": [], "packet": {"metrics": {"last_close": {"value": close}}}}
    body = {"data_as_of": day, "built_at": technical["built_at"], "flags": [], "packet": {"latest_annual_period": annual, "latest_quarter": quarter}}
    return technical, (body if fundamentals else None)


def test_facts_come_from_the_stored_packets():
    assert facts_from(*stored()) == THEN


def test_an_etf_or_a_stock_without_statements_has_no_periods_and_no_price_means_no_facts():
    assert facts_from(*stored(fundamentals=False)) == Facts(DAY, 100.0, None, None)
    assert facts_from(None, None) is None
    technical, _ = stored()
    technical["packet"]["metrics"] = {}
    assert facts_from(technical, None).close is None


def test_nothing_changed_gives_no_reasons():
    assert change_reasons(THEN, THEN) == []
    assert change_reasons(THEN, Facts(date(2026, 10, 20), 105.0, THEN.annual_period, THEN.quarter)) == []


def test_new_results_are_a_reason_either_annual_or_quarterly():
    assert "new annual results" in change_reasons(THEN, Facts(DAY, 100.0, date(2027, 3, 31), THEN.quarter))[0]
    assert "new quarterly results" in change_reasons(THEN, Facts(DAY, 100.0, THEN.annual_period, date(2026, 9, 30)))[0]


def test_a_stock_that_had_no_statements_and_now_has_them_is_a_change():
    assert len(change_reasons(Facts(DAY, 100.0, None, None), THEN)) == 2


def test_a_large_move_either_way_is_a_reason_and_a_small_one_is_not():
    assert "-13%" in change_reasons(THEN, Facts(DAY, 87.0, THEN.annual_period, THEN.quarter))[0]
    assert "+12%" in change_reasons(THEN, Facts(DAY, 112.0, THEN.annual_period, THEN.quarter))[0]
    assert change_reasons(THEN, Facts(DAY, 111.0, THEN.annual_period, THEN.quarter)) == []


def test_price_data_far_past_the_verdict_is_a_reason():
    reasons = change_reasons(THEN, Facts(date(2026, 11, 20), 100.0, THEN.annual_period, THEN.quarter))
    assert reasons == ["42 days of price data since"]


def test_a_missing_close_is_not_a_move():
    assert change_reasons(Facts(DAY, None, None, None), Facts(DAY, 50.0, None, None)) == []
