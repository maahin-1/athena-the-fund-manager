//! The Angel One market-data stream: connect, subscribe the symbols pages want (snap-quote mode), keep the heartbeat, turn each packet into
//! a quote for the hub, and reconnect with backoff. Read-only: only subscribe, unsubscribe and ping messages are ever sent.
use std::collections::{BTreeMap, HashSet};
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
use crate::deriv_quotes::{DerivQuote, DerivQuotes, Key};
use crate::hub::Hub;
use crate::quote::Quote;

pub const STREAM_URL: &str = "wss://smartapisocket.angelone.in/smart-stream";
const SNAP_QUOTE: u8 = 3;
const NSE_CASH: u8 = 1;

/// The stream's exchange type for a segment of the instrument list: NSE cash 1, NFO 2, BSE cash 3, BFO 4, MCX 5, NCDEX 7, currency 13. None for a segment the
/// stream does not carry. Derivative tokens are subscribed under these.
pub fn exchange_type(segment: &str) -> Option<u8> {
    Some(match segment {
        "NSE" => NSE_CASH,
        "NFO" => 2,
        "BSE" => 3,
        "BFO" => 4,
        "MCX" => 5,
        "NCDEX" => 7,
        "CDS" => 13,
        _ => return None,
    })
}
/// The segment's name for a stream exchange type, the other way round.
pub fn segment_of(exchange_type: u8) -> Option<&'static str> {
    Some(match exchange_type {
        1 => "NSE",
        2 => "NFO",
        3 => "BSE",
        4 => "BFO",
        5 => "MCX",
        7 => "NCDEX",
        13 => "CDS",
        _ => return None,
    })
}

const HEARTBEAT: Duration = Duration::from_secs(10);
const SYNC_EVERY: Duration = Duration::from_millis(500);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FeedStatus {
    Connecting,
    On,
    /// A short fixed phrase, never a header value or a token.
    Off(&'static str),
}

/// A subscribe (action 1) or unsubscribe (0) message for tokens grouped by exchange type.
fn message(action: u8, groups: &[(u8, Vec<String>)]) -> Message {
    let list: Vec<serde_json::Value> = groups.iter().map(|(e, tokens)| serde_json::json!({"exchangeType": e, "tokens": tokens})).collect();
    let body = serde_json::json!({"correlationID": "athena", "action": action, "params": {"mode": SNAP_QUOTE, "tokenList": list}});
    Message::Text(body.to_string().into())
}

/// Contracts grouped by exchange type, in a fixed order.
fn by_exchange<'a>(keys: impl Iterator<Item = &'a Key>) -> Vec<(u8, Vec<String>)> {
    let mut groups: BTreeMap<u8, Vec<String>> = BTreeMap::new();
    for (e, t) in keys {
        groups.entry(*e).or_default().push(t.clone());
    }
    groups
        .into_iter()
        .map(|(e, mut t)| {
            t.sort();
            (e, t)
        })
        .collect()
}

/// One connection's life: returns when the socket closes or fails, or when asked to stop.
async fn session_once(
    url: &str, session: &Session, master: &Master, hub: &Hub, derivs: &DerivQuotes, status: &watch::Sender<FeedStatus>, shutdown: &CancellationToken,
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
    let mut subscribed_derivs: HashSet<Key> = HashSet::new();
    let mut beat = interval(HEARTBEAT);
    let mut sync = interval(SYNC_EVERY);
    loop {
        // a page's new symbol or contract is subscribed the moment it is asked for; the round every `SYNC_EVERY` lets go of what nobody wants any more
        let resync = tokio::select! {
            _ = shutdown.cancelled() => return Ok(()),
            _ = beat.tick() => { tx.send(Message::Text("ping".into())).await.map_err(|_| "send failed")?; false }
            _ = sync.tick() => true,
            _ = hub.wants_changed() => true,
            _ = derivs.wants_changed() => true,
            incoming = rx.next() => match incoming {
                Some(Ok(Message::Binary(bytes))) => {
                    if let Ok(tick) = decode(&bytes) {
                        if tick.exchange_ms > 100_000_000_000 {
                            hub.record_lag(chrono::Utc::now().timestamp_millis() - tick.exchange_ms);
                        }
                        if tick.exchange_type == NSE_CASH {
                            if let Some(symbol) = master.symbol(&tick.token) {
                                if let Some(quote) = Quote::from_tick(symbol, &tick) {
                                    hub.update(quote, Instant::now());
                                }
                            }
                        } else if let Some(segment) = segment_of(tick.exchange_type) {
                            derivs.update(tick.exchange_type, DerivQuote::from_tick(segment, tick.exchange_type, &tick));
                        }
                    }
                    false
                }
                Some(Ok(Message::Close(_))) | None => return Err("the feed closed"),
                Some(Ok(_)) => false,
                Some(Err(_)) => return Err("the feed failed"),
            },
        };
        if resync {
            {
                let wanted: HashSet<String> = hub.wanted_symbols().into_iter().filter(|s| master.token(s).is_some()).collect();
                let add: Vec<String> = wanted.difference(&subscribed).filter_map(|s| master.token(s).map(String::from)).collect();
                let remove: Vec<String> = subscribed.difference(&wanted).filter_map(|s| master.token(s).map(String::from)).collect();
                if !add.is_empty() { tx.send(message(1, &[(NSE_CASH, add)])).await.map_err(|_| "send failed")?; }
                if !remove.is_empty() { tx.send(message(0, &[(NSE_CASH, remove)])).await.map_err(|_| "send failed")?; }
                subscribed = wanted;
                let wanted_derivs = derivs.wanted(Instant::now());
                let add_derivs = by_exchange(wanted_derivs.difference(&subscribed_derivs));
                let remove_derivs = by_exchange(subscribed_derivs.difference(&wanted_derivs));
                if !add_derivs.is_empty() { tx.send(message(1, &add_derivs)).await.map_err(|_| "send failed")?; }
                if !remove_derivs.is_empty() { tx.send(message(0, &remove_derivs)).await.map_err(|_| "send failed")?; }
                subscribed_derivs = wanted_derivs;
            }
        }
    }
}

/// Keep a connection to `url` until asked to stop, reconnecting after a drop with 1, 2, 4 … 30 seconds of waiting.
pub async fn run_stream_at(
    url: String, session: Session, master: Arc<Master>, hub: Hub, derivs: DerivQuotes, status: watch::Sender<FeedStatus>, shutdown: CancellationToken,
) {
    let mut wait = Duration::from_secs(1);
    loop {
        let _ = status.send(FeedStatus::Connecting);
        let started = Instant::now();
        let outcome = session_once(&url, &session, &master, &hub, &derivs, &status, &shutdown).await;
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

pub async fn run_stream(session: Session, master: Arc<Master>, hub: Hub, derivs: DerivQuotes, status: watch::Sender<FeedStatus>, shutdown: CancellationToken) {
    run_stream_at(STREAM_URL.to_string(), session, master, hub, derivs, status, shutdown).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::angel::decode::{test_packet, test_packet_on};
    use crate::quote::Source;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_hdr_async;
    use tokio_tungstenite::tungstenite::handshake::server::{Request, Response};

    #[test]
    fn each_segment_of_the_instrument_list_has_its_stream_exchange_type() {
        let want = [("NSE", 1), ("NFO", 2), ("BSE", 3), ("BFO", 4), ("MCX", 5), ("NCDEX", 7), ("CDS", 13)];
        for (segment, kind) in want {
            assert_eq!(exchange_type(segment), Some(kind), "{segment}");
        }
        assert_eq!(exchange_type("NCO"), None);
        assert_eq!(exchange_type(""), None);
        assert_eq!(exchange_type("nfo"), None); // the list's own spelling only
    }

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
                        // let the packet be read before the socket goes: dropped at once, a ping the client sends next meets a reset, and Windows then throws away
                        // what the client had not yet read
                        tokio::time::sleep(Duration::from_millis(200)).await;
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
        let task = tokio::spawn(run_stream_at(url, session(), master(), hub.clone(), DerivQuotes::new(), status, stop.clone()));
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

    /// A stand-in that waits for a subscribe naming exchange type 2, then sends a derivative packet and one on the same token as a cash symbol.
    async fn derivative_server() -> (String, tokio::task::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("ws://{}", listener.local_addr().unwrap());
        let handle = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut ws = accept_hdr_async(stream, |_: &Request, resp: Response| Ok(resp)).await.unwrap();
            let mut subscribes = Vec::new();
            while let Some(Ok(msg)) = ws.next().await {
                if let Message::Text(t) = msg {
                    if t.as_str() == "ping" {
                        continue;
                    }
                    subscribes.push(t.to_string());
                    if t.contains("\"exchangeType\":2") {
                        let option = test_packet_on(2, "777", 1_760_000_000_000, 12_345, 9_000, &[(1, 650, 12_340), (0, 130, 12_350)], 1_250_000, -3.5);
                        let collision = test_packet_on(2, "3045", 1_760_000_000_000, 55_555, 50_000, &[], 10, 1.0); // SBIN's cash token on NFO is not SBIN
                        let _ = ws.send(Message::Binary(option.into())).await;
                        let _ = ws.send(Message::Binary(collision.into())).await;
                        tokio::time::sleep(Duration::from_millis(300)).await;
                        break;
                    }
                }
            }
            subscribes
        });
        (url, handle)
    }

    #[tokio::test]
    async fn wanted_derivative_tokens_are_subscribed_under_their_exchange_type_and_their_ticks_go_to_the_derivative_store_not_the_cash_hub() {
        let (url, server) = derivative_server().await;
        let hub = Hub::new();
        let derivs = DerivQuotes::new();
        derivs.want(&[(2, "777".into()), (2, "3045".into())], Instant::now());
        let (status, _rx) = watch::channel(FeedStatus::Connecting);
        let stop = CancellationToken::new();
        let task = tokio::spawn(run_stream_at(url, session(), master(), hub.clone(), derivs.clone(), status, stop.clone()));
        let got = tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                let q = derivs.get(&[(2, "777".into())]);
                if !q.is_empty() {
                    return q;
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("a derivative quote arrives");
        assert_eq!((got[0].segment.as_str(), got[0].last, got[0].oi), ("NFO", Some(123.45), 1_250_000));
        // the cash symbol that shares a token number with an NFO contract is not confused with it
        let client = hub.register_client();
        hub.watch(client, &["SBIN".into()]);
        assert!(hub.frame(client).is_none(), "an NFO tick on SBIN's token number must not become an SBIN quote");
        assert_eq!(derivs.get(&[(2, "3045".into())])[0].last, Some(555.55));
        stop.cancel();
        let _ = task.await;
        let subs = server.await.unwrap();
        let sub = subs.iter().find(|m| m.contains("\"exchangeType\":2")).expect("a derivative subscribe");
        assert!(sub.contains("\"action\":1") && sub.contains("\"mode\":3") && sub.contains("\"777\""));
    }

    #[tokio::test]
    async fn reconnects_and_subscribes_again_after_a_drop() {
        let (url, server) = fake_server(2).await;
        let hub = Hub::new();
        let client = hub.register_client();
        hub.watch(client, &["SBIN".into()]);
        let (status, _rx) = watch::channel(FeedStatus::Connecting);
        let stop = CancellationToken::new();
        let task = tokio::spawn(run_stream_at(url, session(), master(), hub.clone(), DerivQuotes::new(), status, stop.clone()));
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
        let task = tokio::spawn(run_stream_at("ws://127.0.0.1:9".into(), session(), master(), Hub::new(), DerivQuotes::new(), status, stop.clone()));
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
