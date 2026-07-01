import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

await page.goto('http://127.0.0.1:8899/tokui-preview.html', { waitUntil: 'networkidle', timeout: 30000 });

// Wait for TokUI to render
await page.waitForSelector('[data-ready]', { timeout: 15000 });
await page.waitForTimeout(2000);

// Full page screenshot
await page.screenshot({ path: '/workspace/desktop-shell/renderer/preview-full.png', fullPage: true });
console.log('[ok] preview-full.png');

// Section screenshots
const sections = await page.$$('.section');
for (let idx = 0; idx < sections.length; idx++) {
  await sections[idx].scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const num = String(idx + 1).padStart(2, '0');
  await sections[idx].screenshot({ path: `/workspace/desktop-shell/renderer/preview-${num}.png` });
  console.log(`[ok] preview-${num}.png (section ${idx + 1}/${sections.length})`);
}

await browser.close();
console.log('All screenshots done');
