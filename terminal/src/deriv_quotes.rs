//! Live quotes of futures and options: what the stream last said about each derivative token a page asked about. A page asks by polling the quotes route, which
//! both answers and marks those contracts as wanted; the stream subscribes what is wanted and lets go of what nobody has asked for lately. Kept apart from the
//! cash hub because a token is only unique within its exchange segment and a chain asks for a hundred at a time.
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::angel::decode::Tick;

/// A contract nobody has asked about for this long is unsubscribed.
pub const WANTED_FOR: Duration = Duration::from_secs(30);
/// Most contracts one request may ask for (a chain of 40 strikes each side is 81).
pub const MAX_ASKED: usize = 100;
/// Most contracts streamed at once across all pages; Angel allows 1000 token subscriptions a session and the cash symbols use up to 50.
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
    quotes: HashMap<Key, DerivQuote>,
    wanted: HashMap<Key, Instant>,
}

#[derive(Clone, Default)]
pub struct DerivQuotes(Arc<Mutex<Inner>>);

impl DerivQuotes {
    pub fn new() -> Self {
        DerivQuotes::default()
    }

    /// Mark these contracts as wanted from `now` (a repeat extends it).
    pub fn want(&self, keys: &[Key], now: Instant) {
        let mut g = self.0.lock().unwrap();
        for k in keys {
            g.wanted.insert(k.clone(), now);
        }
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
            g.quotes.insert(key, quote);
        }
    }

    /// The quotes known for these contracts, in the order asked; one the stream has not yet spoken about is left out.
    pub fn get(&self, keys: &[Key]) -> Vec<DerivQuote> {
        let g = self.0.lock().unwrap();
        keys.iter().filter_map(|k| g.quotes.get(k).cloned()).collect()
    }
}

/// The contracts a page asks about: `NFO:12345,MCX:678`, at most `MAX_ASKED`, each a derivatives segment and a numeric token. The reason it is refused, in words
/// the page can show, otherwise.
pub fn parse_keys(text: &str) -> Result<Vec<Key>, &'static str> {
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
        if keys.len() > MAX_ASKED {
            return Err("at most 100 contracts at a time");
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
