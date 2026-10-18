import { expect, test, type Page } from '@playwright/test';

async function openCalendar(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Screen', exact: true }).click();
  await expect(page.locator('.stable .srow').first()).toBeVisible({ timeout: 15_000 });
  await page.locator('.smodes').getByRole('button', { name: 'Calendar' }).click();
  await expect(page.locator('.calendar')).toBeVisible();
}

test('the calendar lists the exchange\'s board meetings and ex-dates for the coming weeks, by day', async ({ page, request }) => {
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
  const soon = new Date(Date.now() + 60 * 86_400_000).toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
  const known = await (await request.get(`/api/calendar?from=${today}&to=${soon}`)).json();
  await openCalendar(page);
  await expect(page.locator('.cal-note')).toContainText('board meetings');
  await expect(page.locator('.cal-note')).toContainText('no economic calendar');
  if (known.events.length || known.actions.length) {
    await expect(page.locator('.cal-day').first()).toBeVisible({ timeout: 15_000 });
    const days = await page.locator('.cal-day h4').allTextContents();
    expect(days.length).toBeGreaterThan(0);
    expect(days[0]).toMatch(/^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2} \d{4} · /);
  }
});

test('the past month shows what has been seen, and the kind chips and search narrow it without another request', async ({ page }) => {
  await openCalendar(page);
  await page.locator('select[aria-label="date range"]').selectOption('past');
  await expect(page.locator('.cal-row').first()).toBeVisible({ timeout: 15_000 });
  const all = await page.locator('.cal-row').count();
  expect(all).toBeGreaterThan(20); // a month of dividends and general meetings
  let requests = 0;
  page.on('request', (r) => r.url().includes('/api/calendar') && requests++);
  await page.locator('.cal-chip', { hasText: 'Dividends' }).click();
  const dividends = await page.locator('.cal-row').count();
  expect(dividends).toBeGreaterThan(0);
  expect(dividends).toBeLessThan(all);
  for (const badges of (await page.locator('.cal-row .cal-badges').allTextContents()).slice(0, 30)) expect(badges).toContain('Dividends');
  await page.locator('.cal-chip', { hasText: 'Dividends' }).click();
  const first = (await page.locator('.cal-row .cal-sym').first().textContent())!;
  await page.getByLabel('search the calendar').fill(first);
  await expect.poll(() => page.locator('.cal-row').count()).toBeLessThan(all);
  expect(await page.locator('.cal-row .cal-sym').first().textContent()).toBe(first);
  expect(requests).toBe(0);
});

test('it can be limited to the stocks of the screen, and a symbol opens its stock', async ({ page }) => {
  await openCalendar(page);
  await page.locator('select[aria-label="date range"]').selectOption('past');
  await expect(page.locator('.cal-row').first()).toBeVisible({ timeout: 15_000 });
  const all = await page.locator('.cal-row').count();
  await page.getByLabel('screen query').fill('index = "nifty 50"');
  await page.getByLabel('only stocks in my screen').check();
  await expect.poll(() => page.locator('.cal-row').count()).toBeLessThan(all);
  const symbols = await page.locator('.cal-row .cal-sym').allTextContents();
  expect(symbols.length).toBeGreaterThan(0);
  await page.locator('.cal-sym').first().click();
  await expect(page.locator('.saction .ssel')).toContainText(symbols[0]);
});

test('the other two views are one click away from it', async ({ page }) => {
  await openCalendar(page);
  await page.locator('.smodes').getByRole('button', { name: 'Table' }).click();
  await expect(page.locator('.stable')).toBeVisible();
  await expect(page.locator('.calendar')).toBeHidden();
  await page.locator('.smodes').getByRole('button', { name: 'Heatmap' }).click();
  await expect(page.locator('.hm-tile').first()).toBeVisible();
});
