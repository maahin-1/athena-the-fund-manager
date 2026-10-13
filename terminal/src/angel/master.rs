//! Angel One's instrument list. The stream names a stock by a number, so a symbol is looked up once and kept.
use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, SystemTime};

use serde_json::Value;

pub const MASTER_URL: &str = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json";
const MAX_AGE: Duration = Duration::from_secs(20 * 3600);
/// Cash-segment series in the order a plain symbol is tried.
pub const SUFFIXES: [&str; 5] = ["-EQ", "-BE", "-BZ", "-SM", "-ST"];

#[derive(Debug)]
pub struct MasterError(pub &'static str);

impl std::fmt::Display for MasterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "the instrument list could not be loaded: {}", self.0)
    }
}

impl std::error::Error for MasterError {}

pub struct Master {
    by_symbol: HashMap<String, String>,
    by_token: HashMap<String, String>,
}

impl Master {
    /// `rows` are `(full symbol such as SBIN-EQ, token)`.
    pub fn new(rows: Vec<(String, String)>) -> Self {
        let mut by_symbol = HashMap::new();
        let mut by_token = HashMap::new();
        for (symbol, token) in rows {
            if let Some(plain) = SUFFIXES.iter().find_map(|s| symbol.strip_suffix(s)) {
                // When one plain name has several series, the earlier series in SUFFIXES wins the reverse lookup too.
                by_token.entry(token.clone()).or_insert_with(|| plain.to_string());
            }
            by_symbol.insert(symbol, token);
        }
        Master { by_symbol, by_token }
    }

    pub fn token(&self, symbol: &str) -> Option<&str> {
        SUFFIXES.iter().find_map(|s| self.by_symbol.get(&format!("{symbol}{s}"))).map(String::as_str)
    }

    pub fn symbol(&self, token: &str) -> Option<&str> {
        self.by_token.get(token).map(String::as_str)
    }
}

/// Keep only NSE cash rows whose symbol ends in a wanted series; returns `(symbol, token)`.
pub fn filter_rows(rows: &Value) -> Vec<(String, String)> {
    rows.as_array()
        .map(|a| {
            a.iter()
                .filter(|r| r.get("exch_seg").and_then(Value::as_str) == Some("NSE"))
                .filter_map(|r| {
                    let symbol = r.get("symbol")?.as_str()?;
                    let token = r.get("token")?.as_str()?;
                    SUFFIXES.iter().any(|s| symbol.ends_with(s)).then(|| (symbol.to_string(), token.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn read_cache(cache: &Path, now: SystemTime) -> Option<Vec<(String, String)>> {
    let age = now.duration_since(std::fs::metadata(cache).ok()?.modified().ok()?).ok()?;
    if age >= MAX_AGE {
        return None;
    }
    let value: Value = serde_json::from_str(&std::fs::read_to_string(cache).ok()?).ok()?;
    let rows: Vec<(String, String)> = value
        .as_array()?
        .iter()
        .filter_map(|r| Some((r.get("symbol")?.as_str()?.to_string(), r.get("token")?.as_str()?.to_string())))
        .collect();
    (!rows.is_empty()).then_some(rows)
}

/// The instrument list from the cache file when it is under 20 hours old, otherwise downloaded (and cached).
pub async fn load_with<F, Fut>(cache: &Path, now: SystemTime, download: F) -> Result<Master, MasterError>
where
    F: FnOnce() -> Fut,
    Fut: std::future::Future<Output = Result<Value, MasterError>>,
{
    if let Some(rows) = read_cache(cache, now) {
        return Ok(Master::new(rows));
    }
    let rows = filter_rows(&download().await?);
    if rows.is_empty() {
        return Err(MasterError("no NSE rows in the download"));
    }
    let json: Vec<Value> = rows.iter().map(|(s, t)| serde_json::json!({"symbol": s, "token": t})).collect();
    if let Some(parent) = cache.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(cache, Value::Array(json).to_string()); // a failed cache write is only a slower next start
    Ok(Master::new(rows))
}

pub async fn load(client: &reqwest::Client, cache: &Path) -> Result<Master, MasterError> {
    load_with(cache, SystemTime::now(), || async {
        client
            .get(MASTER_URL)
            .timeout(Duration::from_secs(60))
            .send()
            .await
            .map_err(|_| MasterError("download failed"))?
            .json::<Value>()
            .await
            .map_err(|_| MasterError("unreadable download"))
    })
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn master() -> Master {
        Master::new(vec![
            ("SBIN-BE".into(), "9999".into()),
            ("SBIN-EQ".into(), "3045".into()),
            ("RVNL-EQ".into(), "9552".into()),
            ("AMBER-SM".into(), "777".into()),
        ])
    }

    #[test]
    fn the_equity_series_wins_over_later_ones() {
        assert_eq!(master().token("SBIN"), Some("3045"));
        assert_eq!(master().token("AMBER"), Some("777"));
        assert_eq!(master().token("NOPE"), None);
    }

    #[test]
    fn a_token_maps_back_to_the_plain_symbol() {
        assert_eq!(master().symbol("3045"), Some("SBIN"));
        assert_eq!(master().symbol("0"), None);
    }

    #[test]
    fn filter_keeps_only_nse_cash_rows() {
        let rows = json!([
            {"token": "1", "symbol": "SBIN-EQ", "exch_seg": "BSE"},
            {"token": "3045", "symbol": "SBIN-EQ", "exch_seg": "NSE"},
            {"token": "5", "symbol": "SBIN26OCTFUT", "exch_seg": "NSE"}
        ]);
        assert_eq!(filter_rows(&rows), vec![("SBIN-EQ".to_string(), "3045".to_string())]);
    }

    #[tokio::test]
    async fn a_fresh_cache_is_used_without_a_download() {
        let dir = std::env::temp_dir().join(format!("athena-master-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cache = dir.join("master.json");
        std::fs::write(&cache, json!([{"symbol": "SBIN-EQ", "token": "3045"}]).to_string()).unwrap();
        let m = load_with(&cache, SystemTime::now(), || async { panic!("no download expected") }).await.unwrap();
        assert_eq!(m.token("SBIN"), Some("3045"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn a_missing_cache_downloads_and_writes_one() {
        let dir = std::env::temp_dir().join(format!("athena-master-dl-{}", std::process::id()));
        let cache = dir.join("master.json");
        let rows = json!([{"token": "3045", "symbol": "SBIN-EQ", "exch_seg": "NSE"}]);
        let m = load_with(&cache, SystemTime::now(), || async { Ok(rows) }).await.unwrap();
        assert_eq!(m.token("SBIN"), Some("3045"));
        assert!(cache.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
