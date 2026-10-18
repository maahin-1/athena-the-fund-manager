import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.locator('input[placeholder*="symbol or command"]');

async function openPeers(page: Page, symbol: string): Promise<void> {
  await page.goto('/');
  await command(page).fill(`PEERS ${symbol}`);
  await command(page).press('Enter');
  await expect(page.locator('.fpeers')).toBeVisible({ timeout: 20_000 });
}

for (const symbol of ['TCS', 'HDFCBANK', 'SHRIRAMFIN', 'ETERNAL']) {
  test(`${symbol}: peers appear with the company first, a median row and a ranking`, async ({ page }) => {
    await openPeers(page, symbol);
    const box = page.locator('.fpeers');
    await expect(box.locator('.fpeer-table tbody tr').first()).toHaveClass(/subject/, { timeout: 15_000 });
    await expect(box.locator('.fpeer-table tbody tr').first()).toContainText(symbol);
    expect(await box.locator('.fpeer-table tbody tr').count()).toBeGreaterThan(6);
    await expect(box.locator('.fpeer-table tbody tr.median')).toContainText('Median of peers');
    await expect(box.locator('.fcrumbs')).not.toHaveText('');
    await expect(box.locator('.fagainst-table tbody tr').filter({ hasText: 'P/E' }).first()).toContainText(/\d+ of \d+|—/);
    // a peer's name opens its page
    const peer = box.locator('.fpeer-table tbody tr.peer button').first();
    const name = (await peer.textContent())!;
    await peer.click();
    await expect(page.locator('.fsym')).toHaveText(name, { timeout: 20_000 });
  });
}

test('a large index member says which indices it is in, and the table can show other columns', async ({ page }) => {
  await openPeers(page, 'TCS');
  const box = page.locator('.fpeers');
  await expect(box.locator('.fchip').first()).toBeVisible();
  expect((await box.locator('.fchip').allTextContents()).join('|')).toContain('NIFTY 50');
  await box.locator('.scols summary').click();
  await box.locator('.scols input[data-column="rsi_14"]').check();
  await expect(box.locator('.fpeer-table th', { hasText: 'RSI' })).toBeVisible();
  await box.locator('select[aria-label="number of peers"]').selectOption('6');
  await expect(box.locator('.fpeer-table tbody tr')).toHaveCount(1 + 6 + 1);
});

test('a company with no statements stored still shows its peers, under the offer to fetch them', async ({ page, request }) => {
  // pick a screened stock the statements store does not hold yet (whichever the long fetch has not reached)
  const screen = await (await request.get('/api/screen')).json();
  const cols: string[] = screen.columns;
  const at = (name: string) => cols.indexOf(name);
  const candidates: string[] = screen.rows
    .filter((r: unknown[]) => r[at('kind')] === 'stock' && typeof r[at('market_cap_cr')] === 'number' && r[at('sector')])
    .map((r: unknown[]) => String(r[0]))
    .slice(1500, 1560);
  let target = '';
  for (const s of candidates) {
    if ((await request.get(`/api/fund/${encodeURIComponent(s)}`)).status() === 404) {
      target = s;
      break;
    }
  }
  test.skip(!target, 'every candidate already has statements stored');
  await openPeers(page, target);
  await expect(page.locator('.ffetch')).toBeVisible();
  await expect(page.locator('.fpeers .fpeer-table tbody tr').first()).toContainText(target, { timeout: 15_000 });
});

test('the peers command is the way in from anywhere and scrolls to the section', async ({ page }) => {
  await openPeers(page, 'TCS');
  await expect(page.locator('.fnavbtn').first()).toHaveText('Peers');
  await expect(page.locator('.fpeers')).toBeInViewport();
});
