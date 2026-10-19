//! Angel One's instrument list. The stream names a stock or an index by a number, so a symbol is looked up once and kept.
use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, SystemTime};

use serde_json::Value;

pub const MASTER_URL: &str = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json";
const MAX_AGE: Duration = Duration::from_secs(20 * 3600);
/// Cash-segment series in the order a plain symbol is tried.
pub const SUFFIXES: [&str; 5] = ["-EQ", "-BE", "-BZ", "-SM", "-ST"];
/// An index row is kept in the cache as `INDEX:` and the list's own name for it.
const INDEX_TAG: &str = "INDEX:";

/// The names the page and the exchange's all-indices snapshot use for an index, from the list's abbreviated ones (`Nifty Fin Service` is the exchange's
/// `NIFTY FINANCIAL SERVICES`): upper case, a word at a time expanded, and a word that ends in a number split from it (`QUALTY30` is `QUALITY 30`) except the
/// index family's own (`NIFTY50`).
pub fn canonical_index_name(listed: &str) -> String {
    let expand = |w: &str| -> String {
        match w {
            "SMLCAP" => "SMALLCAP",
            "MIDSML" => "MIDSMALLCAP",
            "MID" => "MIDCAP",
            "PVT" => "PRIVATE",
            "INFRA" => "INFRASTRUCTURE",
            "SERV" | "SERVICE" => "SERVICES",
            "FIN" => "FINANCIAL",
            "EQL" => "EQUAL",
            "WGT" => "WEIGHT",
            "LIQ" => "LIQUID",
            "QUALTY" => "QUALITY",
            "DIV" => "DIVIDEND",
            "OPPS" => "OPPORTUNITIES",
            "LOWVOL" => "LOW VOLATILITY",
            other => other,
        }
        .to_string()
    };
    let mut words: Vec<String> = Vec::new();
    for raw in listed.split_whitespace() {
        let w = raw.to_ascii_uppercase();
        let digits = w.chars().rev().take_while(char::is_ascii_digit).count();
        let (letters, number) = w.split_at(w.len() - digits);
        if digits > 0 && !letters.is_empty() && !letters.starts_with("NIFTY") {
            words.push(expand(letters));
            words.push(number.to_string());
        } else {
            words.push(expand(&w));
        }
    }
    words.join(" ")
}

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
    /// An index by the name the page uses (`NIFTY 50`).
    indices: HashMap<String, String>,
}

impl Master {
    /// `rows` are `(full symbol such as SBIN-EQ, token)`.
    pub fn new(rows: Vec<(String, String)>) -> Self {
        let mut by_symbol = HashMap::new();
        let mut by_token = HashMap::new();
        let mut indices = HashMap::new();
        for (symbol, token) in rows {
            if let Some(listed) = symbol.strip_prefix(INDEX_TAG) {
                let name = canonical_index_name(listed);
                by_token.entry(token.clone()).or_insert_with(|| name.clone());
                indices.insert(name, token);
                continue;
            }
            if let Some(plain) = SUFFIXES.iter().find_map(|s| symbol.strip_suffix(s)) {
                // When one plain name has several series, the earlier series in SUFFIXES wins the reverse lookup too.
                by_token.entry(token.clone()).or_insert_with(|| plain.to_string());
            }
            by_symbol.insert(symbol, token);
        }
        Master { by_symbol, by_token, indices }
    }

    pub fn token(&self, symbol: &str) -> Option<&str> {
        SUFFIXES
            .iter()
            .find_map(|s| self.by_symbol.get(&format!("{symbol}{s}")))
            .or_else(|| self.indices.get(symbol))
            .map(String::as_str)
    }

    pub fn symbol(&self, token: &str) -> Option<&str> {
        self.by_token.get(token).map(String::as_str)
    }
}

/// Keep the NSE cash rows whose symbol ends in a wanted series, and the NSE index rows (tagged `INDEX:`); returns `(symbol, token)`.
pub fn filter_rows(rows: &Value) -> Vec<(String, String)> {
    rows.as_array()
        .map(|a| {
            a.iter()
                .filter(|r| r.get("exch_seg").and_then(Value::as_str) == Some("NSE"))
                .filter_map(|r| {
                    let symbol = r.get("symbol")?.as_str()?;
                    let token = r.get("token")?.as_str()?;
                    if r.get("instrumenttype").and_then(Value::as_str) == Some("AMXIDX") {
                        return Some((format!("{INDEX_TAG}{symbol}"), token.to_string()));
                    }
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
    // a cache written before indices were kept has none: it is out of date
    (!rows.is_empty() && rows.iter().any(|(s, _)| s.starts_with(INDEX_TAG))).then_some(rows)
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
    fn an_index_is_found_by_the_name_the_page_uses_and_its_token_maps_back_to_it() {
        let m = Master::new(vec![
            ("SBIN-EQ".into(), "3045".into()),
            ("INDEX:Nifty 50".into(), "99926000".into()),
            ("INDEX:Nifty Fin Service".into(), "99926037".into()),
            ("INDEX:India VIX".into(), "99926017".into()),
        ]);
        assert_eq!(m.token("NIFTY 50"), Some("99926000"));
        assert_eq!(m.token("NIFTY FINANCIAL SERVICES"), Some("99926037"));
        assert_eq!(m.token("INDIA VIX"), Some("99926017"));
        assert_eq!(m.symbol("99926000"), Some("NIFTY 50"));
        assert_eq!(m.symbol("99926037"), Some("NIFTY FINANCIAL SERVICES"));
        assert_eq!(m.token("NIFTY 51"), None);
        assert_eq!(m.token("SBIN"), Some("3045")); // stocks are as they were
    }

    #[test]
    fn the_lists_abbreviated_index_names_become_the_exchange_names() {
        let c = canonical_index_name;
        assert_eq!(c("Nifty 50"), "NIFTY 50");
        assert_eq!(c("Nifty Fin Service"), "NIFTY FINANCIAL SERVICES");
        assert_eq!(c("NIFTY MID SELECT"), "NIFTY MIDCAP SELECT");
        assert_eq!(c("NIFTY SMLCAP 250"), "NIFTY SMALLCAP 250");
        assert_eq!(c("NIFTY MIDSML 400"), "NIFTY MIDSMALLCAP 400");
        assert_eq!(c("Nifty Pvt Bank"), "NIFTY PRIVATE BANK");
        assert_eq!(c("Nifty Infra"), "NIFTY INFRASTRUCTURE");
        assert_eq!(c("Nifty Serv Sector"), "NIFTY SERVICES SECTOR");
        assert_eq!(c("NIFTY200 QUALTY30"), "NIFTY200 QUALITY 30");
        assert_eq!(c("NIFTY100 LowVol30"), "NIFTY100 LOW VOLATILITY 30");
        assert_eq!(c("NIFTY50 EQL Wgt"), "NIFTY50 EQUAL WEIGHT");
        assert_eq!(c("NIFTY100 Liq 15"), "NIFTY100 LIQUID 15");
        assert_eq!(c("Nifty Div Opps 50"), "NIFTY DIVIDEND OPPORTUNITIES 50");
        assert_eq!(c("India VIX"), "INDIA VIX");
        assert_eq!(c("Nifty50 TR 2x Lev"), "NIFTY50 TR 2X LEV"); // no expansion known: kept, and not matched to anything
    }

    #[test]
    fn filter_keeps_nse_cash_rows_and_nse_index_rows_only() {
        let rows = json!([
            {"token": "1", "symbol": "SBIN-EQ", "exch_seg": "BSE"},
            {"token": "3045", "symbol": "SBIN-EQ", "exch_seg": "NSE"},
            {"token": "5", "symbol": "SBIN26OCTFUT", "exch_seg": "NSE"},
            {"token": "99926000", "symbol": "Nifty 50", "exch_seg": "NSE", "instrumenttype": "AMXIDX"},
            {"token": "7", "symbol": "Nifty 50", "exch_seg": "BSE", "instrumenttype": "AMXIDX"}
        ]);
        assert_eq!(filter_rows(&rows), vec![("SBIN-EQ".to_string(), "3045".to_string()), ("INDEX:Nifty 50".to_string(), "99926000".to_string())]);
    }

    #[tokio::test]
    async fn a_fresh_cache_is_used_without_a_download() {
        let dir = std::env::temp_dir().join(format!("athena-master-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cache = dir.join("master.json");
        std::fs::write(&cache, json!([{"symbol": "SBIN-EQ", "token": "3045"}, {"symbol": "INDEX:Nifty 50", "token": "99926000"}]).to_string()).unwrap();
        let m = load_with(&cache, SystemTime::now(), || async { panic!("no download expected") }).await.unwrap();
        assert_eq!(m.token("SBIN"), Some("3045"));
        assert_eq!(m.token("NIFTY 50"), Some("99926000"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn a_cache_with_no_indices_is_out_of_date_and_downloaded_again() {
        let dir = std::env::temp_dir().join(format!("athena-master-old-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cache = dir.join("master.json");
        std::fs::write(&cache, json!([{"symbol": "SBIN-EQ", "token": "3045"}]).to_string()).unwrap();
        let rows = json!([{"token": "3045", "symbol": "SBIN-EQ", "exch_seg": "NSE"}, {"token": "99926000", "symbol": "Nifty 50", "exch_seg": "NSE", "instrumenttype": "AMXIDX"}]);
        let m = load_with(&cache, SystemTime::now(), || async { Ok(rows) }).await.unwrap();
        assert_eq!(m.token("NIFTY 50"), Some("99926000"));
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
