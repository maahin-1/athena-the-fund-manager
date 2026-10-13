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
use crate::bars::{daily_bars, yahoo_params, Bars, DEFAULT_LIMIT};
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
}

fn symbol_ok(symbol: &str) -> bool {
    !symbol.is_empty() && symbol.len() <= 24 && symbol.chars().all(|c| c.is_ascii_alphanumeric() || " &-^".contains(c))
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
        match tokio::task::spawn_blocking(move || daily_bars(&path, &sym, limit)).await {
            Ok(Ok(bars)) => (bars, "warehouse", ""),
            _ => (Vec::new(), "warehouse", "warehouse busy"),
        }
    } else if yahoo_params(&tf).is_some() {
        let (bars, loading) = state.bars.intraday.get(&symbol, &tf);
        (bars, "yahoo", if loading { "loading" } else { "" })
    } else {
        return StatusCode::BAD_REQUEST.into_response();
    };
    Json(serde_json::json!({"symbol": symbol, "tf": tf, "bars": bars, "source": source, "note": note})).into_response()
}

fn layout_status(e: LayoutError) -> StatusCode {
    match e {
        LayoutError::BadName | LayoutError::NotAnObject => StatusCode::BAD_REQUEST,
        LayoutError::TooBig => StatusCode::PAYLOAD_TOO_LARGE,
        LayoutError::Missing => StatusCode::NOT_FOUND,
        LayoutError::Io => StatusCode::INTERNAL_SERVER_ERROR,
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
        let bars = Arc::new(Bars { warehouse: PathBuf::from("no/such.duckdb"), intraday: crate::bars::Intraday::new(fetch) });
        let layouts = Arc::new(LayoutStore::new(std::env::temp_dir().join(format!("athena-srv-layouts-{port}"))));
        let state = AppState { hub: Hub::new(), status, port, web_dir: std::env::temp_dir(), bars, layouts };
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
