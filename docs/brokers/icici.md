# ICICI Direct (Breeze Connect) adapter

Module: `src/athena/brokers/live/icici.py` (`IciciBroker`, broker name `icici`). Delivery limit orders only, long only, NSE or BSE.

## Access steps
1. Register an app at the ICICI Direct API portal (api.icicidirect.com) to get an app key and a secret key. Register the static IP the orders will come from at the same time.
2. Put the three settings below in the settings file (never in code or chat).

## Settings (names only)
- `ICICI_APP_KEY`
- `ICICI_SECRET_KEY`
- `ICICI_SESSION_TOKEN` (changes every day)

## Daily session step
1. Open `https://api.icicidirect.com/apiuser/login?api_key=<url-encoded app key>` and log in.
2. Copy the `API_Session` value from the address bar after login.
3. Call `athena.brokers.live.icici.exchange_api_session(app_key, api_session)`; it returns the session token. Save it as `ICICI_SESSION_TOKEN`.
No browser automation is provided. A 401/403 from the API is reported as an expired session.

## Verified (docs and SDK source read 2026-10-29)
- https://api.icicidirect.com/breezeapi/documents/index.html : endpoints, order body fields, statuses (Requested, Queued, Ordered, Partially Executed, Executed, Rejected, Expired, Partially Executed And Expired, Partially Executed And Cancelled, Freezed, Cancelled), checksum rule (SHA256 of timestamp + body + secret key, "token " prefix, UTC ISO timestamp, 60 second tolerance), session flow, limits.
- https://github.com/Idirect-Tech/Breeze-Python-SDK (`breeze_connect.py`, `config.py`): base URL `https://api.icicidirect.com/breezeapi/api/v1/`, endpoints `funds`, `dematholdings`, `portfolioholdings`, `order` (GET list, POST place, DELETE cancel), body sent as raw `data` on every verb, security master at `https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip` (`NSEScripMaster.txt`, `BSEScripMaster.txt`, columns `ExchangeCode`, `ShortName`).
- Limits: 100 calls/minute, 5000/day, 10 orders/second shared by place/cancel/modify/square-off. Orders must come from the registered static IP; it can be changed once a week.
- Order placement body (cash): `stock_code, exchange_code, product=cash, action, order_type=limit, quantity, price, validity=day, user_remark`.

## NOT verified
- No real account was used: nothing was sent to any endpoint. All tests use a fake transport.
- Whether API order placement needs a separate activation: the docs do not say.
- Exact funds field names for "cash" and "blocked": `allocated_equity` and `block_by_trade_equity` are inferred.
- `portfolioholdings` response shape and the from_date it accepts (2000-01-01 is a guess); `dematholdings` has no price, so average price needs one extra call per stock.
- Time zone of `order_datetime` (assumed India time), the order-list error body, how an empty list is returned (null is treated as empty), whether the order list shows a rejection reason (none is read).
- The meaning of `ExchangeCode` in `BSEScripMaster.txt` (assumed to be the BSE code Athena uses as symbol).
- Whether the SDK's extra empty body fields (`settlement_id`, `order_segment_code`, `lots`) are required for cash; they are not sent.
- `user_remark` allows no spaces or special characters, so a client order id with a hyphen is sent as `A` + 19 hex characters of its sha256.
