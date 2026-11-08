from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError, Bar
from athena.forensic.market.lane import MARKET_LANE, MarketData, market_tools
from athena.forensic.model import Case
from athena.forensic.store import ForensicStore
from athena.forensic.supervisor import Supervisor
from athena.forensic.tools import ToolContext, ToolError, base_registry

UTC = timezone.utc
START = date(2026, 1, 5)  # a Monday


def weekdays(n: int, start: date = START) -> list[date]:
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def bar(day: date, close: float, volume: float = 1000.0) -> Bar:
    stamp = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=5, minutes=30)
    return Bar("DEMO", stamp, close, close * 1.01, close * 0.99, close, volume, stamp, "test")


@dataclass
class Action:
    day: date
    kind: str
    text: str = ""


class Warehouse:
    def __init__(self, closes: dict[date, float], volumes: dict[date, float] | None = None, actions=(), index: dict[date, float] | None = None, index_name: str = "Nifty 50"):  # noqa: ANN001
        self.closes, self.volumes, self._actions, self._index, self._index_name = closes, volumes or {}, list(actions), index, index_name

    def bars(self, symbol: str, since=None, until=None):  # noqa: ANN001, ANN201
        return [bar(d, c, self.volumes.get(d, 1000.0)) for d, c in sorted(self.closes.items()) if (since is None or d >= since) and (until is None or d <= until)]

    def index_names(self):  # noqa: ANN201
        return [self._index_name] if self._index is not None else []

    def index_close(self, name: str):  # noqa: ANN201
        return self._index or {}

    def actions(self, symbol: str):  # noqa: ANN201
        return self._actions


class Evidence:
    def __init__(self, rows=()):  # noqa: ANN001
        self.rows = list(rows)

    def claims_for(self, symbol: str, since: datetime, until: datetime):  # noqa: ANN201
        return [r for r in self.rows if since <= r["published_at"] <= until]


def claim(story: str, published: datetime, outcome: str = "unchecked", seen: datetime | None = None, title: str = "Demo profit up 20 percent") -> dict:
    return {"claim_id": story + str(published), "item_id": "i", "source_id": "s", "story_id": story, "kind": "results", "text": title, "fields": {}, "published_at": published, "publisher": "Press", "link": "", "title": title,
            "first_seen_at": seen or published, "outcome": outcome, "detail": "net profit was 12 percent", "ours": {}, "checked_at": None}


def ctx(tmp_path, as_of: date) -> ToolContext:  # noqa: ANN001
    return ToolContext(Case("c", "listed", "DEMO", as_of), ForensicStore(tmp_path / "f.duckdb"), "market")


def run(tmp_path, name: str, warehouse: Warehouse, as_of: date, evidence: Evidence | None = None, **args):  # noqa: ANN001, ANN003, ANN201
    registry = base_registry(market_tools(MarketData(lambda: warehouse, lambda: evidence or Evidence())))
    return registry.call(name, args, ctx(tmp_path, as_of))[0]


DAYS = weekdays(80)
RISING = {d: 100.0 + i for i, d in enumerate(DAYS)}  # 100 on the first day, 179 on the 80th
AS_OF = DAYS[-1]


def test_the_summary_works_out_return_drawdown_volume_and_the_move_against_the_index(tmp_path) -> None:  # noqa: ANN001
    closes = dict(RISING)
    closes[DAYS[40]] = 120.0  # a drop from 140 to 120 and then back on trend
    volumes = {d: 1000.0 for d in DAYS}
    volumes[DAYS[-1]] = 3000.0
    index = {d: 1000.0 + 5 * i for i, d in enumerate(DAYS)}  # 1000 to 1395: +39.5 percent
    out = run(tmp_path, "mkt.summary", Warehouse(closes, volumes, index=index), AS_OF, days=200)
    assert out["first_close"] == 100.0 and out["last_close"] == 179.0 and out["return_percent"] == 79.0
    assert out["index_return_percent"] == 39.5 and out["relative_to_index_points"] == 39.5
    assert out["max_drawdown_percent"] == pytest.approx((120.0 / 139.0 - 1.0) * 100.0, abs=0.001) and out["drawdown_trough_day"] == DAYS[40].isoformat()
    assert out["median_daily_volume"] == 1000.0 and out["last_volume_vs_median"] == 3.0
    assert out["price_breaks_in_window"] == [] and out["trading_days"] == 80


def test_an_index_held_under_another_case_is_found_and_a_missing_index_only_removes_the_comparison(tmp_path) -> None:  # noqa: ANN001
    named = run(tmp_path, "mkt.summary", Warehouse(RISING, index={d: 1.0 + i for i, d in enumerate(DAYS)}, index_name="NIFTY 50"), AS_OF, days=200)
    assert named["index_return_percent"] is not None
    none = run(tmp_path, "mkt.summary", Warehouse(RISING), AS_OF, days=200)
    assert none["index_return_percent"] is None and none["relative_to_index_points"] is None and none["return_percent"] == 79.0


def test_a_split_in_the_window_breaks_the_series_and_returns_are_taken_from_its_ex_date(tmp_path) -> None:  # noqa: ANN001
    closes = {d: (100.0 + i if i < 40 else (100.0 + i) / 2) for i, d in enumerate(DAYS)}  # a two-for-one split goes ex on the 41st day
    split = Action(DAYS[40], "split", "Face value split from Rs 10 to Rs 5")
    out = run(tmp_path, "mkt.summary", Warehouse(closes, actions=[split]), AS_OF, days=200)
    assert out["from"] == DAYS[40].isoformat() and out["first_close"] == 70.0 and out["last_close"] == 89.5
    assert out["returns_taken_from_after_break"] is True and out["price_breaks_in_window"][0]["kind"] == "split"
    assert out["max_drawdown_percent"] == 0.0  # the fall from 139 to 70 on the ex-date is not a drawdown
    moves = run(tmp_path, "mkt.moves", Warehouse(closes, actions=[split]), AS_OF, days=200, k=3)
    assert DAYS[40].isoformat() in moves["ex_dates_skipped"] and all(m["day"] != DAYS[40].isoformat() for m in moves["largest"])


def test_the_largest_moves_come_with_the_volume_against_the_usual_and_the_index_that_day(tmp_path) -> None:  # noqa: ANN001
    closes = dict(RISING)
    for d in DAYS[60:]:
        closes[d] = RISING[d] * 0.9  # a one-off fall of ten percent on the 61st day
    volumes = {d: 1000.0 for d in DAYS}
    volumes[DAYS[60]] = 5000.0
    index = {d: 1000.0 for d in DAYS}
    out = run(tmp_path, "mkt.moves", Warehouse(closes, volumes, index=index), AS_OF, days=200, k=2)
    first = out["largest"][0]
    assert first["day"] == DAYS[60].isoformat() and first["close_to_close_percent"] == pytest.approx((RISING[DAYS[60]] * 0.9 / RISING[DAYS[59]] - 1.0) * 100.0, abs=0.001)
    assert first["volume_vs_prior_20_day_median"] == 5.0 and first["index_percent"] == 0.0 and first["relative_to_index_points"] == first["close_to_close_percent"]
    assert len(out["largest"]) == 2


def test_around_a_date_the_return_is_taken_over_trading_days_and_nothing_later_than_the_day_is_used(tmp_path) -> None:  # noqa: ANN001
    wh = Warehouse(RISING, index={d: 1000.0 for d in DAYS})
    out = run(tmp_path, "mkt.around", wh, AS_OF, day=DAYS[30].isoformat(), before=5, after=10)
    assert out["from"] == DAYS[25].isoformat() and out["to"] == DAYS[40].isoformat() and out["return_percent"] == round((RISING[DAYS[40]] / RISING[DAYS[25]] - 1.0) * 100.0, 3)
    assert out["index_percent"] == 0.0
    with pytest.raises(ToolError, match="not enough bars after"):
        run(tmp_path, "mkt.around", wh, DAYS[50], day=DAYS[48].isoformat(), after=10)
    with pytest.raises(ToolError, match="after the as-of day"):
        run(tmp_path, "mkt.around", wh, DAYS[50], day=DAYS[60].isoformat())
    split = Action(DAYS[32], "bonus")
    with pytest.raises(ToolError, match="bonus went ex"):
        run(tmp_path, "mkt.around", Warehouse(RISING, actions=[split]), AS_OF, day=DAYS[30].isoformat(), before=5, after=10)


def test_too_few_bars_is_a_clear_error(tmp_path) -> None:  # noqa: ANN001
    short = {d: 100.0 for d in DAYS[:10]}
    with pytest.raises(ToolError, match="at least 20 are needed"):
        run(tmp_path, "mkt.summary", Warehouse(short), DAYS[9])


def test_claims_are_dated_by_when_they_were_first_seen_copies_count_once_and_the_outcome_is_shown(tmp_path) -> None:  # noqa: ANN001
    day = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    ev = Evidence([
        claim("s1", day, "confirmed"), claim("s1", day + timedelta(hours=1), "confirmed", title="A copy of the same story"),
        claim("s2", day + timedelta(days=2), "contradicted"),
        claim("s3", day + timedelta(days=3), "unchecked", seen=datetime(2026, 12, 1, tzinfo=UTC)),  # first seen after the as-of day: not known then
    ])
    out = run(tmp_path, "mkt.claims", Warehouse(RISING), date(2026, 4, 1), evidence=ev, days=90)
    assert out["claims_read"] == 3 and out["independent_stories"] == 2 and out["by_outcome"] == {"confirmed": 2, "contradicted": 1}
    assert {c["outcome"] for c in out["latest"]} == {"confirmed", "contradicted"} and "unchecked" in out["limits"]


class Script:
    def __init__(self, replies):  # noqa: ANN001
        self.replies = list(replies)

    def complete(self, system: str, user: str) -> str:
        if not self.replies:
            raise AthenaError("script used up")
        r = self.replies.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


def test_a_supervised_run_cites_the_summary_and_a_number_not_in_it_is_refused(tmp_path) -> None:  # noqa: ANN001
    store = ForensicStore(tmp_path / "forensic.duckdb")
    registry = base_registry(market_tools(MarketData(lambda: Warehouse(RISING), lambda: Evidence())))
    good = {"action": "write", "kind": "OBSERVATION", "text": "the share rose 79.0 percent over the window", "evidence": ["E1"], "values": {"return": 79.0}}
    made_up = {"action": "write", "kind": "OBSERVATION", "text": "the share rose 140 percent over the window", "evidence": ["E1"], "values": {"return": 140.0}}
    llm = Script([{"action": "tool", "tool": "mkt.summary", "args": {"days": 200}}, good, made_up, {"action": "finish", "summary": "read the price"}, "one observation, one refused"])
    report = Supervisor(store, registry, {"market": MARKET_LANE}, llm, total_calls=8, max_rounds=1).investigate(Case("mkt-1", "listed", "DEMO", AS_OF))
    assert report["lanes"]["market"][0]["rejected"] == 1
    kept = [e.text for e in store.entries("mkt-1", kind="OBSERVATION") if e.status != "rejected"]
    assert any("79.0" in t for t in kept) and not any("140" in t for t in kept)
