//! The calendar: the exchange's board meetings (results, dividends, fund raising) and corporate actions by ex-date, from the warehouse, for a range of dates.
//! Read-only. A table that is missing (a warehouse built before it existed) gives an empty list, not an error.
use std::path::Path;

use chrono::NaiveDate;
use duckdb::{AccessMode, Config, Connection};
use serde_json::{json, Value};

pub const MAX_DAYS: i64 = 366;
pub const MAX_ROWS: usize = 5000;

#[derive(Debug, PartialEq, Eq)]
pub enum CalendarError {
    /// A date that is not `YYYY-MM-DD`, an end before the start, or more than `MAX_DAYS` days.
    BadRange,
    Busy,
}

/// What a corporate action is, from the exchange's own wording (a dividend of any kind, a bonus, a split or sub-division, rights, a buyback, a scheme of
/// arrangement or demerger, a capital reduction, a general meeting, an interest or unit-holder distribution); anything else is `other`.
pub fn action_kind(subject: &str) -> &'static str {
    let s = subject.to_ascii_lowercase();
    let has = |words: &[&str]| words.iter().any(|w| s.contains(w));
    if has(&["demerger", "arrangement", "amalgamation", "merger"]) {
        "scheme"
    } else if has(&["bonus"]) {
        "bonus"
    } else if has(&["split", "sub-division", "sub division"]) {
        "split"
    } else if has(&["rights"]) {
        "rights"
    } else if has(&["buy back", "buyback", "buy-back"]) {
        "buyback"
    } else if has(&["capital reduction", "reduction of capital"]) {
        "capital"
    } else if has(&["dividend", "int div", "div -"]) {
        "dividend"
    } else if has(&["general meeting"]) {
        "meeting"
    } else if has(&["interest payment", "distribution", "redemption"]) {
        "distribution"
    } else {
        "other"
    }
}

/// The parts of a board meeting's purpose, which the exchange joins with `/` ("Financial Results/Dividend/Other business matters").
pub fn purpose_kinds(purpose: &str) -> Vec<&'static str> {
    let mut kinds: Vec<&'static str> = Vec::new();
    for part in purpose.split('/') {
        let p = part.trim().to_ascii_lowercase();
        let kind = if p.contains("financial result") {
            "results"
        } else if p.contains("dividend") {
            "dividend"
        } else if p.contains("fund raising") {
            "fundraising"
        } else if p.contains("bonus") {
            "bonus"
        } else if p.contains("split") {
            "split"
        } else if p.contains("buyback") || p.contains("buy back") {
            "buyback"
        } else if p.is_empty() {
            continue;
        } else {
            "other"
        };
        if !kinds.contains(&kind) {
            kinds.push(kind);
        }
    }
    kinds
}

fn date(text: &str) -> Result<NaiveDate, CalendarError> {
    NaiveDate::parse_from_str(text, "%Y-%m-%d").map_err(|_| CalendarError::BadRange)
}

/// Board meetings and corporate actions dated `from` to `to` (inclusive), each with the company's name, oldest date first.
pub fn calendar(path: &Path, from: &str, to: &str) -> Result<Value, CalendarError> {
    let (start, end) = (date(from)?, date(to)?);
    if end < start || (end - start).num_days() > MAX_DAYS {
        return Err(CalendarError::BadRange);
    }
    let config = Config::default().access_mode(AccessMode::ReadOnly).map_err(|_| CalendarError::Busy)?;
    let conn = Connection::open_with_flags(path, config).map_err(|_| CalendarError::Busy)?;
    let (from, to) = (start.to_string(), end.to_string());

    let events: Vec<Value> = conn
        .prepare(
            "SELECT e.symbol, COALESCE(NULLIF(e.company, ''), u.name, e.symbol), CAST(e.event_date AS VARCHAR), e.purpose, COALESCE(e.description, '') \
             FROM events e LEFT JOIN universe u ON u.symbol = e.symbol WHERE e.event_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) ORDER BY e.event_date, e.symbol LIMIT ?",
        )
        .ok()
        .and_then(|mut stmt| {
            stmt.query_map(duckdb::params![from, to, MAX_ROWS as i64 + 1], |r| {
                let purpose: String = r.get(3)?;
                Ok(json!({"symbol": r.get::<_, String>(0)?, "name": r.get::<_, String>(1)?, "date": r.get::<_, String>(2)?, "purpose": purpose, "kinds": purpose_kinds(&purpose), "description": r.get::<_, String>(4)?}))
            })
            .ok()
            .map(|rows| rows.filter_map(Result::ok).collect())
        })
        .unwrap_or_default();

    let actions: Vec<Value> = conn
        .prepare(
            "SELECT a.symbol, COALESCE(u.name, a.symbol), CAST(a.ex_date AS VARCHAR), a.subject FROM actions a LEFT JOIN universe u ON u.symbol = a.symbol \
             WHERE a.ex_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) ORDER BY a.ex_date, a.symbol LIMIT ?",
        )
        .ok()
        .and_then(|mut stmt| {
            stmt.query_map(duckdb::params![from, to, MAX_ROWS as i64 + 1], |r| {
                let subject: String = r.get(3)?;
                Ok(json!({"symbol": r.get::<_, String>(0)?, "name": r.get::<_, String>(1)?, "date": r.get::<_, String>(2)?, "kind": action_kind(&subject), "subject": subject}))
            })
            .ok()
            .map(|rows| rows.filter_map(Result::ok).collect())
        })
        .unwrap_or_default();

    let through = |sql: &str| -> Option<String> { conn.query_row(sql, [], |r| r.get::<_, Option<String>>(0)).ok().flatten() };
    let truncated = events.len() > MAX_ROWS || actions.len() > MAX_ROWS;
    let cut = |mut v: Vec<Value>| {
        v.truncate(MAX_ROWS);
        v
    };
    Ok(json!({
        "from": from, "to": to,
        "events": cut(events), "actions": cut(actions),
        "events_through": through("SELECT CAST(max(event_date) AS VARCHAR) FROM events"),
        "actions_through": through("SELECT CAST(max(ex_date) AS VARCHAR) FROM actions"),
        "truncated": truncated,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn actions_are_told_apart_by_the_exchanges_own_wording() {
        for (text, kind) in [
            ("Interim Dividend - Rs 5 Per Share", "dividend"),
            ("Annual General Meeting/Dividend - Rs 7 Per Share", "dividend"),
            ("Special Dividend", "dividend"),
            ("Bonus 1:1", "bonus"),
            ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", "split"),
            ("Rights 3:26 @ Premium Rs 560/-", "rights"),
            ("Buy Back Of Shares", "buyback"),
            ("Demerger", "scheme"),
            ("Scheme Of Arrangement - Bonus Ncrps 46:1", "scheme"),
            ("Annual General Meeting", "meeting"),
            ("Extra-Ordinary General Meeting", "meeting"),
            ("Interest Payment", "distribution"),
            ("Distribution - Rs 3 Per Unit", "distribution"),
            ("Capital Reduction", "capital"),
            ("Something New", "other"),
        ] {
            assert_eq!(action_kind(text), kind, "{text}");
        }
    }

    #[test]
    fn a_meetings_purpose_is_split_into_its_parts() {
        assert_eq!(purpose_kinds("Financial Results"), vec!["results"]);
        assert_eq!(purpose_kinds("Financial Results/Dividend/Other business matters"), vec!["results", "dividend", "other"]);
        assert_eq!(purpose_kinds("Fund Raising"), vec!["fundraising"]);
        assert_eq!(purpose_kinds("Financial Results/Financial Results"), vec!["results"]);
        assert!(purpose_kinds("").is_empty());
    }

    fn warehouse(tag: &str, with_events: bool) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-calendar-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        let c = Connection::open(&path).unwrap();
        c.execute_batch(
            "CREATE TABLE universe (symbol VARCHAR, kind VARCHAR, name VARCHAR, isin VARCHAR, updated_at TIMESTAMP);
             INSERT INTO universe VALUES ('AAA', 'stock', 'Alpha Limited', 'X', now()), ('BBB', 'stock', 'Beta Limited', 'Y', now());
             CREATE TABLE actions (symbol VARCHAR, ex_date DATE, subject VARCHAR, face_value DOUBLE, series VARCHAR, isin VARCHAR, fetched_at TIMESTAMP);
             INSERT INTO actions VALUES ('AAA', DATE '2026-10-21', 'Interim Dividend - Rs 5', NULL, 'EQ', 'X', now()), ('BBB', DATE '2026-10-22', 'Bonus 1:1', NULL, 'EQ', 'Y', now()),
               ('AAA', DATE '2025-01-01', 'Annual General Meeting', NULL, 'EQ', 'X', now());",
        )
        .unwrap();
        if with_events {
            c.execute_batch(
                "CREATE TABLE events (symbol VARCHAR, event_date DATE, purpose VARCHAR, description VARCHAR, company VARCHAR, fetched_at TIMESTAMP);
                 INSERT INTO events VALUES ('BBB', DATE '2026-10-23', 'Financial Results/Dividend', 'To consider results', 'Beta Ltd', now()),
                   ('AAA', DATE '2026-10-19', 'Financial Results', 'To consider results', '', now()), ('ZZZ', DATE '2026-12-31', 'Fund Raising', '', 'Zed', now());",
            )
            .unwrap();
        }
        path
    }

    #[test]
    fn the_range_gives_events_and_actions_in_date_order_with_names_and_kinds() {
        let a = calendar(&warehouse("a", true), "2026-10-19", "2026-10-31").unwrap();
        let events = a["events"].as_array().unwrap();
        assert_eq!(events.iter().map(|e| e["symbol"].as_str().unwrap()).collect::<Vec<_>>(), vec!["AAA", "BBB"]); // ZZZ is in December
        assert_eq!(events[0]["name"], "Alpha Limited"); // no company in the listing: the universe's name
        assert_eq!(events[1]["name"], "Beta Ltd"); // the listing's own name wins
        assert_eq!(events[1]["kinds"], json!(["results", "dividend"]));
        let actions = a["actions"].as_array().unwrap();
        assert_eq!(actions.iter().map(|e| (e["symbol"].as_str().unwrap(), e["kind"].as_str().unwrap())).collect::<Vec<_>>(), vec![("AAA", "dividend"), ("BBB", "bonus")]);
        assert_eq!(a["events_through"], "2026-12-31");
        assert_eq!(a["actions_through"], "2026-10-22");
        assert_eq!(a["truncated"], false);
    }

    #[test]
    fn the_range_is_inclusive_at_both_ends_and_can_look_back() {
        let a = calendar(&warehouse("b", true), "2026-10-23", "2026-10-23").unwrap();
        assert_eq!(a["events"].as_array().unwrap().len(), 1);
        let past = calendar(&warehouse("c", true), "2024-12-31", "2025-01-02").unwrap();
        assert_eq!(past["actions"][0]["kind"], "meeting");
    }

    #[test]
    fn a_warehouse_without_the_events_table_still_answers_with_the_actions() {
        let a = calendar(&warehouse("d", false), "2026-10-19", "2026-10-31").unwrap();
        assert!(a["events"].as_array().unwrap().is_empty());
        assert_eq!(a["actions"].as_array().unwrap().len(), 2);
        assert!(a["events_through"].is_null());
    }

    #[test]
    fn bad_ranges_are_refused() {
        let w = warehouse("e", true);
        assert_eq!(calendar(&w, "2026-13-01", "2026-12-01"), Err(CalendarError::BadRange));
        assert_eq!(calendar(&w, "yesterday", "2026-12-01"), Err(CalendarError::BadRange));
        assert_eq!(calendar(&w, "2026-10-20", "2026-10-19"), Err(CalendarError::BadRange));
        assert_eq!(calendar(&w, "2026-01-01", "2027-06-01"), Err(CalendarError::BadRange)); // over 366 days
        assert!(calendar(&w, "2026-01-01", "2027-01-01").is_ok()); // 365 days
        assert_eq!(calendar(Path::new("no/such.duckdb"), "2026-10-19", "2026-10-20"), Err(CalendarError::Busy));
    }
}
