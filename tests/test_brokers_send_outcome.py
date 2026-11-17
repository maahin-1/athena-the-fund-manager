"""Batch-2 review B2-1 and B2-2: what the desk may not forget about a send whose outcome is unknown, and what `kill()` may call cancelled. Paper broker and injected fakes only: nothing here reaches a
network or a broker, and no sleep is real.

B2-1  A send that fails after it may have reached the broker leaves a durable record. `resume()` and a new send are refused until a person reconciles it (`resolve_send`), whether the record is the
      desk's own SENDING marker or the adapter's unresolved ledger, and a ledger that cannot be read cannot establish that nothing is unknown. A definite refusal by the broker (HTTP 4xx) is the one
      failure that resolves itself.
B2-2  `kill()` reports an order as cancelled only when the broker's own state says cancelled. A request the broker acknowledged but did not carry out, a fill that raced the cancel, a partial fill,
      an order that vanished and a lookup that failed are each reported as what they are, never as cancelled."""
import json

import pytest

from athena.brokers import safety
from athena.brokers.audit import AuditLog
from athena.brokers.desk import BrokerError, Desk, Refused
from athena.brokers.model import Price
from athena.brokers.paper import PaperBroker

from test_brokers import CHARGES, Clock, make, req  # noqa: E402


def names(desk):
    return [r["event"] for r in desk.audit.rows()]


def fast(desk, sleeps=None):
    desk._sleep = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    return desk


class Ledgered(PaperBroker):
    """A paper broker that, like a live adapter, keeps a ledger of sends whose answer was lost, and can be told how `place` fails."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ledger: dict[str, dict] = {}
        self.read_fails = False
        self.fail = None  # None, or a callable(request, client_id) run in place of the send
        self.placed_for_real = 0

    def place(self, request, client_id):
        if self.fail:
            return self.fail(self, request, client_id)
        self.placed_for_real += 1
        return super().place(request, client_id)

    def unresolved(self):
        if self.read_fails:
            raise ValueError("the ledger file is damaged")
        return dict(self.ledger)

    def resolve(self, client_id):
        return self.ledger.pop(client_id, None) is not None


def lost_answer(broker, request, client_id):
    broker.ledger[client_id] = {"request": request.to_dict()}  # sent; the answer never came back
    raise TimeoutError("read timed out")


def accepted_then_timed_out(broker, request, client_id):
    PaperBroker.place(broker, request, client_id)  # the order really is on the book
    raise TimeoutError("read timed out after the broker accepted it")


class Refused4xx(Exception):
    status = 400


def definite_refusal(broker, request, client_id):
    raise Refused4xx("HTTP 400 insufficient funds")


def ledgered_desk(tmp_path):
    clock = Clock()
    quote = lambda s, e: Price(100.0, 99.9, 100.1)  # noqa: E731
    broker = Ledgered(quote, tmp_path / "paper.json", 100_000.0, CHARGES, clock)
    desk = Desk(broker, safety.Limits(), quote, AuditLog(tmp_path / "a.jsonl", clock), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES)
    return fast(desk), broker, quote, clock


def reopened(tmp_path, broker, quote, clock):
    """The same files after a restart: a new Desk object with nothing in memory."""
    return fast(Desk(broker, safety.Limits(), quote, AuditLog(tmp_path / "a.jsonl", clock), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES))


def marker(tmp_path):
    return json.loads((tmp_path / "SENDING").read_text())


# =============================================== B2-1 ===============================================
def test_a_lost_acknowledgement_keeps_the_unknown_send_on_record_and_resume_is_refused(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = lost_answer
    with pytest.raises(BrokerError, match="TimeoutError"):
        desk.confirm(desk.preview(req()).token)
    assert broker.ledger and (tmp_path / "SENDING").exists()  # the desk did not forget it
    assert marker(tmp_path)["state"] == "outcome_unknown" and "TimeoutError" in marker(tmp_path)["error"]
    with pytest.raises(BrokerError, match="unresolved"):
        desk.kill()
    with pytest.raises(Refused, match="unresolved"):
        desk.resume()
    assert (tmp_path / "KILL").exists()  # trading stays off


def test_the_reviewers_reproduction_no_longer_resumes_with_an_unknown_send(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = lost_answer
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req()).token)
    with pytest.raises(BrokerError):
        desk.kill()
    unknown_remains, sending_remains = bool(broker.ledger), (tmp_path / "SENDING").exists()
    with pytest.raises(Refused):
        desk.resume()
    assert unknown_remains and sending_remains and (tmp_path / "KILL").exists()


def test_a_timeout_after_acceptance_with_no_ledger_entry_is_still_treated_as_unknown(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = accepted_then_timed_out
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req()).token)
    assert len(broker.orders()) == 1 and (tmp_path / "SENDING").exists()  # the order is there; the desk did not hear so, and says so by refusing to go on
    broker.fail = None
    with pytest.raises(Refused, match="earlier send"):
        desk.confirm(desk.preview(req(qty=2)).token)
    assert len(broker.orders()) == 1


def test_a_failure_writing_the_post_send_audit_line_keeps_the_record_and_says_the_order_exists(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    real = desk.audit.append

    def flaky(name, **data):
        if name == "placed":
            raise OSError("disk full")
        return real(name, **data)

    desk.audit.append = flaky
    view = desk.preview(req())
    with pytest.raises(BrokerError, match="P000001") as raised:
        desk.confirm(view.token)
    assert "audit" in str(raised.value) and len(broker.orders()) == 1  # the order was placed; only its log line failed
    assert marker(tmp_path)["state"] == "placed_unlogged" and marker(tmp_path)["order_id"] == "P000001"
    desk.audit.append = real
    with pytest.raises(Refused, match="earlier send"):
        desk.confirm(desk.preview(req(qty=2)).token)
    with pytest.raises(Refused, match="unresolved"):
        desk.resume()


def test_after_a_restart_the_unknown_outcome_still_blocks_resume_and_new_sending(tmp_path):
    desk, broker, quote, clock = ledgered_desk(tmp_path)
    broker.fail = lost_answer
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req()).token)
    broker.fail = None
    again = reopened(tmp_path, broker, quote, clock)
    with pytest.raises(Refused):
        again.resume()
    with pytest.raises(Refused):
        again.confirm(again.preview(req(qty=2)).token)
    assert broker.placed_for_real == 0


def test_the_adapters_ledger_alone_blocks_a_new_order_with_a_different_fingerprint(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.ledger["ath-old-1"] = {"request": "BUY 5 SBIN"}  # no desk marker at all: a ledger left by an earlier run
    view = desk.preview(req(qty=7, price=102.0))  # a different order
    assert not view.ok and view.findings.breaches[0][0] == "no unresolved send" and "ath-old-1" in view.findings.breaches[0][2] and view.token == ""
    with pytest.raises(Refused):
        desk.confirm(desk.tokens.issue(req(qty=7, price=102.0), "paper")[0])  # even a token issued around the preview gate cannot send
    assert broker.placed_for_real == 0
    with pytest.raises(Refused, match="unresolved"):
        desk.resume()


def test_a_ledger_that_cannot_be_read_cannot_show_that_nothing_is_unknown(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.read_fails = True
    assert not desk.preview(req()).ok
    with pytest.raises(Refused, match="could not be read"):
        desk.resume()
    with pytest.raises(BrokerError, match="could not be read"):
        desk.kill()


def test_resolve_send_clears_the_marker_and_the_adapters_ledger_together_and_audits_it(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = lost_answer
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req()).token)
    with pytest.raises(BrokerError):
        desk.kill()
    broker.fail = None
    with pytest.raises(Refused, match="what you checked"):
        desk.resolve_send(" ")
    assert (tmp_path / "SENDING").exists() and broker.ledger  # a blank note resolved nothing
    desk.resolve_send("looked at the broker's order list: the order is not there")
    assert not (tmp_path / "SENDING").exists() and broker.ledger == {}
    event = [r for r in desk.audit.rows() if r["event"] == "send_resolved"][0]["data"]
    assert event["earlier"]["state"] == "outcome_unknown" and len(event["resolved_ledger"]) == 1
    desk.resume()
    assert desk.confirm(desk.preview(req()).token).status == "filled"


def test_resolve_send_refuses_while_the_ledger_cannot_be_read_and_changes_nothing(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = lost_answer
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req()).token)
    broker.read_fails = True
    with pytest.raises(Refused, match="could not be read"):
        desk.resolve_send("checked")
    assert (tmp_path / "SENDING").exists() and "send_resolved" not in names(desk)


def test_a_definite_refusal_by_the_broker_leaves_nothing_to_reconcile(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = definite_refusal
    with pytest.raises(BrokerError, match="400"):
        desk.confirm(desk.preview(req()).token)
    assert not (tmp_path / "SENDING").exists() and [r for r in desk.audit.rows() if r["event"] == "place_failed"][0]["data"]["outcome"] == "refused"
    broker.fail = None
    assert desk.confirm(desk.preview(req()).token).status == "filled"


def test_a_normal_send_and_a_pre_send_refusal_still_leave_no_marker(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    assert desk.confirm(desk.preview(req()).token).status == "filled" and not (tmp_path / "SENDING").exists()
    view = desk.preview(req(qty=2))
    (tmp_path / "KILL").write_text("x")
    with pytest.raises(Refused):
        desk.confirm(view.token)
    assert not (tmp_path / "SENDING").exists()


def test_an_interrupt_during_the_send_keeps_the_marker(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)

    def interrupted(b, r, c):
        raise KeyboardInterrupt

    broker.fail = interrupted
    with pytest.raises(KeyboardInterrupt):
        desk.confirm(desk.preview(req()).token)
    assert (tmp_path / "SENDING").exists()  # the outcome of a send that was cut short is not known


def test_kill_does_not_wait_for_a_send_that_has_already_ended_unknown(tmp_path):
    desk, broker, _, _ = ledgered_desk(tmp_path)
    broker.fail = lost_answer
    with pytest.raises(BrokerError):
        desk.confirm(desk.preview(req()).token)
    sleeps = []
    fast(desk, sleeps)
    with pytest.raises(BrokerError, match="unresolved"):
        desk.kill()
    assert sleeps == []  # the process is alive but nothing is in flight: it is not polled for 20 seconds


# =============================================== B2-2 ===============================================
def resting(desk, broker, *prices):
    return [broker.place(req(qty=1, price=p), f"c{i}") for i, p in enumerate(prices)]


def test_a_cancel_the_broker_acknowledged_but_did_not_carry_out_is_failed_not_cancelled(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    first, = resting(desk, broker, 95.0)
    broker.cancel = lambda order_id: broker.orders()[0]  # the request is accepted and nothing happens
    with pytest.raises(BrokerError, match="could not be cancelled"):
        desk.kill()
    killed = [r["data"] for r in desk.audit.rows() if r["event"] == "kill"][0]
    assert killed["cancelled"] == [] and killed["unresolved"] == [] and any(first.order_id in f and "still open" in f for f in killed["failed"])
    assert [o.status for o in broker.orders()] == ["open"] and (tmp_path / "KILL").exists()


def test_a_cancel_that_lands_on_a_later_poll_is_confirmed_by_the_poll(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    sleeps = []
    fast(desk, sleeps)
    first, = resting(desk, broker, 95.0)
    real_cancel, real_orders, calls = broker.cancel, broker.orders, {"n": 0}

    def slow_cancel(order_id):
        return real_orders()[0]  # acknowledged, not yet done

    def orders():
        calls["n"] += 1
        got = real_orders()
        if calls["n"] >= 3:  # the third look at the book finds it cancelled
            real_cancel(first.order_id)
            got = real_orders()
        return got

    broker.cancel, broker.orders = slow_cancel, orders
    report = desk.kill()
    assert report == [first.order_id] and report.failed == [] and report.unresolved == [] and sleeps  # it waited for the book to agree, and only then called it cancelled


def test_an_order_that_filled_while_the_cancel_was_in_flight_is_reported_as_filled_not_cancelled(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    first, second = resting(desk, broker, 95.0, 94.0)
    real_cancel = broker.cancel

    def cancel(order_id):
        if order_id == first.order_id:
            order = broker.orders()[0]
            order.status, order.filled_quantity, order.average_price = "filled", order.request.quantity, 95.0
            return order
        return real_cancel(order_id)

    broker.cancel = cancel
    report = desk.kill()
    assert report == [second.order_id] and report.filled_during_cancel == [first.order_id] and report.failed == []
    assert "a file cannot undo a fill" in report.message() and first.order_id in report.message()
    assert (tmp_path / "KILL").exists()


def test_a_partial_fill_is_reported_whether_the_rest_cancelled_or_stayed_open(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    first, second = broker.place(req(qty=10, price=95.0), "c0"), broker.place(req(qty=10, price=94.0), "c1")
    real_cancel = broker.cancel

    def cancel(order_id):
        order = real_cancel(order_id) if order_id == first.order_id else [o for o in broker.orders() if o.order_id == order_id][0]
        order.filled_quantity = 4
        return order

    broker.cancel = cancel
    real_orders = broker.orders
    broker.orders = lambda: [_with(o, 4) for o in real_orders()]  # the broker's own book shows 4 of 10 filled on both
    with pytest.raises(BrokerError, match="could not be cancelled"):
        desk.kill()
    killed = [r["data"] for r in desk.audit.rows() if r["event"] == "kill"][0]
    assert killed["cancelled"] == [first.order_id] and killed["partial"] == [f"{first.order_id}: 4 of 10 filled"]
    assert any(second.order_id in f and "4 of 10" in f for f in killed["failed"])  # the other one is still open with a part filled


def _with(order, filled):
    order.filled_quantity = filled
    return order


def test_an_order_that_is_missing_from_the_book_after_the_cancel_is_unresolved(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    first, = resting(desk, broker, 95.0)
    real_orders, state = broker.orders, {"cancel_sent": False}

    def cancel(order_id):
        state["cancel_sent"] = True
        return real_orders()[0]

    broker.cancel = cancel
    broker.orders = lambda: [] if state["cancel_sent"] else real_orders()
    with pytest.raises(BrokerError, match="unresolved"):
        desk.kill()
    killed = [r["data"] for r in desk.audit.rows() if r["event"] == "kill"][0]
    assert killed["cancelled"] == [] and any(first.order_id in u and "not found" in u for u in killed["unresolved"])


def test_a_failed_lookup_after_the_cancel_leaves_the_order_unresolved_and_the_others_are_still_tried(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    first, second = resting(desk, broker, 95.0, 94.0)
    real_orders, real_cancel, state = broker.orders, broker.cancel, {"cancel_sent": False}

    def cancel(order_id):
        state["cancel_sent"] = True
        if order_id == first.order_id:
            return [o for o in real_orders() if o.order_id == order_id][0]  # acknowledged, not confirmed
        return real_cancel(order_id)

    def orders():
        if state["cancel_sent"]:
            raise ConnectionError("HTTP 429")
        return real_orders()

    broker.cancel, broker.orders = cancel, orders
    with pytest.raises(BrokerError, match="unresolved"):
        desk.kill()
    killed = [r["data"] for r in desk.audit.rows() if r["event"] == "kill"][0]
    assert killed["cancelled"] == [second.order_id]  # the second order's own answer said cancelled: no lookup was needed to know it
    assert len(killed["unresolved"]) == 1 and first.order_id in killed["unresolved"][0] and "could not be confirmed" in killed["unresolved"][0]
    assert (tmp_path / "KILL").exists()


def test_a_cancel_the_book_confirms_is_reported_as_cancelled_with_no_fuss(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    fast(desk)
    first, second = resting(desk, broker, 95.0, 94.0)
    report = desk.kill()
    assert report == [first.order_id, second.order_id] and report.failed == [] and report.unresolved == [] and report.filled_during_cancel == [] and report.partial == []
    assert [o.status for o in broker.orders()] == ["cancelled", "cancelled"]
