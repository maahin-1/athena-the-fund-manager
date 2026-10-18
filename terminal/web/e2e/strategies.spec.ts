import { expect, test, type Page } from '@playwright/test';

async function openBuilder(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: 'Research', exact: true }).click();
  await page.locator('.sbuilder > summary').click();
  await expect(page.getByLabel('entry formula')).toBeVisible();
}

const result = (rule: string) => ({
  identifier: 'SBIN', assumptions: 'costs of 0.1% a side are assumed', notes: [],
  panels: [{ rule, description: 'Your strategy.', rows: [['Total return', '+12.0%', '+31.0%']], facts: [], blinded_identical: true, curve: null }],
});

test('a strategy written as formulas is sent to the backtest as the tree the engine reads, and its result is drawn', async ({ page }) => {
  let sent: { symbol: string; strategy: Record<string, unknown> } | null = null;
  await page.route('**/api/backtest', async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({ json: result(String((sent?.strategy as { name?: string })?.name)) });
  });
  await openBuilder(page);
  await page.getByLabel('strategy name').fill('E2E trend');
  await page.getByLabel('entry formula').fill('close > sma(200) and rsi(14) < 70');
  await page.getByLabel('exit formula').fill('close crosses below sma(50)');
  await page.getByLabel('protective stop').fill('2');
  await expect(page.locator('.sb-entry-verdict')).toContainText('A yes/no condition');
  await page.getByRole('button', { name: /^Run on / }).click();
  await expect(page.locator('.backtest .btpanel[data-rule="E2E trend"]')).toBeVisible();
  await expect(page.locator('.backtest .bttable')).toContainText('+12.0%');
  expect(sent!.symbol).toBe('SBIN');
  expect(sent!.strategy).toMatchObject({
    version: 1, name: 'E2E trend', stop_atr: 2,
    entry: { all: [{ op: 'gt', left: { price: 'close' }, right: { ind: 'sma', params: { length: 200 }, line: 'value' } }, { op: 'lt', left: { ind: 'rsi', params: { length: 14 }, line: 'value' }, right: { const: 70 } }] },
    exit: { op: 'crosses_below', left: { price: 'close' }, right: { ind: 'sma', params: { length: 50 }, line: 'value' } },
  });
});

test('a starting strategy fills the form, and a mistake stops the run and says where', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/backtest', async (route) => {
    calls++;
    await route.fulfill({ json: result('x') });
  });
  await openBuilder(page);
  await page.getByRole('button', { name: 'Golden cross' }).click();
  await expect(page.getByLabel('entry formula')).toHaveValue('sma(50) crosses above sma(200)');
  await page.getByLabel('entry formula').fill('close > smaa(50)');
  await expect(page.locator('.sb-entry-verdict')).toContainText('did you mean "sma"?');
  await page.getByRole('button', { name: /^Run on / }).click();
  await expect(page.locator('.sb-message')).toContainText('unknown function');
  expect(calls).toBe(0);
  await page.getByLabel('entry formula').fill('sma(50)');
  await page.getByRole('button', { name: /^Run on / }).click();
  await expect(page.locator('.sb-message')).toContainText('entry must be a yes/no condition');
  expect(calls).toBe(0);
});

test('the engine\'s own refusal of a strategy is shown as it was written', async ({ page }) => {
  await page.route('**/api/backtest', (route) => route.fulfill({ status: 422, json: { error: 'strategy.stop_atr: must be above 0 and at most 10' } }));
  await openBuilder(page);
  await page.getByLabel('strategy name').fill('E2E refused');
  await page.getByLabel('entry formula').fill('close > 1');
  await page.getByLabel('exit formula').fill('close < 1');
  await page.getByRole('button', { name: /^Run on / }).click();
  await expect(page.locator('.rstatus, .note.bad').filter({ hasText: 'strategy.stop_atr' }).first()).toBeVisible();
});

test('a strategy is saved by name, comes back after a reload, and is deleted', async ({ page }) => {
  await openBuilder(page);
  await page.getByLabel('strategy name').fill('E2E keeper');
  await page.getByLabel('entry formula').fill('rsi(14) < 30');
  await page.getByLabel('exit formula').fill('rsi(14) > 55');
  await page.getByLabel('longest hold').fill('40');
  await page.locator('.sbuilder').getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.locator('.sb-message')).toContainText('Saved "E2E keeper"');

  await page.reload();
  await page.getByRole('button', { name: 'Research', exact: true }).click();
  await page.locator('.sbuilder > summary').click();
  await page.getByLabel('saved strategies').selectOption('E2E keeper');
  await expect(page.getByLabel('entry formula')).toHaveValue('rsi(14) < 30');
  await expect(page.getByLabel('longest hold')).toHaveValue('40');

  await page.locator('.sbuilder').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.locator('.sb-message')).toContainText('Deleted "E2E keeper"');
  await expect(page.getByLabel('saved strategies').locator('option', { hasText: 'E2E keeper' })).toHaveCount(0);
});
