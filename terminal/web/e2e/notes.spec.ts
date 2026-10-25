import { expect, test, type Page } from '@playwright/test';

const reset = async (page: Page): Promise<void> => {
  await page.goto('/');
  await page.evaluate(async () => {
    window.localStorage.clear();
    await fetch('/api/notebook', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ version: 1, notes: [] }) });
  });
  await page.reload();
};

async function openNotes(page: Page): Promise<void> {
  if ((await page.locator('.notes').count()) === 0) await page.locator('select.add-select').selectOption('notes'); // the layout saved after the first visit already has the panel
  await expect(page.locator('.notes')).toHaveCount(1);
  await expect(page.locator('.notes')).toBeVisible();
}

test('a note is written for the linked symbol, kept across a reload, searched, edited and deleted', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await reset(page);
  await openNotes(page);
  await expect(page.locator('.notes-list .note').first()).toContainText('No notes yet');

  await page.getByLabel('note text').fill('Margin guidance is the thing to watch.');
  await page.getByLabel('note tag').fill('Margin');
  await page.locator('.notes button[data-action="save"]').click();
  const items = page.locator('.notes .note-item');
  await expect(items).toHaveCount(1);
  await expect(items.first()).toContainText('Margin guidance');
  await expect(items.first()).toContainText('margin');

  await page.waitForTimeout(900); // saved to the server 400 ms after the last change
  const onServer = await page.evaluate(async () => (await fetch('/api/notebook')).json());
  expect(onServer.notes).toHaveLength(1);
  expect(onServer.notes[0].text).toBe('Margin guidance is the thing to watch.');

  await page.reload();
  await openNotes(page);
  await expect(page.locator('.notes .note-item')).toHaveCount(1); // read back from the server

  await page.getByLabel('note text').fill('A second note, untagged.');
  await page.locator('.notes button[data-action="save"]').click();
  await expect(page.locator('.notes .note-item')).toHaveCount(2);
  await page.getByLabel('search notes').fill('second');
  await expect(page.locator('.notes .note-item')).toHaveCount(1);
  await page.getByLabel('search notes').fill('');

  await page.getByLabel(/^edit note/).first().click();
  await page.getByLabel('note text').fill('Edited text.');
  await page.locator('.notes button[data-action="save"]').click();
  await expect(page.locator('.notes .note-item').first()).toContainText('Edited text.');

  await page.getByLabel(/^delete note/).first().click();
  await expect(page.locator('.notes .note-item')).toHaveCount(1);
  await page.locator('.notes button[data-scope="all"]').click();
  await expect(page.locator('.notes .note-item')).toHaveCount(1);
  expect(errors).toEqual([]);
});
