import { expect, test, type Page } from '@playwright/test';

const title = (page: Page, text: string) => page.locator('.dv-tab', { hasText: text });
const row = (page: Page, symbol: string) => page.locator(`.wl-row[data-symbol="${symbol}"]`);

test.beforeEach(async ({ page, request }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  // start every test from layout A with nothing saved in the browser
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto('/');
  await expect(row(page, 'SBIN')).toBeVisible();
  await request.delete('/api/layouts/Smoke-1').catch(() => undefined);
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

test('opens with the default panels and a live feed', async ({ page }) => {
  for (const name of ['Watchlist', 'Chart', 'Depth', 'Quote']) await expect(page.locator('.dv-tab', { hasText: name }).first()).toBeVisible();
  await expect(page.locator('.health')).toContainText('Angel live');
  await expect(page.locator('.tape')).toBeVisible();
});

test('watchlist prices change', async ({ page }) => {
  const cell = row(page, 'SBIN').locator('td').nth(1);
  const first = await cell.textContent();
  await expect.poll(async () => cell.textContent(), { timeout: 8000 }).not.toBe(first);
});

test('clicking a row moves linked panels and leaves an unlinked one alone', async ({ page }) => {
  await row(page, 'RELIANCE').click();
  await expect(title(page, 'RELIANCE · ').first()).toBeVisible(); // the chart
  await expect(title(page, 'Depth · RELIANCE')).toBeVisible();
  await expect(title(page, 'Quote · RELIANCE')).toBeVisible();
  // unlink the chart (blue -> none) and pick another symbol: depth follows, the chart stays
  await page.locator('.dv-groupview', { has: page.locator('.chart-host') }).locator('.link-dot').click();
  await row(page, 'TCS').click();
  await expect(title(page, 'Depth · TCS')).toBeVisible();
  await expect(title(page, 'RELIANCE · ').first()).toBeVisible();
});

test('the command line adds a stock, shows a chart and reports a mistake', async ({ page }) => {
  const cmd = page.getByLabel('command line');
  await cmd.fill('add wipro');
  await cmd.press('Enter');
  await expect(row(page, 'WIPRO')).toBeVisible();
  await cmd.fill('infy chart');
  await cmd.press('Enter');
  await expect(title(page, 'INFY · ').first()).toBeVisible();
  await cmd.fill('@@');
  await cmd.press('Enter');
  await expect(page.locator('.statusline.bad')).toContainText('not a symbol');
});

test('presets switch the arrangement and a saved layout comes back', async ({ page }) => {
  const select = page.locator('.layout-select');
  await select.selectOption('preset:B');
  await expect(page.locator('.chart-host')).toHaveCount(4);
  await select.selectOption('preset:A');
  await expect(page.locator('.chart-host')).toHaveCount(1);
  await row(page, 'LUPIN').click(); // a change worth saving
  await page.getByRole('button', { name: 'Save' }).click();
  await page.locator('.namebox').fill('Smoke-1');
  await page.locator('.namebox').press('Enter');
  await expect(page.locator('.statusline')).toContainText('saved layout "Smoke-1"');
  await select.selectOption('preset:B');
  await expect(page.locator('.chart-host')).toHaveCount(4);
  await select.selectOption('saved:Smoke-1');
  await expect(page.locator('.chart-host')).toHaveCount(1);
  await expect(title(page, 'Depth · LUPIN')).toBeVisible();
});

test('the research layout shows stored analysis and the verdict panel from the warehouse', async ({ page }) => {
  await page.locator('.layout-select').selectOption('preset:D');
  await expect(page.locator('.dv-tab', { hasText: 'Analysis · SBIN' })).toBeVisible();
  await expect(page.locator('.content .facts .k', { hasText: 'P/E' })).toBeVisible({ timeout: 10_000 });
  await page.getByRole('button', { name: 'Technicals' }).click();
  await expect(page.locator('.content .facts .k', { hasText: 'rsi 14' })).toBeVisible();
  await page.getByRole('button', { name: 'Fundamentals' }).click();
  await expect(page.locator('.content .group').first()).toBeVisible();
  await expect(page.locator('.dv-tab', { hasText: 'Verdict · SBIN' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Make a (fresh )?verdict/ })).toBeVisible();
  // following the link group: picking a stock moves the analysis too
  await row(page, 'TCS').click();
  await expect(page.locator('.dv-tab', { hasText: 'Analysis · TCS' })).toBeVisible();
});

test('nothing runs off the screen on a small or scaled display', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 640 });
  const overflow = () => page.evaluate(() => ({ x: document.documentElement.scrollWidth - innerWidth, y: document.documentElement.scrollHeight - innerHeight }));
  for (const preset of ['A', 'B', 'C', 'D']) {
    await page.locator('.layout-select').selectOption(`preset:${preset}`);
    await expect.poll(overflow, { message: `layout ${preset}` }).toEqual({ x: 0, y: 0 });
  }
  await page.getByRole('button', { name: 'Research', exact: true }).click();
  await expect.poll(overflow).toEqual({ x: 0, y: 0 });
  await page.setViewportSize({ width: 700, height: 600 });
  await expect.poll(overflow).toEqual({ x: 0, y: 0 });
});

test('panels can be maximised, floated, added and put back', async ({ page }) => {
  const chart = page.locator('.panel', { has: page.locator('.chart-host') });
  const before = (await chart.boundingBox())!;
  await page.locator('.dv-groupview', { has: page.locator('.chart-host') }).locator('.gbtn[title^="Maximise"]').click(); // maximise
  await expect.poll(async () => (await chart.boundingBox())!.width).toBeGreaterThan(before.width + 100);
  await page.keyboard.press('Escape');
  await expect.poll(async () => Math.round((await chart.boundingBox())!.width)).toBe(Math.round(before.width));
  await page.locator('.add-select').selectOption('quote');
  await expect(page.locator('.dv-tab', { hasText: /^Quote · / })).toHaveCount(2);
  await page.getByRole('button', { name: 'Reset' }).click();
  await expect(page.locator('.dv-tab', { hasText: /^Quote · / })).toHaveCount(1);
});

test('the research page shows every analyst in full and follows the stock picked', async ({ page }) => {
  await page.getByRole('button', { name: 'Research', exact: true }).click();
  await expect(page.locator('.research')).toBeVisible();
  await expect(page.locator('.rsym')).toHaveText('SBIN');
  await page.locator('.ritem[data-symbol="HDFCBANK"]').click();
  await expect(page.locator('.rsym')).toHaveText('HDFCBANK');
  await expect(page.locator('.rcard[data-section="Verdict"] .chip.big')).toBeVisible({ timeout: 10_000 });
  const cards = page.locator('.acard');
  expect(await cards.count()).toBeGreaterThanOrEqual(2);
  await expect(cards.first().locator('.reason')).not.toBeEmpty();
  for (const section of ['Overview', 'Fundamentals', 'Technicals']) await expect(page.locator(`.rcard[data-section="${section}"]`)).toBeVisible();
  // the command line works from here too, and the terminal tab brings the panels back
  const cmd = page.getByLabel('command line');
  await cmd.fill('tcs');
  await cmd.press('Enter');
  await expect(page.locator('.rsym')).toHaveText('TCS');
  await page.getByRole('button', { name: 'Terminal', exact: true }).click();
  await expect(page.locator('.chart-host')).toBeVisible();
  await expect(page.locator('.dv-tab', { hasText: 'Depth · TCS' })).toBeVisible();
});

test('the verdict panel opens the full analysis', async ({ page }) => {
  await page.locator('.layout-select').selectOption('preset:D');
  await row(page, 'HDFCBANK').click();
  await page.getByRole('button', { name: 'Open full analysis' }).click();
  await expect(page.locator('.research')).toBeVisible();
  await expect(page.locator('.rsym')).toHaveText('HDFCBANK');
});

test('an unknown symbol is refused, a known one and an index are accepted', async ({ page }) => {
  const cmd = page.getByLabel('command line');
  await cmd.fill('add zzzfake');
  await cmd.press('Enter');
  await expect(page.locator('.statusline.bad')).toContainText('ZZZFAKE is not a listed stock');
  await expect(row(page, 'ZZZFAKE')).toHaveCount(0);
  await cmd.fill('add wipro');
  await cmd.press('Enter');
  await expect(row(page, 'WIPRO')).toBeVisible();
  await cmd.fill('nifty 50');
  await cmd.press('Enter');
  await expect(page.locator('.statusline.bad')).toHaveCount(0);
});

test('indicators show as chips that remove themselves, and the link colour dot follows the panel', async ({ page }) => {
  const chart = page.locator('.panel', { has: page.locator('.chart-host') });
  await expect(chart.locator('.ichip', { hasText: 'EMA 20' })).toBeVisible();
  await chart.locator('select.picker').selectOption('RSI 14');
  await expect(chart.locator('.ichip')).toHaveCount(2);
  await chart.locator('.ichip', { hasText: 'EMA 20' }).click();
  await expect(chart.locator('.ichip')).toHaveCount(1);
  const dot = page.locator('.dv-groupview', { has: page.locator('.chart-host') }).locator('.link-dot');
  await expect(dot).toHaveAttribute('data-group', 'blue');
  await dot.click();
  await expect(dot).toHaveAttribute('data-group', 'none');
});

test('1M is a month of daily bars and the panels show one title row', async ({ page }) => {
  const chart = page.locator('.panel', { has: page.locator('.chart-host') });
  await chart.getByRole('button', { name: '1M', exact: true }).click();
  await expect(chart.locator('.note')).not.toContainText('limited to 5 days');
  await expect(page.locator('.ph').first()).toBeHidden();
});

test('changing a panel to a link colour moves it to the symbol that colour shows', async ({ page }) => {
  await row(page, 'TCS').click();
  const depth = page.locator('.dv-groupview', { has: page.locator('.depth') });
  await depth.locator('.link-dot').click(); // blue -> none: it keeps TCS
  await row(page, 'INFY').click();
  await expect(page.locator('.dv-tab', { hasText: 'Depth · TCS' })).toBeVisible();
  for (let i = 0; i < 4; i++) {
    if ((await depth.locator('.link-dot').getAttribute('data-group')) === 'blue') break;
    await depth.locator('.link-dot').click();
  }
  await expect(page.locator('.dv-tab', { hasText: 'Depth · INFY' })).toBeVisible();
});

test('the screen page filters the stored market with presets and opens a stock in Research', async ({ page }) => {
  await page.getByRole('button', { name: 'Screen', exact: true }).click();
  await expect(page.locator('.screen')).toBeVisible();
  await expect(page.locator('.stable .srow').first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.screen > .note').first()).toContainText('Prices to');
  const all = await page.locator('.screen .count, .screen > .note').allTextContents();
  expect(all.join(' ')).toMatch(/\d[\d,]* matches/);
  await page.getByRole('button', { name: 'Value', exact: true }).click();
  await expect(page.locator('.presets + .note')).toContainText('P/E under 15');
  const rows = page.locator('.stable .srow');
  expect(await rows.count()).toBeGreaterThan(0);
  // every shown P/E meets the preset's limits (a blank never does)
  const pes = await page.locator('.stable td:nth-child(7)').allTextContents(); // P/E is the 7th default column
  for (const t of pes.slice(0, 40)) {
    const n = Number(t);
    expect(n).toBeGreaterThanOrEqual(0);
    expect(n).toBeLessThanOrEqual(15);
  }
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.getByLabel('search the screen').fill('hdfcbank');
  await expect(page.locator('.stable .srow').first()).toHaveAttribute('data-symbol', 'HDFCBANK');
  await page.locator('.stable .srow').first().click();
  await page.locator('.saction').getByRole('button', { name: 'Research', exact: true }).click();
  await expect(page.locator('.rsym')).toHaveText('HDFCBANK');
  await page.locator('.vtab', { hasText: 'Screen' }).click();
  await page.locator('.stable .srow').first().click();
  await page.locator('.saction').getByRole('button', { name: 'Chart', exact: true }).click();
  await expect(page.locator('.dv-tab', { hasText: 'Depth · HDFCBANK' })).toBeVisible();
});
