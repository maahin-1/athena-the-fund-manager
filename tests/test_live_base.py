"""The shared base of the live adapters: what a lost answer, a partial fill or a failed read-back must never turn into."""
import pytest

from athena.brokers import safety
from athena.brokers.audit import AuditLog
from athena.brokers.desk import BrokerError, Desk
from athena.brokers.live.base import BrokerApiError, LiveBroker, OutcomeUnknown
from athena.brokers.model import Funds, Holding, Order, OrderRequest, Price
from athena.brokers.paper import PaperBroker
from test_brokers import CHARGES, Clock, req


class Fake(LiveBroker):
    name = "fake"
    SETTINGS = ()

    def __init__(self, tmp_path):
        super().__init__(settings={}, ledger_dir=tmp_path)
        self.sent, self.book, self.fail_send, self.fail_read, self.cancel_fails = [], [], None, False, set()

    def _funds(self):
        return Funds(100000.0)

    def _holdings(self):
        return []

    def _orders(self):
        if self.fail_read:
            raise BrokerApiError("HTTP 429", 429)
        return list(self.book)

    def _place(self, request, client_order_id):
        if self.fail_send:
            raise self.fail_send
        self.sent.append(client_order_id)
        order_id = f"B{len(self.sent)}"
        self.book.append(Order(order_id, client_order_id, request, status="open", placed_at="2026-10-26T04:30:00+00:00"))
        return order_id

    def _cancel(self, order_id):
        if order_id in self.cancel_fails:
            raise BrokerApiError("too late", 400)
        for o in self.book:
            if o.order_id == order_id:
                o.status = "cancelled"


def test_a_send_whose_answer_was_lost_blocks_the_same_order_asked_for_again_under_a_new_id(tmp_path):
    b = Fake(tmp_path)
    b.fail_send = BrokerApiError("the request did not complete: ReadTimeout")  # status 0: it may have reached the broker
    with pytest.raises(BrokerApiError):
        b.place(req(), "ath-1")
    b.fail_send = None
    with pytest.raises(OutcomeUnknown, match="resolve"):
        b.place(req(), "ath-2")  # a new preview, a new client id, the same order
    assert b.sent == []
    assert list(b.unresolved()) == ["ath-1"]
    b.place(req(qty=6), "ath-3")  # a different order is not blocked
    assert b.resolve("ath-1")
    b.place(req(), "ath-4")
    assert b.sent == ["ath-3", "ath-4"]


def test_a_refusal_from_the_broker_blocks_nothing(tmp_path):
    b = Fake(tmp_path)
    b.fail_send = BrokerApiError("HTTP 400 insufficient funds", 400)
    with pytest.raises(BrokerApiError):
        b.place(req(), "ath-1")
    b.fail_send = None
    assert b.place(req(), "ath-2").order_id == "B1"


def test_failing_to_read_the_order_back_does_not_make_a_placed_order_look_failed(tmp_path):
    b = Fake(tmp_path)
    b.fail_read = True
    order = b.place(req(), "ath-1")
    assert order.status == "open" and "not yet in the broker" in order.message and b.sent == ["ath-1"]
    assert b.place(req(), "ath-1").order_id == order.order_id and b.sent == ["ath-1"]  # asking again sends nothing


def test_a_fill_of_less_than_the_whole_is_never_reported_as_filled(tmp_path):
    b = Fake(tmp_path)
    b.book = [Order("B1", "x", req(qty=10), status="filled", filled_quantity=4, average_price=100.0, placed_at="2026-10-26T04:30:00+00:00"), Order("B2", "y", req(qty=10), status="filled", filled_quantity=10, average_price=100.0, placed_at="2026-10-26T04:30:00+00:00")]
    first, second = b.orders()
    assert first.status == "open" and "4 of 10" in first.message
    assert second.status == "filled"


# --- the desk around a live broker ---
def live_desk(tmp_path, broker, holdings=()):
    clock = Clock()
    quote = lambda s, e: Price(100.0, 99.9, 100.1, "stream", 0.5)  # noqa: E731
    broker._holdings = lambda: list(holdings)
    limits = safety.Limits(mode="live_delivery", broker="groww", allowlist=("SBIN",), max_order_value=100000.0, max_deployed=100000.0)
    return Desk(broker, limits, quote, AuditLog(tmp_path / "a.jsonl", clock, key=b"k" * 32), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES, human=lambda phrase, summary: True, limits_pinned=lambda: True)


def test_kill_cancels_every_order_it_can_and_says_which_it_could_not(tmp_path):
    b = Fake(tmp_path)
    for i in range(3):
        b.book.append(Order(f"B{i}", f"c{i}", req(), status="open", placed_at="2026-10-26T04:30:00+00:00"))
    b.cancel_fails = {"B0"}
    desk = live_desk(tmp_path, b)
    with pytest.raises(BrokerError, match="B0"):
        desk.kill()
    assert [o.status for o in b.book] == ["open", "cancelled", "cancelled"]
    assert (tmp_path / "KILL").exists()
    assert any(r["event"] == "kill" and r["data"]["failed"] for r in desk.audit.rows())


def test_a_real_sale_needs_shares_that_are_held_and_not_already_offered(tmp_path):
    b = Fake(tmp_path)
    desk = live_desk(tmp_path, b, holdings=[Holding("SBIN", "NSE", 10, 90.0)])
    assert desk.preview(req("SELL", 10, 101.0)).ok
    assert not desk.preview(req("SELL", 11, 101.0)).ok
    b.book.append(Order("B9", "z", req("SELL", 6, 101.0), status="open", placed_at="2026-10-26T04:30:00+00:00"))
    assert not desk.preview(req("SELL", 5, 101.0)).ok and desk.preview(req("SELL", 4, 101.0)).ok


def test_the_daily_loss_rule_says_it_is_not_measured_for_a_real_broker(tmp_path):
    desk = live_desk(tmp_path, Fake(tmp_path))
    text = [t for r, ok, t in desk.preview(req()).findings.items if r == "daily loss"][0]
    assert "not measured" in text


def test_a_buy_filled_today_counts_toward_the_deployed_cap_even_if_holdings_do_not_show_it_yet(tmp_path):
    b = Fake(tmp_path)
    b.book.append(Order("B1", "c", req(qty=100, price=101.0), status="filled", filled_quantity=100, average_price=100.0, placed_at="2026-10-26T04:30:00+00:00"))
    desk = live_desk(tmp_path, b)
    desk.limits = safety.Limits(mode="live_delivery", broker="groww", allowlist=("SBIN",), max_order_value=100000.0, max_deployed=10_500.0)
    assert not desk.preview(req("BUY", 5, 101.0)).ok  # 10,000 filled today + 505 = 10,505 is over
    desk.limits = safety.Limits(mode="live_delivery", broker="groww", allowlist=("SBIN",), max_order_value=100000.0, max_deployed=11_000.0)
    assert desk.preview(req("BUY", 5, 101.0)).ok


def test_a_log_that_does_not_verify_or_a_damaged_key_blocks_a_live_order(tmp_path):
    desk = live_desk(tmp_path, Fake(tmp_path))
    assert desk.preview(req()).ok
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    (tmp_path / "a.jsonl").write_text(lines[0].replace("preview", "prevue") + "\n")
    assert [r for r, ok, _ in desk.preview(req()).findings.items if not ok] == ["signed audit log"]
    desk.audit.key = b""
    assert "signed audit log" in [r for r, ok, _ in desk.preview(req()).findings.items if not ok]


def test_the_rules_are_looked_at_again_after_the_person_has_typed(tmp_path):
    b = Fake(tmp_path)
    desk = live_desk(tmp_path, b)
    view = desk.preview(req())

    def person(phrase, summary):
        (tmp_path / "KILL").write_text("x")  # the kill switch goes on while the person is typing
        return True

    desk._human = person
    from athena.brokers.desk import Refused

    with pytest.raises(Refused, match="kill switch"):
        desk.confirm(view.token)
    assert b.sent == []
