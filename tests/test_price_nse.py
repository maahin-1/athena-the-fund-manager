from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.nse_actions import NseActions

ROWS = [
    {"symbol": "VEDL", "exDate": "30-Apr-2026", "subject": "Demerger", "faceVal": "1"},
    {"symbol": "VEDL", "exDate": "27-Mar-2026", "subject": "Interim Dividend - Rs 11 Per Share", "faceVal": "1"},
    {"symbol": "VEDL", "exDate": "14-Jan-2026", "subject": "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share", "faceVal": "1"},
    {"symbol": "VEDL", "exDate": "-", "subject": "Bonus 1:1", "faceVal": "1"},
    {"symbol": "VEDL", "exDate": "13-May-2020", "subject": " Rights 1:15 @ Premium Rs 1247", "faceVal": "10"},
]


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None):
        self.status_code, self._payload, self.text = status, payload, text if text is not None else "[]"

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response, home_status=403):
        self.response, self.home_status = response, home_status
        self.headers, self.calls = {}, []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, params))
        if url.endswith("nseindia.com"):
            return FakeResponse(self.home_status)
        return self.response


def client(response, today=date(2026, 10, 10)):
    session = FakeSession(response)
    return NseActions(session, today=lambda: today), session


def test_rows_become_actions_with_ordinary_dividends_and_unreadable_dates_dropped():
    nse, _ = client(FakeResponse(payload=ROWS))
    actions = nse("VEDL")
    assert [(a.day, a.kind) for a in actions] == [(date(2020, 5, 13), "rights"), (date(2026, 1, 14), "split"), (date(2026, 4, 30), "demerger")]
    assert actions[1].ratio == pytest.approx(5.0) and actions[0].premium == 1247.0
    assert isinstance(actions[2], CorporateAction)


def test_the_request_asks_for_eight_years_back_to_a_year_ahead_and_primes_the_session_once():
    nse, session = client(FakeResponse(payload=[]))
    nse("VEDL")
    nse("RELIANCE")
    api_calls = [call for call in session.calls if "corporates-corporateActions" in call[0]]
    assert len(api_calls) == 2 and sum(call[0].endswith("nseindia.com") for call in session.calls) == 1
    params = api_calls[0][1]
    assert params == {"index": "equities", "symbol": "VEDL", "from_date": "10-10-2018", "to_date": "10-10-2027"}
    assert session.headers["Referer"] == "https://www.nseindia.com/" and "Mozilla" in session.headers["User-Agent"]


@pytest.mark.parametrize("response", [FakeResponse(status=403), FakeResponse(payload=None, text="<html>blocked</html>"), FakeResponse(payload={"error": "x"})])
def test_a_blocked_garbled_or_unexpected_reply_is_a_clear_error_not_a_crash(response):
    with pytest.raises(AthenaError, match="NSE corporate actions unavailable for VEDL"):
        client(response)[0]("VEDL")


def test_a_network_failure_is_also_an_athena_error():
    class Broken(FakeSession):
        def get(self, url, **kwargs):
            raise OSError("connection reset")

    with pytest.raises(AthenaError, match="NSE corporate actions unavailable for VEDL: connection reset"):
        NseActions(Broken(None), today=lambda: date(2026, 10, 10))("VEDL")
