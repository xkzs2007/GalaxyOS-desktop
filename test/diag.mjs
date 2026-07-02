// capture full renderer error
import puppeteer from 'puppeteer';
import path from 'node:path';

const file = path.resolve('/workspace/galaxyos-desktop/desktop-shell/renderer/index.html');
const browser = await puppeteer.launch({
  executablePath: '/usr/bin/chromium',
  headless: 'new',
  args: ['--no-sandbox', '--disable-setuid-sandbox'],
});
const page = await browser.newPage();
page.on('pageerror', e => console.error('PAGE ERROR:', e.message, e.stack?.split('\n').slice(0,3).join(' | ')));
page.on('console', m => console.log('CONSOLE:', m.type(), m.text().slice(0, 200)));
page.on('requestfailed', r => console.log('REQ FAIL:', r.url().slice(-50), r.failure()?.errorText));

try {
  await page.goto(`file://${file}`, { waitUntil: 'load', timeout: 10000 });
} catch (e) {
  console.log('GOTO ERR:', e.message);
}
await new Promise(r => setTimeout(r, 3000));
await browser.close();
console.log('--- DONE ---');
