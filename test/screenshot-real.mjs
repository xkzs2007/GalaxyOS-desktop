// screenshot-real.mjs — 让 Electron 自己截 renderer 页面
import { app, BrowserWindow } from 'electron';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';

app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('use-gl', 'swiftshader');
app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    show: true,
    backgroundColor: '#1e1e2e',
    webPreferences: {
      offscreen: false,
    },
  });

  // Force visible
  win.webContents.setBackgroundThrottling(false);
  win.webContents.on('did-finish-load', () => console.log('Renderer loaded'));
  win.webContents.on('dom-ready', () => console.log('DOM ready'));
  win.webContents.on('did-fail-load', (_, code, desc) => console.log('FAIL-LOAD:', code, desc));
  win.webContents.on('console-message', (_, lvl, msg, line, src) => console.log('[renderer L' + line + ']', msg, '(', src?.split('/').slice(-1)[0], ')'));
  win.webContents.on('render-process-gone', (_, details) => console.log('RENDERER-GONE:', details.reason));

  // Load the real GalaxyOS renderer
  await win.loadFile('/workspace/galaxyos-desktop/desktop-shell/renderer/index.html');
  // Wait for TokUI to fully boot + welcome page to render
  await new Promise(r => setTimeout(r, 10000));

  // Inspect DOM
  const dom = await win.webContents.executeJavaScript(`
    ({
      bodyHtml: document.body ? document.body.innerHTML.length : 0,
      hasTokUI: typeof window.TokUI !== 'undefined',
      hasUI: typeof window.UI !== 'undefined',
      containerHtml: document.getElementById('tokui-container') ? document.getElementById('tokui-container').innerHTML.length : 0,
    })
  `);
  console.log('DOM:', JSON.stringify(dom));

  const img = await win.webContents.capturePage();
  const out = '/workspace/galaxyos-desktop/test/screenshot-real.png';
  writeFileSync(out, img.toPNG());
  console.log('Saved:', out, '(', img.getSize().width, 'x', img.getSize().height, ')');
  console.log('Size:', img.getSize());

  setTimeout(() => app.quit(), 500);
});
