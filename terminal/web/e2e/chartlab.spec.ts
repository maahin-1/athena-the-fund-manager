import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.getByLabel('command line');

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
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

async function openLab(page: Page, symbol = 'SBIN'): Promise<void> {
  await command(page).fill(`LAB ${symbol}`);
  await command(page).press('Enter');
  await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText(`${symbol} · 1D`);
}

async function addIndicator(page: Page, id: string): Promise<void> {
  await page.locator('.lab-ind').click();
  await page.locator(`.lab-item[data-indicator="${id}"]`).click();
}

test('the Chart Lab draws the symbol with a data window, and F6 reaches it', async ({ page }) => {
  await openLab(page);
  await expect(page.locator('.lab-window')).toContainText('Close');
  await expect(page.locator('.vtab.on')).toHaveText('Chart Lab');
  await page.locator('.vtab', { hasText: 'Terminal' }).click();
  await page.keyboard.press('F6');
  await expect(page.locator('.vtab.on')).toHaveText('Chart Lab');
});

test('indicators get legends and panes, settings change them, undo and redo walk back', async ({ page }) => {
  await openLab(page);
  await addIndicator(page, 'rsi');
  await addIndicator(page, 'bb');
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['BB 20 2', 'RSI 14']); // legends are grouped by pane: overlays first
  await expect(page.locator('.lab-legend')).toHaveCount(2); // the price pane and the RSI pane
  await expect(page.locator('.lab-window')).toContainText('RSI 14');
  await page.locator('.lab-lrow', { hasText: 'RSI' }).locator('[data-action="settings"]').click();
  await page.locator('[data-input="length"]').fill('21');
  await page.locator('.lab-dialog .btn', { hasText: 'OK' }).click();
  await expect(page.locator('.lab-lrow .lg-name').last()).toHaveText('RSI 21');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.locator('.lab-lrow .lg-name').last()).toHaveText('RSI 14');
  await page.getByRole('button', { name: 'Undo' }).click();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.locator('.lab-lrow')).toHaveCount(0);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['RSI 14']);
});

test('log scale, percent scale, chart types, compare and raw prices all keep the chart alive', async ({ page }) => {
  await openLab(page);
  for (const scale of ['log', 'percent', 'auto']) {
    await page.locator(`[data-scale="${scale}"]`).click();
    await expect(page.locator(`[data-scale="${scale}"]`)).toHaveClass(/on/);
  }
  for (const type of ['hollow', 'bars', 'line', 'area', 'heikin', 'candles']) await page.locator('.lab-type').selectOption(type);
  await page.getByLabel('compare symbol').fill('NIFTY 50');
  await page.getByLabel('compare symbol').press('Enter');
  await expect(page.locator('.lab-note')).not.toContainText('could not load');
  await page.getByRole('button', { name: 'ADJ' }).click();
  await expect(page.getByRole('button', { name: 'ADJ' })).not.toHaveClass(/on/);
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');
  await page.locator('[data-interval="5m"]').click();
  await expect(page.locator('.lab [data-range]')).toHaveText(['1D', '5D', '1M']);
});

test('every chart type and interval draws without an error', async ({ page }) => {
  await openLab(page);
  await addIndicator(page, 'rsi');
  for (const type of ['baseline', 'columns', 'step', 'renko', 'linebreak', 'range', 'highlow', 'volcandles', 'kagi', 'pnf', 'heikin', 'candles']) {
    await page.locator('.lab-type').selectOption(type);
    await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  }
  for (const tf of ['1W', '1M', '1D']) {
    await page.locator(`[data-interval="${tf}"]`).click();
    await expect(page.locator(`[data-interval="${tf}"]`)).toHaveClass(/on/);
    await expect(page.locator('.lab-head')).toContainText(`SBIN · ${tf}`);
  }
  await page.locator('.lab [data-range="All"]').click();
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');
});

test('a template saved by name comes back after a reload, with the chart state', async ({ page }) => {
  await openLab(page);
  await addIndicator(page, 'macd');
  page.once('dialog', (d) => void d.accept('E2E set'));
  await page.locator('.lab-templates').selectOption(':save');
  await expect(page.locator('.lab-templates option[value="load:E2E set"]')).toHaveCount(1);
  await page.reload();
  await page.getByLabel('command line').fill('LAB SBIN');
  await page.getByLabel('command line').press('Enter');
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['MACD 12 26 9']); // the chart state itself was kept
  await page.locator('[data-action="remove"]').click();
  await page.locator('.lab-templates').selectOption('load:E2E set');
  await expect(page.locator('.lab-lrow .lg-name')).toHaveText(['MACD 12 26 9']);
});

test('a snapshot downloads a PNG picture', async ({ page }) => {
  await openLab(page);
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Snapshot' }).click()]);
  expect(download.suggestedFilename()).toBe('SBIN-1D.png');
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  expect([...Buffer.concat(chunks).subarray(0, 4)]).toEqual([0x89, 0x50, 0x4e, 0x47]);
});

/** Drag the chart to the right twice, which scrolls back in time: with only the shown window loaded, the left of the chart would be empty. */
async function panBack(page: Page, box: { x: number; y: number; width: number; height: number }): Promise<void> {
  for (let i = 0; i < 2; i++) {
    await page.mouse.move(box.x + box.width * 0.15, box.y + box.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.85, box.y + box.height * 0.5, { steps: 12 });
    await page.mouse.up();
  }
}

/** Candle-coloured pixels (up green or down red) in the left quarter of the chart canvases under `scope`: bars to the left of the shown window, if any were loaded. */
async function candlesLeft(page: Page, scope: string): Promise<number> {
  return page.evaluate((sel) => {
    let n = 0;
    // the price pane is the widest canvas (the axes beside and below it are narrow, and the last-price label on the axis is green)
    const canvases = [...document.querySelectorAll<HTMLCanvasElement>(`${sel} canvas`)].sort((a, b) => b.width * b.height - a.width * a.height).slice(0, 1);
    for (const c of canvases) {
      const ctx = c.getContext('2d');
      if (!ctx || !c.width || !c.height) continue;
      const w = Math.floor(c.width / 4);
      const data = ctx.getImageData(0, 0, w, Math.floor(c.height * 0.8)).data;
      for (let i = 0; i < data.length; i += 4) {
        const [r, g, b] = [data[i], data[i + 1], data[i + 2]];
        const up = Math.abs(r - 38) < 12 && Math.abs(g - 194) < 12 && Math.abs(b - 129) < 12;
        const dn = Math.abs(r - 239) < 12 && Math.abs(g - 83) < 12 && Math.abs(b - 80) < 12;
        if (up || dn) n++;
      }
    }
    return n;
  }, scope);
}

test('with a short range chosen, scrolling back shows the earlier bars that are stored', async ({ page }) => {
  await openLab(page);
  await page.locator('.lab [data-range="1M"]').click();
  await page.waitForTimeout(500);
  const box = (await page.locator('.lab-host').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await panBack(page, box);
  await page.waitForTimeout(500);
  expect(await candlesLeft(page, '.lab-host')).toBeGreaterThan(200);
});

/** A number that changes when the picture on the widest canvas changes. */
async function signature(page: Page, scope: string): Promise<number> {
  return page.evaluate((sel) => {
    const c = [...document.querySelectorAll<HTMLCanvasElement>(`${sel} canvas`)].sort((x, y) => y.width * y.height - x.width * x.height)[0];
    const ctx = c?.getContext('2d');
    if (!ctx) return 0;
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    let sum = 0;
    for (let i = 0; i < data.length; i += 4) sum = (sum * 31 + data[i] + data[i + 1] * 3 + data[i + 2] * 7) % 1_000_000_007;
    return sum;
  }, scope);
}

test('the Terminal chart panel also keeps the earlier bars loaded behind a short range, so it can be scrolled back', async ({ page }) => {
  await page.locator('.panel:has(.chart-body) [data-range="1M"]').first().click();
  await page.waitForTimeout(600);
  const area = page.locator('.panel:has(.chart-body) .chart-body').first();
  const box = (await area.boundingBox())!;
  const before = await signature(page, '.panel:has(.chart-body)');
  // the earlier bars arrive a moment after the range is chosen (longer when the machine is busy): drag again until the picture moves
  await expect
    .poll(async () => {
      await panBack(page, box);
      await page.waitForTimeout(300);
      return (await signature(page, '.panel:has(.chart-body)')) !== before; // with only 22 bars loaded there is nothing to scroll to and the picture stays put
    }, { timeout: 15_000 })
    .toBe(true);
  expect(await candlesLeft(page, '.panel:has(.chart-body)')).toBeGreaterThan(200);
});

test('a renamed company reaches back past its rename: the old symbol is joined from the warehouse record, on one footing, with no cliff', async ({ page, request }) => {
  for (const [symbol, old] of [['SHRIRAMFIN', 'SRTRANSFIN'], ['ETERNAL', 'ZOMATO'], ['TATACONSUM', 'TATAGLOBAL'], ['LTIM', 'LTI'], ['TMPV', 'TATAMOTORS']] as const) {
    const body = await (await request.get(`/api/bars?symbol=${symbol}&tf=1D&limit=5000`)).json();
    const ts: number[] = body.bars.map((b: { t: number }) => b.t);
    const ownFirst = (await (await request.get(`/api/bars?symbol=${old}&tf=1D&limit=5000`)).json()).bars[0].t;
    expect(ts[0], `${symbol} starts where ${old} does`).toBe(ownFirst);
    expect(ts, 'in time order, no day twice').toEqual([...new Set(ts)].sort((a, b) => a - b));
    expect(body.note).toContain(old);
  }
  // a 5x step at the join would be the old prices on the wrong footing: Shriram Finance was split 1:5 after the rename
  const shriram = (await (await request.get('/api/bars?symbol=SHRIRAMFIN&tf=1D&limit=5000')).json()).bars as { t: number; c: number }[];
  const last = (await (await request.get('/api/bars?symbol=SRTRANSFIN&tf=1D&limit=5000')).json()).bars.at(-1).t as number;
  const at = shriram.findIndex((b) => b.t > last);
  expect(Math.abs(shriram[at]!.c / shriram[at - 1]!.c - 1)).toBeLessThan(0.1);

  await openLab(page, 'SHRIRAMFIN');
  await expect(page.locator('.lab-note')).toContainText('SRTRANSFIN');
});

/** Pixels of a clearly coloured kind (not the greys of the grid and the axes) on the widest canvas under `scope`. */
async function colourful(page: Page, scope: string): Promise<number> {
  return page.evaluate((sel) => {
    const c = [...document.querySelectorAll<HTMLCanvasElement>(`${sel} canvas`)].sort((x, y) => y.width * y.height - x.width * x.height)[0];
    const ctx = c?.getContext('2d');
    if (!ctx) return 0;
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < data.length; i += 4) if (Math.max(data[i], data[i + 1], data[i + 2]) - Math.min(data[i], data[i + 1], data[i + 2]) > 70) n++;
    return n;
  }, scope);
}

test('high-low, volume candles, Kagi and point and figure draw on any symbol, each differently, and only the price-move two turn the drawing rail off', async ({ page }) => {
  for (const symbol of ['SBIN', 'RELIANCE', 'SHRIRAMFIN', 'ETERNAL']) {
    await openLab(page, symbol);
    await page.locator('.lab [data-range="1Y"]').click();
    const seen = new Set<number>();
    for (const type of ['candles', 'highlow', 'volcandles', 'kagi', 'pnf']) {
      await page.locator('.lab-type').selectOption(type);
      await page.waitForTimeout(500);
      expect(await colourful(page, '.lab-host'), `${symbol} ${type} drew something`).toBeGreaterThan(300);
      const sig = await signature(page, '.lab-host');
      expect(seen.has(sig), `${symbol} ${type} looks like an earlier type`).toBe(false);
      seen.add(sig);
      const off = type === 'kagi' || type === 'pnf';
      await expect(page.locator('.lab-rail')).toHaveClass(off ? /off/ : /^(?!.*off).*$/);
    }
    await page.locator('.lab-type').selectOption('candles'); // the type is kept when another symbol is opened, and the head of a price-move chart is bare
  }
});

test('the new types survive an interval change and come back from a saved template', async ({ page }) => {
  await openLab(page);
  for (const type of ['volcandles', 'kagi']) {
    await page.locator('.lab-type').selectOption(type);
    for (const tf of ['1W', '1M', '1D']) {
      await page.locator(`[data-interval="${tf}"]`).click();
      await expect(page.locator(`[data-interval="${tf}"]`)).toHaveClass(/on/);
      await page.waitForTimeout(250);
      expect(await colourful(page, '.lab-host')).toBeGreaterThan(100);
    }
  }
  await page.locator('.lab-type').selectOption('pnf');
  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-type')).toHaveValue('pnf');
});
