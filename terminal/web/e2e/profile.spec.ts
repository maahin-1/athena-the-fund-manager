import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

test('the volume profile is off, turned on with its point of control and value area in the data window, kept after a reload, and turned off', async ({ page }) => {
  await expect(page.getByLabel('volume profile')).not.toHaveClass(/on/);
  await expect(page.getByLabel('profile rows')).toBeHidden();
  await expect(page.locator('.lab-window')).not.toContainText('Point of control');

  await page.getByLabel('volume profile').click();
  await expect(page.getByLabel('volume profile')).toHaveClass(/on/);
  await expect(page.locator('.lab-window')).toContainText('Point of control');
  await expect(page.locator('.lab-window')).toContainText('Value area high');
  await expect(page.locator('.lab-window')).toContainText('Value area low');
  await expect(page.locator('.lab-window')).toContainText('24 rows');
  const poc = await page.locator('.lab-window .lab-wrow', { hasText: 'Point of control' }).innerText();

  await page.getByLabel('profile rows').selectOption('48');
  await expect(page.locator('.lab-window')).toContainText('48 rows');

  // zooming to the last 1M changes the bars in view, so the profile is worked out again from them
  await page.locator('.lab-sub [data-range="1M"]').click();
  await expect(page.locator('.lab-window')).toContainText('Point of control');
  const pocMonth = await page.locator('.lab-window .lab-wrow', { hasText: 'Point of control' }).innerText();
  expect(pocMonth).not.toBe(poc);

  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.getByLabel('volume profile')).toHaveClass(/on/); // kept with the chart
  await expect(page.getByLabel('profile rows')).toHaveValue('48');
  await expect(page.locator('.lab-window')).toContainText('Point of control');

  await page.getByLabel('volume profile').click();
  await expect(page.locator('.lab-window')).not.toContainText('Point of control');
  await expect(page.getByLabel('profile rows')).toBeHidden();
});

test('it says why there is none for a chart of price moves, and draws again on the next chart type', async ({ page }) => {
  await page.getByLabel('volume profile').click();
  await expect(page.locator('.lab-window')).toContainText('Point of control');
  await page.locator('.lab-type').selectOption('renko');
  await expect(page.locator('.lab-window')).toContainText('Not drawn for this chart type');
  await page.locator('.lab-type').selectOption('candles');
  await expect(page.locator('.lab-window')).toContainText('Point of control');
});
