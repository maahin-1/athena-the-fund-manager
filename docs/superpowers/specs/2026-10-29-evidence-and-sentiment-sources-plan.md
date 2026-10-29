# Evidence and sentiment sources: plan, and where the whole project stands

Status: approved by the owner on 2026-10-29. Built so far: E0, E1 (TRD 2.83), E2 and E3 (TRD 2.84). Still to build: E4, E5, E6, E7, E8. Written 2026-10-29.

## 1. What this adds, in one paragraph

The Sentiment page reads four publishers and the exchange's filings today. This plan adds roughly twenty more sources, keeps every item with where it came from (a citation the person can click),
checks what can be checked against the warehouse, learns how often each source turns out right, and shows disagreement instead of hiding it. Nothing feeds a verdict until a test says it should.

## 2. Principles (the rules that keep this honest)

1. **Every statement on screen carries a citation**: source name, link, published time, and the time we saw it. No citation, no display. The language model may only cite items that are on its sheet,
   as the news analyst already may only use numbers that are on its sheet.
2. **Facts are checked, opinions are not "resolved".** A claim with a number, a date or an event is checked in code against our own data. An opinion or a tone is shown with its spread.
3. **Trust is a prior at first and is measured later.** The starting tiers below are judgement, and the page says so until a source has enough checked claims to speak for itself.
4. **Copies are not corroboration.** Ten feeds printing one agency story are one voice.
5. **Point in time.** Each item is stored with when we first saw it and is never rewritten, so a later study can ask "what was known on day X". RSS feeds keep no history, so collection must start now.
6. **Terms of use.** We store title, link, time and a short excerpt, not whole articles, and we never defeat a block (no captcha work, no fake logins). A source that refuses us is shown as off with the reason.
7. **A source failing is a state, not an error.** Every source has a health line (working, slow, blocked, off by choice) shown on the page.
8. **Nothing reaches a verdict untested.** The tone of news and posts has never been shown to predict anything here. The page stays display-only until a walk-forward study says otherwise.

## 3. The sources, with what was seen on 2026-10-29

Tier is the starting trust prior: **A** the exchange, a regulator or a company's own filing; **B** established publishers and agencies; **C** aggregators, attention measures and anything unedited.

| Source | Kind | Tier | Seen today | What we take | Open question |
|---|---|---|---|---|---|
| NSE announcements | filing | A | in use | title, time, attachment link | none |
| NSE insider trades (PIT) | filing, structured | A | HTTP 200, rows | who, buy or sell, shares, value, date | check against shareholding quarter |
| NSE FII/DII daily flows | market data | A | HTTP 200 | net buy/sell per category | market-wide, not per stock |
| NSE board-meeting outcomes | filing | A | HTTP 200 | date, purpose | the events calendar already uses dates |
| BSE announcements | filing | A | **403** on a plain request | same as NSE | try the headers a browser sends and one session; if still refused, off with the reason |
| Earnings-call transcripts and recordings | company document | A | links already stored | management tone, guidance, Q&A | needs a model call per transcript; cost approval |
| SEBI press releases | regulator | A | 30 items | orders, circulars | market-wide; match by company name only when named |
| RBI press releases | regulator | A | 10 items | policy, banking rules | market-wide |
| PIB (government releases) | government | A | 20 items | policy | market-wide, noisy |
| Economic Times, Business Standard, Mint, BusinessLine | press | B | in use | title, link, excerpt | none |
| Moneycontrol | press | B | 15 items | same | its terms for feeds to be read |
| CNBC-TV18 | press | B | 200 items | same | a general feed: needs name matching |
| NDTV Profit | press | B | 20 items | same | |
| Times of India business | press | B | 20 items | same | general feed |
| Business Today | press | B | 20 items | same | |
| The Hindu business | press | B | 60 items | same | general feed |
| Indian Express business | press | B | 200 items | same | general feed |
| Financial Express | press | B | the address tried gave a web page, not a feed | same | find the real feed address |
| Zee Business | press | B | **403** | same | try its listed feeds and a browser-style header; else off |
| Yahoo Finance news | aggregator | C | **nothing for Indian tickers** via the library | headline, link | try its search endpoint; its terms limit use to personal; else off |
| GDELT | news index with tone | C | answered, then limited us (one request per 5 s) | article list, its tone score, 30-day tone line | tone is crude and mostly English; name ambiguity |
| Wikipedia page views | attention | C | HTTP 200 | daily views | needs a symbol-to-article table; attention, not sentiment |
| Google Trends | attention | C | not tried | weekly interest | no official API; the library route is unofficial and rate-limited: optional, off by default |
| India VIX | market data | A | **already in the warehouse**, 3,063 days to 28 Oct | level and percentile | no fetching needed |
| Google News | aggregator | C | built, off | | its robots.txt disallows crawling: stays off unless the owner decides |
| Reddit, X | social | C | | | on hold, by the owner's decision |

A source that cannot be made to work honestly is recorded as such in the page and in this table. Of the nine the owner asked for beyond the clean ones, four are expected to need real effort (BSE, Zee, Yahoo, India VIX history from NSE; VIX itself is solved by the warehouse) and one is marginal (Google Trends).

## 4. Design

**Where it lives:** a new package `athena.evidence`, its own DuckDB file `.athena/evidence.duckdb` (its own writer lock, short opens, read by the page through a snapshot like the warehouse now is, so a fetch never blanks a screen).

**One item shape** for everything: `source_id`, `publisher`, `url`, `title`, `excerpt` (cut), `published_at`, `first_seen_at`, `content_hash`, `story_id` (copies of one story share it), `kind` (filing, press, regulator, flow, attention, document), `entities` (symbols matched and how: exact symbol, company name, or none), `claims` (structured, section 5), `fetch_status`.

**Tables:** `items`, `sources` (registry and health), `claims`, `checks` (a claim and what our data said), `source_stats` (per source: claims checked, confirmed, contradicted, with dates).

**Fetching:** a supervisor job `evidence` every hour while the software is open (the RSS group, NSE structured data) and a slower one every six hours (GDELT, Wikipedia, Trends), one polite request per source per second at most, `ETag` and `If-Modified-Since` where the host supports them. Both go through the job wrapper, so status, locks and backoff work like every other job.

**Page (Sentiment v2):** a header strip showing each source as a chip (working, blocked, off) with its item count; a timeline of items, each with a source chip, tier, time, and link; a **Conflicts** box; a **Flows and insiders** box; an **Attention** box (page views, Trends, VIX); a **Management tone** box (transcripts); a **Market and policy** box (SEBI, RBI, PIB). Clicking a chip filters to that source. The tone button stays, now over cited items only.

## 5. How disagreement is handled

There are three kinds of claim and each gets a different treatment. "Which source is right" is only asked where it has an answer.

**A. A checkable fact** (a number, a date, a deal, a holding change, a price move). The claim is pulled into a structured form by a model or a pattern, and **a program, not the model, compares it with our data**:

| The item says | We compare with | Outcome |
|---|---|---|
| "net profit up 12%" or a revenue figure | statements in the fundamentals store | confirmed, contradicted (shows both numbers), or not yet filed |
| board meeting on a date | event calendar and NSE board-meeting data | confirmed or contradicted |
| promoter sold or bought | NSE insider trades; the shareholding pattern after quarter end | confirmed, contradicted, or too early |
| "stock fell 8% today" | the bars in the warehouse | confirmed or contradicted |
| "FIIs sold" | NSE FII/DII | confirmed or contradicted |
| a deal, order or approval | the company's own filing | confirmed, or "unverified rumour" if none exists |

The exchange filing wins over any press item. A later filing wins over an earlier one (restatements, corrections), and both stay on screen with times. A contradicted claim stays visible, marked, and counts against its source's record. Nothing is deleted.

**B. A fact we cannot check locally** (a plan, a rumour, "talks are under way"). Weight rises with the number of **independent** stories saying it (copies of one story count once) and with the tier of the best source. It is shown as "reported by N independent sources, best tier X, not confirmed by a filing". It never becomes a filing-grade fact on volume alone.

**C. An opinion or a tone.** There is no truth to compare with. We show the spread (how many items lean each way, by tier), and a **disagreement flag** when good sources split. We do not average it into one number that hides the split.

**Weighting.** Three separate numbers per item, shown separately and combined only for ordering the list:

- **Relevance:** how well the item names the company (exact symbol and name, name only, sector, market-wide), how recent (a fading weight, in days), and whether it is routine filing noise.
- **Trust:** the tier prior at first; then a shrunk estimate of the source's measured record, as the share of its checked claims that were confirmed, with a prior of the tier so a source with six checked claims cannot be called 100 percent. This reuses the idea behind the analyst trust that already exists. Until a source has at least thirty checked claims the page shows "prior" beside it.
- **Independence:** items of one `story_id` share one vote.

**Does comparing with the warehouse make sense?** Yes, but only for kind A, and it is the strongest tool here because the warehouse is the exchange's own data. It cannot judge opinion, and it cannot check anything the exchange has not yet filed (results before they are filed): those stay "not yet filed", not "wrong".

**What we will not do:** a majority vote on facts; ask a model which source to believe; let a popular site outrank a filing; hide a contradiction to make the page tidy.

## 6. Citations and showing the person where information came from

Every row on the page has a source chip, the publisher name, the published time, and the link to the original. Every number in a model reading is tied to an item id and shown with its chip; a reading that cites an id that is not on its sheet is rejected, as numbers off the sheet already are. The page has a "Sources" view listing each source with its tier, its status, its last fetch, its item count today, its checked-claim record, and its terms note. Exporting a reading includes its citations.

## 7. Transcripts and recordings

The document links are already stored. Plan: fetch the transcript PDF only on request for one company, read it with the existing retrieval index, and ask for management tone, guidance, and the questions analysts pressed, **each with a quote that is found in the passage it cites** (the check `retrieval.review` already does). Recordings (audio) are out of scope until a transcript is missing and the owner approves a speech-to-text step. Cost: one to three model calls per transcript, on demand, through the shared ceiling and cache.

## 8. The local classifier (later, as the owner said)

After the sources are configured and a few weeks of items are stored, run a bake-off: a small local finance sentiment classifier against the model reading, on a labelled sample of our own items. The labels would come from the owner on about 300 items, or from the stock's next-day move as a noisy substitute, stated as such. It is adopted only if it agrees with the careful labels at least as well as the model and costs less. Dependency weight (a model runtime of about 1 to 2 GB) needs the owner's approval first. It is English-trained.

## 9. Does any of it predict? (the gate)

Items collected from day one are stored point in time. After about three months a study asks, per source group and per tone, whether items before a date explain returns after it, using the evaluation harness and the rule we already use: ranges not points, a bootstrap interval that must exclude zero, and an honest statement of the smallest effect the sample could see (small, as with the other studies). Until then the whole thing is for reading. Past tone cannot be rebuilt for RSS sources, so waiting is the price of starting late.

## 10. Phases

| Phase | Work | Size | Depends on |
|---|---|---|---|
| E0 | Evidence package: item shape, store, source registry and health, citation chips in the Sentiment page, Sources view, the supervisor job | medium | |
| E1 | Press and regulator feeds (Moneycontrol, CNBC-TV18, NDTV Profit, Times of India, Business Today, The Hindu, Indian Express, SEBI, RBI, PIB), story clustering for copies | medium | E0 |
| E2 | NSE structured: insider trades, FII/DII, board-meeting notices; the Flows and insiders box | small | E0 | built 2026-10-29; the insider endpoint serves nothing filed after 2026-05-02 |
| E3 | Claim extraction and the program-side checks against the warehouse; Conflicts box; per-source record | large | E1, E2 | built 2026-10-29 with patterns, not a model; trust stays a prior until 30 claims settle |
| E4 | GDELT, Wikipedia page views (with its symbol table), India VIX from the warehouse; the Attention box | medium | E0 |
| E5 | Try-and-see group, each time-boxed to one session: BSE announcements, Zee Business, Yahoo news, Financial Express feed, Google Trends. Each ends as working or as off with the reason shown | small each, may fail | E0 |
| E6 | Transcript reading on request | medium | E0, model ceiling |
| E7 | Local classifier bake-off | medium | E1 to E4, owner approval of the dependency, labels |
| E8 | The predictive study (after about three months of stored items) and the decision on whether anything reaches a verdict | medium | all of the above and time |

Suggested order: E0, E1, E2, E4, E3, E5, E6, then wait for data, then E7 and E8. E3 comes after E1 and E2 because checks need claims and flows to check.

## 11. Decisions needed from the owner

1. **Terms comfort.** Public feeds for personal reading are fine to store as title, link and excerpt. Yahoo, BSE, Zee and Google Trends are grey: do you want them tried at all, and with what limit (we never defeat a block)?
2. **Transcript cost.** Is one to three model calls per transcript, on request, acceptable?
3. **Classifier.** Approve a 1 to 2 GB runtime install when the time comes?
4. **Labels.** Would you label about 300 items, or accept next-day moves as noisy labels?
5. **Google News.** Stays off (robots.txt) unless you decide otherwise.

---

# Where the whole project stands (2026-10-29)

Measured against the PRD phases. "Built" means in the repository with tests; "proven" means shown on real data to work.

| Area | State |
|---|---|
| **Phase 0 foundation**: data store, loaders, resolver, metrics, evaluation harness | built and in use |
| **Phase 1 stocks and ETFs**: orchestrator, valuation, technical, earnings, fundamentals, ownership, flow, news analysts; ETF analyst; backtest | built; the technical analyst and Kronos were tested and found to add no skill, so they are not used to decide; valuation ranges exist but the calibration study could only reject large skill |
| **Phase 2 arbitration**: debate then judge | built as a council that is opt-in; its value cannot be tested with so few conflicts |
| **Options and volatility**: option chain, live derivative prices, F&O board, flow detectors | built |
| **Terminal**: live prices, Chart Lab (drawings, indicators, replay, grid), screens, heatmap, watchlists, fundamentals, valuation, sentiment page | built and in daily use |
| **Data**: prices, actions, derivatives (since 2020), statements (2,492 companies), shareholding, documents, annual-report history back to 2016 | built; annual-report history is partial (the model that reads them timed out) |
| **Operations**: job status, locks, gates, error catching, backup, background supervisor, snapshot reads | built this week; the supervisor's first day broke the screens and was fixed (section 2.82) |
| **Phase 3 brokers**: paper broker, desk, five live adapters, live gate | paper proven; the five live adapters are written from documentation and **none has placed a real order**; Angel One's login, funds, holdings and orders read correctly on a real account (empty account) |
| **Phase 4 bonds and mutual funds** | not started |
| **Licence** | MIT |
| **Sentiment** | four publishers and filings, display only; this plan extends it |

## What is left

**Product work**
1. Everything in sections 1 to 10 above.
2. **Live trading test**: fund the Angel One account with a small amount and send the smallest real order through the full gate; then the same for any other broker the owner uses.
3. **Phase 4**: bonds and mutual funds (loaders from AMFI and the bond master, then the specialists).
4. **Alerts screen** (on hold by the owner) and **Reddit and X** (on hold).
5. **Annual-report history**: finish the Nifty 500 run once the cheap model answers; then re-run the ownership and valuation checks and re-score the calibration with the longer history.

**From the October 28 evaluation, still open**
- Warehouse writers hold the file for a whole run; the snapshot hides it but shorter holds are the real fix.
- Event-driven fetching instead of full sweeps; point-in-time (append-only) fundamentals.
- One owner for the Angel One session (three logins exist); the Windows credential store for the PIN and TOTP seed.
- Calibration should need a bootstrap interval above zero and shrink by sample size.
- Atomic writes for the notebook, formulas and strategies files; a host allow-list for fetched URLs; a size cap on unzipped reports.
- Dependency audits (pip-audit, cargo audit, npm audit; need approval to install), a Python lock file.
- Merge the eight Python start-up patterns into one helper protocol.

**Open decisions**: starting order limits for live trading; own-tables cutover for valuation; weekly backup to a second drive (today's copy is on the same drive); the nightly schedule is now replaced by the supervisor.
