import io
import json
import sys

from athena import terminal_helper


def fake_ops():
    def page(args):
        sys.stdout.write(json.dumps({"symbol": args[0], "price": args[-1]}))
        return 0

    def broken(args):
        raise RuntimeError("store locked")

    def refuses(args):
        raise SystemExit(2)

    return {"page": page, "valuation": broken, "chart_bars": refuses}


def test_each_request_gets_what_the_command_prints_and_failures_are_replies_not_crashes():
    lines = io.StringIO(
        "\n".join(
            [
                json.dumps({"id": 1, "op": "page", "args": ["TCS", "--price", "3200"]}),
                json.dumps({"id": 2, "op": "valuation", "args": ["TCS"]}),
                json.dumps({"id": 3, "op": "chart_bars", "args": []}),
                json.dumps({"id": 4, "op": "rm", "args": []}),
                json.dumps({"id": 5, "op": "page", "args": ["x" * 400]}),
                "not json",
            ]
        )
        + "\n"
    )
    out = io.StringIO()
    terminal_helper.serve(lines, out, fake_ops())
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0] == {"ready": True}
    assert replies[1]["ok"] is True and json.loads(replies[1]["out"]) == {"symbol": "TCS", "price": "3200"}
    assert replies[2] == {"id": 2, "ok": False, "error": "RuntimeError"}
    assert replies[3] == {"id": 3, "ok": False, "error": "exit 2"}
    assert replies[4]["error"] == "bad request" and replies[5]["error"] == "bad request"
    assert replies[6] == {"id": None, "ok": False, "error": "bad request"}


def test_the_real_operations_are_the_commands_the_server_ran():
    ops = terminal_helper.operations()
    from athena.flow import board
    from athena.fundamentals import page
    from athena.valuation import page as valuation
    from athena.warehouse import chart_bars

    assert ops == {"page": page.main, "valuation": valuation.main, "chart_bars": chart_bars.main, "flow_board": board.main}


def test_warming_runs_a_page_and_a_valuation_and_ignores_their_failure():
    called = []
    ops = {"page": lambda a: called.append(("page", a)) or 0, "valuation": lambda a: (_ for _ in ()).throw(RuntimeError("locked"))}
    terminal_helper.warm(ops)
    assert called == [("page", ["TCS"])]
