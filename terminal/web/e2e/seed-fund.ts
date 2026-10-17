import { execFileSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Builds the small fundamentals store the Fundamentals test reads (TCS from the XBRL test fixtures). */
export default function seed(): void {
  const here = dirname(fileURLToPath(import.meta.url));
  const root = resolve(here, '..', '..', '..');
  rmSync(resolve(here, '..', 'e2e-data'), { recursive: true, force: true }); // every run starts with no saved layouts or watchlists
  const python = resolve(root, '.venv', 'Scripts', 'python.exe');
  execFileSync(python, [resolve(root, 'scripts', 'seed_fund_fixture.py'), resolve(here, '..', 'e2e-fund', 'f.duckdb')], { stdio: 'inherit' });
}
