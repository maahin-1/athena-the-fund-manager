import { expect as baseExpect, test, type Page } from '@playwright/test';

// the charts' daily bars come from a real server that is sometimes busy: a load takes seconds, not milliseconds
const expect = baseExpect.configure({ timeout: 15_000 });
test.setTimeout(90_000);

const command = (page: Page) => page.getByLabel('command line');
const IST = 19_800;
const cell = (page: Page, i: number) => page.locator(`.lab-cell[data-cell="${i}"]`);
const head = (page: Page, i: number) => cell(page, i).locator('.lab-head');
const OPEN = Date.UTC(2026, 9, 13, 9, 15) / 1000 - IST;

/** Intraday bars of the given width for 40 sessions ending 14 October 2026, as the server would send them. */
function intraday(minutes: number) {
  const bars: { t: number; o: number; h: number; l: number; c: number; v: number }[] = [];
  for (let day = 0; day < 40; day++) {
    const base = OPEN + (day - 39) * 86_400 + 86_400; // the sessions are consecutive days: weekends do not matter to the stub
    for (let m = 0; m + minutes <= 375; m += minutes) {
      const c = 100 + Math.sin((day * 375 + m) / 90) * 4;
      bars.push({ t: base + m * 60, o: c, h: c + 0.4, l: c - 0.4, c, v: 100 });
    }
  }
  return bars;
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.route('**/api/bars?*', (route) => {
    const tf = new URL(route.request().url()).searchParams.get('tf') ?? '';
    const minutes: Record<string, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60 };
    return minutes[tf] ? route.fulfill({ json: { bars: intraday(minutes[tf]) } }) : route.fallback();
  });
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await command(page).fill('GRID');
  await command(page).press('Enter');
  await expect(page.locator('.vtab.on')).toHaveText('Grid');
  await expect(head(page, 3)).toContainText('· 15m');
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

test('the grid opens as four charts of one symbol at 1D, 1W, 1h and 15m, each drawn', async ({ page }) => {
  await expect(page.locator('.lab-cell:visible')).toHaveCount(4);
  for (const [i, tf] of ['1D', '1W', '1h', '15m'].entries()) {
    await expect(head(page, i)).toContainText(`SBIN · ${tf}`);
    await expect(cell(page, i).locator('canvas').first()).toBeVisible();
  }
  await expect(cell(page, 0).locator('.lab-window')).toHaveCount(0); // compact: no data window, no drawing rail
  await expect(page.getByLabel('sync symbol')).toBeChecked();
  await expect(page.getByLabel('sync crosshair')).toBeChecked();
  await expect(page.getByLabel('sync interval')).not.toBeChecked();
  await expect(page.getByLabel('sync time')).not.toBeChecked();
});

test('the Interval switch keeps the charts in step, and while it is off each chart has its own interval', async ({ page }) => {
  await cell(page, 0).locator('[data-interval="1W"]').click();
  await expect(head(page, 0)).toContainText('· 1W');
  await expect(head(page, 2)).toContainText('· 1h'); // off: the others did not move

  await page.getByLabel('sync interval').check();
  await cell(page, 1).locator('[data-interval="15m"]').click();
  for (const i of [0, 1, 2, 3]) await expect(head(page, i)).toContainText('· 15m');

  await cell(page, 2).getByLabel('custom interval').fill('7m');
  await cell(page, 2).getByLabel('custom interval').press('Enter');
  for (const i of [0, 1, 2, 3]) await expect(head(page, i)).toContainText('· 7m'); // a typed interval too
});

test('the Symbol switch shows a symbol picked in one chart in all, and while it is off a symbol stays in its chart', async ({ page }) => {
  await cell(page, 2).locator('.lab-symbol').fill('TCS');
  await cell(page, 2).locator('.lab-symbol').press('Enter');
  for (const i of [0, 1, 2, 3]) await expect(head(page, i)).toContainText('TCS ·');

  await page.getByLabel('sync symbol').uncheck();
  await cell(page, 0).locator('.lab-symbol').fill('INFY');
  await cell(page, 0).locator('.lab-symbol').press('Enter');
  await expect(head(page, 0)).toContainText('INFY ·');
  await expect(head(page, 1)).toContainText('TCS ·');
});

test('a layout shows the cells it has and keeps the symbols; the settings and charts are kept after a reload', async ({ page }) => {
  await page.getByLabel('grid layout').selectOption('2h');
  await expect(page.locator('.lab-cell:visible')).toHaveCount(2);
  await cell(page, 0).locator('.lab-symbol').fill('TCS');
  await cell(page, 0).locator('.lab-symbol').press('Enter');
  await expect(head(page, 1)).toContainText('TCS ·');
  await page.getByLabel('sync time').check();
  await page.getByLabel('sync crosshair').uncheck();

  await page.getByLabel('grid layout').selectOption('2x2');
  await expect(page.locator('.lab-cell:visible')).toHaveCount(4);
  await expect(head(page, 3)).toContainText('TCS ·'); // a chart brought back joins the others' symbol

  await page.getByLabel('grid layout').selectOption('1+2');
  await expect(page.locator('.lab-cell:visible')).toHaveCount(3);

  await page.reload();
  await command(page).fill('GRID');
  await command(page).press('Enter');
  await expect(page.getByLabel('grid layout')).toHaveValue('1+2');
  await expect(page.locator('.lab-cell:visible')).toHaveCount(3);
  await expect(page.getByLabel('sync time')).toBeChecked();
  await expect(page.getByLabel('sync crosshair')).not.toBeChecked();
  await expect(head(page, 0)).toContainText('TCS · 1D', { timeout: 15_000 });
  await expect(head(page, 2)).toContainText('TCS · 1h', { timeout: 15_000 });
});

test('the Crosshair switch shows the pointer of one chart as a crosshair in the others', async ({ page }) => {
  await expect(head(page, 1)).toContainText(/· 1W.*C\d/); // loaded
  await expect(head(page, 0)).toContainText(/· 1D.*C\d/);
  const before = (await head(page, 1).textContent()) ?? '';
  const box = (await cell(page, 0).locator('.lab-chart').boundingBox())!;
  await page.mouse.move(box.x + 120, box.y + box.height / 2);
  await page.mouse.move(box.x + 140, box.y + box.height / 2);
  await expect(head(page, 1)).not.toHaveText(before); // the weekly chart shows the bar of that date, not its last
  const during = (await head(page, 1).textContent()) ?? '';
  await page.mouse.move(box.x + box.width + 400, box.y - 5); // leave the chart
  await expect(head(page, 1)).toHaveText(before); // and the others go back to their last bar

  await page.getByLabel('sync crosshair').uncheck();
  await page.mouse.move(box.x + 120, box.y + box.height / 2);
  await page.mouse.move(box.x + 140, box.y + box.height / 2);
  await page.waitForTimeout(400);
  await expect(head(page, 1)).toHaveText(before); // switched off: nothing follows
  expect(during).not.toBe(before);
});
