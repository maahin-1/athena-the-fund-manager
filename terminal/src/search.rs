//! Finding an instrument as you type: symbols, company names, ISINs and index names, ranked like a search box (exact, then starts-with, then
//! every word matching, then a small typo allowance). Among equals the more traded stock comes first, so "tata" shows the big Tata names first.
//! Built from the warehouse's `universe` and `screen` tables and kept in memory; typing never touches the database.
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};

/// How many renames back an old symbol is followed (the price join and the statements follow the same number).
const MAX_HOPS: usize = 5;
const REBUILD_AFTER: Duration = Duration::from_secs(600);
pub const MAX_QUERY: usize = 60;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Hit {
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "n")]
    pub name: String,
    #[serde(rename = "k")]
    pub kind: String,
}

struct Entry {
    hit: Hit,
    symbol: String,       // lower case, as listed
    isin: String,         // lower case
    name: String,         // lower case, punctuation as spaces
    words: Vec<String>,   // the name's words, the symbol and any old symbol
    aliases: Vec<String>, // old symbols, lower case
    traded: f64,
}

/// Lower case, anything that is not a letter or digit turned into a space, spaces collapsed.
pub fn normalize(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut space = true;
    for c in text.chars().flat_map(char::to_lowercase) {
        if c.is_alphanumeric() {
            out.push(c);
            space = false;
        } else if !space {
            out.push(' ');
            space = true;
        }
    }
    out.trim_end().to_string()
}

/// Edit distance between two short strings, or `None` when it is more than `max`.
fn distance(a: &str, b: &str, max: usize) -> Option<usize> {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    if a.len().abs_diff(b.len()) > max {
        return None;
    }
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    for (i, ca) in a.iter().enumerate() {
        let mut cur = vec![i + 1];
        for (j, cb) in b.iter().enumerate() {
            cur.push((prev[j] + usize::from(ca != cb)).min(prev[j + 1] + 1).min(cur[j] + 1));
        }
        prev = cur;
    }
    let d = prev[b.len()];
    (d <= max).then_some(d)
}

pub struct SearchIndex {
    entries: Vec<Entry>,
}

impl SearchIndex {
    /// `rows` are (symbol, kind, name, isin, traded value); `indices` are index names; `renames` are (old symbol, new symbol) pairs from the
    /// warehouse's rename table, so that an old symbol finds the company that has it now, through any number of renames.
    pub fn new(rows: Vec<(String, String, String, String, f64)>, indices: Vec<String>, renames: &[(String, String)]) -> Self {
        let mut earlier: HashMap<&str, Vec<&str>> = HashMap::new();
        for (old, new) in renames {
            earlier.entry(new.as_str()).or_default().push(old.as_str());
        }
        let mut entries: Vec<Entry> = rows
            .into_iter()
            .map(|(symbol, kind, name, isin, traded)| {
                let aliases = old_symbols(&earlier, &symbol);
                Self::entry(symbol, kind, name, isin, traded, aliases)
            })
            .collect();
        for name in indices {
            let upper = name.to_uppercase();
            if !entries.iter().any(|e| e.hit.symbol == upper) {
                entries.push(Self::entry(upper.clone(), "index".into(), upper, String::new(), 0.0, Vec::new()));
            }
        }
        SearchIndex { entries }
    }

    fn entry(symbol: String, kind: String, name: String, isin: String, traded: f64, aliases: Vec<String>) -> Entry {
        let norm = normalize(&name);
        let mut words: Vec<String> = norm.split(' ').filter(|w| !w.is_empty()).map(String::from).collect();
        words.push(symbol.to_lowercase());
        words.extend(aliases.iter().cloned());
        let shown = if name.is_empty() { symbol.clone() } else { name };
        Entry { symbol: symbol.to_lowercase(), isin: isin.to_lowercase(), name: norm, words, aliases, traded, hit: Hit { symbol, name: shown, kind } }
    }

    /// Up to `limit` matches, best first, of the given kinds (all when `kinds` is empty). An empty or over-long query matches nothing.
    pub fn search(&self, query: &str, limit: usize, kinds: &[String]) -> Vec<Hit> {
        let raw = query.trim().to_lowercase();
        let q = normalize(query);
        if q.is_empty() || query.chars().count() > MAX_QUERY {
            return Vec::new();
        }
        let tokens: Vec<&str> = q.split(' ').collect();
        let mut scored: Vec<(f64, f64, &Entry)> = self
            .entries
            .iter()
            .filter(|e| kinds.is_empty() || kinds.iter().any(|k| *k == e.hit.kind))
            .filter_map(|e| Self::score(e, &raw, &q, &tokens).map(|s| (s + Self::kind_bonus(&e.hit.kind), e.traded, e)))
            .collect();
        scored.sort_by(|a, b| {
            b.0.partial_cmp(&a.0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal))
                .then(a.2.hit.symbol.len().cmp(&b.2.hit.symbol.len()))
                .then(a.2.hit.symbol.cmp(&b.2.hit.symbol))
        });
        scored.into_iter().take(limit).map(|(_, _, e)| e.hit.clone()).collect()
    }

    fn kind_bonus(kind: &str) -> f64 {
        match kind {
            "stock" => 3.0,
            "index" => 2.0,
            "etf" => 1.0,
            _ => 0.0,
        }
    }

    fn score(e: &Entry, raw: &str, q: &str, tokens: &[&str]) -> Option<f64> {
        if e.symbol == raw || (!e.isin.is_empty() && e.isin == raw) {
            return Some(1000.0);
        }
        if e.name == q {
            return Some(950.0);
        }
        if e.aliases.iter().any(|a| a == raw) {
            return Some(900.0);
        }
        // a symbol or a name that starts with what is typed is one tier: among them the busier stock wins, so "tata" opens with TCS
        if e.symbol.starts_with(raw) || e.name.starts_with(q) {
            return Some(800.0);
        }
        if e.aliases.iter().any(|a| a.starts_with(raw)) {
            return Some(780.0);
        }
        if tokens.iter().all(|t| e.words.iter().any(|w| w.starts_with(t))) {
            let first = e.words.first().is_some_and(|w| w.starts_with(tokens[0]));
            return Some(600.0 + if first { 40.0 } else { 0.0 });
        }
        if tokens.iter().all(|t| e.name.contains(t) || e.symbol.contains(t)) {
            return Some(400.0);
        }
        // a slip of one letter in a long enough word: "relience", "infosy"
        if q.chars().count() >= 4 {
            let near_symbol = distance(raw, &e.symbol, 1);
            let near_word = tokens.len() == 1 && e.words.iter().any(|w| w.chars().count() >= 4 && distance(tokens[0], w, 1).is_some());
            let near_start = q.chars().count() >= 5 && e.name.chars().count() >= q.chars().count() && distance(q, &e.name.chars().take(q.chars().count()).collect::<String>(), 1).is_some();
            if near_symbol.is_some() || near_word || near_start {
                return Some(200.0);
            }
        }
        None
    }
}

/// The index kept in memory, rebuilt from the warehouse when it is ten minutes old. A busy warehouse (a build holds its lock) leaves the old one.
pub struct SearchCache {
    warehouse: std::path::PathBuf,
    /// The name under which what this cache serves is registered for `/api/data-source`.
    consumer: String,
    rebuild_after: Duration,
    /// When it was built, the index, and the provenance of the data it was built from.
    inner: Mutex<Option<(Instant, Arc<SearchIndex>, Value)>>,
}

impl SearchCache {
    pub fn new(warehouse: std::path::PathBuf) -> Self {
        Self::named(warehouse, "search", REBUILD_AFTER)
    }

    pub fn named(warehouse: std::path::PathBuf, consumer: &str, rebuild_after: Duration) -> Self {
        SearchCache { warehouse, consumer: consumer.to_string(), rebuild_after, inner: Mutex::new(None) }
    }

    /// The current index, with `live_indices` (names from the exchange's snapshot) added when it is rebuilt. `None` when there is none and none can be built.
    ///
    /// What is registered is what is **served**: every call records the provenance of the index it returns (a cache hit included), so the badge describes the data a person is searching, not the last
    /// attempt to rebuild it. A rebuild that fails while an index is held is recorded apart, as a refresh failure; the held index keeps being served and keeps its own provenance.
    pub fn get(&self, live_indices: &[String]) -> Option<Arc<SearchIndex>> {
        let mut g = self.inner.lock().unwrap();
        if let Some((at, index, provenance)) = g.as_ref() {
            if at.elapsed() < self.rebuild_after {
                crate::snapshot::record_served(&self.consumer, None, provenance.clone());
                return Some(index.clone());
            }
        }
        match load(&self.warehouse, live_indices) {
            Ok((index, provenance)) => {
                let index = Arc::new(index);
                crate::snapshot::clear_refresh_failure(&self.consumer);
                crate::snapshot::record_served(&self.consumer, None, provenance.clone());
                *g = Some((Instant::now(), index.clone(), provenance));
                Some(index)
            }
            Err(failure) => {
                crate::snapshot::record_refresh_failure(&self.consumer, failure.clone());
                match g.as_ref() {
                    Some((_, index, provenance)) => {
                        crate::snapshot::record_served(&self.consumer, None, provenance.clone());
                        Some(index.clone())
                    }
                    None => {
                        crate::snapshot::record_served(&self.consumer, None, failure); // nothing is held and nothing could be built: what was served is no data, and why
                        None
                    }
                }
            }
        }
    }
}

/// Every earlier symbol of `symbol`, lower case, following renames back at most `MAX_HOPS` times and never through a symbol twice.
fn old_symbols(earlier: &HashMap<&str, Vec<&str>>, symbol: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut frontier = vec![symbol];
    for _ in 0..MAX_HOPS {
        let mut next = Vec::new();
        for name in frontier {
            for old in earlier.get(name).into_iter().flatten() {
                if *old != symbol && !out.iter().any(|o| o.eq_ignore_ascii_case(old)) {
                    out.push(old.to_lowercase());
                    next.push(*old);
                }
            }
        }
        frontier = next;
    }
    out
}

/// Build the index from the warehouse: the index and the provenance of the data it was built from, or the failure (as the registry reports it) when the warehouse could not be read or did not hold the
/// instrument list. Opens without registering: the caller registers what it serves.
fn load(path: &Path, live_indices: &[String]) -> Result<(SearchIndex, Value), Value> {
    let opened = crate::snapshot::open_unrecorded(path).map_err(|f| f.to_value())?;
    let (conn, provenance) = (opened.conn, opened.provenance.to_value());
    let unreadable = crate::snapshot::unreadable_content;
    let mut stmt = conn
        .prepare("SELECT u.symbol, u.kind, COALESCE(u.name, ''), COALESCE(u.isin, ''), COALESCE(s.traded_value_cr, 0) FROM universe u LEFT JOIN screen s ON s.symbol = u.symbol")
        .map_err(|_| unreadable("the instrument list could not be prepared"))?;
    let rows: Vec<(String, String, String, String, f64)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get::<_, f64>(4)?)))
        .map_err(|_| unreadable("the instrument list could not be read"))?
        .filter_map(Result::ok)
        .collect();
    let mut indices: Vec<String> = conn
        .prepare("SELECT DISTINCT index_name FROM index_bars")
        .ok()
        .and_then(|mut s| s.query_map([], |r| r.get::<_, String>(0)).ok().map(|it| it.filter_map(Result::ok).collect()))
        .unwrap_or_default();
    indices.extend(live_indices.iter().cloned());
    // the old symbols of companies that stopped trading under them; a warehouse built before the table existed has none
    let renames: Vec<(String, String)> = conn
        .prepare("SELECT old, new FROM symbol_renames WHERE retired")
        .ok()
        .and_then(|mut s| s.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).ok().map(|it| it.filter_map(Result::ok).collect()))
        .unwrap_or_default();
    Ok((SearchIndex::new(rows, indices, &renames), provenance))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn index() -> SearchIndex {
        let row = |s: &str, k: &str, n: &str, i: &str, t: f64| (s.to_string(), k.to_string(), n.to_string(), i.to_string(), t);
        SearchIndex::new(
            vec![
                row("TCS", "stock", "Tata Consultancy Services Limited", "INE467B01029", 1500.0),
                row("TMPV", "stock", "Tata Motors Passenger Vehicles Limited", "INE155A01022", 900.0),
                row("TATASTEEL", "stock", "Tata Steel Limited", "INE081A01020", 800.0),
                row("TATAPOWER", "stock", "Tata Power Company Limited", "INE245A01021", 300.0),
                row("TATACHEM", "stock", "Tata Chemicals Limited", "INE092A01019", 40.0),
                row("RVNL", "stock", "Rail Vikas Nigam Limited", "INE415G01027", 200.0),
                row("RELIANCE", "stock", "Reliance Industries Limited", "INE002A01018", 2500.0),
                row("RELINFRA", "stock", "Reliance Infrastructure Limited", "INE036A01016", 100.0),
                row("ETERNAL", "stock", "ETERNAL LIMITED", "INE758T01015", 700.0),
                row("M&M", "stock", "Mahindra & Mahindra Limited", "INE101A01026", 600.0),
                row("NIFTYBEES", "etf", "NIPINDETFNIFTYBEES", "INF204KB14I2", 50.0),
                row("XYZSME", "sme", "Some Small Company Limited", "", 1.0),
            ],
            vec!["NIFTY 50".into(), "Nifty Bank".into(), "NIFTY BANK".into(), "INDIA VIX".into()],
            &[("ZOMATO".into(), "ETERNAL".into()), ("TATAMOTORS".into(), "TMPV".into()), ("OLDEST".into(), "ZOMATO".into()), ("RELIANCE".into(), "RELIANCE".into())],
        )
    }

    fn symbols(q: &str) -> Vec<String> {
        index().search(q, 10, &[]).into_iter().map(|h| h.symbol).collect()
    }

    #[test]
    fn an_exact_symbol_or_isin_comes_first() {
        assert_eq!(symbols("tcs")[0], "TCS");
        assert_eq!(symbols("INE415G01027"), ["RVNL"]);
        assert_eq!(symbols("m&m")[0], "M&M");
    }

    #[test]
    fn a_name_is_found_by_its_start_its_words_or_part_of_a_word() {
        assert_eq!(symbols("rail vikas")[0], "RVNL");
        assert_eq!(symbols("vikas nigam")[0], "RVNL"); // words in any place
        assert_eq!(symbols("tata motors")[0], "TMPV");
        assert_eq!(symbols("reliance ind")[0], "RELIANCE");
        assert_eq!(symbols("mahindra")[0], "M&M");
        assert_eq!(symbols("consultancy")[0], "TCS");
    }

    #[test]
    fn a_short_query_lists_the_busier_stocks_first() {
        let tata = symbols("tata");
        assert_eq!(&tata[..3], ["TCS", "TMPV", "TATASTEEL"]); // by traded value among equals, the symbol that starts with it counts as a better start
        assert!(tata.contains(&"TATACHEM".to_string()));
        assert!(!tata.contains(&"RVNL".to_string()));
    }

    #[test]
    fn an_old_symbol_finds_the_renamed_company() {
        assert_eq!(symbols("zomato")[0], "ETERNAL");
        assert_eq!(symbols("tatamotors")[0], "TMPV");
        assert_eq!(symbols("oldest")[0], "ETERNAL"); // through a chain: OLDEST became ZOMATO, which became ETERNAL
        assert_eq!(symbols("reliance")[0], "RELIANCE"); // a symbol is never its own old symbol
    }

    #[test]
    fn indices_are_found_once_each_with_the_names_the_page_uses() {
        let nifty = index().search("nifty", 10, &[]);
        let names: Vec<&str> = nifty.iter().map(|h| h.symbol.as_str()).collect();
        assert!(names.contains(&"NIFTY 50") && names.contains(&"NIFTY BANK") && names.contains(&"NIFTYBEES"));
        assert_eq!(names.iter().filter(|n| **n == "NIFTY BANK").count(), 1);
        assert_eq!(index().search("india vix", 3, &[])[0].kind, "index");
        assert_eq!(symbols("nifty 50")[0], "NIFTY 50");
    }

    #[test]
    fn a_one_letter_slip_still_finds_it_and_nonsense_finds_nothing() {
        assert_eq!(symbols("relience")[0], "RELIANCE");
        assert_eq!(symbols("rvnk")[0], "RVNL");
        assert!(symbols("qqqqzzzz").is_empty());
        assert!(symbols("   ").is_empty());
        assert!(index().search(&"a".repeat(80), 5, &[]).is_empty());
    }

    #[test]
    fn the_limit_is_kept_and_hits_carry_a_name_and_a_kind() {
        let hits = index().search("tata", 2, &[]);
        assert_eq!(hits.len(), 2);
        assert_eq!((hits[0].name.as_str(), hits[0].kind.as_str()), ("Tata Consultancy Services Limited", "stock"));
    }

    #[test]
    fn a_kind_filter_keeps_only_those_kinds() {
        let only = |kinds: &[&str]| -> Vec<String> {
            let kinds: Vec<String> = kinds.iter().map(|k| k.to_string()).collect();
            index().search("nifty", 10, &kinds).into_iter().map(|h| h.symbol).collect()
        };
        assert_eq!(only(&["etf"]), ["NIFTYBEES"]);
        assert!(!only(&["stock"]).contains(&"NIFTY 50".to_string()));
        assert!(only(&["index", "etf"]).contains(&"NIFTY 50".to_string()));
    }

    #[test]
    fn normalize_drops_punctuation_and_case() {
        assert_eq!(normalize("  Mahindra & Mahindra, Ltd.  "), "mahindra mahindra ltd");
        assert_eq!(normalize("NIFTY 50"), "nifty 50");
    }

    #[test]
    fn distance_stops_at_the_allowance() {
        assert_eq!(distance("abc", "abd", 1), Some(1));
        assert_eq!(distance("abc", "xyz", 1), None);
        assert_eq!(distance("abc", "abcdef", 1), None);
    }

    // ---- T5-R3: what is registered is the index that is served, not the last attempt to build one (synthetic warehouses in temporary directories; each test names its reader) ----
    use crate::snapshot::testkit::{publish, ruin_main, temp_dir};

    fn warehouse_with_universe(path: &Path) {
        let c = duckdb::Connection::open(path).unwrap();
        c.execute_batch(
            "DROP TABLE IF EXISTS universe; DROP TABLE IF EXISTS screen; CREATE TABLE universe (symbol VARCHAR, kind VARCHAR, name VARCHAR, isin VARCHAR); CREATE TABLE screen (symbol VARCHAR, traded_value_cr DOUBLE);
             INSERT INTO universe VALUES ('ZSRCH', 'stock', 'Synthetic Search Limited', 'INE000000001'); INSERT INTO screen VALUES ('ZSRCH', 10.0); CHECKPOINT;",
        )
        .unwrap();
    }

    fn registered(consumer: &str) -> (Value, Value) {
        let s = crate::snapshot::data_sources();
        (s["readers"][consumer].clone(), s["refresh_failures"][consumer].clone())
    }

    #[test]
    fn a_failed_rebuild_keeps_serving_the_held_index_and_registers_what_is_served_not_the_failure() {
        let dir = temp_dir("search-retained");
        let main = dir.join("w.duckdb");
        warehouse_with_universe(&main);
        let cache = SearchCache::named(main.clone(), "srch-retained", Duration::ZERO); // every call tries to rebuild
        assert_eq!(cache.get(&[]).unwrap().search("zsrch", 5, &[])[0].symbol, "ZSRCH");
        let (first, _) = registered("srch-retained");
        assert_eq!(first["source"], "main");
        ruin_main(&main); // the rebuild cannot read the file, and no snapshot exists
        let again = cache.get(&[]).expect("the held index is still served");
        assert_eq!(again.search("zsrch", 5, &[])[0].symbol, "ZSRCH");
        let (served, refresh) = registered("srch-retained");
        assert_eq!((served["source"].clone(), served["generation_id"].clone()), (first["source"].clone(), first["generation_id"].clone()), "what was served is the index built from the real file");
        assert!(served.get("failure").is_none(), "the refresh failure is not the provenance of the data served: {served}");
        assert_eq!((refresh["failure"]["main_reason"].clone(), refresh["failure"]["snapshot_reason"].clone()), (json!("main_unreadable"), json!("absent")));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_later_successful_rebuild_clears_the_refresh_failure() {
        let dir = temp_dir("search-recovers");
        let main = dir.join("w.duckdb");
        warehouse_with_universe(&main);
        let cache = SearchCache::named(main.clone(), "srch-recovers", Duration::ZERO);
        cache.get(&[]).unwrap();
        ruin_main(&main);
        cache.get(&[]).unwrap();
        assert!(registered("srch-recovers").1.is_object());
        std::fs::remove_file(&main).unwrap(); // the real file is replaced by a readable one (the rebuild can only succeed against a database)
        warehouse_with_universe(&main);
        cache.get(&[]).unwrap();
        assert!(registered("srch-recovers").1.is_null(), "recovered: nothing is failing any more");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_cache_hit_registers_the_held_indexs_provenance_again() {
        let dir = temp_dir("search-hit");
        let main = dir.join("w.duckdb");
        warehouse_with_universe(&main);
        publish(&main, 3);
        ruin_main(&main); // built from the snapshot generation
        let cache = SearchCache::named(main.clone(), "srch-hit", Duration::from_secs(3600));
        cache.get(&[]).unwrap();
        let (built, _) = registered("srch-hit");
        assert_eq!((built["source"].clone(), built["fallback_reason"].clone()), (json!("snapshot"), json!("main_unreadable")));
        std::thread::sleep(Duration::from_millis(1100));
        cache.get(&[]).unwrap(); // a cache hit: no rebuild, and still described
        let (hit, _) = registered("srch-hit");
        assert_eq!(hit["generation_id"], built["generation_id"], "the generation it was built from");
        assert!(hit["at"].as_u64().unwrap() > built["at"].as_u64().unwrap(), "serving it again renews the entry");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn nothing_held_and_nothing_buildable_is_no_data_with_the_reasons() {
        let dir = temp_dir("search-none");
        let main = dir.join("w.duckdb");
        warehouse_with_universe(&main);
        ruin_main(&main);
        let cache = SearchCache::named(main.clone(), "srch-none", Duration::ZERO);
        assert!(cache.get(&[]).is_none());
        let (served, refresh) = registered("srch-none");
        assert_eq!(served["failure"]["snapshot_reason"], "absent");
        assert_eq!(refresh["failure"]["main_reason"], "main_unreadable");
        std::fs::remove_dir_all(&dir).ok();
    }
}
