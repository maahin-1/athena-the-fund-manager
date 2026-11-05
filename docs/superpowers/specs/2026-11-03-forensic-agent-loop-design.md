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

**Flag promotion (code, not a model).** A `FLAG` is *established* when code proves a relation **that the code owns** (the `checks` lane states relations from a registry of invariants: a balance sheet balances, a subtotal is the sum of its parts, the same restated figure is the same wherever it is printed) or when a second lane has independently supported it: an observation or answer that says something checkable (a quote or numbers, never "cannot determine") and rests on evidence that lane fetched itself and the flag does not cite. A relation a model chose is only a claim about which numbers must agree, so a flag resting on one stays *single-source*, shown last, labelled so and with the note that its numbers do differ as stated. (Changed after the independent review of Nov 4; the first version let a model's own relation count as proof.)

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

### 6e. What an independent audit found in 6b to 6d, and the corrected run (Nov 4, 2026)

Three reviewers read the code and results (a bug review of the loop, a methodology audit of the test, an architecture review). The methodology findings that matter, and what was done:

- **Coverage confound (the main one).** About half of the low group (0 or 1 elevated) had few measures computed (245 companies had none), and in the first run such companies fell 50% about twice as often as well-covered ones (27% against 13%, the reviewer's count); the high group was mostly well covered, because reaching three elevated is mechanically easier with more measures. Comparing the two groups on size alone therefore pushed the ratio toward 1. Added as a stratum (0 to 3, 4 to 7, 8 or more measures computed), alone and with size. This was added after seeing results; both the original and the added analysis are reported.
- **Statements under an old symbol were not joined.** A company renamed since its filings (45 in this window) had its statements under the old symbol, which the loader ignored, so those companies were missing or short of history at the entry date. The loader now joins them through the exchange's rename list (`athena.fundamentals.history`); the test universe grew from 1,942 to 1,982.
- **Power.** With about 100 companies in the high group the test can detect a ratio of about 1.2 at the 30% cut-off, 1.55 at 50% and 3.2 at 70% (80% power). "No separation" was too strong; the honest reading of the first runs is "nothing above about 1.5 at the 50% cut-off".
- **Dead measures.** Receivables, inventory, payables growth and additions against capex are computed for 5 to 53 companies at this date (they need four year-end balance sheets, held only since late 2022), and the implied interest rate for 64. "Three of 14" is in practice three of nine or ten, and operating cash flow against profit and accruals share inputs.
- **Wording of the pre-registered rule.** 6b says a signal that survives neither control is not reported; 6c applied "does not survive both". Both readings are shown below.
- **Not changed:** the outcome definition (largest fall), the exclusion of unexplained price breaks (it uses information after the entry date; the sensitivity run keeps them), industry labels as of now.

Corrected run (as of 1 July 2025, outcomes to 1 November 2026; 1,982 companies, 101 in the high group; `athena.forensic.statements.backtest`):

| Outcome | on size | on volatility | on coverage | on size and coverage |
|---|---|---|---|---|
| fell 30% | 1.02 (0.85 to 1.17) | 0.98 (0.83 to 1.12) | 1.08 (0.91 to 1.24) | 1.06 (0.89 to 1.23) |
| fell 50% | 1.16 (0.79 to 1.56) | 1.08 (0.74 to 1.47) | **1.54 (1.03 to 2.13)** | 1.44 (0.96 to 2.00) |
| fell 70% | 0.74 (0.0 to 2.07) | 0.70 (0.0 to 1.94) | 1.34 (0.0 to 4.0) | 1.19 (0.0 to 3.77) |

Trend of the rate of a 50% fall with the count of elevated measures (0, 1, 2, 3, 4+; not adjusted): z 1.54, p 0.12 for everyone; **z 3.34, p 0.0008** among the 1,013 companies with at least 8 measures computed, where the 50% rates run 11.2, 15.9, 17.4, 21.6, 29.2%. In that subset the ratio at 50% is 1.79 (1.08 to 2.67) on size, 1.35 (0.82 to 1.97) on volatility, 1.83 (1.10 to 2.67) on coverage. Per measure at 50% (size only): operating cash flow against profit 1.72 (1.23 to 2.25), accruals 1.53 (1.21 to 1.91), work in progress share 1.44 (1.01 to 1.91).

- **Reading.** There is now a weak, consistent hint at the 50% cut-off that grows with the count, strongest where the measures are best covered, and it does not hold at 30% or 70% (70% has two events). It is not shown: volatility explains part of it, the intervals touch 1 in the main specifications, there are nine headline looks and 28 per-measure looks, and this is one window in a falling market. No weight or band is changed. The next checks are a pre-specified cash-flow composite (cash flow against profit and accruals) tested at a second date and in the forward log, and continuous outcomes (excess return) with leverage and industry controls.

### 6f. Document checks run by code (Nov 5, 2026)

The review's clearest finding was that a free model should not cross-reference a long document: it stitches table rows into invented quotes, and the text lane's observations were all refused by the quote gate. Cross-referencing is now done by code, and the model only explains what code finds.

- **Where.** `athena.forensic.checks` (`rows.py` reads a statement table from a page: years from the heading, one value per year with the digits printed, nil and bracketed negatives, a note-reference column and trailing blocks of nil columns ignored; `document.py` holds the checks; `lane.py` is the code lane). `athena.forensic.supervisor` runs code lanes first, with no model call.
- **What is checked (104 checks on the first stored draft).** In each restated statement: total assets equal total equity and liabilities; assets are non-current plus current; equity and liabilities are equity plus both liability totals; equity is capital plus other equity; total income is revenue plus other income; profit before tax is income less expenses; profit for the year is profit before tax less the three tax lines; net change in cash is the three flows; cash at the end is cash at the start plus the change. Across the document: the same figure in the summary and the financial section (same basis only: consolidated and standalone are never compared), depreciation and profit before tax in the profit and loss against the cash flow, cash at the end against the balance sheet. Offer structure: fresh issue plus offer for sale is the offer; the objects with an amount do not exceed the fresh issue.
- **Tolerance** is the rounding the table itself carries (half a unit of the last printed digit of each number used), nothing looser.
- **Degradation.** A check that cannot run says so and why (statement not found, years unreadable, a line printed twice with different figures, two units stated, the two figures differing by about 10, 100, 1000 or 10000 times, values inside the table's own rounding) and never counts as clean. More than 20% of the checks that ran differing at once means a table was probably misread: no flag is written and the observation says so. One wrong figure makes every relation it sits in fail; that is one finding, not several.
- **What it writes.** One observation (how many ran, held, differed, could not run, and what is not built), a flag per distinct violated relation authored by `checks` with the contradiction stated in the evidence's own numbers (so the gate re-evaluates it), and one question to the text lane per flag asking for an explanation with a quote.
- **First stored draft (B S Sponge):** 104 checks ran and all hold; 9 could not run (the totals of the balance sheet in the financial section print their labels at the end of the page, apart from the figures). The restated statements in that draft reconcile; nothing is flagged.
- **Not built (listed in every result):** shareholding rows summing to 100% and share counts to the shares before the offer; litigation category totals; borrowings in the statements against the indebtedness section; restated figures against the management discussion. Price checks (pre-IPO prices against the band, holding after the offer) cannot run on a draft because the price is blank.

### 6g. The cash-flow composite at a second date, and the forward log: fixed before any result is seen (Nov 6, 2026)

Written down before the second-date run and before the first forward snapshot is graded. Nothing in it changes a weight or a band.

- **What is tested.** One composite, named in advance: *cash-flow composite elevated* = `cfo_to_profit` elevated **or** `accruals_ratio` elevated (each at the usual 90th percentile of its peers, as of the date). Comparison group = at least one of the two computed and neither elevated. A company with neither computed is out of this test (counted). The two share inputs, so they are one idea, not two; "both elevated" is reported as descriptive only.
- **Second date.** Measures as of **1 January 2025** (filings made on or before it), outcomes from the last close on or before it to **1 May 2026** (16 months). Everything else as in 6b: entry floor, split-adjusted closes, unexplained price breaks left out of the primary and kept in a sensitivity run, ceased-trading and trade-for-trade counted apart.
- **One primary.** Bad outcome = largest fall of the adjusted close of **50% or more**, standardised on size fifths and, separately, on volatility thirds (direct standardisation, bootstrap interval, 2,000 draws), as in 6b. The 30% and 70% cut-offs are reported beside it and are not tested. Per-measure and per-count tables are descriptive.
- **Rule for the reading, fixed now.** *Supported at this date* only if the ratio is above 1 with the lower end of its 95% interval above 1 on **both** size and volatility. *Leaning* if the ratio is at least 1.2 on both and the lower end is above 1 on one. Anything else: *not replicated*, and that is what the report will say. No other control or cut-off is allowed to rescue it.
- **Not independent.** The 16-month window overlaps the first one by about 10 months and sits in the same falling market, so agreement here is a consistency check, not a second confirmation. The report says so whatever the result. A weight change still needs the forward log.
- **Survivors first.** Before running, the stocks that traded on the entry date and stopped trading before the end of the window and have no statements are counted and fetched the same way as in 6d, so the test is not run on survivors only. If a fetch is not possible for some, the count is stated in the report.
- **Forward log.** `athena.forensic.statements.forward` freezes, on a stated date, the measures of every industrial company in the statements store (symbol, elevated measures, measures computed, values, peer ranks) together with the code revision and a hash of the content, in `.athena/forensic_forward/`. Grading uses the same outcome code as 6b on a later date and first checks that today's code gives the frozen measures (any drift is reported, not hidden). **First grading no earlier than 6 November 2027**, then the same primary (composite, 50% fall, size and volatility). The frozen file is the only record; a snapshot is never re-taken for a date to improve it. A new snapshot is taken each quarter and graded 12 months after.

### 6h. Results of 6g, and the document checks on six more drafts (Nov 6, 2026)

**The composite at the second date** (as of 1 January 2025, outcomes to 1 May 2026; `athena.forensic.statements.backtest`). Before the run, the stopped-trading names were counted: 93 traded on the entry date and stopped before the end of the window; 66 already had statements, 59 are old symbols of renamed companies, and 10 had neither (4 are rights entitlements or an ETF), so 6 were fetched (ISEC, SECURCRED, RBL, RANEENGINE, TECHIN, IWEL). 1,807 companies in the primary run (44 failed the entry floor; 5 had an unexplained price break).

- **Composite (cash flow against profit or accruals elevated, 229 companies; neither elevated, 1,362; 216 had neither computed):** fell 50% **1.15 (0.94 to 1.38)** on size and **1.09 (0.91 to 1.30)** on volatility; raw rates 37.1% against 31.1%. By the rule fixed in 6g: **not replicated**. Fell 30%: 0.98 and 0.96. Fell 70%: 1.30 (0.74 to 2.05) and 1.19. Keeping the unexplained-break companies gives the same.
- **Per measure at 50%, size only:** accruals 1.22 (0.96 to 1.50); operating cash flow against profit **1.09 (0.79 to 1.41)**, against 1.72 at the first date. The measure that looked strongest then did not repeat.
- **Three or more elevated against one or none, everyone:** fell 30% 0.99; fell 50% 1.12 (0.80 to 1.47) on size, 1.03 (0.74 to 1.32) on volatility; trend p 0.15.
- **Companies with 8 or more measures computed (869; 48 in the high group), the coverage sensitivity named in 6b:** fell 50% 1.60 (1.01 to 2.30) on size, 1.36 (0.88 to 1.92) on volatility, trend p 0.0001; raw 35.4% against 21.8%. This is the same pattern as at the first date, in the same direction, with the same weakness (volatility takes some of it and its interval reaches 1), and the windows overlap by ten months in one falling market, so it is not a second confirmation.
- **Reading.** The pre-registered composite is **not replicated**. What remains is the weak, count-based pattern among well-covered companies, seen at both dates, not shown at either. No weight or band changes. The forward log (below) is the only clean test left; the first snapshot is graded no earlier than twelve months after it is taken.

**The document checks on six more drafts.** Only one draft had been stored. Six more public drafts (Inox Air Products, Electromech Infraprojects, Deon Energy, Baanganga Gold & Diamond, Rathod Jewellery Manufacturing, Everbrands India) were read into a scratch copy of the document store and run through the checks lane. The first run raised **false differences** in two of the six (20 of 38 checks in one, 8 of 72 in another) and ran very few checks in others. Each cause was a class of layout, not a company, and each is now handled generally:

- numbers broken across a line after a thousands comma or a decimal point (`4, 032.79`, `21,002. 68`), and halves of a bracketed number (`(1` / `4,114.03)`): rejoined when unambiguous, otherwise not read as numbers;
- a statement spread over two pages (assets on one, equity and liabilities on the next, or a closing total alone on a short page): the pages are one statement;
- year headings printed `2025-26` (the year ended March 2026), which had been read as 2025 and paired with the wrong column on other pages;
- relations that leave out lines a statement may carry (instruments entirely equity in nature or non-controlling interest in equity; exceptional items or a share of an associate's profit above profit before tax; an exchange effect in the cash roll-forward): lines that sit between the terms are tried in or out, and when none reconciles the check is *not run* rather than reported as a difference;
- a page of adjustments to profit read as an income statement (an income statement now needs a revenue, total income or total expenses line);
- two tables of no stated basis, or that disagree on most of their shared figures, are two statements (another entity or basis), not one figure printed twice; and a statement whose own arithmetic mostly fails is set aside as misread, with the note in the observation;
- when the section finder does not find the financial sections at all, every page is looked at, and a page is a statement by what it holds.

After the fixes: **0 differences in all seven drafts**; checks that ran and held: 104, 81, 52, 39, 24, 15 and 6 (the last, Rathod Jewellery, prints each label in a text block apart from its figures, so rows cannot be rebuilt from the stored page text; reading it needs the PDF's word positions, which the store does not keep). This means two things, said plainly: the arithmetic of real drafts reconciles wherever the checks can read it, and **a genuine inconsistency in a real draft has not yet been seen**, so detection is proven on built examples (tests) and not on a real case.

### 6i. Reading statement pages by the position of the text, and what it did to the seven drafts (Nov 6, 2026)

The stored page text is the PDF's reading order. On some drafts that order puts a label in one block of text and its figures in another, so a statement page gives figures with no labels (Rathod Jewellery's restated statements, pages 367 to 369, gave no table at all). The reader now also keeps, for the pages that name statement lines (`total assets`, `total income`, `profit before`, `net cash` and so on), the same page read with the text placed by position (`layout_pages` in the document store; `python -m athena.documents.read --layout DOC_ID` adds it to a document already held, from the same address, refusing a file whose hash changed). The text lane and the search still use the reading order; only the checks lane chooses, **per page, whichever text gives more labelled lines of a statement** (the reading order on a tie). The choice is by yield and not a preference because position is worse on other drafts: on Inox Air Products it loses the year headings and sets neighbouring columns against each other (`312.84422.91`).

Checks that ran (all held) on the seven drafts, before and after, with **0 differences on every draft before and after**:

| Draft | Before | After |
|---|---|---|
| B S Sponge | 104 | 116 (none left unrun) |
| Electromech | 52 | 76 |
| Deon Energy | 24 | 42 |
| Rathod Jewellery | 6 | 51 |
| Everbrands | 81 | 81 (unrun 33 to 17) |
| Baanganga | 39 | 39 |
| Inox Air Products | 15 | 21 (from label fixes; position reading did not help it) |

(Part of each gain is the label and number fixes below, not the position reading alone.)

Testing on all seven found, and fixed, three things a single draft would not have shown:

- **A false difference from a parse rule.** The first run with position text gave Deon three differences on total equity. The printed page reconciles (share capital 240.10 plus other equity 474.40 is 714.50). The row `Equity share capital 20 240.10 0.10 -` has a note number and a real nil in the last year; the rule for a second block of nil columns dropped that nil and shifted the values. A trailing run of dashes is now a block only when it is as wide as the figures.
- **Rows read with the wrong columns.** A row with one more amount-like figure than there are year columns (Inox: `(414.97) 110.07 21 9.08`, where `21 9.08` is one number broken by a space) had its first figure taken into the label and the last three used as the years. Such a row is now left out, so a check that needs it is *not run* instead of using wrong values. Numbers broken by a space before a comma or point (`10 ,000.00`, `(8 .23)`) or inside a bracket (`(1 79.65)`) are joined, because nothing else starts with a comma or a point and an open bracket needs a closing one. `21 9.08` is not joined: two plain figures side by side cannot be told from a broken one.
- **Labels.** The cash roll-forward needs net change, opening and closing cash. Aliases now accept `equivalent` or `equivalents`, `and` or `&`, `at` or `as at`, and `cash equivalents` without `cash and`. Totals printed `non - current` (spaces round the hyphen) and the earlier-years tax line worded `taxation pertaining to earlier years` are found too.

Two defects of the document store found by the new tests: a document with no sections found, and a layout reading with no pages, both raised on insert (`executemany` takes no empty list). Both are guarded.

**What is still not read, said plainly.** Inox's cash flow roll-forward (the net change and closing cash are printed with broken figures such as `21 9.08`). Most remaining "not run" checks on the other drafts are lines the draft does not print (for example the tax adjustment for earlier years, or a total equity line), where *not run* is the right answer. Baanganga's financial sections are still not found by the section finder, so every page is searched. As before, **no genuine inconsistency has been seen in a real draft**, so detection is proven on built examples only.

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
