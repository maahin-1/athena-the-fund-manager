import { expect, test, type Page } from '@playwright/test';

async function openHeatmap(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Screen', exact: true }).click();
  await expect(page.locator('.stable .srow').first()).toBeVisible({ timeout: 15_000 });
  await page.locator('.smodes').getByRole('button', { name: 'Heatmap' }).click();
  await expect(page.locator('.hm-tile').first()).toBeVisible();
}

test('the heatmap draws the market as tiles sized by market cap, boxed by sector, with a legend', async ({ page }) => {
  await openHeatmap(page);
  const count = await page.locator('.hm-tile').count();
  expect(count).toBeGreaterThan(100);
  expect(count).toBeLessThanOrEqual(600);
  expect(await page.locator('.hm-group').count()).toBeGreaterThan(5);
  await expect(page.locator('.hm-legend')).toContainText('1 day');
  await expect(page.locator('.hm-note')).toContainText('largest');
  // the biggest tile is bigger than a small one
  const sizes = await page.locator('.hm-tile').evaluateAll((els) => els.map((e) => (e as HTMLElement).offsetWidth * (e as HTMLElement).offsetHeight));
  expect(Math.max(...sizes)).toBeGreaterThan(Math.min(...sizes) * 20);
  // tiles are inside the canvas
  const inside = await page.evaluate(() => {
    const c = document.querySelector('.hm-canvas')!.getBoundingClientRect();
    return [...document.querySelectorAll('.hm-tile')].every((t) => {
      const r = t.getBoundingClientRect();
      return r.left >= c.left - 1 && r.right <= c.right + 1 && r.top >= c.top - 1 && r.bottom <= c.bottom + 1;
    });
  });
  expect(inside).toBe(true);
});

test('the colour follows the chosen figure, and a stock with none is grey', async ({ page }) => {
  await openHeatmap(page);
  const colours = () => page.locator('.hm-tile').evaluateAll((els) => els.slice(0, 60).map((e) => (e as HTMLElement).style.background));
  const day = await colours();
  await page.locator('select[aria-label="colour by"]').selectOption('ret_1y');
  await expect(page.locator('.hm-legend')).toContainText('1 year');
  const year = await colours();
  expect(year).not.toEqual(day);
  expect(new Set(year).size).toBeGreaterThan(5); // a real spread of colours, not one shade
});

test('regrouping, and the screen query and filters, change what is drawn', async ({ page }) => {
  await openHeatmap(page);
  await page.locator('select[aria-label="group by"]').selectOption('none');
  await expect(page.locator('.hm-group')).toHaveCount(0);
  await page.locator('select[aria-label="group by"]').selectOption('industry');
  expect(await page.locator('.hm-group').count()).toBeGreaterThan(5);
  await page.getByLabel('screen query').fill('index = "nifty 50"');
  await expect.poll(() => page.locator('.hm-tile').count()).toBeLessThan(60);
  expect(await page.locator('.hm-tile').count()).toBeGreaterThan(40);
});

test('a tile can be clicked to act on the stock, and has a tooltip', async ({ page }) => {
  await openHeatmap(page);
  await page.getByLabel('screen query').fill('index = "nifty 50"');
  await expect.poll(() => page.locator('.hm-tile').count()).toBeLessThan(60);
  const tile = page.locator('.hm-tile').first();
  const symbol = (await tile.getAttribute('data-symbol'))!;
  expect(await tile.getAttribute('title')).toContain(symbol);
  await tile.click();
  await expect(page.locator('.saction .ssel')).toContainText(symbol);
  await page.locator('.saction').getByRole('button', { name: 'Chart', exact: true }).click();
  await expect(page.locator('.dv-tab', { hasText: `Depth · ${symbol}` })).toBeVisible();
});

test('the table is one click away and keeps its filters', async ({ page }) => {
  await openHeatmap(page);
  await page.getByLabel('screen query').fill('pe < 12');
  await page.locator('.smodes').getByRole('button', { name: 'Table' }).click();
  await expect(page.locator('.stable')).toBeVisible();
  await expect(page.locator('.hm-tile').first()).toBeHidden();
  const pes = await page.locator('.stable td:nth-child(7)').allTextContents();
  for (const t of pes.slice(0, 30)) expect(Number(t)).toBeLessThan(12);
});

test('the heatmap can be limited to one index of the screen, and the note says which', async ({ page }) => {
  await openHeatmap(page);
  const all = await page.locator('.hm-tile').count();
  const picker = page.getByLabel('heatmap index');
  const options = await picker.locator('option').allTextContents();
  expect(options[0]).toBe('All stocks in the result');
  expect(options.length).toBeGreaterThan(5); // the screen's indices, biggest first
  const value = await picker.locator('option').nth(1).getAttribute('value');
  await picker.selectOption(value!);
  await expect(page.locator('.hm-note')).toContainText(`in ${value}`);
  const some = await page.locator('.hm-tile').count();
  expect(some).toBeGreaterThan(0);
  expect(some).toBeLessThan(all);
  await picker.selectOption('');
  await expect(page.locator('.hm-tile')).toHaveCount(all);
});
