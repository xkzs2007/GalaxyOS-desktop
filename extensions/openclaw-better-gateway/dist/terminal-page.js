/**
 * Terminal Page Generator
 * Creates a full-featured browser terminal using xterm.js (CDN)
 * connected via SSE (server→browser) + POST (browser→server).
 */
const DEFAULT_CONFIG = {
    xtermVersion: "5.3.0",
    fitAddonVersion: "0.8.0",
    webLinksAddonVersion: "0.9.0",
};
export function generateTerminalPage(config = {}) {
    const { xtermVersion, fitAddonVersion, webLinksAddonVersion } = {
        ...DEFAULT_CONFIG,
        ...config,
    };
    const CDN = "https://cdn.jsdelivr.net/npm";
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Better Gateway Terminal</title>
  <link rel="stylesheet" href="${CDN}/xterm@${xtermVersion}/css/xterm.css">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    :root {
      --bg: #1e1e1e;
      --fg: #cccccc;
      --status-bg: #007acc;
      --status-bg-err: #cc3333;
      --status-bg-warn: #cc7700;
    }

    html, body {
      background: var(--bg);
      color: var(--fg);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      height: 100%;
      overflow: hidden;
    }

    body {
      display: flex;
      flex-direction: column;
    }

    /* ---- terminal area ---- */
    #terminal-container {
      flex: 1;
      padding: 4px;
      overflow: hidden;
    }
    #terminal-container .xterm { height: 100%; }

    /* ---- status bar ---- */
    #status-bar {
      height: 22px;
      min-height: 22px;
      background: var(--status-bg);
      color: #fff;
      display: flex;
      align-items: center;
      padding: 0 10px;
      font-size: 12px;
      gap: 12px;
      user-select: none;
    }
    #status-bar.disconnected { background: var(--status-bg-err); }
    #status-bar.connecting   { background: var(--status-bg-warn); color: #1e1e1e; }
    .status-left  { display: flex; align-items: center; gap: 6px; }
    .status-right { margin-left: auto; display: flex; align-items: center; gap: 10px; opacity: 0.85; }

    /* ---- loading overlay ---- */
    #loading-overlay {
      position: fixed; inset: 0;
      display: flex; align-items: center; justify-content: center;
      background: var(--bg);
      color: #888;
      font-size: 14px;
      z-index: 100;
      transition: opacity 0.3s;
    }
    #loading-overlay.hidden { opacity: 0; pointer-events: none; }
  </style>
</head>
<body>
  <div id="loading-overlay">Loading terminal&hellip;</div>
  <div id="terminal-container"></div>
  <div id="status-bar" class="connecting">
    <span class="status-left">
      <span id="conn-status">Connecting&hellip;</span>
    </span>
    <span class="status-right">
      <span id="term-size"></span>
    </span>
  </div>

  <script src="${CDN}/xterm@${xtermVersion}/lib/xterm.min.js"><\/script>
  <script src="${CDN}/xterm-addon-fit@${fitAddonVersion}/lib/xterm-addon-fit.min.js"><\/script>
  <script src="${CDN}/xterm-addon-web-links@${webLinksAddonVersion}/lib/xterm-addon-web-links.min.js"><\/script>
  <script>
  (function () {
    'use strict';

    // ---- guard CDN loads ----
    if (typeof Terminal === 'undefined' || typeof FitAddon === 'undefined') {
      document.getElementById('loading-overlay').textContent =
        'Failed to load terminal assets. Check your network / CDN access.';
      return;
    }

    // ---- create terminal ----
    var term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', Menlo, Monaco, 'Courier New', monospace",
      theme: {
        background:       '#1e1e1e',
        foreground:       '#cccccc',
        cursor:           '#aeafad',
        cursorAccent:     '#1e1e1e',
        selectionBackground: '#264f78',
        black:   '#000000', red:     '#cd3131', green:   '#0dbc79',
        yellow:  '#e5e510', blue:    '#2472c8', magenta: '#bc3fbc',
        cyan:    '#11a8cd', white:   '#e5e5e5',
        brightBlack: '#666666', brightRed:     '#f14c4c', brightGreen: '#23d18b',
        brightYellow:'#f5f543', brightBlue:    '#3b8eea', brightMagenta:'#d670d6',
        brightCyan:  '#29b8db', brightWhite:   '#e5e5e5'
      },
      scrollback: 10000,
      allowProposedApi: true,
    });

    var fitAddon  = new FitAddon.FitAddon();
    var linksAddon = (typeof WebLinksAddon !== 'undefined')
      ? new WebLinksAddon.WebLinksAddon()
      : null;

    term.loadAddon(fitAddon);
    if (linksAddon) term.loadAddon(linksAddon);

    var container = document.getElementById('terminal-container');
    term.open(container);
    fitAddon.fit();

    // Remove loading overlay
    var overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.classList.add('hidden');

    // ---- DOM refs ----
    var statusBar  = document.getElementById('status-bar');
    var connStatus = document.getElementById('conn-status');
    var termSize   = document.getElementById('term-size');

    function showSize() {
      var d = fitAddon.proposeDimensions();
      if (d) termSize.textContent = d.cols + '\\u00d7' + d.rows;
    }
    showSize();

    function setStatus(cls, text) {
      statusBar.className = cls;
      connStatus.textContent = text;
    }

    // ---- SSE + POST transport ----
    var STREAM_URL = '/better-gateway/terminal/stream';
    var INPUT_URL  = '/better-gateway/terminal/input';
    var RESIZE_URL = '/better-gateway/terminal/resize';

    var sid = null;          // session ID from server
    var evtSource = null;    // EventSource instance
    var reconnAttempts = 0;
    var MAX_RECONN = 10;
    var RECONN_DELAY = 2000;
    var reconnTimer = null;

    // ---- input batching ----
    // Buffer keystrokes and flush every 30ms to reduce POST overhead
    var inputBuffer = '';
    var inputTimer  = null;
    var INPUT_FLUSH_MS = 30;

    function flushInput() {
      inputTimer = null;
      if (!inputBuffer || !sid) return;
      var payload = inputBuffer;
      inputBuffer = '';
      fetch(INPUT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sid: sid, data: payload }),
      }).catch(function (err) {
        console.warn('[terminal] input POST failed:', err);
      });
    }

    function sendInput(data) {
      inputBuffer += data;
      if (!inputTimer) {
        inputTimer = setTimeout(flushInput, INPUT_FLUSH_MS);
      }
    }

    function sendResize(cols, rows) {
      if (!sid) return;
      fetch(RESIZE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sid: sid, cols: cols, rows: rows }),
      }).catch(function (err) {
        console.warn('[terminal] resize POST failed:', err);
      });
    }

    // ---- base64 decode helper ----
    function b64decode(str) {
      try {
        // Use atob + TextDecoder for proper UTF-8 handling
        var bytes = atob(str);
        var arr = new Uint8Array(bytes.length);
        for (var i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        return new TextDecoder().decode(arr);
      } catch (e) {
        return str;
      }
    }

    // ---- connect via SSE ----
    function connect() {
      if (evtSource) {
        try { evtSource.close(); } catch (_e) {}
      }
      sid = null;

      setStatus('connecting',
        reconnAttempts > 0
          ? 'Reconnecting (' + reconnAttempts + '/' + MAX_RECONN + ')\\u2026'
          : 'Connecting\\u2026');

      evtSource = new EventSource(STREAM_URL);

      // Session ID (first event from server)
      evtSource.addEventListener('session', function (ev) {
        try {
          var data = JSON.parse(ev.data);
          sid = data.sid;
          reconnAttempts = 0;
          setStatus('', 'Connected');
          console.log('[terminal] session:', sid);
          // Send initial terminal size
          var d = fitAddon.proposeDimensions();
          if (d) sendResize(d.cols, d.rows);
        } catch (e) {
          console.warn('[terminal] bad session event:', e);
        }
      });

      // PTY output (default unnamed event) — base64 encoded
      evtSource.addEventListener('message', function (ev) {
        term.write(b64decode(ev.data));
      });

      // PTY exited
      evtSource.addEventListener('exit', function (ev) {
        try {
          var data = JSON.parse(ev.data);
          term.write(
            '\\r\\n\\x1b[90m[Process exited with code ' + data.code + ']\\x1b[0m\\r\\n');
        } catch (_e) {}
        setStatus('disconnected', 'Process exited');
        sid = null;
        try { evtSource.close(); } catch (_e2) {}
      });

      // Server-sent error (e.g. node-pty not installed)
      evtSource.addEventListener('error', function (ev) {
        // EventSource fires 'error' for both server-sent error events
        // and connection failures. Check if we got data.
        if (ev.data) {
          try {
            var data = JSON.parse(ev.data);
            var msg = data.error || 'Unknown server error';
            setStatus('disconnected', msg);
            term.write('\\r\\n\\x1b[1;31m ' + msg + '\\x1b[0m\\r\\n');
            try { evtSource.close(); } catch (_e) {}
            return;
          } catch (_e) {}
        }

        // Connection-level error — attempt reconnect
        try { evtSource.close(); } catch (_e) {}
        sid = null;
        if (reconnAttempts < MAX_RECONN) {
          reconnAttempts++;
          setStatus('disconnected',
            'Disconnected \\u2014 retry ' + reconnAttempts + '/' + MAX_RECONN);
          reconnTimer = setTimeout(connect, RECONN_DELAY);
        } else {
          setStatus('disconnected',
            'Connection failed \\u2014 click to retry');
          statusBar.style.cursor = 'pointer';
          statusBar.onclick = function () {
            statusBar.style.cursor = '';
            statusBar.onclick = null;
            reconnAttempts = 0;
            connect();
          };
        }
      });
    }

    connect();

    // ---- terminal → server ----
    term.onData(function (data) {
      if (sid) sendInput(data);
    });

    // ---- resize handling ----
    function handleResize() {
      fitAddon.fit();
      showSize();
      if (sid) {
        var d = fitAddon.proposeDimensions();
        if (d) sendResize(d.cols, d.rows);
      }
    }

    window.addEventListener('resize', handleResize);

    // ResizeObserver catches iframe resizes (the parent split-handle drag)
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () {
        // Small delay lets the layout settle before we measure
        setTimeout(handleResize, 30);
      }).observe(container);
    }

    // Parent frame can post messages to trigger resize / focus
    window.addEventListener('message', function (ev) {
      if (!ev.data) return;
      if (ev.data.type === 'resize')  setTimeout(handleResize, 50);
      if (ev.data.type === 'focus')   term.focus();
    });

    // ---- Ctrl+L — toggle chat sidebar (forward to parent frame) ----
    // Ctrl only, NOT Cmd — Cmd+L is browser "focus URL bar"
    window.addEventListener('keydown', function (event) {
      if (!event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if ((event.key || '').toLowerCase() !== 'l') return;
      event.preventDefault();
      event.stopPropagation();
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'toggleChat' }, '*');
      }
    }, true);

    // ---- focus ----
    container.addEventListener('click', function () { term.focus(); });
    term.focus();

  })();
  <\/script>
</body>
</html>`;
}
//# sourceMappingURL=terminal-page.js.map