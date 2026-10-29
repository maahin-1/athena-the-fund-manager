# Zerodha (Kite Connect v3) live adapter

Code: `src/athena/brokers/live/zerodha.py`. Delivery (CNC) limit orders only, long only.

## Getting API access
1. Have a Zerodha trading account, then create a developer app at https://developers.kite.trade (needs the account login).
2. Set the app's redirect URL to `http://127.0.0.1:5000/` (what the login helper listens on).
3. Copy the app's API key and secret into the settings file.

## Settings (names only)
- `KITE_API_KEY` (required)
- `KITE_ACCESS_TOKEN` (required; renewed daily)
- `KITE_API_SECRET` (optional for the adapter; the daily login step needs it)

## Daily step
The access token expires every morning (about 6 AM). Each trading day run:

    python -m athena.brokers.kite_login --listen

An expired token shows up as HTTP 403 TokenException; the adapter turns it into an error that says the daily token expired and names this command.

## Cost
Per the user's note and not re-verified here: orders work on the free Personal plan; market data (quotes, historical candles) needs the paid Connect plan. The current price was not read from the docs page (https://kite.trade/docs/connect/v3/ gave no pricing), so check the developer portal.

## How the API is mapped
- Header: `Authorization: token api_key:access_token`, `X-Kite-Version: 3`.
- Funds: `GET /user/margins/equity`; cash = `available.cash`, reserved = `utilised.debits` (equals `net`/`live_balance` in the docs' example). Collateral is not counted.
- Holdings: `GET /portfolio/holdings`; quantity = `quantity` (settled, T+2) + `t1_quantity` (T+1 quantity), so freshly bought shares count as owned. Pledged shares are not added.
- Orders: `GET /orders` (today only). `OPEN`, `TRIGGER PENDING`, `UPDATE`, `MODIFIED`, `PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`, `OPEN PENDING`, `MODIFY VALIDATION PENDING`, `MODIFY PENDING`, `CANCEL PENDING`, `AMO REQ RECEIVED` = open; `COMPLETE` = filled; `CANCELLED`; `REJECTED`. Any other status is an error. A partly filled open order stays open with `filled_quantity`. Non-CNC orders (the person's own) are left out.
- Place: `POST /orders/regular`, form body with `tradingsymbol, exchange, transaction_type, quantity, product=CNC, order_type=LIMIT, price, validity=DAY, tag`.
- `tag`: docs say alphanumeric, max 20 characters; the tag is `A` + 19 hex characters of sha256(client order id).
- Cancel: `DELETE /orders/regular/:order_id`.
- `order_timestamp` has no zone in the docs; it is assumed to be India time (+05:30).

## Verified against the docs (read 2026-10-29)
- https://kite.trade/docs/connect/v3/orders/ (endpoints, form fields, tag limit, statuses, order fields)
- https://kite.trade/docs/connect/v3/portfolio/ (holdings fields)
- https://kite.trade/docs/connect/v3/user/ (margins, Authorization header)
- https://kite.trade/docs/connect/v3/exceptions/ (error body, TokenException with 403)

## NOT verified
- Nothing has touched a real account or any live endpoint; the tests use a fake transport with fixtures shaped like the docs.
- Rate limits (the rate-limit page returned 404); none are enforced by the adapter.
- The timezone of `order_timestamp` (assumed IST); whether `quantity` in holdings includes pledged shares; the full list of statuses (docs say others can occur); plan prices.
