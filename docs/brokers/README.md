# Connecting your broker

Athena places orders through one broker-neutral desk. You choose the broker; nothing else changes. Paper trading is the default and needs no account.

| Broker | Key | Guide | Status |
|---|---|---|---|
| Groww | `groww` | [groww.md](groww.md) | adapter written from the published API docs |
| Zerodha (Kite Connect) | `zerodha` | [zerodha.md](zerodha.md) | adapter written from the published API docs |
| Angel One (SmartAPI) | `angelone` | [angelone.md](angelone.md) | adapter written from the SDK source (its docs site cannot be read by a script) |
| ICICI Direct (Breeze) | `icici` | [icici.md](icici.md) | adapter written from the published API docs and SDK |
| Upstox | `upstox` | [upstox.md](upstox.md) | adapter written from the published API docs |

These are the five largest brokers by NSE active clients (September 2026). **None of them has been tried with a real account yet.** Each guide lists what was confirmed from the documentation and what
could not be. Start with paper trading, then send the smallest real order you can through one broker and read the result before trusting it with more.

## Turning on real orders

Real orders are off. All of the following must be true, and none of them can be switched on by a script or an assistant running commands for you:

1. **Settings.** Put your broker's settings in the settings file (`.env`, see `.env.example`) and run `python -m athena.brokers check`: it logs in and reads your funds, and places nothing.
2. **Limits file.** Edit `.athena/broker.json` by hand:
   ```json
   { "mode": "live_delivery", "broker": "zerodha", "allowlist": ["SBIN", "TCS"],
     "max_order_value": 5000, "max_orders_per_day": 2, "max_deployed": 20000, "max_daily_loss": 500 }
   ```
   Only the shares on `allowlist` can be ordered. Defaults are small on purpose.

   Two more things a real order needs, because Athena cannot measure them for a real broker:
   - **Daily loss.** A real broker reports no realised loss per order, so `max_daily_loss` cannot be measured or enforced and is shown as UNAVAILABLE, never as passed. By default (`"daily_loss_enforcement": "enforced"`)
     that **refuses** real orders. Add `"daily_loss_enforcement": "advisory"` only if you accept watching your broker's own loss report yourself; the preview then lists the limit as UNAVAIL and not enforced.
   - **Exchange holidays.** Keep the exchange's trading holidays in `.athena/broker/holidays.json` as `{"dates": ["2026-10-02", "2026-11-10"]}` (the exchange publishes the list each year). Without that file
     a real order is refused (an open market cannot be confirmed); paper trading falls back to weekdays and says the holidays are not loaded.
3. **Pin the limits.** `python -m athena.brokers pin-limits` at a console. Any later change to the file, by anyone, stops real orders until you pin it again.
4. **Preview, then confirm at a console.** `preview` checks every rule and gives a one-time code. `confirm CODE` then asks you to type the order back (for example `BUY 5 SBIN 101.00`). A script, a
   scheduler or an assistant has no console, so it cannot send the order.
5. **Audit log.** Every step is written to `.athena/broker/audit.jsonl`, each line chained to the one before and signed with a key kept outside the project (in your user folder).
   `python -m athena.brokers audit --verify` checks it.

The kill switch (`python -m athena.brokers kill`) refuses every order and cancels what is open. It is written first and at once, then it waits (up to 20 seconds) for an order already being sent, and tells
you what happened to it: cancelled, **filled** (a file cannot undo a fill: sell at your broker if you want out) or **unresolved** (its outcome is not known: look at your broker's own order list). An order
is reported as cancelled only when the broker's own order book says so: a cancel the broker merely accepted is looked for in the book for up to 10 seconds, and what is still open after that is reported as
**failed**, what cannot be found or read as **unresolved**, an order that filled while its cancel was in flight as **filled**, and a part-fill as **part-filled** (the filled part stays).

A send whose outcome is not known (a timeout or lost answer, a failure after the order may have been accepted, the program stopped mid-send, or an order placed whose audit line could not be written) is
kept on record, and so is anything in the broker adapter's own lost-answer ledger (`python -m athena.brokers unresolved`). While either exists, `resume` and every new order, whatever it is, are refused, and
a ledger that cannot be read counts as unknown. A person looks at the broker's order list and runs `python -m athena.brokers resolve-send --note "what you checked"`, at a console: it clears both together
and writes one line in the audit log. Nothing retries an unknown send by itself. The one failure that needs no such step is a refusal the broker answered with (an HTTP 4xx): nothing was placed.

What this does **not** protect against: a program running as you that reads your settings file and calls your broker directly. Keep the settings file private, and use the smallest limits that suit you.

## Limits worth knowing

- Delivery (CNC) limit orders on NSE and BSE only, long only. No market orders, no intraday, no F&O.
- The daily-loss limit works from the realised profit and loss the desk records, so it is measured for paper trading only. Real brokers do not give it per order: for a real broker it is UNAVAILABLE (see
  above), and the limits that bite are order value, orders per day, money deployed and the allow-list. Check your broker's own report. Nothing here reconciles a real account's loss; that needs a separately
  authorised live programme and has not been done.
- Prices for the "is this limit price sane" check come from the terminal's live stream (Angel One's feed today), whichever broker you trade with. The terminal must be running.
- Tokens expire every day (Groww at 6 AM, Zerodha, Upstox, ICICI and Angel One each in their own way). An expired token is reported as such and nothing is sent.
- If the connection drops while an order is being sent, the order may or may not have reached the broker. The desk records this and refuses to send it again; look at your broker's order list.

## Adding another broker

Write one subclass of `athena.brokers.live.base.LiveBroker` (five methods that talk to the broker's API and map its answers to `Funds`, `Holding` and `Order`), add a line to `ADAPTERS` in
`athena/brokers/live/__init__.py` and a row in `athena/brokers/registry.py`. The desk, limits, audit log and confirmation step apply to it unchanged. Test it offline with a fake transport, as the
tests in `tests/test_broker_*.py` do. Take the existing adapters as the pattern, and say in the guide what you could not confirm.

Athena is a personal research tool, not investment advice, and the authors are not responsible for orders placed with it.
