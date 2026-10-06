# Phase 1h — Research tools: design

Date: 6 Oct 2026. Status: written for review; Plans 1h-a to 1h-d implement it in that order. Builds on Phases 1a-1g.

## Goal

Let a person do their own research on a stock or ETF the way an analyst would, without leaving the app: pick the exact instrument from a list, choose and tune the indicators on the chart, and backtest a strategy of their own (built from a form, pasted as JSON, or described in plain words) with the same no-lookahead, cost and blinding discipline as the built-in rules.

The product will be open source and run on each user's own machine with their own keys; it should be possible later to host it, so everything new is plain data, holds no global state, and takes keys per request, not from the process.

## Non-goals

No shorting, no multi-asset portfolio, no intraday data, no fundamentals inside a strategy (Yahoo statements are not point-in-time), no parameter optimisation or sweeps (they invite curve-fitting), and no code execution of any kind: a strategy is data, never code. The terminal-style UI rebuild is a separate later phase; these features live in the current Streamlit page, but their logic sits outside the page so it survives a rebuild.

## Four parts, four plans

### 1h-a — Full match list, click to analyse

Today the resolver keeps at most 5 candidates (`MAX_CANDIDATES`, `resolver.py:26`), so the list is cut short, and choosing means retyping.

- The resolver keeps up to `MAX_LISTED = 50` ranked candidates in `Ambiguity.candidates` (the exact-name path too). The model classifier and `Resolution.candidates` still see only the top `MAX_CANDIDATES = 5`, so the evaluation harness and its prompts do not change.
- `confirm(ambiguity, index)` works unchanged on the longer list.
- The CLI report shows at most 10 candidates and says "and N more; type the exact symbol".
- The dashboard shows every candidate in a table. Clicking a row (single-row selection) puts that identifier in the search box and the analysis starts at once.
- Implementation detail: the text box is bound to a session key; a pick is stored in a separate "pending" key and applied at the top of the next run, before the text box is created (Streamlit forbids setting a widget's key after it exists).
- Risk: Streamlit's `AppTest` may not be able to simulate a dataframe row selection. If so, the pick is exercised by calling the selection handler directly and the row-selection wiring is covered by a live check.

### 1h-b — Selectable, tunable indicators

A registry replaces the hard-wired lines.

- `athena/technicals/indicators.py`: `IndicatorSpec(key, label, placement, params, compute)`; `Param(name, label, default, minimum, maximum, step)`. `compute(candles, params)` returns named lines. `placement` is `overlay` (drawn on the price chart) or `pane` (own sub-chart).
- Overlays: SMA, EMA, WMA, Bollinger Bands (length, width), Parabolic SAR, Donchian channel (N-day high and low; N=252 is the 52-week high/low). Panes: RSI, MACD, Stochastic, ADX with DI lines, ATR, OBV, CCI, MFI, Williams %R, ROC. Volume is a toggle. All from ta-lib or small numpy functions; no new dependency.
- A chart selection is a plain list of `{id, key, params}` (JSON-friendly). At most 8 indicators; parameters are validated against their ranges with a clear message. The same indicator may appear twice with different settings (SMA 20 and SMA 50).
- `build_chart(candles, selection, title)` returns one multi-pane figure (price and overlays, volume, one row per pane indicator) with a shared x axis, so zoom and pan stay aligned. The default selection reproduces today's chart (SMA 50, SMA 200, Bollinger 20/2, volume, RSI 14).
- `DashboardView` drops `price_figure` and `rsi_figure` and carries `candles` (plain data); the page builds the figure from the selection. This is the one deliberate behaviour change; the chart and app tests are updated to match.
- The page gets an "Indicators" expander: current indicators each with parameter boxes and a remove button, an "Add indicator" picker, and "Reset to default". The selection is kept per session.
- Display only: the specialists keep reading their fixed, stated windows, so moving a slider never changes a verdict. The page says so.
- The ta-lib "Technical indicators" metric panel is unchanged.

### 1h-c — A strategy format, engine support, rule builder

**The format** (`athena/strategies/`): a strategy is a frozen, versioned structure with `name`, `entry` and `exit` conditions, and an optional `stop_atr` (ATR multiple, default off).

- Condition: `all` / `any` (lists), `not`, or a comparison with an operator in `gt ge lt le crosses_above crosses_below` and a left and right expression.
- Expression: a price field (`open high low close volume`); an indicator from the registry (`key`, `params`, `line`); a rolling `max` / `min` / `mean` of a price field or indicator over a window; a constant; arithmetic (`add sub mul div`); every series expression may carry `shift` (read k bars back; `shift: 1` means yesterday).
- Limits: nesting depth, node count and window length are bounded; unknown names, out-of-range parameters and division by a constant zero are rejected with messages that name the offending part.
- `parse_strategy(data) -> Strategy` (strict), `to_dict`, and `describe(strategy) -> str` (plain English, used for the confirmation the user sees).
- Built-in presets expressed in the format: 52-week breakout (buy when the close reaches its highest close of the previous 252 days, sell when it reaches the lowest), golden cross (SMA 50 over SMA 200), RSI mean reversion, Bollinger rebound. The two existing packet rules (`trend`, `persona`) stay as they are.

**No lookahead.** Every indicator and rolling function is causal (the value at bar t depends only on bars up to t), and a strategy is compiled once to boolean arrays that the engine reads at index t. A property test proves it: a run on a truncated history makes the same decisions as the longer run up to the cut, and corrupting every later bar does not change a decision.

**Engine.** A compiled strategy is a second kind of rule beside the packet rules; `run_backtest` accepts either and everything else (next-open fills, protective stop, costs, benchmark, warm-up, split adjustment, blinding replay, summary) is shared and unchanged. A rule that depends on an absolute price level (for example "close above 500") is flagged DIFFERENT by the existing blinding check, which is the intended behaviour.

**Entry points.** `run_rules` takes rule objects as well as built-in names; `RuleRun` and the backtest view carry each rule's description so custom strategies display like built-in ones. CLI: `python -m athena.backtest SBIN --strategy my_strategy.json`. Dashboard: the backtest section gains a strategy picker (built-in rules, a preset, a form builder with up to 4 entry and 4 exit conditions each built from dropdowns and number boxes, or pasted JSON), the plain-English description, and the same results panels.

### 1h-d — Text to strategy

A person types, for example, "buy when the price makes a new 52-week high and RSI is below 70, sell when it closes below the 50-day average".

- `athena/strategies/from_text.py` sends a cheap-tier model call (through the existing router) a prompt containing the format's schema and the indicator list, and asks for JSON only: `{"strategy": {...} | null, "assumptions": [...], "unsupported": [...]}`.
- The answer is parsed with `parse_strategy`. On a validation error the model gets one retry with the error text. Anything the format cannot express (shorting, fundamentals, intraday, several assets, "when momentum is good") comes back in `unsupported` or `assumptions`, not silently approximated.
- The page shows the plain-English description, the assumptions, the unsupported parts and the JSON, and the user presses "Run this strategy". Nothing runs unseen.
- User text is untrusted input; the model's output is only ever data that the strict parser validates, so a prompt injection cannot execute anything.
- Cost: one call of about 2k tokens per strategy. Offline tests use a fake model; a small golden set of phrasings is a live opt-in test.

## Hosting and open-source readiness

`ChartSelection` and `Strategy` are JSON-serialisable; none of the new code reads keys from the environment or keeps a process-wide cache (the existing per-input session memory stays in the page). Model calls in 1h-d use the router that already takes its providers as an argument.

## Testing

Each plan follows the project's method: prototype in a scratch copy, assemble the plan from the verified files, execute task by task with subagents, hand-run mutation checks that must all be caught, update the TRD, and finish with the whole suite green. Property tests cover causality and the parser's rejection of malformed input; the existing dashboard tests are the regression net for the page.

## Open points (decide while building, not blocking)

- Whether `AppTest` can drive dataframe row selection (1h-a).
- Whether the form builder needs an "and/or" mix beyond all-or-any per side (start with all-or-any).
- A strategy library the user can save to disk is out of scope for now.
