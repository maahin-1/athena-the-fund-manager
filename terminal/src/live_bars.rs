//! Today's one-minute bars, built on the server from every quote the hub receives (the stream, and the exchange's snapshot for symbols beyond the stream's cap).
//! The intraday history comes from a free source that may lag the market; these bars fill the minutes since the last bar it has, so a chart opened in the
//! middle of the day has no gap and its newest bars carry the volume traded in them. Kept in memory only, for the current IST day.
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use crate::bars::Bar;

const IST_OFFSET: i64 = 19_800;
const DAY: i64 = 86_400;
const OPEN: i64 = 9 * 3600 + 15 * 60; // 09:15 IST, seconds after IST midnight

#[derive(Default)]
struct Symbol {
    day: i64,
    /// The day's traded volume at the last quote, to turn the running total into each minute's share.
    last_total: Option<f64>,
    bars: Vec<Bar>,
}

#[derive(Clone, Default)]
pub struct LiveBars(Arc<Mutex<HashMap<String, Symbol>>>);

/// Start of the bar of `width` seconds that `t` falls in, counted from the 09:15 open of its IST day (so a 30-minute bar starts 09:15, 09:45 and so on).
pub fn bucket(t: i64, width: i64) -> i64 {
    let local = t + IST_OFFSET;
    let open = local.div_euclid(DAY) * DAY + OPEN;
    open + (local - open).div_euclid(width) * width - IST_OFFSET
}

impl LiveBars {
    #[cfg(test)]
    pub fn new() -> Self {
        LiveBars::default()
    }

    /// One quote: its price at `t` (Unix seconds) and the day's running volume when the quote has one. A quote for an earlier minute than the newest bar
    /// changes nothing; a new IST day starts the symbol afresh.
    pub fn record(&self, symbol: &str, price: f64, total_volume: Option<f64>, t: i64) {
        if !(price.is_finite() && price > 0.0) {
            return;
        }
        let day = (t + IST_OFFSET).div_euclid(DAY);
        let mut g = self.0.lock().unwrap();
        let s = g.entry(symbol.to_string()).or_default();
        if s.day != day {
            *s = Symbol { day, ..Symbol::default() };
        }
        let traded = match (s.last_total, total_volume) {
            (Some(before), Some(now)) if now >= before => now - before,
            _ => 0.0, // the first quote only sets where the running total starts
        };
        if total_volume.is_some() {
            s.last_total = total_volume;
        }
        let minute = bucket(t, 60);
        match s.bars.last_mut() {
            Some(b) if b.t == minute => {
                b.h = b.h.max(price);
                b.l = b.l.min(price);
                b.c = price;
                b.v += traded;
            }
            Some(b) if b.t > minute => {}
            _ => s.bars.push(Bar { t: minute, o: price, h: price, l: price, c: price, v: traded }),
        }
    }

    /// The day's bars for `symbol` at a width of `width` seconds (60 or a multiple of it), oldest first.
    pub fn bars(&self, symbol: &str, width: i64) -> Vec<Bar> {
        let g = self.0.lock().unwrap();
        let Some(s) = g.get(symbol) else { return Vec::new() };
        let mut out: Vec<Bar> = Vec::new();
        for m in &s.bars {
            let t = bucket(m.t, width);
            match out.last_mut() {
                Some(b) if b.t == t => {
                    b.h = b.h.max(m.h);
                    b.l = b.l.min(m.l);
                    b.c = m.c;
                    b.v += m.v;
                }
                _ => out.push(Bar { t, ..m.clone() }),
            }
        }
        out
    }
}

/// The history with the live bars laid over its end: a live bar later than the history's last is added; the history's last bar, when the live bars also
/// cover it, takes their high, low and close (its open and volume stay, as the live bars may have started partway through it). Earlier bars are the history's.
pub fn merge(mut history: Vec<Bar>, live: &[Bar]) -> Vec<Bar> {
    let last_t = history.last().map(|b| b.t);
    for b in live {
        match last_t {
            Some(t) if b.t < t => continue,
            Some(t) if b.t == t => {
                let h = history.last_mut().expect("there is a last bar");
                h.h = h.h.max(b.h);
                h.l = h.l.min(b.l);
                h.c = b.c;
            }
            _ => history.push(b.clone()),
        }
    }
    history
}

/// Seconds in a bar of the page's interval, for the intervals the live bars serve.
pub fn width(tf: &str) -> Option<i64> {
    Some(match tf {
        "1m" => 60,
        "5m" => 300,
        "15m" => 900,
        "30m" => 1800,
        "1h" => 3600,
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // 2026-10-26 09:15 IST is 03:45 UTC
    const OPEN_T: i64 = 1_792_986_300;

    #[test]
    fn bars_are_counted_from_the_open() {
        assert_eq!(bucket(OPEN_T + 61, 60), OPEN_T + 60);
        assert_eq!(bucket(OPEN_T + 29 * 60, 1800), OPEN_T);
        assert_eq!(bucket(OPEN_T + 31 * 60, 1800), OPEN_T + 1800);
        assert_eq!(bucket(OPEN_T + 45 * 60, 3600), OPEN_T);
    }

    #[test]
    fn quotes_become_minute_bars_with_the_volume_traded_in_each() {
        let lb = LiveBars::new();
        lb.record("SBIN", 800.0, Some(1000.0), OPEN_T + 5); // sets the running total
        lb.record("SBIN", 802.0, Some(1600.0), OPEN_T + 30);
        lb.record("SBIN", 799.0, Some(1700.0), OPEN_T + 59);
        lb.record("SBIN", 801.0, Some(2000.0), OPEN_T + 65);
        lb.record("SBIN", 900.0, Some(2100.0), OPEN_T + 10); // late: an earlier minute than the newest bar
        let bars = lb.bars("SBIN", 60);
        assert_eq!(bars.len(), 2);
        assert_eq!((bars[0].o, bars[0].h, bars[0].l, bars[0].c, bars[0].v), (800.0, 802.0, 799.0, 799.0, 700.0));
        assert_eq!((bars[1].o, bars[1].c, bars[1].v), (801.0, 801.0, 300.0));
        let five = lb.bars("SBIN", 300);
        assert_eq!(five.len(), 1);
        assert_eq!((five[0].o, five[0].h, five[0].l, five[0].c, five[0].v), (800.0, 802.0, 799.0, 801.0, 1000.0));
        assert!(lb.bars("INFY", 60).is_empty());
    }

    #[test]
    fn a_new_day_starts_afresh_and_a_bad_price_is_ignored() {
        let lb = LiveBars::new();
        lb.record("SBIN", 800.0, Some(1000.0), OPEN_T);
        lb.record("SBIN", f64::NAN, None, OPEN_T + 1);
        lb.record("SBIN", 810.0, Some(50.0), OPEN_T + DAY);
        let bars = lb.bars("SBIN", 60);
        assert_eq!(bars.len(), 1);
        assert_eq!((bars[0].t, bars[0].o, bars[0].v), (OPEN_T + DAY, 810.0, 0.0));
    }

    #[test]
    fn the_live_bars_extend_the_history_and_update_its_last_bar() {
        let bar = |t: i64, o: f64, h: f64, l: f64, c: f64, v: f64| Bar { t, o, h, l, c, v };
        let history = vec![bar(OPEN_T, 100.0, 101.0, 99.0, 100.5, 500.0), bar(OPEN_T + 300, 100.5, 101.0, 100.0, 100.8, 400.0)];
        let live = vec![bar(OPEN_T, 1.0, 1.0, 1.0, 1.0, 1.0), bar(OPEN_T + 300, 100.7, 102.0, 100.6, 101.9, 50.0), bar(OPEN_T + 600, 101.9, 102.5, 101.8, 102.2, 70.0)];
        let out = merge(history, &live);
        assert_eq!(out.len(), 3);
        assert_eq!(out[0].c, 100.5); // earlier bars are the history's
        assert_eq!((out[1].o, out[1].h, out[1].l, out[1].c, out[1].v), (100.5, 102.0, 100.0, 101.9, 400.0));
        assert_eq!(out[2].c, 102.2);
        assert_eq!(merge(Vec::new(), &live[1..]).len(), 2);
    }
}
