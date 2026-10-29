# Groww (Groww Trade API) adapter

Code: `src/athena/brokers/live/groww.py` (plain REST, no SDK). Tests: `tests/test_broker_groww.py` (offline, fake transport). Nothing here has touched a real account.

## Getting access

1. A Groww account with a Trading API subscription (the docs say an active "Trading API Subscription" is required; the price is not stated on the pages read, so check the purchase page).
2. Create the API key in the Groww Cloud API Keys page. Only equity (CASH) and F&O are offered; this system uses delivery (CNC) limit orders on NSE or BSE only.

## Settings (put the name in the settings file, value on the same line, never in git)

```
GROWW_ACCESS_TOKEN=
```

## The daily token step

The access token expires every day at 6:00 AM. Each morning generate a new one (Groww profile, Trading APIs settings, or the key-plus-secret "approval" flow, which itself needs a daily
approval on the API Keys page) and paste it into `GROWW_ACCESS_TOKEN`. This adapter does not mint tokens: the key/secret/TOTP flow (`POST /v1/token/api/access`, 150 calls per 24 hours) is
not implemented. An expired token shows up as an HTTP 401 or 403 error from `ping`.

## What the adapter calls

| Need | Call |
|---|---|
| headers | `Authorization: Bearer <token>`, `Accept: application/json`, `X-API-VERSION: 1.0` |
| funds | `GET https://api.groww.in/v1/margins/detail/user` -> `equity_margin_details.cnc_balance_available` is used as the available cash; reserved is 0 (no such figure is given) |
| holdings | `GET /v1/holdings/user` -> `payload.holdings[]` (`trading_symbol`, `quantity`, `average_price`) |
| orders | `GET /v1/order/list?segment=CASH&page_size=100` -> `payload.order_list[]` |
| place | `POST /v1/order/create` with `trading_symbol, quantity, price, validity=DAY, exchange, segment=CASH, product=CNC, order_type=LIMIT, transaction_type, order_reference_id` |
| cancel | `POST /v1/order/cancel` with `segment=CASH, groww_order_id` |

Client order id: carried in `order_reference_id` (8 to 20 alphanumeric characters, at most two hyphens; a duplicate gives error GA007). A client id that does not fit is replaced by
`AT` + 18 hex characters of its SHA-256, which is stable across retries.

Statuses: OPEN, NEW, ACKED, TRIGGER_PENDING, APPROVED, MODIFICATION_REQUESTED, CANCELLATION_REQUESTED -> open; EXECUTED, DELIVERY_AWAITED, COMPLETED -> filled (must show a filled
quantity above zero); CANCELLED -> cancelled; REJECTED, FAILED -> rejected. Any other status is an error. `placed_at` comes from `created_at` (or `exchange_time`; with neither, an error). A filled order must show a filled quantity equal to the order quantity, else an error. Orders that are not CNC LIMIT on NSE/BSE (the person's own market or intraday orders) are skipped before their status or price is read.

Rate limits (docs): orders (create, modify, cancel) 10 per second and 250 per minute; non-trading reads (order list, holdings, margin) 20 per second and 500 per minute.

## Verified against the documentation (read 2026-10-29)

Sources: https://groww.in/trade-api/docs/curl , /curl/orders , /curl/portfolio , /curl/margin , /curl/annexures , /curl/instruments (all under https://groww.in/trade-api/docs).
Verified there: base URL, headers, endpoint paths and methods, request body fields, the response shapes above, enum values, `order_reference_id` rules, the daily token expiry, the rate
limits, the subscription requirement.

## NOT verified

- Nothing has been run against a real account or the live service. Response shapes come from the documentation examples.
- Whether an NSE equity `trading_symbol` is plain (`SBIN`) or has a series suffix (`SBIN-EQ`). The order example shows `IDEA` for create and `RELIANCE-EQ` for a detail response; the adapter sends the
  plain symbol and strips a trailing `-EQ` when reading. How BSE symbols are written is not stated; the instrument master is https://growwapi-assets.groww.in/instruments/instrument.csv.
- The time zone of `created_at` (examples have none): read as India time (+05:30).
- Paging of the order list: not confirmed, so the adapter asks for 100 orders without a `page` value and raises an error if a full page of 100 comes back (it will not silently ignore the rest).
- `OPEN` appears in the order examples but not in the annexure status list; it is treated as open.
- Holdings carry no exchange field; the adapter labels them NSE.
- Which of `cnc_balance_available`, `clear_cash` best equals "cash available for a delivery buy"; the adapter uses the first.
- The exact subscription price and whether any plan limits the number of orders.
