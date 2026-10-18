import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.locator('input[placeholder*="symbol or command"]');

async function openLab(page: Page, symbol = 'SBIN'): Promise<void> {
  await page.goto('/');
  await command(page).fill(`LAB ${symbol}`);
  await command(page).press('Enter');
  await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText(`${symbol} · 1D`);
}

async function newFormula(page: Page, name: string, text: string, pane = 'own'): Promise<void> {
  await page.locator('.lab-ind').click();
  await page.locator('.lab-item[data-action="new-formula"]').click();
  await page.getByLabel('formula name').fill(name);
  await page.getByLabel('formula pane').selectOption(pane);
  await page.getByLabel('formula text').fill(text);
  await page.locator('.lab-formula').getByRole('button', { name: 'Save', exact: true }).click();
}

async function deleteFormula(page: Page, name: string): Promise<void> {
  await page.locator('.lab-ind').click();
  const del = page.getByLabel(`delete ${name}`);
  await del.click();
  await del.click(); // a second click confirms
}

test('a formula is written, drawn in its own pane, kept after a reload and deleted', async ({ page }) => {
  await openLab(page);
  await newFormula(page, 'E2E spread', 'sma(20) - sma(50)');
  await expect(page.locator('.lab-legend', { hasText: 'E2E spread' })).toHaveCount(1);
  await expect(page.locator('.lab-window')).toContainText('E2E spread'); // its value is in the data window
  const panes = await page.locator('.lab-host table tr').count();
  expect(panes).toBeGreaterThanOrEqual(3); // price, volume and its own pane

  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-legend', { hasText: 'E2E spread' })).toHaveCount(1); // the saved chart came back with it
  await page.locator('.lab-ind').click();
  await expect(page.locator('.lab-item[data-indicator="f:E2E spread"]')).toBeVisible();
  await page.keyboard.press('Escape');

  await deleteFormula(page, 'E2E spread');
  await expect(page.locator('.lab-legend', { hasText: 'E2E spread' })).toHaveCount(0);
  await page.locator('.lab-ind').click();
  await expect(page.locator('.lab-item[data-indicator="f:E2E spread"]')).toHaveCount(0);
});

test('the editor says what is wrong with a formula, and where, before it is saved', async ({ page }) => {
  await openLab(page);
  await page.locator('.lab-ind').click();
  await page.locator('.lab-item[data-action="new-formula"]').click();
  await page.getByLabel('formula name').fill('E2E broken');
  await page.getByLabel('formula text').fill('smaa(20) > 1');
  await expect(page.locator('.lab-formula .note.bad').first()).toContainText('unknown function "smaa"; did you mean "sma"?');
  await page.locator('.lab-formula').getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.locator('.lab-formula')).toContainText('Fix the formula first');
  await page.getByLabel('formula text').fill('sma(20) > 1');
  await expect(page.locator('.lab-formula .note.ok')).toContainText('yes-or-no');
  await page.locator('.lab-formula').getByRole('button', { name: 'Cancel' }).click();
  await expect(page.locator('.lab-formula')).toHaveCount(0);
  await page.locator('.lab-ind').click();
  await expect(page.locator('.lab-item[data-indicator="f:E2E broken"]')).toHaveCount(0); // nothing was saved
});

test('a formula on the price chart, a yes-or-no formula, and an edit that changes the numbers', async ({ page }) => {
  await openLab(page);
  await newFormula(page, 'E2E mid', '(high + low) / 2', 'overlay');
  await newFormula(page, 'E2E up', 'close > close[1]');
  await expect(page.locator('.lab-legend', { hasText: 'E2E mid' })).toHaveCount(1);
  await expect(page.locator('.lab-legend', { hasText: 'E2E up' })).toHaveCount(1);
  const before = await page.locator('.lab-window').textContent();

  await page.locator('.lab-ind').click();
  await page.getByLabel('edit E2E mid').click();
  await expect(page.getByLabel('formula name')).toHaveAttribute('readonly', '');
  await page.getByLabel('formula text').fill('(high + low) / 2 + 1');
  await page.locator('.lab-formula').getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(async () => (await page.locator('.lab-window').textContent()) !== before).toBe(true); // the numbers changed

  await deleteFormula(page, 'E2E mid');
  await deleteFormula(page, 'E2E up');
  await expect(page.locator('.lab-legend', { hasText: 'E2E' })).toHaveCount(0);
});
