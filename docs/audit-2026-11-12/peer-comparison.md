# Athena compared with four public projects

Reviewed 12 November 2026. This is an architecture and source comparison, not a performance contest. Public repositories were read through GitHub; none was installed or run, no provider credentials were used, and no environment files were opened. Stars, screenshots and paper returns were not used as readiness evidence.

The local November 9 Tauric comparison was useful context, but this review fetched current owning sources independently. That older document has drift: Athena already has Black-76 options calculations and a main stock-rating pipeline, while the document sometimes describes only its newer forensic concern workflow. Compare specific routes, not repository slogans.

## Comparison at a glance

| Peer | Most useful comparison | Where its approach is stronger | Athena's advantage for this user's purpose | What this does not establish |
|---|---|---|---|---|
| TradingAgents | Multi-agent stock reasoning | Explicit graph, parallel isolated analysts, bounded tool turns, later-entry outcome scoring | Direct NSE filings, a local market-data terminal, deterministic accounting/document checks | Debate accuracy or superior investment results |
| Trading-R1 | A finance research harness | Persistent evidence objects, reference-based tool inputs, bounded research/wrap-up stages, broader compute tools | Specialised NSE/DRHP workflows and code-owned accounting invariants | That model-written verification makes its conclusions true |
| ai-hedge-fund v2 | A simulated/paper investment process | One session engine across historical and paper modes, separate decision/fill times, reconciled cash/positions, richer cost model | India-specific data acquisition and an integrated chart/options/research desk | That its v2 work is fully integrated with its older shipped app or is profitable |
| Microsoft Qlib | Quantitative research infrastructure | Experiment records, modular modeling, richer exchange simulation and portfolio workflows | A focused local analyst interface and filing-based explanations without requiring a training stack | That adding ML to Athena would improve its results |

## 1. TradingAgents: learn execution structure and timing, not the number of debaters

Inspected tree: `9fa1196fffbcfd9de5146d2dd2869df84ab99838`. Its setup code starts analyst subgraphs together, gives each a private message history, waits for all reports and then runs research/trader/risk stages. Tool rounds are capped and a final report turn is forced. This provides a clearer runtime dependency graph than Athena's ordinary sequential specialist loop. [Source: graph setup](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/graph/setup.py).

More consequential than the framework choice: its settlement enters after the analysis date and waits for the full holding window. Its backtest explicitly evaluates independent decisions, rather than pretending to maintain a portfolio. Athena should adopt equally explicit timing and naming, especially because its stored-verdict settlement currently begins at the input-data date. [Settlement](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/memory/settlement.py), [backtest scope](https://github.com/TauricResearch/TradingAgents/blob/9fa1196fffbcfd9de5146d2dd2869df84ab99838/tradingagents/backtest.py).

Athena's deterministic calculations and direct NSE filing/document checks are better suited to an Indian analyst's evidence workflow than copying a general vendor-tool debate. Its shadow council is also a useful restraint: persuasive debate does not automatically override the simpler baseline. This is a design advantage for auditability, not demonstrated superior predictive quality.

**Adopt selectively:** bounded parallelism for independent readers, isolated histories, mandatory wrap-up, and explicit executable scoring dates. **Avoid copying wholesale:** extra trader and risk personas without evidence of incremental accuracy. LangGraph would express orchestration; it would not fix a missing publication-date filter or semantic grounding bug.

## 2. Trading-R1: the most relevant evidence-handling ideas

Inspected tree: `b50c941c5833e6c46f36ee1b2277513725ee5367`. Its evidence store deep-copies tool payloads, records provenance and input relationships, and supports lineage traversal. Tool arguments can reference earlier results by ID/path; the runtime resolves these before schema validation. That avoids asking the model to copy a long series or retype a number into a calculation. [Evidence store](https://github.com/TauricResearch/Trading-R1/blob/b50c941c5833e6c46f36ee1b2277513725ee5367/trading_r1/runtime/evidence.py), [reference resolution](https://github.com/TauricResearch/Trading-R1/blob/b50c941c5833e6c46f36ee1b2277513725ee5367/trading_r1/runtime/chaining.py).

The loop separates model/tool stages, has round and wrap-up limits, emits progress events and makes compaction visible. These are concrete improvements over an investigation where complete evidence is stored but only a truncated slice is shown and there is no reliable way to request the omitted fields. [Research loop](https://github.com/TauricResearch/Trading-R1/blob/b50c941c5833e6c46f36ee1b2277513725ee5367/trading_r1/runtime/loop.py).

Athena already has evidence IDs, an action allowlist and stronger task-specific code-owned invariants for its document checks. Preserve them. Different IDs do not prove source independence, however, and neither project should treat an additional model review as a mathematical truth check.

**Adopt selectively:** source-document lineage, result references in tool arguments, resumable case state, field/page retrieval after truncation and explicit workflow instructions for each lane. **Defer:** expanding into every finance domain. A larger tool catalog adds integration and validation work; it is worthwhile only for an actual analyst task. Native tool calling versus JSON actions is a provider-compatibility tradeoff, not an intrinsic correctness ranking.

## 3. ai-hedge-fund v2: the strongest process lesson is shared historical/paper accounting

Inspected tree: `5c553b5222044f6a5527bd377b4104c50ee0ad28`. The v2 README calls the rebuild work in progress and says it is not yet wired into the older app. Its capabilities should therefore not be attributed indiscriminately to every installation. [v2 scope](https://github.com/virattt/ai-hedge-fund/blob/5c553b5222044f6a5527bd377b4104c50ee0ad28/hedge_fund/README.md).

Its session engine first reconciles the broker book, executes a previously pending decision, marks positions and then makes the next decision. Records include cash, positions, decision/execution separation, code/mandate information and a chained hash. Backtesting and paper mode share this engine. Athena's factor studies, daily strategy engine, verdict settlement and paper desk are separate paths; each can be locally reasonable while disagreeing about timing or accounting. The peer's shared transition design is the better starting point if Athena's objective becomes portfolio operation. [Session engine](https://github.com/virattt/ai-hedge-fund/blob/5c553b5222044f6a5527bd377b4104c50ee0ad28/hedge_fund/pipeline/session.py).

Its cost model separates spread, commission, participation-dependent impact and a small-trade band. This is more informative than one fixed basis-point haircut when asking whether a strategy survives scaling. Its defaults are US-oriented and require replacement/calibration for India. A missing volume estimate can still mean no modeled impact; implementation is not validation. [Cost model](https://github.com/virattt/ai-hedge-fund/blob/5c553b5222044f6a5527bd377b4104c50ee0ad28/hedge_fund/brokers/costs.py).

Athena is more directly useful as an NSE research workstation: exchange statements, symbol succession, XBRL layouts, local charting and derivatives are already connected. **Adopt:** shared session accounting and auditable decision-to-fill chronology. **Do not substitute:** investor-persona count for domain coverage or proven signal quality.

## 4. Qlib: compare experiment discipline and exchange realism

Qlib is a quant research platform rather than a ready-made replacement analyst terminal. Its exchange abstraction supports separate buy/sell prices, suspension conventions, price limits, volume participation, trading units and costs. Athena's daily-bar engine deliberately supports a much smaller model. Qlib offers better concepts for asking whether a selected strategy could actually trade the intended size; its market conventions cannot be imported unchanged into NSE simulations. [Exchange source](https://github.com/microsoft/qlib/blob/main/qlib/backtest/exchange.py), inspected file blob `69262fcbbadd79f5e252af7315f3a76b49e77dad`.

Its experiment recorder has run status, parameters, metrics and artifacts. Athena already saves results, but its model/prompt/data provenance is not a uniform run contract across every route. Borrow the contract before adding a tracking service: a small immutable run manifest and artifact directory may meet a single user's needs. [Recorder source](https://github.com/microsoft/qlib/blob/main/qlib/workflow/recorder.py), inspected blob `de023de842373df00671ae257b97a602df10cbff`.

Qlib's training/model ecosystem is broader, but Athena currently needs reliable labels, timestamped data and a defensible evaluation design before more sophisticated learners. More rows do not automatically create independent examples: many stocks in the same month share the same market shock. [Project scope](https://github.com/microsoft/qlib).

**Adopt selectively:** experiment manifests, a narrow simulated-exchange contract and explicit liquidity/suspension handling. **Avoid a full migration now:** it would create substantial data-conversion and integration work without correcting the existing source-vintage and claim-support defects.

## Independent recommendations after the comparison

1. Choose a primary product: an auditable Indian research desk. Treat portfolio execution as a separate maturity track with stricter operational acceptance gates.
2. Test whether one evidence-constrained reader plus deterministic checks beats the current multi-persona pipeline on supported claims, material omissions, time and cost. A simpler system winning is a useful research result.
3. Make “what was known when” one shared service contract. Keep reporting period, publication time, observation time, ingestion time, model decision time and execution time distinct.
4. Measure analyst work directly: time to locate source evidence, verify a report, detect a planted discrepancy and complete a repeatable case. This establishes usefulness without requiring an investment-return claim.
5. Add a public, credential-free demo bundle with known source data, deterministic outputs, build identity and visible fake/stored labels. A live external dependency should enhance the demo rather than determine whether it works.

No peer was independently certified for real-market use here. These comparisons identify concrete patterns worth borrowing, not a claim that another repository has solved the reliability or alpha problem.
