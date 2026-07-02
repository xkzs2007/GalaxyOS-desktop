// capture-rendered-dom.mjs — dump renderer DOM after boot
import { app, BrowserWindow } from 'electron';
import { writeFileSync } from 'node:fs';

app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('use-gl', 'swiftshader');
app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 820, show: true,
    backgroundColor: '#1e1e2e',
  });
  win.webContents.setBackgroundThrottling(false);

  await win.loadFile('/workspace/galaxyos-desktop/desktop-shell/renderer/index.html');
  await new Promise(r => setTimeout(r, 8000));

  // Dump rendered HTML
  const html = await win.webContents.executeJavaScript('document.body.innerHTML');
  writeFileSync('/tmp/renderer-dom.html', html);
  console.log('DOM length:', html.length);

  // Also dump the main container content
  const container = await win.webContents.executeJavaScript(
    'document.getElementById("tokui-container")?.innerHTML || "(empty)"'
  );
  writeFileSync('/tmp/container-dom.html', container);
  console.log('Container length:', container.length);

  // Screenshot
  const img = await win.webContents.capturePage();
  writeFileSync('/workspace/galaxyos-desktop/test/screenshot-real.png', img.toPNG());
  console.log('Screenshot saved');

  setTimeout(() => app.quit(), 500);
});
