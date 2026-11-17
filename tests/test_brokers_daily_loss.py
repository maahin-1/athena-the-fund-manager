"""Unmeasurable daily-loss protection is reported as UNAVAILABLE, never as passed (WP03, T2), and market hours use the exchange's holidays with an explicit answer when they are not known.
Fake and paper adapters only; no account, no login, no order is sent anywhere."""
import json
from datetime import date, datetime, timezone

import pytest

from athena.brokers import safety
from athena.brokers.audit import AuditLog
from athena.brokers.desk import Desk, Refused
from athena.brokers.model import Findings, Price
from athena.trading_calendar import TradingCalendar

from test_brokers import CHARGES, MONDAY_10AM, Clock, make, req, rules  # noqa: E402
from test_brokers_live_gate import KEY, LiveFake  # noqa: E402


def live(tmp_path, enforcement="enforced", calendar=TradingCalendar(frozenset()), human=lambda phrase, summary: True, clock=None):
    clock = clock or Clock()
    quote = lambda s, e: Price(100.0, 99.9, 100.1, "stream", 0.5)  # noqa: E731
    broker = LiveFake(quote, tmp_path / "p.json", 100_000.0, CHARGES, clock)
    limits = safety.Limits(mode="live_delivery", broker="groww", allowlist=("SBIN",), daily_loss_enforcement=enforcement)
    desk = Desk(broker, limits, quote, AuditLog(tmp_path / "a.jsonl", clock, key=KEY), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES, human=human, limits_pinned=lambda: True, calendar=calendar)
    return desk, broker


def test_findings_can_carry_an_unavailable_protection_that_is_never_a_pass():
    f = Findings()
    f.add_unavailable("daily loss", "cannot be measured", blocking=False)
    assert f.ok and f.items == [] and f.unavailable == [("daily loss", "cannot be measured")]  # not among the passed rules
    f.add_unavailable("daily loss", "cannot be measured", blocking=True)
    assert not f.ok and f.breaches[0][0] == "daily loss" and f.breaches[0][2].startswith("UNAVAILABLE")


def test_a_live_order_is_refused_by_default_because_the_daily_loss_limit_cannot_be_measured(tmp_path):
    desk, broker = live(tmp_path)
    view = desk.preview(req())
    assert not view.ok and view.token == ""
    breach = [(r, t) for r, passed, t in view.findings.items if not passed]
    assert [r for r, _ in breach] == ["daily loss"] and breach[0][1].startswith("UNAVAILABLE") and "cannot be measured or enforced" in breach[0][1]
    assert not any(r == "daily loss" and passed for r, passed, _ in view.findings.items)  # never displayed as passed
    assert any("BREACH daily loss" in line for line in view.lines())
    assert broker.orders() == []


def test_the_owner_can_accept_advisory_only_protection_and_it_is_still_shown_as_unavailable_not_ok(tmp_path):
    desk, broker = live(tmp_path, enforcement="advisory")
    view = desk.preview(req())
    assert view.ok and view.token
    assert view.findings.unavailable and view.findings.unavailable[0][0] == "daily loss" and "NOT enforced" in view.findings.unavailable[0][1]
    assert not any(r == "daily loss" for r, _, _ in view.findings.items)  # not listed among the checks that passed
    text = "\n".join(view.lines())
    assert "UNAVAIL daily loss" in text and "ok     daily loss" not in text
    row = [d for e, d in ((r["event"], r["data"]) for r in desk.audit.rows()) if e == "preview"][0]
    assert row["unavailable"] == [f"daily loss: {view.findings.unavailable[0][1]}"]  # the audit log records what was not enforced
    assert desk.confirm(view.token).status == "filled"  # the order itself is allowed: the owner chose advisory


def test_the_default_still_refuses_at_confirm_even_if_the_order_was_previewed_under_looser_limits(tmp_path):
    advisory, _ = live(tmp_path / "x", enforcement="advisory")
    token = advisory.preview(req()).token
    strict, broker = live(tmp_path / "x", enforcement="enforced")
    strict.tokens = advisory.tokens  # the same token store: only the limits differ at confirm
    with pytest.raises(Refused, match="daily loss"):
        strict.confirm(token)
    assert broker.orders() == []


def test_a_live_brokers_per_order_figures_are_never_turned_into_a_loss_measure(tmp_path):
    desk, broker = live(tmp_path, enforcement="advisory")
    placed = broker.place(req(qty=1, price=101.0), "c1")
    placed.realised = -50_000.0  # a figure no real broker reports per order; it must not become a "measured" loss
    found = desk._findings(req())
    assert found.unavailable and found.unavailable[0][0] == "daily loss" and not any(r == "daily loss" for r, _, _ in found.items)


def test_paper_trading_keeps_its_measured_daily_loss_limit_exactly_as_before(tmp_path):
    desk, broker, book, _ = make(tmp_path, limits=safety.Limits(max_daily_loss=50.0, max_order_value=50_000.0))
    broker.place(req("BUY", 100, 101.0), "c1")
    book["price"] = Price(90.0, 90.0, 90.1)
    broker.place(req("SELL", 100, 89.0), "c2")
    found = desk._findings(req("BUY", 1, 90.0))
    assert "daily loss" in rules(found) and found.unavailable == []  # measured, and breached


def test_the_limit_names_its_enforcement_and_the_file_loader_checks_it(tmp_path):
    assert safety.Limits().daily_loss_enforcement == "enforced"
    with pytest.raises(safety.LimitsError, match="daily_loss_enforcement"):
        safety.Limits(daily_loss_enforcement="sometimes")
    path = tmp_path / "broker.json"
    path.write_text(json.dumps({"mode": "paper", "daily_loss_enforcement": "advisory"}))
    assert safety.load_limits(path).daily_loss_enforcement == "advisory"


# ---- market hours and the exchange calendar
def test_an_exchange_holiday_closes_the_market_even_on_a_weekday(tmp_path):
    desk, _, _, clock = make(tmp_path)
    desk._calendar = TradingCalendar(frozenset({date(2026, 10, 26)}))  # the Monday in these tests is a holiday
    found = desk._findings(req())
    assert rules(found) == {"trading hours"} and "exchange holiday" in [t for r, p, t in found.items if r == "trading hours"][0]
    desk._calendar = TradingCalendar(frozenset())
    assert rules(desk._findings(req())) == set()


def test_with_no_calendar_paper_trading_uses_weekdays_and_says_the_holidays_are_not_known(tmp_path):
    desk, _, _, _ = make(tmp_path)
    found = desk._findings(req())
    text = [t for r, p, t in found.items if r == "trading hours"][0]
    assert rules(found) == set() and "holiday calendar not loaded" in text


def test_with_no_calendar_a_live_order_is_refused_because_an_open_market_cannot_be_confirmed(tmp_path):
    desk, broker = live(tmp_path, enforcement="advisory", calendar=None)
    view = desk.preview(req())
    assert not view.ok and [r for r, passed, _ in view.findings.items if not passed] == ["trading hours"]
    assert "cannot be confirmed" in [t for r, p, t in view.findings.items if r == "trading hours"][0]


def test_the_holiday_file_loader_reads_dates_and_a_missing_file_means_unknown(tmp_path):
    path = tmp_path / "holidays.json"
    assert safety.load_holidays(path) is None
    path.write_text(json.dumps({"dates": ["2026-10-02", "2026-11-10"]}))
    cal = safety.load_holidays(path)
    assert not cal.is_trading_day(date(2026, 10, 2)) and cal.is_trading_day(date(2026, 10, 5))
    path.write_text("{not json")
    assert safety.load_holidays(path) is None  # an unreadable file is an unknown calendar, not an empty one
