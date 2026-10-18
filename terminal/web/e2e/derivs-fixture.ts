import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

/** An expiry `days` from today (India), as the instrument list spells it (27OCT2026) and as an ISO date. */
export function expiryIn(days: number): { list: string; iso: string } {
  const d = new Date(Date.now() + 19_800_000 + days * 86_400_000);
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return { list: `${dd}${MONTHS[d.getUTCMonth()]}${d.getUTCFullYear()}`, iso: d.toISOString().slice(0, 10) };
}

export const FIXTURE_EXPIRIES = [4, 11, 39];

/** A small instrument list in the shape of Angel's public one: NIFTY with three expiries and 51 strikes, GOLD (MCX) with a future and options, and a stock that has a
 *  future but no options. The expiries are relative to today, so the list never goes stale. */
export function writeDerivsFixture(): string {
  const rows: Record<string, string>[] = [];
  let token = 1;
  const row = (symbol: string, name: string, expiry: string, strike: number, lot: number, kind: string, seg: string, tick: number): void => {
    rows.push({ token: String(token++), symbol, name, expiry, strike: (strike * 100).toFixed(6), lotsize: String(lot), instrumenttype: kind, exch_seg: seg, tick_size: (tick * 100).toFixed(6) });
  };
  for (const days of FIXTURE_EXPIRIES) {
    const e = expiryIn(days).list;
    row(`NIFTY${e}FUT`, 'NIFTY', e, -0.01, 65, 'FUTIDX', 'NFO', 0.05);
    for (let k = 22_500; k <= 27_500; k += 100) {
      row(`NIFTY${e}${k}CE`, 'NIFTY', e, k, 65, 'OPTIDX', 'NFO', 0.05);
      row(`NIFTY${e}${k}PE`, 'NIFTY', e, k, 65, 'OPTIDX', 'NFO', 0.05);
    }
  }
  const gold = expiryIn(FIXTURE_EXPIRIES[0]).list;
  row(`GOLD${gold}FUT`, 'GOLD', gold, 0, 1, 'FUTCOM', 'MCX', 1);
  for (let k = 150_000; k <= 180_000; k += 1000) {
    row(`GOLD${gold}${k}CE`, 'GOLD', gold, k, 1, 'OPTFUT', 'MCX', 1);
    row(`GOLD${gold}${k}PE`, 'GOLD', gold, k, 1, 'OPTFUT', 'MCX', 1);
  }
  const stock = expiryIn(FIXTURE_EXPIRIES[0]).list;
  row(`RELIANCE${stock}FUT`, 'RELIANCE', stock, -0.01, 500, 'FUTSTK', 'NFO', 0.05);
  const path = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'e2e-derivs', 'derivs_master.json');
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(rows));
  return path;
}
