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
use crate::bars::{yahoo_params, Bars, DEFAULT_LIMIT};
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
    /// The bounded queues of the Python workers (T6): their status, with per-job queue wait and running time, and the way out of a worker that could not be confirmed gone.
    pub work: Arc<crate::bounded::WorkRegistry>,
    pub fundamentals: Arc<FundamentalsJobs>,
    pub flow_board: Arc<crate::flow_board::FlowBoard>,
    pub chart_explain: Arc<crate::chart_explain::ChartExplain>,
    pub sentiment: Arc<crate::sentiment::SentimentJobs>,
    pub ipo: Arc<crate::ipo::IpoJobs>,
    /// The Analyse button of the IPO tab: the forensic checks on one draft offer document.
    pub forensic: Arc<crate::forensic_job::ForensicJobs>,
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
    // which data a daily answer came from (the real file, or a validated snapshot generation and why); null for an answer that is not from the warehouse
    let mut provenance = serde_json::Value::Null;
    let (bars, source, note) = if tf == "1D" {
        let path = state.bars.warehouse.clone();
        let (sym, limit) = (symbol.clone(), q.limit.unwrap_or(DEFAULT_LIMIT).clamp(1, 5000));
        // a stock's prices are adjusted for splits and bonuses (as Streamlit's were); an index has none, and if the adjustment cannot run the raw bars are shown and say so
        let raw_wanted = q.adj == Some(0);
        let adjusted = if raw_wanted || symbol.to_ascii_uppercase().starts_with("NIFTY ") { None } else { state.bars.adjusted.get_served(&symbol).await };
        // what is registered for /api/data-source is the provenance of this response, at the point it is given: a cache hit renews it, and it is never what some other open last got
        let variant = if raw_wanted { "raw" } else { "fallback" };
        match adjusted {
            Some((a, cache)) => {
                let from = a.bars.len().saturating_sub(limit);
                // the helper has already joined a renamed company's earlier symbols; the page is told which
                let note = join_notes(if a.flags.is_empty() { String::new() } else { format!("Price breaks no record explains: {}.", a.flags.join(", ")) }, &a.joined);
                let mut provenance = bars_provenance(a.provenance.clone(), a.bars.last().map(|b| b.t));
                if let Some(o) = provenance.as_object_mut() {
                    o.insert("cache".into(), serde_json::json!({"hit": cache.hit, "age_seconds": cache.age_secs})); // the data is as old as the answer was when it was computed, not as old as this request
                    crate::snapshot::record_served("bars", Some(&format!("{symbol}:adjusted")), provenance.clone());
                }
                return Json(serde_json::json!({"symbol": symbol, "tf": tf, "bars": &a.bars[from..], "source": "warehouse, adjusted", "note": note, "provenance": provenance})).into_response();
            }
            None => match tokio::task::spawn_blocking(move || crate::bars::daily_bars_with_provenance(&path, &sym, limit)).await {
                Ok(Ok((bars, joined, prov))) => {
                    let stock = !symbol.to_ascii_uppercase().starts_with("NIFTY ");
                    let note = if raw_wanted && stock { "Prices are as printed, not adjusted for splits and bonuses.".to_string() } else if stock { "Split and bonus adjustment is unavailable; prices are as printed.".to_string() } else { String::new() };
                    provenance = bars_provenance(Some(prov.to_value()), bars.last().map(|b| b.t));
                    crate::snapshot::record_served("bars", Some(&format!("{symbol}:{variant}")), provenance.clone());
                    (bars, if raw_wanted { "warehouse, raw" } else { "warehouse" }, join_notes(note, &joined))
                }
                Ok(Err(failure)) => {
                    provenance = failure.failure.unwrap_or(serde_json::Value::Null); // why the real file failed and why no snapshot generation replaced it
                    if !provenance.is_null() {
                        crate::snapshot::record_served("bars", Some(&format!("{symbol}:{variant}")), provenance.clone());
                    }
                    (Vec::new(), if raw_wanted { "warehouse, raw" } else { "warehouse" }, "warehouse busy".to_string())
                }
                Err(_) => (Vec::new(), if raw_wanted { "warehouse, raw" } else { "warehouse" }, "warehouse busy".to_string()),
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
    Json(serde_json::json!({"symbol": symbol, "tf": tf, "bars": bars, "source": source, "note": note, "provenance": provenance})).into_response()
}

/// A daily answer's provenance, with the date its own data stops at: the last bar it returned. A snapshot's manifest dates (per dataset) are kept beside it as `snapshot_data_as_of`; the date a copy was
/// taken (`snapshot_created_at`) is a different fact and is never used for either.
pub(crate) fn bars_provenance(provenance: Option<serde_json::Value>, last_t: Option<i64>) -> serde_json::Value {
    let Some(mut p) = provenance else { return serde_json::Value::Null };
    if let Some(o) = p.as_object_mut() {
        if let Some(manifest_dates) = o.remove("data_as_of").filter(|v| !v.is_null()) {
            o.insert("snapshot_data_as_of".into(), manifest_dates);
        }
        let day = last_t.and_then(|t| chrono::DateTime::from_timestamp(t, 0)).map(|d| d.date_naive().to_string());
        o.insert("data_as_of".into(), serde_json::json!(day));
    }
    p
}

/// What each reader of the warehouse last got: the real file or which snapshot generation and why, and whether recent answers came from different generations.
async fn api_data_source(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(crate::snapshot::data_sources_for(&state.bars.warehouse))
}

#[derive(serde::Deserialize)]
struct AnalyseBody {
    query: String,
    #[serde(default)]
    relatives: bool,
}

/// The live analysis of one instrument (all the specialists, as Streamlit's Analyse tab ran them). It calls the language model and can take a minute or more.
async fn api_analyse(State(state): State<AppState>, Json(body): Json<AnalyseBody>) -> Response {
    match state.analyse.view(&body.query, body.relatives).await {
        Ok(view) => Json(view).into_response(),
        Err(e) => analyse_error_response(e),
    }
}

/// What a failed analysis request says to the page (T6: a full queue, a deadline that passed and a held worker each have their own answer; the job's ID is in the body so the page's message and the
/// `/api/work` record can be matched).
pub fn analyse_error_response(error: crate::analyse_worker::AnalyseError) -> Response {
    use crate::analyse_worker::AnalyseError;
    match error {
        AnalyseError::BadQuery => StatusCode::BAD_REQUEST.into_response(),
        AnalyseError::Unavailable => (StatusCode::BAD_GATEWAY, Json(serde_json::json!({"error": "the analysis service did not answer; try again"}))).into_response(),
        AnalyseError::Failed(message) => (StatusCode::UNPROCESSABLE_ENTITY, Json(serde_json::json!({"error": message}))).into_response(),
        AnalyseError::Busy => (StatusCode::SERVICE_UNAVAILABLE, [("retry-after", "10")], Json(serde_json::json!({"error": "the analysis queue is full; try again shortly", "reason": "queue_full"}))).into_response(),
        AnalyseError::TimedOut(job) => (StatusCode::GATEWAY_TIMEOUT, Json(serde_json::json!({"error": format!("the analysis did not finish within its time limit (job {job}); the limit includes the time spent waiting in the queue"), "reason": "deadline", "job": job}))).into_response(),
        AnalyseError::Held => (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({"error": "the analysis worker could not be confirmed stopped and is being held; see /api/work", "reason": "cancel_unconfirmed"}))).into_response(),
        AnalyseError::Unconfirmed(job) => (StatusCode::BAD_GATEWAY, Json(serde_json::json!({"error": format!("the analysis failed and its worker could not be confirmed stopped (job {job}); see /api/work"), "reason": "unconfirmed", "job": job}))).into_response(),
        AnalyseError::Closed => (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({"error": "the terminal is shutting down", "reason": "shutting_down"}))).into_response(),
    }
}

/// The bounded queues: each pool's capacity, what is queued and running, the workers held because a child could not be confirmed gone, and the recent jobs with their queue wait and running time.
async fn api_work(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(state.work.status())
}

#[derive(serde::Deserialize)]
struct WorkResolveBody {
    pool: String,
    job: u64,
    /// `confirmed_gone` (a person has checked that the process is gone) or `retry` (try to end it again now).
    action: String,
    /// Who is doing this (recorded with the job).
    #[serde(default)]
    operator: String,
    /// Why (recorded with the job).
    #[serde(default)]
    reason: String,
}

/// The way out of a worker that could not be confirmed gone (T6): a person says the process is gone, or asks for another attempt to end it. Who and why are required and are recorded with the job.
async fn api_work_resolve(State(state): State<AppState>, Json(body): Json<WorkResolveBody>) -> Response {
    use crate::bounded::{NoteError, OverrideNote, RegistryError, Resolution};
    let how = match body.action.as_str() {
        "confirmed_gone" => Resolution::ConfirmedGone,
        "retry" => Resolution::Retry,
        "acknowledge_recovery" => Resolution::AcknowledgeRecovery,
        "retry_journal" => Resolution::RetryJournal,
        _ => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error": "action must be confirmed_gone, retry, acknowledge_recovery or retry_journal"}))).into_response(),
    };
    let note = match OverrideNote::new(&body.operator, &body.reason) {
        Ok(n) => n,
        Err(NoteError::Operator) => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error": "operator is required: who is doing this (1 to 64 characters)"}))).into_response(),
        Err(NoteError::Reason) => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error": "reason is required: why (1 to 240 characters)"}))).into_response(),
    };
    match state.work.resolve(&body.pool, body.job, how, note) {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(RegistryError::NoPool) => (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": "no such pool"}))).into_response(),
        Err(RegistryError::NotRetained) => (StatusCode::CONFLICT, Json(serde_json::json!({"error": "that job is not holding a worker"}))).into_response(),
        Err(RegistryError::JournalError) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({"error": "the recovery file could not be written or put aside, so nothing was changed", "reason": "journal_error"}))).into_response(),
        Err(RegistryError::NotBlocked) => (StatusCode::CONFLICT, Json(serde_json::json!({"error": "nothing is blocked on the recovery state: it is readable and the journal's last write worked", "reason": "not_blocked"}))).into_response(),
        Err(RegistryError::NoHandle) => (StatusCode::CONFLICT, Json(serde_json::json!({"error": "that worker is held from an earlier run of the terminal: there is no process to try again; only confirmed_gone applies", "reason": "no_handle"}))).into_response(),
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
    match state.analyse.backtest(&body.symbol, body.strategy).await {
        Ok(view) => Json(view).into_response(),
        Err(e) => analyse_error_response(e),
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
        return Json(serde_json::json!({ "live": false, "quotes": [], "now": unix_ms() })).into_response();
    }
    state.deriv_quotes.want(&keys, std::time::Instant::now());
    // `now` is the server's own clock: the receipt times on the quotes are in it, so the page measures ages against it and not against a clock that may disagree
    Json(serde_json::json!({ "live": true, "quotes": state.deriv_quotes.get(&keys), "now": unix_ms() })).into_response()
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
        FundError::Overloaded => (StatusCode::SERVICE_UNAVAILABLE, "the fundamentals service is at its limit; try again shortly".to_string()),
        FundError::TimedOut => (StatusCode::GATEWAY_TIMEOUT, "the request did not finish within its time limit (the wait for a place counts against it)".to_string()),
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

/// The IPO tab: open, coming, closed and listed issues and the drafts filed with SEBI (see `athena.ipo.page`). Display only.
async fn api_ipo(State(state): State<AppState>) -> Response {
    use crate::ipo::IpoError;
    match state.ipo.page().await {
        Ok(doc) => Json(doc).into_response(),
        Err(IpoError::Busy) => (StatusCode::TOO_MANY_REQUESTS, "the IPO page is already being built; try again in a moment").into_response(),
        Err(IpoError::Failed(why)) => (StatusCode::BAD_GATEWAY, why).into_response(),
    }
}

#[derive(serde::Deserialize)]
struct ForensicBody {
    url: String,
    company: String,
    #[serde(default = "default_kind")]
    kind: String,
}

fn default_kind() -> String {
    "DRHP".into()
}

/// Start the forensic checks on one draft offer document (the address of its PDF on SEBI's site). It takes minutes, so the page polls `GET /api/forensic`.
async fn forensic_start(State(state): State<AppState>, Json(body): Json<ForensicBody>) -> Response {
    use crate::forensic_job::StartError;
    match state.forensic.start(&body.url, &body.company, &body.kind) {
        Ok(()) => (StatusCode::ACCEPTED, Json(state.forensic.status())).into_response(),
        Err(StartError::BadRequest) => (StatusCode::BAD_REQUEST, "the address must be a PDF on https://www.sebi.gov.in and the names plain words").into_response(),
        Err(StartError::Busy(other)) => (StatusCode::CONFLICT, Json(serde_json::json!({"error": "another document is being analysed", "url": other}))).into_response(),
    }
}

async fn forensic_status(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(state.forensic.status())
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
        Err(BoardError::Overloaded) => (StatusCode::SERVICE_UNAVAILABLE, "the board is at its limit; try again shortly").into_response(),
        Err(BoardError::TimedOut) => (StatusCode::GATEWAY_TIMEOUT, "the board did not finish within its time limit (the wait for a place counts against it)").into_response(),
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
        .route("/api/data-source", get(api_data_source))
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
        .route("/api/work", get(api_work))
        .route("/api/work/resolve", axum::routing::post(api_work_resolve))
        .route("/api/fund-status", get(api_fund_status))
        .route("/api/fund-fetch", get(fund_fetch_status))
        .route("/api/fund-fetch/:symbol", axum::routing::post(fund_fetch_start))
        .route("/api/fund/:symbol", get(api_fund))
        .route("/api/valuation/:symbol", get(api_valuation))
        .route("/api/flow/board", get(api_flow_board))
        .route("/api/chart/explain", axum::routing::post(api_chart_explain))
        .route("/api/sentiment/:symbol", get(api_sentiment))
        .route("/api/ipo", get(api_ipo))
        .route("/api/forensic", get(forensic_status).post(forensic_start))
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
        start_in(PathBuf::from("no/such.duckdb"), live_derivs).await
    }

    async fn start_in(warehouse: PathBuf, live_derivs: bool) -> (u16, AppState, CancellationToken) {
        start_custom(warehouse, live_derivs, Arc::new(|_s| Box::pin(async { None }))).await
    }

    /// As `start_in`, with the function that stands in for the Python helper's adjusted history (what it returns, and the provenance it reports, is the test's to choose).
    async fn start_custom(warehouse: PathBuf, live_derivs: bool, adjust: crate::bars::AdjustFetcher) -> (u16, AppState, CancellationToken) {
        start_full(warehouse, live_derivs, adjust, Arc::new(crate::analyse_worker::AnalyseWorker::new(vec!["no-such-program-xyz".into()]))).await
    }

    /// As `start_custom`, with the analysis worker the test chooses (a stand-in child that is slow, for instance).
    async fn start_full(warehouse: PathBuf, live_derivs: bool, adjust: crate::bars::AdjustFetcher, analyse: Arc<crate::analyse_worker::AnalyseWorker>) -> (u16, AppState, CancellationToken) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (status, _rx) = watch::channel(FeedStatus::On);
        let fetch: crate::bars::Fetcher = Arc::new(|_s, _t| Box::pin(async { None }));
        let bars = Arc::new(Bars { warehouse, intraday: crate::bars::Intraday::new(fetch), adjusted: crate::bars::AdjustedDaily::new(adjust) });
        let layouts = Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-layouts-{port}"))));
        let verdicts = VerdictJobs::new(vec!["no-such-program-xyz".into()]);
        let work = Arc::new(crate::bounded::WorkRegistry::new());
        work.register(analyse.pool_view());
        let fundamentals = Arc::new(FundamentalsJobs::new(vec!["no-such-program-xyz".into()], vec!["no-such-program-xyz".into()], PathBuf::from("no/such.duckdb"), PathBuf::from("no/such-f.duckdb")));
        let state = AppState { hub: Hub::new(), status, port, web_dir: std::env::temp_dir(), bars, layouts, verdicts, analyse, work, fundamentals, flow_board: Arc::new(crate::flow_board::FlowBoard::new(vec!["no-such-program-xyz".into()], PathBuf::from("no/such-derivs.duckdb"))), chart_explain: Arc::new(crate::chart_explain::ChartExplain::new(vec!["no-such-program-xyz".into()])), sentiment: Arc::new(crate::sentiment::SentimentJobs::new(vec!["no-such-program-xyz".into()], PathBuf::from("no/such.duckdb"))), ipo: Arc::new(crate::ipo::IpoJobs::new(vec!["no-such-program-xyz".into()], PathBuf::from("no/such.duckdb"))), forensic: crate::forensic_job::ForensicJobs::new(Arc::new(|_u, _c, _k| tokio::process::Command::new("no-such-program-xyz"))), indices: crate::indices::IndexStore::new(), search: Arc::new(crate::search::SearchCache::new(PathBuf::from("no/such.duckdb"))), fund_fetch: crate::fund_fetch::FundFetch::new(Arc::new(|_m, _s| tokio::process::Command::new("no-such-program-xyz"))), lists: Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-lists-{port}")))), pyind: crate::pyind::PyInd::new("no-such-python", std::path::Path::new("no/such/runner.py"), &std::env::temp_dir().join(format!("athena-srv-ind-{port}"))), derivs: crate::derivs::DerivsStore::new(), deriv_quotes: crate::deriv_quotes::DerivQuotes::new(), live_derivs };
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

    // ---- T6: what the page is told about a full queue, a deadline and a held worker; the work status and the way out of a held worker ----
    #[tokio::test]
    async fn a_full_queue_a_passed_deadline_and_a_held_worker_each_have_their_own_answer_with_the_job_id() {
        use crate::analyse_worker::AnalyseError;
        use axum::body::to_bytes;
        let body = |r: Response| async move { (r.status(), serde_json::from_slice::<serde_json::Value>(&to_bytes(r.into_body(), 1 << 20).await.unwrap()).unwrap()) };
        let (status, v) = body(analyse_error_response(AnalyseError::Busy)).await;
        assert_eq!((status, v["reason"].as_str()), (StatusCode::SERVICE_UNAVAILABLE, Some("queue_full")), "{v}");
        let (status, v) = body(analyse_error_response(AnalyseError::TimedOut(7))).await;
        assert_eq!((status, v["reason"].as_str(), v["job"].as_u64()), (StatusCode::GATEWAY_TIMEOUT, Some("deadline"), Some(7)), "{v}");
        let (status, v) = body(analyse_error_response(AnalyseError::Held)).await;
        assert_eq!((status, v["reason"].as_str()), (StatusCode::SERVICE_UNAVAILABLE, Some("cancel_unconfirmed")), "{v}");
        assert_eq!(analyse_error_response(AnalyseError::BadQuery).status(), StatusCode::BAD_REQUEST, "the earlier answers are unchanged");
        assert_eq!(analyse_error_response(AnalyseError::Unavailable).status(), StatusCode::BAD_GATEWAY);
    }

    #[tokio::test]
    async fn the_work_route_lists_each_pool_with_its_recent_jobs_and_their_queue_wait_and_running_time() {
        let (port, _state, stop) = start().await;
        let origin = format!("http://127.0.0.1:{port}");
        let r = reqwest::Client::new().post(format!("http://127.0.0.1:{port}/api/analyse")).header("Origin", &origin).json(&serde_json::json!({"query": "TCS"})).send().await.unwrap();
        assert_eq!(r.status(), 502, "the stand-in worker cannot start");
        let work = get_json(port, "/api/work").await;
        let pool = work["pools"].as_array().unwrap().iter().find(|p| p["pool"] == "analyse").cloned().expect("the analyse pool is listed");
        assert_eq!(pool["workers"], 1);
        assert!(pool["capacity"].as_u64().unwrap() >= 1);
        let job = &pool["recent"][0];
        assert_eq!((job["state"].as_str(), job["reason"].as_str()), (Some("failed"), Some("start_failed")), "{pool}");
        assert!(job["queue_wait_ms"].is_u64() && job["run_ms"].is_u64() && job["deadline_ms"].is_u64(), "{job}");
        stop.cancel();
    }

    #[tokio::test]
    async fn resolving_a_job_that_holds_no_worker_is_refused_and_a_bad_request_or_origin_is_too() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/work/resolve");
        let post = |body: serde_json::Value, origin: String| { let (c, u) = (client.clone(), url.clone()); async move { c.post(u).header("Origin", origin).json(&body).send().await.unwrap().status() } };
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "confirmed_gone", "operator": "tester", "reason": "checked by hand"}), origin.clone()).await, 409);
        assert_eq!(post(serde_json::json!({"pool": "nope", "job": 1, "action": "retry", "operator": "tester", "reason": "checked by hand"}), origin.clone()).await, 404);
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "forget"}), origin.clone()).await, 400);
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "retry", "operator": "tester", "reason": "checked by hand"}), "http://evil.test".into()).await, 403);
        stop.cancel();
    }

    async fn get_json(port: u16, path_and_query: &str) -> serde_json::Value {
        let origin = format!("http://127.0.0.1:{port}");
        let r = reqwest::Client::new().get(format!("http://127.0.0.1:{port}{path_and_query}")).header("Origin", origin).send().await.unwrap();
        assert_eq!(r.status(), 200, "{path_and_query}");
        r.json().await.unwrap()
    }

    #[tokio::test]
    async fn a_daily_answer_from_the_real_file_says_so_and_dates_its_own_data() {
        let dir = crate::snapshot::testkit::temp_dir("srv-main");
        let main = dir.join("w.duckdb");
        crate::snapshot::testkit::make_main_for(&main, "SBIN", "2026-11-13", 100.0);
        crate::snapshot::testkit::publish(&main, 0); // a snapshot exists and is not used while the real file opens
        let (port, _state, stop) = start_in(main.clone(), false).await;
        let body = get_json(port, "/api/bars?symbol=SBIN&tf=1D&adj=0").await;
        assert_eq!(body["bars"].as_array().unwrap().len(), 1);
        let p = &body["provenance"];
        assert_eq!((p["source"].as_str(), p["generation_basis"].as_str(), p["fallback_reason"].is_null()), (Some("main"), Some("observed"), true));
        assert!(p["generation_id"].as_str().unwrap().starts_with("main:"));
        assert_eq!(p["data_as_of"], "2026-11-13"); // the date of the last bar it returned
        stop.cancel();
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn a_daily_answer_from_a_snapshot_names_the_generation_the_reason_and_both_dates() {
        let dir = crate::snapshot::testkit::temp_dir("srv-snap");
        let main = dir.join("w.duckdb");
        crate::snapshot::testkit::make_main_for(&main, "ZSNAPSRV", "2026-11-13", 100.0); // a symbol of this test's own: the registry is process-wide, so what is asserted below is what THIS request registered
        let gen = crate::snapshot::testkit::publish(&main, 4);
        crate::snapshot::testkit::ruin_main(&main);
        let (port, _state, stop) = start_in(main.clone(), false).await;
        let body = get_json(port, "/api/bars?symbol=ZSNAPSRV&tf=1D&adj=0").await;
        assert_eq!(body["bars"].as_array().unwrap().len(), 1, "the validated generation answered");
        let p = &body["provenance"];
        assert_eq!((p["source"].as_str(), p["generation_id"].as_str(), p["fallback_reason"].as_str(), p["validated"].as_bool()), (Some("snapshot"), Some(gen.as_str()), Some("main_unreadable"), Some(true)));
        assert_eq!(p["snapshot_created_at"], "2026-11-15T10:15:04Z");
        assert_eq!(p["data_as_of"], "2026-11-13"); // what the bars stop at, not when the copy was taken
        assert_eq!(p["snapshot_data_as_of"]["daily_bars"], "2026-11-13");
        // what /api/data-source then says about this request's reader and subject (`adj=0` registers `bars:<SYMBOL>:raw`): the same generation, source and reason the response carried
        let s = get_json(port, "/api/data-source").await;
        let entry = &s["readers"]["bars:ZSNAPSRV:raw"];
        assert!(entry.is_object(), "this request registered no entry of its own: {s}");
        assert_eq!((entry["source"].as_str(), entry["generation_id"].as_str(), entry["fallback_reason"].as_str(), entry["validated"].as_bool()), (Some("snapshot"), Some(gen.as_str()), Some("main_unreadable"), Some(true)), "{s}");
        assert_eq!(entry["data_as_of"], "2026-11-13");
        assert_eq!(entry["snapshot_data_as_of"]["daily_bars"], "2026-11-13");
        assert!(s["recent_generations"].as_array().unwrap().iter().any(|g| g == gen.as_str()), "{s}");
        stop.cancel();
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn a_daily_answer_with_no_usable_data_says_why_instead_of_naming_a_source() {
        let dir = crate::snapshot::testkit::temp_dir("srv-fail");
        let main = dir.join("w.duckdb");
        crate::snapshot::testkit::make_main_for(&main, "SBIN", "2026-11-13", 100.0);
        crate::snapshot::testkit::ruin_main(&main); // no snapshot was ever published
        let (port, _state, stop) = start_in(main.clone(), false).await;
        let body = get_json(port, "/api/bars?symbol=SBIN&tf=1D&adj=0").await;
        assert_eq!(body["note"], "warehouse busy"); // the existing wording is unchanged
        assert!(body["bars"].as_array().unwrap().is_empty());
        let f = &body["provenance"]["failure"];
        assert_eq!((f["main_reason"].as_str(), f["snapshot_reason"].as_str()), (Some("main_unreadable"), Some("absent")));
        stop.cancel();
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn the_data_source_endpoint_reports_each_readers_last_answer_and_whether_they_differ() {
        let dir = crate::snapshot::testkit::temp_dir("srv-sources");
        let main = dir.join("w.duckdb");
        crate::snapshot::testkit::make_main_for(&main, "ZSRCMAIN", "2026-11-13", 100.0); // a symbol of this test's own: the registry is process-wide
        let (port, _state, stop) = start_in(main.clone(), false).await;
        let body = get_json(port, "/api/bars?symbol=ZSRCMAIN&tf=1D&adj=0").await;
        let s = get_json(port, "/api/data-source").await;
        let entry = &s["readers"]["bars:ZSRCMAIN:raw"]; // `adj=0` registers `bars:<SYMBOL>:raw`
        assert!(entry.is_object(), "this request registered no entry of its own: {s}");
        assert_eq!((entry["source"].as_str(), entry["generation_basis"].as_str(), entry["fallback_reason"].is_null()), (Some("main"), Some("observed"), true), "{s}");
        assert_eq!(entry["generation_id"], body["provenance"]["generation_id"], "the registry holds the generation this answer was served with: {s}");
        assert!(entry["generation_id"].as_str().unwrap().starts_with("main:"), "{s}");
        assert_eq!(entry["data_as_of"], "2026-11-13");
        assert!(s["mixed_generations"].is_boolean() && s["window_seconds"].is_u64() && s["recent_generations"].is_array());
        // a request from another site is refused like every other API request
        let refused = reqwest::Client::new().get(format!("http://127.0.0.1:{port}/api/data-source")).header("Origin", "http://evil.test").send().await.unwrap();
        assert_eq!(refused.status(), 403);
        stop.cancel();
        std::fs::remove_dir_all(&dir).ok();
    }

    // ---- T5-R3: what is registered is what was served, at the serving boundary (the registry is process-wide, so each test uses symbols of its own) ----
    /// The provenance the Python helper reports for the history it computed: a snapshot generation `gen`, taken while a writer held the file.
    fn helper_provenance(gen: &str) -> serde_json::Value {
        serde_json::json!({"source": "snapshot", "source_path": format!("w.snap.{gen}.duckdb"), "generation_id": gen, "generation_basis": "manifest", "snapshot_created_at": "2026-11-15T10:15:00Z",
            "data_as_of": {"daily_bars": "2026-11-13", "index_bars": null}, "fallback_reason": "locked_by_writer", "validated": true, "snapshot_status": null})
    }

    /// A stand-in for the helper: `gens` maps a symbol to the generation its history comes from; the counter counts the helper's runs.
    fn helper_with(gens: &'static [(&'static str, &'static str)], runs: Arc<std::sync::atomic::AtomicUsize>) -> crate::bars::AdjustFetcher {
        Arc::new(move |symbol: String| {
            let runs = runs.clone();
            Box::pin(async move {
                runs.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                let gen = gens.iter().find(|(s, _)| *s == symbol).map(|(_, g)| *g)?;
                let bar = crate::bars::Bar { t: 1_794_528_000, o: 100.0, h: 101.0, l: 99.0, c: 100.5, v: 10.0 }; // 2026-11-13
                Some(crate::bars::Adjusted { bars: vec![bar], flags: vec![], joined: vec![], provenance: Some(helper_provenance(gen)) })
            })
        })
    }

    #[tokio::test]
    async fn the_default_adjusted_answer_is_registered_with_the_provenance_it_was_served_with() {
        let runs = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let (port, _state, stop) = start_custom(PathBuf::from("no/such.duckdb"), false, helper_with(&[("ZADJA", "20261115T101500Z-a1b2c3")], runs)).await;
        let body = get_json(port, "/api/bars?symbol=ZADJA&tf=1D").await; // the default path: adjusted, not `adj=0`
        assert_eq!(body["provenance"]["generation_id"], "20261115T101500Z-a1b2c3");
        let s = get_json(port, "/api/data-source").await;
        let entry = &s["readers"]["bars:ZADJA:adjusted"];
        assert_eq!((entry["source"].as_str(), entry["generation_id"].as_str(), entry["fallback_reason"].as_str()), (Some("snapshot"), Some("20261115T101500Z-a1b2c3"), Some("locked_by_writer")), "{s}");
        assert_eq!(entry["data_as_of"], "2026-11-13"); // the date of the last bar it served; the manifest's per-dataset dates are kept beside it
        assert_eq!(entry["snapshot_data_as_of"]["daily_bars"], "2026-11-13");
        assert!(s["recent_generations"].as_array().unwrap().iter().any(|g| g == "20261115T101500Z-a1b2c3"));
        stop.cancel();
    }

    #[tokio::test]
    async fn a_cache_hit_renews_the_served_provenance_and_says_it_was_cached() {
        let runs = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let (port, _state, stop) = start_custom(PathBuf::from("no/such.duckdb"), false, helper_with(&[("ZCACHE", "20261115T101500Z-a1b2c3")], runs.clone())).await;
        let first = get_json(port, "/api/bars?symbol=ZCACHE&tf=1D").await;
        let at_first = get_json(port, "/api/data-source").await["readers"]["bars:ZCACHE:adjusted"]["at"].as_u64().unwrap();
        assert_eq!(first["provenance"]["cache"]["hit"], false);
        tokio::time::sleep(Duration::from_millis(1100)).await;
        let second = get_json(port, "/api/bars?symbol=ZCACHE&tf=1D").await;
        assert_eq!(runs.load(std::sync::atomic::Ordering::SeqCst), 1, "the second answer came from the cache");
        assert_eq!(second["provenance"]["cache"]["hit"], true);
        assert_eq!(second["provenance"]["generation_id"], "20261115T101500Z-a1b2c3", "the cached answer keeps the generation it was computed from");
        let entry = get_json(port, "/api/data-source").await["readers"]["bars:ZCACHE:adjusted"].clone();
        assert!(entry["at"].as_u64().unwrap() > at_first, "serving it again renews the entry, so a cache that keeps serving is never described as an old answer");
        assert_eq!(entry["recent"], true);
        stop.cancel();
    }

    #[tokio::test]
    async fn two_symbols_served_different_generations_through_one_reader_are_both_kept_and_flagged_mixed() {
        let runs = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let gens: &'static [(&str, &str)] = &[("ZMIXA", "20261115T101500Z-aaaaaa"), ("ZMIXB", "20261115T102000Z-bbbbbb")];
        let (port, _state, stop) = start_custom(PathBuf::from("no/such.duckdb"), false, helper_with(gens, runs)).await;
        get_json(port, "/api/bars?symbol=ZMIXA&tf=1D").await;
        get_json(port, "/api/bars?symbol=ZMIXB&tf=1D").await;
        let s = get_json(port, "/api/data-source").await;
        assert_eq!(s["readers"]["bars:ZMIXA:adjusted"]["generation_id"], "20261115T101500Z-aaaaaa", "{s}");
        assert_eq!(s["readers"]["bars:ZMIXB:adjusted"]["generation_id"], "20261115T102000Z-bbbbbb", "{s}");
        let recent = s["recent_generations"].as_array().unwrap();
        assert!(recent.iter().any(|g| g == "20261115T101500Z-aaaaaa") && recent.iter().any(|g| g == "20261115T102000Z-bbbbbb"));
        assert_eq!(s["mixed_generations"], true);
        stop.cancel();
    }

    #[tokio::test]
    async fn the_data_source_endpoint_carries_the_last_publication_status_even_when_the_real_file_answers() {
        let dir = crate::snapshot::testkit::temp_dir("srv-publication");
        let main = dir.join("w.duckdb");
        crate::snapshot::testkit::make_main_for(&main, "ZPUBL", "2026-11-13", 100.0);
        crate::snapshot::testkit::atomic_write(&crate::snapshot::status_path(&main), &serde_json::json!({"schema": "athena.snapshot-status/1", "last_attempt": {"outcome": "failed", "stage": "validate", "error": "x", "current_unusable": "hash_mismatch"}, "last_success": null, "current_generation": null}).to_string());
        let (port, _state, stop) = start_in(main.clone(), false).await;
        let s = get_json(port, "/api/data-source").await;
        assert_eq!(s["snapshot_publication"]["last_attempt"]["outcome"], "failed", "{s}");
        assert_eq!(s["snapshot_publication"]["last_attempt"]["stage"], "validate");
        stop.cancel();
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_daily_answers_date_is_the_last_bar_and_a_snapshots_manifest_dates_are_kept_apart() {
        let p = serde_json::json!({"source": "snapshot", "data_as_of": {"daily_bars": "2026-11-12"}, "snapshot_created_at": "2026-11-15T10:15:00Z"});
        let got = bars_provenance(Some(p), Some(1_794_528_000)); // 2026-11-13 00:00 UTC (20,770 days after the epoch)
        assert_eq!(got["data_as_of"], "2026-11-13");
        assert_eq!(got["snapshot_data_as_of"]["daily_bars"], "2026-11-12");
        assert_eq!(got["snapshot_created_at"], "2026-11-15T10:15:00Z");
        assert!(bars_provenance(None, Some(0)).is_null(), "a helper that did not report provenance is reported as none, not invented");
        assert_eq!(bars_provenance(Some(serde_json::json!({"source": "main", "data_as_of": null})), None)["data_as_of"], serde_json::Value::Null);
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

    #[tokio::test]
    async fn the_derivative_quotes_route_carries_the_receipt_book_and_trade_times_and_the_servers_clock_and_does_not_make_a_repeat_newer() {
        let (port, state, stop) = start_with(true).await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/derivs/quotes");
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:778")).send().await.unwrap().json().await.unwrap();
        assert!(body["now"].as_i64().unwrap() > 1_700_000_000_000, "the server's own clock, so the page can measure ages in it: {body}");
        let packet = |ms: i64, last: i64| crate::angel::decode::decode(&crate::angel::decode::test_packet_on(2, "778", ms, last, 9_000, &[(1, 650, 12_340), (0, 130, 12_350)], 1_000, 0.0)).unwrap();
        state.deriv_quotes.update_at(1_760_000_000_500, 2, crate::deriv_quotes::DerivQuote::from_tick("NFO", 2, &packet(1_760_000_000_000, 12_345)));
        state.deriv_quotes.update_at(1_760_000_090_000, 2, crate::deriv_quotes::DerivQuote::from_tick("NFO", 2, &packet(1_760_000_000_000, 12_345))); // the same packet again, a minute and a half later
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:778")).send().await.unwrap().json().await.unwrap();
        let q = &body["quotes"][0];
        assert_eq!((q["t"].as_i64(), q["r"].as_i64(), q["bs"].as_i64(), q["ooo"].as_i64()), (Some(1_760_000_000_000), Some(1_760_000_000_500), Some(1_760_000_000_500), Some(0)));
        assert!(q["dly"].is_null());
        stop.cancel();
    }

    #[tokio::test]
    async fn the_derivative_quotes_route_sends_the_packet_time_and_the_last_trade_time_apart_and_the_reason_a_time_was_refused() {
        use crate::angel::decode::{decode, test_packet_on, with_sequence, with_trade_time};
        use crate::deriv_quotes::DerivQuote;
        let (port, state, stop) = start_with(true).await;
        let client = reqwest::Client::new();
        let base = format!("http://127.0.0.1:{port}/api/derivs/quotes");
        let _ = client.get(format!("{base}?tokens=NFO:779")).send().await.unwrap(); // marks the contract wanted
        let packet = |ms: i64, trade_raw: i64, seq: i64, last: i64| {
            let p = with_sequence(with_trade_time(test_packet_on(2, "779", ms, last, 9_000, &[(1, 650, 12_340), (0, 130, 12_350)], 1_000, 0.0), trade_raw), seq);
            DerivQuote::from_tick("NFO", 2, &decode(&p).unwrap())
        };
        // the packet is stamped a minute after the last trade; the stream sent the trade time in seconds
        state.deriv_quotes.update_at(1_760_000_061_000, 2, packet(1_760_000_060_000, 1_760_000_000, 1, 12_345));
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:779")).send().await.unwrap().json().await.unwrap();
        let q = &body["quotes"][0];
        assert_eq!((q["t"].as_i64(), q["lt"].as_i64(), q["r"].as_i64()), (Some(1_760_000_060_000), Some(1_760_000_000_000), Some(1_760_000_061_000)), "{q}");
        assert!(q.get("tf").is_none());
        // a malformed last-traded field: no trade time, and the refusal is on the wire
        state.deriv_quotes.update_at(1_760_000_091_000, 2, packet(1_760_000_090_000, 5, 2, 12_350));
        let body: serde_json::Value = client.get(format!("{base}?tokens=NFO:779")).send().await.unwrap().json().await.unwrap();
        let q = &body["quotes"][0];
        assert!(q["lt"].is_null(), "{q}");
        assert_eq!(q["tf"], serde_json::json!([{"f": "lt", "v": 5, "why": "implausible"}]));
        stop.cancel();
    }

    #[test]
    fn the_calendar_defaults_to_the_india_date_and_thirty_days_and_keeps_what_was_asked_for() {
        let today = chrono::NaiveDate::from_ymd_opt(2026, 10, 19).unwrap();
        assert_eq!(calendar_range(today, None, None), ("2026-10-19".to_string(), "2026-11-18".to_string()));
        assert_eq!(calendar_range(today, Some("2026-01-01".into()), None), ("2026-01-01".to_string(), "2026-11-18".to_string()));
        assert_eq!(calendar_range(today, None, Some("2026-10-25".into())), ("2026-10-19".to_string(), "2026-10-25".to_string()));
    }

    // ---- the indicator route's request-size limit ----
    //
    // These replace one test that sent 5 MB over a real socket to a route that answers 413 and closes before the body is read: the client's write could meet the connection's reset, and the test then failed by chance
    // (a Windows "connection aborted", seen in about a third of the runs of one session). A connection error is not an answer to a size limit, so no test here counts one as success. The exact sizes are decided in
    // process, by sending a request straight through the router (no socket); a real connection is used only for the case that does not race, a body the server reads to the end.

    /// The route's own limit: `DefaultBodyLimit::max(MAX_BODY + 64 KiB)` (see `router`).
    const BODY_LIMIT: usize = crate::pyind::MAX_BODY + 64 * 1024;

    /// A request body of exactly `len` bytes that the route can parse: the file name and no bars, padded in a field the route ignores.
    fn run_body(len: usize) -> Vec<u8> {
        let (head, tail) = (r#"{"file":"x.py","bars":[],"pad":""#, r#""}"#);
        assert!(len >= head.len() + tail.len());
        let mut body = Vec::with_capacity(len);
        body.extend_from_slice(head.as_bytes());
        body.resize(len - tail.len(), b'a');
        body.extend_from_slice(tail.as_bytes());
        assert_eq!(body.len(), len);
        body
    }

    /// A request whose bars serialise to exactly `bars_len` bytes (`[1,1,1]` is 2n+1 bytes; an even length has one two-digit element).
    fn bars_body(bars_len: usize) -> Vec<u8> {
        let (ones, last) = if bars_len % 2 == 1 { ((bars_len - 1) / 2, "1") } else { ((bars_len - 2) / 2, "11") };
        let mut elements = "1,".repeat(ones - 1);
        elements.push_str(last);
        let body = format!(r#"{{"file":"x.py","bars":[{elements}]}}"#).into_bytes();
        assert_eq!(serde_json::to_vec(&serde_json::from_slice::<serde_json::Value>(&body).unwrap()["bars"]).unwrap().len(), bars_len);
        body
    }

    /// One request through the router itself: the same layers and the same limit as the server, with no socket in between.
    async fn through_the_router(state: &AppState, body: axum::body::Body) -> (StatusCode, Vec<u8>) {
        use tower::ServiceExt;
        let port = state.port;
        let request = axum::http::Request::builder()
            .method("POST")
            .uri("/api/pyind/run")
            .header("host", format!("127.0.0.1:{port}"))
            .header("origin", format!("http://127.0.0.1:{port}"))
            .header("content-type", "application/json")
            .body(body)
            .unwrap();
        let response = router(state.clone()).oneshot(request).await.unwrap();
        let status = response.status();
        (status, axum::body::to_bytes(response.into_body(), 1 << 20).await.unwrap().to_vec())
    }

    /// What the handler says when it is reached and the indicator is not switched on: 403 and a JSON error. Nothing was executed.
    fn reached_the_handler(status: StatusCode, body: &[u8], why: &str) {
        let shown = String::from_utf8_lossy(&body[..body.len().min(200)]).into_owned();
        assert_eq!(status, StatusCode::FORBIDDEN, "{why}: the handler answers 403 when the indicator is not switched on; got: {shown}");
        let json = serde_json::from_slice::<serde_json::Value>(body).ok();
        assert_eq!(json.is_some(), true, "{why}: the reply is not the handler's JSON: {shown}");
        let json = json.unwrap();
        assert_eq!(json["error"].as_str().is_some_and(|e| e.contains("not switched on")), true, "{why}: {json}");
    }

    /// What the framework says when the body is past the limit: 413 and its own plain text, which is not the handler's JSON (the handler never ran).
    fn refused_before_the_handler(status: StatusCode, body: &[u8], why: &str) {
        assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE, "{why}");
        assert_eq!(serde_json::from_slice::<serde_json::Value>(body).is_err(), true, "{why}: a JSON reply would be the handler's: {}", String::from_utf8_lossy(&body[..body.len().min(200)]));
    }

    #[tokio::test]
    async fn the_indicator_run_route_passes_exactly_what_is_inside_its_limit_and_refuses_the_next_byte_before_the_handler() {
        let (_port, state, stop) = start().await;
        for (len, why) in [(3 * 1024 * 1024, "3 MB"), (BODY_LIMIT - 1, "one byte under the limit"), (BODY_LIMIT, "exactly the limit")] {
            let (status, body) = through_the_router(&state, axum::body::Body::from(run_body(len))).await;
            reached_the_handler(status, &body, why);
        }
        for (len, why) in [(BODY_LIMIT + 1, "one byte over the limit"), (5 * 1024 * 1024, "5 MB")] {
            let (status, body) = through_the_router(&state, axum::body::Body::from(run_body(len))).await;
            refused_before_the_handler(status, &body, why);
        }
        stop.cancel();
    }

    #[tokio::test]
    async fn a_body_with_no_declared_length_is_stopped_at_the_same_limit_while_it_is_read() {
        let (_port, state, stop) = start().await;
        let streamed = |len: usize| {
            let body = run_body(len);
            let chunks: Vec<Result<axum::body::Bytes, std::io::Error>> = body.chunks(64 * 1024).map(|c| Ok(axum::body::Bytes::copy_from_slice(c))).collect();
            axum::body::Body::from_stream(futures_util::stream::iter(chunks))
        };
        let (status, body) = through_the_router(&state, streamed(BODY_LIMIT)).await;
        reached_the_handler(status, &body, "streamed, exactly the limit");
        let (status, body) = through_the_router(&state, streamed(BODY_LIMIT + 1)).await;
        refused_before_the_handler(status, &body, "streamed, one byte over the limit");
        stop.cancel();
    }

    #[tokio::test]
    async fn the_handlers_own_four_megabyte_limit_on_the_bars_is_a_separate_check_inside_the_framework_limit() {
        let (_port, state, stop) = start().await;
        let max = crate::pyind::MAX_BODY;
        let (status, body) = through_the_router(&state, axum::body::Body::from(bars_body(max))).await;
        reached_the_handler(status, &body, "bars of exactly 4 MB");
        let (status, body) = through_the_router(&state, axum::body::Body::from(bars_body(max + 1))).await;
        let shown = String::from_utf8_lossy(&body[..body.len().min(200)]).into_owned();
        let json = serde_json::from_slice::<serde_json::Value>(&body).ok();
        assert_eq!((status, json.as_ref().and_then(|j| j["error"].as_str()).is_some_and(|e| e.contains("at most 4 MB"))), (StatusCode::BAD_REQUEST, true), "bars one byte past 4 MB are the handler's refusal (400, in JSON), not the framework's (413): {shown}");
        stop.cancel();
    }

    #[tokio::test]
    async fn a_three_megabyte_indicator_request_reaches_the_handler_over_a_real_connection() {
        let (port, _state, stop) = start().await;
        let reply = reqwest::Client::new().post(format!("http://127.0.0.1:{port}/api/pyind/run")).header("Origin", format!("http://127.0.0.1:{port}")).header("content-type", "application/json").body(run_body(3 * 1024 * 1024)).send().await.expect("a body the server reads to the end is answered");
        let status = reply.status();
        let body = reply.bytes().await.unwrap();
        reached_the_handler(status, &body, "3 MB over a socket");
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
        assert_eq!(client.get(format!("{base}/ipo")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(format!("{base}/ipo")).send().await.unwrap().status(), 502);
        // the Analyse button: the address is checked before anything runs, a hostile page is refused, and a job whose program does not exist ends as a fixed failure
        let analyse = |origin: Option<&str>, body: &str| {
            let mut r = client.post(format!("{base}/forensic")).header("content-type", "application/json").body(body.to_string());
            if let Some(o) = origin {
                r = r.header("Origin", o);
            }
            r.send()
        };
        let good = r#"{"url": "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1_draft.pdf", "company": "Deon Energy Limited"}"#;
        assert_eq!(client.get(format!("{base}/forensic")).header("Origin", "http://evil.test").send().await.unwrap().status(), 403);
        assert_eq!(client.get(format!("{base}/forensic")).send().await.unwrap().json::<serde_json::Value>().await.unwrap()["state"], "idle");
        assert_eq!(analyse(Some("http://evil.test"), good).await.unwrap().status(), 403);
        assert_eq!(analyse(None, r#"{"url": "https://evil.test/a.pdf", "company": "Deon"}"#).await.unwrap().status(), 400);
        assert_eq!(analyse(None, r#"{"url": "https://www.sebi.gov.in/a.pdf", "company": "a;b"}"#).await.unwrap().status(), 400);
        assert_eq!(analyse(None, "{}").await.unwrap().status(), 422); // a body without the fields is not a request
        assert_eq!(analyse(None, good).await.unwrap().status(), 202);
        let mut last = serde_json::Value::Null;
        for _ in 0..100 {
            last = client.get(format!("{base}/forensic")).send().await.unwrap().json().await.unwrap();
            if last["state"] != "running" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert_eq!((last["state"].as_str(), last["error"].as_str()), (Some("failed"), Some("the analysis could not be started")));
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

    // ---- T6 review: who and why for an override, the new refusals, and the operation kind in the work status ----
    #[tokio::test]
    async fn an_override_without_an_operator_or_a_reason_is_refused_before_anything_else_is_looked_at() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/work/resolve");
        let post = |body: serde_json::Value| { let (c, u, o) = (client.clone(), url.clone(), origin.clone()); async move { c.post(u).header("Origin", o).json(&body).send().await.unwrap().status() } };
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "retry"})).await, 400, "no operator, no reason");
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "retry", "operator": "asha"})).await, 400, "no reason");
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "retry", "operator": "  ", "reason": "x"})).await, 400, "a blank operator");
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "retry", "operator": "asha", "reason": "x".repeat(241)})).await, 400, "a reason past its size");
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 1, "action": "retry", "operator": "asha", "reason": "looked"})).await, 409, "with both, the request is looked at (and no job holds a worker)");
        stop.cancel();
    }

    #[tokio::test]
    async fn a_failure_with_an_unconfirmed_worker_and_a_closed_service_have_their_own_answers() {
        use crate::analyse_worker::AnalyseError;
        use axum::body::to_bytes;
        let body = |r: Response| async move { (r.status(), serde_json::from_slice::<serde_json::Value>(&to_bytes(r.into_body(), 1 << 20).await.unwrap()).unwrap()) };
        let (status, v) = body(analyse_error_response(AnalyseError::Unconfirmed(9))).await;
        assert_eq!((status, v["reason"].as_str(), v["job"].as_u64()), (StatusCode::BAD_GATEWAY, Some("unconfirmed"), Some(9)), "{v}");
        let (status, v) = body(analyse_error_response(AnalyseError::Closed)).await;
        assert_eq!((status, v["reason"].as_str()), (StatusCode::SERVICE_UNAVAILABLE, Some("shutting_down")), "{v}");
        let (status, _) = body(fund_error(FundError::Overloaded)).await;
        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
        let (status, _) = body(fund_error(FundError::TimedOut)).await;
        assert_eq!(status, StatusCode::GATEWAY_TIMEOUT);
    }

    #[tokio::test]
    async fn the_work_status_says_what_kind_of_work_each_job_was_and_totals_each_kind() {
        let (port, _state, stop) = start().await;
        let origin = format!("http://127.0.0.1:{port}");
        let client = reqwest::Client::new();
        let _ = client.post(format!("http://127.0.0.1:{port}/api/analyse")).header("Origin", &origin).json(&serde_json::json!({"query": "SECRET-QUERY-77"})).send().await.unwrap();
        let _ = client.post(format!("http://127.0.0.1:{port}/api/backtest")).header("Origin", &origin).json(&serde_json::json!({"symbol": "SBIN"})).send().await.unwrap();
        let work = get_json(port, "/api/work").await;
        let pool = work["pools"].as_array().unwrap().iter().find(|p| p["pool"] == "analyse").cloned().expect("listed");
        let kinds: Vec<&str> = pool["recent"].as_array().unwrap().iter().filter_map(|j| j["kind"].as_str()).collect();
        assert_eq!(kinds, vec!["backtest", "view"], "newest first, each with its kind: {pool}");
        assert_eq!((pool["kinds"]["view"]["submitted"].as_u64(), pool["kinds"]["backtest"]["submitted"].as_u64()), (Some(1), Some(1)), "{pool}");
        assert_eq!(work.to_string().contains("SECRET-QUERY-77"), false, "what a person typed is not in the status");
        stop.cancel();
    }

    // ---- T6 review 2: what a client that goes away does to its request (a real HTTP connection and a stand-in worker child) ----
    fn slow_analyse() -> Option<Arc<crate::analyse_worker::AnalyseWorker>> {
        let python = ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists())?;
        let code = r#"
import json, sys, time, os
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    time.sleep(1.5)
    print(json.dumps({"id": r["id"], "ok": True, "result": {"pid": os.getpid()}}), flush=True)
"#;
        Some(Arc::new(crate::analyse_worker::AnalyseWorker::new(vec![python.into(), "-c".into(), code.into()])))
    }

    async fn analyse_pool(port: u16) -> serde_json::Value {
        let work = get_json(port, "/api/work").await;
        work["pools"].as_array().unwrap().iter().find(|p| p["pool"] == "analyse").cloned().expect("the analyse pool is listed")
    }

    #[tokio::test]
    async fn a_client_that_disconnects_from_a_running_analysis_lets_it_finish_and_the_worker_keeps_its_child() {
        let Some(analyse) = slow_analyse() else { return };
        analyse.warm().await;
        let (port, _state, stop) = start_full(PathBuf::from("no/such.duckdb"), false, Arc::new(|_s| Box::pin(async { None })), analyse).await;
        let origin = format!("http://127.0.0.1:{port}");
        let impatient = reqwest::Client::builder().timeout(Duration::from_millis(500)).build().unwrap();
        let gone = impatient.post(format!("http://127.0.0.1:{port}/api/analyse")).header("Origin", &origin).json(&serde_json::json!({"query": "TCS"})).send().await;
        assert_eq!(gone.is_err(), true, "the client gave up before the 1.5 s answer");
        tokio::time::sleep(Duration::from_millis(2_500)).await; // the analysis is read-only: it finishes, and its result is discarded
        let pool = analyse_pool(port).await;
        let job = &pool["recent"][0];
        assert_eq!((job["state"].as_str(), job["abandoned"].as_bool(), job["child"].as_u64()), (Some("done"), Some(true), Some(1)), "the job ran to the end and is marked abandoned: {pool}");
        assert_eq!(pool["totals"]["cancelled"], 0, "{pool}");
        // the same child serves the next request: it was not restarted
        let r = reqwest::Client::new().post(format!("http://127.0.0.1:{port}/api/analyse")).header("Origin", &origin).json(&serde_json::json!({"query": "INFY"})).send().await.unwrap();
        assert_eq!(r.status(), 200);
        assert_eq!(analyse_pool(port).await["recent"][0]["child"], 1, "no new child was started");
        stop.cancel();
    }

    #[tokio::test]
    async fn a_client_that_disconnects_while_its_analysis_is_queued_cancels_the_queued_job_and_the_running_one_is_untouched() {
        let Some(analyse) = slow_analyse() else { return };
        analyse.warm().await;
        let (port, _state, stop) = start_full(PathBuf::from("no/such.duckdb"), false, Arc::new(|_s| Box::pin(async { None })), analyse).await;
        let origin = format!("http://127.0.0.1:{port}");
        let first = tokio::spawn({ let (o, p) = (origin.clone(), port); async move { reqwest::Client::new().post(format!("http://127.0.0.1:{p}/api/analyse")).header("Origin", o).json(&serde_json::json!({"query": "AAA"})).send().await.unwrap().status() } });
        tokio::time::sleep(Duration::from_millis(300)).await; // it is running; the next one waits
        let impatient = reqwest::Client::builder().timeout(Duration::from_millis(500)).build().unwrap();
        let gone = impatient.post(format!("http://127.0.0.1:{port}/api/analyse")).header("Origin", &origin).json(&serde_json::json!({"query": "BBB"})).send().await;
        assert_eq!(gone.is_err(), true);
        assert_eq!(first.await.unwrap(), 200, "the running analysis finished normally");
        let pool = analyse_pool(port).await;
        let states: Vec<(&str, &str)> = pool["recent"].as_array().unwrap().iter().map(|j| (j["state"].as_str().unwrap_or(""), j["reason"].as_str().unwrap_or(""))).collect();
        assert_eq!(states, vec![("cancelled", "cancelled_while_queued"), ("done", "ok"), ("done", "ok")], "the queued request was cancelled when its client went; the warm-up and the first request were done: {pool}");
        stop.cancel();
    }

    #[tokio::test]
    async fn acknowledging_recovery_when_nothing_is_blocked_is_refused_and_a_bad_action_is_still_a_bad_request() {
        let (port, _state, stop) = start().await;
        let client = reqwest::Client::new();
        let origin = format!("http://127.0.0.1:{port}");
        let url = format!("http://127.0.0.1:{port}/api/work/resolve");
        let post = |body: serde_json::Value| { let (c, u, o) = (client.clone(), url.clone(), origin.clone()); async move { c.post(u).header("Origin", o).json(&body).send().await.unwrap().status() } };
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 0, "action": "acknowledge_recovery", "operator": "asha", "reason": "looked"})).await, 409, "the action is known, and nothing is blocked");
        let reply = client.post(url.clone()).header("Origin", origin.clone()).json(&serde_json::json!({"pool": "analyse", "job": 0, "action": "retry_journal", "operator": "asha", "reason": "looked"})).send().await.unwrap();
        assert_eq!(reply.status(), 409, "retry_journal is a known action, and no write has failed");
        let body: serde_json::Value = reply.json().await.unwrap();
        assert_eq!(body["reason"], "not_blocked", "it is refused as nothing being blocked (a retry of a held worker would say it holds none): {body}");
        assert_eq!(post(serde_json::json!({"pool": "analyse", "job": 0, "action": "forget", "operator": "asha", "reason": "looked"})).await, 400);
        stop.cancel();
    }
}
