import { expect, test, type Page } from '@playwright/test';

async function openBoard(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Screen', exact: true }).click();
  await page.locator('button[data-mode="fno"]').click();
  await expect(page.locator('.fno-grid tr[data-symbol]').first()).toBeVisible({ timeout: 20_000 }); // the first answer starts a Python helper
}

test('the F&O board lists the underlyings with futures on the latest day, filters by what the detectors found, and sorts', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await openBoard(page);
  await expect(page.locator('.fno-note')).toContainText('4 underlyings with futures on 2026-03-11');
  const rows = page.locator('.fno-grid tr[data-symbol]');
  await expect(rows).toHaveCount(4);
  await expect(page.locator('tr[data-symbol="NIFTY"]')).toContainText('(index)');
  await expect(page.locator('tr[data-symbol="RISER"]')).toContainText('Long build-up');
  await expect(page.locator('tr[data-symbol="FALLER"]')).toContainText('Short build-up');
  await expect(page.locator('tr[data-symbol="QUIET"]')).toContainText('—');

  await page.locator('button[data-event="long_build_up"]').click();
  await expect(rows).toHaveCount(2); // the stock and the index both had a long build-up
  await expect(page.locator('tr[data-symbol="RISER"]')).toBeVisible();
  await page.locator('button[data-event="long_build_up"]').click();
  await expect(rows).toHaveCount(4);

  await page.getByLabel('kind of underlying').selectOption('index');
  await expect(rows).toHaveCount(1);
  await page.getByLabel('kind of underlying').selectOption('');
  await page.getByLabel('search the board').fill('fall');
  await expect(rows).toHaveCount(1);
  await page.getByLabel('search the board').fill('');

  await page.locator('th[data-column="price_change"]').click();
  await expect(rows.first()).toHaveAttribute('data-symbol', 'RISER'); // largest rise first
  await page.locator('th[data-column="price_change"]').click();
  await expect(rows.first()).toHaveAttribute('data-symbol', 'FALLER');

  await rows.first().click();
  await expect(page.locator('.ssel')).toContainText('FALLER');
  expect(errors).toEqual([]);
});
