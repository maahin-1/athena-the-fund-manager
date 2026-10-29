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
3. **Pin the limits.** `python -m athena.brokers pin-limits` at a console. Any later change to the file, by anyone, stops real orders until you pin it again.
4. **Preview, then confirm at a console.** `preview` checks every rule and gives a one-time code. `confirm CODE` then asks you to type the order back (for example `BUY 5 SBIN 101.00`). A script, a
   scheduler or an assistant has no console, so it cannot send the order.
5. **Audit log.** Every step is written to `.athena/broker/audit.jsonl`, each line chained to the one before and signed with a key kept outside the project (in your user folder).
   `python -m athena.brokers audit --verify` checks it.

The kill switch (`python -m athena.brokers kill`) refuses every order and cancels what is open.

What this does **not** protect against: a program running as you that reads your settings file and calls your broker directly. Keep the settings file private, and use the smallest limits that suit you.

## Limits worth knowing

- Delivery (CNC) limit orders on NSE and BSE only, long only. No market orders, no intraday, no F&O.
- The daily-loss limit works from the realised profit and loss the desk records. Real brokers do not give it per order, so for a real broker the limits that bite are order value, orders per day,
  money deployed and the allow-list. Check your broker's own report.
- Prices for the "is this limit price sane" check come from the terminal's live stream (Angel One's feed today), whichever broker you trade with. The terminal must be running.
- Tokens expire every day (Groww at 6 AM, Zerodha, Upstox, ICICI and Angel One each in their own way). An expired token is reported as such and nothing is sent.
- If the connection drops while an order is being sent, the order may or may not have reached the broker. The desk records this and refuses to send it again; look at your broker's order list.

## Adding another broker

Write one subclass of `athena.brokers.live.base.LiveBroker` (five methods that talk to the broker's API and map its answers to `Funds`, `Holding` and `Order`), add a line to `ADAPTERS` in
`athena/brokers/live/__init__.py` and a row in `athena/brokers/registry.py`. The desk, limits, audit log and confirmation step apply to it unchanged. Test it offline with a fake transport, as the
tests in `tests/test_broker_*.py` do. Take the existing adapters as the pattern, and say in the guide what you could not confirm.

Athena is a personal research tool, not investment advice, and the authors are not responsible for orders placed with it.
