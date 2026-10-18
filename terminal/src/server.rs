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
    pub indices: crate::indices::IndexStore,
    pub search: Arc<crate::search::SearchCache>,
    pub fund_fetch: Arc<crate::fund_fetch::FundFetch>,
    /// Documents the page keeps on the server beside the layouts (the watchlists), one fixed name each.
    pub lists: Arc<LayoutStore>,
}

/// An Origin header is acceptable only when it is this server's own page.
pub fn origin_ok(origin: Option<&str>, port: u16) -> bool {
    matches!(origin, Some(o) if o == format!("http://127.0.0.1:{port}") || o == format!("http://localhost:{port}"))
}

/// The Host header must name this server; this blocks a hostile web page that points its own name at 127.0.0.1.
pub fn host_ok(host: Option<&str>, port: u16) -> bool {
    matches!(host, Some(h) if h == format!("127.0.0.1:{port}") || h == format!("localhost:{port}"))
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
            None => path != "/ws",
        };
        if !origin_fine || !host_ok(header(headers, "host"), state.port) {
            return StatusCode::FORBIDDEN.into_response();
        }
    }
    next.run(request).await
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
    Json(v)
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
    !symbol.is_empty() && symbol.len() <= 24 && symbol.chars().all(|c| c.is_ascii_alphanumeric() || " &-^".contains(c))
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
        let (bars, loading) = state.bars.intraday.get(&symbol, &tf);
        let note = if loading {
            "loading".to_string()
        } else if bars.is_empty() {
            format!("No {tf} bars for {symbol}: the free intraday source has none for it. Daily bars only.")
        } else {
            String::new()
        };
        (bars, "yahoo", note)
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
}

/// The built-in rules replayed over the symbol's history (up to eight years), each against buy and hold. Takes a few seconds to a minute.
async fn api_backtest(State(state): State<AppState>, Json(body): Json<BacktestBody>) -> Response {
    use crate::analyse_worker::AnalyseError;
    match state.analyse.backtest(&body.symbol).await {
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

/// Board meetings and corporate actions for a range of dates (default: today and the next 30 days).
async fn api_calendar(State(state): State<AppState>, Query(q): Query<CalendarQuery>) -> Response {
    let today = chrono::Utc::now().date_naive();
    let from = q.from.unwrap_or_else(|| today.to_string());
    let to = q.to.unwrap_or_else(|| (today + chrono::Duration::days(30)).to_string());
    let path = state.bars.warehouse.clone();
    match tokio::task::spawn_blocking(move || crate::calendar::calendar(&path, &from, &to)).await {
        Ok(Ok(body)) => Json(body).into_response(),
        Ok(Err(crate::calendar::CalendarError::BadRange)) => (StatusCode::BAD_REQUEST, "the dates must be YYYY-MM-DD, in order, at most 366 days apart").into_response(),
        _ => (StatusCode::SERVICE_UNAVAILABLE, "the warehouse is busy").into_response(),
    }
}

async fn api_screen(State(state): State<AppState>) -> Response {
    let path = state.bars.warehouse.clone();
    match tokio::task::spawn_blocking(move || crate::analysis::screen_all(&path)).await {
        Ok(Ok(body)) => Json(body).into_response(),
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

async fn api_fund_status(State(state): State<AppState>) -> Response {
    let jobs = state.fundamentals.clone();
    match tokio::task::spawn_blocking(move || jobs.status()).await {
        Ok(Ok(body)) => Json(body).into_response(),
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
    loop {
        tokio::select! {
            _ = tick.tick() => {
                if let Some(frame) = hub.frame(id) {
                    if socket.send(Message::Text(frame.json().into())).await.is_err() { break; }
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
        .route("/api/bars", get(api_bars))
        .route("/api/analysis", get(api_analysis))
        .route("/api/known", get(api_known))
        .route("/api/screen", get(api_screen))
        .route("/api/calendar", get(api_calendar))
        .route("/api/indices", get(api_indices))
        .route("/api/search", get(api_search))
        .route("/api/watchlists", get(watchlists_get).put(watchlists_put))
        .route("/api/screens", get(screens_get).put(screens_put))
        .route("/api/drawings/:symbol", get(drawings_get).put(drawings_put))
        .route("/api/analyse", axum::routing::post(api_analyse))
        .route("/api/backtest", axum::routing::post(api_backtest))
        .route("/api/fund-status", get(api_fund_status))
        .route("/api/fund-fetch", get(fund_fetch_status))
        .route("/api/fund-fetch/:symbol", axum::routing::post(fund_fetch_start))
        .route("/api/fund/:symbol", get(api_fund))
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
        assert!(host_ok(Some("127.0.0.1:8700"), 8700));
        assert!(host_ok(Some("localhost:8700"), 8700));
        assert!(!host_ok(Some("evil.test:8700"), 8700));
        assert!(!host_ok(None, 8700));
    }

    async fn start() -> (u16, AppState, CancellationToken) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (status, _rx) = watch::channel(FeedStatus::On);
        let fetch: crate::bars::Fetcher = Arc::new(|_s, _t| Box::pin(async { None }));
        let bars = Arc::new(Bars { warehouse: PathBuf::from("no/such.duckdb"), intraday: crate::bars::Intraday::new(fetch), adjusted: crate::bars::AdjustedDaily::new(Arc::new(|_s| Box::pin(async { None }))) });
        let layouts = Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-layouts-{port}"))));
        let verdicts = VerdictJobs::new(vec!["no-such-program-xyz".into()]);
        let analyse = Arc::new(crate::analyse_worker::AnalyseWorker::new(vec!["no-such-program-xyz".into()]));
        let fundamentals = Arc::new(FundamentalsJobs::new(vec!["no-such-program-xyz".into()], vec!["no-such-program-xyz".into()], PathBuf::from("no/such.duckdb"), PathBuf::from("no/such-f.duckdb")));
        let state = AppState { hub: Hub::new(), status, port, web_dir: std::env::temp_dir(), bars, layouts, verdicts, analyse, fundamentals, indices: crate::indices::IndexStore::new(), search: Arc::new(crate::search::SearchCache::new(PathBuf::from("no/such.duckdb"))), fund_fetch: crate::fund_fetch::FundFetch::new(Arc::new(|_m, _s| tokio::process::Command::new("no-such-program-xyz"))), lists: Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-lists-{port}")))) };
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
