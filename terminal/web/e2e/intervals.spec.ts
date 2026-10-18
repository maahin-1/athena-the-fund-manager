import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');
const IST = 19_800;

/** Two sessions of 1-minute bars (Tuesday 13 and Wednesday 14 October 2026), as the server would send them. */
function minuteBars() {
  const bars: { t: number; o: number; h: number; l: number; c: number; v: number }[] = [];
  for (const day of [13, 14]) {
    const open = Date.UTC(2026, 9, day, 9, 15) / 1000 - IST;
    for (let m = 0; m < 375; m++) {
      const c = 100 + Math.sin(m / 20) * 3 + (day - 13);
      bars.push({ t: open + m * 60, o: c, h: c + 0.5, l: c - 0.5, c, v: 100 });
    }
  }
  return bars;
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/bars?*tf=1m*', (route) => route.fulfill({ json: { bars: minuteBars() } }));
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');
});

test('an interval typed in the box is shown, kept as a button, and its ranges follow the source', async ({ page }) => {
  await page.getByLabel('custom interval').fill('7m');
  await page.getByLabel('custom interval').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN · 7m');
  await expect(page.locator('button.rbtn.on[data-interval="7m"]')).toHaveCount(1);
  await expect(page.locator('.lab-sub [data-range]')).toHaveText(['1D', '5D']); // the free 1-minute source holds 5 days
  await expect(page.locator('.lab-note')).toContainText('built from 1m bars');
  // 750 one-minute bars in seven-minute groups from 09:15: 54 bars a day, the last of 4 minutes
  await expect(page.locator('.lab-window')).toContainText('2026-10-14 15:26'); // 53 full bars of seven minutes end at 15:26, and the last 4 minutes of the session are a bar of their own
  await expect(page.locator('.lab-window')).toContainText('Volume400');

  await page.getByLabel('custom interval').fill('2h');
  await page.getByLabel('custom interval').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN · 2h');
  await expect(page.locator('button.rbtn[data-interval="7m"]')).toHaveCount(1); // the last few typed stay as buttons
  await expect(page.locator('button.rbtn.on[data-interval="2h"]')).toHaveCount(1);
  await page.locator('button.rbtn[data-interval="7m"]').click();
  await expect(page.locator('.lab-head')).toContainText('SBIN · 7m');

  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('button.rbtn[data-interval="7m"]')).toHaveCount(1); // kept for the next visit
  await expect(page.locator('button.rbtn[data-interval="2h"]')).toHaveCount(1);
});

test('a built-in interval typed in any spelling selects its own button, and a wrong one is refused with the reason', async ({ page }) => {
  await page.getByLabel('custom interval').fill('60m');
  await page.getByLabel('custom interval').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1h');
  await expect(page.locator('.ranges button.rbtn.on[data-interval="1h"]')).toHaveCount(1);
  await expect(page.locator('button.rbtn[data-interval="60m"]')).toHaveCount(0);

  await page.getByLabel('custom interval').fill('500m');
  await page.getByLabel('custom interval').press('Enter');
  await expect(page.locator('.lab-note')).toContainText('375 minutes');
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1h'); // nothing changed

  await page.getByLabel('custom interval').fill('abc');
  await page.getByLabel('custom interval').press('Enter');
  await expect(page.locator('.lab-note')).toContainText('number and a unit');

  await page.getByLabel('custom interval').fill('3D');
  await page.getByLabel('custom interval').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN · 3D');
  await expect(page.locator('.lab-sub [data-range]')).toHaveText(['6M', '1Y', '5Y', 'All']);
});
