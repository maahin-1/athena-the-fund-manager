import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');
const BLUE: [number, number, number] = [41, 98, 255]; // the default drawing colour #2962ff
const RED: [number, number, number] = [242, 54, 69]; // #f23645

const EMPTY = (symbol: string) => ({ version: 1, symbol, drawings: [] });

async function openLab(page: Page, symbol = 'SBIN'): Promise<void> {
  await command(page).fill(`LAB ${symbol}`);
  await command(page).press('Enter');
  await expect(page.locator('.lab-rail')).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText(symbol);
  await page.waitForTimeout(700); // bars loaded and drawn
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
  await page.goto('/');
  await page.evaluate(async (empties) => {
    window.localStorage.clear();
    for (const doc of empties) await fetch(`/api/drawings/${encodeURIComponent(doc.symbol)}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(doc) });
  }, [EMPTY('SBIN'), EMPTY('TCS')]);
  await page.reload();
});

test.afterEach(async ({ page }) => {
  expect((page as unknown as { errors: string[] }).errors.filter((e) => !e.includes('404'))).toEqual([]);
});

/** A point inside the price pane, by fractions of the chart host (the price axis and time axis are outside it). */
async function at(page: Page, fx: number, fy: number): Promise<[number, number]> {
  const box = (await page.locator('.lab-host').boundingBox())!;
  return [box.x + (box.width - 70) * fx, box.y + (box.height - 30) * fy];
}

async function pick(page: Page, group: string, tool: string): Promise<void> {
  await page.getByLabel(`tool group ${group}`).click();
  await page.locator(`.rail-tool[data-tool="${tool}"]`).click();
}

async function draw(page: Page, tool: string, group: string, points: [number, number][]): Promise<void> {
  await pick(page, group, tool);
  for (const [fx, fy] of points) {
    const [x, y] = await at(page, fx, fy);
    await page.mouse.click(x, y);
  }
  await page.waitForTimeout(150);
}

/** What is saved in the browser for the symbol, parsed. */
async function saved(page: Page, symbol = 'SBIN'): Promise<{ drawings: { id: string; tool: string; points: { t: number; p: number }[]; style: Record<string, unknown>; text?: string; locked: boolean; hidden: boolean }[] }> {
  return page.evaluate((s) => JSON.parse(window.localStorage.getItem(`athena.drawings.${s}`) ?? '{"drawings":[]}'), symbol);
}

/** How many pixels on the chart's canvases are (nearly) this colour: proof that something of that colour is painted. */
async function pixelsOf(page: Page, rgb: [number, number, number]): Promise<number> {
  return page.evaluate((want) => {
    let n = 0;
    for (const c of document.querySelectorAll<HTMLCanvasElement>('.lab-host canvas')) {
      const ctx = c.getContext('2d');
      if (!ctx || !c.width || !c.height) continue;
      const data = ctx.getImageData(0, 0, c.width, c.height).data;
      for (let i = 0; i < data.length; i += 4) if (Math.abs(data[i] - want[0]) < 14 && Math.abs(data[i + 1] - want[1]) < 14 && Math.abs(data[i + 2] - want[2]) < 14 && data[i + 3] > 200) n++;
    }
    return n;
  }, rgb);
}

test('a trend line is drawn with two clicks, listed in Objects, and painted on the chart', async ({ page }) => {
  await openLab(page);
  const before = await pixelsOf(page, BLUE);
  await draw(page, 'trend', 'Lines', [[0.2, 0.7], [0.6, 0.35]]);
  await expect(page.locator('.lab-tab', { hasText: 'Objects (1)' })).toBeVisible();
  await page.locator('.lab-tab', { hasText: 'Objects' }).click();
  await expect(page.locator('.obj-row[data-tool="trend"]')).toHaveCount(1);
  expect(await pixelsOf(page, BLUE)).toBeGreaterThan(before + 150);
  const s = await saved(page);
  expect(s.drawings).toHaveLength(1);
  expect(s.drawings[0].points).toHaveLength(2);
  await expect(page.locator('.lab-props')).toBeVisible(); // the new drawing is selected
});

test('dragging a drawing moves it, dragging a handle moves one end, and a locked one stays', async ({ page }) => {
  await openLab(page);
  await draw(page, 'trend', 'Lines', [[0.2, 0.7], [0.6, 0.35]]);
  const first = (await saved(page)).drawings[0];
  // press on the middle of the line and drag it right and up
  const [mx, my] = await at(page, 0.4, 0.525);
  await page.mouse.move(mx, my);
  await page.mouse.down();
  await page.mouse.move(mx + 60, my - 40, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  const moved = (await saved(page)).drawings[0];
  expect(moved.points[0].t).toBeGreaterThan(first.points[0].t);
  expect(moved.points[0].p).toBeGreaterThan(first.points[0].p);
  expect(moved.points[1].t - moved.points[0].t).toBe(first.points[1].t - first.points[0].t); // the shape kept its length in time
  // drag the second handle
  const [hx, hy] = await at(page, 0.6, 0.35);
  await page.mouse.move(hx + 60, hy - 40);
  await page.mouse.down();
  await page.mouse.move(hx + 140, hy + 20, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  const handled = (await saved(page)).drawings[0];
  expect(handled.points[0]).toEqual(moved.points[0]);
  expect(handled.points[1].t).toBeGreaterThan(moved.points[1].t);
  // lock it: a drag no longer moves it
  await page.getByLabel('lock drawing').click();
  await page.mouse.move(mx + 60, my - 40);
  await page.mouse.down();
  await page.mouse.move(mx + 200, my + 100, { steps: 4 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  expect((await saved(page)).drawings[0].points).toEqual(handled.points);
});

test('the properties bar changes colour, width, line style and words, and the change is painted', async ({ page }) => {
  await openLab(page);
  await draw(page, 'trend', 'Lines', [[0.2, 0.7], [0.6, 0.35]]);
  expect(await pixelsOf(page, RED)).toBeLessThan(20);
  await page.getByLabel('drawing colour #f23645').click();
  await page.getByLabel('drawing width').selectOption('4');
  await page.getByLabel('drawing line style').selectOption('dashed');
  await page.waitForTimeout(200);
  const d = (await saved(page)).drawings[0];
  expect(d.style).toMatchObject({ color: '#f23645', width: 4, dash: 'dashed' });
  expect(await pixelsOf(page, RED)).toBeGreaterThan(100);
  await page.getByLabel('extend right').click();
  expect(((await saved(page)).drawings[0].style as { extendRight?: boolean }).extendRight).toBe(true);
  // a new drawing takes the style last chosen
  await draw(page, 'hline', 'Lines', [[0.3, 0.2]]);
  expect((await saved(page)).drawings[1].style).toMatchObject({ color: '#f23645', width: 4, dash: 'dashed' });
});

test('hide and show from the Objects tab take the drawing off and put it back, and delete removes it', async ({ page }) => {
  await openLab(page);
  await draw(page, 'trend', 'Lines', [[0.2, 0.7], [0.6, 0.35]]);
  await page.locator('.lab-tab', { hasText: 'Objects' }).click();
  const there = await pixelsOf(page, BLUE);
  expect(there).toBeGreaterThan(150);
  await page.getByLabel('hide Trend line').click();
  await page.waitForTimeout(200);
  expect(await pixelsOf(page, BLUE)).toBeLessThan(there / 3);
  await page.getByLabel('show Trend line').click();
  await page.waitForTimeout(200);
  expect(await pixelsOf(page, BLUE)).toBeGreaterThan(there / 2);
  await page.getByLabel('delete Trend line').click();
  await expect(page.locator('.obj-row')).toHaveCount(0);
  expect((await saved(page)).drawings).toHaveLength(0);
});

test('Fibonacci, a rectangle, a long position and text can be drawn, and they are all there after a reload', async ({ page }) => {
  await openLab(page);
  await draw(page, 'fib', 'Fibonacci', [[0.3, 0.8], [0.7, 0.2]]);
  await draw(page, 'rect', 'Shapes', [[0.1, 0.1], [0.25, 0.3]]);
  await draw(page, 'long', 'Position', [[0.5, 0.55], [0.75, 0.4]]);
  await draw(page, 'text', 'Annotation', [[0.4, 0.15]]);
  await page.getByLabel('drawing text').fill('support');
  await page.getByLabel('drawing text').press('Enter');
  await page.waitForTimeout(1200); // saved to the server 400 ms after the last change
  const tools = (await saved(page)).drawings.map((d) => d.tool);
  expect(tools).toEqual(['fib', 'rect', 'long', 'text']);
  expect((await saved(page)).drawings[3].text).toBe('support');
  expect((await saved(page)).drawings[2].points.map((p) => p.p)[1]).toBeLessThan((await saved(page)).drawings[2].points[0].p); // the stop sits below the entry of a long position
  // the server has them too: forget the browser copy and reload
  await page.evaluate(() => window.localStorage.removeItem('athena.drawings.SBIN'));
  await page.reload();
  await openLab(page);
  await page.locator('.lab-tab', { hasText: 'Objects' }).click();
  await expect(page.locator('.obj-row')).toHaveCount(4);
  const onServer = await page.evaluate(async () => (await fetch('/api/drawings/SBIN')).json());
  expect(onServer.drawings).toHaveLength(4);
});

test('each symbol has its own drawings', async ({ page }) => {
  await openLab(page, 'SBIN');
  await draw(page, 'hline', 'Lines', [[0.3, 0.3]]);
  await page.locator('.lab-tab', { hasText: 'Objects' }).click();
  await expect(page.locator('.obj-row')).toHaveCount(1);
  await page.getByLabel('chart symbol').fill('TCS');
  await page.getByLabel('chart symbol').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('TCS');
  await expect(page.locator('.obj-row')).toHaveCount(0);
  await page.getByLabel('chart symbol').fill('SBIN');
  await page.getByLabel('chart symbol').press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN');
  await expect(page.locator('.obj-row')).toHaveCount(1);
});

test('undo and redo step through drawings and chart changes in the order they were made; Delete removes the selected one', async ({ page }) => {
  await openLab(page);
  await draw(page, 'hline', 'Lines', [[0.3, 0.3]]);
  await draw(page, 'vline', 'Lines', [[0.5, 0.3]]);
  expect((await saved(page)).drawings).toHaveLength(2);
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await page.waitForTimeout(150);
  expect((await saved(page)).drawings).toHaveLength(1);
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  await page.waitForTimeout(150);
  expect((await saved(page)).drawings).toHaveLength(2);
  // select the vertical line by pressing on it, then Delete
  await page.mouse.click(...(await at(page, 0.5, 0.8)));
  await expect(page.locator('.lab-props')).toBeVisible();
  await page.keyboard.press('Delete');
  await page.waitForTimeout(150);
  const left = (await saved(page)).drawings;
  expect(left).toHaveLength(1);
  expect(left[0].tool).toBe('hline');
  await page.keyboard.press('Control+z');
  await page.waitForTimeout(150);
  expect((await saved(page)).drawings).toHaveLength(2);
});

test('Clear asks before it removes everything, and Escape ends a tool without drawing', async ({ page }) => {
  await openLab(page);
  await draw(page, 'hline', 'Lines', [[0.3, 0.3]]);
  await draw(page, 'hline', 'Lines', [[0.4, 0.5]]);
  await page.getByLabel('remove all drawings').click();
  await expect(page.getByLabel('remove all drawings')).toContainText('Sure? 2');
  expect((await saved(page)).drawings).toHaveLength(2);
  await page.getByLabel('remove all drawings').click();
  await expect.poll(async () => (await saved(page)).drawings.length).toBe(0);
  await pick(page, 'Lines', 'trend');
  await page.mouse.click(...(await at(page, 0.3, 0.6)));
  await page.keyboard.press('Escape');
  await page.mouse.click(...(await at(page, 0.6, 0.3)));
  expect((await saved(page)).drawings).toHaveLength(0);
});

test('price-move charts have no time axis, so the rail is switched off there and back on after', async ({ page }) => {
  await openLab(page);
  await draw(page, 'hline', 'Lines', [[0.3, 0.3]]);
  await page.getByLabel('chart type').selectOption('renko');
  await expect(page.locator('.lab-rail.off')).toBeVisible();
  await expect(page.getByLabel('tool group Lines')).toBeDisabled();
  await page.getByLabel('chart type').selectOption('candles');
  await expect(page.locator('.lab-rail.off')).toHaveCount(0);
  await expect(page.getByLabel('tool group Lines')).toBeEnabled();
  await page.locator('.lab-tab', { hasText: 'Objects' }).click();
  await expect(page.locator('.obj-row')).toHaveCount(1);
});

test('the magnet snaps a drawing to a bar price', async ({ page }) => {
  await openLab(page);
  await page.getByLabel('magnet').click();
  await page.getByLabel('magnet').click();
  await expect(page.getByLabel('magnet')).toHaveAttribute('data-mode', 'strong');
  await draw(page, 'hline', 'Lines', [[0.5, 0.45]]);
  const { p, t } = (await saved(page)).drawings[0].points[0];
  const bars = await page.evaluate(async () => (await (await fetch('/api/bars?symbol=SBIN&tf=1D&limit=400')).json()).bars as { t: number; o: number; h: number; l: number; c: number }[]);
  const bar = bars.find((b) => b.t === t);
  expect(bar).toBeTruthy(); // the click was within the bars, so its time is a bar's
  expect([bar!.o, bar!.h, bar!.l, bar!.c].some((v) => Math.abs(v - p) < 0.011)).toBe(true);
});

test('tier 2 and 3 tools: a pitchfork, a Gann fan, an ABCD, head and shoulders and a sine line can be drawn from their groups, and they are there after a reload', async ({ page }) => {
  await openLab(page);
  await draw(page, 'pitchfork', 'Pitchforks', [[0.15, 0.7], [0.35, 0.3], [0.5, 0.6]]);
  await draw(page, 'gannfan', 'Gann', [[0.6, 0.8], [0.75, 0.55]]);
  await draw(page, 'abcd', 'Patterns and Elliott waves', [[0.1, 0.2], [0.2, 0.45], [0.28, 0.3], [0.4, 0.6]]);
  await draw(page, 'headshoulders', 'Patterns and Elliott waves', [[0.45, 0.9], [0.5, 0.75], [0.55, 0.85], [0.62, 0.65], [0.7, 0.85], [0.75, 0.75], [0.82, 0.9]]);
  await draw(page, 'sine', 'Cycles', [[0.1, 0.9], [0.3, 0.7]]);
  await page.waitForTimeout(1200);
  const tools = (await saved(page)).drawings.map((d) => d.tool);
  expect(tools).toEqual(['pitchfork', 'gannfan', 'abcd', 'headshoulders', 'sine']);
  expect((await saved(page)).drawings[3].points).toHaveLength(7);
  await page.screenshot({ path: 'test-results/drawings-tier2-3.png' });
  await page.evaluate(() => window.localStorage.removeItem('athena.drawings.SBIN'));
  await page.reload();
  await openLab(page);
  await page.locator('.lab-tab', { hasText: 'Objects' }).click();
  await expect(page.locator('.obj-row')).toHaveCount(5);
});
