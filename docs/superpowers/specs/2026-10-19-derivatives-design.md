# Futures, options and MCX (5f)

Date 2026-10-19. Futures and options on NSE (NFO), BSE (BFO) and MCX: the instrument list, an option chain with implied volatility and Greeks, open interest, put-call ratio, max pain, a volatility curve, and a payoff builder. Per decisions D6 (MCX through Angel) and D10 (built against a fake feed first, verified live in a market session) in the master design.

**Status line this spec commits to:** the page is built and tested against a synthetic feed only. What exists for live data is the part that can be tested without a market: the contract list, and the stream decoder's reading of open interest from the snap-quote packet. **Not built:** subscribing derivative tokens on the stream (exchange types 2, 4 and 5), carrying open interest through the quote hub, and a route that returns live contract quotes. That path touches the live feed, cannot be exercised outside a market session, and is left as its own step (Task B2 in the plan) to be done and checked with a session open. Until then the Options page shows synthetic prices, labelled as such, whatever the feed. Nothing here claims live behaviour.

## 1. The instrument list

Source: Angel's public instrument list (`OpenAPIScripMaster.json`, 153,484 rows on 2026-10-19; no login). Rows used:

| Segment | Types | Rows |
|---|---|---|
| NFO | FUTIDX, FUTSTK, OPTIDX, OPTSTK | 18, 653, 5,791, 31,908 |
| BFO | FUTIDX, FUTSTK, OPTIDX, OPTSTK | 12, 635, 5,924, 34,226 |
| MCX | FUTCOM, FUTIDX, OPTFUT, OPTIDX | 168, 7, 14,796, 630 |

Each row gives token, symbol, underlying name, expiry (`29DEC2026`), strike (in paise-like hundredths: `2300000.000000` is 23,000), lot size, tick size and freeze quantity. Out of scope: currency (CDS), NCDEX, NCO and the commodity spot rows.

The page of derivatives reads a compact table built from the list, kept on disk next to the cash list and refreshed daily, with the same fail-safe as the cash list (a refresh that returns nothing keeps the old table).

Facts the table must get right, each tested against rows in the real list:
- strike = the list's number divided by 100; futures have no strike;
- a contract's kind is Future, Call or Put (from the symbol's last two letters `CE`/`PE`, or `FUT`);
- expiry is a date, parsed from `DDMMMYYYY`; contracts with an expiry before today are dropped;
- the **underlying** is the row's `name`, except that test and odd-lot names (`051NSETEST`, names with `FPI`) are listed under their own name and flagged so the page can hide them by default;
- a stock option and a future of the same name and expiry belong to the same underlying.

## 2. API

`GET /api/derivs/underlyings?segment=NFO|BFO|MCX` the underlyings that have contracts, with kind (index, stock, commodity), lot size of the nearest future, the number of expiries.
`GET /api/derivs/expiries?underlying=NIFTY` the dated expiries in order, with which have futures, options or both, and a mark for the weekly and monthly series.
`GET /api/derivs/chain?underlying=NIFTY&expiry=2026-11-03` the strikes of that expiry with the Call and Put contract (token, symbol, lot size, tick size) per strike, and the future of that expiry when there is one. No prices: the page asks for them separately.
`GET /api/derivs/futures?underlying=NIFTY` the futures of an underlying by expiry (the page's "futures" list and the continuous-contract roll table).
Bad or unknown input is 400 or 404 with a reason, never a panic; bounds: at most 600 strikes in a chain response.

## 3. Prices and open interest

Contract prices reach the page through one interface (`OptionFeed`): `quotes(tokens)` returns last price, bid, ask, volume, open interest and change in open interest per token. Two implementations:
- **Synthetic** (used under `--fake-feed` and in tests): a closed-form Black-76 price from a synthetic spot and a smile, with open interest and volume shaped like a real chain (largest near the money, decaying away), seeded so a given contract always gets the same numbers on the same day. It exists to exercise every screen; it makes no claim about real markets and the page labels it "synthetic prices" whenever it is in use.
- **Angel** (decoder built; subscription and route not built, see the status line): the stream's snap-quote mode for derivative tokens (open interest at offset 131 and its change in percent at 139 of the packet) with the segment's exchange type, within the stream's token limit (the chain would subscribe to the strikes in view, at most 100 tokens).

The snap-quote decoder gains open interest and its change in percent (done); its tests use the packet layout in the Angel documentation and a crafted packet, as the cash decoder's do. The offsets are the documented ones and have not been compared with a real derivative packet.

## 4. Greeks and implied volatility

Pure functions in the page (`options/`), all tested against known values:
- **Black-76** on a forward `F`: price, implied volatility (bracketed Newton with bisection fallback; refuses a price below intrinsic or above the bound with a reason rather than returning a number), delta, gamma, vega (per 1 volatility point), theta (per calendar day) and rho, for calls and puts. Time to expiry runs to 15:30 IST of the expiry date, in years of 365 days, with a floor of one minute.
- The **forward** of an expiry is the future of that expiry when it trades, else spot grown at the risk-free rate (from the interest rate setting, default 7%, no dividends); an index option with a future uses the future.
- The chain's **ATM strike** is the strike nearest the forward; **put-call ratio** is total put open interest over total call open interest (and by volume); **max pain** is the strike where the total payout of all option writers at expiry is least.
- **Volatility curve:** implied volatility by strike for one expiry (out-of-the-money options only, calls above the forward and puts below, which is the usual smile); **ATM term structure:** ATM implied volatility by expiry.
- Honest limit: Black-76 is European. Index options are European (exact); stock options on NSE are physically settled and European since 2018 (also exact); MCX options on futures are American-style on some products, so their implied volatilities and Greeks are approximate and the page says so for MCX.

## 5. The page

A new **Options** tab (command `OPTIONS NIFTY`, `CHAIN`, also reachable from the symbol details panel for an optionable stock).
- Top bar: underlying, expiry, a strikes-around-ATM choice (10, 20, 40 or all), a toggle for the Greeks columns, and the spot/forward, ATM, PCR and max pain as a line.
- **Chain:** calls on the left (open interest with a bar, change in OI, volume, IV, last, bid, ask), strike in the middle, puts on the right; in-the-money side shaded; the ATM row marked; a click on a price adds that contract to the payoff builder as a buy (shift: sell).
- **Volatility curve** and **ATM term structure** as two small charts beside or under the chain.
- **Payoff builder:** legs (buy or sell, call, put or future, lots, price taken from the chain or typed), the payoff at expiry and the payoff now (Black-76 at today's implied volatilities, adjustable by a volatility shift and a days-forward slider), break-evens, maximum profit and loss, net premium, margin is **not** estimated (no free exact source; the page says so), and the net Greeks of the position.
- **Futures list** under the chain: each expiry's price, change, open interest, basis against spot and annualised basis.
- Everything shows "synthetic prices" while the fake feed is in use.

## 6. Continuous futures (second step of this item)

`NIFTY1!` and `NIFTY2!` as Chart Lab symbols: the front and second contract stitched into one series. The roll rule is ours and is written on the chart: roll to the next contract at the close of the day before the last trading day of the current one; no back-adjustment (the series jumps by the roll basis, and the roll dates are marked), with an adjusted option (ratio back-adjustment) offered as a choice. Needs daily futures history: the free source is the exchange's F&O bhavcopy, one file per trading day, loaded into the warehouse by a resumable, rate-limited fill like the cash one. **This is a separate piece of work after the chain and payoff builder, with its own sub-plan,** because the history fill is a long run.

## 7. Out of scope

Margin and SPAN estimates; order entry; strategy backtests of options (the history is not there); currency and NCDEX; Greeks from Angel's endpoint (needs a login session; the local Black-76 is what is built, and Angel's can be compared in a session); open interest history charts (needs recording, which waits for a session like tick recording).

## 8. Testing

Rust: parsing each row kind with real rows pasted as fixtures, strike and expiry, dropping expired contracts, a refresh that returns nothing keeps the table, every API route's bad inputs, chain bounds, the snap-quote OI offsets. Pure TypeScript: Black-76 against textbook values and put-call parity, IV round trip across a grid, edge cases (deep in and out of the money, almost expired, price below intrinsic), ATM, PCR, max pain on hand-made chains, volatility curve choice of calls and puts, payoff of every leg kind and of combinations (straddle, iron condor, covered future), break-evens, max profit and loss, the synthetic feed's determinism and consistency (its prices satisfy put-call parity and its IVs reproduce the smile). Page: chain rendering with a stub feed, ATM and PCR lines, adding legs by click, payoff numbers. End to end: open the tab under the fake feed, choose an expiry, see the chain and the curve, build a straddle and read its break-evens.
