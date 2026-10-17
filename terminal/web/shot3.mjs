import { chromium } from '@playwright/test';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
const errs = [];
p.on('console', (m) => m.type() === 'error' && errs.push(m.text()));
p.on('pageerror', (e) => errs.push(String(e)));
await p.goto('http://127.0.0.1:8700/');
await p.waitForTimeout(2500);
for (const t of ['IRFC', 'suzlon', 'tata motors', 'Reliance Industries', 'rvnl chart']) {
  await p.getByLabel('command line').fill(t);
  await p.getByLabel('command line').press('Enter');
  await p.waitForTimeout(3500);
  const status = await p.locator('.statusline').innerText();
  const chart = (await p.locator('.panel', { has: p.locator('.pt', { hasText: 'Chart' }) }).first().locator('.pt').innerText()).trim();
  const quote = (await p.locator('.panel', { has: p.locator('.pt', { hasText: 'Quote' }) }).first().innerText()).replace(/\s+/g, ' ').slice(0, 60);
  console.log(JSON.stringify(t), '| status:', status, '| chart:', chart, '| quote:', quote);
}
console.log('errors', errs);
await b.close();
