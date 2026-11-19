//! Live quotes of futures and options: what the stream last said about each derivative token a page asked about. A page names the contracts on its socket (the
//! server then pushes each change as it comes) or asks the quotes route; either marks them as wanted. The stream subscribes what is wanted and lets go of what
//! nobody has asked for lately. Kept apart from the cash hub because a token is only unique within its exchange segment and a chain asks for a hundred at a time.
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::sync::Notify;

use crate::angel::decode::{exchange_epoch_ms, Tick};

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
    /// The exchange's timestamp of the **packet** that gave this quote its content (milliseconds since the epoch): when the event the packet reports happened. It is not the time of the last trade
    /// (that is `last_trade_ms`): a new packet need not carry a new trade. Null when the packet had none or its time was refused (see `time_faults`).
    #[serde(rename = "t")]
    pub exchange_ms: Option<i64>,
    /// The exchange's time of the last trade (milliseconds since the epoch), from the packet's own last-traded field; null when the packet had none or it was refused.
    #[serde(rename = "lt")]
    pub last_trade_ms: Option<i64>,
    /// A time the packet carried that was not used, and why: the field (`t` or `lt`), the value as received and the reason.
    #[serde(rename = "tf", skip_serializing_if = "Vec::is_empty")]
    pub time_faults: Vec<TimeFault>,
    /// The stream's sequence number of the packet that gave this quote its content, when it had one. Used only to order packets; not sent to pages.
    #[serde(skip)]
    pub sequence: Option<i64>,
    /// When the server received the packet that gave this quote its present content (milliseconds since the epoch); null before the store has stamped it. A packet that repeats the held quote
    /// changes nothing and does not move it.
    #[serde(rename = "r")]
    pub received_ms: Option<i64>,
    /// When the server received the packet that last changed the best bid or ask. The stream carries no exchange time for the book, so this is a receipt time, not a book time.
    #[serde(rename = "bs")]
    pub book_seen_ms: Option<i64>,
    /// Receipt time less exchange time of the last packet that carried a new trade: how late the stream delivered it. Null until a trade has arrived after the first sight of the contract.
    #[serde(rename = "dly")]
    pub delivery_delay_ms: Option<i64>,
    /// Packets for this contract discarded because their exchange time was older than the held quote's.
    #[serde(rename = "ooo")]
    pub out_of_order: u32,
    /// When the server last discarded such a packet (its receipt, milliseconds since the epoch); null when none was. The count is for the whole session; this says whether a discard is recent.
    #[serde(rename = "oooAt")]
    pub out_of_order_ms: Option<i64>,
}

/// A time a packet carried that was not used: `field` is `t` (the packet's time) or `lt` (the last trade's), `value` as received, `reason` `implausible` (not an epoch time in the stream's units)
/// or `future` (ahead of the server's clock by more than `MAX_EXCHANGE_AHEAD_MS`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TimeFault {
    #[serde(rename = "f")]
    pub field: &'static str,
    #[serde(rename = "v")]
    pub value: i64,
    #[serde(rename = "why")]
    pub reason: &'static str,
}

/// How far ahead of the server's clock an exchange time may be before it is refused as a future time (the exchange's clock and the server's are not the same clock).
pub const MAX_EXCHANGE_AHEAD_MS: i64 = 60_000;

/// The wall clock in milliseconds since the epoch.
fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0)
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
        // the packet's time (milliseconds) and the last trade's (seconds or milliseconds) are two fields; a value present but not an epoch time is a fault, an absent one (zero) is nothing
        let mut time_faults = Vec::new();
        let exchange_ms = match tick.exchange_ms {
            0 => None,
            raw => exchange_epoch_ms(raw).or_else(|| {
                time_faults.push(TimeFault { field: "t", value: raw, reason: "implausible" });
                None
            }),
        };
        let last_trade_ms = match tick.last_trade_raw {
            0 => None,
            raw => tick.last_trade_ms.or_else(|| {
                time_faults.push(TimeFault { field: "lt", value: raw, reason: "implausible" });
                None
            }),
        };
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
            exchange_ms,
            last_trade_ms,
            time_faults,
            sequence: (tick.sequence > 0).then_some(tick.sequence),
            received_ms: None,
            book_seen_ms: None,
            delivery_delay_ms: None,
            out_of_order: 0,
            out_of_order_ms: None,
        }
    }

    /// Whether `other` says the same thing about the market: every field the stream reports, none of the receipt bookkeeping.
    pub fn same_market(&self, other: &DerivQuote) -> bool {
        (&self.segment, &self.token, self.last, self.bid, self.ask, self.prev_close, self.volume, self.oi, self.exchange_ms, self.last_trade_ms)
            == (&other.segment, &other.token, other.last, other.bid, other.ask, other.prev_close, other.volume, other.oi, other.exchange_ms, other.last_trade_ms)
            && self.oi_change_pct == other.oi_change_pct
    }
}

/// What orders one contract's packets, kept apart from the quote it last produced: a packet without a time must not erase the greatest time seen.
#[derive(Default, Clone, Copy)]
struct Mark {
    /// The greatest valid packet time applied (milliseconds): a packet with an older time is older, whatever the packets between said.
    watermark: Option<i64>,
    /// The sequence number of the packet last applied on this connection; forgotten when the connection is (`reset_sequences`).
    sequence: Option<i64>,
}

#[derive(Default)]
struct Inner {
    rev: u64,
    quotes: HashMap<Key, (DerivQuote, u64)>,
    wanted: HashMap<Key, Instant>,
    marks: HashMap<Key, Mark>,
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
        g.marks.retain(|(e, t), _| live.contains(&(*e, t.clone())));
        let mut by_time: Vec<(&Key, &Instant)> = g.wanted.iter().collect();
        by_time.sort_by(|a, b| b.1.cmp(a.1));
        by_time.into_iter().take(MAX_WANTED).map(|(k, _)| k.clone()).collect()
    }

    pub fn update(&self, exchange_type: u8, quote: DerivQuote) {
        self.update_at(now_ms(), exchange_type, quote);
    }

    /// `update`, with the moment the packet was received given (so a test can control the clock).
    ///
    /// Each contract keeps its own times and no other contract's packet touches them:
    /// - `received_ms` is the receipt time of the packet that gave the quote its present content. A packet that repeats the held quote changes nothing and does not make it newer.
    /// - `book_seen_ms` moves only when the best bid or ask changed; a trade at an unchanged book leaves it where it was (an old book is not refreshed by a new trade), and a new bid with no
    ///   new trade leaves the exchange's trade time where it was.
    /// - `delivery_delay_ms` is receipt less the packet's exchange time, for a packet whose time is **newer** than the greatest seen; the first sight of a contract measures nothing (the packet may be an old snapshot).
    /// - The **ordering watermark** is the greatest valid packet time applied. It is kept apart from the quote, so a packet with no time (or a refused one) does not erase it: a later packet older than the
    ///   watermark is older whatever came between. Where the time is equal, or absent, the stream's sequence number orders the packets when both have one (`reset_sequences` forgets it on a new connection).
    ///   A packet that is older is discarded and counted, never applied, and does not move any time or the watermark. A packet with no usable time and no usable sequence order is applied in arrival order.
    /// - A time ahead of this clock by more than `MAX_EXCHANGE_AHEAD_MS` is refused and recorded in `time_faults`; the packet's content is still applied, and the watermark is never moved by a refused time.
    /// - The last trade's time is the packet's own last-traded field. A packet whose field is absent keeps the time held when its last price is the held one (the last trade did not vanish), and has none otherwise;
    ///   a refused field is none. It is never taken from the packet's exchange time.
    pub fn update_at(&self, now_ms: i64, exchange_type: u8, mut quote: DerivQuote) {
        let mut g = self.0.lock().unwrap();
        let key = (exchange_type, quote.token.clone());
        if !g.wanted.contains_key(&key) {
            return;
        }
        let limit = now_ms + MAX_EXCHANGE_AHEAD_MS;
        if let Some(t) = quote.exchange_ms.filter(|t| *t > limit) {
            quote.time_faults.push(TimeFault { field: "t", value: t, reason: "future" });
            quote.exchange_ms = None;
        }
        if let Some(t) = quote.last_trade_ms.filter(|t| *t > limit) {
            quote.time_faults.push(TimeFault { field: "lt", value: t, reason: "future" });
            quote.last_trade_ms = None;
        }
        quote.time_faults.sort_by_key(|f| f.field != "t"); // the packet's time first, then the trade's
        let held = g.quotes.get(&key).map(|(held, _)| held.clone());
        let mut mark = g.marks.get(&key).copied().unwrap_or_default();
        let earlier_sequence = matches!((quote.sequence, mark.sequence), (Some(new), Some(old)) if new < old);
        let older = match (quote.exchange_ms, mark.watermark) {
            (Some(new), Some(mark_time)) if new < mark_time => true,
            (Some(new), Some(mark_time)) if new == mark_time => earlier_sequence,
            (None, _) => earlier_sequence,
            _ => false,
        };
        if older {
            if let Some(mut kept) = held {
                kept.out_of_order += 1;
                kept.out_of_order_ms = Some(now_ms);
                g.rev += 1;
                let rev = g.rev;
                g.quotes.insert(key, (kept, rev));
                return;
            }
        }
        if let (None, None, Some(held)) = (quote.last_trade_ms, quote.time_faults.iter().find(|f| f.field == "lt"), held.as_ref()) {
            if quote.last == held.last {
                quote.last_trade_ms = held.last_trade_ms;
            }
        }
        if let Some(t) = quote.exchange_ms {
            mark.watermark = Some(mark.watermark.map_or(t, |w| w.max(t)));
        }
        if quote.sequence.is_some() {
            mark.sequence = quote.sequence;
        }
        match held {
            None => {
                quote.received_ms = Some(now_ms);
                quote.book_seen_ms = Some(now_ms);
            }
            Some(held) => {
                if held.same_market(&quote) {
                    g.marks.insert(key, mark); // an identical quote is no change to send, and is not a newer one; only its place in the order is noted
                    return;
                }
                quote.received_ms = Some(now_ms);
                quote.book_seen_ms = if (held.bid, held.ask) != (quote.bid, quote.ask) { Some(now_ms) } else { held.book_seen_ms };
                let before = g.marks.get(&key).and_then(|m| m.watermark);
                quote.delivery_delay_ms = match (quote.exchange_ms, before) {
                    (Some(new), Some(old)) if new > old => Some(now_ms - new),
                    _ => held.delivery_delay_ms,
                };
                quote.out_of_order = held.out_of_order;
                quote.out_of_order_ms = held.out_of_order_ms;
            }
        }
        g.marks.insert(key.clone(), mark);
        g.rev += 1;
        let rev = g.rev;
        g.quotes.insert(key, (quote, rev));
    }

    /// A new connection to the stream: its sequence numbers start again, so the sequences held are forgotten. The time order (the watermark) is kept.
    pub fn reset_sequences(&self) {
        for mark in self.0.lock().unwrap().marks.values_mut() {
            mark.sequence = None;
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
    use crate::angel::decode::{decode, test_packet_on, with_sequence, with_trade_time};

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

    // ---- T3: per-contract receipt, book and trade times, kept apart; delayed and out-of-order packets (a controlled clock: `update_at`) ----
    const T0: i64 = 1_760_000_000_000; // an exchange time

    /// A tick of NFO contract `token` with the given exchange time, last trade and best bid and ask (paise).
    fn at(token: &str, exchange_ms: i64, last: i64, bid: i64, ask: i64) -> DerivQuote {
        let t = decode(&test_packet_on(2, token, exchange_ms, last, 9000, &[(1, 650, bid), (0, 130, ask)], 1_000, 0.0)).unwrap();
        DerivQuote::from_tick("NFO", 2, &t)
    }

    fn held(store: &DerivQuotes, token: &str) -> DerivQuote {
        store.get(&[(2, token.to_string())]).pop().expect("held")
    }

    fn wanted(token: &str) -> (DerivQuotes, Key) {
        let store = DerivQuotes::new();
        let key = (2u8, token.to_string());
        store.want(&[key.clone()], Instant::now());
        (store, key)
    }

    #[test]
    fn the_first_packet_stamps_receipt_and_book_and_a_repeat_changes_nothing() {
        let (store, key) = wanted("7");
        store.update_at(T0 + 500, 2, at("7", T0, 10_000, 9_995, 10_005));
        let q = held(&store, "7");
        assert_eq!((q.received_ms, q.book_seen_ms, q.exchange_ms), (Some(T0 + 500), Some(T0 + 500), Some(T0)));
        assert_eq!(q.delivery_delay_ms, None, "the first sight of a contract measures no delay: it may be an old trade");
        let mut sent = HashMap::new();
        assert_eq!(store.changed(&[key.clone()], &mut sent).len(), 1);
        store.update_at(T0 + 90_000, 2, at("7", T0, 10_000, 9_995, 10_005)); // the same packet again, much later
        let again = held(&store, "7");
        assert_eq!((again.received_ms, again.book_seen_ms), (Some(T0 + 500), Some(T0 + 500)), "a repeat is not a change and does not make the quote newer");
        assert!(store.changed(&[key], &mut sent).is_empty());
    }

    #[test]
    fn a_new_trade_does_not_refresh_the_book_and_a_book_change_does_not_refresh_the_trade() {
        let (store, _) = wanted("8");
        store.update_at(T0 + 100, 2, at("8", T0, 10_000, 9_995, 10_005));
        // a trade at the same bid and ask: the trade time and the receipt move, the book was last seen at the first packet
        store.update_at(T0 + 60_100, 2, at("8", T0 + 60_000, 10_005, 9_995, 10_005));
        let q = held(&store, "8");
        assert_eq!((q.exchange_ms, q.received_ms, q.book_seen_ms), (Some(T0 + 60_000), Some(T0 + 60_100), Some(T0 + 100)), "the book was not seen again: {q:?}");
        assert_eq!(q.delivery_delay_ms, Some(100), "receipt less exchange time of the new trade");
        // a new bid with no new trade: the book was seen again, the trade is as old as it was
        store.update_at(T0 + 120_000, 2, at("8", T0 + 60_000, 10_005, 10_000, 10_010));
        let q = held(&store, "8");
        assert_eq!((q.exchange_ms, q.received_ms, q.book_seen_ms), (Some(T0 + 60_000), Some(T0 + 120_000), Some(T0 + 120_000)));
        assert_eq!(q.delivery_delay_ms, Some(100), "a book change is not a trade: the last delivery delay stands");
    }

    #[test]
    fn a_delayed_trade_is_measured_by_how_late_it_was_received() {
        let (store, _) = wanted("9");
        store.update_at(T0 + 100, 2, at("9", T0, 10_000, 9_995, 10_005));
        store.update_at(T0 + 40_000, 2, at("9", T0 + 5_000, 10_010, 9_995, 10_005)); // a trade made 35 s before it was received
        assert_eq!(held(&store, "9").delivery_delay_ms, Some(35_000));
    }

    #[test]
    fn an_out_of_order_packet_is_discarded_counted_and_leaves_the_held_quote_and_its_times_alone() {
        let (store, key) = wanted("10");
        store.update_at(T0 + 100, 2, at("10", T0 + 10_000, 10_000, 9_995, 10_005));
        let mut sent = HashMap::new();
        store.changed(&[key.clone()], &mut sent);
        store.update_at(T0 + 12_000, 2, at("10", T0 + 4_000, 9_000, 8_995, 9_005)); // an older snapshot arriving after a newer one
        let q = held(&store, "10");
        assert_eq!((q.last, q.bid, q.exchange_ms, q.received_ms, q.book_seen_ms), (Some(100.0), Some(99.95), Some(T0 + 10_000), Some(T0 + 100), Some(T0 + 100)), "the older packet is not applied: {q:?}");
        assert_eq!((q.out_of_order, q.out_of_order_ms), (1, Some(T0 + 12_000)), "counted, with when the discard happened");
        assert_eq!(store.changed(&[key.clone()], &mut sent).len(), 1, "the page learns of the discard once");
        store.update_at(T0 + 13_000, 2, at("10", T0 + 10_000, 10_100, 10_095, 10_105)); // the same exchange time is not older: a book and price update
        let q = held(&store, "10");
        assert_eq!((q.last, q.received_ms, q.out_of_order, q.out_of_order_ms), (Some(101.0), Some(T0 + 13_000), 1, Some(T0 + 12_000)), "a newer packet keeps the count and the time of the last discard");
        let v = serde_json::to_value(&q).unwrap();
        assert_eq!((v["ooo"].clone(), v["oooAt"].clone()), (serde_json::json!(1), serde_json::json!(T0 + 12_000)));
    }

    #[test]
    fn the_times_are_in_the_json_the_page_reads() {
        let (store, _) = wanted("11");
        store.update_at(T0 + 100, 2, at("11", T0, 10_000, 9_995, 10_005));
        let v = serde_json::to_value(held(&store, "11")).unwrap();
        assert_eq!((v["r"].clone(), v["bs"].clone(), v["t"].clone(), v["dly"].clone(), v["ooo"].clone()), (serde_json::json!(T0 + 100), serde_json::json!(T0 + 100), serde_json::json!(T0), serde_json::Value::Null, serde_json::json!(0)));
        assert_eq!(v["oooAt"], serde_json::Value::Null, "no discard, no discard time");
    }

    // ---- T3 review corrections (R1, R2, R6 server side): real packets through the decoder into the store; the packet's time, the last trade's time and the sequence are three things ----
    const S0: i64 = T0 / 1000; // the same instant in seconds, as the stream's last-traded field may carry it

    /// A packet as the stream sends it, decoded and made a quote: packet time in ms (0: none), the last-traded field as sent (0: none; seconds or ms), sequence (0: none), last price, best bid and ask (paise).
    fn pk(token: &str, packet_ms: i64, trade_raw: i64, seq: i64, last: i64, bid: i64, ask: i64) -> DerivQuote {
        let p = with_sequence(with_trade_time(test_packet_on(2, token, packet_ms, last, 9000, &[(1, 650, bid), (0, 130, ask)], 1_000, 0.0), trade_raw), seq);
        DerivQuote::from_tick("NFO", 2, &decode(&p).unwrap())
    }

    #[test]
    fn the_packets_time_and_the_last_trades_time_are_two_times_in_the_quote_and_in_the_json() {
        let q = pk("20", T0 + 60_000, S0, 0, 10_000, 9_995, 10_005); // a packet a minute after the last trade
        assert_eq!((q.exchange_ms, q.last_trade_ms, q.sequence), (Some(T0 + 60_000), Some(T0), None));
        let v = serde_json::to_value(&q).unwrap();
        assert_eq!((v["t"].as_i64(), v["lt"].as_i64()), (Some(T0 + 60_000), Some(T0)));
        assert!(v.get("tf").is_none() && v.get("sequence").is_none(), "no faults, and the sequence is not sent to pages: {v}");
        assert_eq!(pk("20", T0, S0, 7, 10_000, 9_995, 10_005).sequence, Some(7));
    }

    #[test]
    fn a_book_only_update_moves_the_packet_time_and_the_book_but_not_the_trade_time() {
        let (store, _) = wanted("21");
        store.update_at(T0 + 61_000, 2, pk("21", T0 + 60_000, S0, 1, 10_000, 9_995, 10_005));
        store.update_at(T0 + 91_500, 2, pk("21", T0 + 90_000, S0, 2, 10_000, 10_000, 10_005)); // the bid improves; no new trade
        let q = held(&store, "21");
        assert_eq!((q.exchange_ms, q.last_trade_ms, q.bid, q.last), (Some(T0 + 90_000), Some(T0), Some(100.0), Some(100.0)), "{q:?}");
        assert_eq!((q.received_ms, q.book_seen_ms, q.delivery_delay_ms), (Some(T0 + 91_500), Some(T0 + 91_500), Some(1_500)), "the delay is the packet's: receipt less the packet's time");
    }

    #[test]
    fn a_trade_update_moves_the_trade_time_and_not_the_book() {
        let (store, _) = wanted("22");
        store.update_at(T0 + 61_000, 2, pk("22", T0 + 60_000, S0, 1, 10_000, 9_995, 10_005));
        store.update_at(T0 + 106_000, 2, pk("22", T0 + 105_000, S0 + 45, 2, 10_010, 9_995, 10_005)); // a trade 45 s after the first, at the same book
        let q = held(&store, "22");
        assert_eq!((q.last_trade_ms, q.last, q.exchange_ms), (Some(T0 + 45_000), Some(100.1), Some(T0 + 105_000)));
        assert_eq!((q.received_ms, q.book_seen_ms, q.delivery_delay_ms), (Some(T0 + 106_000), Some(T0 + 61_000), Some(1_000)));
    }

    #[test]
    fn an_unavailable_trade_time_is_unknown_and_is_never_taken_from_the_packet() {
        let (store, _) = wanted("23");
        store.update_at(T0 + 61_000, 2, pk("23", T0 + 60_000, S0, 0, 10_000, 9_995, 10_005));
        store.update_at(T0 + 91_000, 2, pk("23", T0 + 90_000, 0, 0, 10_010, 9_995, 10_005)); // a new price and the last-traded field empty
        let q = held(&store, "23");
        assert_eq!((q.last, q.last_trade_ms, q.exchange_ms), (Some(100.1), None, Some(T0 + 90_000)), "the trade's time is unknown, not the packet's: {q:?}");
        assert!(q.time_faults.is_empty(), "an absent time is not a fault");
        // the same price and the field empty: the last trade did not vanish, so the time held stands
        let (store, _) = wanted("24");
        store.update_at(T0 + 61_000, 2, pk("24", T0 + 60_000, S0, 0, 10_000, 9_995, 10_005));
        store.update_at(T0 + 91_000, 2, pk("24", T0 + 90_000, 0, 0, 10_000, 10_000, 10_005));
        assert_eq!(held(&store, "24").last_trade_ms, Some(T0));
    }

    #[test]
    fn a_malformed_time_is_a_recorded_fault_and_not_a_time() {
        let (store, _) = wanted("25");
        store.update_at(T0 + 61_000, 2, pk("25", T0 + 60_000, S0, 0, 10_000, 9_995, 10_005));
        store.update_at(T0 + 91_000, 2, pk("25", 12_345, 5, 0, 10_010, 9_995, 10_005)); // a packet time of 12 s after 1970 and a last-traded field of 5
        let q = held(&store, "25");
        assert_eq!((q.exchange_ms, q.last_trade_ms, q.last), (None, None, Some(100.1)), "the content is applied, the times are not: {q:?}");
        assert_eq!(q.time_faults, vec![TimeFault { field: "t", value: 12_345, reason: "implausible" }, TimeFault { field: "lt", value: 5, reason: "implausible" }]);
        let v = serde_json::to_value(&q).unwrap();
        assert_eq!(v["tf"], serde_json::json!([{"f": "t", "v": 12_345, "why": "implausible"}, {"f": "lt", "v": 5, "why": "implausible"}]));
    }

    #[test]
    fn a_future_time_is_refused_at_the_declared_limit_and_does_not_poison_the_order() {
        let now = T0 + 1_000;
        let (store, _) = wanted("26");
        assert_eq!(MAX_EXCHANGE_AHEAD_MS, 60_000, "the declared tolerance, which the page's policy repeats as `maxExchangeAheadMs`");
        store.update_at(now, 2, pk("26", now + 60_000, now + 60_000, 0, 10_000, 9_995, 10_005)); // exactly at the limit, written out rather than read from the constant: accepted
        let q = held(&store, "26");
        assert_eq!((q.exchange_ms, q.last_trade_ms), (Some(now + 60_000), Some(now + 60_000)));
        assert!(q.time_faults.is_empty());
        let (store, _) = wanted("27");
        store.update_at(now, 2, pk("27", now + 60_001, now + 60_001, 0, 10_000, 9_995, 10_005)); // one millisecond over
        let q = held(&store, "27");
        assert_eq!((q.exchange_ms, q.last_trade_ms, q.last), (None, None, Some(100.0)));
        assert_eq!(q.time_faults, vec![TimeFault { field: "t", value: now + 60_001, reason: "future" }, TimeFault { field: "lt", value: now + 60_001, reason: "future" }]);
        // a packet an hour ahead must not become the watermark that every later, honest packet is compared with
        let (store, _) = wanted("28");
        store.update_at(now, 2, pk("28", now + 3_600_000, 0, 0, 10_000, 9_995, 10_005));
        store.update_at(now + 1_000, 2, pk("28", T0 + 500, S0, 0, 10_100, 10_095, 10_105));
        let q = held(&store, "28");
        assert_eq!((q.last, q.exchange_ms, q.out_of_order), (Some(101.0), Some(T0 + 500), 0), "the honest packet is applied: {q:?}");
    }

    #[test]
    fn a_time_less_packet_does_not_erase_the_ordering_watermark_known_then_missing_then_older() {
        let (store, _) = wanted("30");
        store.update_at(T0 + 300_000, 2, pk("30", T0 + 200_000, S0, 0, 10_000, 9_995, 10_005));
        store.update_at(T0 + 400_000, 2, pk("30", 0, S0, 0, 10_100, 10_095, 10_105)); // no packet time: applied in arrival order
        let q = held(&store, "30");
        assert_eq!((q.last, q.exchange_ms, q.received_ms, q.book_seen_ms), (Some(101.0), None, Some(T0 + 400_000), Some(T0 + 400_000)));
        store.update_at(T0 + 500_000, 2, pk("30", T0 + 100_000, S0, 0, 9_900, 9_895, 9_905)); // older than the greatest packet time seen (T0 + 200_000)
        let q = held(&store, "30");
        assert_eq!((q.last, q.bid, q.received_ms, q.book_seen_ms, q.out_of_order), (Some(101.0), Some(100.95), Some(T0 + 400_000), Some(T0 + 400_000), 1), "the stale packet is discarded and counted: {q:?}");
    }

    #[test]
    fn known_then_missing_then_newer_is_applied_and_the_watermark_moves_on() {
        let (store, _) = wanted("31");
        store.update_at(T0 + 300_000, 2, pk("31", T0 + 200_000, S0, 0, 10_000, 9_995, 10_005));
        store.update_at(T0 + 400_000, 2, pk("31", 0, S0, 0, 10_100, 10_095, 10_105));
        store.update_at(T0 + 500_000, 2, pk("31", T0 + 300_000, S0, 0, 10_200, 10_195, 10_205)); // newer than the watermark
        let q = held(&store, "31");
        assert_eq!((q.last, q.exchange_ms, q.received_ms, q.out_of_order), (Some(102.0), Some(T0 + 300_000), Some(T0 + 500_000), 0));
        store.update_at(T0 + 600_000, 2, pk("31", T0 + 250_000, S0, 0, 9_000, 8_995, 9_005)); // older than the new watermark
        let q = held(&store, "31");
        assert_eq!((q.last, q.out_of_order), (Some(102.0), 1));
    }

    #[test]
    fn a_duplicate_packet_is_no_change_with_or_without_a_time() {
        let (store, key) = wanted("32");
        let mut sent = HashMap::new();
        store.update_at(T0 + 1_000, 2, pk("32", 0, S0, 0, 10_000, 9_995, 10_005));
        assert_eq!(store.changed(&[key.clone()], &mut sent).len(), 1);
        store.update_at(T0 + 90_000, 2, pk("32", 0, S0, 0, 10_000, 9_995, 10_005));
        assert!(store.changed(&[key.clone()], &mut sent).is_empty());
        assert_eq!(held(&store, "32").received_ms, Some(T0 + 1_000));
        store.update_at(T0 + 291_000, 2, pk("32", T0 + 200_000, S0, 3, 10_000, 9_995, 10_005));
        store.changed(&[key.clone()], &mut sent);
        store.update_at(T0 + 292_000, 2, pk("32", T0 + 200_000, S0, 3, 10_000, 9_995, 10_005));
        assert!(store.changed(&[key], &mut sent).is_empty());
        assert_eq!(held(&store, "32").received_ms, Some(T0 + 291_000));
    }

    #[test]
    fn the_same_event_time_with_a_distinct_sequence_is_ordered_by_the_sequence() {
        let (store, _) = wanted("33");
        store.update_at(T0 + 300_000, 2, pk("33", T0 + 200_000, S0, 5, 10_000, 9_995, 10_005));
        store.update_at(T0 + 301_000, 2, pk("33", T0 + 200_000, S0, 4, 9_000, 8_995, 9_005)); // the same event time, an earlier sequence: older
        let q = held(&store, "33");
        assert_eq!((q.last, q.received_ms, q.out_of_order), (Some(100.0), Some(T0 + 300_000), 1));
        store.update_at(T0 + 302_000, 2, pk("33", T0 + 200_000, S0, 6, 10_200, 10_195, 10_205)); // a later sequence: newer
        let q = held(&store, "33");
        assert_eq!((q.last, q.received_ms, q.out_of_order), (Some(102.0), Some(T0 + 302_000), 1));
        store.update_at(T0 + 303_000, 2, pk("33", T0 + 200_000, S0, 5, 10_000, 9_995, 10_005)); // sequence 5 is now older than 6
        let q = held(&store, "33");
        assert_eq!((q.last, q.out_of_order), (Some(102.0), 2));
    }

    #[test]
    fn a_time_less_packet_with_an_earlier_sequence_is_older() {
        let (store, _) = wanted("34");
        store.update_at(T0 + 1_000, 2, pk("34", 0, S0, 10, 10_000, 9_995, 10_005));
        store.update_at(T0 + 2_000, 2, pk("34", 0, S0, 3, 9_000, 8_995, 9_005));
        let q = held(&store, "34");
        assert_eq!((q.last, q.received_ms, q.out_of_order), (Some(100.0), Some(T0 + 1_000), 1));
        store.update_at(T0 + 3_000, 2, pk("34", 0, S0, 11, 10_100, 10_095, 10_105));
        assert_eq!((held(&store, "34").last, held(&store, "34").out_of_order), (Some(101.0), 1));
        // without a sequence on either side, arrival order is all there is
        store.update_at(T0 + 4_000, 2, pk("34", 0, S0, 0, 10_200, 10_195, 10_205));
        assert_eq!(held(&store, "34").last, Some(102.0));
    }

    #[test]
    fn a_new_connection_forgets_the_sequence_numbers_but_not_the_time_order() {
        let (store, _) = wanted("35");
        store.update_at(T0 + 300_000, 2, pk("35", T0 + 200_000, S0, 50, 10_000, 9_995, 10_005));
        store.reset_sequences();
        store.update_at(T0 + 301_000, 2, pk("35", T0 + 200_000, S0, 1, 10_100, 10_095, 10_105)); // the stream's numbering started again
        let q = held(&store, "35");
        assert_eq!((q.last, q.out_of_order), (Some(101.0), 0), "{q:?}");
        store.update_at(T0 + 302_000, 2, pk("35", T0 + 100_000, S0, 2, 9_000, 8_995, 9_005)); // an older packet time is older on any connection
        let q = held(&store, "35");
        assert_eq!((q.last, q.out_of_order), (Some(101.0), 1));
    }

    /// The rows the quotes route would send for packets of several situations, built by the decoder and the store the server runs, with controlled clocks: the web tests read them.
    fn the_cases() -> serde_json::Value {
        use serde_json::json;
        const N0: i64 = T0 + 600_000; // the server's clock when the rows are read
        let mut cases = serde_json::Map::new();
        let mut put = |name: &str, note: &str, now: i64, store: &DerivQuotes, tokens: &[&str]| {
            let keys: Vec<Key> = tokens.iter().map(|t| (2u8, t.to_string())).collect();
            cases.insert(name.to_string(), json!({ "note": note, "now": now, "rows": store.get(&keys) }));
        };
        let store = |tokens: &[&str]| {
            let s = DerivQuotes::new();
            s.want(&tokens.iter().map(|t| (2u8, t.to_string())).collect::<Vec<_>>(), Instant::now());
            s
        };
        let s = store(&["40"]);
        s.update_at(N0 - 30_000, 2, pk("40", N0 - 31_000, (N0 - 7_200_000) / 1000, 1, 10_000, 9_995, 10_005));
        s.update_at(N0 - 2_000, 2, pk("40", N0 - 3_000, (N0 - 7_200_000) / 1000, 2, 10_000, 10_000, 10_010));
        put("fresh_book_old_trade", "a book that changed 2 s ago beside a last trade two hours old: the book is fresh and the trade is old", N0, &s, &["40"]);
        let s = store(&["41"]);
        s.update_at(N0 - 30_000, 2, pk("41", N0 - 31_000, 0, 1, 10_000, 9_995, 10_005));
        s.update_at(N0 - 2_000, 2, pk("41", N0 - 3_000, 0, 2, 10_010, 10_000, 10_010));
        put("no_trade_time", "the last-traded field is empty: the trade's time is unknown and is not taken from the packet", N0, &s, &["41"]);
        let s = store(&["42"]);
        s.update_at(N0 - 2_000, 2, pk("42", N0 + 3_600_000, 5, 1, 10_000, 9_995, 10_005));
        put("refused_times", "a packet time an hour ahead and a last-traded field of 5: neither is used, both are on record", N0, &s, &["42"]);
        let s = store(&["43"]);
        s.update_at(N0 - 300_000, 2, pk("43", N0 - 301_000, (N0 - 301_000) / 1000, 1, 10_000, 9_995, 10_005));
        s.update_at(N0 - 200_000, 2, pk("43", 0, (N0 - 301_000) / 1000, 2, 10_100, 10_095, 10_105));
        s.update_at(N0 - 100_000, 2, pk("43", N0 - 400_000, (N0 - 301_000) / 1000, 3, 9_900, 9_895, 9_905));
        put("known_missing_older", "a packet time known, then none, then an older one: the older packet is discarded and counted", N0, &s, &["43"]);
        let s = store(&["44", "45"]);
        for token in ["44", "45"] {
            s.update_at(N0 - 1_000, 2, pk(token, N0 - 86_400_000 - 5_000, (N0 - 86_400_000 - 5_000) / 1000, 1, 10_000, 9_995, 10_005)); // yesterday's snapshot, received just now
        }
        put("replay", "packets received just now that carry yesterday's exchange times: a replay or a closed market, not current activity", N0, &s, &["44", "45"]);
        let s = store(&["46"]);
        s.update_at(N0 - 600_000, 2, pk("46", N0 - 601_000, (N0 - 601_000) / 1000, 1, 10_000, 9_995, 10_005));
        put("stopped", "the last packet was received ten minutes ago and carried a time close to its receipt: a feed that has gone quiet or stopped", N0, &s, &["46"]);
        let s = store(&["47"]);
        s.update_at(N0 - 2_000, 2, pk("47", N0 - 3_000, (N0 - 3_000) / 1000, 1, 10_000, 9_995, 10_005));
        put("live", "a packet received 2 s ago with a time 1 s before its receipt", N0, &s, &["47"]);
        serde_json::Value::Object(cases)
    }

    #[test]
    fn the_rows_of_real_packets_match_the_ui_fixture() {
        let got = the_cases();
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("web/test/fixtures/deriv-quotes.cases.json");
        if std::env::var("ATHENA_UPDATE_FIXTURES").as_deref() == Ok("1") {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, serde_json::to_string_pretty(&got).unwrap() + "\n").unwrap();
            return;
        }
        let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e} (run with ATHENA_UPDATE_FIXTURES=1 to write it)", path.display()));
        let want: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(got, want, "the quotes route's rows no longer match terminal/web/test/fixtures/deriv-quotes.cases.json: review the change, then regenerate with ATHENA_UPDATE_FIXTURES=1");
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
