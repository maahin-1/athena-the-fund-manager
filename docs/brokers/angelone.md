# Angel One (SmartAPI) live adapter

Code: `src/athena/brokers/live/angelone.py`. Delivery (DELIVERY/CNC) limit orders on NSE/BSE only, long only.

## Access steps
1. Have an Angel One trading account with TOTP enabled (the text key beside the QR code is the TOTP secret).
2. Create a SmartAPI app at https://smartapi.angelone.in to get the API key.
3. Put the settings below in the settings file (values never in code, docs or logs).
4. Register your static IP with Angel One if its API terms ask for one (see "Not verified").

## Settings (names only)
Required: `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`.
Optional: `ANGEL_PUBLIC_IP` (the static IP registered with Angel One; if unset the machine's own local IP is sent), `ANGEL_MAC_ADDRESS` (default: this machine's).

## Login and refresh
The adapter logs in lazily on its first call through `athena.brokers.angel_login.login` (SmartApi SDK, TOTP made from the secret) and keeps the JWT in memory. Tests and callers can pass `jwt_token=` or `login_fn=` instead. There is no automatic re-login: when the token expires (Angel tokens last about a day) calls fail with `BrokerApiError` and a new adapter object logs in again.

## Calls
Host `https://apiconnect.angelone.in`. Headers: `Authorization: Bearer <jwt>`, `X-PrivateKey`, `X-UserType: USER`, `X-SourceID: WEB`, `X-ClientLocalIP`, `X-ClientPublicIP`, `X-MACAddress`, JSON content type. No "what is my IP" service is called.
- Funds `GET .../user/v1/getRMS` (`availablecash`), holdings `GET .../portfolio/v1/getAllHolding` (`data.holdings`), orders `GET .../order/v1/getOrderBook`, place `POST .../order/v1/placeOrder`, cancel `POST .../order/v1/cancelOrder`.
- Place body: variety NORMAL, producttype DELIVERY, ordertype LIMIT, duration DAY, price/quantity as strings, squareoff and stoploss "0", `ordertag` = client order id (if over 20 characters, the first 20 of its SHA-256).
- A 200 reply with `status` not true is an error. An unknown order status raises; it is never read as filled. Order times have no zone and are read as India time (+05:30).
- Symbol tokens come from `OpenAPIScripMaster.json`, filtered to NSE/BSE equity rows and cached in `.athena/broker/angelone-scrip-master.json`, refetched on the first use of each India-time day. Only the `-EQ` series resolves; anything else is an error.
- Orders in the book that are not DELIVERY LIMIT on NSE/BSE (placed in the app) are skipped.

## Verified, and where (read 2026-10-29)
- Endpoint paths, header names and default header values, place/cancel/book call shapes, README order example (no `ordertag` in it): SDK source `smartapi-python` 1.5.3 (`SmartApi/smartConnect.py`) and https://github.com/angel-one/smartapi-python
- `ordertag` exists on placeOrder and getOrderBook, limit 20 characters (from Nov 2024): forum, https://smartapi.angelone.in/smartapi/forum/post/17334
- Order book `updatetime` looks like `16-May-2024 11:18:11`; `status` plus `orderstatus` both exist; `uniqueorderid` returned: forum posts found via search on https://smartapi.angelone.in/smartapi/forum

## NOT verified
- https://smartapi.angelone.in/docs and /docs/Orders could not be read (JavaScript page). Response field names for getRMS and getAllHolding, whether `availablecash` already excludes margin blocked by open buys, and whether holding `quantity` includes T1 shares are from memory of the API, not read.
- The full list of `orderstatus` strings: Angel has not published one (forum says the docs will be updated). The mapped set (open, open pending, trigger pending, validation pending, put order req received, modify pending, modify validation pending, modified, cancel pending, not modified, not cancelled, complete, cancelled, rejected) is partly a guess; unmapped strings raise. After-market order strings (`after market order req received`, `modify after market order req received`, `amo req received`, `amo submitted`) are mapped to open but are NOT confirmed by the SDK or docs. `complete` is filled only when `filledshares` equals the order quantity, else an error. Partial fills are assumed to show as open with `filledshares` above zero.
- Whether `ordertag` accepts every character in a client order id, and whether squareoff/stoploss "0" are still wanted for delivery orders.
- The SEBI/SmartAPI static-IP and API-order rules: not found in anything read. Check the SmartAPI site before going live; the adapter does not enforce an IP.
- Rate limits (forum reports roughly 1 request/second for the order book): not enforced here.
- Cancel uses the `variety` the order book reports for that order.
- No call to a trading endpoint has been made against the real service.
