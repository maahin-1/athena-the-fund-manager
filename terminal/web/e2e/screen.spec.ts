import { expect, test, type Page } from '@playwright/test';

async function openScreen(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Screen', exact: true }).click();
  await expect(page.locator('.stable .srow').first()).toBeVisible({ timeout: 15_000 });
}

const count = async (page: Page): Promise<number> => {
  const text = (await page.locator('.screen .note').allTextContents()).join(' ');
  const m = text.match(/([\d,]+) match/);
  return m ? Number(m[1].replace(/,/g, '')) : -1;
};

const queryBox = (page: Page) => page.getByLabel('screen query');

test('a typed query filters the stored market, and a mistake says what and where while the table keeps the last good result', async ({ page }) => {
  await openScreen(page);
  const all = await count(page);
  await queryBox(page).fill('pe < 12 and roe > 15');
  await expect.poll(() => count(page)).toBeLessThan(all);
  const narrowed = await count(page);
  expect(narrowed).toBeGreaterThan(0);
  const pes = await page.locator('.stable td:nth-child(7)').allTextContents(); // P/E is the 7th default column
  for (const t of pes.slice(0, 40)) expect(Number(t)).toBeLessThan(12);

  await queryBox(page).fill('pee < 5');
  await expect(page.locator('.qerror')).toContainText('unknown field "pee"');
  await expect(page.locator('.qerror')).toContainText('did you mean "pe"');
  await expect(queryBox(page)).toHaveAttribute('aria-invalid', 'true');
  expect(await count(page)).toBe(narrowed); // not emptied by the typo

  await queryBox(page).fill('');
  await expect(page.locator('.qerror')).toBeHidden();
  await expect.poll(() => count(page)).toBe(all);
});

test('index membership and the exchange industry narrow the screen, for any index and any industry the data holds', async ({ page }) => {
  await openScreen(page);
  const all = await count(page);
  await queryBox(page).fill('index = "nifty 50"');
  await expect.poll(() => count(page)).toBeGreaterThan(40);
  expect(await count(page)).toBeLessThan(60);
  await queryBox(page).fill('');

  const industries = await page.locator('select[aria-label="industry"]').locator('option').allTextContents();
  expect(industries.length).toBeGreaterThan(10); // "Any industry" and the exchange's own list
  await page.locator('select[aria-label="industry"]').selectOption({ index: 2 });
  await expect.poll(() => count(page)).toBeLessThan(all);
  expect(await count(page)).toBeGreaterThan(0);
  const indices = await page.locator('select[aria-label="index"]').locator('option').allTextContents();
  expect(indices.join('|')).toContain('NIFTY 50');
});

test('a screen is saved by name, comes back after a reload with its query and columns, and can be deleted', async ({ page }) => {
  await openScreen(page);
  await queryBox(page).fill('roe > 18 and de < 1');
  await page.locator('.scols summary').click();
  await page.locator('.scols input[data-column="industry"]').check();
  await expect(page.locator('.stable th[data-column="industry"]')).toBeVisible();
  await page.getByLabel('screen name').fill('E2E quality');
  await page.getByRole('button', { name: 'Save screen' }).click();
  await expect(page.locator('.ssaved')).toContainText('Saved "E2E quality"');

  await page.reload();
  await page.getByRole('button', { name: 'Screen', exact: true }).click();
  await expect(page.locator('.stable .srow').first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByLabel('saved screens').locator('option', { hasText: 'E2E quality' })).toHaveCount(1);
  await page.getByLabel('saved screens').selectOption('E2E quality');
  await expect(queryBox(page)).toHaveValue('roe > 18 and de < 1');
  await expect(page.locator('.stable th[data-column="industry"]')).toBeVisible();

  await page.locator('.ssaved').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.locator('.ssaved')).toContainText('Deleted "E2E quality"');
  await expect(page.getByLabel('saved screens').locator('option', { hasText: 'E2E quality' })).toHaveCount(0);
});

test('the screen is exported as a CSV with the shown columns and the filter applied', async ({ page }) => {
  await openScreen(page);
  await queryBox(page).fill('index = "nifty 50" and pe is not null');
  await expect.poll(() => count(page)).toBeGreaterThan(30);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()]);
  expect(download.suggestedFilename()).toMatch(/^screen-\d{4}-\d{2}-\d{2}\.csv$/);
  const text = (await (await download.createReadStream())!.toArray()).map((b) => b.toString()).join('');
  const lines = text.split('\r\n').filter(Boolean);
  expect(lines[0]).toMatch(/^Symbol,Name,Close/);
  expect(lines.length - 1).toBe(await count(page));
});

test('a preset shows the query it stands for', async ({ page }) => {
  await openScreen(page);
  await page.getByRole('button', { name: 'Value', exact: true }).click();
  await expect(page.locator('.presets + .note')).toContainText('As a query: pe between 0 and 15 and pb between 0 and 3 and roe >= 15');
});
