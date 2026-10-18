import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');
const panel = (page: Page) => page.locator('.panel:has(.det)');
const LABELS = ['Strong sell', 'Sell', 'Neutral', 'Buy', 'Strong buy'];

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await expect(page.locator('.wl-row[data-symbol="SBIN"]')).toBeVisible();
});

test.afterEach(async ({ page }) => {
  // a missing symbol is a 404, and the test that withholds prices on purpose gets the browser's note about its 503
  expect((page as unknown as { errors: string[] }).errors.filter((e) => !e.includes('404') && !e.includes('503'))).toEqual([]);
});

/** The counts of the summary row: sell, neutral, buy. */
async function summaryTotal(page: Page): Promise<number> {
  const cells = await panel(page).locator('.tech-row', { hasText: /^Summary/ }).locator('span').allTextContents();
  return cells.slice(1, 4).reduce((a, b) => a + Number(b), 0);
}

test('the layout A has a Details panel under the watchlist, and the command line brings it forward on a stock', async ({ page }) => {
  await expect(page.locator('.dv-tab', { hasText: 'Details' })).toBeVisible();
  await command(page).fill('DETAILS TCS');
  await command(page).press('Enter');
  await expect(panel(page).locator('.det-name b')).toHaveText('TCS');
  await expect(page.locator('.dv-tab', { hasText: 'Details · TCS' })).toBeVisible();
  await command(page).fill('INFY DETAILS');
  await command(page).press('Enter');
  await expect(panel(page).locator('.det-name b')).toHaveText('INFY');
});

test('Details shows key facts, a performance table, the technicals dial and seasonals for a stock', async ({ page }) => {
  await command(page).fill('DETAILS SBIN');
  await command(page).press('Enter');
  const p = panel(page);
  await expect(p.locator('.det-name .det-full')).toContainText('State Bank');
  await expect(p.locator('.facts').first()).toContainText('Market cap');
  await expect(p.locator('.facts').first()).toContainText('ISIN');
  await expect(p.locator('.perf-row')).toHaveCount(8);
  await expect(p.locator('.perf-row').filter({ hasText: '1Y' }).locator('.v')).toHaveText(/^[+-]\d/);
  const label = await p.locator('.gauge-label').textContent();
  expect(LABELS).toContain(label);
  await expect(p.locator('svg.dial')).toBeVisible();
  await expect(p.locator('svg.seas path.yr').first()).toBeVisible();
  expect(await p.locator('svg.seas path.yr').count()).toBeGreaterThanOrEqual(3);
  await expect(p.locator('.month')).toHaveCount(12);
});

test('switching the technicals timeframe changes how much history the averages have', async ({ page }) => {
  await command(page).fill('DETAILS SBIN');
  await command(page).press('Enter');
  await expect(panel(page).locator('.gauge-label')).toBeVisible();
  const daily = await summaryTotal(page);
  await panel(page).locator('.det-frames .btn', { hasText: '1M' }).click();
  await expect(panel(page).locator('.det-frames .btn.on')).toHaveText('1M');
  const monthly = await summaryTotal(page);
  expect(monthly).toBeGreaterThan(10);
  expect(monthly).toBeLessThan(daily); // monthly bars are too few for the 200-bar averages
});

test('an index gets live facts and the same performance table', async ({ page }) => {
  await command(page).fill('NIFTY 50 DETAILS');
  await command(page).press('Enter');
  const p = panel(page);
  await expect(p.locator('.det-name b')).toHaveText('NIFTY 50');
  await expect(p.locator('.facts').first()).toContainText('Index');
  await expect(p.locator('.perf-row')).toHaveCount(8);
  await expect(p.locator('.perf-row').filter({ hasText: '1Y' }).locator('.v')).toHaveText(/^[+-]\d/);
});

test('a shut section stays shut after a reload', async ({ page }) => {
  await command(page).fill('DETAILS SBIN');
  await command(page).press('Enter');
  const p = panel(page);
  await p.locator('.det-toggle', { hasText: 'Performance' }).click();
  await expect(p.locator('.det-perf .det-body')).toBeHidden();
  await page.waitForTimeout(1400); // the layout is saved a moment after a change
  await page.reload();
  await expect(page.locator('.wl-row[data-symbol="SBIN"]')).toBeVisible();
  await page.locator('.dv-tab', { hasText: 'Details' }).click();
  await expect(panel(page).locator('.det-perf .det-body')).toBeHidden();
  await expect(panel(page).locator('.det-facts .det-body')).toBeVisible();
});

test('when prices cannot be fetched the sections say so plainly and draw nothing', async ({ page }) => {
  await page.route('**/api/bars?symbol=TCS&tf=1D*', (route) => route.fulfill({ status: 503, body: 'busy' }));
  await command(page).fill('DETAILS TCS');
  await command(page).press('Enter');
  const p = panel(page);
  for (const key of ['perf', 'tech', 'seas']) await expect(p.locator(`.det-${key} .note`).first()).toContainText('prices are not available right now');
  await expect(p.locator('svg.dial')).toHaveCount(0);
  await expect(p.locator('.perf-row')).toHaveCount(0);
  await expect(p.locator('.facts').first()).toContainText('Market cap'); // the facts do not depend on the bars
});
