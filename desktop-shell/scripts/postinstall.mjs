import { runCommand, installPlaywrightBrowser } from './install-helpers.mjs';

function main() {
  try {
    runCommand('npx', ['electron-rebuild', '-f', '-w', 'zeromq']);
    console.log('[install] Native rebuild completed');
  } catch (error) {
    console.warn('[install] Native rebuild failed, but installation can continue.');
  }

  installPlaywrightBrowser();
}

main();
