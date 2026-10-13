//! The fallback: the exchange's own quote snapshot (changes about every five seconds). Used for symbols the stream is not delivering.
use std::collections::HashMap;
use std::time::{Duration, Instant};

use chrono::{FixedOffset, NaiveDateTime, TimeZone};
use serde_json::Value;
use tokio_util::sync::CancellationToken;

use crate::hub::Hub;
use crate::quote::{Quote, Source};

pub const QUOTE_URL: &str = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi";
/// The exchange answers empty unless the series is the stock's own; EQ first, as most are.
pub const SERIES: [&str; 5] = ["EQ", "SM", "BE", "BZ", "ST"];
const EVERY: Duration = Duration::from_secs(3);
const PER_ROUND: usize = 10;

fn number(v: Option<&Value>) -> Option<f64> {
    let n = match v? {
        Value::Number(n) => n.as_f64()?,
        Value::String(s) => s.trim().replace(',', "").parse::<f64>().ok()?,
        _ => return None,
    };
    n.is_finite().then_some(n)
}

fn positive(v: Option<&Value>) -> Option<f64> {
    number(v).filter(|n| *n > 0.0)
}

fn side(book: &Value, name: &str) -> Vec<(f64, f64)> {
    (1..=5)
        .filter_map(|i| Some((positive(book.get(format!("{name}Price{i}")))?, positive(book.get(format!("{name}Quantity{i}")))?)))
        .collect()
}

fn exchange_ms(text: Option<&str>) -> Option<i64> {
    let naive = NaiveDateTime::parse_from_str(text?, "%d-%b-%Y %H:%M:%S").ok()?;
    let ist = FixedOffset::east_opt(5 * 3600 + 1800)?;
    Some(ist.from_local_datetime(&naive).single()?.timestamp_millis())
}

pub fn parse_quote(symbol: &str, body: &Value) -> Option<Quote> {
    let data = body.get("equityResponse")?.get(0)?;
    let empty = Value::Null;
    let (meta, trade, price, book) = (
        data.get("metaData").unwrap_or(&empty),
        data.get("tradeInfo").unwrap_or(&empty),
        data.get("priceInfo").unwrap_or(&empty),
        data.get("orderBook").unwrap_or(&empty),
    );
    let last = positive(trade.get("lastPrice"))?;
    let prev_close = positive(meta.get("previousClose"))?;
    Some(Quote {
        symbol: symbol.to_string(),
        last,
        prev_close,
        open: positive(meta.get("open")),
        high: positive(meta.get("dayHigh")),
        low: positive(meta.get("dayLow")),
        volume: positive(trade.get("totalTradedVolume")),
        average: None,
        high_52: positive(price.get("yearHigh")),
        low_52: positive(price.get("yearLow")),
        lower_band: None,
        upper_band: None,
        exchange_ms: exchange_ms(data.get("lastUpdateTime").and_then(Value::as_str)),
        bids: side(book, "buy"),
        asks: side(book, "sell"),
        source: Source::Poll,
    })
}

/// The series to try for a symbol: the one that last worked first, then the rest in the usual order.
pub fn series_order(known: Option<&str>) -> Vec<&'static str> {
    let mut out: Vec<&'static str> = Vec::with_capacity(SERIES.len());
    if let Some(k) = known.and_then(|k| SERIES.iter().find(|s| **s == k)) {
        out.push(k);
    }
    out.extend(SERIES.iter().filter(|s| !out.contains(s)).copied().collect::<Vec<_>>());
    out
}

async fn read(client: &reqwest::Client, symbol: &str, series: &str) -> Option<Quote> {
    let body: Value = client
        .get(QUOTE_URL)
        .query(&[("functionName", "getSymbolData"), ("marketType", "N"), ("series", series), ("symbol", symbol)])
        .header("User-Agent", "Mozilla/5.0")
        .header("Accept", "application/json")
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    parse_quote(symbol, &body)
}

/// Every few seconds, ask the exchange for a few of the wanted symbols that the stream is not delivering.
pub async fn run_poller(hub: Hub, client: reqwest::Client, shutdown: CancellationToken) {
    let mut series: HashMap<String, &'static str> = HashMap::new();
    let mut cursor = 0usize;
    loop {
        tokio::select! {
            _ = shutdown.cancelled() => return,
            _ = tokio::time::sleep(EVERY) => {}
        }
        let stale = hub.stale_symbols(Instant::now());
        if stale.is_empty() {
            continue;
        }
        let take: Vec<String> = (0..stale.len().min(PER_ROUND)).map(|i| stale[(cursor + i) % stale.len()].clone()).collect();
        cursor = cursor.wrapping_add(PER_ROUND);
        let jobs = take.into_iter().map(|symbol| {
            let known = series.get(&symbol).copied();
            let client = client.clone();
            async move {
                for s in series_order(known) {
                    if let Some(q) = read(&client, &symbol, s).await {
                        return Some((symbol, s, q));
                    }
                }
                None
            }
        });
        for (symbol, s, quote) in futures_util::future::join_all(jobs).await.into_iter().flatten() {
            series.insert(symbol, s);
            hub.update(quote, Instant::now());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn body() -> Value {
        json!({"equityResponse": [{
            "metaData": {"previousClose": 802.7, "open": 805.1, "dayHigh": 813.0, "dayLow": 804.2},
            "tradeInfo": {"lastPrice": "812.4", "totalTradedVolume": 4200000},
            "priceInfo": {"yearHigh": 912.0, "yearLow": 600.0},
            "orderBook": {"buyPrice1": 812.35, "buyQuantity1": 1200, "buyPrice2": 812.3, "buyQuantity2": 3000, "sellPrice1": 812.45, "sellQuantity1": 900},
            "lastUpdateTime": "14-Oct-2026 10:15:30"
        }]})
    }

    #[test]
    fn a_full_body_parses() {
        let q = parse_quote("SBIN", &body()).unwrap();
        assert_eq!((q.last, q.prev_close, q.source), (812.4, 802.7, Source::Poll));
        assert_eq!((q.open, q.high, q.low), (Some(805.1), Some(813.0), Some(804.2)));
        assert_eq!(q.bids, vec![(812.35, 1200.0), (812.3, 3000.0)]);
        assert_eq!(q.asks, vec![(812.45, 900.0)]);
        // 10:15:30 IST is 04:45:30 UTC
        assert_eq!(q.exchange_ms, Some(1_791_953_130_000));
    }

    #[test]
    fn empty_and_priceless_bodies_are_none() {
        assert!(parse_quote("SBIN", &json!({"equityResponse": []})).is_none());
        assert!(parse_quote("SBIN", &json!({})).is_none());
        let mut b = body();
        b["equityResponse"][0]["tradeInfo"]["lastPrice"] = json!(0);
        assert!(parse_quote("SBIN", &b).is_none());
    }

    #[test]
    fn the_remembered_series_is_tried_first() {
        assert_eq!(series_order(None), vec!["EQ", "SM", "BE", "BZ", "ST"]);
        assert_eq!(series_order(Some("BE")), vec!["BE", "EQ", "SM", "BZ", "ST"]);
        assert_eq!(series_order(Some("XX")), vec!["EQ", "SM", "BE", "BZ", "ST"]);
    }
}
