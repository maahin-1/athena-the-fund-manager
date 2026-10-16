import { expect, test, type Page } from '@playwright/test';

const title = (page: Page, text: string) => page.locator('.pt', { hasText: text });
const row = (page: Page, symbol: string) => page.locator(`.wl-row[data-symbol="${symbol}"]`);

test.beforeEach(async ({ page, request }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  // start every test from layout A with nothing saved in the browser
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto('/');
  await expect(row(page, 'SBIN')).toBeVisible();
  await request.delete('/api/layouts/Smoke-1').catch(() => undefined);
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

test('opens with the default panels and a live feed', async ({ page }) => {
  for (const name of ['Watchlist', 'Chart', 'Depth', 'Quote']) await expect(page.locator('.dv-tab', { hasText: name }).first()).toBeVisible();
  await expect(page.locator('.health')).toContainText('Angel live');
  await expect(page.locator('.tape')).toBeVisible();
});

test('watchlist prices change', async ({ page }) => {
  const cell = row(page, 'SBIN').locator('td').nth(1);
  const first = await cell.textContent();
  await expect.poll(async () => cell.textContent(), { timeout: 8000 }).not.toBe(first);
});

test('clicking a row moves linked panels and leaves an unlinked one alone', async ({ page }) => {
  await row(page, 'RELIANCE').click();
  await expect(title(page, 'RELIANCE · ').first()).toBeVisible(); // the chart
  await expect(title(page, 'Depth · RELIANCE')).toBeVisible();
  await expect(title(page, 'Quote · RELIANCE')).toBeVisible();
  // unlink the chart (blue -> none) and pick another symbol: depth follows, the chart stays
  await page.locator('.panel', { has: page.locator('.chart-host') }).locator('.dot').click();
  await row(page, 'TCS').click();
  await expect(title(page, 'Depth · TCS')).toBeVisible();
  await expect(title(page, 'RELIANCE · ').first()).toBeVisible();
});

test('the command line adds a stock, shows a chart and reports a mistake', async ({ page }) => {
  const cmd = page.getByLabel('command line');
  await cmd.fill('add wipro');
  await cmd.press('Enter');
  await expect(row(page, 'WIPRO')).toBeVisible();
  await cmd.fill('infy chart');
  await cmd.press('Enter');
  await expect(title(page, 'INFY · ').first()).toBeVisible();
  await cmd.fill('@@');
  await cmd.press('Enter');
  await expect(page.locator('.statusline.bad')).toContainText('not a symbol');
});

test('presets switch the arrangement and a saved layout comes back', async ({ page }) => {
  const select = page.locator('.layout-select');
  await select.selectOption('preset:B');
  await expect(page.locator('.chart-host')).toHaveCount(4);
  await select.selectOption('preset:A');
  await expect(page.locator('.chart-host')).toHaveCount(1);
  await row(page, 'LUPIN').click(); // a change worth saving
  await page.getByRole('button', { name: 'Save' }).click();
  await page.locator('.namebox').fill('Smoke-1');
  await page.locator('.namebox').press('Enter');
  await expect(page.locator('.statusline')).toContainText('saved layout "Smoke-1"');
  await select.selectOption('preset:B');
  await expect(page.locator('.chart-host')).toHaveCount(4);
  await select.selectOption('saved:Smoke-1');
  await expect(page.locator('.chart-host')).toHaveCount(1);
  await expect(title(page, 'Depth · LUPIN')).toBeVisible();
});

test('the research layout shows stored analysis and the verdict panel from the warehouse', async ({ page }) => {
  await page.locator('.layout-select').selectOption('preset:D');
  await expect(page.locator('.pt', { hasText: 'Analysis · SBIN' })).toBeVisible();
  await expect(page.locator('.content .facts .k', { hasText: 'P/E' })).toBeVisible({ timeout: 10_000 });
  await page.getByRole('button', { name: 'Technicals' }).click();
  await expect(page.locator('.content .facts .k', { hasText: 'rsi 14' })).toBeVisible();
  await page.getByRole('button', { name: 'Fundamentals' }).click();
  await expect(page.locator('.content .group').first()).toBeVisible();
  await expect(page.locator('.pt', { hasText: 'Verdict · SBIN' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Make a (fresh )?verdict/ })).toBeVisible();
  // following the link group: picking a stock moves the analysis too
  await row(page, 'TCS').click();
  await expect(page.locator('.pt', { hasText: 'Analysis · TCS' })).toBeVisible();
});
