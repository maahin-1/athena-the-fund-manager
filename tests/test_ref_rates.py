from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.reference import rates
from athena.reference.store import ReferenceStore


def reply(closes, stamps=(1792000000, 1792086400), offset=-18000):
    return {"chart": {"result": [{"meta": {"gmtoffset": offset}, "timestamp": list(stamps), "indicators": {"quote": [{"close": list(closes)}]}}]}}


class Response:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def test_the_latest_close_is_read_as_a_fraction_with_its_exchange_day():
    day, value = rates.parse_chart(reply([4.10, 4.25]))
    assert value == pytest.approx(0.0425)
    assert day == date(2026, 10, 15)  # 1792086400 is 17:46 UTC on the 15th, 12:46 in New York (the offset the reply gives)


def test_a_missing_close_is_skipped_and_an_empty_series_or_an_absurd_yield_is_refused():
    assert rates.parse_chart(reply([4.10, None]))[1] == pytest.approx(0.0410)
    with pytest.raises(AthenaError, match="no closes"):
        rates.parse_chart(reply([None, None]))
    with pytest.raises(AthenaError, match="not plausible"):
        rates.parse_chart(reply([425.0, 425.0]))
    with pytest.raises(AthenaError, match="no price series"):
        rates.parse_chart({"chart": {"result": None}})


def test_the_yield_is_stored_as_a_dated_input_and_a_service_error_is_reported():
    with ReferenceStore(":memory:") as store:
        day, value = rates.store_us_10y(store, get=lambda *a, **k: Response(reply([4.25])))
        assert store.input(rates.KEY, day).value == pytest.approx(0.0425)
        with pytest.raises(AthenaError, match="503"):
            rates.fetch_us_10y(get=lambda *a, **k: Response({}, status=503))
