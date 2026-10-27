"""The order desk from a terminal. Paper trading only in this release; nothing here can reach a real broker.

    python -m athena.brokers brokers                                   which brokers Athena is meant to work with, and what is built
    python -m athena.brokers preview SBIN BUY 5 940.50 --reason "why"  check the order and, if allowed, print a one-time token
    python -m athena.brokers confirm TOKEN                             send the previewed order (paper)
    python -m athena.brokers book | orders | settle | cancel ORDER_ID  funds and holdings, the order list, look at open orders again, cancel one
    python -m athena.brokers kill | resume                             switch trading off (and cancel what is open) or back on
    python -m athena.brokers audit [--verify]                          the log of everything the desk did

The limits come from `.athena/broker.json`, edited by hand. The terminal must be running for live prices."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from athena.brokers import registry, safety
from athena.brokers.audit import AuditLog
from athena.brokers.desk import BrokerError, Desk, Refused
from athena.brokers.model import OrderError, OrderRequest
from athena.brokers.paper import PaperBroker
from athena.brokers.quotes import terminal_quote


def _desk() -> tuple[Desk, PaperBroker]:
    limits = safety.load_limits()
    broker = PaperBroker(terminal_quote)
    return Desk(broker, limits, terminal_quote), broker


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("brokers")
    p = sub.add_parser("preview")
    p.add_argument("symbol")
    p.add_argument("side")
    p.add_argument("quantity", type=int)
    p.add_argument("price", type=float)
    p.add_argument("--exchange", default="NSE")
    p.add_argument("--reason", default="")
    sub.add_parser("confirm").add_argument("token")
    sub.add_parser("book")
    sub.add_parser("orders")
    sub.add_parser("settle")
    sub.add_parser("cancel").add_argument("order_id")
    sub.add_parser("kill")
    sub.add_parser("resume")
    a = sub.add_parser("audit")
    a.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "brokers":
        print("\n".join(registry.describe()))
        return 0
    if args.cmd == "audit":
        log = AuditLog()
        if args.verify:
            ok, bad = log.verify()
            print("the audit log is intact" if ok else f"the audit log was changed at line {bad}")
            return 0 if ok else 2
        for row in log.rows()[-30:]:
            print(row["seq"], row["at"], row["event"], row["data"])
        return 0
    try:
        desk, broker = _desk()
        if args.cmd == "preview":
            view = desk.preview(OrderRequest(args.symbol, args.side, args.quantity, args.price, args.exchange, reason=args.reason))
            print("\n".join(view.lines()))
            return 0 if view.ok else 1
        if args.cmd == "confirm":
            order = desk.confirm(args.token)
            print(f"{order.order_id} {order.status}: {order.request.side} {order.request.quantity} {order.request.symbol}" + (f" at {order.average_price:,.2f}, charges Rs {order.charges:,.2f}" if order.status == "filled" else "") + (f" ({order.message})" if order.message else ""))
            return 0
        if args.cmd == "book":
            f = broker.funds()
            print(f"cash Rs {f.cash:,.2f}, reserved Rs {f.reserved:,.2f}, available Rs {f.available:,.2f}")
            for h in broker.holdings():
                print(f"  {h.exchange}:{h.symbol} {h.quantity} at an average of Rs {h.average_price:,.2f}")
            return 0
        if args.cmd == "orders":
            for o in broker.orders():
                print(f"{o.order_id} {o.status:<9} {o.request.side} {o.request.quantity} {o.request.symbol} limit {o.request.limit_price:,.2f}" + (f"  filled at {o.average_price:,.2f}" if o.status == "filled" else "") + (f"  {o.message}" if o.message else ""))
            return 0
        if args.cmd == "settle":
            done = broker.settle()
            print(f"{len(done)} open order(s) filled" + "".join(f"\n  {o.order_id} {o.request.symbol} at {o.average_price:,.2f}" for o in done))
            return 0
        if args.cmd == "cancel":
            o = desk.cancel(args.order_id)
            print(f"{o.order_id} {o.status}")
            return 0
        if args.cmd == "kill":
            print("trading is off; cancelled: " + (", ".join(desk.kill()) or "nothing was open"))
            return 0
        if args.cmd == "resume":
            desk.resume()
            print("trading is on again (within the limits file)")
            return 0
    except (Refused, BrokerError, OrderError, safety.LimitsError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
