# Phase 3 on Angel One: design note (draft for the owner's sign-off)

Status: **draft, no code written.** Replaces the Zerodha / Kite plan in TRD sections 2.10, 4 and 5 (Oct 5 broker decision). For the owner's own account only; not investment advice.

## 1. What exists today

- `athena.brokers.angel_login`: SmartAPI login from the settings file (API key, client code, PIN, TOTP secret). Read-only. Tokens never printed.
- `terminal/src/angel/`: login, instrument master, WebSocket stream (cash, futures, options, indices). Prices only.
- `athena.backtest`: an in-house paper engine (long-only, next-open fills, cost model in `athena.costs`).
- `athena.risk_overlay`: position-size, concentration, VaR and liquidity checks that can hold a Buy back to Hold.
- `athena.brokers.kite_login` is the old Zerodha login. It stays out of the build; remove it once Angel One works.

Nothing places an order. Nothing reads the account (funds, holdings, positions).

## 2. Principles (from TRD section 5, restated for this build)

1. The model never places, sizes or times an order. A person does, through a ticket in the terminal.
2. Preview first, then a repeat call that carries a one-time confirm token. A single call can never trade.
3. Paper or live is one explicit setting in a config file the chat cannot edit. Never inferred.
4. Capability tiers in that file: `read_only` (default), `paper`, `live_delivery`. F&O, MCX and margin products are not in the first live tier.
5. Every credential, token and broker error body passes through the redaction layer before it is logged or shown. Broker error text is untrusted.
6. Every order, preview, confirm, rejection and fill is appended to an audit log that is never rewritten.

## 3. Build slices (each is testable alone, in this order)

| Slice | What it delivers | Needs the live broker? |
|---|---|---|
| A1 | `Broker` interface and a `FakeBroker` used by every test; the paper broker on live quotes (fills at the touch plus the cost model) | no |
| A2 | Read-only account sync: funds, holdings, positions, order book; a Portfolio panel in the terminal | yes, read only |
| A3 | Order ticket and **preview**: the instrument, side, quantity, limit price, estimated charges, the risk overlay's findings, and the reason a person gave | no orders sent |
| A4 | Confirm token and **paper** execution end to end (ticket, preview, confirm, fill, audit) | no |
| A5 | **Live delivery orders** behind the config gate and the limits in section 4 | yes |
| A6 | Reconciliation: our book against the broker's after every fill and at the close; any difference stops trading and says so | yes |

A1 to A4 can be built and tested with no money at risk. A5 is the only slice that can move money, and it is last.

## 4. Limits for the live tier (all in the config file; every number editable by the owner by hand)

- Allow-list of symbols; default empty (so live is off until the owner adds names).
- Maximum value per order, maximum orders per day, maximum total deployed, maximum loss per day (hitting it stops new orders for the day).
- Delivery (CNC) only, limit orders only, no short selling, no F&O, no MCX, no margin product in the first release.
- Market hours only; no order within the first and last few minutes unless the owner sets otherwise.
- A **kill switch**: one file or button that sets the tier to `read_only` at once and cancels open orders. It also trips by itself on a reconciliation difference or repeated broker errors.
- Every order carries our own tag and a client order id, so a retry can never place it twice.

## 5. Open questions for the owner

1. **"Its subsidiaries."** I know Angel One's SmartAPI (equity, F&O, commodities, GTT). If you meant other group products (for example a mutual-fund or wealth platform), name them; I will check their APIs before designing for them.
2. **Account rules to verify before A5.** India's rules for retail API trading have been changing (broker-registered strategies, static IP for order APIs, per-second order limits). I have not verified Angel One's current terms for your account type, so this note does not assume them. A5 does not start until I have read Angel's current published terms and you have confirmed your account can place API orders.
3. **Starting limits.** Suggested first values: Rs 10,000 per order, 3 orders a day, Rs 25,000 deployed, Rs 1,000 daily loss. Your numbers decide.
4. **Does the debate verdict feed the ticket?** Suggestion: it pre-fills the reason text only, and never the quantity or price.

## 6. Not in scope

Algorithmic or unattended trading; any model-chosen order; F&O and commodity execution; margin and leverage; multi-account use; sharing or selling the tool.

## 7. Verification plan

Fake-broker tests for every slice, including: preview without confirm places nothing; a reused or expired token is refused; a limit breach is refused with the reason; the kill switch cancels and blocks; a reconciliation difference stops trading; redaction of a planted token in a broker error. A5 gets a dry run with the smallest allowed order, watched by the owner, before anything else.
