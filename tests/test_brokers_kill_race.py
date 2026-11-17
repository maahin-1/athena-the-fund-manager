"""The kill switch against a confirmation in progress (WP03, T1). Paper and fake adapters only; threads are released by events, nothing sleeps for real.

The protocol under test: a confirmation writes a SENDING marker, THEN takes its last look at the kill switch, then sends; `kill()` writes KILL, THEN looks at the marker. Whichever comes first, one of
them sees the other, so an order is never sent after kill() has reported that nothing is in flight, and an order already in transit is reconciled (cancelled, filled or explicitly unresolved)."""
import json
import threading

import pytest

from athena.brokers import safety
from athena.brokers.audit import AuditLog
from athena.brokers.desk import BrokerError, Desk, Refused
from athena.brokers.model import Price
from athena.trading_calendar import TradingCalendar

from test_brokers import CHARGES, Clock, make, req  # noqa: E402


def events(desk):
    return [(r["event"], r["data"]) for r in desk.audit.rows()]


def names(desk):
    return [e for e, _ in events(desk)]


def fast(desk, sleeps=None):
    """No real waiting: the desk's polling sleep records instead of sleeping."""
    desk._sleep = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    return desk


def hook_audit(desk, event, action):
    """Run `action` just before the audit line `event` is written (the line is still written after)."""
    real = desk.audit.append

    def wrapped(name, **data):
        if name == event:
            action()
        return real(name, **data)

    desk.audit.append = wrapped


def test_a_kill_requested_just_before_the_send_stops_the_send_and_says_what_it_could_not_confirm(tmp_path):
    # the audit "placing" line is written after the confirmation's check and before the send: kill() lands exactly there
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    report = {}

    def kill_now():
        try:
            desk.kill()
        except BrokerError as exc:
            report["error"] = str(exc)

    hook_audit(desk, "placing", kill_now)
    view = desk.preview(req())
    with pytest.raises(Refused, match="kill switch"):
        desk.confirm(view.token)
    assert broker.orders() == [] and (tmp_path / "KILL").exists()  # no order was sent after the switch was on
    assert "in progress" in report["error"] and "send_aborted" in names(desk) and not (tmp_path / "SENDING").exists()
    killed = [d for e, d in events(desk) if e == "kill"][0]
    assert killed["unresolved"] and killed["in_transit"] is True  # kill() could not see the send finish, and said so rather than claiming it was clear


def test_a_kill_that_lands_during_the_send_waits_for_it_then_reports_the_order_as_filled_not_as_cancelled(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    in_place, release, kill_waiting = threading.Event(), threading.Event(), threading.Event()
    real_place = broker.place

    def slow_place(request, client_id):
        in_place.set()  # the order is in transit
        assert release.wait(5), "the test never released the send"
        return real_place(request, client_id)

    broker.place = slow_place

    def sleeper(seconds):
        kill_waiting.set()  # kill() is now polling for the send to finish
        threading.Event().wait(0.001)

    desk._sleep = sleeper
    view = desk.preview(req())
    result = {}
    sender = threading.Thread(target=lambda: result.setdefault("order", desk.confirm(view.token)))
    sender.start()
    assert in_place.wait(5)

    def do_kill():
        result["killed"] = desk.kill()

    killer = threading.Thread(target=do_kill)
    killer.start()
    assert kill_waiting.wait(5)
    assert "killed" not in result  # kill has not finished: it is waiting for the send, not racing it
    release.set()
    sender.join(5)
    killer.join(5)
    assert result["order"].status == "filled" and len(broker.orders()) == 1
    killed = result["killed"]
    assert killed == [] and killed.filled_in_transit == [result["order"].order_id] and killed.unresolved == []  # filled while it was being killed: reported, not undone
    assert "a file cannot undo a fill" in killed.message()
    assert (tmp_path / "KILL").exists() and not (tmp_path / "SENDING").exists()


def test_a_kill_while_a_person_is_still_typing_returns_at_once_and_the_later_send_is_refused(tmp_path):
    clock = Clock()
    quote = lambda s, e: Price(100.0, 99.9, 100.1, "stream", 0.5)  # noqa: E731
    from test_brokers_live_gate import KEY, LiveFake  # noqa: E402

    broker = LiveFake(quote, tmp_path / "p.json", 100_000.0, CHARGES, clock)
    typing, finish_typing = threading.Event(), threading.Event()

    def person(phrase, summary):
        typing.set()
        assert finish_typing.wait(5)
        return True

    limits = safety.Limits(mode="live_delivery", broker="groww", allowlist=("SBIN",), daily_loss_enforcement="advisory")
    desk = Desk(broker, limits, quote, AuditLog(tmp_path / "a.jsonl", clock, key=KEY), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES, human=person, limits_pinned=lambda: True,
                calendar=TradingCalendar(frozenset()))
    fast(desk)
    view = desk.preview(req())
    outcome = {}

    def confirm():
        try:
            outcome["order"] = desk.confirm(view.token)
        except Refused as exc:
            outcome["refused"] = str(exc)

    t = threading.Thread(target=confirm)
    t.start()
    assert typing.wait(5)  # the desk lock is held and the person has not typed yet
    assert desk.kill() == []  # not blocked by the held lock, and no send is in transit
    assert (tmp_path / "KILL").exists()
    finish_typing.set()
    t.join(5)
    assert "refused" in outcome and "kill switch" in outcome["refused"] and broker.orders() == []


def test_a_normal_confirmation_and_a_definitely_refused_send_both_leave_no_sending_marker(tmp_path):
    # A send the broker answered with a refusal (HTTP 4xx) is known not to have placed an order. A failure with no such answer is NOT like that: see test_brokers_send_outcome.py (batch-2 review B2-1).
    desk, broker, _, _ = make(tmp_path)
    assert desk.confirm(desk.preview(req()).token).status == "filled" and not (tmp_path / "SENDING").exists()

    class Refusal(Exception):
        status = 400

    def refused(request, client_id):
        raise Refusal("HTTP 400")

    broker.place = refused
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req(qty=2)).token)
    assert not (tmp_path / "SENDING").exists() and "place_failed" in names(desk)


def test_a_send_that_never_finished_in_a_process_that_has_gone_blocks_new_orders_and_resume_until_a_person_resolves_it(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    (tmp_path / "SENDING").write_text(json.dumps({"pid": 2_000_000_000, "client_id": "ath-abc123-xyz", "symbol": "SBIN", "side": "BUY", "quantity": 5, "since": "2026-10-26T04:30:00+00:00"}))  # the process died mid-send
    view = desk.preview(req())
    with pytest.raises(Refused, match="earlier send"):
        desk.confirm(view.token)
    assert broker.orders() == [] and (tmp_path / "SENDING").exists()  # nothing was sent, and the unresolved record is kept
    with pytest.raises(BrokerError, match="unresolved"):
        desk.kill()  # kill cannot say that nothing is in flight: it reports the unknown send, and trading stays off
    assert (tmp_path / "KILL").exists()
    with pytest.raises(Refused, match="unresolved send"):
        desk.resume()
    assert (tmp_path / "KILL").exists()
    desk.resolve_send("checked the broker's order list: nothing was sent")
    assert not (tmp_path / "SENDING").exists() and "send_resolved" in names(desk)
    desk.resume()
    assert not (tmp_path / "KILL").exists()
    assert desk.confirm(desk.preview(req()).token).status == "filled"


def test_an_order_that_cannot_be_cancelled_is_reported_failed_the_others_are_still_cancelled_and_trading_stays_off(tmp_path):
    desk, broker, book, _ = make(tmp_path, cash=100_000.0)
    fast(desk)
    first = broker.place(req(qty=1, price=95.0), "c1")
    second = broker.place(req(qty=1, price=94.0), "c2")
    real_cancel = broker.cancel

    def cancel(order_id):
        if order_id == first.order_id:
            raise RuntimeError("it has just filled")
        return real_cancel(order_id)

    broker.cancel = cancel
    with pytest.raises(BrokerError, match="could not be cancelled"):
        desk.kill()
    states = {o.order_id: o.status for o in broker.orders()}
    assert states[second.order_id] == "cancelled" and (tmp_path / "KILL").exists()
    killed = [d for e, d in events(desk) if e == "kill"][0]
    assert killed["cancelled"] == [second.order_id] and killed["failed"] and killed["unresolved"] == []


def test_a_partial_or_late_fill_is_reported_among_the_orders_found_not_hidden(tmp_path):
    desk, broker, _, _ = make(tmp_path, cash=100_000.0)
    fast(desk)
    resting = broker.place(req(qty=10, price=95.0), "c1")
    assert resting.status == "open"
    assert desk.kill() == [resting.order_id] and desk.kill() == []  # a second kill is harmless


def test_a_live_adapters_orders_of_unknown_outcome_make_the_kill_unresolved_and_trading_stays_off(tmp_path):
    from test_brokers_live_gate import LiveFake  # noqa: E402

    clock = Clock()
    quote = lambda s, e: Price(100.0, 99.9, 100.1, "stream", 0.5)  # noqa: E731
    broker = LiveFake(quote, tmp_path / "p.json", 100_000.0, CHARGES, clock)
    broker.unresolved = lambda: {"ath-abc-123": {"request": "BUY 5 SBIN"}}  # the adapter sent an order and never got the answer
    desk = Desk(broker, safety.Limits(), quote, AuditLog(tmp_path / "a.jsonl", clock), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES, calendar=TradingCalendar(frozenset()))
    fast(desk)
    with pytest.raises(BrokerError, match="unresolved") as raised:
        desk.kill()
    assert "ath-abc-123 (broker answer lost)" in str(raised.value) and (tmp_path / "KILL").exists()
    assert [d for e, d in events(desk) if e == "kill"][0]["unresolved"] == ["ath-abc-123 (broker answer lost)"]


def test_kill_with_no_send_in_flight_is_unchanged_for_callers_that_only_want_the_ids(tmp_path):
    desk, broker, _, _ = make(tmp_path, cash=100_000.0)
    fast(desk)
    o = broker.place(req(qty=1, price=95.0), "c1")
    ids = desk.kill()
    assert ids == [o.order_id] and isinstance(ids, list) and ids.unresolved == [] and ids.filled_in_transit == [] and ids.message().startswith("trading is off")


def test_the_marker_is_written_before_the_last_look_at_the_kill_switch(tmp_path):
    # the order of operations is the whole protocol: marker, then the kill check, then the send
    desk, _, _, _ = make(tmp_path)
    seen = []
    real_look = desk._kill_is_set

    def probe():
        seen.append("marker" if (tmp_path / "SENDING").exists() else "no marker")
        return real_look()

    desk._kill_is_set = probe
    desk.confirm(desk.preview(req()).token)
    assert seen == ["marker", "marker"]  # the look before the audit line and the look right before the send both happen with the marker already in place
