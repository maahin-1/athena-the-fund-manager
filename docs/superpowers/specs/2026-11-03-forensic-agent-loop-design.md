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
