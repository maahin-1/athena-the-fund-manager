import io
import json
from dataclasses import dataclass

from athena.dashboard.worker import MAX_QUERY, handle, serve


@dataclass(frozen=True)
class FakeView:
    status: str
    query: str
    notes: tuple[str, ...] = ()


class FakeService:
    def __init__(self):
        self.calls = []

    def view(self, query, overlay=None, list_relatives=False):
        self.calls.append((query, overlay, list_relatives))
        if query == "BOOM":
            raise RuntimeError("the model is down")
        return FakeView("ok", query, ("a note",))


def test_a_view_request_is_answered_with_the_dashboard_view_as_plain_data():
    service = FakeService()
    reply = handle(service, {"id": 7, "op": "view", "query": " TCS ", "relatives": True})
    assert reply == {"id": 7, "ok": True, "result": {"status": "ok", "query": "TCS", "notes": ("a note",)}}
    assert service.calls == [("TCS", None, True)]


def test_a_failure_is_a_reply_and_the_worker_goes_on():
    out = io.StringIO()
    serve(FakeService(), ['{"id": 1, "op": "view", "query": "BOOM"}', "not json", '[1]', "", '{"id": 2, "op": "view", "query": "INFY"}', '{"id": 3, "op": "nope"}'], out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, None, None, 2, 3]
    assert replies[0] == {"id": 1, "ok": False, "error": "the analysis failed (RuntimeError)"}  # the library message is not forwarded
    assert not replies[1]["ok"] and not replies[2]["ok"]
    assert replies[3]["ok"] and replies[3]["result"]["query"] == "INFY"
    assert "unknown operation" in replies[4]["error"]


def test_an_empty_or_long_query_is_refused_before_the_service_is_asked():
    service = FakeService()
    assert not handle(service, {"id": 1, "op": "view", "query": "  "})["ok"]
    assert not handle(service, {"id": 2, "op": "view", "query": "X" * (MAX_QUERY + 1)})["ok"]
    assert service.calls == []
