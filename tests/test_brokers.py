import json
from datetime import datetime, timedelta, timezone

import pytest

from athena.brokers import registry, safety
from athena.brokers.audit import AuditLog, redact
from athena.brokers.desk import BrokerError, Desk, Refused
from athena.brokers.model import Funds, Order, OrderError, OrderRequest, Price
from athena.brokers.paper import PaperBroker
from athena.brokers.quotes import frame_price
from athena.costs import model as costs

MONDAY_10AM = datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc)  # 10:00 in India
SATURDAY = datetime(2026, 10, 31, 4, 30, tzinfo=timezone.utc)
CHARGES = costs.load(path=costs.Path("no-such-file.json"))


class Clock:
    def __init__(self, now=MONDAY_10AM):
        self.now = now

    def __call__(self):
        return self.now


def req(side="BUY", qty=5, price=101.0, symbol="SBIN", **kw):
    return OrderRequest(symbol, side, qty, price, reason=kw.pop("reason", "testing"), **kw)


def make(tmp_path, price=Price(100.0, 99.9, 100.1), limits=None, cash=100_000.0, clock=None):
    clock = clock or Clock()
    book = {"price": price}
    quote = lambda s, e: book["price"]  # noqa: E731
    broker = PaperBroker(quote, tmp_path / "paper.json", cash, CHARGES, clock)
    desk = Desk(broker, limits or safety.Limits(), quote, AuditLog(tmp_path / "a.jsonl", clock), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES)
    return desk, broker, book, clock


# --- the request ---
def test_a_request_must_be_a_whole_delivery_limit_order_in_whole_paise():
    assert req(symbol=" sbin ").symbol == "SBIN"
    for bad in (dict(side="HOLD"), dict(qty=0), dict(qty=1.5), dict(qty=True), dict(price=0), dict(price=float("nan")), dict(price=100.123), dict(exchange="MCX"), dict(product="MIS")):
        with pytest.raises(OrderError):
            req(**bad)
    with pytest.raises(OrderError):
        OrderRequest("SBIN", "BUY", 1, None)  # a market order has no price: it cannot be written


def test_the_fingerprint_is_the_order_and_not_the_reason():
    assert req(reason="one").fingerprint() == req(reason="two").fingerprint() != req(qty=6).fingerprint()
    assert OrderRequest.from_dict(req().to_dict()) == req()


# --- the paper broker ---
def test_a_buy_fills_at_the_ask_and_takes_cash_and_charges_and_a_sale_books_the_result(tmp_path):
    _, broker, book, _ = make(tmp_path)
    buy = broker.place(req("BUY", 10, 101.0), "c1")
    assert buy.status == "filled" and buy.average_price == 100.1
    fee = costs.leg(CHARGES, "delivery", "buy", 1001.0)["total"]
    assert broker.funds().cash == pytest.approx(100_000 - 1001.0 - fee)
    assert [(h.symbol, h.quantity, round(h.average_price, 2)) for h in broker.holdings()] == [("SBIN", 10, 100.1)]
    book["price"] = Price(110.0, 109.9, 110.1)
    sell = broker.place(req("SELL", 10, 105.0), "c2")
    assert sell.status == "filled" and sell.average_price == 109.9 and sell.realised == pytest.approx((109.9 - 100.1) * 10 - sell.charges)
    assert broker.holdings() == []


def test_an_order_that_cannot_be_met_is_rejected_whole_and_there_is_no_short_selling(tmp_path):
    _, broker, _, _ = make(tmp_path, cash=500.0)
    assert broker.place(req("BUY", 10, 100.0), "c1").status == "rejected"
    sell = broker.place(req("SELL", 1, 100.0), "c2")
    assert sell.status == "rejected" and "no short selling" in sell.message
    assert broker.funds().cash == 500.0 and broker.holdings() == []


def test_an_order_off_the_touch_rests_holds_its_cash_and_fills_on_settle_or_cancels(tmp_path):
    _, broker, book, _ = make(tmp_path, cash=10_000.0)
    o = broker.place(req("BUY", 10, 95.0), "c1")
    assert o.status == "open" and broker.funds().reserved > 950 and broker.funds().available < 10_000 - 950
    assert broker.settle() == []
    book["price"] = Price(94.0, 93.9, 94.0)
    done = broker.settle()
    assert [x.order_id for x in done] == [o.order_id] and done[0].average_price == 94.0 and broker.funds().reserved == 0
    other = broker.place(req("BUY", 10, 80.0), "c2")
    assert broker.cancel(other.order_id).status == "cancelled" and broker.funds().reserved == 0
    with pytest.raises(KeyError):
        broker.cancel("P999999")


def test_placing_twice_with_one_client_id_gives_one_order_and_the_book_survives_a_restart(tmp_path):
    _, broker, _, _ = make(tmp_path)
    a, b = broker.place(req(), "same"), broker.place(req(), "same")
    assert a.order_id == b.order_id and len(broker.orders()) == 1
    again = PaperBroker(lambda s, e: None, tmp_path / "paper.json", charges=CHARGES)
    assert len(again.orders()) == 1 and again.holdings()[0].quantity == 5 and again.funds().cash == pytest.approx(broker.funds().cash)


def test_no_quote_means_an_order_rests_rather_than_fills(tmp_path):
    _, broker, book, _ = make(tmp_path, price=None)
    assert broker.place(req(), "c1").status == "open"


# --- the limits ---
def test_the_limits_file_is_read_with_unknown_keys_refused(tmp_path):
    p = tmp_path / "broker.json"
    assert safety.load_limits(p) == safety.Limits()
    p.write_text(json.dumps({"mode": "read_only", "max_order_value": 500, "allowlist": ["sbin"]}), encoding="utf-8")
    got = safety.load_limits(p)
    assert got.mode == "read_only" and got.max_order_value == 500 and got.allowlist == ("SBIN",)
    p.write_text(json.dumps({"max_order_valu": 5}), encoding="utf-8")
    with pytest.raises(safety.LimitsError, match="unknown"):
        safety.load_limits(p)
    with pytest.raises(safety.LimitsError):
        safety.Limits(mode="yolo")


def rules(findings):
    return {r for r, ok, _ in findings.breaches}


def test_each_rule_breaks_on_its_own(tmp_path):
    desk, broker, book, clock = make(tmp_path)
    assert desk._findings(req()).ok
    assert rules(desk._findings(req(qty=500))) == {"order value", "deployed"}  # Rs 50,500 against 10,000 an order and 25,000 held
    assert rules(desk._findings(req(price=120.0))) == {"price check"}  # 20% from the live price
    book["price"] = None
    assert rules(desk._findings(req())) == {"price check"}
    book["price"] = Price(100.0, 99.9, 100.1)
    clock.now = SATURDAY
    assert rules(desk._findings(req())) == {"trading hours"}
    clock.now = MONDAY_10AM.replace(hour=11)  # 16:30 India time
    assert rules(desk._findings(req())) == {"trading hours"}
    clock.now = MONDAY_10AM
    (tmp_path / "KILL").write_text("x")
    assert rules(desk._findings(req())) == {"kill switch"}
    (tmp_path / "KILL").unlink()
    desk.limits = safety.Limits(mode="read_only")
    assert rules(desk._findings(req())) == {"mode"}


def test_the_daily_order_count_the_deployed_cap_and_the_daily_loss_stop_new_orders(tmp_path):
    desk, broker, book, _ = make(tmp_path, limits=safety.Limits(max_orders_per_day=2, max_deployed=1_200.0, max_order_value=5_000.0))
    broker.place(req("BUY", 10, 101.0), "c1")  # about Rs 1,001 held
    assert rules(desk._findings(req("BUY", 5, 101.0))) == {"deployed"}  # 1,001 + 505 is over 1,200
    broker.place(req("BUY", 1, 101.0), "c2")
    assert "orders today" in rules(desk._findings(req("BUY", 1, 101.0)))
    desk2, broker2, book2, _ = make(tmp_path / "x", limits=safety.Limits(max_daily_loss=50.0, max_order_value=50_000.0))
    broker2.place(req("BUY", 100, 101.0), "c1")
    book2["price"] = Price(90.0, 90.0, 90.1)
    broker2.place(req("SELL", 100, 89.0), "c2")  # sold about Rs 1,000 below cost
    assert "daily loss" in rules(desk2._findings(req("BUY", 1, 90.0)))


def test_live_mode_needs_the_allow_list_and_a_live_adapter_and_there_is_none(tmp_path):
    desk, _, _, _ = make(tmp_path, limits=safety.Limits(mode="live_delivery", allowlist=("TCS",)))
    assert rules(desk._findings(req())) == {"allowlist", "live broker"}
    assert rules(desk._findings(req(symbol="TCS"))) == {"live broker"}


# --- tokens and the desk ---
def test_a_preview_needs_a_reason_and_a_breach_issues_no_token(tmp_path):
    desk, _, _, _ = make(tmp_path)
    with pytest.raises(Refused, match="why"):
        desk.preview(OrderRequest("SBIN", "BUY", 1, 100.0))
    bad = desk.preview(req(qty=500))
    assert not bad.ok and bad.token == "" and "refused" in bad.lines()[-1]
    good = desk.preview(req())
    assert good.ok and good.token and good.charges > 0 and any("contract note" in n for n in good.notes)


def test_confirm_places_the_previewed_order_once_and_only_once(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    view = desk.preview(req())
    order = desk.confirm(view.token)
    assert order.status == "filled" and len(broker.orders()) == 1
    with pytest.raises(Refused, match="already used"):
        desk.confirm(view.token)
    with pytest.raises(Refused, match="unknown token"):
        desk.confirm("not-a-token")
    assert len(broker.orders()) == 1


def test_a_token_expires_and_cannot_be_used_for_a_different_order(tmp_path):
    desk, broker, _, clock = make(tmp_path)
    view = desk.preview(req())
    clock.now += timedelta(seconds=safety.TOKEN_SECONDS + 1)
    with pytest.raises(Refused, match="expired"):
        desk.confirm(view.token)
    view = desk.preview(req())
    rows = json.loads((tmp_path / "t.json").read_text())
    key = max((k for k, v in rows.items() if not v["used"]), key=lambda k: rows[k]["expires"])
    rows[key]["request"]["quantity"] = 9  # an edited record no longer matches the fingerprint it was issued with
    (tmp_path / "t.json").write_text(json.dumps(rows))
    with pytest.raises(Refused, match="does not match"):
        desk.confirm(view.token)
    assert broker.orders() == []


def test_the_rules_are_checked_again_at_confirm_so_a_kill_switch_after_the_preview_stops_it(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    view = desk.preview(req())
    (tmp_path / "KILL").write_text("x")
    with pytest.raises(Refused, match="kill switch"):
        desk.confirm(view.token)
    assert broker.orders() == []


def test_the_live_mode_refuses_and_paper_mode_refuses_a_live_broker(tmp_path):
    desk, _, _, _ = make(tmp_path, limits=safety.Limits(mode="live_delivery", allowlist=("SBIN",)))
    assert not desk.preview(req()).ok  # no live adapter, so no token to confirm
    desk, broker, _, _ = make(tmp_path / "y")
    broker.live = True
    view = desk.preview(req())
    with pytest.raises(Refused, match="paper but the broker is live"):
        desk.confirm(view.token)


def test_a_broker_failure_is_redacted_and_logged_not_leaked(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    secret = "AbCdEf0123456789AbCdEf0123456789XyZ"

    def boom(request, client_id):
        raise RuntimeError(f"401 Unauthorized, Bearer {secret}")

    broker.place = boom
    view = desk.preview(req())
    with pytest.raises(BrokerError) as caught:
        desk.confirm(view.token)
    assert secret not in str(caught.value) and "<redacted>" in str(caught.value)
    assert secret not in (tmp_path / "a.jsonl").read_text(encoding="utf-8")


def test_kill_cancels_open_orders_and_switches_trading_off_and_resume_switches_it_on(tmp_path):
    desk, broker, book, _ = make(tmp_path)
    open_order = broker.place(req("BUY", 5, 90.0), "c1")
    assert open_order.status == "open"
    assert desk.kill() == [open_order.order_id] and broker.orders()[0].status == "cancelled"
    assert rules(desk._findings(req())) == {"kill switch"}
    desk.resume()
    assert desk._findings(req()).ok
    assert desk.cancel(open_order.order_id).status == "cancelled"
    with pytest.raises(Refused):
        desk.cancel("P000999")


# --- the audit log ---
def test_the_audit_log_is_chained_and_an_edit_is_found(tmp_path):
    desk, _, _, _ = make(tmp_path)
    view = desk.preview(req())
    desk.confirm(view.token)
    log = AuditLog(tmp_path / "a.jsonl")
    assert [r["event"] for r in log.rows()] == ["preview", "placed"] and log.verify() == (True, None)
    text = (tmp_path / "a.jsonl").read_text(encoding="utf-8")
    (tmp_path / "a.jsonl").write_text(text.replace('"quantity": 5', '"quantity": 6', 1), encoding="utf-8")
    assert log.verify() == (False, 1)
    assert AuditLog(tmp_path / "none.jsonl").verify() == (True, None)


def test_redaction_removes_token_like_strings_wherever_they_sit():
    out = redact({"a": ["Bearer abc.def-123", {"b": "x" * 40}], "n": 5})
    assert out == {"a": ["<redacted>", {"b": "<redacted>"}], "n": 5}


# --- quotes and the registry ---
def test_a_terminal_frame_gives_last_bid_and_ask_for_the_symbol_asked():
    frame = {"type": "q", "q": [{"s": "TCS", "l": 3000.0}, {"s": "SBIN", "l": 940.0, "b": [[939.9, 10]], "a": [[940.1, 20]]}]}
    assert frame_price(frame, "SBIN") == Price(940.0, 939.9, 940.1) and frame_price(frame, "TCS") == Price(3000.0) and frame_price(frame, "ITC") is None
    assert frame_price({"type": "status"}, "SBIN") is None


def test_the_registry_lists_the_big_brokers_and_says_none_is_checked_but_paper():
    names = {b.key: b for b in registry.BROKERS}
    assert {"groww", "zerodha", "angelone", "upstox", "dhan", "paper"} <= set(names)
    assert [k for k, b in names.items() if b.checked] == ["paper"]
    assert len(registry.describe()) == len(registry.BROKERS)
