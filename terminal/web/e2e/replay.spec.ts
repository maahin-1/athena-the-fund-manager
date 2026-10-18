import { expect as baseExpect, test, type Page } from '@playwright/test';

const expect = baseExpect.configure({ timeout: 15_000 });
test.setTimeout(90_000);

const command = (page: Page) => page.getByLabel('command line');
const status = (page: Page) => page.locator('.lab-replay-status');
const barNumber = async (page: Page): Promise<number> => Number(/bar (\d+) of/.exec((await status(page).textContent()) ?? '')?.[1]);
const dateOf = async (page: Page): Promise<string> => /· (\d{4}-\d{2}-\d{2}) ·/.exec((await status(page).textContent()) ?? '')?.[1] ?? '';

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-head')).toContainText(/SBIN · 1D.*C\d/);
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

async function startFromMiddle(page: Page): Promise<void> {
  await page.getByLabel('replay', { exact: true }).click();
  await expect(page.locator('.lab-note')).toContainText('Click the bar');
  const box = (await page.locator('.lab-host').boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await page.mouse.move(box.x + box.width * 0.5 + 3, box.y + box.height * 0.4);
  await page.mouse.click(box.x + box.width * 0.5 + 3, box.y + box.height * 0.4);
  await expect(page.locator('.lab-replay')).toBeVisible();
  await page.mouse.move(5, 5); // off the chart: the data window then shows the newest bar, not the one under the pointer
}

test('replay starts from the bar clicked, steps both ways, and shows nothing after the bar it has reached', async ({ page }) => {
  await startFromMiddle(page);
  await expect(status(page)).toContainText('Replay · ');
  const n = await barNumber(page);
  const day = await dateOf(page);
  expect(n).toBeGreaterThan(50);
  await expect(page.locator('.lab-window')).toContainText(day); // the data window is the bar replay has reached, not the latest

  await page.getByLabel('replay next').click();
  await expect.poll(() => barNumber(page)).toBe(n + 1);
  expect((await dateOf(page)) > day).toBe(true);
  await page.getByLabel('replay back').click();
  await page.getByLabel('replay back').click();
  await expect.poll(() => barNumber(page)).toBe(n - 1);

  await page.keyboard.press('ArrowRight');
  await expect.poll(() => barNumber(page)).toBe(n);
  await page.keyboard.press('ArrowLeft');
  await expect.poll(() => barNumber(page)).toBe(n - 1);
});

test('play runs at the chosen speed to the latest bar and stops there; Space pauses; live prices do not move it', async ({ page }) => {
  await startFromMiddle(page);
  const close = await page.locator('.lab-window .lab-wrow', { hasText: 'Close' }).innerText();
  await page.waitForTimeout(2500);
  expect(await page.locator('.lab-window .lab-wrow', { hasText: 'Close' }).innerText()).toBe(close); // a live price would have moved it

  const n = await barNumber(page);
  await page.keyboard.press('Space'); // play at 1 bar a second
  await page.waitForTimeout(2500);
  await page.keyboard.press('Space');
  const paused = await barNumber(page);
  expect(paused).toBeGreaterThan(n);
  expect(paused).toBeLessThan(n + 6);
  await page.waitForTimeout(1500);
  expect(await barNumber(page)).toBe(paused); // paused means paused

  await page.getByLabel('replay speed').selectOption('50');
  await page.getByLabel('replay play').click();
  await expect(status(page)).toContainText('latest bar', { timeout: 60_000 });
  await expect(page.locator('.lab-note')).toContainText('latest bar');
  await expect(page.getByLabel('replay play')).not.toHaveClass(/on/);
});

test('jump to a date, change the interval keeping the moment, and a wrong date says so', async ({ page }) => {
  await startFromMiddle(page);
  await page.getByLabel('replay date').fill('2026-03-02');
  await page.getByLabel('replay date').press('Enter');
  await expect.poll(() => dateOf(page)).toBe('2026-03-02');
  await page.getByLabel('replay date').fill('tomorrow');
  await page.getByLabel('replay date').press('Enter');
  await expect(page.locator('.lab-note')).toContainText('YYYY-MM-DD');

  await page.locator('.lab-bar [data-interval="1W"]').click();
  await expect(page.locator('.lab-head')).toContainText('· 1W');
  await expect(page.locator('.lab-replay')).toBeVisible(); // still replaying, at the same moment
  const week = await dateOf(page);
  expect(week <= '2026-03-02').toBe(true);
  expect(week >= '2026-02-20').toBe(true); // the last week that had ended by then
  await expect(page.locator('.lab-window')).toContainText(week);
});

test('Escape cancels choosing the start and leaves replay; the symbol box ends it; Exit shows every bar again', async ({ page }) => {
  await page.getByLabel('replay', { exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('replay', { exact: true })).not.toHaveClass(/on/);
  await expect(page.locator('.lab-replay')).toBeHidden();

  await startFromMiddle(page);
  await page.keyboard.press('Escape');
  await expect(page.locator('.lab-replay')).toBeHidden();
  await expect(page.locator('.lab-window')).toContainText('2026-10'); // the latest bars again

  await startFromMiddle(page);
  await page.locator('.lab-symbol').fill('TCS');
  await page.locator('.lab-symbol').press('Enter');
  await expect(page.locator('.lab-replay')).toBeHidden();
  await expect(page.locator('.lab-head')).toContainText('TCS · 1D');

  await startFromMiddle(page);
  await page.getByLabel('replay exit').click();
  await expect(page.locator('.lab-replay')).toBeHidden();
});

test('indicators are worked out from the revealed bars only', async ({ page }) => {
  await page.locator('.lab-ind').click();
  await page.locator('.lab-item[data-indicator="sma"]').click();
  await expect(page.locator('.lab-legend .lab-lrow').first()).toBeVisible();
  await startFromMiddle(page);
  const row = page.locator('.lab-legend .lab-lrow .lg-vals').first();
  await expect(row).not.toBeEmpty();
  const during = await row.innerText();
  await page.keyboard.press('Escape');
  await expect(page.locator('.lab-replay')).toBeHidden();
  await expect(page.locator('.lab-legend .lab-lrow .lg-vals').first()).not.toHaveText(during); // the latest value is not the one replay showed
});
