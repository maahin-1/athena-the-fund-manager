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
  await expect(page.locator('.wl-row[data-symbol="SBIN"]')).toBeVisible();
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

async function openLab(page: Page, symbol = 'SBIN'): Promise<void> {
  await command(page).fill(`LAB ${symbol}`);
  await command(page).press('Enter');
  await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText(`${symbol} · 1D`);
}

async function addIndicator(page: Page, id: string): Promise<void> {
  await page.locator('.lab-ind').click();
  await page.locator(`.lab-item[data-indicator="${id}"]`).click();
}

test('the Chart Lab draws the symbol with a data window, and F6 reaches it', async ({ page }) => {
  await openLab(page);
  await expect(page.locator('.lab-window')).toContainText('Close');
  await expect(page.locator('.vtab.on')).toHaveText('Chart Lab');
  await page.locator('.vtab', { hasText: 'Terminal' }).click();
  await page.keyboard.press('F6');
  await expect(page.locator('.vtab.on')).toHaveText('Chart Lab');
});

test('indicators get legends and panes, settings change them, undo and redo walk back', async ({ page }) => {
  await openLab(page);
  await addIndicator(page, 'rsi');
  await addIndicator(page, 'bb');
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['BB 20 2', 'RSI 14']); // legends are grouped by pane: overlays first
  await expect(page.locator('.lab-legend')).toHaveCount(2); // the price pane and the RSI pane
  await expect(page.locator('.lab-window')).toContainText('RSI 14');
  await page.locator('.lab-lrow', { hasText: 'RSI' }).locator('[data-action="settings"]').click();
  await page.locator('[data-input="length"]').fill('21');
  await page.locator('.lab-dialog .btn', { hasText: 'OK' }).click();
  await expect(page.locator('.lab-lrow .lg-name').last()).toHaveText('RSI 21');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.locator('.lab-lrow .lg-name').last()).toHaveText('RSI 14');
  await page.getByRole('button', { name: 'Undo' }).click();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.locator('.lab-lrow')).toHaveCount(0);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['RSI 14']);
});

test('log scale, percent scale, chart types, compare and raw prices all keep the chart alive', async ({ page }) => {
  await openLab(page);
  for (const scale of ['log', 'percent', 'auto']) {
    await page.locator(`[data-scale="${scale}"]`).click();
    await expect(page.locator(`[data-scale="${scale}"]`)).toHaveClass(/on/);
  }
  for (const type of ['hollow', 'bars', 'line', 'area', 'heikin', 'candles']) await page.locator('.lab-type').selectOption(type);
  await page.getByLabel('compare symbol').fill('NIFTY 50');
  await page.getByLabel('compare symbol').press('Enter');
  await expect(page.locator('.lab-note')).not.toContainText('could not load');
  await page.getByRole('button', { name: 'ADJ' }).click();
  await expect(page.getByRole('button', { name: 'ADJ' })).not.toHaveClass(/on/);
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');
  await page.locator('[data-interval="5m"]').click();
  await expect(page.locator('.lab [data-range]')).toHaveText(['1D', '5D', '1M']);
});

test('every chart type and interval draws without an error', async ({ page }) => {
  await openLab(page);
  await addIndicator(page, 'rsi');
  for (const type of ['baseline', 'columns', 'step', 'renko', 'linebreak', 'range', 'heikin', 'candles']) {
    await page.locator('.lab-type').selectOption(type);
    await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  }
  for (const tf of ['1W', '1M', '1D']) {
    await page.locator(`[data-interval="${tf}"]`).click();
    await expect(page.locator(`[data-interval="${tf}"]`)).toHaveClass(/on/);
    await expect(page.locator('.lab-head')).toContainText(`SBIN · ${tf}`);
  }
  await page.locator('.lab [data-range="All"]').click();
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');
});

test('a template saved by name comes back after a reload, with the chart state', async ({ page }) => {
  await openLab(page);
  await addIndicator(page, 'macd');
  page.once('dialog', (d) => void d.accept('E2E set'));
  await page.locator('.lab-templates').selectOption(':save');
  await expect(page.locator('.lab-templates option[value="load:E2E set"]')).toHaveCount(1);
  await page.reload();
  await page.getByLabel('command line').fill('LAB SBIN');
  await page.getByLabel('command line').press('Enter');
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['MACD 12 26 9']); // the chart state itself was kept
  await page.locator('[data-action="remove"]').click();
  await page.locator('.lab-templates').selectOption('load:E2E set');
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['MACD 12 26 9']);
});

test('a snapshot downloads a PNG picture', async ({ page }) => {
  await openLab(page);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Snapshot' }).click()]);
  expect(download.suggestedFilename()).toBe('SBIN-1D.png');
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  expect([...Buffer.concat(chunks).subarray(0, 4)]).toEqual([0x89, 0x50, 0x4e, 0x47]);
});
