//! `--fake-feed`: deterministic random-walk quotes for whatever symbols pages watch, so the page and its tests run without a broker.
use std::collections::HashMap;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio_util::sync::CancellationToken;

use crate::hub::Hub;
use crate::quote::{Quote, Source};

fn seed(symbol: &str) -> u64 {
    symbol.bytes().fold(1469598103934665603u64, |h, b| (h ^ b as u64).wrapping_mul(1099511628211))
}

fn next(state: &mut u64) -> f64 {
    *state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
    ((*state >> 33) as f64) / ((1u64 << 31) as f64) // 0..1
}

pub fn quote_for(symbol: &str, price: f64, prev_close: f64, ms: i64) -> Quote {
    let tick = 0.05;
    let mid = (price / tick).round() * tick;
    Quote {
        symbol: symbol.to_string(),
        last: price,
        prev_close,
        open: Some(prev_close),
        high: Some(price.max(prev_close)),
        low: Some(price.min(prev_close)),
        volume: Some(1_000_000.0),
        average: Some(prev_close),
        high_52: Some(prev_close * 1.2),
        low_52: Some(prev_close * 0.7),
        lower_band: Some(prev_close * 0.9),
        upper_band: Some(prev_close * 1.1),
        exchange_ms: Some(ms),
        bids: (1..=5).map(|i| (mid - tick * i as f64, 500.0 * i as f64)).collect(),
        asks: (1..=5).map(|i| (mid + tick * i as f64, 400.0 * i as f64)).collect(),
        source: Source::Stream,
    }
}

pub async fn run_fake(hub: Hub, shutdown: CancellationToken) {
    let mut walk: HashMap<String, (u64, f64, f64)> = HashMap::new(); // symbol -> (rng, price, prev close)
    loop {
        tokio::select! {
            _ = shutdown.cancelled() => return,
            _ = tokio::time::sleep(Duration::from_millis(250)) => {}
        }
        let ms = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0);
        for symbol in hub.wanted_symbols() {
            let entry = walk.entry(symbol.clone()).or_insert_with(|| {
                let s = seed(&symbol);
                let base = 100.0 + (s % 3000) as f64;
                (s, base, base)
            });
            let step = (next(&mut entry.0) - 0.5) * 0.004 * entry.2;
            entry.1 = ((entry.1 + step).max(1.0) * 20.0).round() / 20.0;
            hub.update(quote_for(&symbol, entry.1, entry.2, ms), Instant::now());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn watched_symbols_get_changing_prices() {
        let hub = Hub::new();
        let c = hub.register_client();
        hub.watch(c, &["SBIN".into()]);
        let stop = CancellationToken::new();
        let task = tokio::spawn(run_fake(hub.clone(), stop.clone()));
        let mut seen = Vec::new();
        for _ in 0..40 {
            tokio::time::sleep(Duration::from_millis(100)).await;
            if let Some(f) = hub.frame(c) {
                seen.push(f.0[0].last);
            }
            if seen.len() >= 4 {
                break;
            }
        }
        stop.cancel();
        let _ = task.await;
        assert!(seen.len() >= 4, "got {} quotes", seen.len());
        assert!(seen.windows(2).any(|w| w[0] != w[1]), "prices never moved");
    }
}
