//! The Angel One market-data stream: connect, subscribe the symbols pages want (snap-quote mode), keep the heartbeat, turn each packet into
//! a quote for the hub, and reconnect with backoff. Read-only: only subscribe, unsubscribe and ping messages are ever sent.
use std::collections::HashSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use tokio::sync::watch;
use tokio::time::{interval, sleep};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;

use crate::angel::decode::decode;
use crate::angel::login::Session;
use crate::angel::master::Master;
use crate::hub::Hub;
use crate::quote::Quote;

pub const STREAM_URL: &str = "wss://smartapisocket.angelone.in/smart-stream";
const SNAP_QUOTE: u8 = 3;
const NSE_CASH: u8 = 1;
const HEARTBEAT: Duration = Duration::from_secs(10);
const SYNC_EVERY: Duration = Duration::from_millis(500);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FeedStatus {
    Connecting,
    On,
    /// A short fixed phrase, never a header value or a token.
    Off(&'static str),
}

fn message(action: u8, tokens: &[String]) -> Message {
    let body = serde_json::json!({
        "correlationID": "athena",
        "action": action,
        "params": {"mode": SNAP_QUOTE, "tokenList": [{"exchangeType": NSE_CASH, "tokens": tokens}]}
    });
    Message::Text(body.to_string().into())
}

/// One connection's life: returns when the socket closes or fails, or when asked to stop.
async fn session_once(
    url: &str, session: &Session, master: &Master, hub: &Hub, status: &watch::Sender<FeedStatus>, shutdown: &CancellationToken,
) -> Result<(), &'static str> {
    let mut request = url.into_client_request().map_err(|_| "bad stream address")?;
    let headers = request.headers_mut();
    for (name, value) in [
        ("Authorization", &session.jwt),
        ("x-api-key", &session.api_key),
        ("x-client-code", &session.client_code),
        ("x-feed-token", &session.feed_token),
    ] {
        headers.insert(name, HeaderValue::from_str(value).map_err(|_| "unusable credentials")?);
    }
    let (socket, _) = tokio_tungstenite::connect_async(request).await.map_err(|_| "could not connect")?;
    let (mut tx, mut rx) = socket.split();
    let _ = status.send(FeedStatus::On);
    let mut subscribed: HashSet<String> = HashSet::new();
    let mut beat = interval(HEARTBEAT);
    let mut sync = interval(SYNC_EVERY);
    loop {
        tokio::select! {
            _ = shutdown.cancelled() => return Ok(()),
            _ = beat.tick() => { tx.send(Message::Text("ping".into())).await.map_err(|_| "send failed")?; }
            _ = sync.tick() => {
                let wanted: HashSet<String> = hub.wanted_symbols().into_iter().filter(|s| master.token(s).is_some()).collect();
                let add: Vec<String> = wanted.difference(&subscribed).filter_map(|s| master.token(s).map(String::from)).collect();
                let remove: Vec<String> = subscribed.difference(&wanted).filter_map(|s| master.token(s).map(String::from)).collect();
                if !add.is_empty() { tx.send(message(1, &add)).await.map_err(|_| "send failed")?; }
                if !remove.is_empty() { tx.send(message(0, &remove)).await.map_err(|_| "send failed")?; }
                subscribed = wanted;
            }
            incoming = rx.next() => match incoming {
                Some(Ok(Message::Binary(bytes))) => {
                    if let Ok(tick) = decode(&bytes) {
                        if let Some(symbol) = master.symbol(&tick.token) {
                            if let Some(quote) = Quote::from_tick(symbol, &tick) {
                                hub.update(quote, Instant::now());
                            }
                        }
                    }
                }
                Some(Ok(Message::Close(_))) | None => return Err("the feed closed"),
                Some(Ok(_)) => {}
                Some(Err(_)) => return Err("the feed failed"),
            }
        }
    }
}

/// Keep a connection to `url` until asked to stop, reconnecting after a drop with 1, 2, 4 … 30 seconds of waiting.
pub async fn run_stream_at(
    url: String, session: Session, master: Arc<Master>, hub: Hub, status: watch::Sender<FeedStatus>, shutdown: CancellationToken,
) {
    let mut wait = Duration::from_secs(1);
    loop {
        let _ = status.send(FeedStatus::Connecting);
        let started = Instant::now();
        let outcome = session_once(&url, &session, &master, &hub, &status, &shutdown).await;
        if shutdown.is_cancelled() {
            return;
        }
        let _ = status.send(FeedStatus::Off(outcome.err().unwrap_or("the feed stopped")));
        if started.elapsed() > Duration::from_secs(60) {
            wait = Duration::from_secs(1);
        }
        tokio::select! {
            _ = shutdown.cancelled() => return,
            _ = sleep(wait) => {}
        }
        wait = (wait * 2).min(Duration::from_secs(30));
    }
}

pub async fn run_stream(session: Session, master: Arc<Master>, hub: Hub, status: watch::Sender<FeedStatus>, shutdown: CancellationToken) {
    run_stream_at(STREAM_URL.to_string(), session, master, hub, status, shutdown).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::angel::decode::test_packet;
    use crate::quote::Source;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_hdr_async;
    use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};

    fn session() -> Session {
        Session { api_key: "K".into(), client_code: "C1".into(), jwt: "J".into(), feed_token: "F".into() }
    }

    fn master() -> Arc<Master> {
        Arc::new(Master::new(vec![("SBIN-EQ".into(), "3045".into())]))
    }

    /// A stand-in for Angel One: accepts connections, records header names and subscribe messages, answers with one packet.
    async fn fake_server(connections: usize) -> (String, tokio::task::JoinHandle<Vec<(Vec<String>, Vec<String>)>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("ws://{}", listener.local_addr().unwrap());
        let handle = tokio::spawn(async move {
            let mut seen = Vec::new();
            for _ in 0..connections {
                let (stream, _) = listener.accept().await.unwrap();
                let mut header_names: Vec<String> = Vec::new();
                let names = &mut header_names;
                let mut ws = accept_hdr_async(stream, |req: &Request, resp: Response| {
                    *names = req.headers().keys().map(|k| k.as_str().to_string()).collect();
                    Ok(resp)
                })
                .await
                .unwrap();
                let mut subscribes: Vec<String> = Vec::new();
                while let Some(Ok(msg)) = ws.next().await {
                    if let Message::Text(t) = msg {
                        if t.as_str() == "ping" {
                            continue;
                        }
                        subscribes.push(t.to_string());
                        let packet = test_packet("3045", 1_760_000_000_000, 81240, 80270, &[(1, 1200, 81245), (0, 900, 81250)]);
                        let _ = ws.send(Message::Binary(packet.into())).await;
                        break; // drop the socket after the first subscribe so a test can see the reconnect
                    }
                }
                seen.push((header_names, subscribes));
            }
            seen
        });
        (url, handle)
    }

    #[tokio::test]
    async fn subscribes_with_the_four_headers_and_puts_a_stream_quote_in_the_hub() {
        let (url, server) = fake_server(1).await;
        let hub = Hub::new();
        let client = hub.register_client();
        hub.watch(client, &["SBIN".into()]);
        let (status, _rx) = watch::channel(FeedStatus::Connecting);
        let stop = CancellationToken::new();
        let task = tokio::spawn(run_stream_at(url, session(), master(), hub.clone(), status, stop.clone()));
        let frame = tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if let Some(f) = hub.frame(client) {
                    return f;
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("a quote reaches the hub");
        assert_eq!(frame.0[0].symbol, "SBIN");
        assert_eq!(frame.0[0].last, 812.4);
        assert_eq!(frame.0[0].source, Source::Stream);
        stop.cancel();
        let _ = task.await;
        let seen = server.await.unwrap();
        let (names, subs) = &seen[0];
        for h in ["authorization", "x-api-key", "x-client-code", "x-feed-token"] {
            assert!(names.iter().any(|n| n == h), "header {h} missing");
        }
        assert!(subs[0].contains("\"3045\"") && subs[0].contains("\"mode\":3") && subs[0].contains("\"action\":1"));
    }

    #[tokio::test]
    async fn reconnects_and_subscribes_again_after_a_drop() {
        let (url, server) = fake_server(2).await;
        let hub = Hub::new();
        let client = hub.register_client();
        hub.watch(client, &["SBIN".into()]);
        let (status, _rx) = watch::channel(FeedStatus::Connecting);
        let stop = CancellationToken::new();
        let task = tokio::spawn(run_stream_at(url, session(), master(), hub.clone(), status, stop.clone()));
        let seen = tokio::time::timeout(Duration::from_secs(10), server).await.expect("two connections").unwrap();
        stop.cancel();
        let _ = task.await;
        assert_eq!(seen.len(), 2);
        assert!(seen[1].1[0].contains("\"3045\""));
    }

    #[tokio::test]
    async fn status_never_carries_a_secret() {
        let (status, rx) = watch::channel(FeedStatus::Connecting);
        let stop = CancellationToken::new();
        // nothing listens on this port, so the connection fails and the status becomes Off with a fixed phrase
        let task = tokio::spawn(run_stream_at("ws://127.0.0.1:9".into(), session(), master(), Hub::new(), status, stop.clone()));
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if matches!(*rx.borrow(), FeedStatus::Off(_)) {
                    break;
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("goes off");
        let shown = format!("{:?}", *rx.borrow());
        assert_eq!(shown, "Off(\"could not connect\")");
        stop.cancel();
        let _ = task.await;
    }
}
