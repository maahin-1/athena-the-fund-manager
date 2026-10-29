//! The local server: the page, the push WebSocket and a small API. Local only: requests from other hosts or origins are refused.
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::body::Bytes;
use axum::extract::{Path, Query, Request, State};
use axum::http::{HeaderMap, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use tokio::sync::watch;
use tokio::time::{interval, MissedTickBehavior};
use tower_http::services::ServeDir;

use crate::angel::stream::FeedStatus;
use crate::analysis::analysis;
use crate::verdict_job::{symbol_ok as verdict_symbol_ok, StartError, VerdictJobs};
use crate::bars::{daily_bars_joined, yahoo_params, Bars, DEFAULT_LIMIT};
use crate::fundamentals::{FundError, FundamentalsJobs};
use crate::hub::Hub;
use crate::layouts::{LayoutError, LayoutStore};

pub const FRAME_INTERVAL: Duration = Duration::from_millis(16);
/// How long a request for intraday bars waits for a fetch that is running before it answers with what there is.
pub const INTRADAY_WAIT: Duration = Duration::from_secs(4);
/// How often a page's socket renews the contracts it named (they are let go after `deriv_quotes::WANTED_FOR` without a renewal).
pub const DERIV_RENEW: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct AppState {
    pub hub: Hub,
    pub status: watch::Sender<FeedStatus>,
    pub port: u16,
    pub web_dir: PathBuf,
    pub bars: Arc<Bars>,
    pub layouts: Arc<LayoutStore>,
    pub verdicts: VerdictJobs,
    pub analyse: Arc<crate::analyse_worker::AnalyseWorker>,
    pub fundamentals: Arc<FundamentalsJobs>,
    pub flow_board: Arc<crate::flow_board::FlowBoard>,
    pub chart_explain: Arc<crate::chart_explain::ChartExplain>,
    pub sentiment: Arc<crate::sentiment::SentimentJobs>,
    pub indices: crate::indices::IndexStore,
    pub search: Arc<crate::search::SearchCache>,
    pub fund_fetch: Arc<crate::fund_fetch::FundFetch>,
    /// Documents the page keeps on the server beside the layouts (the watchlists), one fixed name each.
    pub lists: Arc<LayoutStore>,
    /// The Python indicator files the user placed in a folder, switched on one by one.
    pub pyind: Arc<crate::pyind::PyInd>,
    /// The futures and options contracts of NFO, BFO and MCX, from the instrument list.
    pub derivs: crate::derivs::DerivsStore,
    /// What the stream last said about the futures and options pages asked for.
    pub deriv_quotes: crate::deriv_quotes::DerivQuotes,
    /// False under the fake feed: there is no broker stream to ask, and the quotes route says so.
    pub live_derivs: bool,
}

/// An Origin header is acceptable only when it is this server's own page.
pub fn origin_ok(origin: Option<&str>, port: u16) -> bool {
    matches!(origin, Some(o) if o == format!("http://127.0.0.1:{port}") || o == format!("http://localhost:{port}"))
}

/// The Host header must name this server; this blocks a hostile web page that points its own name at 127.0.0.1.
pub fn host_ok(host: Option<&str>, port: u16) -> bool {
    matches!(host, Some(h) if h == format!("127.0.0.1:{port}") || h == format!("localhost:{port}"))
}

/// `Sec-Fetch-Site` as a browser sets it: absent for a tool that is not a browser; `same-origin` for this page's own requests; `none` for one the person typed. Anything else is another site.
pub fn fetch_site_ok(value: Option<&str>) -> bool {
    matches!(value, None | Some("same-origin") | Some("none"))
}

fn header<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|v| v.to_str().ok())
}

/// `/ws` needs a good Origin (browsers always send one for a WebSocket); `/api/*` accepts a good Origin or none (a same-origin GET carries
/// none). Both need a good Host.
async fn guard(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let path = request.uri().path().to_string();
    if path == "/ws" || path.starts_with("/api/") {
        let headers = request.headers();
        let origin = header(headers, "origin");
        let origin_fine = match origin {
            Some(_) => origin_ok(origin, state.port),
            // a same-origin GET carries no Origin; a request a web page makes to us from another site says so in Sec-Fetch-Site (a blind `<img src>` to a route that costs model credits)
            None => path != "/ws" && fetch_site_ok(header(headers, "sec-fetch-site")),
        };
        if !origin_fine || !host_ok(header(headers, "host"), state.port) {
            return StatusCode::FORBIDDEN.into_response();
        }
    }
    let mut response = next.run(request).await;
    response.headers_mut().insert("content-security-policy", axum::http::HeaderValue::from_static("frame-ancestors 'none'"));
    response.headers_mut().insert("x-content-type-options", axum::http::HeaderValue::from_static("nosniff"));
    response
}

fn status_json(status: &FeedStatus) -> serde_json::Value {
    match status {
        FeedStatus::Connecting => serde_json::json!({"feed": "connecting"}),
        FeedStatus::On => serde_json::json!({"feed": "on"}),
        FeedStatus::Off(reason) => serde_json::json!({"feed": "off", "reason": reason}),
    }
}

async fn api_status(State(state): State<AppState>) -> Json<serde_json::Value> {
    let mut v = status_json(&state.status.borrow());
    v["clients"] = state.hub.client_count().into();
    v["live"] = state.live_derivs.into(); // false under the fake feed, whose quotes look like the stream's: an order desk must not price from them
    // how late the broker's ticks arrive: receipt time less the exchange's own time on the tick, over the recent ticks (null before any tick)
    v["stream_lag_ms"] = serde_json::to_value(state.hub.lag()).unwrap_or(serde_json::Value::Null);
    Json(v)
}

/// The background data jobs: what the supervisor last did and every job's status file (read from disk, so it also shows a job started by hand).
async fn api_jobs() -> Json<serde_json::Value> {
    Json(crate::jobs::read_jobs(std::path::Path::new(".athena/supervisor.json"), std::path::Path::new(".athena/status")))
}

#[derive(serde::Deserialize)]
struct BarsQuery {
    symbol: String,
    tf: Option<String>,
    limit: Option<usize>,
    /// `adj=0` asks for the bars as printed, without the split and bonus adjustment.
    adj: Option<u8>,
}

fn symbol_ok(symbol: &str) -> bool {
    // the first character is a letter, a digit or the `^` of an index: never `-`, which a command line would read as a flag
    symbol.chars().next().is_some_and(|c| c.is_ascii_alphanumeric() || c == '^') && symbol.len() <= 24 && symbol.chars().all(|c| c.is_ascii_alphanumeric() || " &-^".contains(c))
}

/// `note` followed by the sentence for each joined earlier symbol.
fn join_notes(note: String, joined: &[crate::bars::Joined]) -> String {
    std::iter::once(note).chain(joined.iter().map(|j| j.note.clone())).filter(|n| !n.is_empty()).collect::<Vec<_>>().join(" ")
}

async fn api_bars(State(state): State<AppState>, Query(q): Query<BarsQuery>) -> Response {
    if !symbol_ok(&q.symbol) {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let symbol = q.symbol.to_ascii_uppercase();
    let tf = q.tf.unwrap_or_else(|| "1D".to_string());
    let (bars, source, note) = if tf == "1D" {
        let path = state.bars.warehouse.clone();
        let (sym, limit) = (symbol.clone(), q.limit.unwrap_or(DEFAULT_LIMIT).clamp(1, 5000));
        // a stock's prices are adjusted for splits and bonuses (as Streamlit's were); an index has none, and if the adjustment cannot run the raw bars are shown and say so
        let raw_wanted = q.adj == Some(0);
        let adjusted = if raw_wanted || symbol.to_ascii_uppercase().starts_with("NIFTY ") { None } else { state.bars.adjusted.get(&symbol).await };
        match adjusted {
            Some(a) => {
                let from = a.bars.len().saturating_sub(limit);
                // the helper has already joined a renamed company's earlier symbols; the page is told which
                let note = join_notes(if a.flags.is_empty() { String::new() } else { format!("Price breaks no record explains: {}.", a.flags.join(", ")) }, &a.joined);
                return Json(serde_json::json!({"symbol": symbol, "tf": tf, "bars": &a.bars[from..], "source": "warehouse, adjusted", "note": note})).into_response();
            }
            None => match tokio::task::spawn_blocking(move || daily_bars_joined(&path, &sym, limit)).await {
                Ok(Ok((bars, joined))) => {
                    let stock = !symbol.to_ascii_uppercase().starts_with("NIFTY ");
                    let note = if raw_wanted && stock { "Prices are as printed, not adjusted for splits and bonuses.".to_string() } else if stock { "Split and bonus adjustment is unavailable; prices are as printed.".to_string() } else { String::new() };
                    (bars, if raw_wanted { "warehouse, raw" } else { "warehouse" }, join_notes(note, &joined))
                }
                _ => (Vec::new(), if raw_wanted { "warehouse, raw" } else { "warehouse" }, "warehouse busy".to_string()),
            },
        }
    } else if yahoo_params(&tf).is_some() {
        // a first request waits a moment for the history rather than answering "loading" and having the page ask again
        let (bars, loading) = state.bars.intraday.get_waiting(&symbol, &tf, INTRADAY_WAIT).await;
        let live = crate::live_bars::width(&tf).map(|w| state.hub.live_bars().bars(&symbol, w)).unwrap_or_default();
        let note = if loading && live.is_empty() {
            "loading".to_string()
        } else if bars.is_empty() && live.is_empty() {
            format!("No {tf} bars for {symbol}: the free intraday source has none for it. Daily bars only.")
        } else {
            String::new()
        };
        let source = if live.is_empty() { "yahoo" } else { "yahoo, with today's bars from the live stream" };
        (crate::live_bars::merge(bars, &live), source, note)
    } else {
        return StatusCode::BAD_REQUEST.into_response();
    };
    Json(serde_json::json!({"symbol": symbol, "tf": tf, "bars": bars, "source": source, "note": note})).into_response()
}

#[derive(serde::Deserialize)]
struct AnalyseBody {
    query: String,
    #[serde(default)]
    relatives: bool,
}

/// The live analysis of one instrument (all the specialists, as Streamlit's Analyse tab ran them). It calls the language model and can take a minute or more.
async fn api_analyse(State(state): State<AppState>, Json(body): Json<AnalyseBody>) -> Response {
    use crate::analyse_worker::AnalyseError;
    match state.analyse.view(&body.query, body.relatives).await {
        Ok(view) => Json(view).into_response(),
        Err(AnalyseError::BadQuery) => StatusCode::BAD_REQUEST.into_response(),
        Err(AnalyseError::Unavailable) => (StatusCode::BAD_GATEWAY, Json(serde_json::json!({"error": "the analysis service did not answer; try again"}))).into_response(),
        Err(AnalyseError::Failed(message)) => (StatusCode::UNPROCESSABLE_ENTITY, Json(serde_json::json!({"error": message}))).into_response(),
    }
}

#[derive(serde::Deserialize)]
struct BacktestBody {
    symbol: String,
    /// The user's own strategy as a tree; absent for the built-in rules.
    #[serde(default)]
    strategy: Option<serde_json::Value>,
}

/// The built-in rules replayed over the symbol's history (up to eight years), each against buy and hold. Takes a few seconds to a minute.
async fn api_backtest(State(state): State<AppState>, Json(body): Json<BacktestBody>) -> Response {
    use crate::analyse_worker::AnalyseError;
    match state.analyse.backtest(&body.symbol, body.strategy).await {
        Ok(view) => Json(view).into_response(),
        Err(AnalyseError::BadQuery) => StatusCode::BAD_REQUEST.into_response(),
        Err(AnalyseError::Unavailable) => (StatusCode::BAD_GATEWAY, Json(serde_json::json!({"error": "the analysis service did not answer; try again"}))).into_response(),
        Err(AnalyseError::Failed(message)) => (StatusCode::UNPROCESSABLE_ENTITY, Json(serde_json::json!({"error": message}))).into_response(),
    }
}

#[derive(serde::Deserialize)]
struct CalendarQuery {
    from: Option<String>,
    to: Option<String>,
}

/// The range asked for, or today (the India date) and the next 30 days for what was not given.
fn calendar_range(today: chrono::NaiveDate, from: Option<String>, to: Option<String>) -> (String, String) {
    (from.unwrap_or_else(|| today.to_string()), to.unwrap_or_else(|| (today + chrono::Duration::days(30)).to_string()))
}

/// Board meetings and corporate actions for a range of dates (default: today and the next 30 days).
async fn api_calendar(State(state): State<AppState>, Query(q): Query<CalendarQuery>) -> Response {
    let (from, to) = calendar_range(crate::derivs::today_ist(), q.from, q.to);
    let path = state.bars.warehouse.clone();
    match tokio::task::spawn_blocking(move || crate::calendar::calendar(&path, &from, &to)).await {
        Ok(Ok(body)) => Json(body).into_response(),
        Ok(Err(crate::calendar::CalendarError::BadRange)) => (StatusCode::BAD_REQUEST, "the dates must be YYYY-MM-DD, in order, at most 366 days apart").into_response(),
        _ => (StatusCode::SERVICE_UNAVAILABLE, "the warehouse is busy").into_response(),
    }
}

#[derive(serde::Deserialize)]
struct DerivsQuery {
    underlying: Option<String>,
    expiry: Option<String>,
    segment: Option<String>,
}

/// An underlying's name as the list spells it: letters, digits, `&`, `-` and `_`, at most 24 characters.
fn derivs_name(q: &DerivsQuery) -> Option<String> {
    let name = q.underlying.as_deref()?.trim().to_ascii_uppercase();
    (!name.is_empty() && name.len() <= 24 && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '&' | '-' | '_'))).then_some(name)
}

fn derivs_reply(result: Result<Result<serde_json::Value, crate::derivs::DerivsError>, crate::derivs::NotReady>) -> Response {
    use crate::derivs::{DerivsError::*, NotReady};
    match result {
        Ok(Ok(body)) => Json(body).into_response(),
        Ok(Err(UnknownUnderlying)) => (StatusCode::NOT_FOUND, "no contracts for that underlying").into_response(),
        Ok(Err(UnknownExpiry)) => (StatusCode::NOT_FOUND, "that underlying has no contracts expiring that day").into_response(),
        Ok(Err(BadDate)) => (StatusCode::BAD_REQUEST, "the expiry must be a date, YYYY-MM-DD").into_response(),
        Ok(Err(TooManyStrikes)) => (StatusCode::BAD_REQUEST, format!("more than {} strikes in that expiry", crate::derivs::MAX_STRIKES)).into_response(),
        Err(NotReady::Loading) => (StatusCode::SERVICE_UNAVAILABLE, "the contract list is loading").into_response(),
        Err(NotReady::Failed(why)) => (StatusCode::SERVICE_UNAVAILABLE, why).into_response(),
    }
}

async fn api_derivs_underlyings(State(state): State<AppState>, Query(q): Query<DerivsQuery>) -> Response {
    let segment = q.segment.as_deref().unwrap_or("ALL").to_ascii_uppercase();
    if segment != "ALL" && !crate::derivs::SEGMENTS.contains(&segment.as_str()) {
        return (StatusCode::BAD_REQUEST, "the segment must be NFO, BFO, MCX or ALL").into_response();
    }
    derivs_reply(state.derivs.with(|t| Ok(t.underlyings(&segment))))
}

async fn api_derivs_expiries(State(state): State<AppState>, Query(q): Query<DerivsQuery>) -> Response {
    let Some(name) = derivs_name(&q) else { return (StatusCode::BAD_REQUEST, "give an underlying").into_response() };
    derivs_reply(state.derivs.with(|t| t.expiries(&name)))
}

async fn api_derivs_chain(State(state): State<AppState>, Query(q): Query<DerivsQuery>) -> Response {
    let (Some(name), Some(expiry)) = (derivs_name(&q), q.expiry.clone()) else { return (StatusCode::BAD_REQUEST, "give an underlying and an expiry").into_response() };
    derivs_reply(state.derivs.with(|t| t.chain(&name, &expiry)))
}

#[derive(serde::Deserialize)]
struct QuotesQuery {
    tokens: Option<String>,
}

/// Live quotes for the contracts asked about (`tokens=NFO:123,MCX:45`). It marks them as wanted, so the stream subscribes them, and answers with what the stream has
/// said so far (a contract it has not yet spoken about is simply absent: ask again in a moment). `live` says whether there is a stream at all; with none (the
/// fake feed, or the broker not connected) it answers `live: false` and the page keeps its synthetic prices.
async fn api_derivs_quotes(State(state): State<AppState>, Query(q): Query<QuotesQuery>) -> Response {
    let keys = match crate::deriv_quotes::parse_keys(q.tokens.as_deref().unwrap_or("")) {
        Ok(k) => k,
        Err(why) => return (StatusCode::BAD_REQUEST, why).into_response(),
    };
    let live = state.live_derivs && matches!(*state.status.borrow(), FeedStatus::On);
    if !live {
        return Json(serde_json::json!({ "live": false, "quotes": [] })).into_response();
    }
    state.deriv_quotes.want(&keys, std::time::Instant::now());
    Json(serde_json::json!({ "live": true, "quotes": state.deriv_quotes.get(&keys) })).into_response()
}

async fn api_derivs_futures(State(state): State<AppState>, Query(q): Query<DerivsQuery>) -> Response {
    let Some(name) = derivs_name(&q) else { return (StatusCode::BAD_REQUEST, "give an underlying").into_response() };
    derivs_reply(state.derivs.with(|t| t.futures(&name)))
}

/// Answers kept in memory: the market screen until the warehouse file changes, the fundamentals counts for `FUND_STATUS_FOR`. Keyed by the file, as
/// tests run several servers in one process.
type Remembered = std::sync::Mutex<std::collections::HashMap<PathBuf, (SystemTime, std::time::Instant, Arc<serde_json::Value>)>>;
static SCREEN_ANSWERS: std::sync::LazyLock<Remembered> = std::sync::LazyLock::new(Default::default);
static FUND_COUNTS: std::sync::LazyLock<Remembered> = std::sync::LazyLock::new(Default::default);
/// The fundamentals store is written for hours while a backfill runs, so its counts are kept for a minute rather than until the file changes.
pub const FUND_STATUS_FOR: Duration = Duration::from_secs(60);

fn modified(path: &std::path::Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

async fn api_screen(State(state): State<AppState>) -> Response {
    let path = state.bars.warehouse.clone();
    let stamp = modified(&path);
    if let Some(stamp) = stamp {
        if let Some((at, _, body)) = SCREEN_ANSWERS.lock().unwrap().get(&path) {
            if *at == stamp {
                return Json(body.as_ref().clone()).into_response();
            }
        }
    }
    let read = path.clone();
    match tokio::task::spawn_blocking(move || crate::analysis::screen_all(&read)).await {
        Ok(Ok(body)) => {
            if let Some(stamp) = stamp {
                SCREEN_ANSWERS.lock().unwrap().insert(path, (stamp, std::time::Instant::now(), Arc::new(body.clone())));
            }
            Json(body).into_response()
        }
        _ => (StatusCode::SERVICE_UNAVAILABLE, "the warehouse is busy").into_response(),
    }
}

async fn api_known(State(state): State<AppState>, Query(q): Query<SymbolQuery>) -> Response {
    let symbol = q.symbol.to_ascii_uppercase();
    if !symbol_ok(&symbol) {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let path = state.bars.warehouse.clone();
    match tokio::task::spawn_blocking(move || crate::analysis::known(&path, &symbol)).await {
        Ok(Ok(body)) => Json(body).into_response(),
        _ => (StatusCode::SERVICE_UNAVAILABLE, "the warehouse is busy").into_response(),
    }
}

#[derive(serde::Deserialize)]
struct SymbolQuery {
    symbol: String,
}

async fn api_analysis(State(state): State<AppState>, Query(q): Query<SymbolQuery>) -> Response {
    let symbol = q.symbol.to_ascii_uppercase();
    if !verdict_symbol_ok(&symbol) {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let path = state.bars.warehouse.clone();
    match tokio::task::spawn_blocking(move || analysis(&path, &symbol)).await {
        Ok(Ok(body)) => Json(body).into_response(),
        _ => (StatusCode::SERVICE_UNAVAILABLE, "the warehouse is busy").into_response(),
    }
}

#[derive(serde::Deserialize)]
struct FundQuery {
    basis: Option<String>,
}

fn fund_error(e: FundError) -> Response {
    let (status, message) = match e {
        FundError::BadSymbol | FundError::BadBasis => return StatusCode::BAD_REQUEST.into_response(),
        FundError::NoData => (StatusCode::NOT_FOUND, "no statements stored".to_string()),
        FundError::Busy => (StatusCode::SERVICE_UNAVAILABLE, "the fundamentals store is busy; try again in a moment".to_string()),
        FundError::Failed(m) => (StatusCode::BAD_GATEWAY, m),
    };
    (status, Json(serde_json::json!({"error": message}))).into_response()
}

/// One company's statements, key figures, ratios and growth, from the stored filings (see `athena.fundamentals.page`).
async fn api_fund(State(state): State<AppState>, Path(symbol): Path<String>, Query(q): Query<FundQuery>) -> Response {
    let basis = q.basis.unwrap_or_else(|| "consolidated".to_string());
    match state.fundamentals.page(&symbol.to_ascii_uppercase(), &basis).await {
        Ok(doc) => Json(doc).into_response(),
        Err(e) => fund_error(e),
    }
}

/// One company's value range, cost of capital and assumptions (see `athena.valuation.page`).
async fn api_valuation(State(state): State<AppState>, Path(symbol): Path<String>, Query(q): Query<FundQuery>) -> Response {
    let basis = q.basis.unwrap_or_else(|| "consolidated".to_string());
    match state.fundamentals.valuation(&symbol.to_ascii_uppercase(), &basis).await {
        Ok(doc) => Json(doc).into_response(),
        Err(e) => fund_error(e),
    }
}

/// The same company as an Excel workbook.
async fn api_fund_export(State(state): State<AppState>, Path(symbol): Path<String>, Query(q): Query<FundQuery>) -> Response {
    let basis = q.basis.unwrap_or_else(|| "consolidated".to_string());
    let symbol = symbol.to_ascii_uppercase();
    match state.fundamentals.export(&symbol, &basis).await {
        Ok(bytes) => (
            [
                ("content-type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet".to_string()),
                ("content-disposition", format!("attachment; filename=\"{symbol}-{basis}.xlsx\"")),
            ],
            bytes,
        )
            .into_response(),
        Err(e) => fund_error(e),
    }
}

#[derive(serde::Deserialize)]
struct SentimentQuery {
    tone: Option<String>,
}

/// What the exchange, the press and (when set up) Reddit said about one stock, and on request a model's reading of the news (see `athena.sentiment.page`). Display only.
async fn api_sentiment(State(state): State<AppState>, Path(symbol): Path<String>, Query(q): Query<SentimentQuery>) -> Response {
    use crate::sentiment::SentimentError;
    let tone = match q.tone.as_deref() {
        None | Some("0") => false,
        Some("1") => true,
        Some(_) => return (StatusCode::BAD_REQUEST, "tone must be 0 or 1").into_response(),
    };
    match state.sentiment.page(&symbol.to_ascii_uppercase(), tone).await {
        Ok(doc) => Json(doc).into_response(),
        Err(SentimentError::BadSymbol) => (StatusCode::BAD_REQUEST, "not a symbol").into_response(),
        Err(SentimentError::Busy) => (StatusCode::TOO_MANY_REQUESTS, "two sentiment pages are already running; try again in a moment").into_response(),
        Err(SentimentError::Failed(why)) => (StatusCode::BAD_GATEWAY, why).into_response(),
    }
}

/// A plain-language reading of the chart on the page, from a facts sheet worked out in code (see `athena.charting.explain`). A POST: it can cost a model call.
async fn api_chart_explain(State(state): State<AppState>, body: Bytes) -> Response {
    use crate::chart_explain::ExplainError;
    match state.chart_explain.explain(&body).await {
        Ok(doc) => Json(doc).into_response(),
        Err(ExplainError::BadRequest) => (StatusCode::BAD_REQUEST, "the request must be a JSON object of at most 400 KB").into_response(),
        Err(ExplainError::Busy) => (StatusCode::TOO_MANY_REQUESTS, "another explanation is running; try again in a moment").into_response(),
        Err(ExplainError::Failed(why)) => (StatusCode::BAD_GATEWAY, why).into_response(),
    }
}

/// The futures and options board: every underlying with futures on the latest day of the derivatives history, with what the detectors found (see `athena.flow.board`).
async fn api_flow_board(State(state): State<AppState>) -> Response {
    use crate::flow_board::BoardError;
    match state.flow_board.board().await {
        Ok(doc) => Json(doc).into_response(),
        Err(BoardError::NoData) => (StatusCode::NOT_FOUND, "no derivatives history is stored yet").into_response(),
        Err(BoardError::Busy) => (StatusCode::SERVICE_UNAVAILABLE, "the derivatives file is being loaded; try again in a moment").into_response(),
        Err(BoardError::Failed(why)) => (StatusCode::BAD_GATEWAY, why).into_response(),
    }
}

async fn api_fund_status(State(state): State<AppState>) -> Response {
    let key = state.fundamentals.store_path().to_path_buf();
    if let Some((_, at, body)) = FUND_COUNTS.lock().unwrap().get(&key) {
        if at.elapsed() < FUND_STATUS_FOR {
            return Json(body.as_ref().clone()).into_response();
        }
    }
    let jobs = state.fundamentals.clone();
    match tokio::task::spawn_blocking(move || jobs.status()).await {
        Ok(Ok(body)) => {
            FUND_COUNTS.lock().unwrap().insert(key, (SystemTime::now(), std::time::Instant::now(), Arc::new(body.clone())));
            Json(body).into_response()
        }
        Ok(Err(e)) => fund_error(e),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

async fn verdict_start(State(state): State<AppState>, Path(symbol): Path<String>) -> Response {
    match state.verdicts.start(&symbol.to_ascii_uppercase()) {
        Ok(()) => StatusCode::ACCEPTED.into_response(),
        Err(StartError::BadSymbol) => StatusCode::BAD_REQUEST.into_response(),
        Err(StartError::Busy) => StatusCode::CONFLICT.into_response(),
    }
}

async fn verdict_status(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(state.verdicts.status())
}

fn layout_status(e: LayoutError) -> StatusCode {
    match e {
        LayoutError::BadName | LayoutError::NotAnObject => StatusCode::BAD_REQUEST,
        LayoutError::TooBig => StatusCode::PAYLOAD_TOO_LARGE,
        LayoutError::Missing => StatusCode::NOT_FOUND,
        LayoutError::Io => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

async fn fund_fetch_status(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(state.fund_fetch.status())
}

/// Start fetching one company's filings (statements, shareholding, documents). 409 when another company is being fetched.
async fn fund_fetch_start(State(state): State<AppState>, Path(symbol): Path<String>) -> Response {
    use crate::fund_fetch::StartError;
    match state.fund_fetch.start(&symbol.to_ascii_uppercase()) {
        Ok(()) => (StatusCode::ACCEPTED, Json(state.fund_fetch.status())).into_response(),
        Err(StartError::BadSymbol) => StatusCode::BAD_REQUEST.into_response(),
        Err(StartError::Busy(other)) => (StatusCode::CONFLICT, Json(serde_json::json!({"error": "another company is being fetched", "symbol": other}))).into_response(),
    }
}

#[derive(serde::Deserialize)]
struct SearchQuery {
    q: String,
    limit: Option<usize>,
    /// Comma-separated kinds to keep (`stock`, `etf`, `sme`, `index`); all when absent.
    kinds: Option<String>,
}

/// Instruments matching what is typed: symbols, names, ISINs, index names. Answers at once from memory; 503 only when the warehouse has never been readable.
async fn api_search(State(state): State<AppState>, Query(q): Query<SearchQuery>) -> Response {
    if q.q.chars().count() > crate::search::MAX_QUERY {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let limit = q.limit.unwrap_or(10).clamp(1, 25);
    let (cache, names, text) = (state.search.clone(), state.indices.names(), q.q.clone());
    let kinds: Vec<String> = q.kinds.as_deref().unwrap_or("").split(',').map(|k| k.trim().to_string()).filter(|k| !k.is_empty()).collect();
    match tokio::task::spawn_blocking(move || cache.get(&names).map(|i| i.search(&text, limit, &kinds))).await {
        Ok(Some(hits)) => Json(serde_json::json!({"q": q.q, "results": hits})).into_response(),
        _ => (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({"error": "the instrument list is not available"}))).into_response(),
    }
}

async fn api_indices(State(state): State<AppState>) -> Response {
    match state.indices.json() {
        Some(v) => Json(v).into_response(),
        None => (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({"error": "no index snapshot yet"}))).into_response(),
    }
}

const WATCHLISTS: &str = "watchlists";
const SCREENS: &str = "screens";
const FORMULAS: &str = "formulas";
const STRATEGIES: &str = "strategies";
const NOTEBOOK: &str = "notebook";

/// The name a symbol's drawings are kept under in the lists folder: `dr-` and the upper-cased symbol, where letters, digits, space and `-` stay and every other character
/// (including `_`) becomes `_` and two hex digits (`M&M` is `dr-M_26M`). None for a symbol outside `A-Z 0-9 & ^ space -` or one whose name would pass the store's 40 characters.
pub fn drawing_name(symbol: &str) -> Option<String> {
    let upper = symbol.trim().to_ascii_uppercase();
    if upper.is_empty() || upper.chars().count() > 24 {
        return None;
    }
    let mut out = String::from("dr-");
    for c in upper.chars() {
        match c {
            'A'..='Z' | '0'..='9' | ' ' | '-' => out.push(c),
            '&' | '^' | '_' => out.push_str(&format!("_{:02x}", c as u32)),
            _ => return None,
        }
    }
    (out.chars().count() <= 40).then_some(out)
}

async fn drawings_get(State(state): State<AppState>, Path(symbol): Path<String>) -> Response {
    let Some(name) = drawing_name(&symbol) else { return StatusCode::BAD_REQUEST.into_response() };
    match state.lists.get(&name) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(LayoutError::Missing) => StatusCode::NO_CONTENT.into_response(), // none drawn yet is not an error: the browser would log a 404
        Err(e) => layout_status(e).into_response(),
    }
}

async fn drawings_put(State(state): State<AppState>, Path(symbol): Path<String>, body: Bytes) -> Response {
    let Some(name) = drawing_name(&symbol) else { return StatusCode::BAD_REQUEST.into_response() };
    match state.lists.put(&name, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

/// The notebook: one document of the owner's notes, kept as the watchlists are (the page validates what is in it). It stays in the terminal's own folder on this machine.
async fn notebook_get(State(state): State<AppState>) -> Response {
    match state.lists.get(NOTEBOOK) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(LayoutError::Missing) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn notebook_put(State(state): State<AppState>, body: Bytes) -> Response {
    match state.lists.put(NOTEBOOK, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn watchlists_get(State(state): State<AppState>) -> Response {
    match state.lists.get(WATCHLISTS) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(LayoutError::Missing) => StatusCode::NO_CONTENT.into_response(), // none saved yet is not an error: the browser would log a 404
        Err(e) => layout_status(e).into_response(),
    }
}

async fn watchlists_put(State(state): State<AppState>, body: Bytes) -> Response {
    match state.lists.put(WATCHLISTS, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

/// The saved screens: one document, kept as the watchlists are (the page validates what is in it).
async fn screens_get(State(state): State<AppState>) -> Response {
    match state.lists.get(SCREENS) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(LayoutError::Missing) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn screens_put(State(state): State<AppState>, body: Bytes) -> Response {
    match state.lists.put(SCREENS, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

/// The user's saved formulas: one document, as the screens are (the page validates what is in it).
async fn formulas_get(State(state): State<AppState>) -> Response {
    match state.lists.get(FORMULAS) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(LayoutError::Missing) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn formulas_put(State(state): State<AppState>, body: Bytes) -> Response {
    match state.lists.put(FORMULAS, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

/// The user's saved strategies (the text of each entry and exit and its risk options): one document, validated by the page.
async fn strategies_get(State(state): State<AppState>) -> Response {
    match state.lists.get(STRATEGIES) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(LayoutError::Missing) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn strategies_put(State(state): State<AppState>, body: Bytes) -> Response {
    match state.lists.put(STRATEGIES, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

#[derive(serde::Deserialize)]
struct PyIndEnable {
    file: String,
    sha256: String,
}

#[derive(serde::Deserialize)]
struct PyIndDisable {
    file: String,
}

#[derive(serde::Deserialize)]
struct PyIndRun {
    file: String,
    bars: serde_json::Value,
}

fn pyind_status(e: crate::pyind::PyIndError) -> Response {
    use crate::pyind::PyIndError::*;
    match e {
        Bad(m) => (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error": m}))).into_response(),
        NotEnabled => (StatusCode::FORBIDDEN, Json(serde_json::json!({"error": "this indicator is not switched on, or the file changed after it was"}))).into_response(),
        Failed(m) => (StatusCode::UNPROCESSABLE_ENTITY, Json(serde_json::json!({"error": m}))).into_response(),
        TimedOut => (StatusCode::GATEWAY_TIMEOUT, Json(serde_json::json!({"error": "the indicator took too long and was stopped"}))).into_response(),
        Unavailable => (StatusCode::BAD_GATEWAY, Json(serde_json::json!({"error": "the indicator runner could not be started"}))).into_response(),
    }
}

/// The Python indicator files in the folder and whether each is switched on (a file is read, never run, to be listed).
async fn pyind_list(State(state): State<AppState>) -> Response {
    match state.pyind.list().await {
        Ok(body) => Json(body).into_response(),
        Err(e) => pyind_status(e),
    }
}

async fn pyind_enable(State(state): State<AppState>, Json(body): Json<PyIndEnable>) -> Response {
    match state.pyind.enable(&body.file, &body.sha256).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => pyind_status(e),
    }
}

async fn pyind_disable(State(state): State<AppState>, Json(body): Json<PyIndDisable>) -> Response {
    match state.pyind.disable(&body.file).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => pyind_status(e),
    }
}

async fn pyind_run(State(state): State<AppState>, Json(body): Json<PyIndRun>) -> Response {
    match state.pyind.run(&body.file, &body.bars).await {
        Ok(lines) => Json(lines).into_response(),
        Err(e) => pyind_status(e),
    }
}

async fn layouts_list(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(serde_json::json!({"names": state.layouts.list()}))
}

async fn layouts_get(State(state): State<AppState>, Path(name): Path<String>) -> Response {
    match state.layouts.get(&name) {
        Ok(body) => ([("content-type", "application/json")], body).into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn layouts_put(State(state): State<AppState>, Path(name): Path<String>, body: Bytes) -> Response {
    match state.layouts.put(&name, &body) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn layouts_delete(State(state): State<AppState>, Path(name): Path<String>) -> Response {
    match state.layouts.delete(&name) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => layout_status(e).into_response(),
    }
}

async fn ws_upgrade(State(state): State<AppState>, upgrade: WebSocketUpgrade) -> Response {
    upgrade.on_upgrade(move |socket| client_loop(socket, state))
}

fn unix_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

async fn client_loop(mut socket: WebSocket, state: AppState) {
    let hub = state.hub.clone();
    let id = hub.register_client();
    let mut status = state.status.subscribe();
    let hello = serde_json::json!({"type": "hello", "server_ms": unix_ms()}).to_string();
    let mut first = status_json(&status.borrow_and_update());
    first["type"] = "status".into();
    if socket.send(Message::Text(hello.into())).await.is_err() || socket.send(Message::Text(first.to_string().into())).await.is_err() {
        hub.drop_client(id);
        return;
    }
    let mut tick = interval(FRAME_INTERVAL);
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    // the futures and options this page named on its socket: each change is pushed with the share quotes, and the wants are renewed while the page holds them
    let mut deriv_keys: Vec<crate::deriv_quotes::Key> = Vec::new();
    let mut deriv_sent: std::collections::HashMap<crate::deriv_quotes::Key, u64> = std::collections::HashMap::new();
    let mut renewed = std::time::Instant::now();
    loop {
        tokio::select! {
            _ = tick.tick() => {
                if let Some(frame) = hub.frame(id) {
                    if socket.send(Message::Text(frame.json().into())).await.is_err() { break; }
                }
                if !deriv_keys.is_empty() && state.live_derivs && matches!(*state.status.borrow(), FeedStatus::On) {
                    if renewed.elapsed() >= DERIV_RENEW {
                        state.deriv_quotes.want(&deriv_keys, std::time::Instant::now());
                        renewed = std::time::Instant::now();
                    }
                    let rows = state.deriv_quotes.changed(&deriv_keys, &mut deriv_sent);
                    if !rows.is_empty() {
                        let text = serde_json::json!({"type": "d", "sm": unix_ms(), "d": rows}).to_string();
                        if socket.send(Message::Text(text.into())).await.is_err() { break; }
                    }
                }
            }
            changed = status.changed() => {
                if changed.is_err() { break; }
                let mut v = status_json(&status.borrow_and_update());
                v["type"] = "status".into();
                if socket.send(Message::Text(v.to_string().into())).await.is_err() { break; }
            }
            incoming = socket.recv() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(text.as_str()) {
                        if v.get("type").and_then(|t| t.as_str()) == Some("watch") {
                            let symbols: Vec<String> = v.get("symbols").and_then(|s| s.as_array())
                                .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).take(200).collect()).unwrap_or_default();
                            hub.watch(id, &symbols);
                        } else if v.get("type").and_then(|t| t.as_str()) == Some("derivs") {
                            // a list the server cannot read names nothing: the page keeps polling the quotes route, as before
                            let keys = crate::deriv_quotes::parse_socket_keys(v.get("keys").and_then(|k| k.as_str()).unwrap_or("")).unwrap_or_default();
                            deriv_sent.retain(|k, _| keys.contains(k));
                            state.deriv_quotes.want(&keys, std::time::Instant::now());
                            renewed = std::time::Instant::now();
                            deriv_keys = keys;
                        }
                    }
                }
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(_)) => {}
            }
        }
    }
    hub.drop_client(id);
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/ws", get(ws_upgrade))
        .route("/api/status", get(api_status))
        .route("/api/jobs", get(api_jobs))
        .route("/api/bars", get(api_bars))
        .route("/api/analysis", get(api_analysis))
        .route("/api/known", get(api_known))
        .route("/api/screen", get(api_screen))
        .route("/api/calendar", get(api_calendar))
        .route("/api/derivs/underlyings", get(api_derivs_underlyings))
        .route("/api/derivs/expiries", get(api_derivs_expiries))
        .route("/api/derivs/chain", get(api_derivs_chain))
        .route("/api/derivs/futures", get(api_derivs_futures))
        .route("/api/derivs/quotes", get(api_derivs_quotes))
        .route("/api/indices", get(api_indices))
        .route("/api/search", get(api_search))
        .route("/api/watchlists", get(watchlists_get).put(watchlists_put))
        .route("/api/screens", get(screens_get).put(screens_put))
        .route("/api/formulas", get(formulas_get).put(formulas_put))
        .route("/api/strategies", get(strategies_get).put(strategies_put))
        .route("/api/pyind", get(pyind_list))
        .route("/api/pyind/enable", axum::routing::put(pyind_enable))
        .route("/api/pyind/disable", axum::routing::put(pyind_disable))
        // the route takes the bars the runner is allowed (4 MB, with room for the file name), not axum's 2 MB default for a JSON body
        .route("/api/pyind/run", axum::routing::post(pyind_run).layer(axum::extract::DefaultBodyLimit::max(crate::pyind::MAX_BODY + 64 * 1024)))
        .route("/api/drawings/:symbol", get(drawings_get).put(drawings_put))
        .route("/api/analyse", axum::routing::post(api_analyse))
        .route("/api/backtest", axum::routing::post(api_backtest))
        .route("/api/fund-status", get(api_fund_status))
        .route("/api/fund-fetch", get(fund_fetch_status))
        .route("/api/fund-fetch/:symbol", axum::routing::post(fund_fetch_start))
        .route("/api/fund/:symbol", get(api_fund))
        .route("/api/valuation/:symbol", get(api_valuation))
        .route("/api/flow/board", get(api_flow_board))
        .route("/api/chart/explain", axum::routing::post(api_chart_explain))
        .route("/api/sentiment/:symbol", get(api_sentiment))
        .route("/api/notebook", get(notebook_get).put(notebook_put))
        .route("/api/fund/:symbol/export", get(api_fund_export))
        .route("/api/verdict-job", get(verdict_status))
        .route("/api/verdict/:symbol", axum::routing::post(verdict_start))
        .route("/api/layouts", get(layouts_list))
        .route("/api/layouts/:name", get(layouts_get).put(layouts_put).delete(layouts_delete))
        .fallback_service(ServeDir::new(state.web_dir.clone()))
        .layer(middleware::from_fn_with_state(state.clone(), guard))
        .with_state(state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fake::run_fake;
    use futures_util::{SinkExt, StreamExt};
    use tokio::net::TcpListener;
    use tokio_tungstenite::tungstenite::client::IntoClientRequest;
    use tokio_tungstenite::tungstenite::Message as WsMessage;
    use tokio_util::sync::CancellationToken;

    #[test]
    fn origin_and_host_tables() {
        assert!(origin_ok(Some("http://127.0.0.1:8700"), 8700));
        assert!(origin_ok(Some("http://localhost:8700"), 8700));
        assert!(!origin_ok(Some("http://evil.test"), 8700));
        assert!(!origin_ok(Some("http://127.0.0.1:9999"), 8700));
        assert!(!origin_ok(None, 8700));
    }

    #[test]
    fn a_request_with_no_origin_is_accepted_only_if_it_is_not_from_another_site() {
        assert!(fetch_site_ok(None) && fetch_site_ok(Some("same-origin")) && fetch_site_ok(Some("none")));
        assert!(!fetch_site_ok(Some("cross-site")) && !fetch_site_ok(Some("same-site")));
    }

    #[test]
    fn a_symbol_never_starts_like_a_command_line_flag() {
        for good in ["SBIN", "M&M", "NIFTY 50", "^NSEI", "BAJAJ-AUTO", "3MINDIA"] {
            assert!(symbol_ok(good), "{good}");
        }
        for bad in ["", "-h", "--version", " X", "A;B", "../x"] {
            assert!(!symbol_ok(bad), "{bad}");
        }
        assert!(host_ok(Some("127.0.0.1:8700"), 8700));
        assert!(host_ok(Some("localhost:8700"), 8700));
        assert!(!host_ok(Some("evil.test:8700"), 8700));
        assert!(!host_ok(None, 8700));
    }

    async fn start() -> (u16, AppState, CancellationToken) {
        start_with(false).await
    }

    async fn start_with(live_derivs: bool) -> (u16, AppState, CancellationToken) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (status, _rx) = watch::channel(FeedStatus::On);
        let fetch: crate::bars::Fetcher = Arc::new(|_s, _t| Box::pin(async { None }));
        let bars = Arc::new(Bars { warehouse: PathBuf::from("no/such.duckdb"), intraday: crate::bars::Intraday::new(fetch), adjusted: crate::bars::AdjustedDaily::new(Arc::new(|_s| Box::pin(async { None }))) });
        let layouts = Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-layouts-{port}"))));
        let verdicts = VerdictJobs::new(vec!["no-such-program-xyz".into()]);
        let analyse = Arc::new(crate::analyse_worker::AnalyseWorker::new(vec!["no-such-program-xyz".into()]));
        let fundamentals = Arc::new(FundamentalsJobs::new(vec!["no-such-program-xyz".into()], vec!["no-such-program-xyz".into()], PathBuf::from("no/such.duckdb"), PathBuf::from("no/such-f.duckdb")));
        let state = AppState { hub: Hub::new(), status, port, web_dir: std::env::temp_dir(), bars, layouts, verdicts, analyse, fundamentals, flow_board: Arc::new(crate::flow_board::FlowBoard::new(vec!["no-such-program-xyz".into()], PathBuf::from("no/such-derivs.duckdb"))), chart_explain: Arc::new(crate::chart_explain::ChartExplain::new(vec!["no-such-program-xyz".into()])), sentiment: Arc::new(crate::sentiment::SentimentJobs::new(vec!["no-such-program-xyz".into()], PathBuf::from("no/such.duckdb"))), indices: crate::indices::IndexStore::new(), search: Arc::new(crate::search::SearchCache::new(PathBuf::from("no/such.duckdb"))), fund_fetch: crate::fund_fetch::FundFetch::new(Arc::new(|_m, _s| tokio::process::Command::new("no-such-program-xyz"))), lists: Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-lists-{port}")))), pyind: crate::pyind::PyInd::new("no-such-python", std::path::Path::new("no/such/runner.py"), &std::env::temp_dir().join(format!("athena-srv-ind-{port}"))), derivs: crate::derivs::DerivsStore::new(), deriv_quotes: crate::deriv_quotes::DerivQuotes::new(), live_derivs };
        let stop = CancellationToken::new();
        tokio::spawn(run_fake(state.hub.clone(), stop.clone()));
        let app = router(state.clone());
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        (port, state, stop)
    }

    fn ws_request(port: u16, origin: &str) -> tokio_tungstenite::tungstenite::handshake::client::Request {
        let mut r = format!("ws://127.0.0.1:{port}/ws").into_client_request().unwrap();
        r.headers_mut().insert("Origin", origin.parse().unwrap());
        r
    }

    #[tokio::test]
    async fn a_page_with_a_good_origin_watches_a_symbol_and_gets_frames() {
        let (port, state, stop) = start().await;
        let (mut ws, _) = tokio_tungstenite::connect_async(ws_request(port, &format!("http://127.0.0.1:{port}"))).await.unwrap();
        ws.send(WsMessage::Text(r#"{"type":"watch","symbols":["SBIN"]}"#.into())).await.unwrap();
        let mut types = Vec::new();
        let mut got_quote = false;
        let deadline = tokio::time::timeout(Duration::from_secs(5), async {
            while let Some(Ok(WsMessage::Text(t))) = ws.next().await {
                let v: serde_json::Value = serde_json::from_str(t.as_str()).unwrap();
                types.push(v["type"].as_str().unwrap().to_string());
                if v["type"] == "q" && v["q"][0]["s"] == "SBIN" {
                    got_quote = true;
                    break;
                }
            }
        })
        .await;
        assert!(deadline.is_ok() && got_quote, "types seen: {types:?}");
        assert_eq!(&types[..2], ["hello", "status"]);
        assert_eq!(state.hub.client_count(), 1);
        drop(ws);
        for _ in 0..50 {
            if state.hub.client_count() == 0 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert_eq!(state.hub.client_count(), 0, "a closed page is dropped from the hub");
        stop.cancel();
    }

    #[tokio::test]
    async fn a_bad_origin_or_a_missing_origin_is_refused_on_the_websocket() {
        let (port, _state, stop) = start().await;
        assert!(tokio_tungstenite::connect_async(ws_request(port, "http://evil.test")).await.is_err());
        let plain = format!("ws://127.0.0.1:{port}/ws").into_client_request().unwrap();
        assert!(tokio_tungstenite::connect_async(plain).await.is_err());
        stop.cancel();
    }

    #[tokio::test]
    async fn layouts_save_list_load_and_delete_over_http() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let base = format!("http://127.0.0.1:{port}/api/layouts");
        let put = client.put(format!("{base}/Mine-1")).header("Origin", &origin).body(r#"{"version":1}"#).send().await.unwrap();
        assert_eq!(put.status(), 204);
        let list: serde_json::Value = client.get(&base).header("Origin", &origin).send().await.unwrap().json().await.unwrap();
        assert!(list["names"].as_array().unwrap().iter().any(|n| n == "Mine-1"));
        let got = client.get(format!("{base}/Mine-1")).header("Origin", &origin).send().await.unwrap();
        assert_eq!(got.text().await.unwrap(), r#"{"version":1}"#);
        assert_eq!(client.put(format!("{base}/x")).header("Origin", &origin).body("[]").send().await.unwrap().status(), 400);
        assert_eq!(client.put(format!("{base}/Mine-1")).header("Origin", "http://evil.test").body("{}").send().await.unwrap().status(), 403);
        assert_eq!(client.delete(format!("{base}/Mine-1")).header("Origin", &origin).send().await.unwrap().status(), 204);
        assert_eq!(client.get(format!("{base}/Mine-1")).header("Origin", &origin).send().await.unwrap().status(), 404);
        stop.cancel();
    }

    #[tokio::test]
    async fn watchlists_are_one_document_saved_and_loaded_over_http() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/watchlists");
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().status(), 204);
        assert_eq!(client.put(&url).header("Origin", &origin).body(r#"{"version":1,"lists":[]}"#).send().await.unwrap().status(), 204);
        let got = client.get(&url).header("Origin", &origin).send().await.unwrap();
        assert_eq!(got.text().await.unwrap(), r#"{"version":1,"lists":[]}"#);
        assert_eq!(client.put(&url).header("Origin", &origin).body("[]").send().await.unwrap().status(), 400);
        let big = format!(r#"{{"x":"{}"}}"#, "a".repeat(250 * 1024));
        assert_eq!(client.put(&url).header("Origin", &origin).body(big).send().await.unwrap().status(), 413);
        assert_eq!(client.put(&url).header("Origin", "http://evil.test").body("{}").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn saved_screens_are_one_document_saved_and_loaded_over_http_apart_from_the_watchlists() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/screens");
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().status(), 204);
        let body = r#"{"version":1,"screens":[{"name":"Value","query":"pe < 15"}]}"#;
        assert_eq!(client.put(&url).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().text().await.unwrap(), body);
        assert_eq!(client.get(format!("http://127.0.0.1:{port}/api/watchlists")).header("Origin", &origin).send().await.unwrap().status(), 204); // not the same document
        assert_eq!(client.put(&url).header("Origin", &origin).body("[]").send().await.unwrap().status(), 400);
        let big = format!(r#"{{"x":"{}"}}"#, "a".repeat(250 * 1024));
        assert_eq!(client.put(&url).header("Origin", &origin).body(big).send().await.unwrap().status(), 413);
        assert_eq!(client.put(&url).header("Origin", "http://evil.test").body("{}").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn saved_formulas_are_one_document_apart_from_the_screens_and_the_watchlists() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/formulas");
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().status(), 204);
        let body = r#"{"version":1,"formulas":[{"name":"Spread","text":"sma(20) - sma(50)","pane":"own"}]}"#;
        assert_eq!(client.put(&url).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().text().await.unwrap(), body);
        assert_eq!(client.get(format!("http://127.0.0.1:{port}/api/screens")).header("Origin", &origin).send().await.unwrap().status(), 204);
        assert_eq!(client.put(&url).header("Origin", &origin).body("[]").send().await.unwrap().status(), 400);
        assert_eq!(client.put(&url).header("Origin", "http://evil.test").body("{}").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn saved_strategies_are_one_document_apart_from_the_formulas() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/strategies");
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().status(), 204);
        let body = r#"{"version":1,"strategies":[{"name":"Mine","form":{"entry":"close > sma(50)"}}]}"#;
        assert_eq!(client.put(&url).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().text().await.unwrap(), body);
        assert_eq!(client.get(format!("http://127.0.0.1:{port}/api/formulas")).header("Origin", &origin).send().await.unwrap().status(), 204);
        assert_eq!(client.put(&url).header("Origin", "http://evil.test").body("{}").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn the_indicator_routes_refuse_a_foreign_page_a_file_not_switched_on_and_a_bad_name() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let base = format!("http://127.0.0.1:{port}/api/pyind");
        let sha = "a".repeat(64);
        let enable = serde_json::json!({"file": "x.py", "sha256": sha}).to_string();
        // a page from anywhere else can neither switch a file on nor run one
        assert_eq!(client.put(format!("{base}/enable")).header("Origin", "http://evil.test").header("content-type", "application/json").body(enable.clone()).send().await.unwrap().status(), 403);
        assert_eq!(client.post(format!("{base}/run")).header("Origin", "http://evil.test").header("content-type", "application/json").body(r#"{"file":"x.py","bars":[]}"#).send().await.unwrap().status(), 403);
        // a name that is not a file name, and a hash that is not a hash
        let bad = serde_json::json!({"file": "../x.py", "sha256": sha}).to_string();
        assert_eq!(client.put(format!("{base}/enable")).header("Origin", &origin).header("content-type", "application/json").body(bad).send().await.unwrap().status(), 400);
        assert_eq!(client.put(format!("{base}/enable")).header("Origin", &origin).header("content-type", "application/json").body(r#"{"file":"x.py","sha256":"short"}"#).send().await.unwrap().status(), 400);
        // a file that was never switched on does not run
        assert_eq!(client.post(format!("{base}/run")).header("Origin", &origin).header("content-type", "application/json").body(r#"{"file":"x.py","bars":[]}"#).send().await.unwrap().status(), 403);
        assert_eq!(client.post(format!("{base}/run")).header("Origin", &origin).header("content-type", "application/json").body(r#"{"file":"a/b.py","bars":[]}"#).send().await.unwrap().status(), 400);
        stop.cancel();
    }

    #[test]
    fn a_symbol_becomes_a_safe_file_name() {
        assert_eq!(drawing_name("SBIN").as_deref(), Some("dr-SBIN"));
        assert_eq!(drawing_name("sbin").as_deref(), Some("dr-SBIN"));
        assert_eq!(drawing_name("M&M").as_deref(), Some("dr-M_26M"));
        assert_eq!(drawing_name("NIFTY 50").as_deref(), Some("dr-NIFTY 50"));
        assert_eq!(drawing_name("BAJAJ-AUTO").as_deref(), Some("dr-BAJAJ-AUTO"));
        assert_eq!(drawing_name("A_B").as_deref(), Some("dr-A_5fB")); // an underscore is encoded, so no symbol can collide with an encoded one
        assert_ne!(drawing_name("A_26B"), drawing_name("A&B"));
        assert_eq!(drawing_name("../x"), None);
        assert_eq!(drawing_name("A/B"), None);
        assert_eq!(drawing_name(""), None);
        assert_eq!(drawing_name(&"A".repeat(25)), None);
        assert_eq!(drawing_name(&"&".repeat(24)), None); // 3 + 72 characters
        for name in ["M&M", "NIFTY 50", "A_B", "BAJAJ-AUTO"] {
            assert!(crate::layouts::name_ok(&drawing_name(name).unwrap()), "{name}");
        }
    }

    #[tokio::test]
    async fn drawings_are_one_document_per_symbol_saved_and_loaded_over_http() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = |s: &str| format!("http://127.0.0.1:{port}/api/drawings/{s}");
        assert_eq!(client.get(url("SBIN")).header("Origin", &origin).send().await.unwrap().status(), 204);
        let body = r#"{"version":1,"symbol":"SBIN","drawings":[]}"#;
        assert_eq!(client.put(url("SBIN")).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.get(url("SBIN")).header("Origin", &origin).send().await.unwrap().text().await.unwrap(), body);
        assert_eq!(client.get(url("TCS")).header("Origin", &origin).send().await.unwrap().status(), 204); // another symbol has its own
        assert_eq!(client.put(url("M%26M")).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.get(url("M%26M")).header("Origin", &origin).send().await.unwrap().status(), 200);
        assert_eq!(client.put(url("NIFTY%2050")).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.put(url("A%2FB")).header("Origin", &origin).body(body).send().await.unwrap().status(), 400);
        assert_eq!(client.put(url("SBIN")).header("Origin", &origin).body("[]").send().await.unwrap().status(), 400);
        let big = format!(r#"{{"x":"{}"}}"#, "a".repeat(250 * 1024));
        assert_eq!(client.put(url("SBIN")).header("Origin", &origin).body(big).send().await.unwrap().status(), 413);
        assert_eq!(client.put(url("SBIN")).header("Origin", "http://evil.test").body(body).send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn the_notebook_is_one_document_saved_and_loaded_over_http() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/notebook");
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().status(), 204); // none written yet is not an error
        let body = r#"{"version":1,"notes":[{"id":"n1","symbol":"TCS","text":"a note","tag":"","created":1,"updated":1}]}"#;
        assert_eq!(client.put(&url).header("Origin", &origin).body(body).send().await.unwrap().status(), 204);
        assert_eq!(client.get(&url).header("Origin", &origin).send().await.unwrap().text().await.unwrap(), body);
        assert_eq!(client.put(&url).header("Origin", &origin).body("[]").send().await.unwrap().status(), 400);
        let big = format!(r#"{{"x":"{}"}}"#, "a".repeat(250 * 1024));
        assert_eq!(client.put(&url).header("Origin", &origin).body(big).send().await.unwrap().status(), 413);
        assert_eq!(client.put(&url).header("Origin", "http://evil.test").body(body).send().await.unwrap().status(), 403);
        assert_eq!(client.get(&url).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn a_company_fetch_is_started_by_post_and_reports_its_state() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let base = format!("http://127.0.0.1:{port}/api/fund-fetch");
        let idle: serde_json::Value = client.get(&base).send().await.unwrap().json().await.unwrap();
        assert_eq!(idle["state"], "idle");
        assert_eq!(client.post(format!("{base}/rv%20nl")).header("Origin", &origin).send().await.unwrap().status(), 400);
        assert_eq!(client.post(format!("{base}/RVNL")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.post(format!("{base}/RVNL")).header("Origin", &origin).send().await.unwrap().status(), 202);
        let mut last = serde_json::Value::Null;
        for _ in 0..100 {
            last = client.get(&base).send().await.unwrap().json().await.unwrap();
            if last["state"] != "running" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        assert_eq!(last["state"], "failed"); // the test server's program does not exist
        assert_eq!(last["symbol"], "RVNL");
        stop.cancel();
    }

    #[tokio::test]
    async fn search_refuses_an_over_long_query_and_says_so_when_there_is_no_warehouse() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/search");
        assert_eq!(client.get(format!("{base}?q={}", "a".repeat(80))).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}?q=tata")).send().await.unwrap().status(), 503); // the test has no warehouse
        stop.cancel();
    }

    #[tokio::test]
    async fn indices_are_unavailable_until_a_snapshot_exists_and_then_served() {
        let (port, state, stop) = start().await;
        let client = reqwest::Client::new();
        let url = format!("http://127.0.0.1:{port}/api/indices");
        assert_eq!(client.get(&url).send().await.unwrap().status(), 503);
        state.indices.set(crate::indices::Snapshot { as_of: "16-Oct-2026 15:30".into(), rows: vec![] });
        let v: serde_json::Value = client.get(&url).send().await.unwrap().json().await.unwrap();
        assert_eq!(v["as_of"], "16-Oct-2026 15:30");
        stop.cancel();
    }

    #[tokio::test]
    async fn analysis_and_verdict_routes_validate_and_report() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let base = format!("http://127.0.0.1:{port}/api");
        assert_eq!(client.get(format!("{base}/analysis?symbol=a;b")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/analysis?symbol=SBIN")).send().await.unwrap().status(), 503); // no warehouse in this test
        let idle: serde_json::Value = client.get(format!("{base}/verdict-job")).send().await.unwrap().json().await.unwrap();
        assert_eq!(idle["state"], "idle");
        assert_eq!(client.post(format!("{base}/verdict/a;b")).header("Origin", &origin).send().await.unwrap().status(), 400);
        assert_eq!(client.post(format!("{base}/verdict/SBIN")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.post(format!("{base}/verdict/SBIN")).header("Origin", &origin).send().await.unwrap().status(), 202);
        stop.cancel();
    }

    #[tokio::test]
    async fn raw_bars_are_asked_for_with_adj_zero_and_say_so() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/bars?symbol=SBIN&tf=1D");
        let raw: serde_json::Value = client.get(format!("{base}&adj=0")).send().await.unwrap().json().await.unwrap();
        assert_eq!(raw["source"], "warehouse, raw"); // no warehouse in this test, so no bars, but the raw path was taken
        let adjusted: serde_json::Value = client.get(&base).send().await.unwrap().json().await.unwrap();
        assert_eq!(adjusted["source"], "warehouse");
        stop.cancel();
    }

    #[tokio::test]
    async fn the_calendar_route_refuses_bad_dates_and_reports_a_missing_warehouse() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/calendar");
        assert_eq!(client.get(format!("{base}?from=2026-13-01&to=2026-12-01")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}?from=2026-10-20&to=2026-10-19")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}?from=2026-01-01&to=2028-01-01")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}?from=2026-10-19&to=2026-10-31")).send().await.unwrap().status(), 503); // no warehouse in this test
        assert_eq!(client.get(&base).send().await.unwrap().status(), 503); // the default range is valid
        stop.cancel();
    }

    #[tokio::test]
    async fn the_derivatives_routes_wait_for_the_list_then_answer_and_refuse_bad_input() {
        let (port, state, stop) = start().await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/derivs");
        assert_eq!(client.get(format!("{base}/underlyings")).send().await.unwrap().status(), 503); // still loading
        let rows = serde_json::json!([
            {"token": "1", "symbol": "NIFTY27NOV35FUT", "name": "NIFTY", "expiry": "27NOV2035", "strike": "-1.000000", "lotsize": "65", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
            {"token": "2", "symbol": "NIFTY27NOV3523000CE", "name": "NIFTY", "expiry": "27NOV2035", "strike": "2300000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"},
            {"token": "3", "symbol": "NIFTY27NOV3523000PE", "name": "NIFTY", "expiry": "27NOV2035", "strike": "2300000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.000000"}
        ]);
        state.derivs.set_ready(crate::derivs::Derivs::from_rows(&rows, chrono::NaiveDate::from_ymd_opt(2026, 10, 19).unwrap()));
        let get = |path: String| {
            let client = client.clone();
            async move { client.get(path).send().await.unwrap() }
        };
        let u = get(format!("{base}/underlyings?segment=nfo")).await;
        assert_eq!(u.status(), 200);
        let body: serde_json::Value = u.json().await.unwrap();
        assert_eq!(body["underlyings"][0]["underlying"], "NIFTY");
        assert_eq!(get(format!("{base}/underlyings?segment=XXX")).await.status(), 400);
        assert_eq!(get(format!("{base}/expiries?underlying=nifty")).await.status(), 200);
        assert_eq!(get(format!("{base}/expiries")).await.status(), 400);
        assert_eq!(get(format!("{base}/expiries?underlying=a;b")).await.status(), 400);
        assert_eq!(get(format!("{base}/expiries?underlying=NOPE")).await.status(), 404);
        let chain: serde_json::Value = get(format!("{base}/chain?underlying=NIFTY&expiry=2035-11-27")).await.json().await.unwrap();
        assert_eq!(chain["strikes"][0]["strike"], 23000.0);
        assert_eq!(chain["strikes"][0]["call"]["token"], "2");
        assert_eq!(chain["future"]["token"], "1");
        assert_eq!(get(format!("{base}/chain?underlying=NIFTY&expiry=27NOV2035")).await.status(), 400);
        assert_eq!(get(format!("{base}/chain?underlying=NIFTY&expiry=2035-11-28")).await.status(), 404);
        assert_eq!(get(format!("{base}/chain?underlying=NIFTY")).await.status(), 400);
        assert_eq!(get(format!("{base}/futures?underlying=NIFTY")).await.status(), 200);
        assert_eq!(get(format!("{base}/futures?underlying=NOPE")).await.status(), 404);
        assert_eq!(client.get(format!("{base}/underlyings")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[tokio::test]
    async fn the_derivative_quotes_route_says_when_there_is_no_stream_and_otherwise_marks_contracts_wanted_and_answers_what_the_stream_said() {
        // no broker stream (the fake feed): live false, nothing marked wanted
        let (port, state, stop) = start().await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/derivs/quotes");
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:777")).send().await.unwrap().json().await.unwrap();
        assert_eq!(body["live"], false);
        assert!(state.deriv_quotes.wanted(std::time::Instant::now()).is_empty());
        assert_eq!(client.get(format!("{base}?tokens=NSE:3045")).send().await.unwrap().status(), 400);
        stop.cancel();

        // a stream is there: the contract is wanted, absent until the stream speaks, then answered
        let (port, state, stop) = start_with(true).await;
        let base = format!("http://127.0.0.1:{port}/api/derivs/quotes");
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:777,MCX:5")).send().await.unwrap().json().await.unwrap();
        assert_eq!(body["live"], true);
        assert_eq!(body["quotes"], serde_json::json!([]));
        assert_eq!(state.deriv_quotes.wanted(std::time::Instant::now()).len(), 2);
        let tick = crate::angel::decode::decode(&crate::angel::decode::test_packet_on(2, "777", 1_760_000_000_000, 12_345, 9_000, &[(1, 650, 12_340), (0, 130, 12_350)], 1_250_000, -3.5)).unwrap();
        state.deriv_quotes.update(2, crate::deriv_quotes::DerivQuote::from_tick("NFO", 2, &tick));
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:777")).send().await.unwrap().json().await.unwrap();
        let q = &body["quotes"][0];
        assert_eq!((q["seg"].as_str(), q["token"].as_str(), q["last"].as_f64(), q["oi"].as_i64(), q["oip"].as_f64()), (Some("NFO"), Some("777"), Some(123.45), Some(1_250_000), Some(-3.5)));
        assert_eq!((q["bid"].as_f64(), q["ask"].as_f64()), (Some(123.4), Some(123.5)));
        assert_eq!(client.get(format!("{base}?tokens=NFO:x")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(&base).send().await.unwrap().status(), 200); // no tokens: just the status
        assert_eq!(client.get(format!("{base}?tokens=NFO:1")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        stop.cancel();
    }

    #[test]
    fn the_calendar_defaults_to_the_india_date_and_thirty_days_and_keeps_what_was_asked_for() {
        let today = chrono::NaiveDate::from_ymd_opt(2026, 10, 19).unwrap();
        assert_eq!(calendar_range(today, None, None), ("2026-10-19".to_string(), "2026-11-18".to_string()));
        assert_eq!(calendar_range(today, Some("2026-01-01".into()), None), ("2026-01-01".to_string(), "2026-11-18".to_string()));
        assert_eq!(calendar_range(today, None, Some("2026-10-25".into())), ("2026-10-19".to_string(), "2026-10-25".to_string()));
    }

    #[tokio::test]
    async fn the_indicator_run_route_takes_a_body_between_two_and_four_megabytes_and_refuses_a_larger_one() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let url = format!("http://127.0.0.1:{port}/api/pyind/run");
        let origin = format!("http://127.0.0.1:{port}");
        let post = |bytes: usize| {
            let bar = r#"{"t":1,"o":1,"h":1,"l":1,"c":1,"v":1},"#;
            let body = format!(r#"{{"file":"x.py","bars":[{}{{"t":1,"o":1,"h":1,"l":1,"c":1,"v":1}}]}}"#, bar.repeat(bytes / bar.len()));
            client.post(&url).header("Origin", &origin).header("content-type", "application/json").body(body).send()
        };
        // 3 MB is inside the runner's own limit: the engine answers (this test's indicator folder has no such file), the framework does not refuse it
        assert_ne!(post(3 * 1024 * 1024).await.unwrap().status(), 413);
        // 5 MB is past it: refused before the handler
        assert_eq!(post(5 * 1024 * 1024).await.unwrap().status(), 413);
        stop.cancel();
    }

    #[tokio::test]
    async fn fundamentals_routes_validate_and_report() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api");
        assert_eq!(client.get(format!("{base}/fund/a;b")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/fund/TCS?basis=both")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/fund/TCS/export?basis=both")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/fund/TCS")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(format!("{base}/fund/TCS")).send().await.unwrap().status(), 502); // the stand-in program does not exist
        assert_eq!(client.get(format!("{base}/fund-status")).send().await.unwrap().status(), 404); // no store in this test
        assert_eq!(client.get(format!("{base}/valuation/a;b")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/valuation/TCS?basis=both")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/valuation/TCS")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(format!("{base}/valuation/TCS")).send().await.unwrap().status(), 502); // no valuation module is configured in this test
        assert_eq!(client.get(format!("{base}/flow/board")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(format!("{base}/flow/board")).send().await.unwrap().status(), 502); // the stand-in program for the board does not exist
        let post = |origin: Option<&str>, body: &str| {
            let mut r = client.post(format!("{base}/chart/explain")).body(body.to_string());
            if let Some(o) = origin {
                r = r.header("Origin", o);
            }
            r.send()
        };
        assert_eq!(client.get(format!("{base}/sentiment/TCS")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(format!("{base}/sentiment/a;b")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/sentiment/TCS?tone=2")).send().await.unwrap().status(), 400);
        assert_eq!(client.get(format!("{base}/sentiment/TCS")).send().await.unwrap().status(), 502); // the stand-in program for the page does not exist
        assert_eq!(post(Some("http://evil.test"), "{}").await.unwrap().status(), 403);
        assert_eq!(post(None, "[1]").await.unwrap().status(), 400);
        assert_eq!(post(None, "{}").await.unwrap().status(), 502); // the stand-in program for the explainer does not exist
        stop.cancel();
    }

    #[tokio::test]
    async fn the_status_api_checks_origin_and_host() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let url = format!("http://127.0.0.1:{port}/api/status");
        let ok = client.get(&url).send().await.unwrap();
        assert_eq!(ok.status(), 200);
        let body: serde_json::Value = ok.json().await.unwrap();
        assert_eq!(body["feed"], "on");
        assert_eq!(client.get(&url).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(&url).header("Host", "evil.test").send().await.unwrap().status(), 403);
        stop.cancel();
    }
}
