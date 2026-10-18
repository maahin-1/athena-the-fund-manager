import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');
const row = (page: Page, symbol: string) => page.locator(`.wl-row[data-symbol="${symbol}"]`);
const order = (page: Page) => page.locator('.wl-row').evaluateAll((rows) => rows.map((r) => (r as HTMLElement).dataset.symbol));

/** The document every test starts from, written to the server so one test's lists never leak into the next. */
const START = {
  version: 1,
  active: 'l1',
  lists: [{
    id: 'l1', name: 'Main', columns: ['last', 'chg', 'chgpct'], sort: { key: null, dir: 1 },
    items: [
      { t: 'section', title: 'Indices' }, { t: 'sym', s: 'NIFTY 50' }, { t: 'sym', s: 'NIFTY BANK' },
      { t: 'section', title: 'Stocks' }, { t: 'sym', s: 'SBIN' }, { t: 'sym', s: 'TCS' }, { t: 'sym', s: 'INFY' },
    ],
  }],
};

/** Open the watchlist menu (if it is not open) and press one of its buttons. */
async function menu(page: Page, name: string | RegExp): Promise<void> {
  if (!(await page.locator('.wl-menu').count())) await page.getByLabel('watchlist menu').click();
  await page.locator('.wl-menu').getByRole('button', { name }).click();
}

async function reset(page: Page): Promise<void> {
  await page.evaluate(async (doc) => {
    window.localStorage.clear();
    await fetch('/api/watchlists', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(doc) });
  }, START);
  await page.reload();
  await expect(row(page, 'SBIN')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.goto('/');
  await reset(page);
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
  await reset(page);
});

test('index rows and the top-bar strip show the exchange snapshot values', async ({ page }) => {
  await expect(row(page, 'NIFTY 50').locator('td[data-col="last"]')).toHaveText(/^\d[\d,]*\.\d\d$/);
  await expect(page.locator('.idxstrip .idx[data-index="NIFTY 50"]')).toContainText('N 50');
  await expect(page.locator('.idxstrip .idx[data-index="INDIA VIX"]')).toBeVisible();
  await expect(row(page, 'NIFTY BANK').locator('td[data-col="chgpct"]')).toHaveText(/%$/);
});

test('a new list, a heading, a flag and a command that adds to a named list all persist across a reload', async ({ page }) => {
  await menu(page, 'New list');
  await page.locator('.wl-input').fill('Banks');
  await page.locator('.wl-input').press('Enter');
  await expect(page.locator('.wl-lists')).toHaveValue('l2');
  await expect(page.locator('.wl-lists option:checked')).toHaveText('Banks');
  await command(page).fill('ADD HDFCBANK TO Banks');
  await command(page).press('Enter');
  await expect(row(page, 'HDFCBANK')).toBeVisible();
  await command(page).fill('ADD SBIN TO Nowhere');
  await command(page).press('Enter');
  await expect(page.locator('.statusline')).toContainText('no watchlist named "Nowhere"');
  await menu(page, '+ Section');
  await page.locator('.wl-input').fill('Private');
  await page.locator('.wl-input').press('Enter');
  await expect(page.locator('.wl-section')).toContainText('Private');
  await row(page, 'HDFCBANK').locator('.wl-flag').click();
  await expect(row(page, 'HDFCBANK').locator('.wl-flag')).toHaveAttribute('data-color', 'red');
  await page.waitForTimeout(700); // the save goes out a moment after the last change
  await page.reload();
  await expect(page.locator('.wl-lists option:checked')).toHaveText('Banks');
  await expect(row(page, 'HDFCBANK').locator('.wl-flag')).toHaveAttribute('data-color', 'red');
  await command(page).fill('LIST Main');
  await command(page).press('Enter');
  await expect(row(page, 'SBIN')).toBeVisible();
  await expect(row(page, 'HDFCBANK')).toHaveCount(0);
});

test('columns can be added and the list sorts by a heading, ascending then descending then back', async ({ page }) => {
  await menu(page, 'Columns');
  await page.getByLabel('Volume').check();
  await expect(page.locator('.wl-th', { hasText: 'Volume' })).toBeVisible();
  await expect(row(page, 'SBIN').locator('td[data-col="volume"]')).toBeVisible();
  await page.locator('.wl-th', { hasText: 'Symbol' }).click();
  await expect(page.locator('.wl-th', { hasText: 'Symbol' })).toContainText('▲');
  expect(await order(page)).toEqual(['INFY', 'NIFTY 50', 'NIFTY BANK', 'SBIN', 'TCS']);
  await page.locator('.wl-th', { hasText: 'Symbol' }).click();
  expect(await order(page)).toEqual(['TCS', 'SBIN', 'NIFTY BANK', 'NIFTY 50', 'INFY']);
  await page.locator('.wl-th', { hasText: 'Symbol' }).click();
  expect(await order(page)).toEqual(['NIFTY 50', 'NIFTY BANK', 'SBIN', 'TCS', 'INFY']);
});

test('dragging a row reorders the list and the order is kept', async ({ page }) => {
  await row(page, 'INFY').dragTo(row(page, 'SBIN'), { targetPosition: { x: 20, y: 2 } }); // upper half: before SBIN
  expect(await order(page)).toEqual(['NIFTY 50', 'NIFTY BANK', 'INFY', 'SBIN', 'TCS']);
  await page.waitForTimeout(700);
  await page.reload();
  await expect(row(page, 'INFY')).toBeVisible();
  expect(await order(page)).toEqual(['NIFTY 50', 'NIFTY BANK', 'INFY', 'SBIN', 'TCS']);
});

test('a heading folds its symbols away and unfolds them', async ({ page }) => {
  await page.locator('.wl-section', { hasText: 'Stocks' }).locator('.wl-title').click();
  await expect(row(page, 'SBIN')).toHaveCount(0);
  await page.locator('.wl-section', { hasText: 'Stocks' }).locator('.wl-title').click();
  await expect(row(page, 'SBIN')).toBeVisible();
});

test('export shows the text form and an import adds known symbols and names the unknown ones', async ({ page }) => {
  await menu(page, 'Import / Export');
  await expect(page.locator('.wl-text')).toHaveValue('###Indices\nNSE:NIFTY 50,NSE:NIFTY BANK\n###Stocks\nNSE:SBIN,NSE:TCS,NSE:INFY');
  await page.locator('.wl-text').fill('###Stocks\nNSE:HDFCBANK,NSE:ZZZNOTASTOCK');
  await page.getByRole('button', { name: 'Import into this list' }).click();
  await expect(row(page, 'HDFCBANK')).toBeVisible();
  await expect(page.locator('.wl-note')).toContainText('added 1');
  await expect(page.locator('.wl-note')).toContainText('ZZZNOTASTOCK');
});

test('removing a symbol keeps the others, and deleting a list asks twice', async ({ page }) => {
  await row(page, 'TCS').hover();
  await row(page, 'TCS').locator('.x').click();
  await expect(row(page, 'TCS')).toHaveCount(0);
  await expect(row(page, 'SBIN')).toBeVisible();
  await menu(page, 'New list');
  await page.locator('.wl-input').fill('Scratch');
  await page.locator('.wl-input').press('Enter');
  await menu(page, 'Delete');
  await expect(page.locator('.wl-menu').getByRole('button', { name: /Sure\?/ })).toBeVisible();
  await menu(page, /Sure\?/);
  await expect(page.locator('.wl-lists option')).toHaveCount(1);
});

test('the Advanced columns show market cap, P/E, ROE and the 1-year return for every stock, and sort', async ({ page }) => {
  await menu(page, 'Columns');
  await page.locator('.wl-pop .wl-presets').getByRole('button', { name: 'Advanced' }).click();
  for (const title of ['Mkt cap (Rs cr)', 'P/E', 'ROE', '1Y']) await expect(page.locator('th.wl-th', { hasText: title })).toBeVisible();
  await expect(row(page, 'SBIN').locator('td[data-col="pe"]')).toHaveText(/^\d+\.\d\d$/);
  await expect(row(page, 'SBIN').locator('td[data-col="mcap"]')).toHaveText(/^[\d,]+$/);
  await expect(row(page, 'SBIN').locator('td[data-col="roe"]')).toHaveText(/^-?\d+\.\d%$/);
  await expect(row(page, 'SBIN').locator('td[data-col="r1y"]')).toHaveText(/^[+-]\d+\.\d\d%$/);
  await expect(row(page, 'NIFTY 50').locator('td[data-col="mcap"]')).toHaveText('—'); // an index has no market cap
  await page.locator('th.wl-th', { hasText: 'P/E' }).click();
  const pes = await page.locator('.wl-row[data-symbol="SBIN"], .wl-row[data-symbol="TCS"], .wl-row[data-symbol="INFY"]').evaluateAll((rows) =>
    rows.map((r) => parseFloat((r.querySelector('td[data-col="pe"]') as HTMLElement).textContent ?? '')),
  );
  const numbers = pes.filter((n) => !Number.isNaN(n));
  expect(numbers.length).toBeGreaterThanOrEqual(2);
  expect(numbers).toEqual([...numbers].sort((a, b) => a - b));
  await menu(page, 'Columns');
  await page.locator('.wl-pop .wl-presets').getByRole('button', { name: 'Basic' }).click();
  await expect(page.locator('th.wl-th', { hasText: 'P/E' })).toHaveCount(0);
});
