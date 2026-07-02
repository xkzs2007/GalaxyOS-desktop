// Screenshot the demo page
import puppeteer from 'puppeteer';
import path from 'node:path';

const file = path.resolve('/workspace/galaxyos-desktop/test/screenshot-demo.html');
const browser = await puppeteer.launch({
  executablePath: '/usr/bin/chromium',
  headless: 'new',
  args: ['--no-sandbox', '--disable-setuid-sandbox'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 720 });
await page.goto(`file://${file}`, { waitUntil: 'load' });
await new Promise(r => setTimeout(r, 800));
await page.screenshot({
  path: '/workspace/galaxyos-desktop/test/screenshot-desktop.png',
  fullPage: false,
});
await browser.close();
console.log('✓ Saved screenshot-desktop.png');
