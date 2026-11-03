# Forensic agent loop: agents that investigate, remember, question each other, and a supervisor that concludes

Design of Nov 3, 2026. Owner answers that fix the shape: the supervisor gives a **concern level with evidence** (no buy or avoid); agents are **free within caps**; the first test is a **listed company with a known past problem**; budget is **about 40 model calls per target, run in the background**, while free models are used.

## 1. What this is for, and what it is not

**For:** finding things that do not add up in a company's own statements, its offer document, and what outsiders say about it, and showing the owner the two sides of each difference with page or number references. One engine for three kinds of target: a draft offer document (DRHP), a listed company's financial statements and filings, and the news and forum claims about a company.

**Not:** a fraud detector, an accusation, a buy or sell signal, or a rating. Every flag reads "A and B differ, here are both, here is what could innocently explain it, here is how to check". Ranges, not points; point-in-time; owner-only; not investment advice (standing rules).

## 2. Why the fixed list of 14 questions is replaced

The 14 questions (`athena.documents.drhp.QUESTIONS`) gather facts from sections chosen in advance. They cannot follow a lead (a receivables jump leads to the customer note, which leads to the related-party table), they never compare two places, and a 500-page document is mostly outside their reach. They stay as a cheap baseline to measure the loop against, not as the pipeline.

## 3. The parts

**Case.** One target at one date (`as_of`): kind (`drhp`, `listed`, `news`), identifiers, the documents and data held for it. Everything an agent sees is dated on or before `as_of`.

**Tools (all code, no model).** The only way an agent touches data; each returns a small, citable result.
- Documents: `doc.map()` (the section map with page counts, the table of contents an agent navigates by), `doc.open(section, from_page, pages)`, `doc.search(query, sections)`, `doc.table(section, name)` (restated statements through the existing statement extractor), `doc.figures()` (offer figures already taken by program).
- Financial: `fin.series(metric)`, `fin.ratio(name)` (Beneish, Sloan accruals, cash conversion, receivable and inventory days, other income share, tax rate, capex to depreciation), `fin.peers(metric)` (sector percentile), `fin.restated_vs_reported()`.
- Market and evidence: `mkt.prices(window)`, `mkt.events()`, `evidence.claims(symbol)` (checked headlines, E3), `insider.trades()`, `flows()`.
- Memory: `ledger.read(filter)`, `ledger.write(entry)`, `notes.read()`, `notes.write()`, `ask(agent, question)`.
- Verification (not callable by a model; runs on every write): a quote must be in the cited page; a number must be in the cited result; arithmetic is done only by tools.

**Agents (lanes).** Each is a loop: read its notebook and the ledger, choose one action as a JSON object, code runs it, the result goes back, repeat until it stops or hits its step cap. No native tool calling is relied on (free models are unreliable at it); the reply is `{"action": "...", "args": {...}}`, parsed by code, with one repair retry.
1. *Statements*: ratios, trends, restated against reported, peers.
2. *Text*: reads documents by navigating the map; extracts claims with quotes; answers other agents' questions from the text.
3. *Ownership and governance*: promoters, pre-IPO prices, pledges, insider trades, related parties, SEBI orders.
4. *Market and news*: price and volume around events, announcement timeline, checked headlines, attention.
5. *Peers*: what a document says about peers or the industry against what peers file.
Which lanes run depends on the case: DRHP uses 2, 1, 3, 5; listed uses 1, 3, 4, 2; news uses 4, 2, 3.

**Memory.** Two kinds, both in `.athena/forensic.duckdb`, both stamped with `as_of` and run id.
- *Notebook* per agent per case: what it checked (so it does not repeat), what it found, open questions, hypotheses it holds, and a running summary the agent rewrites when it grows past a size limit.
- *Ledger* shared by all: typed entries (below), append-only, each with author, time, evidence references and status.

**Messages (ledger entry types).**
- `OBSERVATION`: a fact, its value and its reference.
- `FLAG`: two or more references that disagree, a type (arithmetic, text against data, document against document, claim against outside evidence, anomaly against peers), and the innocent explanations considered.
- `QUESTION`: from one agent to another, a specific request ("does the text explain the rise in receivables in FY26?").
- `ANSWER`: a quote or value with reference, or "cannot determine". "No explanation found" is an answer and is never treated as evidence of wrongdoing.
- `NOTE`: a hypothesis or a reminder, not shown to the owner.

**Flag promotion (code, not a model).** A `FLAG` is *established* when code proves the contradiction (two verified numbers that cannot both be right) or when two independent lanes support it with verified evidence. Otherwise it stays *single-source* and is shown last, labelled so.

**Supervisor.** A loop of its own with three jobs: plan (choose lanes from the case and the section map, give each a first brief), referee (read notebooks and the ledger, route unanswered questions, merge duplicate flags, ask for a second opinion on a flag that has one source), stop (when the budget is spent, or the lanes have nothing new for a full round, or coverage is complete). It writes the narrative. **The concern level is computed by code** from the established flags (count, type weights, corroboration, size against the company) and shown as a range; the supervisor's text cannot change it. The report always carries: established flags with evidence chains, single-source observations, what was examined, **what was not examined and why**, and the calls and tokens used.

## 4. Caps and cost

Per case: about 40 model calls in total, split by the supervisor (it starts with a plan of about 8 per lane); each agent at most 6 steps per round and 2 rounds; `max_tokens` 6000 (the mid tier's model reasons first); the cheap tier first and the mid tier on failure (`reader` and `reader_retry` roles), the supervisor on mid until a paid tier is chosen. A run is a background job with progress and a preview of its worst-case size. Model calls that time out twice are skipped for 15 minutes (done).

## 5. Reliability with free models

JSON action protocol with one repair retry; a hard step cap so a looping agent ends; code verifies every quote and number, so an invented citation is dropped and counted; a lane that fails entirely is reported as not examined, never as clean; the baseline (fixed questions plus code detectors) always runs first and is kept, so a failed loop still leaves a useful report.

## 6. How we know it works

A test set of past Indian governance failures (public cases, listed companies), run as of a date well before the public event, with only data that existed then. Controls: matched healthy companies from the same sector and size. Measured: did the established flags include the thing that later became public (detection rate), how many established flags fire on controls (false-alarm rate), calls and tokens, and the same numbers for the baseline. The loop is kept only if it beats the baseline on detection at an acceptable false-alarm rate. The set is for measuring and never for tuning a rule to a company (standing rule: no per-ticker fixes). **Open:** whether the warehouse holds enough point-in-time statements for the chosen cases; the first task is to list candidate cases and check coverage.

### 6a. What the data allows (found Nov 4, 2026, while building the statements lane)

- The store holds **income statements from FY2019 filings, but balance sheets and cash flows only from filings made since October 2022.** Four consecutive year-end balance sheets exist only for FY2023 to FY2026, so the measures built on growth over three years (receivables, inventory, payables, fixed-asset additions against capex, implied interest rate) have no history before mid-2026. On 1 July 2025 about ten of the fourteen measures can be computed, for between 800 and 1,700 of 1,956 industrial companies; on 1 July 2022 only the four income-only measures can (about 550 to 860 companies).
- The two well-sourced failures checked cannot be tested on statements. **Gensol Engineering** (SEBI interim order 15 April 2025, funds diverted through related parties) has one annual year in the store; **Brightcom Group** (SEBI forensic audit ordered September 2021, disclosed 28 February 2022; interim order August 2023) has income statements from FY2019 but no balance sheet or cash flow before late 2022, and the forensic audit was public long before the 2023 order. A test set of famous failures is therefore a handful of anecdotes at best.
- So the evaluation is changed to three parts. **(A) A cross-sectional test:** compute the measures and the convergence count as of 1 July 2025 for every company, then compare what happened to the companies with several elevated measures against the rest over the following sixteen months (the largest fall from the 1 July 2025 price, the loss against the market, trading suspensions and surveillance listings where we can see them, auditor changes where announcements are held). Roughly a thousand companies, so a rate with a spread, not three stories. The outcome is a bad outcome, not proof of fraud, and it is stated that way. **(B) The verified cases** (Gensol, Brightcom, others verified from sources) as illustrations, with the income-only measures where the data allows, and said plainly where it does not. **(C) A forward log:** every report is kept with its date and graded against what happens in the following months, the way claim trust is graded.
- Deeper history (balance sheets and cash flows for FY2019 to FY2022) could be added from annual reports with the existing annual-report extractor; it is slow and model-assisted, and is deferred until (A) shows whether the detectors carry any signal at all.

### 6b. The cross-sectional test, fixed before any result is seen (Nov 4, 2026)

Written down before the harness was run, so the cut-offs cannot be chosen after looking at outcomes (standing rule: never tune to price).

- **Date and window.** Measures and convergence are computed as of **1 July 2025** (only filings made on or before it; peers ranked on the same date). Outcomes run from the last close on or before 1 July 2025 to **1 November 2026** (16 months).
- **Who is in.** Every industrial company the statements universe holds that has a close within 7 days before the entry date and at least 40 bars in the 60 trading days before it (a floor so a stale price is not read as a price). Companies that fail the floor are counted, not dropped silently.
- **Prices.** Warehouse bars with recorded splits and bonuses applied (`athena.warehouse.history`). A company with a large overnight break (under 0.6x or over 1.7x) that no record explains, inside the window, is **left out of the primary result** (an unrecorded split would read as a collapse) and shown in a sensitivity run that keeps it.
- **Bad outcome (primary).** The largest fall of the adjusted close from the entry close at any point in the window, at three cut-offs reported side by side: **30%, 50%, 70%**. No single cut-off is the headline.
- **Secondary.** Return over the window against Nifty 500, and the share that fell 30% or more *relative to* Nifty 500 at any point. **Ceased trading** (last bar before 1 November 2026 and before the end of the window) and **moved to the trade-for-trade segment** (series BE or BZ at any point in the window, having been EQ at entry) are counted as separate outcomes; delisting cannot be split into merger and distress with the data held, and the report says so.
- **Groups.** By the number of elevated measures at the entry date: 0, 1, 2, 3, 4 or more. The comparison that matters is **three or more against one or none**.
- **Size and volatility control.** Companies with weak statements tend to be small and volatile and fall more regardless. Size is the average daily traded value (close x volume) over the 60 days before entry, in fifths; volatility is the standard deviation of daily returns over the 250 days before entry, in thirds. The headline ratio is the rate in the high group divided by the rate it would have if it had the rates of the low group *within each size fifth* (direct standardisation), with a bootstrap interval; a second run standardises on volatility thirds. A signal that survives neither is not reported as one.
- **Per measure.** The same standardised ratio for each of the 14 measures alone (elevated against not). Fourteen looks means some will appear by chance; the report says so and these are for orientation, not for choosing weights.
- **Coverage control (added before the first run).** A company with few measures computed has fewer chances to reach three elevated, so a second run keeps only companies with at least 8 of the 14 measures computed. The primary result is still the one over everyone; if the two disagree, both are reported.
- **No tuning from this run.** The scoring weights and bands stay as they are. If the result is strong, a weight change needs a second, later date (the forward log), not this one. If it is null, the report says null.
- **What this does not test.** The model loop (text reading, questions between lanes, the supervisor's narrative). It tests only whether the code measures carry information about later outcomes.

### 6c. Result of the first run of 6b (Nov 4, 2026)

`python -m athena.forensic.statements.backtest` (code: `athena.forensic.statements.backtest`, `outcomes`; tests `tests/test_forensic_backtest.py`). 1,914 companies in the primary run (37 failed the entry floor; 5 had an unexplained price break in the window and are kept only in the sensitivity run). Nifty 500 fell about 7% over the window, with a trough 13% under the entry level.

- **Primary result: null.** High group (three or more elevated measures, 97 companies) against low (one or none, 1,625 companies), observed over expected given size fifth: fell 30% **1.04** (95% 0.89 to 1.18); fell 50% **1.12** (0.74 to 1.51); fell 70% **1.20** (0.0 to 3.13, 3 events); 30% below Nifty 500 **1.06** (0.86 to 1.24); moved to trade-for-trade **1.14** (0.77 to 1.53). Standardising on volatility instead gives the same picture (1.01, 1.08, 1.20). Raw rates look slightly worse for the high group (fell 50%: 25.8% against 20.7%), and size explains most of it.
- **Coverage control (companies with at least 8 measures computed, 973 of them; 74 high).** Fell 50%: 1.71 (1.02 to 2.60) standardised on size, but **1.32 (0.81 to 1.98)** on volatility. By the rule in 6b a signal that does not survive both controls is not reported as one. Fell 30%: 1.11 and 1.05, both including 1.
- **Per measure (size only, fell 50%, 14 measures looked at, so some will look good by chance).** Two stand out: operating cash flow against profit **1.74 (1.25 to 2.28)** and accruals **1.55 (1.17 to 1.94)**. Neither does at the 30% cut-off (1.01, 1.02). A post-hoc check, not in 6b: among companies with 8 or more measures, size and volatility together give 1.53 (0.96 to 2.29) and 1.48 (0.93 to 2.19). Suggestive, not shown; to be tested on a later date.
- **What the run cannot see: survivors only.** The statements store holds the companies listed today. None of the 104 main-board stocks that stopped trading before October 2026 is in it (a mix of ETFs, renamed symbols and small names, not sorted further here; genuine delistings are among the absent), so "stopped trading" is zero for everybody and the worst outcomes (delisting after a collapse) are missing. That biases the test toward finding nothing. A null here does **not** clear the measures.
- **Other limits.** One date, one 16-month window, one market phase (a falling small-cap market: 60% of all companies fell 30% at some point, 20% fell 50%). Prices where an exchange record of a small split or bonus is missing can read as a fall.
- **Decision.** No weight or band changed. The measures are kept as a screen whose strongest candidates are the two cash-flow measures; the forward log (C) and a second date (as of 1 January 2025, outcomes to 1 May 2026, overlapping, so not independent) are the next checks. Adding delisted companies to the statements store would remove the largest bias and is worth doing before drawing any conclusion about the loop.

### 6d. The same test with the stopped-trading companies added (Nov 4, 2026)

The 104 main-board stocks that traded on 1 July 2025 and stopped before October 2026 were sorted: 66 are old symbols of companies that carry on under a new one (their statements are already joined), 40 already had statements, and 38 had none. Those 38 were fetched from NSE with the ordinary ingest (`--universe SYMBOL,SYMBOL --no-history`): 34 returned filings (12 to 19 each) and 4 returned none (ETFs and similar). The test universe grew from 1,914 to 1,941 companies, and 1.4% of them now stop trading inside the window.

- **Result unchanged.** High against low, size-standardised: fell 30% **1.02** (0.85 to 1.17); fell 50% **1.14** (0.76 to 1.57); fell 70% **1.21** (0.0 to 2.92); 30% below Nifty 500 **1.05** (0.85 to 1.24); moved to trade-for-trade **1.15** (0.76 to 1.59); stopped trading **1.26** (0.0 to 3.93, 2 events). Volatility-standardised gives 0.98, 1.08, 1.17.
- **Eight or more measures computed:** fell 50% **1.72** (1.04 to 2.6) on size and **1.31** (0.81 to 1.94) on volatility, as before: by the rule in 6b not a signal.
- **Per measure:** operating cash flow against profit 1.76 (1.25 to 2.31) and accruals 1.55 (1.2 to 1.92) at the 50% cut-off, nothing at 30%. Still orientation only.
- **The survivor bias for this window is now small**, since every stopped stock with filings is in. It remains for earlier dates (about 1,060 main-board symbols with more than 200 bars ended before October 2026) and those would need the same fetch, with statements before late 2022 limited to income lines.
- **Reading.** One window, one date: the 14 measures as a group do not separate later price outcomes from size and volatility here. That is not evidence they are useless (the outcome is a price fall, which many things cause; the measures aim at governance and earnings quality), and not evidence they work. The forward log and a second, later date decide.

## 7. Build order (each step testable on its own)

1. **Skeleton**: store, ledger, notebooks, tool registry, agent loop with a scripted fake model, supervisor, flag promotion, concern-level rule, cost accounting. All tests with fake models.
2. **Statements lane and detectors** (code first): the ratio set above, sector percentiles, restated against reported; a listed company end to end with the lane alone.
3. **Test set and harness**: candidate cases, point-in-time coverage check, baseline against loop.
4. **Text lane** with navigation by section map; DRHP as the target (restated statements through the existing extractor, adjustments table, pre-IPO prices, promoter holding checks).
5. **Ownership and market lanes**, then peers.
6. **Interfaces**: a Rust endpoint and a Forensics box (listed) and an Analyse button on the IPO tab; the concern level as an extra input to the existing blend.
7. Reddit and X remain on hold; Attention data (E4 of the evidence plan) can feed the market lane.

## 8. Risks and open questions

- Free models may be too weak to navigate a long document well; the baseline and the scripted fallback bound the damage, and the harness tells us.
- Language: wording is reviewed so a flag never reads as an accusation about a named person; promoter and director names are used only to look up public orders.
- Data: Layer C sources (SEBI orders, exchange announcements) are free but their structure must be checked before relying on them.
- SME issues have no SEBI draft (the exchange holds it); they are shown with a warning until the exchange filings are connected.
- Later, recorded: crypto and forex scope (plan section 8).
