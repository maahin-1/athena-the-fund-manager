import { expect, test, type Page } from '@playwright/test';

/** The page fills the window it is given and nothing sticks out of it: at desktop, laptop, tablet and phone sizes, in every view, after resizing the
 * window, when a layout saved in another window size is restored, and for the pop-ups. */
const SIZES: [number, number][] = [[2560, 1300], [1917, 960], [1366, 768], [1024, 700], [768, 1024], [390, 844]];
const VIEWS = ['Terminal', 'Chart Lab', 'Fundamentals', 'Research', 'Screen'];

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && !m.text().includes('404') && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

/** What pokes out of the window: an element past an edge that no scrolling or clipping ancestor holds in. */
async function overflow(page: Page): Promise<{ app: number[]; vp: number[]; bad: string[] }> {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const a = document.getElementById('app')!.getBoundingClientRect();
    const bad: string[] = [];
    for (const e of document.querySelectorAll('body *')) {
      if (e.closest('[hidden]')) continue;
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const q = e.getBoundingClientRect();
      if (q.width === 0 || q.height === 0) continue;
      let held = false;
      for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
        const o = getComputedStyle(p);
        const clips = ['hidden', 'auto', 'scroll', 'clip'];
        if (clips.includes(o.overflowX) || clips.includes(o.overflowY)) {
          const r = p.getBoundingClientRect();
          if (q.right > r.right + 1 || q.bottom > r.bottom + 1 || q.left < r.left - 1 || q.top < r.top - 1) {
            held = true;
            break;
          }
        }
      }
      if (!held && (q.right > vw + 1 || q.bottom > vh + 1 || q.left < -1 || q.top < -1)) bad.push(`${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]} → ${Math.round(q.left)},${Math.round(q.top)},${Math.round(q.right)},${Math.round(q.bottom)}`);
    }
    return { app: [Math.round(a.left), Math.round(a.top), Math.round(a.right), Math.round(a.bottom)], vp: [0, 0, vw, vh], bad };
  });
}

test('the app fills the window exactly and nothing sticks out, at every size and in every view', async ({ page }) => {
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto('/');
    await page.evaluate(() => window.localStorage.clear());
    await page.reload();
    await expect(page.locator('.wl-row').first()).toBeVisible();
    for (const view of VIEWS) {
      await page.locator('.vtab', { hasText: new RegExp(`^${view}$`) }).click();
      await page.waitForTimeout(250);
      const r = await overflow(page);
      expect(r.app, `${w}x${h} ${view}: the app is the window`).toEqual(r.vp);
      expect(r.bad, `${w}x${h} ${view}: nothing outside the window`).toEqual([]);
    }
  }
});

test('growing, shrinking and rotating the window keeps the panels exactly filling it', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 640 });
  await page.goto('/');
  await expect(page.locator('.wl-row').first()).toBeVisible();
  for (const [w, h] of [[1917, 960], [960, 960], [1534, 816], [700, 500], [1917, 960], [800, 1280]] as [number, number][]) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    const m = await page.evaluate(() => {
      const g = [...document.querySelectorAll('.dv-groupview')].map((x) => x.getBoundingClientRect());
      const dock = document.querySelector('.dock')!.getBoundingClientRect();
      return { right: Math.round(Math.max(...g.map((x) => x.right))), bottom: Math.round(Math.max(...g.map((x) => x.bottom))), vw: document.documentElement.clientWidth, dockBottom: Math.round(dock.bottom) };
    });
    expect(m.right, `${w}x${h} right edge`).toBe(m.vw);
    expect(m.bottom, `${w}x${h} bottom edge`).toBe(m.dockBottom);
  }
});

test('a layout saved in one window size fills a window of another size', async ({ context }) => {
  for (const [w1, h1, w2, h2] of [[1100, 700, 1917, 960], [1917, 960, 1100, 700], [1917, 960, 900, 800]] as [number, number, number, number][]) {
    const first = await context.newPage();
    await first.setViewportSize({ width: w1, height: h1 });
    await first.goto('/');
    await first.evaluate(() => window.localStorage.clear());
    await first.reload();
    await first.getByRole('button', { name: 'Theme' }).click(); // a change, so the layout is saved
    await first.waitForTimeout(1200);
    await first.close();
    const second = await context.newPage();
    await second.setViewportSize({ width: w2, height: h2 });
    await second.goto('/');
    await expect(second.locator('.wl-row').first()).toBeVisible();
    const m = await second.evaluate(() => {
      const g = [...document.querySelectorAll('.dv-groupview')].map((x) => x.getBoundingClientRect());
      return { left: Math.round(Math.min(...g.map((x) => x.left))), right: Math.round(Math.max(...g.map((x) => x.right))), vw: document.documentElement.clientWidth };
    });
    expect([m.left, m.right], `saved at ${w1}x${h1}, opened at ${w2}x${h2}`).toEqual([0, m.vw]);
    await second.close();
  }
});

test('the search drop-down and the Chart Lab pop-ups stay inside a small window', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 640 });
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await page.getByLabel('command line').fill('tata');
  await expect(page.locator('.suggest .sg-item').first()).toBeVisible();
  const box = await page.locator('.suggest:not([hidden])').boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(420);
  expect(box!.y + box!.height).toBeLessThanOrEqual(640);
  await page.getByLabel('command line').press('Escape');
  await page.getByLabel('command line').fill('lab');
  await page.getByLabel('command line').press('Enter');
  await page.locator('.lab-ind').click();
  const pop = await page.locator('.lab-pop:not([hidden])').boundingBox();
  expect(pop!.x).toBeGreaterThanOrEqual(0);
  expect(pop!.x + pop!.width).toBeLessThanOrEqual(420);
  expect(pop!.y + pop!.height).toBeLessThanOrEqual(640);
});

test('a narrow window gets the one-column arrangement and does not overwrite the saved layout', async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 800 });
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await expect(page.locator('.wl-row').first()).toBeVisible();
  await expect(page.locator('.dv-groupview')).toHaveCount(3); // the tape, the chart, and one tabbed group (watchlist, quote, depth)
  const widths = await page.locator('.dv-groupview').evaluateAll((g) => g.map((x) => Math.round(x.getBoundingClientRect().width)));
  expect(widths.every((w) => w === 600)).toBe(true); // every group is the full width: one column
  await page.waitForTimeout(1200);
  expect(await page.evaluate(() => window.localStorage.getItem('athena.terminal.current'))).toBeNull();
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.reload();
  await expect(page.locator('.dv-groupview')).toHaveCount(6); // layout A again: chart, watchlist, details, depth, quote, tape
});

test('the Fundamentals search box is still there in a phone-width window', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByLabel('command line').fill('FUND');
  await page.getByLabel('command line').press('Enter');
  const box = await page.getByLabel('search company').boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.width).toBeGreaterThan(100);
});
