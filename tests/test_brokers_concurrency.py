import json
import os
import threading
import time

import pytest
from test_brokers import Clock, make, req, rules

from athena.brokers import quotes
from athena.brokers.audit import AuditLog
from athena.brokers.desk import Refused
from athena.brokers.lock import LockTimeout, exclusive
from athena.brokers.model import Order, Price


def test_two_confirms_of_one_token_at_the_same_moment_place_one_order(tmp_path):
    desk, broker, _, _ = make(tmp_path)
    token = desk.preview(req()).token
    results = []

    def go():
        try:
            results.append(desk.confirm(token))
        except Refused as exc:
            results.append(exc)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(isinstance(r, Order) for r in results) == 1 and sum(isinstance(r, Refused) for r in results) == 3
    assert len(broker.orders()) == 1 and AuditLog(tmp_path / "a.jsonl", Clock()).verify() == (True, None)


def test_audit_lines_written_from_several_threads_keep_one_unbroken_chain(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")

    def go(n):
        for i in range(10):
            log.append("event", n=n, i=i)

    threads = [threading.Thread(target=go, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r["seq"] for r in log.rows()] == list(range(1, 41)) and log.verify() == (True, None)


def test_the_lock_waits_for_a_running_owner_gives_up_after_the_wait_and_takes_over_from_a_dead_one(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text(json.dumps({"pid": os.getppid()}))  # the parent process is running
    with pytest.raises(LockTimeout, match="held by pid"):
        with exclusive(lock, wait=0.2, poll=0.05):
            pass
    lock.write_text(json.dumps({"pid": 2_000_000_000}))  # nobody
    with exclusive(lock, wait=1):
        assert json.loads(lock.read_text())["pid"] == os.getpid()
    assert not lock.exists()


def test_a_second_paper_process_does_not_overwrite_what_the_first_one_did(tmp_path):
    _, first, _, _ = make(tmp_path)
    _, second, _, _ = make(tmp_path)  # opened before the first one placed anything
    first.place(req(qty=1), "a")
    second.place(req(qty=2), "b")
    assert sorted(o.client_order_id for o in first.orders()) == ["a", "b"] and first.holdings()[0].quantity == 3


def test_a_price_that_is_polled_or_old_is_not_a_live_price_but_an_unlabelled_one_is_not_judged(tmp_path):
    desk, _, book, _ = make(tmp_path)
    book["price"] = Price(100.0, 99.9, 100.1, source="poll", age_seconds=1.0)
    assert rules(desk._findings(req())) == {"price check"}
    book["price"] = Price(100.0, 99.9, 100.1, source="stream", age_seconds=45.0)
    assert rules(desk._findings(req())) == {"price check"}
    book["price"] = Price(100.0, 99.9, 100.1, source="stream", age_seconds=2.0)
    assert desk._findings(req()).ok
    book["price"] = Price(100.0, 99.9, 100.1)
    assert desk._findings(req()).ok


def test_a_terminal_frame_carries_its_source_and_age_and_an_unreachable_feed_is_not_live():
    now_ms = int(time.time() * 1000)
    frame = {"type": "q", "q": [{"s": "SBIN", "l": 940.0, "src": "poll", "t": now_ms - 30_000}]}
    got = quotes.frame_price(frame, "SBIN")
    assert got.source == "poll" and 29 < got.age_seconds < 32
    assert quotes.frame_price({"type": "q", "q": [{"s": "SBIN", "l": 940.0}]}, "SBIN").age_seconds is None
    assert quotes.feed_is_live("http://127.0.0.1:1/api/status") is False  # nothing is listening there
