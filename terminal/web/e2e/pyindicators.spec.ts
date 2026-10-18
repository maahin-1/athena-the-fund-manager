import { expect, test, type Page } from '@playwright/test';

const command = (page: Page) => page.locator('input[placeholder*="symbol or command"]');

async function openLab(page: Page, symbol = 'SBIN'): Promise<void> {
  await page.goto('/');
  await command(page).fill(`LAB ${symbol}`);
  await command(page).press('Enter');
  await expect(page.locator('.lab-host canvas').first()).toBeVisible();
  await expect(page.locator('.lab-head')).toContainText(`${symbol} · 1D`);
}

const row = (page: Page) => page.locator('.lab-item-row[data-file="e2e_average.py"]');

async function switchOff(page: Page): Promise<void> {
  await page.locator('.lab-ind').click();
  const off = row(page).getByRole('button', { name: 'Switch off' });
  if (!(await off.count())) return;
  const listed = page.waitForResponse((r) => r.url().endsWith('/api/pyind') && r.request().method() === 'GET'); // the list read after it is switched off
  await off.click();
  await listed;
}

test('a Python file is listed switched off, switched on after a plain warning, drawn, and switched off again', async ({ page }) => {
  await openLab(page);
  await switchOff(page); // a run that was stopped half way leaves it on
  await page.reload();
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-head')).toContainText('SBIN · 1D');

  await page.locator('.lab-ind').click();
  await expect(row(page)).toContainText('E2E average');
  await expect(row(page)).toContainText('off');
  await expect(row(page).locator('[data-indicator]')).toHaveCount(0); // cannot be added while off

  await row(page).getByRole('button', { name: 'Switch on…' }).click();
  const dialog = page.getByRole('dialog', { name: 'switch on e2e_average.py' });
  await expect(dialog).toContainText('NOT sandboxed');
  await expect(dialog).toContainText('SHA-256');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(dialog).toHaveCount(0);
  await page.locator('.lab-ind').click();
  await expect(row(page).locator('[data-indicator]')).toHaveCount(0); // cancelling switched nothing on

  await row(page).getByRole('button', { name: 'Switch on…' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Switch on', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);

  await page.locator('.lab-ind').click();
  await row(page).locator('[data-indicator="py:e2e_average.py"]').click();
  await expect(page.locator('.lab-legend .lab-lrow', { hasText: 'avg3' })).toHaveCount(1); // the line the file returned, named in the legend once the server has run it
  await expect(page.locator('.lab-legend .lab-lrow', { hasText: 'avg3' })).toContainText(/\d+\.\d\d/);
  await expect(page.locator('.lab-window')).toContainText('avg3');

  await page.reload(); // the chart is saved with the file's indicator: it comes back, and runs again
  await command(page).fill('LAB SBIN');
  await command(page).press('Enter');
  await expect(page.locator('.lab-legend .lab-lrow', { hasText: 'avg3' })).toHaveCount(1);

  await switchOff(page);
  await page.keyboard.press('Escape');
  await page.locator('.lab-ind').click();
  await expect(row(page).locator('[data-indicator]')).toHaveCount(0);
  await page.keyboard.press('Escape');
});
