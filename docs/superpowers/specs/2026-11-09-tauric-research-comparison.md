# Tauric Research's two repositories against Athena: what to learn, what not to take

Written Nov 9, 2026 from a read of both repositories (shallow clones of `TauricResearch/TradingAgents` v0.7.1 and `TauricResearch/Trading-R1` v0.1.0, both Apache-2.0), their READMEs, and the code of the parts named below. Nothing was run from them. Nothing in this note is built yet; section 5 lists what would be, for the owner to choose from.

## 1. What each one is

**TradingAgents** (about 9,000 lines of package code, about 11,800 lines of tests) is a LangGraph pipeline that imitates a trading firm. Four analysts (market, sentiment, news, fundamentals) run side by side, each on its own tools and private message history, with a cap on tool rounds and a forced wrap-up turn. Then a bull and a bear researcher debate, a research manager commits to a five-tier rating, a trader proposes a transaction, three risk debaters (aggressive, neutral, conservative) argue, and a portfolio manager gives the final Buy / Overweight / Hold / Underweight / Sell. The decision is appended to a markdown **memory log**; later, when the holding window has traded, it is **settled** (raw and alpha return against a regional benchmark), a short **reflection** is written by a model, and that lesson is injected into later runs for the same and other tickers. A **backtest** command runs the graph over a ticker-by-date grid and reports hit rate and mean alpha by rating. A great deal of the recent work (releases 0.4 to 0.7) is look-ahead and point-in-time repair, with tests named for each leak. It reads Yahoo, Alpha Vantage, SEC EDGAR, FRED, Polymarket, StockTwits and Reddit, runs on about 15 model providers, and can screen social posts with TypeSafe's Jev.

**Trading-R1** (about 28,600 lines) is not the model of the paper of the same name. It is a terminal and background service for financial research that talks to a hosted model (`tauric`, needs a Tauric account and key) or to OpenAI, Anthropic, Gemini or a vLLM server you run. Its interest for us is the harness around the model: one bounded tool-calling loop; **an evidence store** in which every data, compute or web result becomes an immutable record (`e7`) with provenance, and later tools take values by reference (`prev:e7.ttm.total_debt`) instead of the model retyping them; a context that is only a *view* of that store (a cut result becomes a pointer the model can restore by id); a grounding-review and a verify pass before the answer; about 100 plain-code compute tools (valuation, risk, portfolio, fixed income, derivatives, technical, statistics); 17 markdown **skills** (workflows such as earnings-quality, relative valuation, scenario analysis, each with principles and the tools it needs); durable user **memory** notes; **scheduled reports** delivered to email, Telegram or a folder; PDF and Markdown export; a background service.

## 2. Side by side

| | TradingAgents | Trading-R1 | Athena today |
|---|---|---|---|
| Output | A rating and a trade proposal | A researched answer, exported report | A concern range with evidence; never buy or avoid |
| Who does arithmetic | Mostly the model | Tools (`calc`, 100 compute tools), then a model-run verify pass | Code only; a model cannot enter a number the tools did not return |
| Proof that a finding is real | A structured schema; prose | Evidence ids; a grounding-review prompt | A gate on every ledger write (quote in cited page, number in cited result, code evaluates stated relations); only code-owned relations or a second lane make a flag established |
| Point in time | Many fixes, tested one leak at a time | Evidence dated by `as_of` | Filing-date cut in statements; first-seen cut on claims; as-of days on every lane tool; a source's coverage reported as unavailable, not empty |
| Memory | Decision log with settled outcomes and model-written lessons fed to later runs | Notes about the user | Ledger and notebook per case; a forward log graded no earlier than 2027-11-06 |
| Test of whether it works | A grid backtest; scores by rating; says it is "indicative" | None | Blinded backtests, calibration studies that refuse to claim skill, planted-error sensitivity for the document checks |
| Documents | None (statements through vendors) | Web and filings by search | Whole offer documents cut into sections, ten families of arithmetic checks, a draft-against-prospectus comparison |
| Markets | US first; India by `.NS`; crypto | Any | India first: NSE filings, shareholding, insider filings, warehouse of bars from 2010 |
| Reports | One-page HTML, saved markdown | PDF, Markdown, email, Telegram | JSON document and a panel; no export |
| Schedule and delivery | Headless flags for cron | Jobs, daemon, three delivery channels | An operations supervisor; no delivery |
| Models | About 15 providers, per-tier provider, model catalogue and capability checks | Profiles, health, usage | Three tiers with fallback chains, a health file, a monthly call meter |
| Fixed income, option pricing, portfolio optimisation | Not present | Present as compute tools | Not present (Phase 4 not started) |

## 3. Where Athena is ahead, and should stay

The gate, the refusal of advice, code-owned relations, the document checks, India-specific data and the habit of saying what was not examined are not in either repository, and they are the reason a flag from Athena can be believed. The Tauric rating pipeline would break the owner's rule if copied (a trader that proposes a transaction and a portfolio manager that approves it). Their own README says results vary with model, temperature and period and are not advice; the TradingAgents paper reports gains from historical backtests, not live trading. Neither repository measures whether its rating beats a base rate by an interval that excludes zero; Athena's studies do, and mostly report that nothing is shown.

## 4. Things that are not worth taking

- The five-tier ratings, the trader, the risk debaters and the portfolio manager (advice; and Athena's own council debate is opt-in and untested).
- Bull and bear debate as a source of truth. It produces fluent text, not a checkable relation.
- The hosted `tauric` model and API. Using it sends prompts off the machine and ties the owner's use to a Tauric account; Athena is owner-only on free tiers and local files.
- StockTwits and Reddit sentiment (Reddit and X are on hold by the owner's decision, and the plan's rule is that no tone feeds a verdict untested).
- Model-written "lessons" fed back into verdict runs. TradingAgents does this point-in-time, which is careful, but it is a feedback loop on a signal not shown to work; if built it must be display-only.
- Crypto and forex modes (decided out of scope in 6w).

## 5. What could be built, ranked

Effort: S under a day, M a few days, L a week or more. Every item keeps the standing rules (generic, ranges, code does the arithmetic, point-in-time, owner-only).

**First, cheap and directly useful**

1. **Entry-day audit for settlement (S).** TradingAgents enters a position at the *first close after* the analysis date, because the analysis reads news after that day's close (their issue 1518), and waits for the whole window to trade across long holidays. Athena's verdict settlement measures from "the verdict's price day". Check whether the verdict's inputs include that day's close and news; if so, move the entry to the next close. This could change how the calibration reads, so look before changing.
2. **A point-in-time leak test suite for Athena's lane tools (S to M).** Their test names are a checklist of leaks: fundamentals knowledge time, statement filed-after-date, undated items admitted only when the window reaches the present, live profile snapshots withheld from historical runs, memory lessons only after their resolution date. Write the same tests for the statements, ownership, market, peers and claims tools. One real candidate: the company's industry and name come from today's tables even for a case dated in the past.
3. **Instrument identity in every lane prompt (S).** Their fix for the model inventing a different company: resolve name, sector, ISIN and listing date by code and put them first. Athena's statements lane has a profile tool; the text, ownership and market lanes do not start with one.
4. **A forced wrap-up turn at the step cap (S, small gain).** Their analyst, on reaching its tool-round cap, is told to write its report and that turn ends it whatever it answers. Athena already warns an agent that has written nothing when few steps are left (`agent.py`, "Write now what you have verified so far"), so most of this exists; what is missing is a last turn that is guaranteed, not asked for.
5. **Model-chain health at the start of a long job (S).** Found today, not in their code but of the same kind as their model catalogue validation: the cheap tier's first model times out, the OpenAI key is expired (401), and the mid tier's first model answers 404; only the mid tier's last model works. The annual-report run refused to start for this reason. Show it at the top of the terminal's status and rewrite the chain order from the probe.

**Medium**

6. **Report export (M).** One-page HTML and Markdown (PDF later) of an Analyse result and of a forensic case report, with the run's settings recorded, as TradingAgents saves and Trading-R1 exports. The owner can then keep, print or share a result.
7. **Playbooks for lanes (M).** Trading-R1's skills are markdown with frontmatter, steps and principles ("the outlier is the phenomenon of interest"; "do not assert contested thresholds"). Athena's lane briefs are one sentence. Free models do better with explicit steps; a test can check that every tool a playbook names is registered, as `test_every_tool_a_lane_names_is_registered` already does for lanes.
8. **Resume an interrupted investigation (M).** Their checkpoint resume. Athena keeps the ledger and evidence in DuckDB, so a rerun could start from the entries already kept and skip lanes that finished.
9. **Scheduled digests with delivery (M to L).** Trading-R1's jobs and channels. For Athena: a weekly note of new SEBI drafts, what changed between a draft and its prospectus, the quarterly reminder to take the forward-log snapshot, and statements that newly differ; to a folder first (safe), email or Telegram only with the owner's credentials in the existing secrets path.
10. **Lanes in parallel (M).** Their analysts run side by side. Athena's lanes run in turn; the call budget is unchanged, only the waiting time falls.
11. **Jev screening of headlines (M).** TradingAgents uses Jev to ask "is this post about the company" and "which way does it lean" and drops the clearly off-topic. Athena's claim extraction names companies by patterns; Jev could screen relevance for the Sentiment page and the claims. Needs the owner's key; display only.

**Large**

12. **A library of plain-code analytics (L, in stages).** Trading-R1's compute tools are the largest gap: bond price, yield, duration and convexity, yield curve and credit spread (Phase 4 bonds); Black-Scholes, binomial and implied volatility with position Greeks (Athena has option chains but no pricing); portfolio risk (VaR, stress, drawdown, beta, correlation) and optimisation; three-statement and LBO models. They suit the "code does arithmetic" rule exactly and would be reached from the lanes as tools. Reimplement from textbook formulas with tests; the Apache-2.0 licence would allow adapting code with a notice, but formulas do not need it.
13. **Result references between tools (M inside 12).** `prev:e7.path` lets a tool take a number from an earlier result without the model retyping it. Athena's flags already cite `{ev, path}`; extending that to tool arguments would remove a class of copying errors.
14. **A display-only record of past calls (M).** Settled outcomes of the concern levels Athena has given, with the date they became known, shown to the owner. This is the safe half of TradingAgents' reflection loop; the half that feeds lessons into the next run is not recommended.

## 6. Suggested order

1, 2, 3, 4 and 5 first (all small, all about correctness). Then 6 (the owner can use it at once). Then 12 in stages, starting with bonds because Phase 4 needs them. Items 7 to 11 and 14 as the owner prefers.

## 7. Decisions for the owner

1. Which of the items above to build, and in what order.
2. For 9, which delivery channels, if any beyond a folder.
3. For 11, whether to use the Jev key for headline screening.
4. For 12, whether Phase 4 (bonds and mutual funds) is to start now.
