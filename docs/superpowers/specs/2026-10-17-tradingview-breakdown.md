# TradingView breakdown and gap analysis (findings, 2026-10-17)

Status: **findings and a draft roadmap, for review.** Nothing in this file is built. The roadmap in section 9 is not yet in the TRD or the phase plans; it goes in once approved.

## 1. How this was done, and the limits

| Method | What it gave | Limit |
|---|---|---|
| TradingView's own public pages (pricing, features, support articles), read with a page fetcher | Plan limits, feature areas, drawing-tool names, chart types | Some help pages returned 404 or named a thing without describing it; those gaps are marked |
| REA browser capture of the logged-out chart page (`in.tradingview.com/chart/`): accessibility tree and one screenshot, passive, no script or network capture | The real shape of the UI: toolbars, panels, default watchlist, symbol-details sections | One page, logged out, market shut, NSE data delayed |
| Public web search | Third-party facts (options scripts, import/export, flags) | Third-party; marked as such |
| General knowledge of standard indicator algorithms | The indicator catalogue in section 4 | Names and public maths only; not scraped from TradingView |

**Boundary kept:** TradingView forbids copying or scraping. This study takes behaviour, structure and design ideas only. No code, bundle source, icons, branding, data or Pine Script text was copied, and none will be. The replica is ours: own code, own look, own name.

**Not verified:** anything behind a login (paid plan screens, their option chain UI, their screener columns). Where the list below says "assumed", it comes from the plan page's wording, not from seeing the screen.

## 2. The product in one picture

What a logged-out visitor sees on the chart page (REA capture, 1600x900):

```
top bar:    symbol search | compare | interval | chart type | Indicators | templates | Alert | Replay
            | undo/redo | layout setup | Save | layouts | quick search | settings | fullscreen | snapshot | publish | Trade
left bar:   cursors | trend tools | Fib/Gann | patterns | forecast+measure | brush/shapes | text/annotation | icons
            | measure | zoom | magnet | keep drawing | lock | hide | remove
chart:      title row (name · interval · exchange · flag · more · market status, OHLC, change)
            sell/buy box | indicator legends with hide/settings/remove/more | volume | last-price line + countdown
bottom:     range buttons 1D 5D 1M 3M 6M YTD 1Y 5Y All (each a "period in interval" pair) | go-to-date | clock | ADJ
right dock: Watchlist (sectioned: indices, stocks, futures, forex, crypto) / symbol details
            (price, key facts, performance, seasonals, technicals gauge, ATM IV term structure, volatility curve, profile)
right rail: Watchlist, Alerts, Object tree and data window, AI copilot, Screeners, Pine editor, Calendars,
            Community, Notifications, Chats, Products, Help
```

Default Indian watchlist: NIFTY, BANKNIFTY, SENSEX, CNXIT; stocks RELIANCE, AXISBANK, HDFCBANK, ICICIBANK, BAJFINANCE; futures `NIFTY1!`, `BANKNIFTY1!`, `RELIANCE1!`, `INFY1!`; forex; crypto. Futures use a continuous symbol: `NAME1!` is the nearest expiry, `NAME2!` the next.

## 3. Plans (India page, billed annually; verified from the pricing page)

| | Basic ₹0 | Essential ₹995 | Plus ₹2,095 | Premium ₹4,195 | Ultimate ₹17,333 |
|---|---|---|---|---|---|
| Charts per tab | 1 | 2 | 4 | 8 | 16 |
| Indicators per chart | 2 | 5 | 10 | 25 | 50 |
| Indicators on indicators | 1 | 1 | 9 | 24 | 49 |
| Parallel chart connections | 2 | 10 | 20 | 50 | 200 |
| Price alerts | 3 | 20 | 100 | 400 | 1,000 |
| Watchlist alerts | none | 0 | 0 | 2 | 15 |
| Symbols per watchlist | 30 | 1,000 | 1,000 | 1,000 | 1,000 |
| Portfolios / holdings each | 1 / 20 | 3 / 50 | 4 / 75 | 5 / 100 | 7 / 150 |
| Historical bars | 5K | 10K | 10K | 20K | 40K |
| Minute history | 180 days | 365 days | | | |
| Custom timeframes, seconds, ticks, range bars | no | yes | yes | yes | yes |
| Charts on custom formulas | no | yes | yes | yes | yes |
| Volume footprint, volume profile | no | yes | yes | yes | yes |
| Bar replay | no | yes | yes | yes | yes |
| Chart data export | D, W, M only | all | all | all | all |

Not stated clearly on the page: saved layout counts (1, 5, 10 listed for five plans), minute-history for the upper plans, the AI-screener request counts, market-data subscription counts.

**What this means for us.** The caps are a business model, not a technical need. Athena is a single-user local tool, so the replica ships **every paid feature with no caps** (limits only where memory or speed demand, e.g. a sane indicator count per chart). The features that were paid-only (seconds and tick intervals, custom formulas, footprint, replay, export, indicators on indicators) are the ones to build deliberately, because they are where data limits bite, not plan limits.

## 4. Feature inventory

Counts are TradingView's own claims from its features page unless marked.

### 4.1 Charting
- **Chart types (20+):** Candles, Bars, Hollow candles, Volume candles, Heikin Ashi; Line, Line with markers, Step line, Area, HLC area, Baseline, Columns, High-low; price-based: Range, Renko, Line break, Kagi, Point and figure; indicator-based: Volume footprint, Session volume profile, TPO.
- **Intervals:** seconds, ticks, minutes, hours, days, weeks, months, any custom length, custom range bars. Range shortcuts 1D 5D 1M 3M 6M YTD 1Y 5Y All, each tied to an interval.
- **Scales and modes:** price scale auto, log, percent, indexed to 100; invert; lock scale; adjust for splits/dividends (the `ADJ` toggle); session (regular vs extended); timezone; go-to-date; last-price line and bar countdown; pre/post-market price.
- **Compare and overlay:** compare symbols on the same or a separate scale; spreads and ratios as custom instruments (math on other symbols); charts based on formulas.
- **Multi-chart layouts:** up to 16 charts; sync symbol, interval, crosshair, time range, drawings; per-chart settings.
- **Data window and object tree:** values under the crosshair for every series; a tree of every drawing and indicator with show, hide, lock, order, delete.
- **Other:** undo/redo, snapshot, fullscreen, keyboard shortcuts, quick search (command palette), templates for indicators and for chart style, layouts saved by name (cloud in theirs).

### 4.2 Indicators (400+ built-in, 100,000+ community, custom via Pine)
Standard catalogue by family (names are public algorithms; this is the set to build, not a scrape):
- **Moving averages:** SMA, EMA, WMA, HMA, VWMA, SMMA/RMA, DEMA, TEMA, LSMA, ALMA, KAMA, McGinley, MA cross, MA ribbon, multiple-MA.
- **Trend:** Supertrend, Parabolic SAR, ADX/DMI, Aroon, Ichimoku, Vortex, Alligator, Guppy, Linear regression channel, ZigZag, Chande Kroll stop, Chandelier exit, Mass index, Choppiness, Schaff trend cycle, Coppock.
- **Momentum:** RSI, Stochastic, Stochastic RSI, MACD, CCI, Williams %R, ROC, Momentum, TRIX, Awesome and Accelerator oscillators, Ultimate oscillator, PPO, CMO, DPO, Fisher, Relative vigor, TSI, SMI ergodic, Elder ray, Connors RSI, KST, Balance of power.
- **Volatility:** Bollinger Bands, %B and bandwidth, ATR, Keltner, Donchian, envelopes, price channel, historical volatility, standard deviation, Chaikin volatility, relative volatility, Ulcer index.
- **Volume:** Volume, OBV, A/D, CMF, MFI, VWAP, anchored VWAP, volume oscillator, net volume, ease of movement, force index, Klinger, price-volume trend, Chaikin oscillator, volume delta and cumulative delta (needs tick data), volume profile (visible range, session, fixed range, anchored).
- **Levels and structure:** pivot points (standard, Fibonacci, Camarilla, Woodie, DeMark), auto Fibonacci, previous-day/week/month highs and lows, opening range, fractals, support and resistance zones.
- **Pattern recognition:** candlestick patterns (about 30) and automatic chart patterns.
- **Market breadth and relative:** advance/decline, up/down volume, McClellan, relative strength vs an index, correlation, beta, rank correlation, seasonality.
- **Derivatives:** open interest, OI change, put-call ratio, max pain, ATM IV, IV rank, IV percentile, term structure, volatility curve.
- **Per indicator:** inputs (length, source, multiplier, timeframe), style (colour, width, line type, plot visibility, fills, levels, opacity), a values-on-scale switch, a timeframe override ("on 1D while the chart is 15m"), alerts on its conditions, and indicators fed by other indicators (the paid "indicators on indicators").
- **Custom indicators:** Pine Script v6, a proprietary language with a cloud editor, versioning, profiler and a publish/library flow. **We do not replicate Pine** (proprietary language and ecosystem; Pine import is also already parked). We replace it with our own formula language (section 6).

### 4.3 Drawing tools (110+)
- **Cursors:** cross, dot, arrow, demonstration, magic, eraser.
- **Lines:** trendline, ray, info line, extended line, trend angle, horizontal line, horizontal ray, vertical line, crossline. **Channels:** parallel, regression trend, flat top/bottom, disjoint. **Pitchforks:** classic, inside, Schiff, modified Schiff.
- **Fibonacci:** retracement, extension, channel, time zone, speed fan, trend-based time, circles, spiral, arcs, wedge, pitchfan. **Gann:** box, square fixed, square, fan.
- **Patterns:** XABCD, Cypher, head and shoulders, ABCD, triangle, three drives, Elliott waves; cycles: cyclic lines, time cycles, sine line.
- **Forecast and measure:** long and short position, position forecast, bars pattern, ghost feed, sector; price range, date range, date-and-price range; volume-based: anchored VWAP, fixed-range and anchored volume profile.
- **Shapes:** rectangle, rotated rectangle, circle, ellipse, triangle, arc, curve, double curve, path, polyline; brush, highlighter; arrows and arrow markers.
- **Annotation:** text, note, price note, pin, table, callout, comment, price label, signpost; images, icons, emojis.
- **Utilities:** measure, zoom, magnet (weak, strong), keep drawing, lock all, hide all, remove, sync drawings across charts and symbols.
- **Per drawing:** colour, width, style, extend left/right, labels, visibility by timeframe, lock, alert on touch or cross (a "drawing alert"), saved per symbol.

### 4.4 Watchlists and symbol pages
- Multiple named lists; coloured **sections**; coloured **flag lists**; drag to reorder; import and export as `.txt` (comma separated, exchange prefix); column choice (Last, Chg, Chg%, plus more); **advanced view** with sections Overview (Price, Financials, Performance), Technicals, Financials, and grouping by currency, sector, type or exchange; alerts on a whole list.
- **Symbol details panel:** price and pre/post price, key facts, performance (1W to 1Y, YTD), **seasonals** (year-on-year paths), **technicals gauge** (a Buy/Sell summary from oscillators and moving averages), news, profile (website, employees, ISIN), and for indices and F&O underlyings an **ATM IV term structure** and **volatility curve**.
- **Symbol pages** (per ticker): overview, financials, forecasts, technicals, news, community ideas.
- **Portfolios:** holdings, transactions, performance.

### 4.5 F&O, indices and expiries
Seen in the capture: NSE and BSE indices as first-class symbols (NIFTY, BANKNIFTY, SENSEX, CNXIT); continuous futures `NIFTY1!`, `BANKNIFTY1!`, stock futures `RELIANCE1!`; USDINR; the details panel shows option-derived IV for the index.
From TradingView's feature list: **options chain** (real-time quotes, volatility, Greeks, theoretical prices), **strategy builder** (P&L and Greeks profiles), **volatility curves by expiry**.
Not found in public docs: how expiry rollover for continuous contracts is configured; weekly vs monthly expiry handling (third-party scripts make users type expiry by hand). So this part is specified by us from Indian market rules, not copied from them (section 7).

### 4.6 Alerts
- **Types:** price (13 built-in conditions: crossing, crossing up/down, greater/less than, entering/exiting channel, moving up/down by, and so on), indicator and strategy alerts, drawing alerts, Pine alerts, watchlist alerts, **multi-condition** alerts (up to five).
- **Triggers:** once, once per bar, once per bar close, every time; expiry date; custom message with placeholders.
- **Delivery:** browser popup, sound, email, mobile push, webhook.

### 4.7 Bar replay and paper trading
- Replay from a chosen bar, play/pause/step, **9 speeds**, intervals chosen separately, multi-chart synced replay, second-level replay (paid), drawings and indicators keep working, session can be paused and resumed.
- Simulated trading with virtual money, balance, leverage, commission. **Parked by you** (paper trading); replay itself is not paper trading and is in scope.

### 4.8 Screeners, heatmaps, calendars, news
- **Stock screener** (hundreds of fields: price, performance, valuation, financials, dividends, technicals, analyst rating; column sets; saved screens), ETF, bond, crypto, DEX screeners; **Pine screener** (custom script as filter, skipped for the same reason as Pine); AI screener (natural-language request, counted per plan).
- **Heatmaps** (stocks by sector sized by market cap, coloured by change; ETFs; crypto).
- **Calendars:** economic, earnings, dividend, IPO. **Macro:** maps, yield curves. **News flow** with filters.

### 4.9 Out of scope for a single-user local tool
Social network, ideas, chats, Leap competition, wizards/marketplace, publish-a-script, referrals, mobile and desktop apps, broker integration (Zerodha parked), 3.5 million-instrument data licences, TradingView's own data feeds.

## 5. Gap analysis: Athena terminal today vs TradingView

| Area | Athena today | Gap |
|---|---|---|
| Chart types | Candles + volume | 20+ types missing (Heikin Ashi, Renko, Kagi, line family, baseline, footprint, TPO) |
| Intervals | Fixed set, 1M = 22 daily bars | Custom intervals, seconds/ticks (feed is quote-level, not tick history), range bars |
| Indicators in the page | 5 (EMA 20, SMA 50, Bollinger 20, RSI 14, MACD), fixed settings | Everything else; settings dialog; styles; overrides; indicator-on-indicator; templates. The Python registry has 16, unused by the page |
| Custom indicators | none | Formula language, editor, library |
| Drawing tools | none | All of 4.3, plus object tree, persistence, drawing alerts |
| Compare/overlay, log/percent scale, ADJ toggle | none (always adjusted) | All |
| Multi-chart layout | dockable panels, one chart each, link colours | Chart grid up to 16 with sync options (links already exist, which is the hard part) |
| Watchlists | one list, add/remove | Many lists, sections, flags, columns, advanced view, import/export, groups |
| Symbol details | quote, depth, analysis panels | Seasonals, technicals gauge, key stats, performance table, IV widgets |
| Indices | named in the command line; **no live index quotes** (Angel index tokens filtered out of our master) | Index feeds |
| Futures and options | **none.** The Angel master loader keeps only NSE cash rows | NFO/BFO instrument master, continuous futures, expiries, option chain, Greeks, payoff builder |
| Alerts | none | Whole engine and delivery |
| Replay | none | Replay mode |
| Screener | Six presets, filters, sort over the stored screen | More fields, saved screens, custom filter builder, heatmap |
| Calendars, news flow | news inside the Streamlit analysis only | Calendars, news panel |
| Export, snapshot, shortcuts, command palette | command line exists | Data export, PNG snapshot, shortcuts, palette |
| **Athena-only strengths** | Five-specialist analysis, verdicts, risk overlay, point-in-time backtest with blinding check, split-adjusted warehouse | Keep; this is the AI layer (section 8) |

## 6. Custom indicators without Pine

Pine is a Turing-complete language with a cloud runtime; copying it is both proprietary and heavy. We need the same user outcome: define an indicator or a chart series from a formula, set its inputs, plot it, alert on it, backtest it.

Design (to be specified properly in its own spec):
- **Formula language ("AthenaScript"):** a small, side-effect-free expression language, evaluated by our own interpreter, never `eval`. Series types, previous-bar reference `x[1]`, the built-in functions (`sma`, `ema`, `rsi`, `highest`, `lowest`, `crossover`, `if`), user `input` declarations (name, default, min, max), `plot(series, color, style)`, `hline`, `fill`, and `alert(condition)`. Extends the existing strategy-condition grammar in `athena.strategies` rather than starting over, so the backtester can run the same formula.
- **Runs in the page** (TypeScript) for live drawing, with the Python registry as the reference implementation for tests (same numbers on the same data).
- **Safety:** bounded length, bounded loops (none: array ops only), bounded history reach, validated with a path to any error, like the strategy builder does.
- **Indicators on indicators and formula charts (`A / B`, `A - B`)** fall out of the same series model.

## 7. Free-data feasibility (the real limit, not the plan caps)

TradingView's data is licensed from exchanges. We have free sources only (Angel One SmartAPI stream and REST, Yahoo for delayed intraday, NSE public files, our warehouse from 2023-01-02).

| Need | Source | Status / risk |
|---|---|---|
| Live equity quotes | Angel stream | Working |
| Index quotes (NIFTY, BANKNIFTY, SENSEX, sector indices) | Angel index tokens | Not loaded (master filter keeps NSE cash only); to add; needs a market session to prove |
| Futures and options instruments | Angel instrument master (NFO, BFO segments carry strike, expiry, lot size) | Not loaded; **to verify** that the master carries every weekly and monthly series |
| Option quotes and depth | Angel stream (NFO tokens) | **To verify** subscription limits (token cap per connection) against a full chain |
| Greeks and IV | Angel's option-Greeks endpoint if available, else computed locally (Black-76 / Black-Scholes with vollib, already named in the TRD) | Computing locally is certain; the broker endpoint is a bonus |
| Open interest | stream fields for derivatives | **To verify** |
| Intraday history | Yahoo (delayed, limited), Angel historical candles (limited per call) | Depth of minute history is lower than TradingView's 180 to 365 days; set expectation |
| Tick and second history | none free | Seconds/ticks are **live-only** (built from our own stream and stored from now on), never historical |
| Footprint, delta, order-flow | needs tick-by-tick trades with side | **Not feasible free**; build volume profile from bars (approximate), mark footprint "not available" |
| Economic, earnings, dividend, IPO calendars | NSE/BSE public corporate-action files, RBI; economic calendar has no clean free Indian source | Partial |
| Fundamentals (100+ metrics) | Yahoo statements already used | Working; not point-in-time (already documented) |
| Heatmap, breadth | derived from our own full-market quotes/warehouse | Feasible |

Continuous futures (`NIFTY1!`): built by us from the instrument master's expiry list (nearest unexpired = 1, next = 2), with a roll rule we choose (roll on expiry day close; optional back-adjustment off by default) and shown in the symbol info, since TradingView's own rule is not documented.

## 8. The "AI insight" layer (the part TradingView does not have)

This is where Athena differs. Everything below reuses what exists (specialists, verdicts, risk overlay, warehouse, blinded backtest, live analysis worker).
1. **Verdict on the chart:** the stored verdict band and the five specialist signals as a chart overlay, with a tooltip of the reasons.
2. **Explain this chart:** one click sends the visible window, indicators and drawings to the analysis service; returns a plain-language read with the evidence list (no black-box score).
3. **Pattern finder with a narrator:** deterministic detectors find candlestick and chart patterns; the model explains each in context and says how often that pattern worked on this stock in the warehouse (a measured hit-rate, honest about sample size).
4. **Natural-language screener:** "profitable midcaps near their 52-week high with falling debt" becomes filters on the stored screen; the filters are shown and editable before they run.
5. **Natural-language alerts:** "tell me if RELIANCE closes above its 50-day average on volume twice the usual" becomes a structured alert the person confirms.
6. **Text-to-formula and text-to-strategy:** drafts AthenaScript or a strategy JSON for the person to review (already listed "not built yet" in the TRD).
7. **Options strategy suggestions:** from the chain and the stored view, propose a few payoff shapes with max loss and breakeven shown; never an order.
8. **Backtest from the chart:** right-click any indicator or drawing alert, "backtest this", through the existing engine with blinding check.

Rules for all of it: the model never invents a number (it only phrases numbers the code computed), every claim shows its data and as-of time, and nothing places an order.

## 9. Draft roadmap (not yet in the TRD or plans)

Ordering puts what fixes today's bugs and what everything else depends on first. Sizes: S under a day, M a few days, L about a week or more of focused work. Each phase gets its own spec, then plan, then build, tests and graphify rebuild, as before.

| Phase | Name | Contents | Size | Depends on |
|---|---|---|---|---|
| 5a | Chart engine | Indicator framework (registry, settings dialog, styles, legends, overrides), pane layout, data window, crosshair sync, scale modes (log, percent), ADJ toggle, compare/overlay, undo/redo, snapshot, templates, scroll and edge rules matched to TradingView (the same family as the candle bug) | L | none |
| 5b | Chart types and intervals | Heikin Ashi, hollow, line family, baseline, columns, high-low, Renko, line break, Kagi, point and figure, range bars, custom intervals; live-built seconds and ticks | M | 5a |
| 5c | Indicator library | The catalogue in 4.2 in tiers: tier 1 (about 30: the common ones), tier 2 (about 40), tier 3 (rest); tests compare each against the Python/ta-lib reference | L | 5a |
| 5d | Drawing tools | Engine (hit-testing, handles, snapping, persistence per symbol), then tools in tiers: lines/channels, Fib, shapes/text, patterns/Gann, forecast/measure; object tree; lock, hide, sync | L | 5a |
| 5e | Watchlists and symbol details | Multiple lists, sections, flags, columns, advanced view, import/export; details panel with seasonals, technicals gauge, performance, key stats; **live index quotes** | M | none |
| 5f | F&O | NFO/BFO master, continuous futures, expiry selector (weekly and monthly), option chain with Greeks/IV/OI, volatility curve, payoff builder, max pain, PCR | L | 5e (indices), verification in section 7 |
| 5g | Alerts | Engine in the Rust server (it already holds the live ticks), conditions (price, indicator, drawing, watchlist, multi), triggers, browser/sound/webhook delivery, history | M | 5a, 5d |
| 5h | Formula language | AthenaScript spec, interpreter, editor with errors, library, alerts and backtest on the same formula | L | 5a, 5c |
| 5i | Screener and markets | Fields and column sets, saved screens, custom filter builder, heatmap, calendars, news flow | M | none |
| 5j | Replay | Bar replay with speeds, synced multi-chart replay | M | 5a, 5b |
| 5k | Chart grid | Up to 16 charts with sync options | M | 5a |
| 5l | AI layer | Items in section 8, in the order 1, 3, 2, 4, 5, 6, 7, 8 | L | 5a to 5h |

Suggested order: **5a, 5e, 5b, 5c, 5d, 5f, 5g, 5h, 5i, 5k, 5j, 5l**, with 5l's first item (verdict overlay) pulled forward right after 5a because it is cheap and ours.

**How this helps the open bugs.** The candle-scroll and edge bugs come from our chart not having TradingView's rules for the last bar, the right margin, session bounds and the live bar. 5a writes those rules down once (the data window, last-price line, market-status badge, `ADJ`, interval-tied ranges) and tests them against a fake feed, which is the cure for the "works shut, breaks live" gap that remains from the earlier audit.

## 10. Decisions needed from you

1. **Order:** is the suggested order right, or do you want F&O (5f) earlier since it is the largest missing piece for Indian trading?
2. **Footprint and order-flow:** accept "not available" (no free tick data), or look at a paid tick source later?
3. **Pine:** confirm our own formula language instead of Pine (and Pine import stays parked).
4. **Name and look:** the replica gets its own look, not TradingView's colours, icons or wordmark. Confirm.
5. **Verification session:** F&O and index feeds can only be proven while the market is open. Tell me when you can run a session, or I build against a recorded or fake feed first and verify live later.

## Sources

- [TradingView pricing, India](https://in.tradingview.com/pricing/)
- [TradingView features](https://www.tradingview.com/features/)
- [Drawing tools available on TradingView](https://in.tradingview.com/support/solutions/43000703396)
- [Chart types available on TradingView](https://in.tradingview.com/support/solutions/43000703407)
- [Getting started with Supercharts](https://www.tradingview.com/support/solutions/43000746464-getting-started-with-supercharts/)
- [Watchlist advanced view mode](https://in.tradingview.com/support/solutions/43000771546)
- [How to import or export a watchlist](https://in.tradingview.com/support/solutions/43000487233)
- [Bar Replay: how and why to test a strategy in the past](https://in.tradingview.com/support/solutions/43000712747)
- Logged-out chart page `in.tradingview.com/chart/`, observed with REA on 2026-10-17 (accessibility tree and screenshot only)
