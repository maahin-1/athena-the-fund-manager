# Formula language, formula indicators, strategy builder, Python indicators (5h, 2026-10-18)

## Goal

A user can write `close > sma(200) and rsi(14) < 30`, draw `sma(20) - sma(50)` on a chart, backtest "buy when this, sell when that" without hand-writing JSON, and place a Python indicator file in a folder and draw it. A formula, a chart line and a backtest agree because they are the same tree.

## Decision (D5, kept)

One small side-effect-free expression language that compiles to the **strategy tree the backtest already runs** (`athena.strategies`, version 1: `price`, `ind`, `rolling`, `const`, `arith`, compares, `all`/`any`/`not`). Python stays the authority: it re-validates every tree (`parse_strategy`). The page evaluates the same tree in TypeScript to draw it. No Pine. Python indicators are files the user places; nothing from the page can create or edit them.

## The language

One formula is one expression. A number-valued expression is an indicator; a yes/no expression is a condition.

```
close > sma(200) and rsi(14) < 30
sma(20) - sma(50)
crossover(ema(12), ema(26))            ema(12) crosses above ema(26)
close >= highest(close, 252)[1]        today's close against the high of the 252 days before it
(high + low) / 2
bbands(20, 2).lower                    a line of an indicator with several
macd(12, 26, 9).histogram > 0
```

- **Values:** numbers; prices `open high low close volume` (also `o h l c v`); indicator calls with all parameters optional and positional or named (`sma()` is 50, `bbands(length=20, width=2)`); `highest(x, n)`, `lowest(x, n)`, `average(x, n)` over the last `n` bars (2 to 1000); `x[k]` the value `k` bars ago (0 to 500; shifts add); `.line` picks a line of an indicator with several (default the first).
- **Operators:** `+ - * /` and brackets; `> >= < <=`; `crosses above`, `crosses below`, `crossover(a, b)`, `crossunder(a, b)`; `and`, `or`, `not`. `not` binds tightest, then `and`, then `or`. Words are not case sensitive.
- **Indicators (16, the ones the backtest engine knows):** sma, ema, wma, bbands (upper, lower), sar, channel (high, low), rsi, macd (macd, signal, histogram), stoch (k, d), adx (adx, plus_di, minus_di), atr, obv, cci, mfi, willr, roc, each on its Python registry's parameter names and ranges. The page maps each to its own implementation; a numeric test fixes that the two agree.
- **Limits** (the strategy model's): 60 parts, 8 levels, 8 conditions in one `and`/`or` (a chain `a and b and c` is one), windows 2 to 1000, shifts to 500, no division by a literal zero. A formula that breaks one is refused with the place.
- **Unknown stays unknown:** where something cannot be computed yet (warm-up, a shift past the start, a division by zero), a comparison is not true and not false; `not` of unknown is unknown, so a bar with a gap is never a signal. This is the engine's three-valued logic, in TypeScript as in Python.
- **Errors** say what and where (character position), and suggest the nearest name for a mistyped function.

## Slices

- **A. Language core.** `formula/` in the page: parser to the tree, a TypeScript evaluator with the three-valued logic, the indicator catalogue (names, parameters, lines, the map to the page's implementations). Cross-checked against Python: a script generates a golden file (bars, trees, expected series from the Python engine); the page's tests require text to compile to the stored tree and the tree to evaluate to the stored series.
- **B. Formula indicators in the Chart Lab.** A user's numeric formulas are saved as a document on the server (`/api/formulas`), appear in the Indicators menu under "My formulas", are drawn on the price pane or in their own pane, can be hidden, edited and deleted, and survive in templates and layouts by name.
- **C. Strategy builder.** A form in the Research tab's backtest area: a name, an entry formula and an exit formula (conditions), the risk options the strategy model has (protective stop, trailing stop, profit target, bars held, share of cash), Run and Save, with errors from both the page and Python. `/api/backtest` accepts a strategy and returns the same summary the built-in rules get. Saved strategies are a server document.
- **D. Python indicators.** Files in an `indicators/` folder beside the data (placed by the user). Each is listed with its SHA-256 and must be switched on once; a changed file is off again. A file defines `compute(bars)` and is run in a separate Python child process (isolated mode, a time limit, bars in on stdin, series out as JSON). The server never writes a `.py` file, and no page request can. It is not a sandbox on Windows and the page says so next to the switch.

## Not in scope

Loops, user functions, variables and multi-line scripts (a later step if wanted), strategy short selling and multiple positions, plotting several lines from one formula, alerts from formulas (5g will take the same condition tree), Pine import.

## Evidence required

Parser: every operator and precedence, shifts adding, line selection, named and positional parameters, defaults, every limit, every error with its position, type errors (a yes/no used as a number), case. Evaluator: each node kind, warm-up, shift, rolling, three-valued logic, division by zero. Parity: the golden file from Python. Then per slice its own tests, and the full suites at the end of each.
