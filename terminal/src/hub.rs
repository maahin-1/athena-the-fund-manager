//! The hub: the latest quote per symbol, which symbols each page watches, and the frames sent to pages.
//! A frame holds only the quotes that changed since that page last received them, merged per symbol.
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::quote::{Quote, Source};

/// A streamed quote is used while its last message is at most this old; after that the polled quote may take over.
pub const FRESH: Duration = Duration::from_secs(15);
/// Most symbols streamed at once. Angel One's forum and WebSocket 2.0 announcement give 1000 token subscriptions per session (counted per token and
/// mode) and 3 connections per client code, so 50 in one connection and one mode is far inside the limit (found 2026-10-14; recheck the current docs).
pub const MAX_STREAMED: usize = 50;

pub type ClientId = u64;

struct Entry {
    rev: u64,
    quote: Quote,
    stream_at: Option<Instant>,
}

#[derive(Default)]
struct Client {
    set: HashSet<String>,
    sent: HashMap<String, u64>,
}

#[derive(Default)]
struct Inner {
    rev: u64,
    next_client: ClientId,
    quotes: HashMap<String, Entry>,
    clients: HashMap<ClientId, Client>,
}

#[derive(Clone, Default)]
pub struct Hub(Arc<Mutex<Inner>>);

pub struct Frame(pub Vec<Quote>);

impl Frame {
    /// `sm` is the server's clock when the frame was built, so the page can show how long a frame took to arrive.
    pub fn json(&self) -> String {
        let sm = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0);
        serde_json::json!({"type": "q", "sm": sm, "q": self.0}).to_string()
    }
}

impl Hub {
    pub fn new() -> Self {
        Hub::default()
    }

    pub fn register_client(&self) -> ClientId {
        let mut g = self.0.lock().unwrap();
        g.next_client += 1;
        let id = g.next_client;
        g.clients.insert(id, Client::default());
        id
    }

    pub fn drop_client(&self, client: ClientId) {
        self.0.lock().unwrap().clients.remove(&client);
    }

    pub fn client_count(&self) -> usize {
        self.0.lock().unwrap().clients.len()
    }

    /// Replace the set of symbols this client watches. Symbols new to the client are re-sent on the next frame.
    pub fn watch(&self, client: ClientId, symbols: &[String]) {
        let mut g = self.0.lock().unwrap();
        if let Some(c) = g.clients.get_mut(&client) {
            let set: HashSet<String> = symbols.iter().map(|s| s.trim().to_ascii_uppercase()).filter(|s| !s.is_empty()).collect();
            c.sent.retain(|s, _| set.contains(s));
            c.set = set;
        }
    }

    pub fn update(&self, quote: Quote, now: Instant) {
        let mut g = self.0.lock().unwrap();
        let previous_stream_at = g.quotes.get(&quote.symbol).and_then(|e| e.stream_at);
        let stream_at = match quote.source {
            Source::Stream => Some(now),
            Source::Poll => {
                if previous_stream_at.is_some_and(|t| now.saturating_duration_since(t) <= FRESH) {
                    return; // a polled quote never replaces a fresh streamed one
                }
                previous_stream_at
            }
        };
        g.rev += 1;
        let rev = g.rev;
        let symbol = quote.symbol.clone();
        g.quotes.insert(symbol, Entry { rev, quote, stream_at });
    }

    /// Sorted union of every client's symbols, cut at the streaming cap.
    pub fn wanted_symbols(&self) -> Vec<String> {
        let g = self.0.lock().unwrap();
        let mut all: Vec<String> = g.clients.values().flat_map(|c| c.set.iter().cloned()).collect::<HashSet<_>>().into_iter().collect();
        all.sort();
        all.truncate(MAX_STREAMED);
        all
    }

    /// Wanted symbols with no streamed quote in the last `FRESH` seconds.
    pub fn stale_symbols(&self, now: Instant) -> Vec<String> {
        let wanted = self.wanted_symbols();
        let g = self.0.lock().unwrap();
        wanted
            .into_iter()
            .filter(|s| match g.quotes.get(s).and_then(|e| e.stream_at) {
                Some(t) => now.saturating_duration_since(t) > FRESH,
                None => true,
            })
            .collect()
    }

    pub fn frame(&self, client: ClientId) -> Option<Frame> {
        let mut g = self.0.lock().unwrap();
        let Inner { quotes, clients, .. } = &mut *g;
        let c = clients.get_mut(&client)?;
        let mut out: Vec<Quote> = Vec::new();
        for symbol in &c.set {
            if let Some(e) = quotes.get(symbol) {
                if e.rev > c.sent.get(symbol).copied().unwrap_or(0) {
                    out.push(e.quote.clone());
                    c.sent.insert(symbol.clone(), e.rev);
                }
            }
        }
        if out.is_empty() {
            return None;
        }
        out.sort_by(|a, b| a.symbol.cmp(&b.symbol));
        Some(Frame(out))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::quote::sample;

    fn names(f: Frame) -> Vec<String> {
        f.0.into_iter().map(|q| q.symbol).collect()
    }

    #[test]
    fn two_updates_before_a_frame_give_one_entry_with_the_latest_values() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        let now = Instant::now();
        hub.update(sample("SBIN", 810.0, Source::Stream), now);
        hub.update(sample("SBIN", 811.0, Source::Stream), now);
        let f = hub.frame(c).unwrap();
        assert_eq!(f.0.len(), 1);
        assert_eq!(f.0[0].last, 811.0);
    }

    #[test]
    fn a_second_frame_with_no_new_update_is_none() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        hub.update(sample("SBIN", 810.0, Source::Stream), Instant::now());
        assert!(hub.frame(c).is_some());
        assert!(hub.frame(c).is_none());
    }

    #[test]
    fn a_client_receives_only_what_it_watches() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        let now = Instant::now();
        hub.update(sample("SBIN", 810.0, Source::Stream), now);
        hub.update(sample("TCS", 3900.0, Source::Stream), now);
        assert_eq!(names(hub.frame(c).unwrap()), vec!["SBIN"]);
    }

    #[test]
    fn each_client_gets_an_update_once() {
        let hub = Hub::new();
        let (a, b) = (hub.register_client(), hub.register_client());
        hub.watch(a, &["SBIN".into()]);
        hub.watch(b, &["SBIN".into()]);
        hub.update(sample("SBIN", 810.0, Source::Stream), Instant::now());
        assert!(hub.frame(a).is_some());
        assert!(hub.frame(b).is_some());
        assert!(hub.frame(a).is_none());
    }

    #[test]
    fn a_symbol_added_later_is_sent_at_once() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        let now = Instant::now();
        hub.update(sample("SBIN", 810.0, Source::Stream), now);
        hub.update(sample("TCS", 3900.0, Source::Stream), now);
        assert_eq!(names(hub.frame(c).unwrap()), vec!["SBIN"]);
        hub.watch(c, &["SBIN".into(), "TCS".into()]);
        assert_eq!(names(hub.frame(c).unwrap()), vec!["TCS"]);
    }

    #[test]
    fn wanted_symbols_are_a_sorted_union_cut_at_the_cap() {
        let hub = Hub::new();
        let (a, b) = (hub.register_client(), hub.register_client());
        hub.watch(a, &["TCS".into(), "sbin".into()]);
        hub.watch(b, &["SBIN".into(), "INFY".into()]);
        assert_eq!(hub.wanted_symbols(), vec!["INFY", "SBIN", "TCS"]);
        let many: Vec<String> = (0..80).map(|i| format!("S{i:03}")).collect();
        hub.watch(a, &many);
        assert_eq!(hub.wanted_symbols().len(), MAX_STREAMED);
    }

    #[test]
    fn a_silent_stream_goes_stale_and_a_poll_may_then_take_over() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        let t0 = Instant::now();
        assert_eq!(hub.stale_symbols(t0), vec!["SBIN"]); // nothing yet
        hub.update(sample("SBIN", 810.0, Source::Stream), t0);
        assert!(hub.stale_symbols(t0 + Duration::from_secs(5)).is_empty());
        let late = t0 + Duration::from_secs(20);
        assert_eq!(hub.stale_symbols(late), vec!["SBIN"]);
        hub.update(sample("SBIN", 811.0, Source::Poll), late);
        let f = hub.frame(c).unwrap();
        assert_eq!((f.0[0].last, f.0[0].source), (811.0, Source::Poll));
        assert_eq!(hub.stale_symbols(late), vec!["SBIN"]); // still stale: keep polling until the stream is back
    }

    #[test]
    fn a_poll_never_replaces_a_fresh_stream_quote() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        let t0 = Instant::now();
        hub.update(sample("SBIN", 810.0, Source::Stream), t0);
        hub.update(sample("SBIN", 790.0, Source::Poll), t0 + Duration::from_secs(5));
        assert_eq!(hub.frame(c).unwrap().0[0].last, 810.0);
    }

    #[test]
    fn dropping_a_client_forgets_it() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        hub.drop_client(c);
        assert_eq!(hub.client_count(), 0);
        assert!(hub.wanted_symbols().is_empty());
        assert!(hub.frame(c).is_none());
    }

    #[test]
    fn frame_json_has_the_type_and_short_names() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        hub.update(sample("SBIN", 810.0, Source::Stream), Instant::now());
        let v: serde_json::Value = serde_json::from_str(&hub.frame(c).unwrap().json()).unwrap();
        assert_eq!(v["type"], "q");
        assert!(v["sm"].as_u64().unwrap() > 1_700_000_000_000);
        assert_eq!(v["q"][0]["s"], "SBIN");
        assert_eq!(v["q"][0]["src"], "stream");
    }
}
