# Agentic Multi-Asset Hedge Fund — Technical Requirements Document (TRD)

Sep 26, 2026 · @maahin · Revised Oct 3, 2026 (gap closure — see Revision history)

## 1. System architecture overview & design principles

**Pipeline shape.** `Input resolution (deterministic resolver → optional model fallback → ask the user) → data layer (live adapters + local batch store, each dataset carrying coverage and as-of) → metrics engine (code-computed metrics packets) → specialist agents (parallel, by asset class) → conflict detection → debate-then-judge arbitration where needed → risk & compliance overlay (rule-based, never debated) → unified verdict + full report → visualization/dashboard agent → (optional) paper/live execution`. This mirrors the finance guide's own recurring 5-step pattern (data input → framework application → signal generation → quantitative validation → action/output) generalized across desks, with an explicit judge stage the guide never specified.

**Design principles**

- **Portability first.** Every agent's brain — role, evaluation method, required inputs, output schema — is a plain spec file (markdown system prompt + JSON/YAML output contract), never vendor-specific skill syntax. Any agent runtime that can load a system prompt and enforce a schema can run it.
- **Persona is prompt, plumbing is shared.** Following virattt/ai-hedge-fund's `LLMAgent` pattern, each specialist file is only its system prompt; JSON parsing, caching, abstention ("too hard, go neutral"), and fail-loud vs. fail-quiet behavior live once in a shared base class every specialist inherits.
- **Compute in code, judge in prompts.** Any figure a specialist cites (alpha, beta, tracking error, expense drag, overlap, ratios) is computed by the metrics engine (§2.13) and handed to the specialist as data. A specialist interprets numbers; it never calculates them. This is what makes "computed, not asserted" enforceable and lets the number-grounding check (§2.14) work.
- **Honest coverage.** The system is built on free data only, so some inputs will be missing. Every specialist output and every evaluation states what data it ran on (`data_coverage`) and what was missing. A specialist reports `partial` or abstains rather than inventing a figure.
- **Deliberation where it needs judgment, arithmetic where it doesn't.** Disagreement requiring qualitative weighing (valuation says buy, risk flags a limit breach, ESG flags governance) goes through the debate-then-judge protocol (§2.15), adapted from TauricResearch/TradingAgents' bull/bear → Research Manager and aggressive/conservative/neutral → Portfolio Manager pattern. Low-stakes fan-in of many similar numeric signals uses ai-hedge-fund's simpler conviction-weighted blend (`Σ weight·signal / Σ weight`, respecting each agent's long-only vs. long-short permission). Hard limits (position size, VaR, drawdown) are rules, not debates.
- **Vendor-neutral LLM layer.** A small provider registry (model name → client) keeps Anthropic, OpenAI, and others interchangeable, following both reference repos' pattern (ai-hedge-fund: a JSON-driven registry; TradingAgents: explicit per-vendor clients including Bedrock/Azure). Any optional vendor-specific component (e.g. a decision-model classifier, §2.12) sits behind an interface with a non-vendor implementation.
- **Capability-aware adapters.** Every data source and broker sits behind a common interface declaring what it actually supports — native, emulated, or absent — modeled on ccxt's `describe()`/`has{}` pattern, so the orchestrator degrades gracefully instead of hard-failing.
- **Backtest integrity.** Historical inputs are "blinded" — tickers, names, and calendar dates replaced with relative markers — following ai-hedge-fund's approach, so an agent's evaluation can't lean on memorized knowledge of how a name actually performed.

Full provenance for every claim in this section is in §7, Full repository reuse audit.

## 2. Component specifications

**2.1 Orchestrator / Portfolio Manager agent.** Resolves the input (§2.12) and routes it to the specialist subset (§3 routing table); collects their verdicts; detects conflicts that require judgment vs. those that are simple numeric fan-in using the trigger rule in §2.15; runs the appropriate arbitration path (§1); applies the risk overlay's verdict as a hard override capability; emits the unified verdict. Judge prompts are adapted near-verbatim from TradingAgents' Research Manager / Portfolio Manager: conflict alone is never grounds for a default Hold — commit to the stronger case, sized by how decisively it wins, or state plainly why the evidence is genuinely balanced. *Skeleton implemented in Plan 1c:* resolve, route by the section 3 table, run the specialists that exist, and a conviction-weighted blend (resolution_path "blend") with the 2.15 conflict detection; the debate and judge, the risk overlay and every specialist other than Quant/Technical are not built. Try it with `python -m athena.cli SBIN`.

**2.2 Specialist agent base class.** One shared class (following ai-hedge-fund's `LLMAgent`) implements: prompt assembly from the persona file + a `FundamentalsSnapshot`-style data render (including the metrics packet from §2.13), JSON output parsing against the agent's schema, an abstention path ("too hard, go neutral") rather than a forced answer, fail-loud vs. fail-quiet error handling, response caching, and a `blind` mode for backtests that strips identifying tickers/dates. It also sets and enforces `data_coverage`: when required inputs are missing the class computes the label from the specialist's declared required-inputs list, abstains automatically on `insufficient`, and passes the missing list into the prompt on `partial` so the reasoning can qualify its conclusion. Every output passes through the number-grounding validator (§2.14) before it is accepted. *Implemented in Plan 1a:* coverage derived from the declared critical and optional metric names, abstention without a model call, JSON parsing, contract validation, number grounding, one retry with feedback, response caching, and fail-loud (default) or fail-quiet handling, behind an `LLMClient` protocol. Not yet implemented: `blind` mode (arrives with the backtest) and a real provider client (Plan 1b). A spec can also declare `shows` (the only figures the model sees, and the only figures its numbers are checked against) and a packet can list `not_applicable` figures (meaningless for this instrument, for example free cash flow for a bank), which count neither as missing nor against coverage. Every system prompt explains how to read each unit.

**2.3 Equity specialists** (from the finance guide's Equity Research, Strategy Consulting, Quant Trading, Earnings Intelligence desks): Valuation (Graham net-net, Lynch PEG/GARP, Buffett owner-earnings/moat, comps/DCF), Moat & Quality (moat width/trajectory/competitive-advantage period), Quant/Technical (multi-timeframe trend requiring all timeframes aligned, Bollinger Bands, volume confirmation, minimum 2:1 reward:risk), Earnings Intelligence (pre-earnings decision matrix, Sloan-accrual earnings-quality scoring). **Data dependence (see §6.1):** Valuation, Moat, and Earnings need multi-year financial statements, which free sources provide only patchily. When statements are missing these specialists run in a reduced **ratio-only mode** (ratios and index/valuation multiples that are available) and report `data_coverage: partial`; owner-earnings and net-net work requires statements and is skipped with the gap listed. Quant/Technical needs only price/volume and is fully covered. *Quant/Technical implemented in Plan 1a:* ta-lib indicators and multi-timeframe trend alignment are computed in `technicals/packet.py`; the specialist is a persona plus a critical/optional metric list in `agents/quant_technical.py`. Valuation, Moat & Quality and Earnings Intelligence are not yet built. *Valuation, Moat & Quality and Earnings Intelligence implemented in Plan 1f in ratio-only mode* (`agents/valuation.py`, `moat_quality.py`, `earnings_intelligence.py`): P/E, P/B, owner-earnings and free-cash-flow yield, PEG, Graham's number and the market's own valuation percentile; returns, margins, growth and leverage as evidence for a moat (never its source); Sloan accruals, cash conversion, quarter growth, surprise history and the next report date. Not built: discounted cash flow, peer comparables, Graham net-net, and moat source analysis.

**2.4 Debt specialists** (from the Fixed Income & Credit desk): Credit Analysis (covenant rated strong/standard/weak/red-flag, spread decomposition, distressed-recovery waterfall by capital-structure tranche), Duration/Curve Positioning. **Scope for Phase 2:** government securities and listed corporate bonds using price, yield, rating, and issue terms. Covenant rating and the recovery waterfall require offer-document data; no free API for it was found, so they are produced only when that data is obtained (extraction from exchange-filed documents is a later, separately scoped item) and otherwise listed as missing.

**2.5 Mutual Fund Analyst (new, no OSS precedent found).** Inputs: scheme NAV history (via mftool), expense ratio (AMFI batch data), holdings with ISIN (AMFI/AMC monthly disclosure, batch), category benchmark (static category→benchmark mapping table maintained in this project). Manager tenure is optional — no free source was found. Metrics, computed by the metrics engine (§2.13), not by the specialist: expense-ratio drag vs. category, alpha/beta net of fees vs. category benchmark, style-drift signal (returns-based style analysis from NAV), holdings-overlap score against the user's existing positions (monthly granularity, stated in the output).

**2.6 ETF Analyst (new, no OSS precedent found).** Inputs: fund holdings (source to verify, §6.1), stated index total-return series (niftyindices.com historical data and jugaad-data index TRI), ETF NAV and market price history, average daily volume and quote depth from live NSE data. Metrics via the metrics engine: tracking error (computed via Riskfolio-Lib's tracking-error machinery, not asserted; produced for ETFs whose index TRI series is available and marked missing otherwise), bid-ask spread/liquidity depth, premium/discount to indicative NAV as the creation-redemption proxy (free iNAV source unverified — marked unavailable until confirmed), index-methodology quality (qualitative, from provided index documentation), tax-efficiency signal.

**2.7 Risk & Compliance overlay.** Uniform across all four asset classes: VaR/CVaR, maximum drawdown, position-size and concentration limits, built on Riskfolio-Lib (26 risk measures, tracking-error and turnover constraints) and/or PyPortfolioOpt (efficient frontier, Black-Litterman, HRP). A deterministic rule engine, not an LLM agent in the debate: it runs after the verdict, has override authority over any specialist's individual verdict, and a limit breach is always surfaced even when every specialist says buy.

**2.8 Options/Volatility specialist (new).** Greeks, IV surface, decay curves via vollib's Black-Scholes/Black-76 pricing and IV inversion. Option chain from nsepython (breakage risk, hence the freshness limit and canary in §5). Needs a risk-free rate for India — source not yet confirmed (§9, open decision 6).

**2.9 Visualization/Dashboard agent.** Consumes every specialist's structured numeric output and renders: candles + moving averages + Bollinger Bands + volume and momentum/pattern-recognition flags via ta-lib-python; an options Greeks/IV/decay panel via vollib; a portfolio risk/overlap view via Riskfolio-Lib. Built as an interactive, re-queryable dashboard, not a static image. Each panel shows its dataset's as-of time and coverage label. *Implemented in Plan 1d (stocks and ETFs only):* a Streamlit page (`python -m athena.dashboard`) with a Plotly candlestick chart (50- and 200-day averages, Bollinger Bands, volume), an RSI chart, the technical-indicator table and the risk and benchmark table, each with as-of, source and coverage; data is end of day. Not built: the options panel, the portfolio risk and overlap view, mutual funds, and any live feed. Plan 1e added Valuation, Business quality and Earnings panels for stocks (not ETFs).

**2.10 Paper-trading / backtest engine.** *Decided Oct 6, 2026 (Phase 1g): a small in-house engine in `athena.backtest`, in the shape of backtrader's broker/strategy split but with no dependency* (backtrader is GPLv3 and dormant, FinRL is a heavy reinforcement-learning framework this needs none of). Long-only, one position, all in; a decision is made at a close from the bars up to that close and filled at the next open; a protective stop (2 x ATR below the entry fill) fills at the stop, or at the open when the bar gaps through it; costs are 15 bps per side (an approximation of delivery charges); cash earns nothing, while the Sharpe ratio still subtracts the overnight rate, so time in cash is penalised. The price history (jugaad-data, unadjusted) is first adjusted for splits and bonus issues detected from an overnight break below x0.6 or above x1.7, the factor snapped to a common ratio (1/2, 1/10, ...) when within 10% of it; this is not corporate-action data, smaller ratios such as a 3:2 bonus are missed, dividends are never adjusted, and the report lists every adjustment made. Rules are deterministic functions of the technical packet (`trend`, and `persona`, the Quant/Technical setup), so a run needs no language model and costs nothing. Every run is also replayed on blinded bars (ticker `ASSET`, dates shifted back 28 years so weekdays and month lengths line up, prices rescaled to 100), and the report says whether it traded identically; results are compared with buy and hold (same first open, same costs), the market (NIFTY 50) and the overnight rate (Sharpe). **Not backtested:** the language-model specialists (a model may remember how a name performed, and each replayed day costs a call) and the fundamentals (Yahoo statements are not point-in-time). Run it with `python -m athena.backtest SBIN`, or tick the backtest box on the dashboard for a stock or ETF. Live/paper execution stays a separate layer: a broker adapter for Kite Connect (Zerodha) exposed as MCP tools following the alpaca-mcp-server (OpenAPI-spec-driven tool generation, hand-crafted order-placement overrides) and ccxt-mcp (accounts referenced by name never credential, capability tiers config-file-owned, preview-then-confirm-token round-trip before any real trade, output redaction) patterns, with one flag switching paper vs. live base URL.

**2.11 Data layer (new).** Two kinds of source behind one contract. **Live adapters** (the `Adapter` protocol in §3) serve quotes, OHLCV, and option chains on demand. **Batch loaders** (the `BatchLoader` protocol in §3) ingest periodic file-based disclosures — AMFI expense ratios and portfolio holdings, index total-return series, NSE/BSE bond repository records — into a local store. The store is DuckDB over Parquet (the pattern borrowed from Jon-Becker/prediction-market-analysis, §7). Every record carries `as_of`, `source`, and the dataset's freshness class; reads return a `Record(as_of, source, payload)`. Batch refresh runs on a schedule per dataset (cadence table in §5) and stores history, so backtests and metrics can read point-in-time values. Fallback chains and the daily canary are specified in §5.

**2.12 Instrument resolver (new).** Maps raw input to the classification schema (§3). Order of operations:

1. **Normalize** the input (trim, uppercase tickers, strip exchange suffixes).
2. **ISIN handling.** Validate the checksum; use the prefix only as a hint toward type. Prefix→type rules must be verified against exchange/AMFI/NSDL data before they are used as a decision rule (§9, open decision 8).
3. **Exact match** against master lists in this order: NSE ETF list → NSE equity list → AMFI scheme master (scheme code or exact name) → bond master (by ISIN). An ETF-list hit wins over an equity-list hit. Index funds are mutual funds.
4. **Fuzzy name match** to a scored candidate list. A single candidate above the match threshold is accepted with `resolution_path: "fuzzy"`; otherwise the input is ambiguous.
5. **Ambiguity.** An optional model classifier, behind a `Classifier` interface, chooses among the candidate records only (never from open vocabulary); it routes when its top probability is at or above a threshold (proposed default 0.85). Otherwise the resolver returns the candidate list and asks the user. The model classifier may be Jev (TypeSafe's decision model, reached through OpenRouter's Decisions API — alpha, probabilities per option, no free-text output, 32k context; not to be confused with jarrodwatts/jev-trader, §7) or any small LLM. **Adoption gate:** the classifier stays off until the labeled resolver test set (§2.14) shows deterministic misroutes that it fixes. Send identifiers only, never holdings.

*Implemented in Plan 0c (equity and ETF only; funds and bonds unsupported until their masters exist).* The model classifier is a `Classifier` protocol with no implementation yet. Candidates whose symbol or name starts with the query (3+ characters) are ranked first but can never be auto-accepted; auto-accept needs an edit similarity of at least 0.90 with a 0.05 lead (on 40 prefix queries the right instrument was offered 18 times before this and 39 times after, with no wrong answers). ETF free-text name search is weak because NSE ETF names are squashed (`NIPINDETFNIFTYBEES`); tickers and ISINs work.

**2.13 Metrics engine (new).** A pure-function package, `metrics/`, with unit tests against known values. It reads from the data layer and emits a **metrics packet** (§3) per instrument. Conventions are fixed in code and documented in the spec so results are reproducible: alpha/beta by regression of net-of-fee fund returns on the category benchmark's TRI over a 3-year window; tracking error as the annualized standard deviation of active returns versus the stated index TRI; expense drag versus category-average TER; overlap as weighted ISIN intersection of holdings; style drift from returns-based style analysis on NAV. The risk-free rate for India is an open input (§9, open decision 6). The engine uses numpy/pandas and Riskfolio-Lib. Specialists receive the packet as data; none of them compute these figures. *Fundamentals implemented in Plan 1e:* `metrics/fundamentals.py` builds valuation, quality and earnings figures from stored statements; see section 6.1 for the data caveats.

*Implemented in Plan 0d (stock and ETF metrics).* Stock benchmark is the Nifty 50 price index; ETF tracking index is the Nifty 50 TRI. ETF tracking error is measured on exchange closing prices, so it includes premium/discount noise and overstates NAV-based tracking error (NIFTYBEES: about 2.5% on this basis); the packet carries a note saying so. The default window is 1 year (252 returns), not the 3 years planned for funds. Mutual-fund metrics (expense drag, alpha vs category benchmark, overlap) are not implemented.

**2.14 Evaluation harness (new).** Automated checks run in CI and on demand:

- **Schema validation** of every specialist and judge output.
- **Number-grounding validator.** Every numeric token in `reasoning` must match a value present in the input packet (with rounding tolerance); a failing output is rejected and retried or abstained.
- **Golden sets.** 20–30 hand-labeled instruments per asset class, each with an expected signal *range* (e.g. "bearish or neutral"), not an exact verdict.
- **Resolver test set.** 100–200 labeled instruments covering clean tickers, ISINs, fund-name variants (Direct/Regular, Growth/IDCW), ETF-vs-fund ambiguity, and bond ISINs.
- **Judge stability.** The ruling must not flip when the order of specialists is swapped.
- **Abstention test.** With required inputs removed, a specialist must report `partial` or `insufficient`, not a confident signal.
- **Portability test.** The specialist specs and orchestrator logic run on at least one agent runtime other than the build runtime (the runtime to use is an open decision, §9).
- **Optional dev-time grader.** Anonymized rubric review of MF/ETF outputs by several different models (llm-council idea, §7).

*Implemented in Plan 0e:* number-grounding validator, specialist-output and judge-verdict validators, abstention and order-invariance checks (callable-based, so they apply to any specialist or judge), and a resolver evaluation that scores correct / safe / wrong / missed (wrong must stay at zero). Not yet implemented: golden sets (they need the Phase 1 specialists), the portability test (needs a named second runtime), and the optional multi-model rubric grader. The resolver set is 20 hand-labeled cases plus seeded synthetic ones generated from the master lists, short of the 100-200 labeled instruments targeted above; synthetic cases test self-consistency, not the lists.

**2.15 Arbitration protocol (new).** The detail behind debate-then-judge:

- **Trigger.** A pair of specialists conflicts when their signals are opposite (bullish vs. bearish) and both confidences are at or above `conflict_min_confidence` (proposed default 60). A neutral signal never triggers a debate; it enters the conviction-weighted blend. A `data_coverage: insufficient` specialist does not participate.
- **Debate.** At most 2 rounds (`debate_rounds = 2`): each side argues, rebuts once. Capped at a maximum number of debates per request (proposed default 3). N-agent debate is the Phase 4 generalization.
- **Anonymization.** The judge sees specialists as "Analyst A/B/C", with order randomized per run. The label→specialist mapping is stored with the transcript reference. (Idea adopted from llm-council's anonymized review.)
- **Judge.** A single judge on the strongest model tier, using the judge prompt in §8 and the verdict contract in §3.
- **Risk overlay.** Applied after the verdict as a rule (§2.7). Not part of the debate.
- **Conviction.** Set by the judge, then capped by the weaker of the conflicting specialists' coverage labels: a verdict resting on `partial` data does not carry full conviction.
- **Judge panel (conditional, Phase 4b).** Only if the evaluation harness shows single-judge instability: K = 3 judges from different vendors rule independently on the anonymized transcript, each emitting the verdict contract; a chairman on the strongest model merges. Agreement across judges calibrates conviction (`panel_agreement`); a split sets `contested: true`. Multi-vendor diversity is the point, because persona specialists on one model share correlated errors. Cost is bounded because the panel runs only on flagged conflicts.

## 3. Data models & interface contracts

**Specialist agent output contract** (every specialist, every asset class, same shape — ai-hedge-fund's pattern, extended with coverage):

```json
{
  "signal": "bullish" | "bearish" | "neutral",
  "confidence": 0,
  "reasoning": "2-4 sentences, in the persona's voice, citing specific figures from the data provided",
  "data_coverage": "full" | "partial" | "insufficient",
  "missing": ["..."]
}
```

`insufficient` means the base class abstains (`signal: "neutral"`, `confidence: 0`) and the report lists `missing`. `partial` means the specialist proceeds and its reasoning must name which conclusions the gap affects.

**Judge verdict contract** (orchestrator, after debate-then-judge or blend):

```json
{
  "verdict": "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell",
  "conviction": 0,
  "key_risks": ["..."],
  "resolution_path": "debate" | "blend",
  "debate_transcript_ref": "id, if resolution_path == debate",
  "panel_agreement": 0.0,
  "contested": false
}
```

`panel_agreement` and `contested` are present only when the Phase 4b judge panel is used.

**DataAdapter / BrokerAdapter interface** (one shape, many implementations — ccxt's `Exchange` pattern):

```python
class DataAdapter(Protocol):
    def describe(self) -> dict: ...        # capability map: {"fetch_ohlcv": True, "fetch_quote": "emulated", ...}
    def fetch_quote(self, symbol: str, **params) -> Quote: ...
    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None, **params) -> list[Bar]: ...

class BrokerAdapter(DataAdapter, Protocol):   # Phase 5
    def place_order(self, symbol: str, side: str, qty: float, order_type: str, **params) -> Order: ...
```

Every `Quote` and `Bar` result carries `as_of` and `source`.

**BatchLoader interface** (file-based disclosures into the local store):

```python
class BatchLoader(Protocol):
    def describe(self) -> dict: ...   # datasets provided, cadence, expected publication lag
    def refresh(self, dataset: str, since=None) -> RefreshResult: ...   # pull source files into the store; fail loud on empty/changed schema
    def read(self, dataset: str, key: str, **params) -> Record: ...     # Record(as_of, source, payload)
```

Concrete live adapters: `NseAdapter` (jugaad-data/nsepython), `YahooAdapter`, `AmfiAdapter` (mftool, NAV), `KiteAdapter` (pykiteconnect). Concrete batch loaders: `AmfiTerLoader`, `AmfiHoldingsLoader`, `IndexTriLoader` (niftyindices.com / jugaad-data), `BondRepositoryLoader` (NSE/BSE trade repositories). Each declares its own `describe()` map so the orchestrator knows, per source, what's native, emulated, or unsupported.

**Instrument classification schema** (output of the resolver, §2.12):

```json
{
  "asset_class": "equity" | "bond" | "mutual_fund" | "etf",
  "identifier_type": "ticker" | "isin" | "scheme_code" | "name",
  "identifier": "...",
  "resolution_path": "exact" | "fuzzy" | "model" | "user_confirmed",
  "confidence": 0.0,
  "candidates": [{"asset_class": "...", "identifier": "...", "name": "..."}],
  "routed_specialists": ["..."]
}
```

`cusip` was removed from `identifier_type` — it is a US identifier and outside v1 scope.

**Routing table (initial; revisit when each specialist exists):**

| asset_class | Specialists | Notes |
| --- | --- | --- |
| equity | Valuation, Moat & Quality, Quant/Technical, Earnings Intelligence; Options/Volatility if options-eligible | + risk overlay |
| etf | ETF Analyst, Quant/Technical; Options/Volatility if options-eligible | + risk overlay; valuation desks do not apply |
| mutual_fund | Mutual Fund Analyst | + risk overlay; no Quant/Technical (NAV is daily, no volume) |
| bond | Credit Analysis, Duration/Curve Positioning | + risk overlay |

**Metrics packet** (output of the metrics engine, input to specialists):

```json
{
  "instrument": "...",
  "as_of": "...",
  "metrics": {
    "expense_ratio_drag": {"value": 0.0, "unit": "pct", "inputs": ["fund_ter", "category_avg_ter"], "window": null, "source": "AMFI TER"}
  },
  "missing": ["manager_tenure"]
}
```

## 4. Tech stack & licensing decisions

| Layer | Choice | License | Note |
| --- | --- | --- | --- |
| Core language | Python | — | Matches every adopted library below |
| LLM provider layer | Custom registry (model name → client) | — | Pattern from both ai-hedge-fund and TradingAgents; keeps the system off any one vendor |
| Orchestration engine | Custom state machine implementing debate-then-judge | — | TradingAgents' LangGraph `StateGraph` is the design reference, not an import — its state-machine logic is reimplemented vendor-neutral to avoid a hard LangGraph dependency, per the portability requirement |
| Specialist base class | Custom, modeled on ai-hedge-fund's `LLMAgent` | MIT (reference) | Persona-as-prompt pattern adopted directly |
| Local data store | DuckDB over Parquet | Open source (permissive) | Batch loader destination and point-in-time history; pattern from prediction-market-analysis |
| Instrument resolver | Custom: deterministic lookups + fuzzy match behind a `Classifier` interface | — | Optional model classifier (Jev via OpenRouter Decisions API, alpha, or any small LLM) only past the §2.12 adoption gate |
| Metrics engine | Custom pure functions on numpy/pandas + Riskfolio-Lib | — | Unit-tested against known values |
| Judge panel (conditional) | Custom, ideas from karpathy/llm-council | No license detected — ideas only, no code copied | Phase 4b, only if §2.14 shows single-judge instability |
| Technical indicators | ta-lib-python | BSD 2-Clause | Adopt as-is; native C-library dependency, prebuilt wheels cover common platforms |
| Options/Greeks/IV | vollib | Open source (permissive) | Adopt as-is for core pricing math |
| Portfolio risk & construction | Riskfolio-Lib (primary), PyPortfolioOpt (lighter-weight cross-check) | Open source (permissive) | Adopt as-is |
| Backtesting | In-house engine (`athena.backtest`), decided Oct 6, 2026 | No dependency; backtrader (GPLv3, dormant since 2023) and FinRL (heavy) were the alternatives | Reversible: the engine is one module behind `run_backtest` |
| Forecasting signal (optional) | Kronos | MIT | Adapt — validate on our asset classes before trusting as a signal (trained mostly on crypto/global exchanges) |
| Broker execution | pykiteconnect (Zerodha, official) | Official SDK | Preferred over any unofficial wrapper |
| Free market data | jugaad-data, nsepython (NSE/BSE), Yahoo Finance, mftool (AMFI NAV), AMFI TER/holdings files, niftyindices.com index data | Open source / public files | Fallback chains required — NSE-scraping libraries break on endpoint changes. Free-only for v1 |
| Broker tool exposure | Custom MCP server, patterned on alpaca-mcp-server + ccxt-mcp | — | Generic MCP (FastMCP-style), not tied to any agent host |

**Explicitly not imported as dependencies:** TauricResearch/TradingAgents (Apache 2.0 — architecture and prompts adopted, code not imported due to LangGraph coupling), karpathy/llm-council (no license detected, self-described unsupported "vibe coded" app, free-text outputs, silently drops failed models which contradicts the fail-loud rule; the anonymization, rank-aggregation, and chairman ideas are adopted, code is not), ccxt (crypto-only content, pattern only), Open-Dev-Society/OpenStock (AGPL-3.0, UI ideas only), jarrodwatts/jev-trader (wrong scope entirely), RyanCodrai/turbovec (unrelated to finance), garrytan/gbrain (unrelated to finance).

## 5. Non-functional requirements, security & compliance

**Model routing / cost.** Follow the finance guide's own tiering: bulk/high-volume extraction on a fast/cheap model, standard analysis (valuation, earnings, risk) on a mid-tier model, complex synthesis (judge rulings, IC-grade memos) on the strongest available model. The guide's own NBIM case study routed roughly 95%/4%/1% across these tiers for a 60–80% cost reduction with no quality loss on the bulk tier. Tier selection sits behind a `Router` interface: a static config is the default; a decision-model router (e.g. Jev, difficulty × specialty × risk) is an optional implementation. **Provider decision (Oct 5, 2026):** the primary LLM provider is the NVIDIA API catalog (NIM), an OpenAI-compatible chat-completions endpoint at `https://integrate.api.nvidia.com/v1` with free developer credits, chosen for cost. Third-party write-ups (not NVIDIA's own docs, so verify) report about 1,000 starting credits and a limit of about 40 requests per minute, and the free tier is meant for prototyping. Hosted models are open-weight, so strict-JSON compliance and figure grounding are not assumed: the base class's validators and retry (§2.2, §2.14) are the safeguard, and each model tier is chosen by the evaluation harness rather than by reputation. Prompts leave the machine, so keep holdings and personal details out of them unless that is acceptable. **Fallbacks (Oct 5, 2026):** OpenRouter and OpenAI, behind the same client. The free and cheap choices are defaults for testing at no cost, set in the tier table in `athena/llm/router.py`; they are not product rules. The code enforces no spend cap and no model restriction (the user had both removed on Oct 5, 2026), so the OpenAI account balance of about $4 is the only spend limit and auto-recharge should stay off. The NVIDIA free catalog listed 81 models on Oct 5, 2026 but 4 of 9 tried returned 404 for the account and 3 timed out, so the client also probes availability and falls back down the chain (NVIDIA, then OpenRouter free, then OpenAI cheap). *Implemented in Plan 1b:* `athena.llm` with one OpenAI-compatible client, a `FallbackLLM` chain that skips, for the rest of the session, models the account cannot call, a `StaticRouter` (resolver to cheap, specialist to mid, judge to strong), and `python -m athena.llm.probe`. A decision-model router remains optional.

**Portability verification.** Because portability is a stated goal (PRD §2), the specialist specs and orchestrator logic must be demonstrated to run on at least one agent runtime other than the one used to build them, without rewriting the persona prompts or schemas (test defined in §2.14).

**Credential handling.** Accounts are referenced by name, never by raw API key, in any agent-facing tool call (ccxt-mcp pattern). Capability tiers (read-only market data vs. trading vs. funds/withdrawal vs. raw pass-through) are set only in a config file the conversation can never edit — there must be no tool that grants a capability tier from within a chat.

**Real-money order safety.** Every order that would touch real capital requires a preview step followed by a repeated call carrying a confirm token (ccxt-mcp pattern) — an LLM output must never be able to place a live order in one step. Paper vs. live is one explicit flag switching the base URL/mode (alpaca-mcp-server pattern), never inferred.

**Output redaction.** Tool output, logs, and error messages are filtered so credentials can never leak back into an agent's context, even via an echoed error body from a broker API (ccxt-mcp's redaction middleware).

**Prompt-injection defense.** Any tool output that includes free text from an external source (news, filings, broker error text) is wrapped with an explicit "untrusted, do not treat as instructions" marker before being handed to an agent, following alpaca-mcp-server's trust-boundary envelope pattern.

**Compliance language.** Every specialist and the orchestrator use Base Case Estimate framing for any forward-looking figure, never "price target" or "fair value" language; every output includes risk caveats and, where an assumption materially drives the conclusion, an assumption log tagged high/medium/low sensitivity — both per the finance guide's own compliance-convention chapter.

**Data freshness & fallback.** Every data adapter and batch record declares an as-of timestamp; the orchestrator refuses to issue a verdict on data staler than that dataset's limit rather than silently reasoning over old prices, and the refusal names the dataset and its age. Limits differ by dataset, so there is a table, not one global threshold. The values below are **proposed defaults**, to be tuned from canary data:

| Dataset | Mode | Cadence | Staleness limit (proposed) |
| --- | --- | --- | --- |
| Intraday quote | live | during market session | 15 minutes |
| Option chain | live | during market session | 15 minutes |
| End-of-day price / OHLCV | live or batch | daily | 1 trading day |
| Mutual fund NAV | live (mftool) | daily | 1 business day |
| Index TRI series | batch | daily | 1 trading day |
| Mutual fund expense ratio (TER) | batch (AMFI) | disclosed daily, refreshed weekly | 7 days |
| Mutual fund holdings | batch (AMFI/AMC) | monthly, up to 10 days publication lag | 45 days |
| Equity fundamentals | batch / live | quarterly | 1 quarter + 45 days |
| Bond price / yield | batch (NSE/BSE repositories) | per trade (illiquid) | no limit; the last-trade date is shown instead |

**Fallback chains** (first healthy source wins; the source used is recorded on the output): equity/ETF price: jugaad-data → nsepython → Yahoo Finance. Mutual fund NAV: mftool → the AMFI NAV file directly (source file format to verify). Index TRI: jugaad-data → niftyindices.com download. Each adapter's `describe()` map is how the orchestrator knows what a fallback can and cannot supply.

**Adapter health canary.** A daily job fetches a small reference set per adapter (a large-cap equity, an ETF, an index fund, a government security) and checks: non-empty, schema unchanged, as-of within the dataset's limit. A failing adapter is marked degraded; the orchestrator moves down the fallback chain and labels the affected evaluation's coverage, and the failure is logged and alerted. This answers PRD §5's silent-degradation risk — NSE-scraping libraries break when endpoints change.

**Coverage honesty.** A specialist that claims `full` coverage without its declared required inputs is a defect. The base class derives the label from the required-inputs list rather than trusting the model, and the abstention test in §2.14 guards it.

## 6. Data source & broker reference

| Need | Source | Access | Notes |
| --- | --- | --- | --- |
| NSE/BSE quotes & historical | [jugaad-data](https://github.com/jugaad-py/jugaad-data), [nsepython](https://github.com/aeron7/nsepython) | Free | Actively maintained; NSE endpoints shift often — needs a fallback chain, not a single source. jugaad-data provides prices, indices (including TRI values), derivatives, and bhavcopy; its README states it does **not** provide fundamentals, corporate actions, or bonds |
| NSE option chain, ETF data, bond/debt data, results announcements | nsepython | Free | Listed in its README; depth of bond/ETF/results data is unverified |
| Global equities/ETFs, free | Yahoo Finance | Free | Rate-limited. Financial statements for `.NS` tickers are patchy (empty frames and about 4 years of history reported) |
| AMFI mutual fund NAV | [mftool](https://github.com/NayakwadiS/mftool) | Free | NAV, historical NAV, scheme list, daily performance. **No** holdings, expense ratio, or manager data |
| Mutual fund expense ratio (TER) | AMFI website TER spreadsheets | Free | Spreadsheet download, not an API — needs a batch loader |
| Mutual fund holdings | AMFI / AMC portfolio disclosure files | Free | Monthly, with ISINs, within 10 days of month end; file formats vary by AMC — needs a batch loader |
| Index total-return series | niftyindices.com historical data; jugaad-data | Free | Needed for ETF tracking error and fund benchmark alpha |
| Indian bond data | NSE/BSE corporate bond trade repositories | Free | Issue date, rating, coupon frequency; no API found; no covenant or indenture data |
| Macro/rates | FRED | Free | Standard for US series. India coverage is unverified (likely limited and lagged); an Indian risk-free rate source is an open decision |
| Zerodha execution | [pykiteconnect](https://github.com/zerodha/pykiteconnect) | Paid/brokerage account | Official SDK, MIT license. Plans (checked Oct 2026): the free Personal plan covers orders, GTT and portfolio only; live WebSocket data and historical candles need the paid Connect plan at Rs 500 per month per API key, which is the one place the free-data-only rule is under review. |
| US equities/options paper broker (reference architecture) | [alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) | Free paper / funded live | Not usable for Indian markets directly; its MCP tool-generation pattern is the template to copy |
| Options Greeks/IV | [vollib](https://github.com/vollib/vollib) | Free (library) | Core pricing/IV math |
| Portfolio risk/construction | [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib), [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt) | Free (library) | Riskfolio-Lib is the broader of the two |
| Backtesting | [backtrader](https://github.com/mementum/backtrader) or [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Free (library) | See §4 and §9 for the license/maintenance decision |
| Forecasting signal | [Kronos](https://github.com/shiyu-coder/Kronos) | Free (Hugging Face weights) | Validate before trusting on non-crypto assets |
| Technical indicators | [ta-lib-python](https://github.com/TA-Lib/ta-lib-python) | Free (library) | Requires native C-library dependency (prebuilt wheels available) |

### 6.1 Coverage matrix (free-only, v1)

What each specialist needs, what a free source provides, and how v1 behaves. Status: **Covered** = a free source supplies it; **Partial** = supplied unreliably or incompletely; **Gap** = no free source found; **Unverified** = a candidate exists but has not been checked.

| Specialist / metric | Needs | Free source | Status | v1 treatment |
| --- | --- | --- | --- | --- |
| Equity Valuation, Moat, Earnings | Multi-year financial statements, earnings calendar and surprises, index valuation | Yahoo `.NS` statements (4 annual periods, 5-6 quarters; banks lack operating and working-capital lines; duplicated or missing quarters occur), Yahoo `earnings_dates` (24 reports with estimate and surprise), NSE index P/E, P/B and yield via jugaad-data `index_pe_raw` (back to 2015) | Covered, with caveats (verified 5 Oct 2026) | Ratio-only valuation and quality/earnings figures implemented in Plan 1e; lenders get an explicit "not meaningful" list; **not point-in-time, so not backtestable**; no DCF, comparables or net-net |
| Equity Quant/Technical | OHLCV | jugaad-data, nsepython, Yahoo | Covered | Full |
| Stock and ETF backtest | OHLCV (8 years), NIFTY 50 close, Nifty 1D Rate Index | jugaad-data (equity series only), Yahoo, NSE index history | Covered (verified Oct 6, 2026 on SBIN and NIFTYBEES) | The two technical rules only; the language-model specialists and the fundamentals are not backtested |
| Credit Analysis | Price, yield, rating, issue terms | NSE/BSE trade repositories; nsepython bond data (depth unverified) | Partial | Government securities and listed corporates only |
| Covenants / recovery waterfall | Offer documents | Exchange-filed PDFs (extraction not scoped) | Gap | Produced only when data obtained; otherwise listed as missing |
| Duration / curve positioning | Yield curve | Government-security yields — source unverified (candidates: RBI, CCIL) | Unverified | Treat bonds as price series until a source is confirmed |
| MF NAV / performance | NAV history | mftool | Covered | Full |
| MF expense ratio | TER | AMFI TER spreadsheets | Covered (batch) | Weekly refresh |
| MF holdings / overlap | Holdings with ISIN | AMFI / AMC monthly disclosure | Covered (batch) | Monthly granularity, stated in the output |
| MF manager tenure | Manager history | None found | Gap | Optional; listed as missing |
| MF category benchmark | Category → benchmark map | Static table maintained in this project | Covered by us | Maintain and test the table |
| ETF tracking error | ETF NAV/price + index TRI | niftyindices.com; jugaad-data | Covered for NSE-index ETFs | Marked missing for ETFs without a TRI series |
| ETF liquidity | Quotes, depth, volume | NSE live quotes | Partial | Live-adapter dependent |
| ETF creation-redemption health | Indicative NAV, authorized-participant data | Free iNAV source unverified | Unverified | Premium/discount to iNAV proxy if a source is confirmed, else unavailable |
| ETF holdings | Constituents | Source unverified | Unverified | Verify before Phase 3 |
| Options / volatility | Option chain | nsepython (breakage risk) | Partial | 15-minute staleness limit, canary |
| Risk-free rate (alpha, Sharpe, pricing) | India T-bill / repo rate | None confirmed; FRED India coverage unverified | Open | Open decision 6 |

### 6.2 Source verification log (5 Oct 2026)

Checked against the live sources; re-checked by `pytest --live`.

| Source | Result |
| --- | --- |
| NSE holiday list (`NSELive().holiday_list()`) | Works; segment `CM`; current year only; 2 Oct 2026 is a holiday |
| NSE `EQUITY_L.csv`, `eq_etfseclist.csv` | Work with a browser `User-Agent`; ETF list gives each ETF's underlying index |
| jugaad-data `stock_df` | Works; dates are IST midnight stored as naive UTC (18:30 the previous day); unadjusted prices. Returns every NSE series for the symbol despite `series="EQ"` (SBIN: EQ 1948 rows plus bond series N2/N5/N6/T0 near 10,000 and BL block deals, 612 duplicated days over 8 years); the adapter keeps one equity-series row per day, preferring EQ |
| jugaad-data `index_tri_raw(name, index_name, from, to)` | Works; returns TRI and NTR |
| Yahoo `.NS` history | Works but invents flat zero-volume rows on market holidays; use `auto_adjust=False`; statements returned 4 years for SBIN |
| AMFI `NAVAll.txt` | Works; scheme code, ISINs, NAV, date in one file (mutual funds on hold) |
| NSE live quotes and option chain (jugaad-data, nsepython) | **Broken** (KeyError / empty); intraday quote and option-chain datasets unavailable for now |
| `nsepython.index_total_returns` | **Broken** (endpoint returns HTML); use jugaad-data |
| AMFI TER | Page builds its table in the browser; no direct file found (mutual funds on hold) |
| AMFI portfolio holdings | About 45 separate fund-house sites with differing formats (mutual funds on hold) |
| niftyindices `NIFTY 1D RATE INDEX` via jugaad-data `index_raw` | Works; daily overnight-rate accrual index; ratio between two dates is the risk-free return (5.25% annualised over the last year) |
| niftyindices `NIFTY 50` price index via jugaad-data `index_raw` | Works; stock benchmark |
| FRED India short-rate series (`IRSTCB01INM156N`, `INTDSRINM193N`) | Stale (end 2023 / 2022); only `INDIRLTLT01STM` (10-year yield) is recent, with ~3-month lag; FRED resets plain Python `requests` connections (curl works) |
| yfinance statements (`financials`, `balance_sheet`, `cashflow`, `quarterly_financials`) | Work for 15 of 16 NSE symbols tried; `ZOMATO` is now `ETERNAL`; ITC shows a duplicated quarter, SBIN a missing one; capex is negative; `returnOnEquity` and `dividendYield` are unreliable |
| yfinance `earnings_dates` | Works; next report plus EPS estimate, reported and surprise percent for 24 reports; US Eastern timestamps |
| jugaad-data `NSEIndexHistory.index_pe_raw` | Works; daily P/E, P/B and dividend yield for any NSE index, back to 2015 |

## 7. Full repository reuse audit

Every repo below was cloned and its actual code inspected (not just its README), so these verdicts can be trusted as engineering assessments, not summaries of marketing copy. (The Oct 3, 2026 additions — llm-council, Jev — were assessed from the repository's source file and published documentation, not a full clone.)

**User-supplied repositories:**

| Repo | Verdict | Why |
| --- | --- | --- |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) (MIT) | **Adopt/Adapt** | `LLMAgent` base class + persona-as-prompt files (Buffett/Munger/Graham/Lynch/Druckenmiller) map directly onto our specialist design; conviction-weighted `blend_signals`; multi-vendor LLM registry; backtest "blinding." Equities-only, no bonds/funds/ETFs; paper broker is roadmapped, not built |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) (Apache 2.0) | **Adopt (orchestration)** | Real LangGraph `StateGraph`: analysts → bull/bear debate → Research Manager judge → trader → aggressive/conservative/neutral risk debate → Portfolio Manager judge. This is the exact conflict-resolution pattern our project needed and the finance guide never specified. No execution/paper-trading layer at all — pair with ai-hedge-fund's broker layer |
| [jarrodwatts/jev-trader](https://github.com/jarrodwatts/jev-trader) (MIT) | **Skip** | Single-agent crypto market-making demo bot on Monad/Kuru, vendor-locked to a proprietary model, explicitly "no backtests, no historical analytics" by its own spec. Wrong scope entirely. Unrelated to TypeSafe's Jev decision model despite the shared name |
| [alpacahq/alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) (MIT) | **Adapt (pattern)** | Generic MCP (FastMCP), not Claude-specific; generates tools from Alpaca's OpenAPI spec, hand-writes order-placement overrides, wraps every output in a trust-boundary envelope. Hard-locked to Alpaca's API/account model — template only, not usable for Indian brokers |
| [mementum/backtrader](https://github.com/mementum/backtrader) (GPLv3) | **Adapt (with caveats)** | `Cerebro`/`BackBroker`/pluggable pandas data feeds/`SignalStrategy` fits our need well; no commits since 2023, GPLv3 copyleft — fork-with-legal-review or switch to FinRL |
| [ccxt/ccxt](https://github.com/ccxt/ccxt) (MIT) | **Reference only** | `Exchange` base class + `describe()`/`has{}` capability map is the strongest adapter-design template found; its own `ccxt-mcp` server (name-based accounts, config-owned capability tiers, preview/confirm-token writes, redaction) is the strongest broker-safety template found. Crypto content itself unusable |
| [TA-Lib/ta-lib-python](https://github.com/TA-Lib/ta-lib-python) (BSD-2) | **Adopt** | Real C-library wrapper, not a reimplementation; Function/Abstract/Streaming APIs cover every indicator category we listed |
| [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) (MIT) | **Adapt (validate first)** | Genuine pretrained candlestick foundation model (AAAI 2026), simple `KronosPredictor.predict()` API; trained mostly on crypto/global exchanges — validate before trusting on our asset classes; its own docs disclaim it's not a production trading system |
| [RyanCodrai/turbovec](https://github.com/RyanCodrai/turbovec) (MIT) | **Skip** | Rust ANN vector index for RAG, unrelated to finance despite the name; revisit only for a future large-scale "similar historical setups" embedding-search feature |
| [Jon-Becker/prediction-market-analysis](https://github.com/Jon-Becker/prediction-market-analysis) (MIT) | **Reference only** | Polymarket/Kalshi research framework; wrong asset class, but its forecast-calibration methodology and `Analysis`-base-class + DuckDB-over-Parquet pattern are worth borrowing for our own benchmarking layer and (Oct 3, 2026) for the local data store |
| [Open-Dev-Society/OpenStock](https://github.com/Open-Dev-Society/OpenStock) (AGPL-3.0) | **Reference only** | Next.js watchlist/dashboard UI, all "analysis" is proxied Finnhub data with no scoring or LLM reasoning; own docs admit weak India support, no options, no funds/ETFs, no backtesting |
| [garrytan/gbrain](https://github.com/garrytan/gbrain) (MIT) | **Reference only** | Not finance-related — a personal knowledge/memory system; its fact-provenance model, grants/permission scoping, and subagent-vs-durable-job routing convention are generically useful orchestration ideas, not finance content |
| [karpathy/llm-council](https://github.com/karpathy/llm-council) (no license detected) | **Reference only** (added Oct 3, 2026) | Three stages: parallel first opinions → anonymized peer ranking (letter labels, `FINAL RANKING:` regex parse, mean-rank aggregation) → chairman synthesis, over OpenRouter, FastAPI + React, JSON-file storage. Self-described "99% vibe coded" and unsupported; free-text outputs; silently drops failed models. No license, so no code is copied. Adopted ideas: anonymized relabeling before judging, rank aggregation as a disagreement signal, chairman-as-judge, and — for the conditional Phase 4b panel — model-vendor diversity |

**Optional components (not repositories):** Jev (TypeSafe's decision model; `typesafe/jev-1.13` on OpenRouter's alpha Decisions API; returns probabilities per typed option and no text; 32k context; billed per input token). Role in this project: optional model classifier for ambiguous instrument names (§2.12) and an optional implementation of the model `Router` (§5), both behind interfaces and gated on evaluation. It cannot act as a specialist or judge because it produces no reasoning.

**Found via further GitHub search:**

| Repo | Fills |
| --- | --- |
| [zerodha/pykiteconnect](https://github.com/zerodha/pykiteconnect) | Official Zerodha execution SDK |
| [jugaad-data](https://github.com/jugaad-py/jugaad-data), [nsepython](https://github.com/aeron7/nsepython) | Free NSE/BSE data |
| [mftool](https://github.com/NayakwadiS/mftool) | AMFI mutual fund NAV data |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Actively-maintained backtest/simulation alternative to backtrader |
| [vollib/vollib](https://github.com/vollib/vollib) | Option Greeks & IV pricing |
| [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt), [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | Portfolio construction, risk, tracking-error math |
| [asrajavel/mf-analysis](https://github.com/asrajavel/mf-analysis), [basav22/portfolio-overlap-java](https://github.com/basav22/portfolio-overlap-java) | Early-stage mutual-fund analysis/overlap references only — not production-ready |

**Confirmed white space:** no mature, actively-maintained open-source project does real mutual-fund expense-ratio/performance-attribution analysis or ETF tracking-error/index-methodology analysis end to end. This is uncontested territory the project must build originally (§2.5, §2.6).

## 8. Exemplar agent prompt templates

These show the pattern every specialist and judge prompt should follow — short, persona-only, with a locked output contract. New specialists (Mutual Fund Analyst, ETF Analyst, Options/Volatility) should be written in this same shape. The output contract shown in each exemplar is the abbreviated form; the base class (§2.2) adds `data_coverage` and `missing` as the full contract in §3 requires, and supplies the metrics packet as part of the data.

**Equity Valuation specialist** (adapted from ai-hedge-fund's Buffett persona and the finance guide's owner-earnings/moat framework):

```
You are a senior equity analyst applying a long-term business-owner lens, not a trader's.
Work the checklist: circle of competence; durable competitive advantage (moat source: intangible
assets, switching costs, network effect, or cost advantage); owner earnings vs. reported earnings;
balance-sheet strength; management quality and capital allocation; would you hold this ten years?
Reason ONLY from the data provided. Treat the most recent filing date shown as the present day;
do not use knowledge of anything after it.
Output strictly as: {"signal": "bullish"|"bearish"|"neutral", "confidence": 0-100,
"reasoning": "2-4 sentences citing specific figures from the data"}
```

**Mutual Fund Analyst** (new, no OSS precedent — written for this project):

```
You are a fund analyst evaluating a mutual fund on cost and skill, not narrative.
Interpret the figures given in the metrics packet; do not calculate your own: expense-ratio drag
versus category average; alpha and beta measured net of fees against the stated category benchmark;
manager tenure (if provided) and any style drift versus the fund's stated mandate; holdings overlap
against the positions the user already holds.
A fund that merely tracks its category at a high fee is not a buy regardless of past returns.
Reason ONLY from the NAV history, factsheet, metrics packet, and benchmark data provided. If a
required figure is listed as missing, say so and qualify your conclusion.
Output strictly as: {"signal": "bullish"|"bearish"|"neutral", "confidence": 0-100,
"reasoning": "2-4 sentences citing the specific expense ratio, alpha/beta, and overlap figures"}
```

**Judge / Portfolio Manager agent** (adapted near-verbatim from TradingAgents' Research Manager and Portfolio Manager prompts):

```
You judge a debate between two or more specialist agents who disagree on the same instrument.
The debate always contains conflicting arguments; deciding which side is stronger is the job, so
conflict alone is not a reason to default to Hold. Commit to the side with the stronger case, sized
by how decisively it wins. Choose Hold only when the evidence is still genuinely balanced after
weighing it, or too thin to support a call — never to appear neutral by default. Weigh each side on
its merits, independent of which spoke first or last. Ground every conclusion in specific evidence
from the transcript, not in the specialists' confidence scores alone.
Output strictly as: {"verdict": "Buy"|"Overweight"|"Hold"|"Underweight"|"Sell", "conviction": 0-100,
"key_risks": ["..."], "resolution_path": "debate"}
```

Every persona file additionally opens with a disclaimer, following ai-hedge-fund's legal-safety pattern: *"A stylized analytical framework, not financial advice; not a registered investment adviser."*

## 9. Build roadmap & open technical decisions

**Roadmap** (mirrors PRD §3, with technical detail):

0. **Foundation.** Local data store (§2.11) and batch loaders (`AmfiTerLoader`, `AmfiHoldingsLoader`, `IndexTriLoader`, `BondRepositoryLoader`); live adapters with `describe()` and as-of; staleness table, fallback chains, and daily canary (§5); deterministic instrument resolver and routing table (§2.12, §3); metrics package skeleton with the first tested functions (§2.13); evaluation harness skeleton with schema and number-grounding checks (§2.14).
1. **Stocks.** Orchestrator skeleton (classification + simple blend, debate stubbed) + equity specialists (§2.3, with ratio-only mode) + `NseAdapter`/`YahooAdapter` + ta-lib-python-backed dashboard + a stock backtest (in-house engine, Phase 1g).
2. **Debt/bonds.** Credit/duration specialists (§2.4) on government securities and listed corporate bonds; bonds still backtest as generic price series.
3. **Mutual funds & ETFs.** Build §2.5/§2.6 from scratch against `AmfiAdapter`, the AMFI batch loaders, `IndexTriLoader`, the metrics engine, and Riskfolio-Lib.
4. **Full arbitration.** Implement the debate-then-judge state machine (§1, §2.1, §2.15) — 2-round debate, anonymized labels, single judge; add the Options/Volatility specialist (§2.8) and its dashboard panel. Phase 4b judge panel only if §2.14 shows single-judge instability.
5. **Live/paper execution.** `KiteAdapter` behind the MCP tool-exposure layer (§2.10), paper/live toggle, preview/confirm-token safety (§5).

**Open technical decisions, in priority order:**

1. ~~**backtrader vs. FinRL** for the backtest/paper-trading core~~ — *resolved Oct 6, 2026:* an in-house engine for the stock backtest (§2.10). Revisit only if the paper-trading layer needs a fuller broker simulator.
2. **LangGraph dependency.** TradingAgents' orchestration is the design template, but its `StateGraph` primitives are LangGraph-specific; decide whether to take a LangGraph dependency for speed or reimplement the state machine vendor-neutral for the portability goal (PRD §2) — this TRD assumes reimplementation.
3. **Kronos validation plan.** Define a concrete backtest comparing Kronos-derived forecasts against a naive baseline on Indian equities/bonds/fund NAVs before it's allowed to influence any specialist's verdict.
4. **Fixed-income mechanics.** Decide how far to go modeling real bond mechanics (coupons, day-count, duration) versus treating bonds as generic price series indefinitely.
5. **Single-user vs. broader audience.** Any move beyond personal use raises the regulatory question flagged in PRD §5 and should be revisited before it happens, not after.
6. ~~**India risk-free rate source**~~ **Resolved 5 Oct 2026:** the Nifty 1D Rate Index (an overnight-rate accrual index from niftyindices) supplies per-period risk-free returns as level ratios. It is an overnight proxy, slightly below a 91-day T-bill; no free T-bill series was found.
7. **Second agent runtime** for the portability test (§2.14). Name it before Phase 1 ends.
8. **Verify-before-spec items** (found unverified in the Oct 3, 2026 review): ISIN prefix→type rules; nsepython bond, ETF, and results data depth; free iNAV source; free ETF holdings source; free government-security yield source; the AMFI NAV file as an mftool fallback; AMC holdings file formats. *Partly resolved 5 Oct 2026 (see §6.2): jugaad-data TRI, NSE equity/ETF lists and the AMFI NAV file are confirmed; ETF holdings, iNAV, G-sec yields, ISIN prefix rules, AMFI TER capture and AMC holdings formats remain open.*
9. **Model classifier adoption.** Decide whether Jev (or any model classifier) is enabled in the resolver, based on the labeled resolver test set; default is off.
10. **Judge panel trigger.** Define the instability tolerance in §2.14 (for example, the fraction of order-swap flips) that switches on the Phase 4b panel.

## Revision history

- **Sep 26, 2026** — Initial TRD. (Original preserved at `docs/archive/TRD-2026-09-26.md`.)
- **Oct 5, 2026 (Phase 0e)** — Evaluation harness implemented; resolver prefix ranking fixed (see `docs/superpowers/plans/2026-10-05-phase-0e-evaluation-harness.md`).
- **Oct 5, 2026 (broker decision)** — Zerodha (Kite Connect) is the only broker; Groww and Upstox dropped from scope.
- **Oct 5, 2026 (LLM provider decision)** — NVIDIA API catalog (NIM) is the primary LLM provider, with OpenRouter (free models only) and OpenAI (cheap models only, hard spend cap) as fallbacks; Anthropic and Google not used.
- **Oct 5, 2026 (Phase 1a)** — Specialist base class and Quant/Technical specialist implemented (see docs/superpowers/plans/2026-10-05-phase-1a-specialist-base-and-quant-technical.md).
- **Oct 5, 2026 (Phase 1b)** — LLM provider layer implemented with enforced spending limits (see docs/superpowers/plans/2026-10-05-phase-1b-llm-provider-layer.md).
- **Oct 5, 2026 (spend cap removed)** — At the user's request the OpenAI spend guard, the OpenAI cheap-model allowlist and the OpenRouter free-only rule were removed from the code; free and cheap models are test defaults only.
- **Oct 5, 2026 (Phase 0d)** — Metrics engine implemented for stocks and ETFs; open decision 6 (risk-free rate) resolved (see `docs/superpowers/plans/2026-10-05-phase-0d-metrics-engine.md`).
- **Oct 5, 2026 (Phase 0c)** — Instrument resolver implemented for equity and ETF (see `docs/superpowers/plans/2026-10-05-phase-0c-instrument-resolver.md`).
- **Oct 5, 2026 (Phase 0b)** — Added §6.2 source verification log; holiday-aware freshness; equity/ETF data layer implemented (see `docs/superpowers/plans/2026-10-05-phase-0b-equity-etf-data.md`).
- **Oct 5, 2026 (code)** — Adapter protocol split into DataAdapter and BrokerAdapter to match `src/athena/contracts.py` (Phase 0a implemented: contracts, freshness, store, fallback, coverage, canary).
- **Oct 5, 2026 (Phase 1c)** — Orchestrator skeleton and command line implemented (see docs/superpowers/plans/2026-10-05-phase-1c-orchestrator-skeleton.md).
- **Oct 5, 2026 (Phase 1d)** — Stocks and ETF dashboard implemented (see docs/superpowers/plans/2026-10-05-phase-1d-dashboard.md).
- **Oct 5, 2026 (Phase 1e)** — Fundamentals data and metrics implemented and shown on the dashboard (see docs/superpowers/plans/2026-10-05-phase-1e-fundamentals-data-and-metrics.md).
- **Oct 6, 2026 (Phase 1g)** — Stock and ETF backtest implemented as an in-house engine; backtrader vs. FinRL resolved (see docs/superpowers/plans/2026-10-06-phase-1g-stock-backtest.md). First results on 8 years of SBIN: the trend rule returned +9% against +206% for buy and hold (Sharpe -0.16 against 0.57), and the persona rule was in the market on 1.6% of days with three trades, because "all trends up" puts price near its 60-day high while "reward:risk of at least 2" needs room back to that high. Neither rule beats buy and hold on SBIN or NIFTYBEES; the numbers are illustrative, not a finding about the market.
- **Oct 6, 2026 (price series fix)** — The jugaad price adapter now keeps only equity-series rows (EQ preferred; BE, BZ, SM, ST as fallback) and drops bond and block-deal rows that jugaad returns for the same symbol. Found while preparing the stock backtest: long histories carried repeated days and prices near 10,000; the recent 760-day window was affected only in the 4th decimal of RSI and ATR.
- **Oct 6, 2026 (Phase 1f)** — Valuation, Moat & Quality and Earnings Intelligence specialists implemented and routed (see docs/superpowers/plans/2026-10-06-phase-1f-fundamentals-specialists.md).
- **Oct 3, 2026** — Gap closure after review against the PRD, a check of free data sources, and a review of karpathy/llm-council and Jev. Added: free-only data decision and coverage matrix (§6.1); data layer with batch loaders and local store (§2.11); instrument resolver and routing table (§2.12, §3); metrics engine (§2.13); evaluation harness (§2.14); arbitration protocol detail (§2.15); `data_coverage` in the specialist contract; per-dataset staleness table, fallback chains, and adapter canary (§5); Phase 0 in the roadmap; five new open decisions (6–10); llm-council and Jev entries in §4 and §7; refreshed data source table (§6) with confirmed and unverified sources.
