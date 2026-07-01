// test/code-editor-puppeteer.mjs — Headless Chromium test for TokUI + CodeMirror
import puppeteer from 'puppeteer';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const testPage = path.join(__dirname, 'code-editor-test.html');

const browser = await puppeteer.launch({
  executablePath: '/usr/bin/chromium',
  headless: 'new',
  args: ['--no-sandbox', '--disable-setuid-sandbox'],
});

const page = await browser.newPage();

// Collect console messages
const logs = [];
page.on('console', msg => {
  const text = msg.text();
  if (text.startsWith('[test]')) logs.push(text);
});

page.on('pageerror', err => {
  logs.push(`[test] PAGE ERROR: ${err.message}`);
});

await page.goto(`file://${testPage}`, { waitUntil: 'networkidle0', timeout: 30000 });

// Wait for CodeMirror to fully load and render
await page.waitForSelector('.cm-editor', { timeout: 15000 }).catch(() => {
  logs.push('[test] TIMEOUT: .cm-editor not found after 15s');
});

// Additional wait for dynamic imports
await new Promise(r => setTimeout(r, 3000));

// Check DOM content
const cmContent = await page.evaluate(() => {
  const el = document.querySelector('.cm-editor');
  if (!el) return 'NO .cm-editor';
  const lines = el.querySelectorAll('.cm-line');
  return lines.length > 0
    ? `Found ${lines.length} lines: ${Array.from(lines).slice(0, 5).map(l => l.textContent.slice(0, 40)).join(' | ')}`
    : 'No .cm-line found';
});

logs.push(`[test] DOM: ${cmContent}`);

// Screenshot
await page.screenshot({ path: path.join(__dirname, 'screenshot.png'), fullPage: true });

await browser.close();

// Output results
for (const log of logs) console.log(log);

const errors = logs.filter(l => l.includes('✗') || l.includes('FAIL') || l.includes('ERROR') || l.includes('TIMEOUT'));
if (errors.length > 0) {
  console.error(`\n❌ ${errors.length} errors found`);
  process.exit(1);
} else {
  console.log('\n✅ All tests passed!');
}
