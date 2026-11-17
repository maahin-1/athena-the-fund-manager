//! Decode one snap-quote packet from Angel One's market-data stream (little-endian; prices in paise).
//! Offsets follow the SmartAPI v2 stream layout; mode 3 packets are 379 bytes.

pub const SNAP_QUOTE_LEN: usize = 379;

#[derive(Debug, Clone, PartialEq)]
pub struct Tick {
    /// The stream's exchange type (1 NSE cash, 2 NFO, 3 BSE cash, 4 BFO, 5 MCX, …): a token is unique only within its exchange.
    pub exchange_type: u8,
    pub token: String,
    /// The packet's own exchange timestamp as sent (bytes 35–43): the time of the event the packet reports, not of the last trade.
    pub exchange_ms: i64,
    /// (T3-R1) The last-traded timestamp as sent (bytes 123–131): a different field. Its unit is not stated by the stream; see `last_trade_ms`.
    pub last_trade_raw: i64,
    /// The last-traded time in milliseconds when `last_trade_raw` is a plausible epoch time in either seconds or milliseconds; none when it is absent (zero) or malformed.
    pub last_trade_ms: Option<i64>,
    /// (T3-R2) The stream's sequence number (bytes 27–35): zero or negative when the packet has none.
    pub sequence: i64,
    pub last: i64,
    pub average: i64,
    pub volume: i64,
    pub open: i64,
    pub high: i64,
    pub low: i64,
    pub prev_close: i64,
    pub upper: i64,
    pub lower: i64,
    pub high_52: i64,
    pub low_52: i64,
    /// Open interest in contracts (futures and options; zero for a share) and its change in percent since the previous close.
    pub open_interest: i64,
    pub oi_change_pct: f64,
    /// `(price, quantity)`, best first as the packet lists them; empty levels dropped.
    pub bids: Vec<(i64, i64)>,
    pub asks: Vec<(i64, i64)>,
}

#[derive(Debug, PartialEq)]
pub enum DecodeError {
    Unsupported,
}

/// 2000-01-01 and 2100-01-01 in seconds: an epoch time outside them is not a market time in either unit. The ranges of seconds and of milliseconds do not overlap, so a magnitude says which it is.
const EPOCH_2000_S: i64 = 946_684_800;
const EPOCH_2100_S: i64 = 4_102_444_800;

/// A time in milliseconds from a raw epoch value that is either seconds or milliseconds (the stream's last-traded field states no unit), by its magnitude; none when it fits neither range
/// (absent, negative, tiny or enormous). The unit is a declared reading of the value, checked only against the repository's own earlier reading of the same field (`src/athena/live/angel.py`),
/// not against a live stream.
pub fn epoch_ms(raw: i64) -> Option<i64> {
    if (EPOCH_2000_S..EPOCH_2100_S).contains(&raw) {
        Some(raw * 1000)
    } else {
        exchange_epoch_ms(raw)
    }
}

/// A time that is in milliseconds (the packet's exchange timestamp); none when it is not an epoch time of 2000 to 2099.
pub fn exchange_epoch_ms(raw: i64) -> Option<i64> {
    (EPOCH_2000_S * 1000..EPOCH_2100_S * 1000).contains(&raw).then_some(raw)
}

fn i64_at(p: &[u8], at: usize) -> i64 {
    i64::from_le_bytes(p[at..at + 8].try_into().expect("slice of 8"))
}

fn f64_at(p: &[u8], at: usize) -> f64 {
    f64::from_le_bytes(p[at..at + 8].try_into().expect("slice of 8"))
}

fn u16_at(p: &[u8], at: usize) -> u16 {
    u16::from_le_bytes(p[at..at + 2].try_into().expect("slice of 2"))
}

pub fn decode(packet: &[u8]) -> Result<Tick, DecodeError> {
    if packet.len() < SNAP_QUOTE_LEN || packet[0] != 3 {
        return Err(DecodeError::Unsupported);
    }
    let token_bytes = &packet[2..27];
    let end = token_bytes.iter().position(|b| *b == 0).unwrap_or(token_bytes.len());
    let token = String::from_utf8_lossy(&token_bytes[..end]).into_owned();
    let (mut bids, mut asks) = (Vec::new(), Vec::new());
    for i in 0..10 {
        let at = 147 + i * 20;
        let (flag, quantity, price) = (u16_at(packet, at), i64_at(packet, at + 2), i64_at(packet, at + 10));
        if price <= 0 || quantity <= 0 {
            continue;
        }
        if flag == 1 {
            bids.push((price, quantity));
        } else if flag == 0 {
            asks.push((price, quantity));
        }
    }
    Ok(Tick {
        exchange_type: packet[1],
        token,
        exchange_ms: i64_at(packet, 35),
        last_trade_raw: i64_at(packet, 123),
        last_trade_ms: epoch_ms(i64_at(packet, 123)),
        sequence: i64_at(packet, 27),
        last: i64_at(packet, 43),
        average: i64_at(packet, 59),
        volume: i64_at(packet, 67),
        open: i64_at(packet, 91),
        high: i64_at(packet, 99),
        low: i64_at(packet, 107),
        prev_close: i64_at(packet, 115),
        upper: i64_at(packet, 347),
        lower: i64_at(packet, 355),
        high_52: i64_at(packet, 363),
        low_52: i64_at(packet, 371),
        open_interest: i64_at(packet, 131),
        oi_change_pct: f64_at(packet, 139),
        bids,
        asks,
    })
}

/// A snap-quote packet built from fields, for tests here and in the stream and server tests.
#[cfg(test)]
pub fn test_packet(token: &str, ms: i64, last: i64, prev: i64, depth: &[(u16, i64, i64)]) -> Vec<u8> {
    test_packet_on(1, token, ms, last, prev, depth, 0, 0.0)
}

/// The packet with its last-traded timestamp field (bytes 123–131) set to `raw` (seconds or milliseconds, as a test needs).
#[cfg(test)]
pub fn with_trade_time(mut p: Vec<u8>, raw: i64) -> Vec<u8> {
    p[123..131].copy_from_slice(&raw.to_le_bytes());
    p
}

/// The packet with its sequence number (bytes 27–35) set.
#[cfg(test)]
pub fn with_sequence(mut p: Vec<u8>, seq: i64) -> Vec<u8> {
    p[27..35].copy_from_slice(&seq.to_le_bytes());
    p
}

/// The same on a given exchange type and with open interest and its change in percent.
#[cfg(test)]
#[allow(clippy::too_many_arguments)]
pub fn test_packet_on(exchange: u8, token: &str, ms: i64, last: i64, prev: i64, depth: &[(u16, i64, i64)], oi: i64, oi_pct: f64) -> Vec<u8> {
    let mut p = vec![0u8; SNAP_QUOTE_LEN];
    p[0] = 3;
    p[1] = exchange;
    p[2..2 + token.len()].copy_from_slice(token.as_bytes());
    p[35..43].copy_from_slice(&ms.to_le_bytes());
    p[43..51].copy_from_slice(&last.to_le_bytes());
    p[115..123].copy_from_slice(&prev.to_le_bytes());
    for (i, (flag, qty, price)) in depth.iter().enumerate() {
        let o = 147 + i * 20;
        p[o..o + 2].copy_from_slice(&flag.to_le_bytes());
        p[o + 2..o + 10].copy_from_slice(&qty.to_le_bytes());
        p[o + 10..o + 18].copy_from_slice(&price.to_le_bytes());
    }
    p[131..139].copy_from_slice(&oi.to_le_bytes());
    p[139..147].copy_from_slice(&oi_pct.to_le_bytes());
    p[347..355].copy_from_slice(&88290i64.to_le_bytes());
    p[355..363].copy_from_slice(&72240i64.to_le_bytes());
    p[363..371].copy_from_slice(&91000i64.to_le_bytes());
    p[371..379].copy_from_slice(&60000i64.to_le_bytes());
    p
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fields_are_read_back() {
        let p = test_packet("3045", 1_760_000_000_000, 81240, 80270, &[(1, 1200, 81245), (1, 3400, 81240), (0, 900, 81250)]);
        let t = decode(&p).unwrap();
        assert_eq!(t.token, "3045");
        assert_eq!(t.exchange_ms, 1_760_000_000_000);
        assert_eq!((t.last, t.prev_close), (81240, 80270));
        assert_eq!((t.upper, t.lower, t.high_52, t.low_52), (88290, 72240, 91000, 60000));
    }

    #[test]
    fn open_interest_and_its_change_are_read_from_a_derivative_packet_and_are_zero_for_a_share() {
        let mut p = test_packet("53300", 0, 12_550, 11_000, &[]);
        p[1] = 2; // the NSE derivatives segment
        p[131..139].copy_from_slice(&1_234_500i64.to_le_bytes());
        p[139..147].copy_from_slice(&(-7.25f64).to_le_bytes());
        let t = decode(&p).unwrap();
        assert_eq!(t.open_interest, 1_234_500);
        assert!((t.oi_change_pct - -7.25).abs() < 1e-12);
        let share = decode(&test_packet("3045", 0, 81240, 80270, &[])).unwrap();
        assert_eq!((share.open_interest, share.oi_change_pct), (0, 0.0));
    }

    #[test]
    fn open_interest_does_not_disturb_the_neighbouring_fields() {
        let mut p = test_packet("53300", 1_760_000_000_000, 12_550, 11_000, &[(1, 75, 12_545), (0, 150, 12_555)]);
        p[131..139].copy_from_slice(&i64::MAX.to_le_bytes());
        p[139..147].copy_from_slice(&f64::MAX.to_le_bytes());
        let t = decode(&p).unwrap();
        assert_eq!((t.last, t.prev_close, t.exchange_ms), (12_550, 11_000, 1_760_000_000_000));
        assert_eq!((t.upper, t.lower, t.high_52, t.low_52), (88290, 72240, 91000, 60000));
        assert_eq!((t.bids.clone(), t.asks.clone()), (vec![(12_545, 75)], vec![(12_555, 150)]));
    }

    // ---- T3-R1/R2: the packet's own time, the last trade's time and the sequence are three fields ----
    #[test]
    fn the_packet_time_the_last_trade_time_and_the_sequence_are_read_from_their_own_places() {
        let p = with_sequence(with_trade_time(test_packet("3045", 1_760_000_060_000, 81240, 80270, &[(1, 10, 81235)]), 1_760_000_000), 42);
        let t = decode(&p).unwrap();
        assert_eq!(t.exchange_ms, 1_760_000_060_000, "the packet's own time");
        assert_eq!(t.last_trade_raw, 1_760_000_000, "the raw last-traded field");
        assert_eq!(t.last_trade_ms, Some(1_760_000_000_000), "the stream gave seconds: the trade is a minute older than the packet");
        assert_eq!(t.sequence, 42);
        assert_eq!((t.last, t.prev_close, t.open_interest, t.bids.clone()), (81240, 80270, 0, vec![(81235, 10)]), "the neighbouring fields are undisturbed");
    }

    #[test]
    fn a_last_trade_time_in_either_unit_is_read_and_an_absent_or_malformed_one_is_none_with_the_raw_value_kept() {
        let at = |raw: i64| decode(&with_trade_time(test_packet("3045", 1_760_000_060_000, 81240, 80270, &[]), raw)).unwrap();
        assert_eq!(at(1_760_000_000_000).last_trade_ms, Some(1_760_000_000_000), "milliseconds");
        assert_eq!(at(1_760_000_000).last_trade_ms, Some(1_760_000_000_000), "seconds");
        for bad in [0i64, -5, 7, 946_684_799, 4_102_444_800, 946_684_799_999, 4_102_444_800_000, i64::MAX] {
            let t = at(bad);
            assert_eq!((t.last_trade_ms, t.last_trade_raw), (None, bad), "{bad} is not a plausible epoch time in seconds or milliseconds");
        }
        assert_eq!(at(946_684_800).last_trade_ms, Some(946_684_800_000), "the first plausible second");
        assert_eq!(at(4_102_444_799).last_trade_ms, Some(4_102_444_799_000), "the last plausible second");
    }

    #[test]
    fn flag_one_is_a_bid_and_flag_zero_an_ask() {
        let p = test_packet("3045", 0, 81240, 80270, &[(1, 1200, 81245), (1, 3400, 81240), (0, 900, 81250)]);
        let t = decode(&p).unwrap();
        assert_eq!(t.bids, vec![(81245, 1200), (81240, 3400)]);
        assert_eq!(t.asks, vec![(81250, 900)]);
    }

    #[test]
    fn empty_levels_are_dropped() {
        let p = test_packet("3045", 0, 81240, 80270, &[(1, 0, 81245), (0, 900, 0)]);
        let t = decode(&p).unwrap();
        assert!(t.bids.is_empty() && t.asks.is_empty());
    }

    #[test]
    fn short_and_other_mode_packets_are_unsupported() {
        assert_eq!(decode(&[0u8; 100]), Err(DecodeError::Unsupported));
        let mut p = test_packet("3045", 0, 1, 1, &[]);
        p[0] = 1;
        assert_eq!(decode(&p), Err(DecodeError::Unsupported));
    }
}
