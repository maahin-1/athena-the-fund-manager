import { expect as baseExpect, test, type Page } from '@playwright/test';
import { expiryIn, FIXTURE_EXPIRIES } from './derivs-fixture';

const expect = baseExpect.configure({ timeout: 15_000 });
test.setTimeout(90_000);

const command = (page: Page) => page.getByLabel('command line');
const stat = (page: Page, key: string) => page.locator(`[data-stat="${key}"] .v`);

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors.filter((e) => !/favicon|Failed to load resource/.test(e))).toEqual([]);
});

async function open(page: Page, text = 'OPTIONS') {
  await command(page).fill(text);
  await command(page).press('Enter');
  await expect(page.locator('.opt-page')).toBeVisible();
  await expect(page.locator('tr.opt-row').first()).toBeVisible();
}

test('the Options tab opens on NIFTY with its first expiry, the chain around the money and the synthetic badge', async ({ page }) => {
  await open(page);
  await expect(page.getByLabel('underlying')).toHaveValue('NIFTY');
  await expect(page.getByLabel('expiry', { exact: true })).toHaveValue(expiryIn(FIXTURE_EXPIRIES[0]).iso);
  await expect(page.locator('.opt-synth')).toHaveText('synthetic prices');
  expect(await page.locator('tr.opt-row').count()).toBeGreaterThan(25); // 20 strikes each side of the money, fewer where the list ends
  await expect(page.locator('tr.atm')).toHaveCount(1);
  for (const k of ['Spot', 'Forward', 'ATM', 'ATM IV', 'PCR (OI)', 'Max pain']) await expect(stat(page, k)).not.toHaveText('–');
  await expect(stat(page, 'Lot')).toHaveText('65');
});

test('the smile and the term curve are drawn, and the futures list shows the three expiries with their basis', async ({ page }) => {
  await open(page);
  await expect(page.locator('svg[aria-label="Implied volatility by strike"] polyline.opt-line')).toBeVisible();
  await expect(page.locator('svg[aria-label="At-the-money implied volatility by expiry"] .opt-line')).toBeVisible();
  const rows = page.locator('.opt-futtable tr[data-expiry]');
  await expect(rows).toHaveCount(3);
  await expect(rows.first().locator('td').nth(5)).not.toHaveText('–');
});

test('another expiry gives another chain with more days to go', async ({ page }) => {
  await open(page);
  const first = Number(await stat(page, 'Days').textContent());
  await page.getByLabel('expiry', { exact: true }).selectOption(expiryIn(FIXTURE_EXPIRIES[2]).iso);
  await expect(async () => expect(Number(await stat(page, 'Days').textContent())).toBeGreaterThan(first + 30)).toPass();
});

test('Greeks columns appear on request and the strikes shown can be widened', async ({ page }) => {
  await open(page);
  await expect(page.locator('td[data-col="delta"]')).toHaveCount(0);
  await page.getByLabel('show Greeks').check();
  await expect(page.locator('td[data-col="delta"]').first()).toBeVisible();
  await page.getByLabel('strikes shown').selectOption('all');
  await expect(page.locator('tr.opt-row')).toHaveCount(51);
});

test('a short straddle built from the chain has two break-evens and unlimited loss', async ({ page }) => {
  await open(page);
  const atm = page.locator('tr.atm');
  await atm.locator('td[data-side="call"][data-col="ltp"]').click({ modifiers: ['Shift'] });
  await atm.locator('td[data-side="put"][data-col="ltp"]').click({ modifiers: ['Shift'] });
  await expect(page.locator('.opt-legs tr')).toHaveCount(2);
  await expect(page.locator('[data-key="Max loss"] .v')).toHaveText('unlimited');
  await expect(page.locator('[data-key="Net premium"] .v')).toContainText('received');
  await expect(page.locator('[data-key="Break-even"] .v')).toContainText(' · ');
  await expect(page.locator('svg[aria-label="Profit and loss of the position at expiry and now"] polyline.opt-line')).toHaveCount(2);
  await page.getByLabel('clear legs').click();
  await expect(page.locator('.opt-legs')).toHaveCount(0);
});

test('the commands NIFTY OPTIONS and OPTIONS GOLD pick the underlying, and an underlying with no options says so', async ({ page }) => {
  await open(page, 'GOLD OPTIONS');
  await expect(page.getByLabel('underlying')).toHaveValue('GOLD');
  await expect(stat(page, 'Lot')).toHaveText('1');
  await command(page).fill('OPTIONS NIFTY');
  await command(page).press('Enter');
  await expect(page.getByLabel('underlying')).toHaveValue('NIFTY');
  const input = page.getByLabel('underlying');
  await input.fill('RELIANCE');
  await input.press('Enter');
  await expect(page.locator('.opt-note')).toContainText('RELIANCE has no option contracts');
});

test('F6 reaches the Options tab from the Grid', async ({ page }) => {
  await command(page).fill('GRID');
  await command(page).press('Enter');
  await expect(page.locator('.lab-cell').first()).toBeVisible();
  await page.keyboard.press('F6');
  await expect(page.locator('.opt-page')).toBeVisible();
});
