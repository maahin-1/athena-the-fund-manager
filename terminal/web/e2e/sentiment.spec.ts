import { expect, test } from '@playwright/test';

const page_doc = (tone: unknown = null) => ({
  symbol: 'TCS', company: 'Tata Consultancy Services Limited', as_of: '2026-10-23', window_days: 30,
  news: {
    items: [
      { date: '2026-10-22', publisher: 'NSE', source: 'nse', title: 'Outcome of Board Meeting', link: 'https://example.test/a', routine: false, used: true },
      { date: '2026-10-21', publisher: 'NSE', source: 'nse', title: 'Trading window closure', link: '', routine: true, used: false },
    ],
    notes: [], counts: { all: 2, used: 1, routine: 1, press: 0 },
  },
  social: { enabled: false, notes: ['Reddit is off. To turn it on, register a script app.'], by_subreddit: {}, items: [] },
  tone, caveat: 'Whether the tone of news or posts says anything about later returns has not been tested here.',
});

test('the Sentiment page shows the news and the Reddit notice, filters, and asks for a reading only on request', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const asked: string[] = [];
  await page.route('**/api/sentiment/**', async (route) => {
    asked.push(route.request().url());
    const tone = route.request().url().includes('tone=1') ? { signal: 'neutral', confidence: 20, reasoning: 'Thin coverage.', data_coverage: 'partial', missing: [] } : null;
    await route.fulfill({ json: page_doc(tone) });
  });
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.getByLabel('command line').fill('SENT TCS');
  await page.getByLabel('command line').press('Enter');
  await expect(page.locator('.sentiment')).toBeVisible();
  await expect(page.locator('.sent-head')).toContainText('TCS · Tata Consultancy Services Limited');
  await expect(page.locator('.sent-news tr')).toHaveCount(2);
  await expect(page.locator('.sentiment')).toContainText('Reddit is off');
  await expect(page.locator('.sentiment')).toContainText('has not been tested');
  expect(asked.filter((u) => u.includes('tone=1'))).toHaveLength(0); // no model call without the button

  await page.getByLabel('only the news an analyst would use').check();
  await expect(page.locator('.sent-news tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'Read the tone' }).click();
  await expect(page.locator('.sent-tone')).toContainText('Thin coverage.');
  expect(asked.filter((u) => u.includes('tone=1'))).toHaveLength(1);

  await page.getByRole('button', { name: 'Terminal', exact: true }).click();
  await expect(page.locator('.sentiment')).toBeHidden();
  expect(errors).toEqual([]);
});
