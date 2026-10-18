//! Decode one snap-quote packet from Angel One's market-data stream (little-endian; prices in paise).
//! Offsets follow the SmartAPI v2 stream layout; mode 3 packets are 379 bytes.

pub const SNAP_QUOTE_LEN: usize = 379;

#[derive(Debug, Clone, PartialEq)]
pub struct Tick {
    pub token: String,
    pub exchange_ms: i64,
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
        token,
        exchange_ms: i64_at(packet, 35),
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
    let mut p = vec![0u8; SNAP_QUOTE_LEN];
    p[0] = 3;
    p[1] = 1;
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
    p[131..139].copy_from_slice(&0i64.to_le_bytes());
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
