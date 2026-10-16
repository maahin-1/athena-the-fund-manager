# Screener.in breakdown, data feasibility and the two-track plan (findings, 2026-10-17)

Status: **findings and a draft roadmap, for review.** Nothing here is built, and nothing is in the TRD or phase plans yet. Companion to [2026-10-17-tradingview-breakdown.md](2026-10-17-tradingview-breakdown.md).

## 1. Method, and one line I am not crossing

| Method | Gave |
|---|---|
| Screener's public pages (home, premium, one public company page, what-is-free) read with a page fetcher | Features, plan limits, the company page's section and row structure (names only, no numbers kept) |
| The installed Angel SmartAPI SDK (`SmartApi/smartConnect.py`, the route table) | The authoritative list of what Angel's API can return |
| Shape-only probes of NSE's public website endpoints, run 2026-10-17 for one symbol (status codes and field names, nothing stored) | Which primary-source data exists today and in what form |
| Athena's own code | What we already ingest and parse |

**Not done, and why.** You suggested falling back to `api-anything` to duplicate Screener's pages. I will not scrape or mirror Screener. Its [terms](https://www.screener.in/guides/terms) grant "personal, non-commercial transitory viewing only" and forbid copying the materials or "mirroring" them on another server. More to the point it is unnecessary: Screener says its feed "is sourced and curated using automated algorithms from various data sources", and those sources are the exchange filings, which we can reach directly. We rebuild the same pages from the primary filings and compute the ratios ourselves. `api-anything` stays what it was used for: finding NSE and BSE's own endpoints.

## 2. What Screener is

### 2.1 Plans (verified from its premium page; prices include GST)

| | Free "Hobby Investor" ₹0 | Premium "Active Investor" ₹4,999/year |
|---|---|---|
| Follow companies | 50 | unlimited |
| Stock alerts | 10 | 800 |
| Screen alerts | 2 | 75 |
| Phrase alerts | 2 | 50 |
| Follow people | 2 | 300 |
| Comparison columns | 15 | 55 |
| Quick ratios | 18 | 60 |
| Key insights | 20 / month | unlimited |
| Insights table | 5 / month | unlimited |
| AI summaries | 10 / month | unlimited |
| Screener AI | no | ₹500 free credits |
| Segment results, detailed peer comparison, download results, industry filter, one-click filters, multiple watchlists, 10,000+ commodity trends | no | yes |

Free already includes: Excel automation, fundamental charts, stock screens, custom ratios, search everywhere. Listed as work in progress on both plans: concalls, more screening ratios, qualitative data.

**For us:** same rule as TradingView. Single user, so every premium feature ships with no caps. The numeric limits (50 follows, 15 columns) are a business model.

### 2.2 Tools menu
Create a stock screen (queries over 10 years of financial data); Screener AI (insights from hundreds of company documents); Commodity prices (10,000+, 10 years); Search shareholders (companies where a named person holds more than 1%); Company announcements (search, filter, alerts); account features: watchlists, custom ratios and columns, saved screens, stock and screen and phrase alerts, notebook, follow people, a combined announcements feed.

### 2.3 The company page (observed structure, in order)
1. **Header:** name, logo, notebook, AI link, anchor bar (Summary, Chart, Analysis, Peers, Quarters, Profit and Loss, Balance Sheet, Cash Flow, Ratios, Investors, Documents), price and change, **Export to Excel**, Follow, links to the company site, BSE, NSE, F&O.
2. **About** and revenue breakup by business vertical.
3. **Key ratios:** market cap, price, 52-week high/low, stock P/E, book value, dividend yield, ROCE, ROE, face value; "add ratio to table", "edit ratios".
4. **Chart:** 1M to Max; selectors Price, PE ratio, Sales and margin, EV/EBITDA, Price to book, Market cap to sales; alerts.
5. **Analysis:** machine-written pros and cons, flagged to be checked independently.
6. **Peers:** sector and industry breadcrumbs, index memberships, a peer table with editable columns, and a detailed comparison (premium).
7. **Quarterly results** (consolidated, standalone link, 12 quarters): Sales, Expenses, Operating Profit, OPM %, Other Income, Interest, Depreciation, Profit before tax, Tax %, Net Profit, EPS; each quarter links to its raw PDF.
8. **Profit and Loss** (annual since March 2015, plus TTM): same rows plus Dividend Payout %; compounded growth tables for sales, profit, price (10, 5, 3 years, TTM or 1 year) and ROE.
9. **Balance Sheet:** Equity Capital, Reserves, Borrowings, Other Liabilities, Total Liabilities, Fixed Assets, CWIP, Investments, Other Assets, Total Assets.
10. **Cash Flows:** operating, investing, financing, net cash flow, free cash flow, CFO to EBITDA.
11. **Ratios:** debtor days, inventory days, days payable, cash conversion cycle, working capital days, ROCE %.
12. **Insights:** company-specific KPIs by year or quarter (headcount, order book, attrition, revenue mix, R&D share and so on), pulled from documents; mostly locked.
13. **Shareholding:** quarterly and yearly; Promoters, FIIs, DIIs, Government, Public, number of shareholders.
14. **Documents:** announcements (BSE PDFs), **annual reports** back to 2013, **credit ratings** (ICRA), **concalls** back to 2016 (transcript, AI summary, PPT, recording; users can add missing links).

## 3. Can Angel One give this? No, and here is the proof

**Angel's route table** (from the installed SDK): login and profile; place, modify, cancel and list orders; trade book; RMS limits; positions and holdings; GTT rules; margin and charge estimates; eDIS; **market quote** (`/market/v1/quote`, modes LTP, OHLC, FULL); **candle history** and **OI history**; scrip search; **option greeks**; **gainers and losers**; **put-call ratio**; **OI build-up**; NSE and BSE intraday.

FULL quote returns LTP, OHLC, last trade quantity, net and percent change, average price, volume, open interest, lower and upper circuit, total buy and sell quantity, **52-week high and low** and the best five depth. There is **no endpoint for financial statements, ratios, shareholding, documents or market cap**. Angel is the market-data half only.

| Screener element | Angel |
|---|---|
| Live price, change, 52-week high/low, circuit limits, volume, depth | **Yes** (already used by the terminal) |
| Price history for the chart and price CAGR | **Yes**, limited per call; our warehouse already holds adjusted daily bars from 2023 |
| F&O link, OI, PCR, greeks, gainers/losers | **Yes** (feeds the TradingView track's options phase) |
| Quarterly results, P&L, balance sheet, cash flow | No |
| Ratios (ROCE, ROE, P/E, days) | No (computed from statements and price) |
| Shareholding pattern, shareholders, pledges | No |
| Annual reports, concalls, announcements, credit ratings | No |
| Peers, sector, industry | No (instrument master has no industry) |

## 4. Where the rest comes from: NSE's own public endpoints (verified live, 2026-10-17, symbol TCS)

| Endpoint (nseindia.com/api/…) | Status | What it returns |
|---|---|---|
| `corporates-financial-results` (period Quarterly / Annual) | 200 | 162 quarterly and 42 annual filing records per symbol, consolidated and standalone, audited flag, period dates, and a link to each filing's **XBRL** |
| the annual XBRL file itself (`nsearchives.nseindia.com/corporate/xbrl/…`) | 200 | **Full Ind-AS statements.** One FY2024 consolidated file: 230 distinct tags, including about 100 balance-sheet tags (assets, liabilities, borrowings current and non-current, inventories, receivables, payables, investments, property, equity, reserves) and about 57 cash-flow tags (operating, investing, financing, purchases, proceeds, depreciation) plus the income statement |
| `results-comparision` | 200 | The five latest periods side by side with banks' fields (gross NPA, capital adequacy, CET1, interest earned) as well as industrial ones |
| `corporate-share-holdings-master` | 200 | 20 shareholding-pattern records with promoter group, public, employee trusts and DRs, each with an XBRL link |
| `annual-reports` | 200 | 17 years of annual-report PDFs (from and to year, file name, filing time) |
| `corporate-announcements` | 200 | Filings with attachment links and text, including investor-meet and call notices (a company-wide request returned 3,381 rows, so always filter by symbol and date) |
| `corporate-board-meetings`, `event-calendar` | 200 | Upcoming and past board meetings and their purpose (results dates) |
| `corporates-corporateActions` | 200 | Splits, bonuses, dividends, rights, with ex-dates (already used for price adjustment) |
| `corporate-pledgedata`, `corporates-pit` (insider trading), `corporate-sast-reg29` | 200 | Pledges, insider trades, large acquirer disclosures |
| `corporate-credit-rating` | 200 | Rating actions with agency, rating, date (287 rows for one name) |
| `NextApi … getMetaData` | 200 | Symbol flags (F&O, series) |
| `NextApi … getSymbolData` | **404, "Unexpected end of JSON input"** | Earlier finding (12 Oct) said this gave issued share count, free-float market cap and face value. It no longer answers the same way; `getYearwiseData` and `getIndexList` now return empty lists |
| nseindia.com home page | 403 | As before; the API endpoints answer without the home page, after one priming request |

So **every section of Screener's company page has a primary source**, with three caveats in section 6. Athena already has the hard parts: `athena.pit.nse_results` (filing lists, throttled at 1.5 s per request with back-off because NSE blocks bursts for minutes), `athena.pit.xbrl` (parser with Ind-AS and bank tag sets, never guessing a figure), `athena.pit.statements` (point-in-time assembly), `athena.news.nse_announcements`, `athena.loaders.fundamentals` (Yahoo, as a cross-check), and `athena.metrics` plus the five specialists that already read these.

## 5. Mapping Screener to Athena

| Screener element | Built from | Status |
|---|---|---|
| Price, 52-week range, volume | Angel stream and warehouse | Have |
| Market cap, P/E, book value, dividend yield, face value | price × shares (shares from XBRL paid-up capital and face value, or the shareholding record; Yahoo as a check), statements | New (ratio engine) |
| Quarterly results, P&L | NSE XBRL, consolidated and standalone | Parser exists for headline lines; extend to every row |
| Balance sheet, cash flow | NSE annual XBRL | New rows in the parser |
| Ratios, growth tables, ROCE, ROE, days, CCC, FCF | our own formulas over the statements | New |
| Shareholding | shareholding XBRL | New parser |
| Peers, sector, industry | NSE industry field on the filings, index membership from NSE index lists | New |
| Documents | annual-reports, announcements, credit-rating endpoints; concall notices from announcements | New (links, then text) |
| Pros and cons, AI summaries, key insights, insights table, Screener AI | **Athena's specialists, the live analysis worker and the language model, reading the statements and documents above** | Reuse; section 7 |
| Screens, saved screens, alerts | the Screen view we have plus a query language | Extend |
| Export to Excel | write `.xlsx` from the same tables | New |
| Notebook, follow, watchlists | local store beside layouts | New |
| Search shareholders | shareholding XBRL names above 1% across companies | New, heavy |
| Commodity trends (10,000+) | no free source for that breadth | **Not feasible as stated** |

## 6. Honest gaps and risks

1. **History depth.** NSE has XBRL only from about October 2018 (our own `XBRL_FROM`). That gives FY2019 onward, about 7 annual points and 28 quarters. Screener shows 2015 onward. Earlier years need annual-report PDFs read by a model with the figure checked against the next year's comparative column, or they stay blank. Proposed: blank at first, PDF extraction as a later phase.
2. **Banks, NBFCs and insurers** use different statement formats; Screener changes its row set for them. The XBRL parser already has bank tag sets; the page needs a second row layout.
3. **BSE-only companies** and pre-2018 detail need BSE's own endpoints (not yet probed).
4. **Unofficial endpoints.** NSE can change them without notice (the `getSymbolData` change above shows it). Every fetcher must fail soft, name the failed source in the page, and keep the last stored data with its as-of date.
5. **Rate limits are the real cost.** At 1.5 s per request, one company needs about 30 filing fetches (consolidated and standalone, quarterly and annual, shareholding), roughly a minute. The Nifty 500 is about 8 hours one-off and resumable; the full market is about 35 hours. So: tier by index, fetch on first view, and refresh only what a new filing date shows. No background schedule without your approval.
6. **Document text.** PDFs (annual reports, transcripts) need extraction before any summary; scanned ones may fail and are reported as such.
7. **Definitions.** Screener's ROCE, OPM and days formulas are conventions; ours are written out per ratio on the page and tested, so a difference from Screener is explainable, not hidden.

## 7. The AI layer (reusing what we built for Streamlit)

Everything below runs through the existing live-analysis worker and specialists; the model phrases numbers the code computed and never invents one.
1. **Pros and cons** from the ratio engine: every line cites its figure and period (replaces Screener's machine-written pros and cons).
2. **Five specialist cards** (Valuation, Moat and Quality, Quant/Technical, Earnings Intelligence, News sentiment) and the verdict with risk overlay, now fed the full statements, not four Yahoo lines.
3. **Document summaries:** annual report, each concall transcript and PPT, with the quoted passage next to each claim.
4. **Key insights and the insights table:** company KPIs extracted from documents (order book, headcount, margins guidance), each with source document, page and quote; blank when the document does not say it.
5. **Screener AI:** a natural-language question becomes our query language; the query is shown and editable before it runs.
6. **Phrase alerts:** "tell me when a filing mentions capex guidance" over announcements and transcripts.
7. **Change detector:** what moved between the last two results (margin, debt, promoter holding, pledges) in plain words, from the stored statements.
8. **Red flags:** pledges rising, promoter selling, auditor change or qualification, receivable days jumping, cash flow below profit for years, rating downgrades, each from a filing.

## 8. Two tracks, separate tabs

You asked for the two builds to live in separate tabs, not mixed. Proposed top bar:

`Terminal` (docked panels, as now) · **`Chart Lab`** (TradingView track: full chart, indicators, drawings, options) · **`Fundamentals`** (Screener track: company page, screens, documents) · `Screen` (the stored market screen we have; folds into Fundamentals later) · `Research` (AI verdict and analysts, as now)

- **Chart Lab** and **Fundamentals** share only the symbol (the existing link colours) and the AI worker; neither imports the other's code.
- Each is its own page module in `terminal/web/src`, its own Rust route group, its own phases, so either can ship without the other.
- Fundamentals reads one new read-only DuckDB file (`fundamentals`), written by Python ingest jobs, mirroring how bars already work (Python writes, Rust reads).

## 9. Draft roadmap, Fundamentals track (6a to 6k)

| Phase | Contents | Size | Depends on |
|---|---|---|---|
| 6a | Statement store: schema, XBRL parser extended to every row (P&L, balance sheet, cash flow, banks), per-symbol ingest job, resumable, throttled; Nifty 50 first as the proof | L | none |
| 6b | Ratio engine and growth tables, formulas documented and tested; TTM; market cap, P/E, ROCE, ROE, days, CCC, FCF; banks' own ratios | M | 6a |
| 6c | Company page, first cut: header, key ratios, quarters, P&L, balance sheet, cash flow, ratios, with the Excel export; consolidated and standalone toggle | L | 6a, 6b |
| 6d | Shareholding: parser, quarterly and yearly table, pledges, insider and large-holder disclosures | M | 6a |
| 6e | Documents: annual reports, announcements, credit ratings, concall notices; list with links; later text extraction | M | 6a |
| 6f | Peers and comparison: sector, industry, index membership, editable columns, detailed comparison | M | 6b |
| 6g | Screens: query language (extends the strategy-condition grammar), field catalogue from 6b, saved screens, one-click filters, industry filter, results table with column editing | L | 6b, 6f |
| 6h | Alerts, follow, watchlists, notebook: stock, screen and phrase alerts, local store | M | 6g, 6e |
| 6i | AI layer items 1, 2, 7, 8 (pros and cons, specialists on full statements, change detector, red flags) | M | 6c, 6d |
| 6j | AI layer items 3, 4, 5, 6 (document summaries, key insights table, Screener AI, phrase alerts); PDF text extraction | L | 6e, 6g |
| 6k | Wider coverage: Nifty 500 and beyond, BSE-only names, pre-2018 history from annual-report PDFs, search shareholders | L | 6a to 6e |

**Combined order across both tracks** (they can alternate; each phase gets its own spec, plan, build, tests and graphify rebuild):
`5a` chart engine → `6a` statements → `6b` ratios → `6c` company page → `5e` watchlists/index quotes → `5b` → `6d` → `5c` → `6e` → `5d` → `6f` → `5f` F&O → `6g` → `5g` alerts → `6h` → `5h` → `6i` → `5i` → `6j` → `5k`, `5j` → `5l`, `6k`.
Rationale: 5a and 6a to 6c unblock everything else and both tabs become usable early; F&O waits for a market-hours verification.

## 10. Decisions needed

1. **Scraping Screener:** confirm we rebuild from NSE/BSE filings and do not scrape or mirror Screener (section 1).
2. **Coverage first:** Nifty 50 for the proof, then Nifty 500 (about 8 hours of throttled fetching), the rest on first view. Agree?
3. **History:** accept FY2019 onward from XBRL at first, with pre-2018 PDF extraction as a later phase?
4. **Commodity trends (10,000+ products):** drop, or replace with a short list of public series (crude, gold, metals) later?
5. **Tab names and layout:** Terminal · Chart Lab · Fundamentals · Screen · Research. Rename or reorder?
6. **Refresh:** on-demand and a manual "refresh universe" command only, with no scheduled background job, unless you approve one.

## Sources

- [Screener.in](https://www.screener.in/) home, [premium plans](https://www.screener.in/premium/), a public company page (structure only), [terms of service](https://www.screener.in/guides/terms)
- [Angel One SmartAPI quote modes (forum announcement)](https://smartapi.angelone.in/smartapi/forum/post/16902); installed SDK route table
- NSE public website endpoints probed 2026-10-17 (shape only)
