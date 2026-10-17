import { chromium } from '@playwright/test';
const b = await chromium.launch({ channel: 'chrome' });
const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
const errs = [];
p.on('console', (m) => m.type() === 'error' && errs.push(m.text()));
p.on('pageerror', (e) => errs.push(String(e)));
p.on('response', (r) => { if (r.url().includes('/api/') && r.status() >= 400) errs.push(r.status() + ' ' + r.url()); });
await p.goto('http://127.0.0.1:8700/');
await p.waitForTimeout(3000);
await p.locator('.wl-row[data-symbol="RVNL"]').click();
for (const r of ['1D', '5D']) {
  await p.locator('.panel [data-range]', { hasText: new RegExp('^' + r + '$') }).first().click().catch(() => {});
  await p.waitForTimeout(9000);
  const head = await p.locator('.panel', { has: p.locator('.pt', { hasText: 'Chart' }) }).first().innerText();
  console.log(r, JSON.stringify(head.replace(/\s+/g, ' ').slice(0, 160)));
}
console.log('errors', errs);
await p.screenshot({ path: 'C:/Users/LEGION/AppData/Local/Temp/claude/d--projects-learn-project-athena-the-fund-manager/608bc073-258a-4e5c-b2f1-60f2be42ba55/scratchpad/live2.png' });
await b.close();
