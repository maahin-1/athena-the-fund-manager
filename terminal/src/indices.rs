//! Live index values: the exchange's own all-indices snapshot (about 140 indices in one reply), refreshed while the terminal runs.
//! A failed refresh keeps the last snapshot and the page is told how old it is; nothing is guessed.
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use chrono::Utc;
use serde::Serialize;
use serde_json::Value;
use tokio_util::sync::CancellationToken;

use crate::poll::{market_open, number};

pub const URL: &str = "https://www.nseindia.com/api/allIndices";
const GAP_OPEN: Duration = Duration::from_secs(5);
const GAP_CLOSED: Duration = Duration::from_secs(300);

/// One index. Field names are short because the page polls this often.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct IndexRow {
    pub s: String,
    pub l: f64,
    pub pc: Option<f64>,
    pub ch: Option<f64>,
    pub pct: Option<f64>,
    pub o: Option<f64>,
    pub h: Option<f64>,
    pub lo: Option<f64>,
    pub h52: Option<f64>,
    pub l52: Option<f64>,
    pub pe: Option<f64>,
    pub pb: Option<f64>,
    pub dy: Option<f64>,
    pub w1: Option<f64>,
    pub m1: Option<f64>,
    pub y1: Option<f64>,
    pub adv: Option<f64>,
    pub dec: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Snapshot {
    /// The exchange's own timestamp text, for example `16-Oct-2026 15:30`.
    pub as_of: String,
    pub rows: Vec<IndexRow>,
}

/// Read the exchange's reply. A row without a name or a last value is left out; a reply without `data` is not a snapshot.
pub fn parse(body: &Value) -> Option<Snapshot> {
    let data = body.get("data")?.as_array()?;
    let rows: Vec<IndexRow> = data
        .iter()
        .filter_map(|r| {
            let s = r.get("index")?.as_str()?.trim().to_string();
            let l = number(r.get("last"))?;
            if s.is_empty() {
                return None;
            }
            Some(IndexRow {
                s,
                l,
                pc: number(r.get("previousClose")),
                ch: number(r.get("variation")),
                pct: number(r.get("percentChange")),
                o: number(r.get("open")),
                h: number(r.get("high")),
                lo: number(r.get("low")),
                h52: number(r.get("yearHigh")),
                l52: number(r.get("yearLow")),
                pe: number(r.get("pe")),
                pb: number(r.get("pb")),
                dy: number(r.get("dy")),
                w1: number(r.get("oneWeekAgoVal")),
                m1: number(r.get("oneMonthAgoVal")),
                y1: number(r.get("oneYearAgoVal")),
                adv: number(r.get("advances")),
                dec: number(r.get("declines")),
            })
        })
        .collect();
    if rows.is_empty() {
        return None;
    }
    let as_of = body.get("timestamp").and_then(Value::as_str).unwrap_or("").to_string();
    Some(Snapshot { as_of, rows })
}

#[derive(Clone, Default)]
pub struct IndexStore(Arc<Mutex<Option<(Snapshot, Instant)>>>);

impl IndexStore {
    pub fn new() -> Self {
        IndexStore::default()
    }

    pub fn set(&self, snapshot: Snapshot) {
        *self.0.lock().unwrap() = Some((snapshot, Instant::now()));
    }

    /// The route's body, or `None` before the first snapshot. `stale_s` is how long ago the snapshot was fetched.
    pub fn json(&self) -> Option<Value> {
        let g = self.0.lock().unwrap();
        let (snap, at) = g.as_ref()?;
        Some(serde_json::json!({
            "as_of": snap.as_of,
            "open": market_open(Utc::now()),
            "stale_s": at.elapsed().as_secs(),
            "rows": snap.rows,
        }))
    }
}

async fn fetch(client: &reqwest::Client) -> Option<Snapshot> {
    let body: Value = client
        .get(URL)
        .header("User-Agent", "Mozilla/5.0")
        .header("Accept", "application/json")
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    parse(&body)
}

/// Ask the exchange now, then again every few seconds while trading and every few minutes when shut.
pub async fn run_refresher(store: IndexStore, client: reqwest::Client, shutdown: CancellationToken) {
    loop {
        if let Some(snapshot) = fetch(&client).await {
            store.set(snapshot);
        }
        let gap = if market_open(Utc::now()) { GAP_OPEN } else { GAP_CLOSED };
        tokio::select! {
            _ = shutdown.cancelled() => return,
            _ = tokio::time::sleep(gap) => {}
        }
    }
}

/// `--fake-feed`: three drifting indices, so the page and its tests need no network.
pub async fn run_fake(store: IndexStore, shutdown: CancellationToken) {
    let base = [("NIFTY 50", 23000.0), ("NIFTY BANK", 52000.0), ("INDIA VIX", 14.0), ("NIFTY IT", 38000.0)];
    let mut tick = 0u64;
    loop {
        let rows = base
            .iter()
            .enumerate()
            .map(|(i, (name, prev))| {
                let wobble = (((tick + i as u64 * 7) % 40) as f64 - 20.0) / 2000.0;
                let last = ((prev * (1.0 + wobble)) * 100.0).round() / 100.0;
                IndexRow {
                    s: name.to_string(),
                    l: last,
                    pc: Some(*prev),
                    ch: Some(((last - prev) * 100.0).round() / 100.0),
                    pct: Some((((last - prev) / prev) * 10000.0).round() / 100.0),
                    o: Some(*prev),
                    h: Some(last.max(*prev)),
                    lo: Some(last.min(*prev)),
                    h52: Some(prev * 1.2),
                    l52: Some(prev * 0.8),
                    pe: Some(20.0),
                    pb: Some(3.0),
                    dy: Some(1.2),
                    w1: Some(prev * 0.99),
                    m1: Some(prev * 0.98),
                    y1: Some(prev * 0.9),
                    adv: Some(26.0),
                    dec: Some(24.0),
                }
            })
            .collect();
        store.set(Snapshot { as_of: "fake".into(), rows });
        tick += 1;
        tokio::select! {
            _ = shutdown.cancelled() => return,
            _ = tokio::time::sleep(Duration::from_millis(500)) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn body() -> Value {
        json!({"timestamp": "16-Oct-2026 15:30", "data": [
            {"index": "NIFTY 50", "last": 23146.15, "variation": 148.8, "percentChange": 0.65, "open": 23065.1, "high": 23149.85, "low": 22913.8,
             "previousClose": 22997.35, "yearHigh": 26373.2, "yearLow": 22179.9, "pe": "19.77", "pb": "2.84", "dy": "1.49", "advances": "26", "declines": "24",
             "oneWeekAgoVal": 22520.45, "oneMonthAgoVal": 23270.6, "oneYearAgoVal": 25709.85},
            {"index": "INDIA VIX", "last": 13.4, "pe": "-", "variation": -0.2},
            {"index": "", "last": 1.0},
            {"index": "NO LAST"}
        ]})
    }

    #[test]
    fn rows_are_read_with_text_numbers_and_dashes_handled() {
        let snap = parse(&body()).unwrap();
        assert_eq!(snap.as_of, "16-Oct-2026 15:30");
        assert_eq!(snap.rows.len(), 2); // the unnamed row and the row without a last value are left out
        let n = &snap.rows[0];
        assert_eq!((n.s.as_str(), n.l, n.pc, n.pe, n.adv, n.y1), ("NIFTY 50", 23146.15, Some(22997.35), Some(19.77), Some(26.0), Some(25709.85)));
        let v = &snap.rows[1];
        assert_eq!((v.s.as_str(), v.pe, v.ch, v.o), ("INDIA VIX", None, Some(-0.2), None)); // a dash is no number, not zero
    }

    #[test]
    fn a_reply_without_rows_is_not_a_snapshot() {
        assert!(parse(&json!({})).is_none());
        assert!(parse(&json!({"data": []})).is_none());
        assert!(parse(&json!({"data": [{"index": "X"}]})).is_none());
    }

    #[test]
    fn the_store_is_empty_until_set_and_then_says_how_old_it_is() {
        let store = IndexStore::new();
        assert!(store.json().is_none());
        store.set(parse(&body()).unwrap());
        let v = store.json().unwrap();
        assert_eq!(v["as_of"], "16-Oct-2026 15:30");
        assert_eq!(v["rows"][0]["s"], "NIFTY 50");
        assert!(v["stale_s"].as_u64().unwrap() < 5);
        assert!(v["open"].is_boolean());
    }
}
