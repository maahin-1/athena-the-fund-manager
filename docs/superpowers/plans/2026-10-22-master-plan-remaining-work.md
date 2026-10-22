# Athena: what is built, what is left, in what order (22 Oct 2026)

Written for the owner. It replaces the status tables in `2026-10-18-roadmap.md` and `2026-10-22-valuation-and-research-adoption.md` (their detail still stands). Nothing here is started until the owner says so.

Sizes are working sessions (one block of work like 22 Oct) unless a row says "background", which is wall-clock time of a throttled job that runs without attention.

## 0. Where the project stands

| Layer | State |
|---|---|
| Market data | Daily prices since Jan 2018 for ~4,500 symbols (4.7M bars), corporate-action adjustment, indices, events, derivatives master. Live feed via Angel One; index quotes live. |
| Fundamentals | NSE filings for 2,334 companies (1.16M lines, FY2019 on, only the latest 13 quarters), segments for 896, corrected share counts (22 Oct). Shareholding and documents stages not run. |
| Reference and valuation | Damodaran India tables and country premiums, industry map (2,310 of 2,398), cost of capital, DCF, bank model, ranges, implied return, business mix and financial-arm carve-out, sensitivity, point-in-time ledger (first snapshot 22 Oct). |
| Terminal | Quote, chart lab (16 types, 47 indicators, 22 drawings), watchlists, fundamentals page with Valuation section, screens, peers, heatmaps, replay, grid, options chain (partly). |
| AI layer | Four analysts in the stored-verdict path (technical, valuation, quality, earnings), a fifth (news) outside it; confidence-weighted blend; risk overlay; calibration; point-in-time scorer. 12 verdicts stored. **Measured: the analysts beat the best fixed answer in 2 of 12 cases.** |

The data layer is well ahead of the AI layer. The plan below closes that gap, in an order where each step can be measured.

## 1. Standing rules (unchanged)

Fix defect classes for the whole universe, never single tickers. Ranges, not points. The model never does arithmetic or trades. Point-in-time honesty. Assumptions are dated, editable inputs. Owner use only. Nothing is scheduled without approval. Free data only.

## 2. The workstreams

### W1. Data depth (the owner asked for at least 10 years)

| # | Item | Size | Notes |
|---|---|---|---|
| 1.1 | Prices back to 2010 (and delisted symbols, which the exchange archive still holds) | background ~2-4 h + 1 session for checks | `warehouse.build --since 2010-01-01`. Corporate actions and renames get the same treatment; re-run the price-integrity audit on the older years. Cuts survivorship bias in every backtest. Needs the warehouse free while it writes (the app shows "busy" at times). |
| 1.2 | All NSE quarters back to the Dec 2018 quarter | background ~20 h over a few nights | We hold ~13 quarters per company; NSE lists ~31 consolidated quarters from Dec 2018. Same ingest, `--quarters` raised; resumable, 1.5 s between requests. |
| 1.3 | Financials before Dec 2018 (FY2016 to FY2018 at least) | 3 to 5 sessions + model cost | NSE's XBRL starts with the Dec 2018 quarter, so this needs annual-report PDFs read by a model and cross-checked against the next year's comparatives (roadmap item 6e2). Start with the top ~500 by market cap. Owner decision below. |
| 1.4 | Shareholding stage, then documents stage of the full fetch | background ~1 day each + 0.5 session | Already built; waiting to be run. |
| 1.5 | Industry map gaps (125 companies with none), pre-2026 Damodaran tables (archives) | 1 session | Archives only if own tables (W6) need history. |
| 1.6 | BSE indices, intraday for the 177 indices with no source | 1 session (source hunt) | Owner's note: tomorrow morning, for indices other than NIFTY 50 and NIFTY BANK. |
| 1.7 | Market-session checks (index quotes, F&O/MCX streams, tick rate) | 0.5 session in market hours | Blocked on a live session. |

### W2. Put the analysts on the good data

| # | Item | Size |
|---|---|---|
| 2.1 | Build the fundamentals packet from the NSE store (today it is Yahoo statements) with corrected shares, TTM, segments and business mix | 2 sessions |
| 2.2 | Feed the Valuation analyst the code-computed range, implied return and sensitivity as facts (it may cite them; it never computes) | 1 session |
| 2.3 | Add the news analyst to the stored-verdict path | 0.5 session |
| 2.4 | Ownership analyst (shareholding changes, pledges, promoter and institutional trend) | 1.5 sessions, after 1.4 |
| 2.5 | Re-run the point-in-time score with the new packets and compare with today's table | 1 session + model cost |

Analysts stay **independent**: each reads only its own packet and returns a typed output. They never read each other.

### W3. Measurement (so every change is judged on evidence)

| # | Item | Size |
|---|---|---|
| 3.1 | Analyst scorecard: hit rate, slugging, per-date rank IC and t-statistic, by rating | 1 session |
| 3.2 | Typed decisions (including REVIEW) and a decision log settled against the Nifty at 5, 20 and 60 days | 1.5 sessions |
| 3.3 | Walk-forward harness on the 15-year prices (needs 1.1) | 1 session |
| 3.4 | India cost model (brokerage, STT, stamp, GST, slippage) | 1 session; **needs the owner's contract-note charges** |
| 3.5 | Prompt cache and call budget | 0.5 session |

### W4. The council: debate, then a judge

| # | Item | Size |
|---|---|---|
| 4.1 | Evidence sheet: one read-only page of facts and flags per stock, built in code from all packets | 1 session |
| 4.2 | Debate on conflicts only: a bull and a bear case, one round, every figure grounded in the sheet | 1.5 sessions |
| 4.3 | Judge on the strong tier: reads analyst outputs, the debate and the sheet; writes a typed decision (stance, range, drivers, what would change my mind, settle-by date). Cannot raise conviction above the code's caps and may move the band by at most one step | 1.5 sessions |
| 4.4 | Behind a flag, scored against the plain blend in the point-in-time test; promoted only if it wins | 1 session |

The numbers stay in code: the blend, conviction caps, calibration dampening and the risk overlay.

### W5. Derivatives, flow and order-flow

| # | Item | Size |
|---|---|---|
| 5.1 | F&O history: derivatives bhavcopy, continuous futures, open-interest series | 2 sessions + background |
| 5.2 | Detectors in code (long build-up, short covering, put-call shifts, max-pain drift, bulk and block deals, delivery spikes, insider and SAST disclosures), each event stamped with its time | 2.5 sessions |
| 5.3 | Flow analyst (interprets the detectors' facts; detects nothing itself) | 1 session |
| 5.4 | Record ticks and depth from the live stream (history builds from the first day) | 1 session; then it runs in market hours |
| 5.5 | Order-book imbalance and tape features once enough ticks exist | 1.5 sessions, weeks later |
| 5.6 | Alerts engine (price, indicator, drawing, multi-condition; browser, sound, webhook) | 2 sessions |

### W6. Valuation completion

| # | Item | Size |
|---|---|---|
| 6.1 | Own industry tables from NSE filings with history, and a divergence report against Damodaran | 2 sessions |
| 6.2 | Market-risk gauge: implied premium, turbulence index | 1 session |
| 6.3 | Distress and failure probability; young-company (negative earnings) path; option overhang with an owner-supplied count | 2.5 sessions |
| 6.4 | Screen columns for industry comparison and implied return | 0.5 session |
| 6.5 | Listed investments at market (holdings table); minorities at price to book as default if the study supports it | 1 session |
| 6.6 | Re-run the calibration study with business mix and new history | 0.5 session |
| 6.7 | Register the monthly snapshot task (the owner runs one command) | minutes |

### W7. Documents and retrieval (the first place retrieval pays)

| # | Item | Size |
|---|---|---|
| 7.1 | Document text (annual reports, concalls, announcements) with dates | 2 sessions after 1.4 |
| 7.2 | Hybrid retrieval: keyword (BM25) plus embeddings, a published-before-as-of filter, quoted passages required | 2 sessions |
| 7.3 | AI over statements and documents: pros and cons, red flags, key insights with the quoted passage | 2.5 sessions |
| 7.4 | PDF history and shareholder search | 1.5 sessions |

### W8. Terminal

Chart AI (explain a chart, pattern narration with measured hit rates, backtest from chart): 2 sessions. More indicators and drawing tier 2: 2 sessions. Follow, notes, notebook: 1 session. Verdict panel shows analysts, debate and judge: 1 session. Derivative push stream: last.

### W9. Sentiment (Track C)

Reddit official API and open forums with robots.txt and rate limits; relevance gate; sentiment analyst; test the daily stance against forward returns and write up whether it helps. 4 sessions, then weeks of data.

### W10. Later, owner use only

Personal analyst (goals, horizons, ranges with haircuts, ETF core, stress tests) and the quant lab (shared Monte Carlo, options strategy lab, model research harness, portfolio and derivatives backtests). Spec: `2026-10-19-personal-analyst-and-quant-lab-design.md`.

### W11. Quality and operations

Defuse XML parsing for downloaded filings (use `defusedxml`); independent code review of every phase that touches three or more files; end-to-end browser run after UI changes; backups of the stores before any bulk rewrite (as done for share counts); keep TRD and the graph current.

## 3. Order

| When | What | Why this order |
|---|---|---|
| Now (background) | 1.1 prices to 2010; 1.2 quarters to Dec 2018 | They are the slowest and block 3.3 and 6.6. |
| Next 3 sessions | 2.1, 2.2, 2.3, 3.1, 3.2 | Analysts on good data, with measurement ready before anything else is judged. |
| Then | 2.5 re-score, 4.1 to 4.4 council, 1.4 shareholding and documents | The council is only worth promoting if the re-score shows signal. |
| After | W5 flow, W6 completion, 1.3 older financials | Detectors need history; 1.3 needs the owner's source decision. |
| Later | W7 retrieval, W8, W9, W10 | Retrieval waits for document text. |

## 4. Risks

- The analysts may stay at no skill even on good data. The plan keeps that visible: the scorecard, the ledger and the point-in-time test all report it.
- Free-data order flow is partial: public deals and open interest are real; the order book only exists from the day recording starts.
- Annual-report extraction (1.3) can be wrong; every extracted year is cross-checked against the following year's comparatives, and an unmatched year is left out.
- NSE can throttle or block long fetches; jobs are throttled, resumable and stop after repeated refusals.

## 5. Decisions (22 Oct 2026)

Settled by the owner:

1. **Backfills start only after the owner confirms the build** (the app stays free of "busy" messages while he looks around).
2. **Financials before Dec 2018 come from annual-report PDFs for the whole universe**, each year cross-checked against the next year's comparatives. This is the most expensive item in the plan: roughly 2,300 companies x 3 older years of reports read by a model. Work in rank order of market cap, report the model cost and the share of years that pass the cross-check after the first 500, and let the owner stop or narrow it from there.
3. **Nothing is scheduled.** Jobs are run by hand; the monthly valuation snapshot task stays unregistered until the owner runs its one command (`scripts/athena-valuation-snapshot-task.xml`).
4. **The judge uses the best available model, paid if need be**; everything else stays on the free tiers. The router's strong tier today ends at `gpt-5.4-mini`; before the judge is built, the owner names the model he wants and a monthly ceiling, and the judge is called only on demand and on conflicts.

Still open:

5. Brokerage and statutory charges from a contract note (3.4); not blocking.
6. The owner's go on the build, and which workstream he wants first (the plan suggests W1 backfills in the background with W2 and W3 in front).
