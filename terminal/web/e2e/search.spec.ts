import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');
const items = (page: Page) => page.locator('.suggest .sg-item');
const chartTitle = (page: Page) => page.locator('.panel', { has: page.locator('.pt', { hasText: 'Chart' }) }).first().locator('.pt');

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
  // a company with nothing stored is a 404 and a busy fetch a 409; the browser logs both, and both are the page working as designed
  expect((page as unknown as { errors: string[] }).errors.filter((e) => !e.includes('404') && !e.includes('409'))).toEqual([]);
});

test('typing part of a name drops down matching stocks, and a click opens one', async ({ page }) => {
  await command(page).fill('tata mo');
  await expect(items(page).first()).toBeVisible();
  await expect(items(page).first()).toContainText('TMPV');
  await expect(items(page).first()).toContainText('Tata Motors');
  await items(page).first().click();
  await expect(chartTitle(page)).toContainText('TMPV');
  await expect(page.locator('.suggest:not([hidden])')).toHaveCount(0);
  await expect(command(page)).toHaveValue('');
});

test('arrow keys and Enter pick from the list; Escape closes it', async ({ page }) => {
  await command(page).fill('tata');
  await expect(items(page)).not.toHaveCount(0);
  await command(page).press('ArrowDown');
  await command(page).press('ArrowDown');
  await expect(items(page).nth(1)).toHaveClass(/on/);
  const second = await items(page).nth(1).getAttribute('data-symbol');
  await command(page).press('Enter');
  await expect(chartTitle(page)).toContainText(second!);
  await command(page).fill('reli');
  await expect(items(page).first()).toBeVisible();
  await command(page).press('Escape');
  await expect(page.locator('.suggest:not([hidden])')).toHaveCount(0);
});

test('a name typed in full and Enter goes to the best match, like a search box', async ({ page }) => {
  await command(page).fill('rail vikas nigam');
  await command(page).press('Enter');
  await expect(chartTitle(page)).toContainText('RVNL');
  await command(page).fill('zomato');
  await command(page).press('Enter');
  await expect(chartTitle(page)).toContainText('ETERNAL');
});

test('ADD with a partial name adds the chosen stock to the watchlist', async ({ page }) => {
  await command(page).fill('add tata ste');
  await expect(items(page).first()).toContainText('TATASTEEL');
  await items(page).first().click();
  await expect(page.locator('.wl-row[data-symbol="TATASTEEL"]')).toBeVisible();
  await expect(page.locator('.statusline')).toContainText('added TATASTEEL');
});

test('a slip of a letter and an index name both find something', async ({ page }) => {
  await command(page).fill('relience');
  await expect(items(page).first()).toContainText('RELIANCE');
  await command(page).fill('nifty ba');
  await expect(items(page).first()).toContainText('NIFTY BANK');
  await command(page).fill('qqqqzzzz');
  await expect(page.locator('.suggest:not([hidden])')).toHaveCount(0);
});

test('the Chart Lab symbol box and the Fundamentals search box suggest too', async ({ page }) => {
  await command(page).fill('LAB');
  await command(page).press('Enter');
  await page.getByLabel('chart symbol').fill('infosys');
  await expect(items(page).first()).toContainText('INFY');
  await items(page).first().click();
  await expect(page.locator('.lab-head')).toContainText('INFY');
  await command(page).fill('FUND');
  await command(page).press('Enter');
  await page.getByLabel('search company').fill('tata consultancy');
  await expect(items(page).first()).toContainText('TCS');
  await items(page).first().click();
  await expect(page.locator('.fsym').first()).toHaveText('TCS');
});

test('a company with nothing stored offers Fetch now, shows the steps, and ends with a clear message', async ({ page }) => {
  let polls = 0;
  await page.route('**/api/fund-fetch/RVNL', (route) => route.fulfill({ status: 202, contentType: 'application/json', body: '{"state":"running","symbol":"RVNL"}' }));
  await page.route('**/api/fund-fetch', (route) => {
    polls++;
    const body = polls < 2 ? { state: 'idle' } : polls < 4 ? { state: 'running', symbol: 'RVNL', stage: 'fetching shareholding', step: 2, of: 3 } : { state: 'done', symbol: 'RVNL' };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await command(page).fill('FUND');
  await command(page).press('Enter');
  await page.getByLabel('search company').fill('rail vikas');
  await expect(items(page).first()).toContainText('RVNL');
  await items(page).first().click();
  await expect(page.getByRole('button', { name: 'Fetch now' })).toBeVisible();
  await page.getByRole('button', { name: 'Fetch now' }).click();
  await expect(page.locator('.ffetching')).toContainText('fetching shareholding');
  // the e2e store holds no RVNL, so after "done" the page says the exchange had nothing in the form it reads
  await expect(page.locator('.fbody .note.bad')).toContainText('no financial results for RVNL', { timeout: 15_000 });
});

test('a fetch refused because another company is running says which', async ({ page }) => {
  await page.route('**/api/fund-fetch/RVNL', (route) => route.fulfill({ status: 409, contentType: 'application/json', body: '{"error":"busy","symbol":"TCS"}' }));
  await command(page).fill('FUND RVNL');
  await command(page).press('Enter');
  await page.getByRole('button', { name: 'Fetch now' }).click();
  await expect(page.locator('.fbody .note.bad')).toContainText('being asked for TCS');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
