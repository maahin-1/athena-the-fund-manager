import { expect, test } from '@playwright/test';

test('Explain gives the facts sheet worked out in code, and says no model was used when none is available', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.getByLabel('command line').fill('LAB SBIN');
  await page.getByLabel('command line').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN');
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: 'Explain', exact: true }).click();
  const box = page.locator('.lab-explain');
  await expect(box).toBeVisible();
  await expect(box.locator('.explain-fact').first()).toBeVisible({ timeout: 30_000 });
  await expect(box).toContainText('Chart reading: SBIN');
  await expect(box).toContainText('no model is available'); // the test server runs with the model switched off
  await expect(box.locator('.explain-fact', { hasText: 'last close' }).first()).toContainText('rupees');
  await expect(box.locator('.explain-fact', { hasText: 'RSI (14)' })).toHaveCount(1);
  await box.getByRole('button', { name: 'Close' }).click();
  await expect(box).toBeHidden();
  expect(errors).toEqual([]);
});
