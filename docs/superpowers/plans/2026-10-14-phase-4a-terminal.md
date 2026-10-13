# Phase 4a Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fast local terminal: a Rust server that holds the Angel One live feed and pushes it to a docked TypeScript page (watchlist, chart, depth, quote, tape), with saved layouts, link groups and a command line.

**Architecture:** `terminal/` at the repo root holds one Rust crate (`athena-terminal`, binary of the same name) and one Vite page (`terminal/web`). The server logs in to Angel One, keeps one WebSocket feed, merges ticks per symbol into a hub, and every 16 ms sends each connected page only the symbols that changed since its last frame. Bars come from the DuckDB warehouse (daily, read-only) and Yahoo (1, 5 and 15 minute). The exchange quote page is the fallback when the feed is stale. Spec: `docs/superpowers/specs/2026-10-14-phase-4a-terminal-design.md`.

**Tech Stack:** Rust (tokio, axum with ws, tokio-tungstenite with rustls, reqwest with rustls, serde, hmac, sha1, duckdb bundled), TypeScript (Vite, vitest, dockview-core, lightweight-charts), Playwright for the smoke test.

## Global Constraints

- Read-only: the program contains no order, position or funds call and no URL for one.
- Secrets (`ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`, every token) are never printed, logged, put in an error string, serialized, or sent to the page. The settings file is read by the program itself; nothing in this plan reads, prints or copies its contents.
- Server binds `127.0.0.1` only and rejects WebSocket and API requests whose `Origin` is not `http://127.0.0.1:<port>` or `http://localhost:<port>`.
- Freshness: a streamed quote is used while the last message for that symbol arrived at most 15 seconds ago; otherwise the polled exchange quote is used and marked `poll`. The page always opens, whatever the feed does.
- Series fallback order for the cash segment: `-EQ`, `-BE`, `-BZ`, `-SM`, `-ST`. Polled exchange series order: EQ, SM, BE, BZ, ST.
- At most 50 streamed symbols (the cap is lowered if Task 6 finds Angel's documented limit is lower).
- Nothing from the stream reaches an analyst or a verdict. Streamlit is not modified.
- Prices from the feed are integers in paise: divide by 100. A zero means "none".
- Frame interval 16 ms. JSON messages. No binary protocol unless Task 13 measures a need.
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## File Structure

```
terminal/
  Cargo.toml
  src/
    main.rs            # args, wiring, run
    config.rs          # settings file reader (names only; values never logged)
    totp.rs            # RFC 6238 six-digit code
    angel/
      mod.rs
      login.rs         # REST login -> Session (redacted Debug)
      master.rs        # instrument file: symbol -> token, cache
      decode.rs        # binary snap-quote packet -> Tick
      stream.rs        # websocket task: connect, subscribe, heartbeat, reconnect
    quote.rs           # Quote (the shape sent to the page) + conversions
    hub.rs             # latest quotes, subscriptions, frame building, freshness
    poll.rs            # exchange quote page fallback
    bars.rs            # warehouse daily + Yahoo intraday, cache
    layouts.rs         # layout files + presets
    server.rs          # axum routes, websocket, origin check, static files
    fake.rs            # --fake-feed: deterministic ticks for tests
  tests/               # integration tests
  web/
    package.json  vite.config.ts  tsconfig.json  index.html
    src/
      main.ts          # shell: top bar, dock, wiring
      feed.ts          # websocket client, subscribe diffing
      links.ts         # link groups
      command.ts       # command parser
      layout.ts        # layout model, save/load, presets
      theme.ts         # dark/light tokens
      indicators.ts    # EMA, SMA, Bollinger, RSI, MACD
      ranges.ts        # range row -> bar size
      panels/ watchlist.ts chart.ts depth.ts quote.ts tape.ts
    test/              # vitest
    e2e/smoke.spec.ts  # Playwright
scripts/terminal-market-check.py   # market-hours measurement
```

---

### Task 1: Crate scaffold and settings reader

**Files:**
- Create: `terminal/Cargo.toml`, `terminal/src/main.rs`, `terminal/src/config.rs`
- Modify: `.gitignore` (add `terminal/target/`, `terminal/web/node_modules/`, `terminal/web/dist/`)

**Interfaces:**
- Produces: `config::Settings { api_key, client_code, pin, totp_secret }` with a `Debug` that prints only `Settings(client_code=…)`; `config::read(path: &Path) -> Result<Settings, ConfigError>` where `ConfigError::Missing(Vec<&'static str>)` lists names only.

- [ ] **Step 1: Write the failing test** in `terminal/src/config.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_four_names_and_debug_hides_values() {
        let text = "ANGEL_API_KEY=k1\nANGEL_CLIENT_CODE=C123\nANGEL_PIN=9999\nANGEL_TOTP_SECRET=JBSWY3DPEHPK3PXP\nOTHER=x\n";
        let s = parse(text).unwrap();
        assert_eq!(s.client_code, "C123");
        let shown = format!("{s:?}");
        assert!(shown.contains("C123"));
        for hidden in ["k1", "9999", "JBSWY3DPEHPK3PXP"] {
            assert!(!shown.contains(hidden), "{hidden} leaked");
        }
    }

    #[test]
    fn missing_names_are_listed_without_values() {
        let err = parse("ANGEL_API_KEY=k1\n").unwrap_err();
        match err {
            ConfigError::Missing(names) => assert_eq!(names, vec!["ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"]),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn quotes_and_comments_are_handled() {
        let text = "# c\nANGEL_API_KEY=\"k1\"\nANGEL_CLIENT_CODE='C1'\nANGEL_PIN=1\nANGEL_TOTP_SECRET=AAAA\n";
        assert_eq!(parse(text).unwrap().api_key, "k1");
    }
}
```

- [ ] **Step 2:** `cargo test --manifest-path terminal/Cargo.toml` → FAIL (no `parse`).
- [ ] **Step 3: Implement** `Settings`, `ConfigError` (`Missing`, `Unreadable`), `parse(&str)`, `read(&Path)`. `parse` skips blank and `#` lines, splits on the first `=`, trims, strips one pair of matching quotes. Manual `Debug` for `Settings`. `main.rs`: `fn main() { println!("athena-terminal"); }`. `Cargo.toml`: package `athena-terminal` edition 2021, dependencies added as later tasks need them.
- [ ] **Step 4:** run tests → PASS.
- [ ] **Step 5: Commit** `feat(terminal): crate scaffold and a settings reader that never shows values`.

### Task 2: TOTP

**Files:** Create `terminal/src/totp.rs`; add `hmac = "0.12"`, `sha1 = "0.10"` to `Cargo.toml`; `mod totp;` in `main.rs`.

**Interfaces:** Produces `totp::code(secret_base32: &str, unix_seconds: u64) -> Result<String, TotpError>` (six digits, zero-padded, 30-second step, SHA-1).

- [ ] **Step 1: Failing tests** (RFC 6238 SHA-1 vectors; the ASCII secret `12345678901234567890` is base32 `GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ`; the RFC's 8-digit codes truncated to six digits):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    const SECRET: &str = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";

    #[test]
    fn rfc6238_vectors() {
        assert_eq!(code(SECRET, 59).unwrap(), "287082");
        assert_eq!(code(SECRET, 1111111109).unwrap(), "081804");
        assert_eq!(code(SECRET, 1234567890).unwrap(), "005924");
        assert_eq!(code(SECRET, 2000000000).unwrap(), "279037");
    }

    #[test]
    fn spaces_lowercase_and_padding_are_accepted() {
        assert_eq!(code("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", 59).unwrap(), "287082");
        assert_eq!(code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ====", 59).unwrap(), "287082");
    }

    #[test]
    fn a_bad_secret_is_an_error_that_does_not_echo_it() {
        let err = code("not base32 !!", 59).unwrap_err();
        assert!(!format!("{err}").contains("not base32"));
    }
}
```

- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement**:

```rust
use hmac::{Hmac, Mac};
use sha1::Sha1;

#[derive(Debug)]
pub struct TotpError;
impl std::fmt::Display for TotpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "the TOTP secret is not valid base32")
    }
}
impl std::error::Error for TotpError {}

fn base32(input: &str) -> Result<Vec<u8>, TotpError> {
    let mut bits = 0u32;
    let mut have = 0u32;
    let mut out = Vec::new();
    for ch in input.chars().filter(|c| !c.is_whitespace() && *c != '=') {
        let v = match ch.to_ascii_uppercase() {
            c @ 'A'..='Z' => c as u32 - 'A' as u32,
            c @ '2'..='7' => c as u32 - '2' as u32 + 26,
            _ => return Err(TotpError),
        };
        bits = (bits << 5) | v;
        have += 5;
        if have >= 8 {
            have -= 8;
            out.push((bits >> have) as u8);
            bits &= (1 << have) - 1;
        }
    }
    if out.is_empty() { Err(TotpError) } else { Ok(out) }
}

pub fn code(secret_base32: &str, unix_seconds: u64) -> Result<String, TotpError> {
    let key = base32(secret_base32)?;
    let mut mac = Hmac::<Sha1>::new_from_slice(&key).map_err(|_| TotpError)?;
    mac.update(&(unix_seconds / 30).to_be_bytes());
    let h = mac.finalize().into_bytes();
    let o = (h[19] & 0x0f) as usize;
    let n = ((h[o] as u32 & 0x7f) << 24) | ((h[o + 1] as u32) << 16) | ((h[o + 2] as u32) << 8) | h[o + 3] as u32;
    Ok(format!("{:06}", n % 1_000_000))
}
```

- [ ] **Step 4:** run → PASS. If the 2000000000 vector fails, recompute it from the RFC table (8-digit 69279037) before changing code.
- [ ] **Step 5: Commit** `feat(terminal): TOTP code from the secret (RFC 6238 vectors)`.

### Task 3: Angel login

**Files:** Create `terminal/src/angel/mod.rs`, `terminal/src/angel/login.rs`; add `reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }`, `serde = { version = "1", features = ["derive"] }`, `serde_json = "1"`, `tokio = { version = "1", features = ["full"] }`.

**Interfaces:**
- Consumes: `config::Settings`, `totp::code`.
- Produces: `login::Session { api_key, client_code, jwt, feed_token }` (manual `Debug` showing only `client_code`); `login::build_request(settings: &Settings, code: &str) -> serde_json::Value` (body `{"clientcode","password","totp"}`); `login::parse_reply(body: &serde_json::Value) -> Result<(String, String), LoginError>` returning `(jwt, feed_token)`, error text is the broker's `message` cut to 120 characters, never the PIN, secret or a token; `async fn login(client: &reqwest::Client, settings: &Settings, unix: u64) -> Result<Session, LoginError>`.

Endpoint `POST https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword` with headers `Content-type: application/json`, `Accept: application/json`, `X-UserType: USER`, `X-SourceID: WEB`, `X-ClientLocalIP: 127.0.0.1`, `X-ClientPublicIP: 127.0.0.1`, `X-MACAddress: 00:00:00:00:00:00`, `X-PrivateKey: <api key>`. Reply `{"status":true,"data":{"jwtToken","refreshToken","feedToken"}}`.

- [ ] **Step 1: Failing tests**:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn body_has_the_three_fields() {
        let s = Settings::for_test("K", "C1", "1234", "AAAA");
        let b = build_request(&s, "123456");
        assert_eq!(b, json!({"clientcode": "C1", "password": "1234", "totp": "123456"}));
    }

    #[test]
    fn reply_gives_tokens() {
        let r = json!({"status": true, "data": {"jwtToken": "J", "refreshToken": "R", "feedToken": "F"}});
        assert_eq!(parse_reply(&r).unwrap(), ("J".to_string(), "F".to_string()));
    }

    #[test]
    fn refusal_carries_only_a_short_message() {
        let r = json!({"status": false, "message": "Invalid totp"});
        assert_eq!(parse_reply(&r).unwrap_err().to_string(), "Angel One refused the login: Invalid totp");
        let long = "x".repeat(500);
        let r = json!({"status": false, "message": long});
        assert!(parse_reply(&r).unwrap_err().to_string().len() < 160);
    }

    #[test]
    fn missing_feed_token_is_an_error() {
        let r = json!({"status": true, "data": {"jwtToken": "J"}});
        assert!(parse_reply(&r).is_err());
    }

    #[test]
    fn session_debug_hides_tokens() {
        let s = Session { api_key: "K".into(), client_code: "C1".into(), jwt: "JJJ".into(), feed_token: "FFF".into() };
        let t = format!("{s:?}");
        assert!(t.contains("C1") && !t.contains("JJJ") && !t.contains("FFF") && !t.contains("K\""));
    }
}
```

Add `Settings::for_test` under `#[cfg(test)]` in `config.rs`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement as specified; `login` sends the request with a 15-second timeout and maps transport errors to `LoginError::Request(kind)` where `kind` is the error category only. **Step 4:** tests PASS. **Step 5: Commit** `feat(terminal): Angel One login that shows no secret`.
- [ ] **Step 6 (manual, outside the test run):** add `--check-login` to `main.rs` that reads the settings file given by `--env-file` (default `.env`), logs in, and prints `login works for <client code>; feed token received|NOT received`. Run it once and report only that line.

### Task 4: Instrument master

**Files:** Create `terminal/src/angel/master.rs`; add `tokio` fs usage only.

**Interfaces:** Produces `Master::new(rows: Vec<(String /*symbol*/, String /*token*/)>)`, `Master::token(&self, symbol: &str) -> Option<&str>` trying suffixes `-EQ,-BE,-BZ,-SM,-ST` in order, `Master::symbol(&self, token: &str) -> Option<&str>` (the plain symbol without suffix), `async fn load(client, cache: &Path, now: SystemTime) -> Result<Master, MasterError>` (use the cache file when under 20 hours old, else download `https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json`, keep only rows with `exch_seg == "NSE"` and a symbol ending in one of the suffixes, write `[{"token","symbol"}]` to the cache; a failed cache write is ignored).

- [ ] **Step 1: Failing tests**: suffix order (`SBIN-BE` and `SBIN-EQ` both present gives the `-EQ` token); `symbol("3045")` returns `SBIN`; unknown symbol returns `None`; `filter_rows` keeps only NSE rows with a wanted suffix (feed it a JSON array with a BSE row, an NSE `SBIN-EQ`, an NSE `SBIN26OCTFUT` row; only the first NSE row remains); a cache younger than 20 hours is read without a download (use a temp file and a download closure that panics).
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): instrument master lookup with the series fallback`.

### Task 5: Tick decoder

**Files:** Create `terminal/src/angel/decode.rs`.

**Interfaces:** Produces `Tick { token: String, exchange_ms: i64, last: i64, average: i64, volume: i64, open: i64, high: i64, low: i64, prev_close: i64, upper: i64, lower: i64, high_52: i64, low_52: i64, bids: Vec<(i64, i64)>, asks: Vec<(i64, i64)> }` (prices in paise, `(price, quantity)` pairs), `decode(packet: &[u8]) -> Result<Tick, DecodeError>`. Only snap-quote (mode 3, 379 bytes) packets are decoded; a packet of another mode or length is `DecodeError::Unsupported`. Text frames (`pong`) never reach this function.

Layout (little-endian, from the SmartAPI v2 stream; byte offsets): `0` mode u8; `1` exchange type u8; `2..27` token ASCII, NUL-terminated; `27..35` sequence i64; `35..43` exchange timestamp ms i64; `43..51` last price i64; `51..59` last qty i64; `59..67` average price i64; `67..75` volume i64; `75..83` total buy qty f64; `83..91` total sell qty f64; `91..99` open; `99..107` high; `107..115` low; `115..123` previous close (all i64); `123..131` last traded time s i64; `131..139` open interest i64; `139..147` OI change i64; `147..347` ten depth entries of 20 bytes each: flag u16, quantity i64, price i64, orders u16; flag `1` is a bid, flag `0` is an ask; `347..355` upper circuit i64; `355..363` lower circuit i64; `363..371` 52-week high i64; `371..379` 52-week low i64.

- [ ] **Step 1: Failing tests** with a builder that writes a packet from fields:

```rust
fn packet(token: &str, ms: i64, last: i64, prev: i64, depth: &[(u16, i64, i64)]) -> Vec<u8> {
    let mut p = vec![0u8; 379];
    p[0] = 3;
    p[1] = 1;
    p[2..2 + token.len()].copy_from_slice(token.as_bytes());
    p[35..43].copy_from_slice(&ms.to_le_bytes());
    p[43..51].copy_from_slice(&last.to_le_bytes());
    p[115..123].copy_from_slice(&prev.to_le_bytes());
    for (i, (flag, qty, price)) in depth.iter().enumerate() {
        let o = 147 + i * 20;
        p[o..o + 2].copy_from_slice(&flag.to_le_bytes());
        p[o + 2..o + 10].copy_from_slice(&qty.to_le_bytes());
        p[o + 10..o + 18].copy_from_slice(&price.to_le_bytes());
    }
    p[347..355].copy_from_slice(&88290i64.to_le_bytes());
    p[355..363].copy_from_slice(&72240i64.to_le_bytes());
    p
}
```

Tests: token `"3045"` decoded; `last`, `prev_close` read back; depth `[(1,1200,81245),(1,3400,81240),(0,900,81250)]` gives two bids and one ask in packet order; circuit limits read back; a 100-byte packet and a mode-1 packet are `Unsupported`; a zero price level is dropped from the lists.
- [ ] **Step 2–5:** fail, implement with small `i64_at(&[u8], usize)` helpers, pass, commit `feat(terminal): decode Angel snap-quote ticks`.

### Task 6: Quote shape and hub

**Files:** Create `terminal/src/quote.rs`, `terminal/src/hub.rs`.

**Interfaces:**
- `quote::Quote { symbol: String, last: f64, prev_close: f64, open: Option<f64>, high: Option<f64>, low: Option<f64>, volume: Option<f64>, average: Option<f64>, high_52: Option<f64>, low_52: Option<f64>, lower_band: Option<f64>, upper_band: Option<f64>, exchange_ms: Option<i64>, bids: Vec<(f64, f64)>, asks: Vec<(f64, f64)>, source: Source }` with `Source::{Stream, Poll}` serialized as `"stream"` and `"poll"`; `Quote::from_tick(symbol, &Tick) -> Option<Quote>` (None when `last` or `prev_close` is zero); prices divided by 100; serde field names are short and fixed: `s l pc o h lo v avg h52 l52 lb ub t b a src`.
- `hub::Hub` (cheap to clone, `Arc` inside): `update(&self, quote: Quote, now: Instant)`, `watch(&self, client: ClientId, symbols: &[String])` (replaces that client's set), `drop_client(&self, client)`, `wanted_symbols(&self) -> Vec<String>` (union, sorted, cut to 50), `frame(&self, client: ClientId, now: Instant) -> Option<Frame>` (only symbols in the client's set whose revision is newer than the client's last sent revision; marks them sent), `stale_symbols(&self, now: Instant) -> Vec<String>` (wanted symbols whose last `Stream` quote is older than 15 s or absent), `frame_json(&Frame) -> String` of the form `{"type":"q","q":[…]}`.
- A `Poll` quote never replaces a `Stream` quote that is still fresh.

- [ ] **Step 1: Failing tests** for: paise to rupees (`81240 → 812.4`); zero price gives `None`; two updates to one symbol before a frame produce one entry holding the latest values; a second `frame` call with no new update returns `None`; a client only receives symbols it watches; two clients each get the update once; `wanted_symbols` is the sorted union and is cut at 50; a stream quote 20 s old is stale and a fresh poll quote replaces it, but a poll quote never replaces a 5-second-old stream quote; `drop_client` forgets the client's set; `frame_json` round-trips through `serde_json` with the short field names.
- [ ] **Step 2–5:** fail, implement (a `Mutex<Inner>`; a global revision counter; per-symbol `(rev, Quote, stream_at: Option<Instant>)`; per-client `(set, sent: HashMap<String,u64>)`), pass, commit `feat(terminal): quote shape and the per-client frame hub`.
- [ ] **Step 6:** open Angel's SmartAPI documentation for the WebSocket limits (tokens per connection, connections per client) with WebFetch, write the numbers into a comment in `hub.rs`, and lower the 50 cap if the documented per-connection limit is below it. Record the finding in the spec's Design section in the same commit.

### Task 7: Angel stream task

**Files:** Create `terminal/src/angel/stream.rs`; add `tokio-tungstenite = { version = "0.24", features = ["rustls-tls-webpki-roots"] }`, `futures-util = "0.3"`.

**Interfaces:** Produces `async fn run_stream(session: Session, master: Arc<Master>, hub: Hub, wanted: tokio::sync::watch::Receiver<Vec<String>>, shutdown: CancellationToken)`; connects to `wss://smartapisocket.angelone.in/smart-stream` with headers `Authorization: <jwt>`, `x-api-key`, `x-client-code`, `x-feed-token`; sends the text frame `ping` every 10 seconds; on every change of `wanted` computes added and removed symbols and sends `{"correlationID":"athena","action":1|0,"params":{"mode":3,"tokenList":[{"exchangeType":1,"tokens":["…"]}]}}` (action 1 subscribes, 0 unsubscribes); decodes each binary message with `decode`, maps `token → symbol` with `master.symbol`, builds a `Quote`, calls `hub.update`. Text frames and undecodable packets are ignored. On any socket error it waits 1, 2, 4, … up to 30 seconds and reconnects, re-subscribing every wanted symbol. It exposes `status: watch::Sender<FeedStatus>` with `Connecting | On | Off(reason)` where `reason` is a short fixed phrase, never a header value.

- [ ] **Step 1: Failing integration test** (`terminal/tests/stream.rs`) with an in-process `tokio-tungstenite` server on `127.0.0.1:0` standing in for Angel: it accepts one connection, checks the four headers are present by name only, reads the subscribe message and checks its `tokenList` for the wanted symbol's token, sends one snap-quote packet built like Task 5's, and the test asserts the hub holds that quote with source `Stream`. A second test: the server drops the socket after the first packet; the task reconnects and subscribes again (assert two subscribe messages arrive). The URL is a parameter of an inner `run_stream_at(url, …)` so tests can point it at the local server (plain `ws://`).
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): Angel stream task with resubscribe and backoff`.

### Task 8: Fallback poller

**Files:** Create `terminal/src/poll.rs`.

**Interfaces:** Produces `poll::parse_quote(symbol: &str, body: &serde_json::Value) -> Option<Quote>` (port of the Python `parse_quote`: `equityResponse[0]`, `metaData.previousClose/open/dayHigh/dayLow`, `tradeInfo.lastPrice/totalTradedVolume/totalTradedValue`, `priceInfo.yearHigh/yearLow/priceBand`, `orderBook.buyPrice1..5/buyQuantity1..5/sellPrice1..5/sellQuantity1..5`, `lastUpdateTime` as `%d-%b-%Y %H:%M:%S` in IST; source `Poll`; `None` when last or previous close is missing or zero), and `async fn run_poller(hub: Hub, client: reqwest::Client, shutdown)` which every 3 seconds takes `hub.stale_symbols(now)` (at most 10 per round, round-robin), asks `https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=<S>&symbol=<SYM>` with headers `User-Agent: Mozilla/5.0` and `Accept: application/json`, tries series in the order EQ, SM, BE, BZ, ST (remembering the one that worked per symbol), and updates the hub with the first answer that parses.

- [ ] **Step 1: Failing tests** with a fixture body: price, previous close, five levels each side, band `"722.40-882.90"` kept as `lower_band`/`upper_band`; empty `equityResponse` gives `None`; a missing last price gives `None`; the series memory tries the remembered series first (test the ordering function, not the network).
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): exchange quote fallback poller`.

### Task 9: Server, push and safety

**Files:** Create `terminal/src/server.rs`, `terminal/src/fake.rs`; modify `main.rs` (args: `--env-file`, `--port` default 8700, `--fake-feed`, `--check-login`, `--web-dir` default `terminal/web/dist`); add `axum = { version = "0.7", features = ["ws"] }`, `tower-http = { version = "0.5", features = ["fs"] }`.

**Interfaces:** Routes: `GET /` and static files from `--web-dir`; `GET /ws` (WebSocket); `GET /api/status` returns `{"feed":"connecting|on|off","reason":"…","clients":n}`. Page → server messages: `{"type":"watch","symbols":["SBIN","TCS"]}`. Server → page: `{"type":"q","q":[…]}` frames every 16 ms when something changed, `{"type":"status","feed":…}` on change, `{"type":"hello","server_ms":<unix ms>}` on connect. `server::origin_ok(origin: Option<&str>, port: u16) -> bool` accepts only `http://127.0.0.1:<port>` and `http://localhost:<port>` (a missing Origin is refused for `/ws` and `/api/*`). `fake::run_fake(hub, wanted, shutdown)` emits a deterministic random-walk tick per wanted symbol every 250 ms (seeded by the symbol) so the page and the smoke test run with no broker.

- [ ] **Step 1: Failing tests**: `origin_ok` table (accepts both local origins with the right port, refuses `http://evil.test`, refuses a wrong port, refuses `None`); an integration test starts the server with the fake feed on port 0, connects a `tokio-tungstenite` client with a good Origin, sends `watch`, and receives a `q` frame for that symbol within 2 seconds; the same connection with a bad Origin is refused (HTTP 403); `/api/status` with no Origin is 403; a client that closes is dropped from the hub (`clients` returns to 0).
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): local server with origin check and 16 ms push`.

### Task 10: Bars API

**Files:** Create `terminal/src/bars.rs`; add `duckdb = { version = "1", features = ["bundled"] }`.

**Interfaces:** `GET /api/bars?symbol=SBIN&tf=1D|15m|5m|1m&limit=N` returns `{"symbol","tf","bars":[{"t":<unix seconds>,"o","h","l","c","v"}],"source":"warehouse|yahoo","note":"…"}`. Daily bars come from the warehouse file (path from `--warehouse`, default `.athena/warehouse.duckdb`) opened read-only; the SQL reads the existing daily-bars table (inspect `src/athena/warehouse/warehouse.py` for the exact table and column names in step 1 and write them in the test) for the symbol, newest `limit` rows (default 500), oldest first. Intraday bars come from `https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?interval=<1m|5m|15m>&range=<1d|5d|5d>` (parse `chart.result[0].timestamp` and `indicators.quote[0].open/high/low/close/volume`, skipping null rows), cached in memory for 60 seconds per `(symbol, tf)`, fetched in a background task so the request that triggers a fetch returns the cached or empty list with `note: "loading"` and never blocks the push loop. If the warehouse cannot be opened (for example a build holds it), daily bars return an empty list with `note: "warehouse busy"`.

- [ ] **Step 1: Failing tests**: build a temp DuckDB file with the warehouse's real daily table and three rows; the SQL helper returns them oldest first and honours `limit`; a missing file returns the `warehouse busy` note and no panic; a recorded Yahoo JSON fixture (three bars, one null) parses to two bars; the cache serves a second call within 60 seconds without a second fetch (inject the fetch closure and count calls).
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): daily bars from the warehouse and Yahoo intraday bars`.

### Task 11: Layout store

**Files:** Create `terminal/src/layouts.rs`.

**Interfaces:** `GET /api/layouts` returns `{"names":[…]}`; `GET /api/layouts/<name>` returns the stored JSON; `PUT /api/layouts/<name>` stores the request body (must be a JSON object, at most 200 KB); `DELETE /api/layouts/<name>`. Files live in `.athena/terminal/layouts/<name>.json`. A name matches `^[A-Za-z0-9 _-]{1,40}$`; anything else is 400, so no path can escape the folder. Presets A, B and C are shipped by the page, not the server.

- [ ] **Step 1: Failing tests**: name validator accepts `Mine-1`, refuses `../x`, `a/b`, an empty name and a 41-character name; put, list, get, delete round trip in a temp dir; a body that is not a JSON object is refused; a body over 200 KB is refused.
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): layout files with a safe name rule`.

### Task 12: Page scaffold and pure logic

**Files:** Create `terminal/web/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/command.ts`, `src/links.ts`, `src/layout.ts`, `src/ranges.ts`, `src/indicators.ts`, `src/theme.ts`, and tests under `test/`.

Install: `cd terminal/web && npm install --save-dev vite typescript vitest` and `npm install lightweight-charts dockview-core`; commit `package-lock.json`.

**Interfaces:**
- `command.ts`: `parse(input: string, ctx: { watchlist: string[]; layouts: string[] }): Command` where `Command = {kind:'symbol', symbol} | {kind:'chart', symbol} | {kind:'layout', name} | {kind:'add', symbol} | {kind:'remove', symbol} | {kind:'error', message}`. Rules: input is trimmed and upper-cased for symbols; `SBIN` is `symbol`; `SBIN CHART` is `chart`; `ADD TCS` and `REMOVE TCS`; a word equal (case-insensitive) to a saved layout name or to `A`, `B`, `C` after the word `LAYOUT` (or alone if it matches a saved name) is `layout`; a symbol must match `^[A-Z0-9&-]{1,20}$` else `error`; empty input is `error: 'type a symbol or a command'`.
- `links.ts`: link groups `none | red | yellow | green | blue`; `LinkBus` with `setSymbol(group, symbol)`, `symbolOf(group): string | null`, `subscribe(group, fn)`; `none` never notifies others.
- `layout.ts`: `LayoutModel { version: 1, dock: unknown /*dockview JSON*/, panels: Record<string, {type, group, symbol?, tf?, indicators?}> , watchlist: string[], theme }`, `serialize/deserialize` with validation (unknown panel types and a wrong `version` are rejected with an error), and `PRESETS = { A, B, C }` as `LayoutModel` factories (A: watchlist | chart | depth+quote, tape bottom; B: 2×2 charts + tape; C: quote, chart, then text panels are not in 4a so C is quote, chart, depth, watchlist, tape in a dense grid).
- `ranges.ts`: `RANGES` is a fixed list of `{label, tf, bars}`: `1D → 1m`, `5D → 5m`, `1M → 15m` (Yahoo gives only a few days of intraday history, so the chart shows a note "intraday history is limited to 5 days" for 1M and uses what exists), `3M → 1D (63 bars)`, `6M → 1D (126)`, `YTD → 1D (bars since 1 January, computed from today's date)`, `1Y → 1D (252)`, `5Y → 1D (1260)`, `All → 1D (every bar)`.
- `indicators.ts`: `sma(values, n)`, `ema(values, n)`, `bollinger(values, n, k)`, `rsi(values, n)`, `macd(values, fast, slow, signal)` returning arrays aligned to the input with `null` for warm-up positions.

- [ ] **Step 1: Failing tests** (vitest): the parser table above including `sbin chart`, `add tcs`, `layout b`, an invalid symbol `@@`, empty input; link bus (blue panel follows blue, `none` follows nobody, a change in one group does not move another); layout round trip, a wrong `version` throws, an unknown panel type throws, each preset has the panels the spec lists; ranges mapping; indicator numbers on small known series (SMA of `[1,2,3,4,5]` with n 3 is `[null,null,2,3,4]`; EMA n 3 of the same series seeds with the first SMA and equals `[null,null,2,3,4]`; RSI of a strictly rising series is 100; MACD histogram equals macd minus signal).
- [ ] **Step 2–5:** `npx vitest run` fails, implement, passes, commit `feat(terminal): page scaffold, command parser, links, layouts, ranges, indicators`.

### Task 13: Feed client and panels

**Files:** Create `terminal/web/src/feed.ts`, `src/panels/{watchlist,chart,depth,quote,tape}.ts`, `src/format.ts`.

**Interfaces:**
- `feed.ts`: `class Feed` with `connect()`, `watch(symbols: Set<string>)` (sends only when the union changed), `onQuote(fn)`, `onStatus(fn)`, automatic reconnect with backoff 0.5, 1, 2 … 10 s that re-sends the watch set, `latencyMs` computed as `Date.now() - hello.server_ms` offset-corrected on `hello` and exposed for the top bar.
- Every panel is `{ mount(el: HTMLElement, api: PanelApi): void; dispose(): void; setSymbol(sym: string): void }` where `PanelApi` gives `feed`, `links`, `group`, `symbolOf()`.
- `watchlist.ts`: columns Symbol, Last, Chg, Chg% (TradingView order), a collapsible `Indices` section with `NIFTY 50`, `NIFTY BANK`, an editable `Stocks` section, a coloured link flag per row, the selected row outlined, flash green or red for 250 ms on a change, `source` shown as a small dot (stream green, poll amber).
- `chart.ts`: Lightweight Charts candlesticks with volume as a histogram in the same pane (scale margins at the bottom), OHLC legend that follows the crosshair and shows the last bar when idle, a last-price tag and dotted line, the range row from `ranges.ts`, an indicator picker for the five indicators, history from `/api/bars`, live ticks update the last bar (`series.update`) and open a new bar when the tick's time crosses a bar boundary.
- `depth.ts`: five bids and five asks with quantity bars; `quote.ts`: large last price, change, O H L, volume, previous close, circuit band, 52-week range, source and age; `tape.ts`: a scrolling strip of watchlist moves.
- `format.ts`: `price(n)` two decimals with Indian digit grouping, `signed(n)`, `pct(n)`, `age(ms)`.

- [ ] **Step 1: Failing tests**: `format` table; `Feed.watch` sends once for an unchanged set and once for a changed one (fake WebSocket); a bar-boundary helper `barTime(tsSec, tf)` floors a timestamp to the bar start in IST (test 1m, 5m, 15m, 1D); a pure `applyTick(bars, tick, tf)` that updates or appends the last bar (tests: inside the bar updates high and low and close; across the boundary appends); watchlist row model sorts nothing and keeps insertion order.
- [ ] **Step 2–5:** fail, implement, pass, commit `feat(terminal): feed client and market panels`.

### Task 14: Shell, presets and run script

**Files:** Create `terminal/web/src/main.ts`, `terminal/web/src/style.css`, `scripts/terminal.ps1`.

**Behaviour:** top bar: layout menu (A, B, C, saved layouts, Save, Save as, Delete), command line (focus with `/` or `Ctrl+K`), feed health (`● Angel live · 38 ms`, `● polled`, `● connecting`), theme toggle; dockview area with all panels; panel header has a link-group dot (click to cycle none, red, yellow, green, blue) and a close button; `F1` focuses the watchlist group, `F2` the chart, `F3` depth/quote, `F4` the tape; fullscreen button; layout and watchlist autosave to `localStorage` under `athena.terminal.current` and also PUT to `/api/layouts/_last` every 5 seconds when changed; the dark theme is the default (amber labels, green and red moves) with a light theme matching the observed TradingView look.
`scripts/terminal.ps1`: builds the page if `terminal/web/dist` is older than `terminal/web/src`, builds the server in release mode, and starts `athena-terminal.exe --port 8700`, opening `http://127.0.0.1:8700`.

- [ ] **Step 1: Failing test** (vitest): `Layout menu model` — saving a layout and loading it restores panels and link groups; loading a preset replaces the dock; a corrupted saved layout falls back to preset A and reports it in the status line.
- [ ] **Step 2–5:** fail, implement, pass; run `npm run build`; commit `feat(terminal): the shell with docking, presets, saved layouts and the run script`.

### Task 15: Smoke test and market-hours check

**Files:** Create `terminal/web/e2e/smoke.spec.ts`, `playwright.config.ts`, `scripts/terminal-market-check.py`.

- [ ] **Step 1: Smoke test**: start `athena-terminal --fake-feed --port 8701`, open the page, and assert: all default panels are present; the watchlist shows prices that change within 2 seconds; clicking a watchlist row moves a linked chart to that symbol and leaves an unlinked chart where it is; saving a layout named `Smoke-1`, reloading and loading it restores the panel set; the command `ADD TCS` adds a row and `SBIN CHART` focuses the chart; the top bar reads `connecting` or `polled` or `live` and never `error`; the browser console has no errors.
- [ ] **Step 2: Market-hours check** (`scripts/terminal-market-check.py`): during 09:15 to 15:30 IST connects to the running server's `/ws`, watches 10 stocks for 120 seconds, and prints per stock the number of quotes received, the median and 95th-percentile gap between quotes, and the measured server-to-page delay from `hello`. It prints no secret and needs no broker setting itself.
- [ ] **Step 3:** run the smoke test, run the whole Rust and vitest suites, then the project's Python suite (`.venv/Scripts/python.exe -m pytest tests -q`) to prove nothing in Streamlit moved. Commit `test(terminal): smoke test with a fake feed and the market-hours check`.

### Task 16: Documentation and finish

**Files:** Modify `TRD.md` (a short "Terminal (4a)" subsection beside 2.20: what it is, how to run it, the safety rules, the measured numbers once known), the spec (record the Angel limits found in Task 6 and the measured tick rate), and the memory file.

- [ ] **Step 1:** write the TRD subsection and the spec result notes.
- [ ] **Step 2:** the market-hours check is run on the next trading morning; record its numbers in the spec before closing the phase.
- [ ] **Step 3:** use superpowers:finishing-a-development-branch.

---

## Self-review

- **Spec coverage:** server, login, feed, push, fallback, bars, layouts (Tasks 1 to 11); docking, links, presets, command line, hotkeys, panels, themes, range row, OHLC legend, last-price tag (Tasks 12 to 14); safety and tests (global constraints, Tasks 9, 15); documentation (16). Left out on purpose, as in the spec: orders, portfolio, alerts, tick storage, news, heatmap, analyst and backtest panels.
- **Known soft spots, to settle in the task that owns them:** Angel's documented symbol limit (Task 6, step 6); the warehouse's exact table name (Task 10, step 1); Yahoo's intraday history limits (Task 12 `ranges.ts`); the 1M mapping is the one place the spec's range row cannot be matched exactly with free intraday data, so the page says so on the chart.
