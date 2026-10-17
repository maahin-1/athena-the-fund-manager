import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto('/');
  await expect(page.locator('.wl-row[data-symbol="SBIN"]')).toBeVisible();
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors.filter((e) => !e.includes('404'))).toEqual([]);
});

test('the Fundamentals tab shows a stored company with its statements', async ({ page }) => {
  await page.getByLabel('command line').fill('FUND TCS');
  await page.getByLabel('command line').press('Enter');
  const pl = page.locator('[data-section="Profit & Loss"]');
  await expect(pl).toBeVisible();
  await expect(page.locator('.fund .fsym')).toHaveText('TCS');
  await expect(pl).toContainText('2,40,893'); // FY2024 sales in crores, Indian grouping, from the fixture filing
  await expect(page.locator('[data-section="Balance Sheet"]')).toContainText('Total Assets');
  await expect(page.locator('[data-basis="standalone"]')).toBeDisabled();
});

test('a company with nothing stored says how to fetch it', async ({ page }) => {
  await page.getByLabel('command line').fill('FUND RELIANCE');
  await page.getByLabel('command line').press('Enter');
  await expect(page.locator('.fund .note.bad')).toContainText('athena.fundamentals.ingest');
});

test('Export to Excel downloads a workbook', async ({ page }) => {
  await page.getByLabel('command line').fill('FUND TCS');
  await page.getByLabel('command line').press('Enter');
  const link = page.locator('a.fexport');
  await expect(link).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  expect(download.suggestedFilename()).toBe('TCS-consolidated.xlsx');
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  expect(Buffer.concat(chunks).subarray(0, 2).toString()).toBe('PK'); // an .xlsx is a zip file
});

test('F6 reaches the Fundamentals tab from the terminal', async ({ page }) => {
  await page.keyboard.press('F6');
  await expect(page.locator('.vtab.on')).toHaveText('Fundamentals');
});
