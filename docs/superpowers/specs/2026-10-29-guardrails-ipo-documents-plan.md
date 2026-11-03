# Guard rails, a search bar for Sentiment, an IPO tab, and reading long documents cheaply: plan

Status: DRAFT for the owner's approval. Nothing here is built. Written 2026-10-29. Facts below were checked on that day against the code and the live endpoints; what is not known is marked.

## 0. What the code does today (the honest state)

1. **There is no free-text agent.** Nobody can type a question to a model. Text typed by a person reaches only: the command line (`command.ts`, fixed patterns, anything else is "not a symbol"), the search boxes (`/api/search`, an in-memory instrument match, no model), and the instrument resolver (`athena.resolver`, which can ask a cheap model to classify a name, command line only). The models are reached by buttons: verdict job, Explain this chart, Read the tone. So the guard rails the owner asks for protect a door that is not built yet: they are the first requirement of any "Ask" feature, and a cheap safeguard on the resolver today.
2. **Specialists never read documents.** Each reads a small packet computed in code (a few hundred figures with units); a number it cites that is not in the packet is rejected (`evaluation.grounding`). The news analyst reads headlines and short excerpts, not articles.
3. **Annual reports** are read only for the three statement pages: code finds the pages (`pdf_extract.find_statements`, at most 3 pages), a model copies the figures, code converts units and checks accounting identities and the overlap year against the exchange's filings. Cost this month: about 4,000 prompt tokens a call.
4. **A document-search layer exists and is not used.** `retrieval.index` (180-word chunks, BM25 plus optional embeddings, a point-in-time rule) and `retrieval.review` (eight concern topics, four passages each, a model must quote and code checks the quote is in the passage) are written and tested, but nothing feeds them text. Concall transcripts and annual reports are stored as titles and links only (`fundamentals.documents`). **So earnings calls and the narrative of annual reports are not analysed at all today.**
5. **Model spend this month** (`python -m athena.llm.budget`): mid tier 987 calls, 3.35 M prompt and 0.32 M completion tokens (about 3,400 in per call), mostly `gpt-5.4-mini`; judge 44 calls on `gpt-5.4`, 143 K in; cheap 105 calls. The ceiling is in calls, not tokens, and only the judge has one.
6. **IPOs are not handled anywhere** (no code mentions them). A new listing reaches the platform with no manual step: the exchange's equity master and the daily bhavcopy are read by the `prices` job, so a stock appears in search and charts after its first session (Paras Healthcare listed 2026-10-28 and is already known). Angel One is used only for live quotes and orders; its public documentation shows no IPO endpoint (I found none; Angel's support could confirm), and an IPO application is made in its app.

## 1. Guard rails

**Goal:** an Ask feature answers only about Indian listed securities (stocks, ETFs, indices), mutual funds, IPOs and the data on this platform; anything else gets a short refusal and costs no model call where possible.

**Design, cheapest check first:**

| Layer | What it does | Cost |
|---|---|---|
| 1. Shape | length cap (500 characters), strip control characters, reject empty | none |
| 2. Scope gate in code | recognise an instrument (the same name index the search uses), a platform verb (chart, screen, IPO, fund, valuation, results) or a finance term from a fixed glossary; clear hits go through, clear misses (no instrument, no term, no verb) are refused | none |
| 3. Classifier | only for the ambiguous middle: one call to the cheap tier with a 150-token prompt, JSON `{in_scope, topic}`; the reply is cached by the normalised question | about 300 tokens |
| 4. Scoped prompt | the system prompt states the scope and that document text, headlines and posts are data, never instructions | in the call |
| 5. Tools, not browsing | the agent may call only our read-only data functions (price, fundamentals, filings, IPO list); no web, no file access, no orders | none |
| 6. Output check | no price target, no "buy/sell this for you" wording, numbers must come from the sheet (the grounding check that exists), the standing not-advice line | none |
| 7. Log | each refusal with its layer, so the glossary and the classifier can be tuned | none |

**Where:** new package `athena.guard` (`scope.py`, `glossary.py`, `refusals.py`); a Rust `/api/ask` that calls it first and refuses before spawning anything. The resolver's model path gets layer 2 now.

**Tests:** a red-team file of about 150 lines (off-topic, injection such as "ignore your rules", a headline that contains an instruction, Hindi and Hinglish, borderline such as "what is NAV", "is SIP better than lump sum", "bitcoin") with the expected verdict for each; the suite fails if any off-topic line reaches a model.

**Decisions needed:** (a) scope edges: is general investing education in scope ("what is a P/E")? crypto, gold, forex, tax on gains, loans? (b) refusal wording; (c) Hindi and Hinglish input, yes or no; (d) is an Ask tab wanted, or only the guard on the resolver for now.

## 2. Sentiment page search like Chart Lab

Chart Lab's box is `attachSuggest(input, {enterTakesBest: true, onPick})` over `/api/search` (symbol, company name, ISIN, index, typo tolerant, more-traded first). The Sentiment page has a bare box that takes Enter only. Change: attach the same suggest to it, with the same keys (up, down, Enter, Escape, click); show the company name beside the symbol after a pick; a small "recent" list under the box (last ten viewed, kept in the browser); a clear message when the symbol is not on the list. Kinds: stock, etf, sme, index (mutual funds when Phase 4 adds them). About 40 lines plus tests mirroring Chart Lab's. No server change. Size: small, half a day.

## 3. IPO tab, after Screen

**Data, all checked live on 2026-10-29 with plain requests, no block:**

| Source | Gives | Notes |
|---|---|---|
| NSE `all-upcoming-issues?category=ipo` | 8 rows: main board and SME, status Forthcoming, Active or Closed, dates, price band, issue size, lot (SME) | the calendar |
| NSE `ipo-current-issue` and `ipo-detail` | live subscription by investor category (QIB, non-institutional in two bands, retail) | sampled over the open days |
| NSE `public-past-issues` | 59 issues in two months: price band, final price, listing date | listing day gain computed from our bars |
| SEBI "Draft offer documents filed with SEBI" listing | DRHP, UDRHP, addendum, RHP and abridged prospectus filings, each with a SEBI page and a PDF link (parsed 25 rows) | the pipeline before the exchange lists an issue; matched to NSE rows by company name |
| FPO | the same NSE category returned nothing today | added when a row appears; a rights issue is a corporate action already read (`nse_actions`) |
| BSE | refused a plain request earlier | stays in the try-and-see group (E5) |

**Tab layout:** four lists, **Open now** (with a live subscription bar by category), **Upcoming**, **Recently listed** (issue price, listing price, gain on day one and since, from our bars), and **Filed with SEBI** (the DRHP pipeline, newest first). A row opens a detail pane: dates, price band, size, fresh issue against offer for sale where the document gives it, lot, registrar and lead managers when read, every link (NSE, SEBI page, the PDF), and an **Analyse the draft** button. No subscribe or avoid verdict: the page lists facts and drawbacks with page citations, display only, owner-only, not advice.

**Storage and jobs:** tables `ipo_issues`, `ipo_filings`, `ipo_subscription` (a snapshot per hour while open, so the subscription curve is kept) in the evidence file or its own `ipo.duckdb`, with `first_seen_at` like everything else. Supervisor job `ipo`, every 2 hours while any issue is open, else every 6. A Rust `/api/ipo` and `/api/ipo/:id` read it the way `/api/fund` does, through the snapshot, so a fetch never blanks the tab.

**Analysis of a DRHP:** section 4. Shown with its cost before it runs ("about 14 calls, 40 thousand tokens"), run as a job with progress, cached by document hash.

## 4. Reading long documents without losing information, at a fraction of the cost

A DRHP is 300 to 700 pages, roughly 250 thousand to 600 thousand words, about 350 thousand to 800 thousand tokens. A concall transcript is 8 to 15 thousand words. An annual report is 150 to 300 pages. No model should be handed any of it whole: at the current mid-tier price one DRHP read whole would cost more than the whole month's specialist spend, and long-context models lose facts in the middle. The method is the one the platform already uses for statements, widened: **code finds and counts, retrieval narrows, a model reads a few passages and must quote, code checks the quote, and one strong call writes from a small sheet.**

**Pipeline (one per document, run once, cached by content hash):**

1. **Fetch and extract** the PDF text per page (`pypdf`, as now; scanned pages flagged, not guessed). Keep page numbers.
2. **Sectionise by code.** A DRHP has a fixed order and printed headings (Risk Factors, Objects of the Offer, Capital Structure, Basis for Offer Price, Industry Overview, Our Business, Management, Promoters, Financial Information, Legal and Other Regulatory Disclosures, Outstanding Litigation). Find them from the contents page and heading patterns; store section and page for every chunk in a document store (`documents.duckdb`: doc, page, section, chunk, text, hash). Annual reports and transcripts use the same store (a transcript is split by speaker turn into prepared remarks and Q and A).
3. **Pull numbers by program, not by model.** The offer structure (fresh against offer for sale, size, price band, lot, reservation by category), objects of the offer (use of proceeds with amounts), pre- and post-issue promoter holding, the summary of restated financials, litigation counts and amounts, related-party totals: tables are located by heading, read with a table extractor, and the figures taken as they print, then cross-checked (proceeds add up to the fresh issue; shareholding adds to 100). A model is used only to copy a table whose layout code cannot parse, and its copy is checked the way `pdf_extract` is checked. Ratios (issue size to our peers' market cap, the offer-for-sale share, implied valuation at the upper band) are computed in code.
4. **Targeted questions with retrieval.** A fixed list of about 14 questions (the eight in `retrieval.review`, plus use of proceeds quality, offer-for-sale share and who sells, customer and supplier concentration, key risk factors ranked by the document's own ordering, litigation outliers, promoter background, regulatory approvals pending, dependence on one product or state). For each, retrieve the top 4 to 6 chunks from the right sections (BM25 plus a small local embedding model, about 130 MB, free to run), send about 1,000 to 1,500 tokens to a **cheap** model, require a verbatim quote with page, and drop any finding whose quote is not in the cited chunk (the existing check). About 14 calls of about 2,000 tokens in: **under 30 thousand tokens a document against more than 350 thousand to read it whole.**
5. **Escalate only on failure.** If the cheap model's JSON is invalid or a quote fails twice, rerun that one question on the mid tier. The ladder is already how `Specialist` retries.
6. **One synthesis call, strong tier.** The strong model receives only the findings sheet (the extracted figures, the verified findings with page numbers; about 3 to 4 thousand tokens) and writes the drawbacks, what looks strong, and what could not be read, each point citing a finding id. It may not use a figure that is not on the sheet (the grounding check), and it never gives a rating. This is the one place where a paid, better model earns its price, because its input is small and everything in it has already been checked.
7. **Coverage report, so nothing is silently missed.** Each run records which sections were found, how many pages each held, which questions returned no passage, and which pages were unreadable; the output says "not read: ..." rather than staying quiet. A section that was found but not queried is listed. This is the answer to "how do we know nothing was missed": we cannot read every word with a model, so we make the gaps visible.
8. **DRHP to RHP and addenda.** The later document is diffed against the earlier by section hash; only changed sections are re-read, and the changes are listed (price band, objects of the offer, litigation).

**Earnings calls:** the transcript is fetched on request, split by speaker; the questions are guidance (quote any number), margin and cost commentary, capital expenditure, demand outlook, risks admitted, and the topics analysts pressed in Q and A; each with a quote check. Last quarter's answers (kept) are compared with this quarter's by code on the numeric fields, and by a model only for the wording shift, with both quotes shown. Cost: 5 to 8 calls of about 2,000 tokens. Recordings stay out of scope until a transcript is missing.

**News and posts:** headlines, links, times and short excerpts only (terms of use and cost); the claims layer (E3) checks what can be checked; one model call reads the cited items for tone, on request. Unchanged.

## 5. Cheaper per agent, better where it matters

1. **Spend by job, not by habit.** Mechanical copying and yes-or-no reading on the cheap tier; a verified question on the mid tier only after a cheap failure; the strong tier only for the single synthesis call per company, whose input is small. Today every specialist call is on the mid tier at about 3,400 tokens in; most are packets of figures that a cheaper model reads as well, which the evaluation harness can test with the existing case sets before any switch.
2. **Provider prompt caching.** The persona and output instructions are the same on every call. Put them first and keep them byte-identical so the provider's prefix cache (OpenAI and others) charges the cached share at a fraction of the input price; measure the cached-token count the API returns.
3. **Batch for non-urgent work.** Annual-report and DRHP passes and overnight reviews do not need an instant answer; providers' batch endpoints charge about half. Interactive buttons stay live.
4. **Smaller inputs.** Send only the metrics a specialist declares (already done with `shows`); put units once; send the diff since the last run when a company was analysed before and nothing changed (skip the call and reuse the cached verdict when the packet hash is unchanged; the reply cache does this for identical text, extend it to "same packet, same persona version").
5. **Structured output and a hard completion cap** to cut retries and rambling; the ultra reasoning model needs 12,000 completion tokens and is slow, so it is kept for the synthesis call, not for copying.
6. **Local models for the free work:** a small embedding model for retrieval; later the finance sentiment classifier (E7). Neither costs tokens.
7. **Count tokens, not calls.** Extend `llm_budget.json` with tokens per month per tier and a per-job cap shown before a job starts, so an IPO analysis says its price first.
8. **Measure quality against cost.** A small gold set (twenty documents with the facts a person would list) scores each pipeline version: facts found, quotes verified, cost. Change a model or a prompt only when the score does not drop. The paid tier's value is judged on this, not assumed.
9. **Free first, paid when it shows.** Keep the free models for development and the cheap steps; move the synthesis call to a stronger paid model once the gold set shows a gain. The owner sets the monthly ceiling in tokens.

## 6. Order of work

1. Sentiment search bar (small, no risk).
2. IPO data layer and tab without analysis (NSE, SEBI, subscription, listing gain, links).
3. Document store and the structured-figure extraction (also unlocks concalls and annual reports).
4. DRHP analysis job with the coverage report; earnings-call reading.
5. Guard package and `/api/ask` (when the owner wants the Ask tab); the resolver guard can go in with step 1.
6. Token ceilings, prompt-cache measurement, the gold set; then E4 to E8 of the evidence plan continue.

## 7. Questions for the owner

1. Scope edges for the guard (education, crypto, gold, tax, forex, Hindi) and whether an Ask tab is wanted.
2. IPO tab: include SME issues (the exchange lists them with the main board)? A listing-day-gain column is fine as a fact; confirm no verdict.
3. Budget: a monthly token ceiling for the strong tier, and whether OpenAI credits may pay for the synthesis call.
4. Is a 130 MB local embedding model acceptable (the earlier approval was for a 1 to 2 GB classifier runtime)?

## 8. Later work, recorded Nov 3, 2026

Owner decisions of Nov 3: the strong-model synthesis waits (free models for now, paid models are tried later and judged on the gold set); the 130 MB embedding model is not needed (keyword search over named sections worked); nothing is pushed until the forensic design below is settled.

1. **Crypto and forex.** Today the scope guard refuses both. The owner wants them added later: they need their own data (no exchange calendar, 24-hour prices, different venues and rules), their own instrument resolver entries and a changed guard (they move from the off-limits list to in-scope terms). Not started; do it as its own piece after the forensic work.
2. **Forensic analysis of companies and issues.** Design to be agreed first; see the brainstorm of Nov 3 (detectors in code, text claims extracted by a model with a quote, differences found by code, base rates by sector, a gold set of past governance failures for testing, never an accusation).
