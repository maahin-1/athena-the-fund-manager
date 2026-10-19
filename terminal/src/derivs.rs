//! Futures and options contracts from Angel One's public instrument list (NFO, BFO and MCX): the underlyings that have contracts, their expiries, the strikes of an
//! expiry with the call and put of each, and the futures. No prices here; the list says what exists, not what it trades at.
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};
use std::time::{Duration, SystemTime};

use chrono::{Datelike, NaiveDate};
use serde_json::{json, Value};

pub const MASTER_URL: &str = crate::angel::master::MASTER_URL;
pub const SEGMENTS: [&str; 3] = ["NFO", "BFO", "MCX"];
/// The most strikes one chain response carries.
pub const MAX_STRIKES: usize = 600;
const MAX_AGE: Duration = Duration::from_secs(20 * 3600);

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Kind {
    Future,
    Call,
    Put,
}

#[derive(Clone, Debug)]
pub struct Contract {
    pub token: String,
    pub symbol: String,
    pub underlying: String,
    pub segment: String,
    /// `index`, `stock` or `commodity`.
    pub class: &'static str,
    pub kind: Kind,
    pub expiry: NaiveDate,
    /// In rupees (the list gives hundredths); none for a future.
    pub strike: Option<f64>,
    pub lot: u32,
    pub tick: f64,
    /// Test and odd names the page hides by default (`051NSETEST`, `NIFTYFPI`).
    pub odd: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub enum DerivsError {
    UnknownUnderlying,
    UnknownExpiry,
    BadDate,
    TooManyStrikes,
}

#[derive(Default)]
pub struct Derivs {
    contracts: Vec<Contract>,
}

fn class_of(kind: &str) -> Option<&'static str> {
    match kind {
        "FUTIDX" | "OPTIDX" => Some("index"),
        "FUTSTK" | "OPTSTK" => Some("stock"),
        "FUTCOM" | "OPTFUT" => Some("commodity"),
        _ => None,
    }
}

fn number(row: &Value, key: &str) -> Option<f64> {
    row.get(key)?.as_str()?.trim().parse().ok().or_else(|| row.get(key)?.as_f64())
}

fn is_odd(name: &str) -> bool {
    name.chars().next().is_some_and(|c| c.is_ascii_digit()) || name.contains("TEST") || name.ends_with("FPI")
}

/// One list row as a contract, or none for a row that is not a future or option of NFO, BFO or MCX, has a date that is not one, or a kind it cannot tell.
fn parse_row(row: &Value) -> Option<Contract> {
    let segment = row.get("exch_seg")?.as_str()?;
    if !SEGMENTS.contains(&segment) {
        return None;
    }
    let instrument = row.get("instrumenttype")?.as_str()?;
    let class = class_of(instrument)?;
    let symbol = row.get("symbol")?.as_str()?;
    let kind = if instrument.starts_with("FUT") {
        Kind::Future
    } else if symbol.ends_with("CE") {
        Kind::Call
    } else if symbol.ends_with("PE") {
        Kind::Put
    } else {
        return None;
    };
    let name = row.get("name")?.as_str()?.trim().to_string();
    let expiry = NaiveDate::parse_from_str(row.get("expiry")?.as_str()?.trim(), "%d%b%Y").ok()?;
    let strike = if kind == Kind::Future { None } else { Some(number(row, "strike")? / 100.0) };
    Some(Contract {
        token: row.get("token")?.as_str()?.to_string(),
        symbol: symbol.to_string(),
        segment: segment.to_string(),
        class,
        kind,
        expiry,
        strike,
        lot: number(row, "lotsize")?.max(0.0) as u32,
        tick: number(row, "tick_size").map(|t| t / 100.0).unwrap_or(0.0),
        odd: is_odd(&name),
        underlying: name,
    })
}

impl Derivs {
    /// Build from the list's rows, dropping contracts that expired before `today`.
    pub fn from_rows(rows: &Value, today: NaiveDate) -> Derivs {
        let mut contracts: Vec<Contract> = rows.as_array().map(|a| a.iter().filter_map(parse_row).filter(|c| c.expiry >= today).collect()).unwrap_or_default();
        contracts.sort_by(|a, b| (&a.underlying, a.expiry, a.kind, a.strike.map(|s| (s * 100.0) as i64), &a.segment).cmp(&(&b.underlying, b.expiry, b.kind, b.strike.map(|s| (s * 100.0) as i64), &b.segment)));
        Derivs { contracts }
    }

    pub fn len(&self) -> usize {
        self.contracts.len()
    }

    /// The compact form kept on disk: `[token, symbol, name, expiry, strike, lot, instrumenttype, segment, tick]`, the list's own spelling, so reading it back goes
    /// through the same parser as a download.
    pub fn compact_rows(rows: &Value, today: NaiveDate) -> Value {
        let kept: Vec<Value> = rows
            .as_array()
            .map(|a| a.iter().filter(|r| parse_row(r).is_some_and(|c| c.expiry >= today)).cloned().collect())
            .unwrap_or_default();
        Value::Array(kept)
    }

    /// The underlyings that have contracts in `segment` (any when it is `ALL`), by name.
    pub fn underlyings(&self, segment: &str) -> Value {
        let mut by_name: BTreeMap<(String, String), Vec<&Contract>> = BTreeMap::new();
        for c in &self.contracts {
            if segment == "ALL" || c.segment == segment {
                by_name.entry((c.underlying.clone(), c.segment.clone())).or_default().push(c);
            }
        }
        let list: Vec<Value> = by_name
            .into_iter()
            .map(|((name, seg), cs)| {
                let mut expiries: Vec<NaiveDate> = cs.iter().map(|c| c.expiry).collect();
                expiries.sort();
                expiries.dedup();
                let lot = cs.iter().filter(|c| c.kind == Kind::Future).min_by_key(|c| c.expiry).or_else(|| cs.iter().min_by_key(|c| c.expiry)).map(|c| c.lot).unwrap_or(0);
                json!({ "underlying": name, "segment": seg, "class": cs[0].class, "odd": cs[0].odd, "expiries": expiries.len(), "lot": lot, "futures": cs.iter().any(|c| c.kind == Kind::Future), "options": cs.iter().any(|c| c.kind != Kind::Future) })
            })
            .collect();
        json!({ "underlyings": list })
    }

    fn of(&self, underlying: &str) -> Vec<&Contract> {
        self.contracts.iter().filter(|c| c.underlying == underlying).collect()
    }

    /// The dated expiries of an underlying in order, with which have futures, options or both and whether each is the last of its month (a monthly) or not (a weekly).
    pub fn expiries(&self, underlying: &str) -> Result<Value, DerivsError> {
        let cs = self.of(underlying);
        if cs.is_empty() {
            return Err(DerivsError::UnknownUnderlying);
        }
        let mut by_date: BTreeMap<NaiveDate, (bool, bool)> = BTreeMap::new();
        for c in &cs {
            let e = by_date.entry(c.expiry).or_default();
            if c.kind == Kind::Future {
                e.0 = true;
            } else {
                e.1 = true;
            }
        }
        let dates: Vec<NaiveDate> = by_date.keys().copied().collect();
        let list: Vec<Value> = dates
            .iter()
            .map(|d| {
                let (futures, options) = by_date[d];
                let monthly = !dates.iter().any(|o| o.year() == d.year() && o.month() == d.month() && o > d);
                json!({ "date": d.to_string(), "futures": futures, "options": options, "series": if monthly { "monthly" } else { "weekly" } })
            })
            .collect();
        Ok(json!({ "underlying": underlying, "expiries": list }))
    }

    /// The strikes of one expiry with the call and put of each, and the future of that expiry when there is one.
    pub fn chain(&self, underlying: &str, expiry: &str) -> Result<Value, DerivsError> {
        let date = NaiveDate::parse_from_str(expiry, "%Y-%m-%d").map_err(|_| DerivsError::BadDate)?;
        let all = self.of(underlying);
        if all.is_empty() {
            return Err(DerivsError::UnknownUnderlying);
        }
        let cs: Vec<&Contract> = all.into_iter().filter(|c| c.expiry == date).collect();
        if cs.is_empty() {
            return Err(DerivsError::UnknownExpiry);
        }
        let leg = |c: &Contract| json!({ "token": c.token, "symbol": c.symbol, "lot": c.lot });
        let mut strikes: BTreeMap<i64, (Option<&Contract>, Option<&Contract>)> = BTreeMap::new();
        for c in cs.iter().filter(|c| c.kind != Kind::Future) {
            let at = (c.strike.unwrap_or(0.0) * 100.0).round() as i64;
            let e = strikes.entry(at).or_default();
            if c.kind == Kind::Call {
                e.0 = Some(c);
            } else {
                e.1 = Some(c);
            }
        }
        if strikes.len() > MAX_STRIKES {
            return Err(DerivsError::TooManyStrikes);
        }
        let rows: Vec<Value> = strikes
            .into_iter()
            .map(|(at, (call, put))| json!({ "strike": at as f64 / 100.0, "call": call.map(leg), "put": put.map(leg) }))
            .collect();
        let future = cs.iter().find(|c| c.kind == Kind::Future).map(|c| leg(c));
        let first = cs.iter().find(|c| c.kind != Kind::Future).or_else(|| cs.first()).copied();
        Ok(json!({
            "underlying": underlying, "expiry": date.to_string(), "segment": first.map(|c| c.segment.clone()), "class": first.map(|c| c.class),
            "lot": first.map(|c| c.lot), "tick": first.map(|c| c.tick), "future": future, "strikes": rows,
        }))
    }

    /// The futures of an underlying by expiry.
    pub fn futures(&self, underlying: &str) -> Result<Value, DerivsError> {
        let cs = self.of(underlying);
        if cs.is_empty() {
            return Err(DerivsError::UnknownUnderlying);
        }
        let list: Vec<Value> = cs
            .iter()
            .filter(|c| c.kind == Kind::Future)
            .map(|c| json!({ "expiry": c.expiry.to_string(), "token": c.token, "symbol": c.symbol, "lot": c.lot, "tick": c.tick, "segment": c.segment }))
            .collect();
        Ok(json!({ "underlying": underlying, "futures": list }))
    }

    /// A contract by its token, for the live feed to name what it is streaming (used when derivative tokens are subscribed, which awaits a market session).
    #[allow(dead_code)]
    pub fn by_token(&self, token: &str) -> Option<&Contract> {
        self.contracts.iter().find(|c| c.token == token)
    }
}

fn read_cache(cache: &Path, now: SystemTime) -> Option<Value> {
    let age = now.duration_since(std::fs::metadata(cache).ok()?.modified().ok()?).ok()?;
    if age >= MAX_AGE {
        return None;
    }
    let value: Value = serde_json::from_str(&std::fs::read_to_string(cache).ok()?).ok()?;
    value.as_array().is_some_and(|a| !a.is_empty()).then_some(value)
}

#[derive(Debug)]
pub struct LoadError(pub &'static str);

/// The contract table from the cache file when it is under 20 hours old, else from `download` (and cached, as the compact rows). A download with no contracts is an
/// error and leaves an older cache alone, so one bad day does not erase the table.
pub async fn load_with<F, Fut>(cache: &Path, now: SystemTime, today: NaiveDate, download: Option<F>) -> Result<Derivs, LoadError>
where
    F: FnOnce() -> Fut,
    Fut: std::future::Future<Output = Result<Value, LoadError>>,
{
    if let Some(rows) = read_cache(cache, now) {
        return Ok(Derivs::from_rows(&rows, today));
    }
    let stale = std::fs::read_to_string(cache).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok());
    if let Some(download) = download {
        match download().await {
            Ok(rows) => {
                let table = Derivs::from_rows(&rows, today);
                if table.len() > 0 {
                    if let Some(parent) = cache.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    let _ = std::fs::write(cache, Derivs::compact_rows(&rows, today).to_string()); // a failed write only means a slower next start
                    return Ok(table);
                }
            }
            Err(_) => {}
        }
    }
    // no fresh cache and no good download: an older cache is better than nothing
    match stale {
        Some(rows) => {
            let table = Derivs::from_rows(&rows, today);
            if table.len() > 0 {
                return Ok(table);
            }
            Err(LoadError("the instrument list has no futures or options"))
        }
        None => Err(LoadError("the instrument list could not be loaded")),
    }
}

pub async fn load(client: &reqwest::Client, cache: &Path, today: NaiveDate, offline: bool) -> Result<Derivs, LoadError> {
    let download = (!offline).then(|| {
        || async {
            client
                .get(MASTER_URL)
                .timeout(Duration::from_secs(90))
                .send()
                .await
                .map_err(|_| LoadError("download failed"))?
                .json::<Value>()
                .await
                .map_err(|_| LoadError("unreadable download"))
        }
    });
    load_with(cache, SystemTime::now(), today, download).await
}

/// Where the contract table is: still loading, ready, or the reason it could not be loaded. The routes answer 503 with that reason until it is ready.
#[derive(Clone)]
pub struct DerivsStore {
    inner: Arc<RwLock<StoreState>>,
}

enum StoreState {
    Loading,
    Ready(Derivs),
    Failed(String),
}

#[derive(Debug, PartialEq, Eq)]
pub enum NotReady {
    Loading,
    Failed(String),
}

impl DerivsStore {
    pub fn new() -> Self {
        DerivsStore { inner: Arc::new(RwLock::new(StoreState::Loading)) }
    }

    pub fn set_ready(&self, table: Derivs) {
        if let Ok(mut s) = self.inner.write() {
            *s = StoreState::Ready(table);
        }
    }

    /// Record a failed load, unless a table is already there (an old table is better than a message).
    pub fn set_failed(&self, reason: &str) {
        if let Ok(mut s) = self.inner.write() {
            if !matches!(*s, StoreState::Ready(_)) {
                *s = StoreState::Failed(reason.to_string());
            }
        }
    }

    pub fn with<R>(&self, f: impl FnOnce(&Derivs) -> R) -> Result<R, NotReady> {
        match &*self.inner.read().map_err(|_| NotReady::Failed("the contract list is unavailable".into()))? {
            StoreState::Ready(t) => Ok(f(t)),
            StoreState::Loading => Err(NotReady::Loading),
            StoreState::Failed(m) => Err(NotReady::Failed(m.clone())),
        }
    }
}

/// The date in India now, which is the date a contract's expiry is compared with.
pub fn today_ist() -> NaiveDate {
    ist_date(chrono::Utc::now())
}

/// The date in India at a moment: between midnight and 05:30 IST the UTC date is still yesterday's.
pub fn ist_date(now: chrono::DateTime<chrono::Utc>) -> NaiveDate {
    (now + chrono::Duration::seconds(19_800)).date_naive()
}

/// Load the table at start and again every twelve hours, so a new day's expiries appear without a restart. A failed refresh keeps the table that is there.
pub async fn run_loader(store: DerivsStore, client: reqwest::Client, cache: PathBuf, offline: bool, shutdown: tokio_util::sync::CancellationToken) {
    loop {
        match load(&client, &cache, today_ist(), offline).await {
            Ok(table) => store.set_ready(table),
            Err(e) => store.set_failed(e.0),
        }
        tokio::select! { _ = shutdown.cancelled() => return, _ = tokio::time::sleep(Duration::from_secs(12 * 3600)) => {} }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_india_date_is_ahead_of_utc_between_18_30_and_midnight_utc() {
        let at = |h, m| chrono::NaiveDate::from_ymd_opt(2026, 10, 18).unwrap().and_hms_opt(h, m, 0).unwrap().and_utc();
        assert_eq!(ist_date(at(18, 29)), d(2026, 10, 18)); // 23:59 IST
        assert_eq!(ist_date(at(18, 30)), d(2026, 10, 19)); // midnight IST
        assert_eq!(ist_date(at(23, 0)), d(2026, 10, 19)); // 04:30 IST
    }

    fn d(y: i32, m: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, day).unwrap()
    }

    /// Rows in the list's own shape (strike in hundredths as text, lot size and tick as text).
    fn row(token: &str, symbol: &str, name: &str, expiry: &str, strike: &str, lot: &str, kind: &str, seg: &str, tick: &str) -> Value {
        json!({"token": token, "symbol": symbol, "name": name, "expiry": expiry, "strike": strike, "lotsize": lot, "instrumenttype": kind, "exch_seg": seg, "tick_size": tick})
    }

    fn rows() -> Value {
        json!([
            row("1", "NIFTY27OCT26FUT", "NIFTY", "27OCT2026", "-1.000000", "65", "FUTIDX", "NFO", "5.000000"),
            row("2", "NIFTY24NOV26FUT", "NIFTY", "24NOV2026", "-1.000000", "65", "FUTIDX", "NFO", "5.000000"),
            row("3", "NIFTY03NOV2623000PE", "NIFTY", "03NOV2026", "2300000.000000", "65", "OPTIDX", "NFO", "5.000000"),
            row("4", "NIFTY03NOV2623000CE", "NIFTY", "03NOV2026", "2300000.000000", "65", "OPTIDX", "NFO", "5.000000"),
            row("5", "NIFTY03NOV2623100CE", "NIFTY", "03NOV2026", "2310000.000000", "65", "OPTIDX", "NFO", "5.000000"),
            row("6", "NIFTY24NOV2623000CE", "NIFTY", "24NOV2026", "2300000.000000", "65", "OPTIDX", "NFO", "5.000000"),
            row("7", "NIFTY29DEC2623000PE", "NIFTY", "29DEC2026", "2300000.000000", "65", "OPTIDX", "NFO", "5.000000"),
            row("8", "ASHOKLEY27OCT26FUT", "ASHOKLEY", "27OCT2026", "-1.000000", "5000", "FUTSTK", "NFO", "1.000000"),
            row("9", "CROMPTON27OCT26177.5CE", "CROMPTON", "27OCT2026", "17750.000000", "2150", "OPTSTK", "NFO", "1.000000"),
            row("10", "GOLD30OCT26164000CE", "GOLD", "30OCT2026", "16400000.000000", "1", "OPTFUT", "MCX", "50.000000"),
            row("11", "GOLD05DEC26FUT", "GOLD", "05DEC2026", "0.000000", "1", "FUTCOM", "MCX", "100.000000"),
            row("12", "BANKEX26NOV68100PE", "BANKEX", "26NOV2026", "6810000.000000", "30", "OPTIDX", "BFO", "5.000000"),
            row("13", "051NSETEST27NOV36FUT", "051NSETEST", "27NOV2036", "-1.000000", "50", "FUTSTK", "NFO", "5.000000"),
            row("14", "NIFTYFPI29DEC26FUT", "NIFTYFPI", "29DEC2026", "-1.000000", "1100", "FUTIDX", "NFO", "5.000000"),
            row("15", "OLD25SEP26FUT", "OLD", "25SEP2026", "-1.000000", "10", "FUTSTK", "NFO", "5.000000"),
            row("16", "SBIN-EQ", "SBIN", "", "-1.000000", "1", "", "NSE", "5.000000"),
            row("17", "SILVER04DEC26FUT", "SILVER", "04DEC2026", "0.000000", "30", "FUTCOM", "MCX", "100.000000"),
            row("18", "GOLD", "GOLD", "", "0.000000", "1", "COMDTY", "MCX", "0.000000"),
            row("19", "USDINR27OCT26FUT", "USDINR", "27OCT2026", "-1.000000", "1000", "FUTCUR", "CDS", "25.000000")
        ])
    }

    fn table() -> Derivs {
        Derivs::from_rows(&rows(), d(2026, 10, 19))
    }

    #[test]
    fn keeps_futures_and_options_of_the_three_segments_and_nothing_else() {
        let t = table();
        assert_eq!(t.len(), 19 - 4); // the expired one, the cash row, the commodity spot row and the currency future are not contracts
        assert!(t.by_token("16").is_none());
        assert!(t.by_token("18").is_none());
        assert!(t.by_token("19").is_none());
        assert!(t.by_token("15").is_none());
    }

    #[test]
    fn reads_strike_in_rupees_kind_expiry_lot_and_tick_from_the_lists_spelling() {
        let t = table();
        let c = t.by_token("3").unwrap();
        assert_eq!((c.kind, c.strike, c.expiry, c.lot, c.segment.as_str(), c.class), (Kind::Put, Some(23000.0), d(2026, 11, 3), 65, "NFO", "index"));
        assert_eq!(t.by_token("4").unwrap().kind, Kind::Call);
        assert_eq!(t.by_token("9").unwrap().strike, Some(177.5)); // a decimal strike
        assert_eq!(t.by_token("9").unwrap().class, "stock");
        assert_eq!(t.by_token("10").unwrap().strike, Some(164000.0)); // MCX
        assert_eq!(t.by_token("10").unwrap().class, "commodity");
        assert_eq!(t.by_token("12").unwrap().segment, "BFO");
        let f = t.by_token("1").unwrap();
        assert_eq!((f.kind, f.strike, f.lot), (Kind::Future, None, 65));
        assert!((f.tick - 0.05).abs() < 1e-9);
    }

    #[test]
    fn a_contract_that_expires_today_is_kept_and_one_that_expired_yesterday_is_not() {
        let rows = json!([
            row("1", "A27OCT26FUT", "A", "27OCT2026", "-1.000000", "1", "FUTSTK", "NFO", "5.000000"),
            row("2", "B26OCT26FUT", "B", "26OCT2026", "-1.000000", "1", "FUTSTK", "NFO", "5.000000")
        ]);
        let t = Derivs::from_rows(&rows, d(2026, 10, 27));
        assert!(t.by_token("1").is_some());
        assert!(Derivs::from_rows(&rows, d(2026, 10, 28)).by_token("1").is_none());
        assert!(t.by_token("2").is_none());
    }

    #[test]
    fn flags_test_and_odd_names_so_the_page_can_hide_them() {
        let t = table();
        assert!(t.by_token("13").unwrap().odd);
        assert!(t.by_token("14").unwrap().odd);
        assert!(!t.by_token("1").unwrap().odd);
        let u = t.underlyings("NFO");
        let by = |n: &str| u["underlyings"].as_array().unwrap().iter().find(|x| x["underlying"] == n).cloned().unwrap();
        assert_eq!(by("051NSETEST")["odd"], true);
        assert_eq!(by("NIFTY")["odd"], false);
    }

    #[test]
    fn lists_the_underlyings_of_a_segment_with_their_class_lot_and_expiry_count() {
        let t = table();
        let nfo = t.underlyings("NFO");
        let names: Vec<&str> = nfo["underlyings"].as_array().unwrap().iter().map(|x| x["underlying"].as_str().unwrap()).collect();
        assert_eq!(names, vec!["051NSETEST", "ASHOKLEY", "CROMPTON", "NIFTY", "NIFTYFPI"]);
        let nifty = nfo["underlyings"].as_array().unwrap().iter().find(|x| x["underlying"] == "NIFTY").unwrap();
        assert_eq!(nifty["class"], "index");
        assert_eq!(nifty["lot"], 65);
        assert_eq!(nifty["expiries"], 4); // 27 Oct, 3 Nov, 24 Nov, 29 Dec
        assert_eq!(nifty["futures"], true);
        assert_eq!(nifty["options"], true);
        let mcx = t.underlyings("MCX");
        let names: Vec<&str> = mcx["underlyings"].as_array().unwrap().iter().map(|x| x["underlying"].as_str().unwrap()).collect();
        assert_eq!(names, vec!["GOLD", "SILVER"]);
        assert_eq!(t.underlyings("BFO")["underlyings"].as_array().unwrap().len(), 1);
        assert_eq!(t.underlyings("ALL")["underlyings"].as_array().unwrap().len(), 5 + 2 + 1);
        assert_eq!(t.underlyings("NOPE")["underlyings"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn lists_expiries_in_order_with_what_trades_and_weekly_or_monthly() {
        let e = table().expiries("NIFTY").unwrap();
        let list = e["expiries"].as_array().unwrap();
        let dates: Vec<&str> = list.iter().map(|x| x["date"].as_str().unwrap()).collect();
        assert_eq!(dates, vec!["2026-10-27", "2026-11-03", "2026-11-24", "2026-12-29"]);
        assert_eq!((list[0]["futures"].clone(), list[0]["options"].clone()), (json!(true), json!(false)));
        assert_eq!((list[1]["futures"].clone(), list[1]["options"].clone()), (json!(false), json!(true)));
        assert_eq!(list[1]["series"], "weekly"); // 3 Nov is not the last expiry of November
        assert_eq!(list[2]["series"], "monthly");
        assert_eq!(list[3]["series"], "monthly");
        assert_eq!(table().expiries("NOPE"), Err(DerivsError::UnknownUnderlying));
    }

    #[test]
    fn builds_a_chain_with_strikes_in_order_the_call_and_put_of_each_and_the_future() {
        let c = table().chain("NIFTY", "2026-11-03").unwrap();
        let strikes = c["strikes"].as_array().unwrap();
        assert_eq!(strikes.iter().map(|s| s["strike"].as_f64().unwrap()).collect::<Vec<_>>(), vec![23000.0, 23100.0]);
        assert_eq!(strikes[0]["call"]["token"], "4");
        assert_eq!(strikes[0]["put"]["token"], "3");
        assert_eq!(strikes[1]["call"]["token"], "5");
        assert!(strikes[1]["put"].is_null()); // a strike with only a call
        assert_eq!(c["lot"], 65);
        assert_eq!(c["segment"], "NFO");
        assert!(c["future"].is_null()); // no future expires on 3 Nov
        let m = table().chain("NIFTY", "2026-11-24").unwrap();
        assert_eq!(m["future"]["token"], "2");
        assert_eq!(m["strikes"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn a_chain_of_a_commodity_and_of_a_stock_carry_their_own_lots_and_ticks() {
        let g = table().chain("GOLD", "2026-10-30").unwrap();
        assert_eq!((g["segment"].clone(), g["class"].clone(), g["lot"].clone()), (json!("MCX"), json!("commodity"), json!(1)));
        assert_eq!(g["strikes"][0]["strike"], 164000.0);
        let s = table().chain("CROMPTON", "2026-10-27").unwrap();
        assert_eq!(s["strikes"][0]["strike"], 177.5);
        assert_eq!(s["lot"], 2150);
        assert!((s["tick"].as_f64().unwrap() - 0.01).abs() < 1e-9);
    }

    #[test]
    fn refuses_a_chain_with_a_bad_date_an_unknown_underlying_or_an_expiry_it_does_not_have() {
        let t = table();
        assert_eq!(t.chain("NIFTY", "03NOV2026"), Err(DerivsError::BadDate));
        assert_eq!(t.chain("NIFTY", ""), Err(DerivsError::BadDate));
        assert_eq!(t.chain("NOPE", "2026-11-03"), Err(DerivsError::UnknownUnderlying));
        assert_eq!(t.chain("NIFTY", "2026-11-04"), Err(DerivsError::UnknownExpiry));
    }

    #[test]
    fn refuses_a_chain_with_more_strikes_than_the_limit() {
        let rows: Vec<Value> = (0..MAX_STRIKES + 1)
            .map(|i| row(&i.to_string(), &format!("X03NOV26{}CE", 100 + i), "X", "03NOV2026", &format!("{}.000000", (100 + i) * 100), "1", "OPTSTK", "NFO", "5.000000"))
            .collect();
        let t = Derivs::from_rows(&Value::Array(rows), d(2026, 10, 19));
        assert_eq!(t.chain("X", "2026-11-03"), Err(DerivsError::TooManyStrikes));
    }

    #[test]
    fn lists_the_futures_of_an_underlying_by_expiry() {
        let f = table().futures("NIFTY").unwrap();
        let list = f["futures"].as_array().unwrap();
        assert_eq!(list.iter().map(|x| x["expiry"].as_str().unwrap()).collect::<Vec<_>>(), vec!["2026-10-27", "2026-11-24"]);
        assert_eq!(list[0]["lot"], 65);
        assert_eq!(table().futures("CROMPTON").unwrap()["futures"].as_array().unwrap().len(), 0);
        assert_eq!(table().futures("NOPE"), Err(DerivsError::UnknownUnderlying));
    }

    #[test]
    fn a_row_with_a_bad_date_or_no_kind_is_skipped_not_fatal() {
        let rows = json!([
            row("1", "X27OCT26FUT", "X", "NOTADATE", "-1.000000", "1", "FUTSTK", "NFO", "5.000000"),
            row("2", "X27OCT26100XX", "X", "27OCT2026", "10000.000000", "1", "OPTSTK", "NFO", "5.000000"),
            row("3", "Y27OCT26FUT", "Y", "27OCT2026", "-1.000000", "1", "FUTSTK", "NFO", "5.000000"),
            json!({"token": "4"}),
            json!(5)
        ]);
        let t = Derivs::from_rows(&rows, d(2026, 10, 19));
        assert_eq!(t.len(), 1);
        assert!(t.by_token("3").is_some());
        assert_eq!(Derivs::from_rows(&json!("not a list"), d(2026, 10, 19)).len(), 0);
    }

    #[test]
    fn the_store_says_loading_then_ready_and_keeps_a_table_over_a_later_failure() {
        let store = DerivsStore::new();
        assert_eq!(store.with(|t| t.len()), Err(NotReady::Loading));
        store.set_failed("download failed");
        assert_eq!(store.with(|t| t.len()), Err(NotReady::Failed("download failed".into())));
        store.set_ready(table());
        assert_eq!(store.with(|t| t.len()), Ok(table().len()));
        store.set_failed("download failed again");
        assert_eq!(store.with(|t| t.len()), Ok(table().len()));
    }

    #[tokio::test]
    async fn a_fresh_cache_is_used_without_a_download_and_the_compact_cache_reads_back_to_the_same_table() {
        let dir = std::env::temp_dir().join(format!("athena-derivs-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cache = dir.join("derivs.json");
        let today = d(2026, 10, 19);
        std::fs::write(&cache, Derivs::compact_rows(&rows(), today).to_string()).unwrap();
        let t = load_with::<fn() -> std::future::Ready<Result<Value, LoadError>>, _>(&cache, SystemTime::now(), today, None).await.unwrap();
        assert_eq!(t.len(), table().len());
        assert_eq!(t.chain("NIFTY", "2026-11-03").unwrap(), table().chain("NIFTY", "2026-11-03").unwrap());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn a_missing_cache_downloads_and_writes_the_compact_rows() {
        let dir = std::env::temp_dir().join(format!("athena-derivs-dl-{}", std::process::id()));
        let cache = dir.join("derivs.json");
        let today = d(2026, 10, 19);
        let t = load_with(&cache, SystemTime::now(), today, Some(|| async { Ok(rows()) })).await.unwrap();
        assert_eq!(t.len(), table().len());
        let written: Value = serde_json::from_str(&std::fs::read_to_string(&cache).unwrap()).unwrap();
        assert_eq!(written.as_array().unwrap().len(), table().len()); // only contracts, not the cash rows
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn a_download_with_no_contracts_keeps_an_older_cache_and_a_failed_one_too() {
        let dir = std::env::temp_dir().join(format!("athena-derivs-old-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cache = dir.join("derivs.json");
        let today = d(2026, 10, 19);
        std::fs::write(&cache, Derivs::compact_rows(&rows(), today).to_string()).unwrap();
        let old = SystemTime::now() + Duration::from_secs(48 * 3600); // the cache is now two days old
        let empty = load_with(&cache, old, today, Some(|| async { Ok(json!([])) })).await.unwrap();
        assert_eq!(empty.len(), table().len());
        let failed = load_with(&cache, old, today, Some(|| async { Err(LoadError("download failed")) })).await.unwrap();
        assert_eq!(failed.len(), table().len());
        assert!(std::fs::read_to_string(&cache).unwrap().contains("NIFTY")); // the cache was not erased
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn with_no_cache_and_no_good_download_it_says_so() {
        let dir = std::env::temp_dir().join(format!("athena-derivs-none-{}", std::process::id()));
        let cache = dir.join("derivs.json");
        let r = load_with(&cache, SystemTime::now(), d(2026, 10, 19), Some(|| async { Ok(json!([])) })).await;
        assert!(r.is_err());
        let offline = load_with::<fn() -> std::future::Ready<Result<Value, LoadError>>, _>(&cache, SystemTime::now(), d(2026, 10, 19), None).await;
        assert!(offline.is_err());
    }
}
