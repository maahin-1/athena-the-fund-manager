from datetime import date

from athena.pit.yahoo_asof import NOTE, yahoo_statements_as_of

PAYLOAD = {
    "info": {"sector": "Technology", "financialCurrency": "INR", "currentPrice": 999.0, "marketCap": 1e12, "dividendRate": 5.0, "sharesOutstanding": 7e9},
    "annual": {
        "income": {"Net Income": {"2025-03-31": 50.0, "2024-03-31": 40.0}},
        "balance": {"Ordinary Shares Number": {"2025-03-31": 3e9, "2024-03-31": 2.9e9}},
        "cashflow": {},
    },
    "quarterly": {"income": {"Net Income": {"2026-03-31": 15.0, "2025-12-31": 14.0, "2025-09-30": 13.0}}},
    "earnings_dates": [{"date": "2026-05-10", "reported": 3.0}],
    "quality_flags": ["an existing flag"],
}


def test_a_period_is_kept_only_after_its_legal_filing_deadline_has_passed():
    out = yahoo_statements_as_of(PAYLOAD, date(2026, 5, 20))  # the 2026-03-31 quarter is due by 2026-05-15, the year by 2026-05-30
    assert sorted(out["quarterly"]["income"]["Net Income"]) == ["2025-09-30", "2025-12-31", "2026-03-31"]
    assert sorted(out["annual"]["income"]["Net Income"]) == ["2024-03-31", "2025-03-31"]
    early = yahoo_statements_as_of(PAYLOAD, date(2026, 5, 10))
    assert sorted(early["quarterly"]["income"]["Net Income"]) == ["2025-09-30", "2025-12-31"]
    assert sorted(yahoo_statements_as_of(PAYLOAD, date(2025, 5, 30))["annual"]["income"]["Net Income"]) == ["2024-03-31"]  # due on the 30th: not yet


def test_nothing_that_describes_today_survives_and_the_share_count_is_the_latest_years_balance_sheet_one():
    out = yahoo_statements_as_of(PAYLOAD, date(2025, 7, 1))
    assert out["info"] == {"sector": "Technology", "financialCurrency": "INR", "sharesOutstanding": 3e9}
    assert out["earnings_dates"] == [] and out["quality_flags"] == ["an existing flag", NOTE]
    assert yahoo_statements_as_of(PAYLOAD, date(2024, 1, 1))["info"]["sharesOutstanding"] is None


def test_the_stored_payload_is_not_changed():
    before = repr(PAYLOAD)
    yahoo_statements_as_of(PAYLOAD, date(2025, 7, 1))
    assert repr(PAYLOAD) == before
