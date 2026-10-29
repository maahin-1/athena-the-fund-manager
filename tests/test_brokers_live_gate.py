"""What a real order needs that paper trading does not: a typed confirmation, a signed audit log, limits a person pinned."""
import io
import json

import pytest

from athena.brokers import human, safety
from athena.brokers.audit import AuditLog
from athena.brokers.desk import Desk, Refused
from athena.brokers.model import Price
from athena.brokers.paper import PaperBroker
from test_brokers import CHARGES, Clock, req

KEY = b"k" * 32


class Tty(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


# --- the person at the console ---
def test_ask_needs_a_console_on_both_ends():
    assert human.ask("? ", Tty("yes\n"), Tty()) == "yes"
    assert human.ask("? ", Tty("yes\n", tty=False), Tty()) is None  # piped in: a script or a tool
    assert human.ask("? ", Tty("yes\n"), Tty(tty=False)) is None
    assert human.ask("? ", Tty(""), Tty()) is None


def test_the_confirm_phrase_is_the_order_and_nothing_else_passes():
    phrase = human.order_phrase("BUY", 5, "SBIN", 101.0)
    assert phrase == "BUY 5 SBIN 101.00"
    assert human.console_confirm(phrase, "s", lambda prompt: phrase)
    for wrong in ("", "y", "yes", "BUY 5 SBIN", "buy 5 sbin 101.00", None):
        assert not human.console_confirm(phrase, "s", lambda prompt, w=wrong: w)


# --- the pin on the limits file ---
def test_a_pinned_limits_file_stays_pinned_only_until_it_changes(tmp_path):
    limits, mine = tmp_path / "broker.json", tmp_path / "user"
    limits.write_text(json.dumps({"max_order_value": 1000}))
    assert not human.limits_pinned(limits, mine)  # never pinned
    human.pin_limits(limits, mine)
    assert human.limits_pinned(limits, mine)
    limits.write_text(json.dumps({"max_order_value": 1000000}))  # loosened by whatever could edit the file
    assert not human.limits_pinned(limits, mine)
    human.pin_limits(limits, mine)
    assert human.limits_pinned(limits, mine)


def test_the_audit_key_is_made_once_and_kept_outside_the_given_project(tmp_path):
    first = human.audit_key(tmp_path)
    assert len(first) == 32 and human.audit_key(tmp_path) == first
    assert human.audit_key(tmp_path / "other", create=False) is None


# --- the signed audit log ---
def test_a_signed_log_is_checked_with_the_key_and_cannot_be_rewritten_without_it(tmp_path):
    clock = Clock()
    log = AuditLog(tmp_path / "a.jsonl", clock, key=KEY)
    for i in range(3):
        log.append("preview", n=i)
    assert log.verify(KEY) == (True, None) and log.verify() == (True, None)
    assert log.verify(b"x" * 32) == (False, 1)  # a different key
    rows = log.rows()
    # someone who can edit the file rewrites a line AND recomputes the hash chain, but has no key
    forged = AuditLog(tmp_path / "f.jsonl", clock)
    for r in rows:
        forged.append(r["event"], **({"n": 99} if r["seq"] == 2 else r["data"]))
    assert forged.verify() == (True, None) and forged.verify(KEY) == (True, None)  # unsigned from the start: the chain alone cannot tell
    # but cutting the signatures off part of a signed log is found
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    last = json.loads(lines[-1])
    last.pop("mac")
    (tmp_path / "a.jsonl").write_text("\n".join([*lines[:-1], json.dumps(last)]) + "\n")
    assert log.verify(KEY) == (False, 3)


def test_a_signed_line_edited_in_place_fails(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", Clock(), key=KEY)
    log.append("placed", n=1)
    log.append("placed", n=2)
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    row = json.loads(lines[0])
    row["data"]["n"] = 100
    (tmp_path / "a.jsonl").write_text("\n".join([json.dumps(row), lines[1]]) + "\n")
    assert log.verify(KEY)[0] is False


# --- the desk with a live broker ---
class LiveFake(PaperBroker):
    live = True
    name = "fakelive"


def live_desk(tmp_path, human_fn=None, pinned=True, key=KEY):
    clock = Clock()
    quote = lambda s, e: Price(100.0, 99.9, 100.1, "stream", 0.5)  # noqa: E731
    broker = LiveFake(quote, tmp_path / "p.json", 100_000.0, CHARGES, clock)
    limits = safety.Limits(mode="live_delivery", broker="groww", allowlist=("SBIN",))
    desk = Desk(broker, limits, quote, AuditLog(tmp_path / "a.jsonl", clock, key=key), safety.Tokens(tmp_path / "t.json", clock), clock, tmp_path / "KILL", CHARGES, human=human_fn, limits_pinned=lambda: pinned)
    return desk, broker


def test_a_live_order_is_previewable_only_with_a_console_pinned_limits_and_a_signed_log(tmp_path):
    ok = lambda phrase, summary: True  # noqa: E731
    desk, _ = live_desk(tmp_path / "a", ok)
    assert desk.preview(req()).ok
    desk, _ = live_desk(tmp_path / "b", None)
    assert [r for r, passed, _ in desk.preview(req()).findings.items if not passed] == ["person at the console"]
    desk, _ = live_desk(tmp_path / "c", ok, pinned=False)
    assert [r for r, passed, _ in desk.preview(req()).findings.items if not passed] == ["limits pinned"]
    desk, _ = live_desk(tmp_path / "d", ok, key=None)
    assert [r for r, passed, _ in desk.preview(req()).findings.items if not passed] == ["signed audit log"]


def test_a_live_order_is_sent_only_when_the_person_types_the_order(tmp_path):
    asked = []

    def person(phrase, summary):
        asked.append(phrase)
        return False

    desk, broker = live_desk(tmp_path / "a", person)
    view = desk.preview(req())
    with pytest.raises(Refused, match="typing it at a console"):
        desk.confirm(view.token)
    assert asked == ["BUY 5 SBIN 101.00"] and broker.orders() == []
    assert any(r["event"] == "confirm_refused" for r in desk.audit.rows())

    desk, broker = live_desk(tmp_path / "b", lambda phrase, summary: True)
    view = desk.preview(req())
    order = desk.confirm(view.token)
    assert order.status == "filled" and len(broker.orders()) == 1


def test_a_script_cannot_confirm_a_live_order_through_the_real_console_check(tmp_path):
    desk, broker = live_desk(tmp_path, lambda phrase, summary: human.console_confirm(phrase, summary, lambda prompt: human.ask(prompt, Tty("BUY 5 SBIN 101.00\n", tty=False), Tty())))
    view = desk.preview(req())
    with pytest.raises(Refused):
        desk.confirm(view.token)
    assert broker.orders() == []
