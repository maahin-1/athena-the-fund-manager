"""The shared timing contract (WP02): decision date in IST, the next session after it, and which dated evidence a case may use."""
from datetime import date, datetime, timezone

import pytest

from athena import timing

UTC = timezone.utc


def test_the_decision_date_is_the_exchange_local_date_not_the_utc_date():
    assert timing.decision_date(datetime(2026, 1, 7, 5, 0, tzinfo=UTC)) == date(2026, 1, 7)  # 10:30 IST
    assert timing.decision_date(datetime(2026, 1, 7, 20, 0, tzinfo=UTC)) == date(2026, 1, 8)  # 01:30 IST the next morning
    with pytest.raises(ValueError):
        timing.decision_date(datetime(2026, 1, 7, 5, 0))  # a naive time has no exchange date


SESSIONS = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9), date(2026, 1, 13)]  # the 12th (a Monday) was a holiday


@pytest.mark.parametrize(
    "after,expected",
    [(date(2026, 1, 7), date(2026, 1, 8)), (date(2026, 1, 9), date(2026, 1, 13)), (date(2026, 1, 10), date(2026, 1, 13)), (date(2026, 1, 12), date(2026, 1, 13)), (date(2026, 1, 13), None), (date(2025, 12, 1), date(2026, 1, 5))],
)
def test_the_next_session_is_strictly_after_the_decision_date_and_skips_weekends_and_holidays(after, expected):
    assert timing.next_session(after, SESSIONS) == expected


def test_a_decision_is_historical_when_its_case_date_is_before_the_exchange_today():
    now = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)  # already 13 November in India
    assert timing.is_historical(date(2026, 11, 12), now) and not timing.is_historical(date(2026, 11, 13), now)


def test_evidence_published_after_the_case_date_is_never_admitted_in_either_mode():
    for strict in (True, False):
        got = timing.admit(date(2027, 1, 1), date(2025, 1, 1), strict=strict)
        assert not got.ok and "after the case date" in got.reason and got.precision == "date"


def test_a_date_only_publication_counts_as_available_at_the_end_of_its_day_so_the_case_day_is_admitted():
    assert timing.admit(date(2025, 1, 1), date(2025, 1, 1), strict=True).ok  # the policy is stated, not assumed: see POLICY_NOTE
    assert timing.admit(date(2024, 12, 31), date(2025, 1, 1), strict=True).ok
    assert "end of" in timing.POLICY_NOTE


def test_an_instant_is_compared_as_an_instant_against_the_end_of_the_case_day_in_ist():
    late = datetime(2025, 1, 1, 19, 0, tzinfo=UTC)  # 00:30 IST on 2 January
    assert not timing.admit(late, date(2025, 1, 1), strict=True).ok and timing.admit(late, date(2025, 1, 2), strict=True).ok
    assert timing.admit(datetime(2025, 1, 1, 17, 0, tzinfo=UTC), date(2025, 1, 1), strict=True).precision == "instant"  # 22:30 IST on the 1st


def test_an_unknown_publication_is_refused_in_strict_historical_mode_and_admitted_but_labelled_live():
    strict = timing.admit(None, date(2025, 1, 1), strict=True)
    assert not strict.ok and strict.precision == "unknown" and "unknown" in strict.reason
    live = timing.admit(None, date(2026, 11, 12), strict=False)
    assert live.ok and live.precision == "unknown" and "labelled" in live.reason


def test_a_naive_publication_time_is_not_an_instant():
    with pytest.raises(ValueError):
        timing.admit(datetime(2025, 1, 1, 9, 0), date(2025, 1, 1), strict=True)


def test_the_policy_names_are_versioned():
    assert timing.ENTRY_POLICY.endswith("_v1") and timing.STRICT_POLICY.endswith("_v1") and timing.LIVE_POLICY.endswith("_v1")
    assert timing.LEGACY_ASSOCIATION != timing.ENTRY_POLICY
