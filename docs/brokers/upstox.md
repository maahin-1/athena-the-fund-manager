# Upstox live adapter

Code: `src/athena/brokers/live/upstox.py` (`UpstoxBroker`, registry key `upstox`). Delivery (product `D`) limit orders, `DAY` validity, NSE/BSE equity, long only.

## Access steps
1. Open an Upstox account, create an app in the Upstox developer console (My Apps), set a redirect URL you control.
2. Register a static IP (one primary, one secondary) in the app. Order calls from any other IP are rejected. Reads are not restricted.
3. Put the settings below in the settings file (never in code or chat).

## Settings (names only)
- `UPSTOX_ACCESS_TOKEN` (required): today's token.
- `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI` (optional): only the daily login helpers need them.

## Daily token step
The token dies at 03:30 the next morning, whatever time it was made; there is no refresh token for trading. Each trading day: open `authorize_url(api_key, redirect_uri, state)`, log in, copy `code` from the redirect (check `state` matches), call `exchange_auth_code(api_key, api_secret, code, redirect_uri)`, and store the result in `UPSTOX_ACCESS_TOKEN`. No browser automation.

## Behaviour
- Place and cancel use the v3 order API (host `api-hft.upstox.com`), which Upstox documents as the current one. Reads use v2.
- `tag` carries the client order id (max 40 characters). An id that is longer, or has characters outside letters, digits, `-` and `_`, is replaced by a 40-character hash; the ledger still maps it.
- Symbols resolve through the instruments file (`NSE.json.gz`), equity (`NSE_EQ`, type `EQ`) only, cached once a day at `.athena/broker/upstox-instruments-<EXCH>.json`. Unknown or ambiguous symbol: error, nothing sent.
- Statuses: all 17 of the appendix are mapped (pending/open/modify/cancel-pending -> `open`, `complete` -> `filled`, `cancelled*` -> `cancelled`, `rejected`). Any other text raises.
- `order_timestamp` has no zone in the docs; it is read as IST (+05:30) and written as ISO-8601 with that offset.
- Order-book rows that are not delivery limit orders on NSE/BSE are left out of `orders()`.
- Limits: order APIs 10/s, 500/min, 2000 per 30 min; other APIs 50/s, 500/min, 2000 per 30 min. Order window 05:30 to 24:00 IST. No paid plan is named on the pages read.

## Verified (pages read 2026-10-29)
- https://upstox.com/developer/api-documentation/v3/place-order/ (URL, body, `tag` max 40, response `data.order_ids`)
- https://upstox.com/developer/api-documentation/v3/cancel-order/ (DELETE, `order_id` query)
- https://upstox.com/developer/api-documentation/get-order-book/ (URL, fields)
- https://upstox.com/developer/api-documentation/get-holdings/
- https://upstox.com/developer/api-documentation/appendix/order-status/
- https://upstox.com/developer/api-documentation/get-token/ and /authorize/
- https://upstox.com/developer/api-documentation/instruments/
- https://upstox.com/developer/api-documentation/rate-limiting/
- https://upstox.com/developer/api-documentation/announcements/algo-trading-circular/ (static IP, `X-Algo-Name`)

## NOT verified
- Funds: the docs page was not readable. Path `/v2/user/get-funds-and-margin?segment=SEC` and the `data.equity.{available_margin,used_margin}` shape come from the SDK docs and memory; `cash = available + used` is my reading.
- Authorize dialog host (page gives the path only); `BSE.json.gz` name and BSE trading-symbol form; tag character set.
- Whether `quantity` in holdings includes `t1_quantity`.
- Whether a bare-array or wrapped order-book answer is current (both accepted).
- `X-Algo-Name` is not sent; needed only if the app is an Algo App.
- Never run against a live account; nothing here has touched the real trading endpoints.
