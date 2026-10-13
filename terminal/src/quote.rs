//! The quote the page receives. Field names are short on purpose: this is the hot path.
use serde::Serialize;

use crate::angel::decode::Tick;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Source {
    Stream,
    Poll,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Quote {
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "l")]
    pub last: f64,
    #[serde(rename = "pc")]
    pub prev_close: f64,
    #[serde(rename = "o")]
    pub open: Option<f64>,
    #[serde(rename = "h")]
    pub high: Option<f64>,
    #[serde(rename = "lo")]
    pub low: Option<f64>,
    #[serde(rename = "v")]
    pub volume: Option<f64>,
    #[serde(rename = "avg")]
    pub average: Option<f64>,
    #[serde(rename = "h52")]
    pub high_52: Option<f64>,
    #[serde(rename = "l52")]
    pub low_52: Option<f64>,
    #[serde(rename = "lb")]
    pub lower_band: Option<f64>,
    #[serde(rename = "ub")]
    pub upper_band: Option<f64>,
    /// Exchange timestamp, milliseconds since the Unix epoch.
    #[serde(rename = "t")]
    pub exchange_ms: Option<i64>,
    #[serde(rename = "b")]
    pub bids: Vec<(f64, f64)>,
    #[serde(rename = "a")]
    pub asks: Vec<(f64, f64)>,
    #[serde(rename = "src")]
    pub source: Source,
}

fn rupees(paise: i64) -> Option<f64> {
    (paise > 0).then(|| paise as f64 / 100.0)
}

fn levels(rows: &[(i64, i64)]) -> Vec<(f64, f64)> {
    rows.iter().filter(|(p, q)| *p > 0 && *q > 0).take(5).map(|(p, q)| (*p as f64 / 100.0, *q as f64)).collect()
}

impl Quote {
    /// A streamed quote from one decoded tick; `None` when the tick holds no price or no previous close.
    pub fn from_tick(symbol: &str, tick: &Tick) -> Option<Quote> {
        let (last, prev_close) = (rupees(tick.last)?, rupees(tick.prev_close)?);
        Some(Quote {
            symbol: symbol.to_string(),
            last,
            prev_close,
            open: rupees(tick.open),
            high: rupees(tick.high),
            low: rupees(tick.low),
            volume: (tick.volume > 0).then(|| tick.volume as f64),
            average: rupees(tick.average),
            high_52: rupees(tick.high_52),
            low_52: rupees(tick.low_52),
            lower_band: rupees(tick.lower),
            upper_band: rupees(tick.upper),
            exchange_ms: (tick.exchange_ms > 100_000_000_000).then_some(tick.exchange_ms),
            bids: levels(&tick.bids),
            asks: levels(&tick.asks),
            source: Source::Stream,
        })
    }
}

#[cfg(test)]
pub fn sample(symbol: &str, last: f64, source: Source) -> Quote {
    Quote {
        symbol: symbol.into(), last, prev_close: last - 1.0, open: None, high: None, low: None, volume: None, average: None,
        high_52: None, low_52: None, lower_band: None, upper_band: None, exchange_ms: None, bids: vec![], asks: vec![], source,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::angel::decode::{decode, test_packet};

    #[test]
    fn paise_become_rupees() {
        let t = decode(&test_packet("3045", 1_760_000_000_000, 81240, 80270, &[(1, 1200, 81245), (0, 900, 81250)])).unwrap();
        let q = Quote::from_tick("SBIN", &t).unwrap();
        assert_eq!(q.last, 812.4);
        assert_eq!(q.prev_close, 802.7);
        assert_eq!(q.bids, vec![(812.45, 1200.0)]);
        assert_eq!(q.asks, vec![(812.5, 900.0)]);
        assert_eq!((q.lower_band, q.upper_band), (Some(722.4), Some(882.9)));
        assert_eq!(q.source, Source::Stream);
    }

    #[test]
    fn a_tick_without_a_price_is_no_quote() {
        let t = decode(&test_packet("3045", 0, 0, 80270, &[])).unwrap();
        assert!(Quote::from_tick("SBIN", &t).is_none());
        let t = decode(&test_packet("3045", 0, 81240, 0, &[])).unwrap();
        assert!(Quote::from_tick("SBIN", &t).is_none());
    }

    #[test]
    fn json_uses_the_short_names() {
        let v = serde_json::to_value(sample("SBIN", 812.4, Source::Poll)).unwrap();
        assert_eq!(v["s"], "SBIN");
        assert_eq!(v["l"], 812.4);
        assert_eq!(v["src"], "poll");
        assert!(v.get("symbol").is_none());
    }
}
