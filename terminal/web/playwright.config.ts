import { defineConfig } from '@playwright/test';

// The smoke test runs the real server with its fake feed, against the built page and a throw-away layouts folder and a small fundamentals store built from the XBRL test fixtures.
export default defineConfig({
  testDir: 'e2e',
  globalSetup: './e2e/seed-fund.ts',
  timeout: 45_000,
  retries: 0,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8701', channel: 'chrome', viewport: { width: 1600, height: 900 } },
  webServer: {
    command: '..\\target\\debug\\athena-terminal.exe --fake-feed --port 8701 --web-dir dist --warehouse ..\\..\\.athena\\warehouse.duckdb --layouts-dir e2e-data\\layouts --fund-store e2e-fund\\f.duckdb --fund-python ..\\..\\.venv\\Scripts\\python.exe --indicators-dir e2e\\indicators --indicator-runner ..\\..\\src\\athena\\indicators_user\\runner.py',
    url: 'http://127.0.0.1:8701/',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
