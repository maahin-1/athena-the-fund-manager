//! Live quotes of futures and options: what the stream last said about each derivative token a page asked about. A page names the contracts on its socket (the
//! server then pushes each change as it comes) or asks the quotes route; either marks them as wanted. The stream subscribes what is wanted and lets go of what
//! nobody has asked for lately. Kept apart from the cash hub because a token is only unique within its exchange segment and a chain asks for a hundred at a time.
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::sync::Notify;

use crate::angel::decode::Tick;

/// A contract nobody has asked about for this long is unsubscribed.
pub const WANTED_FOR: Duration = Duration::from_secs(30);
/// Most contracts one request may ask for (a chain of 40 strikes each side is 81).
pub const MAX_ASKED: usize = 100;
/// Most contracts streamed at once across all pages; Angel allows 1000 token subscriptions a session and the cash symbols use up to 600 (`hub::MAX_STREAMED`).
pub const MAX_WANTED: usize = 300;

/// A contract on the stream: the exchange type the stream wants and the token.
pub type Key = (u8, String);

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DerivQuote {
    #[serde(rename = "seg")]
    pub segment: String,
    pub token: String,
    pub last: Option<f64>,
    pub bid: Option<f64>,
    pub ask: Option<f64>,
    #[serde(rename = "pc")]
    pub prev_close: Option<f64>,
    pub volume: i64,
    /// Open interest in contracts, and its change since the previous close in percent as the stream gives it.
    pub oi: i64,
    #[serde(rename = "oip")]
    pub oi_change_pct: f64,
    /// The exchange's own time of the last trade, milliseconds since the epoch; null when the packet had none.
    #[serde(rename = "t")]
    pub exchange_ms: Option<i64>,
}

/// Prices come in paise; the currency segment's come in 1/10,000,000 of a rupee (the stream's documentation).
fn divisor(exchange_type: u8) -> f64 {
    if exchange_type == 13 {
        10_000_000.0
    } else {
        100.0
    }
}

impl DerivQuote {
    /// From a decoded tick on `segment` (the list's name for it) whose exchange type was `exchange_type`.
    pub fn from_tick(segment: &str, exchange_type: u8, tick: &Tick) -> DerivQuote {
        let d = divisor(exchange_type);
        let price = |p: i64| (p > 0).then(|| p as f64 / d);
        DerivQuote {
            segment: segment.to_string(),
            token: tick.token.clone(),
            last: price(tick.last),
            bid: tick.bids.first().and_then(|(p, _)| price(*p)),
            ask: tick.asks.first().and_then(|(p, _)| price(*p)),
            prev_close: price(tick.prev_close),
            volume: tick.volume.max(0),
            oi: tick.open_interest.max(0),
            oi_change_pct: if tick.oi_change_pct.is_finite() { tick.oi_change_pct } else { 0.0 },
            exchange_ms: (tick.exchange_ms > 100_000_000_000).then_some(tick.exchange_ms),
        }
    }
}

#[derive(Default)]
struct Inner {
    rev: u64,
    quotes: HashMap<Key, (DerivQuote, u64)>,
    wanted: HashMap<Key, Instant>,
}

/// The quotes and the wants, and a signal raised when a contract is newly wanted so the stream subscribes it at once.
#[derive(Clone, Default)]
pub struct DerivQuotes(Arc<Mutex<Inner>>, Arc<Notify>);

impl DerivQuotes {
    pub fn new() -> Self {
        DerivQuotes::default()
    }

    /// Mark these contracts as wanted from `now` (a repeat extends it).
    pub fn want(&self, keys: &[Key], now: Instant) {
        let mut g = self.0.lock().unwrap();
        let mut new = false;
        for k in keys {
            let before = g.wanted.insert(k.clone(), now);
            new |= before.is_none_or(|at| now.saturating_duration_since(at) > WANTED_FOR);
        }
        if new {
            self.1.notify_one();
        }
    }

    /// Resolves when a contract was newly wanted since the last time it resolved.
    pub fn wants_changed(&self) -> tokio::sync::futures::Notified<'_> {
        self.1.notified()
    }

    /// The quotes of these contracts that changed since `sent` last recorded them, and `sent` brought up to date: what one page's socket is sent next.
    pub fn changed(&self, keys: &[Key], sent: &mut HashMap<Key, u64>) -> Vec<DerivQuote> {
        let g = self.0.lock().unwrap();
        let mut out = Vec::new();
        for k in keys {
            if let Some((q, rev)) = g.quotes.get(k) {
                if *rev > sent.get(k).copied().unwrap_or(0) {
                    out.push(q.clone());
                    sent.insert(k.clone(), *rev);
                }
            }
        }
        out
    }

    /// The contracts to be on the stream at `now`: those asked for within `WANTED_FOR`, the most recently asked first when there are more than `MAX_WANTED`.
    /// Contracts nobody wants any more are forgotten, quotes and all.
    pub fn wanted(&self, now: Instant) -> HashSet<Key> {
        let mut g = self.0.lock().unwrap();
        g.wanted.retain(|_, at| now.saturating_duration_since(*at) <= WANTED_FOR);
        let live: HashSet<Key> = g.wanted.keys().cloned().collect();
        g.quotes.retain(|(e, t), _| live.contains(&(*e, t.clone())));
        let mut by_time: Vec<(&Key, &Instant)> = g.wanted.iter().collect();
        by_time.sort_by(|a, b| b.1.cmp(a.1));
        by_time.into_iter().take(MAX_WANTED).map(|(k, _)| k.clone()).collect()
    }

    pub fn update(&self, exchange_type: u8, quote: DerivQuote) {
        let mut g = self.0.lock().unwrap();
        let key = (exchange_type, quote.token.clone());
        if g.wanted.contains_key(&key) {
            if g.quotes.get(&key).is_some_and(|(held, _)| *held == quote) {
                return; // an identical quote is no change to send
            }
            g.rev += 1;
            let rev = g.rev;
            g.quotes.insert(key, (quote, rev));
        }
    }

    /// The quotes known for these contracts, in the order asked; one the stream has not yet spoken about is left out.
    pub fn get(&self, keys: &[Key]) -> Vec<DerivQuote> {
        let g = self.0.lock().unwrap();
        keys.iter().filter_map(|k| g.quotes.get(k).map(|(q, _)| q.clone())).collect()
    }
}

/// The contracts a page asks about: `NFO:12345,MCX:678`, at most `MAX_ASKED`, each a derivatives segment and a numeric token. The reason it is refused, in words
/// the page can show, otherwise.
pub fn parse_keys(text: &str) -> Result<Vec<Key>, &'static str> {
    parse_keys_up_to(text, MAX_ASKED, "at most 100 contracts at a time")
}

/// As `parse_keys`, for the list a page sends on its socket: up to `MAX_WANTED`, the most the stream carries.
pub fn parse_socket_keys(text: &str) -> Result<Vec<Key>, &'static str> {
    parse_keys_up_to(text, MAX_WANTED, "at most 300 contracts at a time")
}

fn parse_keys_up_to(text: &str, max: usize, too_many: &'static str) -> Result<Vec<Key>, &'static str> {
    let mut keys: Vec<Key> = Vec::new();
    for part in text.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        let (segment, token) = part.split_once(':').ok_or("each contract is SEGMENT:TOKEN")?;
        if !crate::derivs::SEGMENTS.contains(&segment) {
            return Err("the segment must be NFO, BFO or MCX");
        }
        if token.is_empty() || token.len() > 12 || !token.bytes().all(|b| b.is_ascii_digit()) {
            return Err("a token is digits only");
        }
        let exchange = crate::angel::stream::exchange_type(segment).ok_or("the segment must be NFO, BFO or MCX")?;
        let key = (exchange, token.to_string());
        if !keys.contains(&key) {
            keys.push(key);
        }
        if keys.len() > max {
            return Err(too_many);
        }
    }
    Ok(keys)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::angel::decode::{decode, test_packet_on};

    fn tick(exchange: u8, token: &str, last: i64, oi: i64, pct: f64) -> Tick {
        decode(&test_packet_on(exchange, token, 1_760_000_000_000, last, 9000, &[(1, 650, last - 5), (0, 130, last + 5)], oi, pct)).unwrap()
    }

    #[test]
    fn a_tick_becomes_a_quote_in_rupees_with_the_best_bid_and_ask_and_open_interest() {
        let q = DerivQuote::from_tick("NFO", 2, &tick(2, "45678", 12_345, 1_250_000, -3.5));
        assert_eq!((q.segment.as_str(), q.token.as_str()), ("NFO", "45678"));
        assert_eq!(q.last, Some(123.45));
        assert_eq!(q.bid, Some(123.4));
        assert_eq!(q.ask, Some(123.5));
        assert_eq!(q.prev_close, Some(90.0));
        assert_eq!((q.oi, q.oi_change_pct), (1_250_000, -3.5));
        assert_eq!(q.exchange_ms, Some(1_760_000_000_000));
    }

    #[test]
    fn a_missing_price_is_null_not_zero_and_the_currency_segment_has_its_own_divisor() {
        let q = DerivQuote::from_tick("NFO", 2, &tick(2, "1", 0, 0, 0.0));
        assert_eq!((q.last, q.bid, q.oi), (None, None, 0));
        let c = DerivQuote::from_tick("CDS", 13, &tick(13, "1", 835_000_000, 0, 0.0));
        assert_eq!(c.last, Some(83.5));
    }

    #[test]
    fn contracts_are_parsed_by_segment_and_token_and_bad_ones_refused_with_a_reason() {
        assert_eq!(parse_keys("NFO:123, MCX:45 ,NFO:123").unwrap(), vec![(2, "123".to_string()), (5, "45".to_string())]);
        assert_eq!(parse_keys("").unwrap(), vec![]);
        assert!(parse_keys("NSE:3045").is_err()); // cash is not a derivative
        assert!(parse_keys("NFO").is_err());
        assert!(parse_keys("NFO:12a").is_err());
        assert!(parse_keys("NFO:1234567890123").is_err());
        let many = (0..=MAX_ASKED).map(|i| format!("NFO:{i}")).collect::<Vec<_>>().join(",");
        assert!(parse_keys(&many).is_err());
        let enough = (0..MAX_ASKED).map(|i| format!("NFO:{i}")).collect::<Vec<_>>().join(",");
        assert_eq!(parse_keys(&enough).unwrap().len(), MAX_ASKED);
    }

    #[test]
    fn a_non_finite_change_is_zero() {
        let q = DerivQuote::from_tick("NFO", 2, &tick(2, "1", 100, 5, f64::NAN));
        assert_eq!(q.oi_change_pct, 0.0);
    }

    #[test]
    fn wanted_contracts_are_kept_for_thirty_seconds_after_the_last_ask_and_then_forgotten_with_their_quotes() {
        let store = DerivQuotes::new();
        let t0 = Instant::now();
        let a: Key = (2, "1".into());
        let b: Key = (5, "1".into()); // the same token on another segment is another contract
        store.want(&[a.clone(), b.clone()], t0);
        store.update(2, DerivQuote::from_tick("NFO", 2, &tick(2, "1", 100, 5, 1.0)));
        assert_eq!(store.wanted(t0 + Duration::from_secs(10)).len(), 2);
        assert_eq!(store.get(&[a.clone(), b.clone()]).len(), 1); // only the NFO one has spoken
        store.want(&[a.clone()], t0 + Duration::from_secs(25)); // asked again
        let later = store.wanted(t0 + Duration::from_secs(40));
        assert_eq!(later, HashSet::from([a.clone()]));
        assert_eq!(store.get(&[a.clone()]).len(), 1);
        let gone = store.wanted(t0 + Duration::from_secs(100));
        assert!(gone.is_empty());
        assert!(store.get(&[a]).is_empty());
    }

    #[test]
    fn a_page_is_sent_each_change_once_and_an_identical_tick_is_no_change() {
        let store = DerivQuotes::new();
        let t0 = Instant::now();
        let a = (2u8, "1".to_string());
        store.want(&[a.clone()], t0);
        let mut sent = HashMap::new();
        assert!(store.changed(&[a.clone()], &mut sent).is_empty());
        store.update(2, DerivQuote::from_tick("NFO", 2, &tick(2, "1", 100, 5, 1.0)));
        assert_eq!(store.changed(&[a.clone()], &mut sent).len(), 1);
        assert!(store.changed(&[a.clone()], &mut sent).is_empty());
        store.update(2, DerivQuote::from_tick("NFO", 2, &tick(2, "1", 100, 5, 1.0)));
        assert!(store.changed(&[a.clone()], &mut sent).is_empty(), "the same quote again");
        store.update(2, DerivQuote::from_tick("NFO", 2, &tick(2, "1", 101, 5, 1.0)));
        assert_eq!(store.changed(&[a], &mut sent)[0].last, Some(1.01));
    }

    #[test]
    fn a_newly_wanted_contract_is_signalled_and_a_repeat_is_not() {
        let store = DerivQuotes::new();
        let t0 = Instant::now();
        let rt = tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap();
        rt.block_on(async {
            store.want(&[(2, "7".into())], t0);
            tokio::time::timeout(Duration::from_millis(50), store.wants_changed()).await.expect("signalled");
            store.want(&[(2, "7".into())], t0 + Duration::from_secs(1));
            assert!(tokio::time::timeout(Duration::from_millis(50), store.wants_changed()).await.is_err());
        });
    }

    #[test]
    fn the_socket_list_may_hold_up_to_the_stream_cap() {
        let many = (0..MAX_WANTED).map(|i| format!("NFO:{i}")).collect::<Vec<_>>().join(",");
        assert_eq!(parse_socket_keys(&many).unwrap().len(), MAX_WANTED);
        assert!(parse_socket_keys(&format!("{many},NFO:99999")).is_err());
    }

    #[test]
    fn a_tick_for_a_contract_nobody_asked_for_is_dropped() {
        let store = DerivQuotes::new();
        store.update(2, DerivQuote::from_tick("NFO", 2, &tick(2, "9", 100, 5, 1.0)));
        assert!(store.get(&[(2, "9".into())]).is_empty());
    }

    #[test]
    fn no_more_than_the_limit_are_streamed_and_the_latest_asked_win() {
        let store = DerivQuotes::new();
        let t0 = Instant::now();
        for i in 0..(MAX_WANTED + 20) {
            store.want(&[(2, i.to_string())], t0 + Duration::from_millis(i as u64));
        }
        let wanted = store.wanted(t0 + Duration::from_secs(1));
        assert_eq!(wanted.len(), MAX_WANTED);
        assert!(wanted.contains(&(2, (MAX_WANTED + 19).to_string())));
        assert!(!wanted.contains(&(2, "0".to_string())));
    }
}
