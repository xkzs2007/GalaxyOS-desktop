(function () {
  "use strict";

  const INJECT_VERSION = "2026-02-11.1";

  const config = window.__BETTER_GATEWAY_CONFIG__ || {
    reconnectIntervalMs: 3000,
    maxReconnectAttempts: 10,
    autoReconnectOnGap: true,
    autoReconnectOnVisibility: true,
    visibilityReconnectThresholdMs: 15000,
  };

  let reconnectAttempts = 0;
  let statusIndicator = null;
  let originalWebSocket = window.WebSocket;
  let activeConnections = new Set();
  let currentState = "connected";
  let ideTabInjected = false;
  let hiddenSince = null;
  let pendingForcedReconnect = false;
  let lastConnectionArgs = null;
  let lastPageReloadAt = 0;

  function createStatusIndicator() {
    if (statusIndicator) return statusIndicator;

    statusIndicator = document.createElement("div");
    statusIndicator.id = "better-gateway-status";
    statusIndicator.style.cssText = `
      position: fixed;
      bottom: 12px;
      left: 12px;
      padding: 8px 14px;
      border-radius: 6px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      font-weight: 500;
      z-index: 999999;
      transition: all 0.3s ease;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      cursor: pointer;
      user-select: none;
    `;

    // Click handler - always refresh the page
    statusIndicator.addEventListener("click", function () {
      window.location.reload();
    });

    // Hover effect
    statusIndicator.addEventListener("mouseenter", function () {
      statusIndicator.style.transform = "scale(1.05)";
      statusIndicator.style.boxShadow = "0 4px 12px rgba(0, 0, 0, 0.25)";
    });
    statusIndicator.addEventListener("mouseleave", function () {
      statusIndicator.style.transform = "scale(1)";
      statusIndicator.style.boxShadow = "0 2px 8px rgba(0, 0, 0, 0.15)";
    });

    document.body.appendChild(statusIndicator);
    return statusIndicator;
  }

  function updateStatus(state, message) {
    currentState = state;
    const indicator = createStatusIndicator();

    const styles = {
      connected: {
        background: "#10b981",
        color: "#ffffff",
        icon: "●",
      },
      disconnected: {
        background: "#ef4444",
        color: "#ffffff",
        icon: "●",
        clickHint: " (click to refresh)",
      },
      reconnecting: {
        background: "#f59e0b",
        color: "#ffffff",
        icon: "↻",
      },
      failed: {
        background: "#6b7280",
        color: "#ffffff",
        icon: "↻",
        clickHint: " (click to refresh)",
      },
    };

    const style = styles[state] || styles.disconnected;
    indicator.style.background = style.background;
    indicator.style.color = style.color;
    
    const displayMessage = message + (style.clickHint || "");
    indicator.innerHTML = `<span style="margin-right: 6px;">${style.icon}</span>${displayMessage}`;
    indicator.title = "Click to refresh page";

    if (state === "connected") {
      setTimeout(function () {
        indicator.style.opacity = "0.7";
      }, 2000);
    } else {
      indicator.style.opacity = "1";
    }
  }

  // ==================== IDE Embedded View ====================

  // View modes: 'chat' | 'ide' | 'split'
  let currentViewMode = 'chat';
  try {
    const saved = localStorage.getItem('better-gateway-view-mode');
    if (saved === 'ide' || saved === 'split') currentViewMode = saved;
  } catch (_e) { /* localStorage unavailable */ }

  // SVG icon for code/IDE (matches gateway's feather icon style)
  const IDE_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="16 18 22 12 16 6"></polyline>
      <polyline points="8 6 2 12 8 18"></polyline>
    </svg>
  `;

  // SVG icon for split view
  const SPLIT_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
      <line x1="12" y1="3" x2="12" y2="21"></line>
    </svg>
  `;

  // SVG icon for CLI/Terminal (prompt arrow + underscore)
  const CLI_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="4 17 10 11 4 5"></polyline>
      <line x1="12" y1="19" x2="20" y2="19"></line>
    </svg>
  `;

  // SVG icon for send button (up arrow)
  const SEND_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <line x1="12" y1="19" x2="12" y2="5"></line>
      <polyline points="5 12 12 5 19 12"></polyline>
    </svg>
  `;

  // SVG icon for stop button (filled square)
  const STOP_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="currentColor" stroke="none">
      <rect x="5" y="5" width="14" height="14" rx="2" ry="2"></rect>
    </svg>
  `;

  // SVG icon for new session (document with plus)
  const NEW_SESSION_ICON_SVG = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
      <polyline points="14 2 14 8 20 8"></polyline>
      <line x1="12" y1="18" x2="12" y2="12"></line>
      <line x1="9" y1="15" x2="15" y2="15"></line>
    </svg>
  `;

  function findChatNavItem() {
    var candidates = document.querySelectorAll('.nav-item');
    for (var i = 0; i < candidates.length; i++) {
      var item = candidates[i];
      var href = String(item.getAttribute('href') || '');
      var text = String(item.textContent || '').trim().toLowerCase();
      try {
        if (href) {
          var url = new URL(href, window.location.origin);
          if (url.pathname === '/chat' || url.pathname === '/better-gateway/chat' || /\/chat$/.test(url.pathname)) {
            return item;
          }
        }
      } catch (_e) {
        if (href === '/chat' || href === '/better-gateway/chat' || /\/chat(\?|$)/.test(href)) {
          return item;
        }
      }
      if (text === 'chat') {
        return item;
      }
    }
    return null;
  }

  function createIdeNavItem() {
    const item = document.createElement("a");
    item.id = "better-gateway-ide-nav";
    item.href = "#ide";
    item.className = "nav-item";
    item.title = "IDE - Code Editor (click for split view, Shift+click for IDE only)";
    item.innerHTML = `
      <span class="nav-item__icon" aria-hidden="true">${IDE_ICON_SVG}</span>
      <span class="nav-item__text">IDE</span>
    `;

    item.addEventListener("click", function (e) {
      e.preventDefault();
      if (e.shiftKey) {
        // Shift+click toggles split view (IDE + chat)
        if (currentViewMode === 'split') {
          setViewMode('chat');
        } else {
          setViewMode('split');
        }
      } else {
        // Regular click toggles IDE-only view (no chat sidebar by default)
        if (currentViewMode === 'ide') {
          setViewMode('chat');
        } else {
          setViewMode('ide');
        }
      }
    });

    return item;
  }

  function createCliNavItem() {
    const item = document.createElement("a");
    item.id = "better-gateway-cli-nav";
    item.href = "#cli";
    item.className = "nav-item";
    item.title = "CLI - Terminal (click for split view, Shift+click for CLI only)";
    item.innerHTML = `
      <span class="nav-item__icon" aria-hidden="true">${CLI_ICON_SVG}</span>
      <span class="nav-item__text">CLI</span>
    `;

    item.addEventListener("click", function (e) {
      e.preventDefault();
      if (e.shiftKey) {
        // Shift+click toggles split-cli view (terminal + chat)
        if (currentViewMode === 'split-cli') {
          setViewMode('chat');
        } else {
          setViewMode('split-cli');
        }
      } else {
        // Regular click toggles CLI-only view (no chat sidebar by default)
        if (currentViewMode === 'cli') {
          setViewMode('chat');
        } else {
          setViewMode('cli');
        }
      }
    });

    return item;
  }

  function createIdeFrame() {
    const frame = document.createElement("iframe");
    frame.id = "better-gateway-ide-frame";
    frame.src = `/better-gateway/ide?v=${encodeURIComponent(INJECT_VERSION)}`;
    frame.style.cssText = `
      border: none;
      background: #1e1e1e;
      display: none;
    `;
    return frame;
  }

  function createCliFrame() {
    const frame = document.createElement("iframe");
    frame.id = "better-gateway-cli-frame";
    frame.src = `/better-gateway/terminal?v=${encodeURIComponent(INJECT_VERSION)}`;
    frame.style.cssText = `
      border: none;
      background: #1e1e1e;
      display: none;
    `;
    return frame;
  }

  function createSplitResizeHandle() {
    const handle = document.createElement("div");
    handle.id = "better-gateway-split-handle";
    handle.style.cssText = `
      width: 4px;
      cursor: col-resize;
      background: #3c3c3c;
      display: none;
      flex-shrink: 0;
    `;
    handle.addEventListener("mouseenter", function() {
      handle.style.background = "#0078d4";
    });
    handle.addEventListener("mouseleave", function() {
      if (!handle.dataset.dragging) {
        handle.style.background = "#3c3c3c";
      }
    });
    return handle;
  }

  function createChatToggleButton() {
    const button = document.createElement("button");
    button.id = "better-gateway-chat-toggle";
    button.type = "button";
    button.style.cssText = `
      position: absolute;
      top: 12px;
      right: 12px;
      width: 30px;
      height: 30px;
      border-radius: 6px;
      border: 1px solid #3c3c3c;
      background: #252526;
      color: #d4d4d4;
      cursor: pointer;
      z-index: 20;
      display: none;
      font-size: 16px;
      line-height: 1;
      padding: 0;
    `;
    button.addEventListener("mouseenter", function() {
      button.style.background = "#2d2d2d";
      button.style.borderColor = "#4f4f4f";
    });
    button.addEventListener("mouseleave", function() {
      button.style.background = "#252526";
      button.style.borderColor = "#3c3c3c";
    });
    button.addEventListener("click", function() {
      if (currentViewMode === "split") {
        setViewMode("ide");
      } else if (currentViewMode === "ide") {
        setViewMode("split");
      } else if (currentViewMode === "split-cli") {
        setViewMode("cli");
      } else if (currentViewMode === "cli") {
        setViewMode("split-cli");
      }
    });
    return button;
  }

  function getActiveFrame() {
    var cliFrame = document.getElementById("better-gateway-cli-frame");
    var ideFrame = document.getElementById("better-gateway-ide-frame");
    if (cliFrame && cliFrame.style.display !== "none") return cliFrame;
    return ideFrame;
  }

  function setupSplitResize() {
    const handle = document.getElementById("better-gateway-split-handle");
    if (!handle) return;

    let isDragging = false;

    handle.addEventListener("mousedown", function(e) {
      isDragging = true;
      handle.dataset.dragging = "true";
      handle.style.background = "#0078d4";
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      // Keep receiving mouse events when cursor crosses the iframe.
      var frame = getActiveFrame();
      if (frame) frame.style.pointerEvents = "none";
      e.preventDefault();
    });

    document.addEventListener("mousemove", function(e) {
      if (!isDragging) return;
      var frame = getActiveFrame();
      if (!frame) return;
      const container = frame.parentElement;
      if (!container) return;
      
      const containerRect = container.getBoundingClientRect();
      const newWidth = e.clientX - containerRect.left;
      const minWidth = 300;
      const maxWidth = containerRect.width - 350; // Leave room for chat
      const clampedWidth = Math.min(Math.max(newWidth, minWidth), maxWidth);

      frame.style.width = clampedWidth + "px";
      frame.style.flex = "none";
    });

    document.addEventListener("mouseup", function() {
      if (isDragging) {
        isDragging = false;
        delete handle.dataset.dragging;
        handle.style.background = "#3c3c3c";
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        var frame = getActiveFrame();
        if (frame) frame.style.pointerEvents = "";
      }
    });
  }

  function setViewMode(mode) {
    const main = document.querySelector("main.content");
    if (!main) return;

    // Create or get the split container (flex wrapper for frames | handle | chat)
    let splitWrapper = document.getElementById("better-gateway-split-wrapper");
    let ideFrame = document.getElementById("better-gateway-ide-frame");
    let cliFrame = document.getElementById("better-gateway-cli-frame");
    let splitHandle = document.getElementById("better-gateway-split-handle");

    if (!splitWrapper) {
      // Create wrapper: flex row container that replaces main in the layout
      splitWrapper = document.createElement("div");
      splitWrapper.id = "better-gateway-split-wrapper";
      splitWrapper.style.cssText = `
        display: flex;
        flex-direction: row;
        flex: 1;
        overflow: hidden;
        min-width: 0;
        min-height: 0;
        position: relative;
      `;
      ideFrame = createIdeFrame();
      cliFrame = createCliFrame();
      splitHandle = createSplitResizeHandle();
      const chatToggleButton = createChatToggleButton();

      // Wrap main: replace main with wrapper, put main inside wrapper
      const parent = main.parentNode;
      parent.replaceChild(splitWrapper, main);
      splitWrapper.appendChild(ideFrame);
      splitWrapper.appendChild(cliFrame);
      splitWrapper.appendChild(splitHandle);
      splitWrapper.appendChild(main);
      splitWrapper.appendChild(chatToggleButton);

      setupSplitResize();
    } else {
      ideFrame = document.getElementById("better-gateway-ide-frame");
      cliFrame = document.getElementById("better-gateway-cli-frame");
      splitHandle = document.getElementById("better-gateway-split-handle");
    }

    const chatNav = findChatNavItem();
    const ideNav = document.getElementById("better-gateway-ide-nav");
    const cliNav = document.getElementById("better-gateway-cli-nav");
    const chatToggleButton = document.getElementById("better-gateway-chat-toggle");

    // Helper: hide both content frames
    function hideFrames() {
      if (ideFrame) { ideFrame.style.display = "none"; }
      if (cliFrame) { cliFrame.style.display = "none"; }
    }

    // Helper: show a frame in fullscreen mode
    function showFrameFullscreen(frame) {
      frame.style.display = "block";
      frame.style.width = "100%";
      frame.style.height = "100%";
      frame.style.flex = "1";
      frame.style.minWidth = "";
    }

    // Helper: show a frame in split mode (left panel)
    function showFrameSplit(frame) {
      frame.style.display = "block";
      frame.style.width = "55%";
      frame.style.height = "100%";
      frame.style.flex = "none";
      frame.style.minWidth = "280px";
    }

    // Apply the view mode
    if (mode === 'chat') {
      // Chat only — hide both frames, chat takes full width
      hideFrames();
      splitHandle.style.display = "none";
      main.style.display = "";
      main.style.flex = "1";
      main.style.width = "";
      main.style.minWidth = "";
      main.style.overflow = "";
      
      if (chatNav) chatNav.classList.add("active");
      if (ideNav) ideNav.classList.remove("active");
      if (cliNav) cliNav.classList.remove("active");
      if (chatToggleButton) chatToggleButton.style.display = "none";
      
    } else if (mode === 'ide') {
      // IDE only — IDE fullscreen
      hideFrames();
      showFrameFullscreen(ideFrame);
      splitHandle.style.display = "none";
      main.style.display = "none";
      
      if (chatNav) chatNav.classList.remove("active");
      if (ideNav) ideNav.classList.add("active");
      if (cliNav) cliNav.classList.remove("active");
      if (chatToggleButton) {
        chatToggleButton.style.display = "block";
        chatToggleButton.textContent = "←";
        chatToggleButton.title = "Show Chat Sidebar (Cmd/Ctrl+L)";
      }
      
    } else if (mode === 'split') {
      // Split view: IDE (left) | handle | Chat (right)
      hideFrames();
      showFrameSplit(ideFrame);
      splitHandle.style.display = "block";
      main.style.display = "";
      main.style.flex = "1";
      main.style.width = "";
      main.style.minWidth = "320px";
      main.style.overflow = "auto";
      
      if (chatNav) chatNav.classList.add("active");
      if (ideNav) ideNav.classList.add("active");
      if (cliNav) cliNav.classList.remove("active");
      if (chatToggleButton) {
        chatToggleButton.style.display = "block";
        chatToggleButton.textContent = "→";
        chatToggleButton.title = "Hide Chat Sidebar (Cmd/Ctrl+L)";
      }

    } else if (mode === 'cli') {
      // CLI only — Terminal fullscreen
      hideFrames();
      showFrameFullscreen(cliFrame);
      splitHandle.style.display = "none";
      main.style.display = "none";
      
      if (chatNav) chatNav.classList.remove("active");
      if (ideNav) ideNav.classList.remove("active");
      if (cliNav) cliNav.classList.add("active");
      if (chatToggleButton) {
        chatToggleButton.style.display = "block";
        chatToggleButton.textContent = "←";
        chatToggleButton.title = "Show Chat Sidebar (Cmd/Ctrl+L)";
      }

    } else if (mode === 'split-cli') {
      // Split view: CLI (left) | handle | Chat (right)
      hideFrames();
      showFrameSplit(cliFrame);
      splitHandle.style.display = "block";
      main.style.display = "";
      main.style.flex = "1";
      main.style.width = "";
      main.style.minWidth = "320px";
      main.style.overflow = "auto";
      
      if (chatNav) chatNav.classList.add("active");
      if (ideNav) ideNav.classList.remove("active");
      if (cliNav) cliNav.classList.add("active");
      if (chatToggleButton) {
        chatToggleButton.style.display = "block";
        chatToggleButton.textContent = "→";
        chatToggleButton.title = "Hide Chat Sidebar (Cmd/Ctrl+L)";
      }
    }

    // Notify CLI iframe to resize/focus when becoming visible
    if ((mode === 'cli' || mode === 'split-cli') && cliFrame && cliFrame.contentWindow) {
      setTimeout(function() {
        try {
          cliFrame.contentWindow.postMessage({ type: 'resize' }, '*');
          cliFrame.contentWindow.postMessage({ type: 'focus' }, '*');
        } catch (_e) {}
      }, 100);
    }

    currentViewMode = mode;
    // Any non-chat mode needs click interception on nav items
    ideViewActive = (mode !== 'chat');
    console.log("[BetterGateway] View mode:", mode);
  }

  // Legacy function names for compatibility
  function toggleIdeView() {
    if (currentViewMode === 'ide' || currentViewMode === 'split') {
      setViewMode('chat');
    } else {
      setViewMode('ide');
    }
  }

  function showIdeView() {
    setViewMode('ide');
  }

  function showChatView() {
    setViewMode('chat');
  }

  function setupIdeHotkeys() {
    // Ctrl+L — toggle chat sidebar in IDE/CLI views
    // Ctrl only, NOT Cmd — Cmd+L is browser "focus URL bar"
    window.addEventListener("keydown", function (event) {
      if (!event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if (String(event.key || "").toLowerCase() !== "l") return;

      const target = event.target;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }

      if (!ideViewActive) return;

      event.preventDefault();
      if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
      event.stopPropagation();

      if (currentViewMode === "split") {
        setViewMode("ide");
      } else if (currentViewMode === "ide") {
        setViewMode("split");
      } else if (currentViewMode === "split-cli") {
        setViewMode("cli");
      } else if (currentViewMode === "cli") {
        setViewMode("split-cli");
      }
    }, true);

    // Ctrl+` — toggle terminal (like VS Code)
    window.addEventListener("keydown", function (event) {
      if (!event.ctrlKey || event.altKey || event.shiftKey || event.metaKey) return;
      if (event.code !== "Backquote") return;

      const target = event.target;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }

      event.preventDefault();
      if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
      event.stopPropagation();

      if (currentViewMode === "cli" || currentViewMode === "split-cli") {
        setViewMode("chat");
      } else {
        setViewMode("split-cli");
      }
    }, true);

    // Listen for postMessage from IDE/CLI iframes (e.g. Cmd/Ctrl+L)
    window.addEventListener("message", function (event) {
      if (!event.data || !event.data.type) return;
      if (event.data.type === "toggleChat") {
        if (currentViewMode === "split") {
          setViewMode("ide");
        } else if (currentViewMode === "ide") {
          setViewMode("split");
        } else if (currentViewMode === "split-cli") {
          setViewMode("cli");
        } else if (currentViewMode === "cli") {
          setViewMode("split-cli");
        }
      }
    });
  }

  // Track if IDE/split view is active (for nav click handlers)
  let ideViewActive = false;

  function injectIdeNavItem() {
    if (ideTabInjected) return false;

    // Don't inject on standalone pages (IDE / Terminal)
    if (window.location && (
      window.location.pathname === "/better-gateway/ide" ||
      window.location.pathname === "/better-gateway/terminal"
    )) {
      return false;
    }

    // Check if BOTH nav items already exist — only then skip entirely.
    // If only IDE exists (old inject.js ran first), we still need to add CLI.
    var existingIdeNav = document.getElementById("better-gateway-ide-nav");
    var existingCliNav = document.getElementById("better-gateway-cli-nav");
    if (existingIdeNav && existingCliNav) {
      ideTabInjected = true;
      return false;
    }

    // Find the Chat nav item and use its parent container as the insertion point.
    // Be tolerant of Control UI route changes (query strings, base paths, etc).
    const chatLink = findChatNavItem();
    if (!chatLink) {
      return false;
    }

    const navItems = chatLink.parentElement;
    if (!navItems) {
      return false;
    }

    // Intercept Chat link clicks when IDE/CLI is active
    chatLink.addEventListener("click", function (e) {
      if (ideViewActive) {
        e.preventDefault();
        e.stopPropagation();
        showChatView();
      }
    });

    // For all other nav items, restore main content before navigation
    // This ensures the gateway's SPA routing works properly
    const allNavItems = document.querySelectorAll(".nav-item");
    allNavItems.forEach(function (navItem) {
      // Skip Chat, IDE, and CLI links (handled separately)
      if (navItem === chatLink
        || navItem.id === "better-gateway-ide-nav"
        || navItem.id === "better-gateway-cli-nav") {
        return;
      }
      
      navItem.addEventListener("click", function () {
        if (ideViewActive) {
          // Restore original content before navigation
          showChatView();
        }
      });
    });

    // Create and insert IDE nav item (if not already present)
    if (!existingIdeNav) {
      const ideNavItem = createIdeNavItem();
      navItems.appendChild(ideNavItem);
    }

    // Create and insert CLI nav item (if not already present)
    if (!existingCliNav) {
      const cliNavItem = createCliNavItem();
      navItems.appendChild(cliNavItem);
    }

    ideTabInjected = true;
    console.log("[BetterGateway] IDE + CLI nav items injected");
    return true;
  }

  function tryInjectIdeNavItem() {
    // Try immediately
    if (injectIdeNavItem()) return;

    // Retry a few times with increasing delays (handles SPAs)
    var retryDelays = [100, 300, 500, 1000, 2000];
    var retryIndex = 0;
    
    function retryInjection() {
      if (injectIdeNavItem()) return;
      if (retryIndex < retryDelays.length) {
        setTimeout(retryInjection, retryDelays[retryIndex++]);
      }
    }
    setTimeout(retryInjection, retryDelays[retryIndex++]);

    // Also use MutationObserver for dynamic content
    var observer = new MutationObserver(function (mutations, obs) {
      try {
        if (injectIdeNavItem()) {
          obs.disconnect();
        }
      } catch (_error) {}
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    // Give up after 10 seconds
    setTimeout(function () {
      observer.disconnect();
    }, 10000);
  }

  // ==================== Chat Sidebar Mention Enhancer ====================

  const mentionState = {
    textarea: null,
    composer: null,
    chips: null,
    picker: null,
    files: [],
    selected: [],
    pickerOpen: false,
    pickerItems: [],
    activeIndex: 0,
    mentionRange: null,
  };

  const FILE_CONTEXT_CHAR_LIMIT = 6000;
  const TOTAL_CONTEXT_CHAR_LIMIT = 18000;

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function fetchWorkspaceFiles() {
    if (typeof fetch !== "function") return;
    try {
      const response = await fetch("/better-gateway/api/files?path=%2F&recursive=true");
      if (!response.ok) return;
      const payload = await response.json();
      mentionState.files = Array.isArray(payload.files)
        ? payload.files.filter((entry) => entry && entry.type === "file").map((entry) => entry.path)
        : [];
      if (mentionState.textarea) refreshMentionPicker();
    } catch (error) {
      console.warn("[BetterGateway] failed to index workspace files", error);
    }
  }

  function findMentionRange(value, cursorIndex) {
    const before = value.slice(0, cursorIndex);
    const match = before.match(/(^|\s)@([^\s@.,!?;:]*)$/);
    if (!match) return null;
    const token = match[0];
    const query = match[2] || "";
    const atIndex = cursorIndex - token.length + token.lastIndexOf("@");
    return { start: atIndex, end: cursorIndex, query };
  }

  function getMentionCandidates(query) {
    const needle = String(query || "").toLowerCase();
    return mentionState.files
      .filter((path) => !mentionState.selected.some((entry) => entry.path === path))
      .map((path) => {
        const lower = path.toLowerCase();
        const index = needle ? lower.indexOf(needle) : 0;
        return { path, index, name: path.split("/").pop() || path };
      })
      .filter((item) => !needle || item.index !== -1)
      .sort((a, b) => {
        if (a.index !== b.index) return a.index - b.index;
        if (a.path.length !== b.path.length) return a.path.length - b.path.length;
        return a.path.localeCompare(b.path);
      })
      .slice(0, 20);
  }

  function closeMentionPicker() {
    mentionState.pickerOpen = false;
    mentionState.pickerItems = [];
    mentionState.mentionRange = null;
    if (mentionState.picker) {
      mentionState.picker.style.display = "none";
      mentionState.picker.innerHTML = "";
    }
  }

  function renderMentionPicker() {
    if (!mentionState.picker || !mentionState.pickerOpen || mentionState.pickerItems.length === 0) {
      closeMentionPicker();
      return;
    }

    mentionState.picker.innerHTML = mentionState.pickerItems
      .map((item, idx) => {
        const activeClass = idx === mentionState.activeIndex ? "active" : "";
        return '<div class="better-gateway-chat-file-option ' + activeClass + '" data-path="' + escapeHtml(item.path) + '">'
          + '<span class="path">' + escapeHtml(item.path) + '</span>'
          + '<span class="name">' + escapeHtml(item.name) + '</span>'
          + '</div>';
      })
      .join("");
    mentionState.picker.style.display = "block";

    mentionState.picker.querySelectorAll(".better-gateway-chat-file-option").forEach(function (option) {
      option.addEventListener("mousedown", function (event) {
        event.preventDefault();
        selectMentionFile(option.dataset.path || "");
      });
    });
  }

  function renderMentionChips() {
    if (!mentionState.chips) return;
    if (typeof document === "undefined" || !document.createElement) return;
    mentionState.chips.innerHTML = "";

    mentionState.selected.forEach(function (entry) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "better-gateway-chat-file-chip";
      chip.setAttribute("data-path", entry.path);
      chip.innerHTML = '<span class="chip-path">' + escapeHtml(entry.path) + '</span><span class="chip-remove" aria-hidden="true">×</span>';
      chip.addEventListener("click", function () {
        mentionState.selected = mentionState.selected.filter((item) => item.path !== entry.path);
        renderMentionChips();
        refreshMentionPicker();
      });
      mentionState.chips.appendChild(chip);
    });
  }

  function refreshMentionPicker() {
    if (!mentionState.textarea) return;
    const range = findMentionRange(mentionState.textarea.value, mentionState.textarea.selectionStart || 0);
    if (!range) {
      closeMentionPicker();
      return;
    }
    mentionState.mentionRange = range;
    mentionState.pickerItems = getMentionCandidates(range.query);
    mentionState.activeIndex = Math.min(mentionState.activeIndex, Math.max(mentionState.pickerItems.length - 1, 0));
    mentionState.pickerOpen = mentionState.pickerItems.length > 0;
    renderMentionPicker();
  }

  async function readFileContext(path) {
    const response = await fetch("/better-gateway/api/files/read?path=" + encodeURIComponent(path));
    if (!response.ok) throw new Error("read failed");
    const payload = await response.json();
    const content = String(payload.content || "");
    if (content.length <= FILE_CONTEXT_CHAR_LIMIT) {
      return { path, content, truncated: false };
    }
    return {
      path,
      content: content.slice(0, FILE_CONTEXT_CHAR_LIMIT),
      truncated: true,
      originalLength: content.length,
    };
  }

  async function selectMentionFile(path) {
    if (!path) return;
    if (mentionState.selected.some((entry) => entry.path === path)) return;

    let context = { path, content: "", truncated: false, error: "pending" };
    mentionState.selected.push(context);
    renderMentionChips();

    const range = mentionState.mentionRange;
    if (range && mentionState.textarea) {
      const nextValue = mentionState.textarea.value.slice(0, range.start) + mentionState.textarea.value.slice(range.end);
      mentionState.textarea.value = nextValue;
      mentionState.textarea.setSelectionRange(range.start, range.start);
    }

    closeMentionPicker();
    if (mentionState.textarea) mentionState.textarea.focus();

    try {
      const loaded = await readFileContext(path);
      const idx = mentionState.selected.findIndex((entry) => entry.path === path);
      if (idx !== -1) {
        mentionState.selected[idx] = loaded;
      }
    } catch (error) {
      const idx = mentionState.selected.findIndex((entry) => entry.path === path);
      if (idx !== -1) {
        mentionState.selected[idx] = { path, content: "", truncated: false, error: "read_failed" };
      }
    }
    renderMentionChips();
  }

  function prepareFileRefsForMessage(refs) {
    if (!refs || refs.length === 0) return [];

    let remaining = TOTAL_CONTEXT_CHAR_LIMIT;
    const output = [];
    refs.forEach(function (entry) {
      if (!entry || !entry.path || remaining <= 0) return;
      const content = String(entry.content || "");
      if (!content) {
        output.push({ path: entry.path, truncated: Boolean(entry.truncated), error: entry.error || "empty" });
        return;
      }
      const slice = content.slice(0, remaining);
      output.push({
        path: entry.path,
        content: slice,
        truncated: Boolean(entry.truncated) || slice.length < content.length,
      });
      remaining -= slice.length;
    });
    return output;
  }

  function buildMessageWithFileRefs(baseMessage, fileRefs) {
    const body = String(baseMessage || "");
    if (!fileRefs || fileRefs.length === 0) return body;

    const blocks = fileRefs
      .map(function (ref) {
        const path = String((ref && ref.path) || "");
        if (!path) return "";
        var content = String((ref && ref.content) || "");
        if (!content) return "<file path=\"" + path + "\">(could not read file)</file>";
        var tag = "<file path=\"" + path + "\"";
        if (ref.truncated) tag += " truncated=\"true\"";
        tag += ">\n" + content + "\n</file>";
        return tag;
      })
      .filter(Boolean);
    if (blocks.length === 0) return body;
    return body + "\n\n" + blocks.join("\n\n");
  }

  function attachChatComposerEnhancements() {
    if (typeof document === "undefined" || !document.querySelector) return;
    const textarea = document.querySelector("main.content textarea");
    if (!textarea || mentionState.textarea === textarea) return;

    mentionState.textarea = textarea;
    mentionState.composer = textarea.parentElement || textarea.closest("form") || textarea.parentElement;
    if (!mentionState.composer) return;

    let chips = mentionState.composer.querySelector(".better-gateway-chat-file-chips");
    if (!chips) {
      chips = document.createElement("div");
      chips.className = "better-gateway-chat-file-chips";
      textarea.parentElement.insertBefore(chips, textarea);
    }

    let picker = mentionState.composer.querySelector(".better-gateway-chat-file-picker");
    if (!picker) {
      picker = document.createElement("div");
      picker.className = "better-gateway-chat-file-picker";
      textarea.parentElement.insertBefore(picker, textarea);
    }

    mentionState.chips = chips;
    mentionState.picker = picker;

    if (!document.getElementById("better-gateway-chat-mention-style")) {
      const style = document.createElement("style");
      style.id = "better-gateway-chat-mention-style";
      style.textContent = `
        .better-gateway-chat-file-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
        .better-gateway-chat-file-chip { border: 1px solid #2a4a67; background: #1f3347; color: #dbeafe; border-radius: 999px; padding: 4px 8px; display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
        .better-gateway-chat-file-chip .chip-path { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .better-gateway-chat-file-chip .chip-remove { font-size: 12px; line-height: 1; }
        .better-gateway-chat-file-picker { position: absolute; left: 0; right: 0; bottom: calc(100% + 6px); background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 8px; max-height: 220px; overflow: auto; display: none; z-index: 999; }
        .better-gateway-chat-file-option { display: flex; justify-content: space-between; gap: 8px; padding: 8px 10px; cursor: pointer; }
        .better-gateway-chat-file-option:hover, .better-gateway-chat-file-option.active { background: #2a2d2e; }
        .better-gateway-chat-file-option .path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .better-gateway-chat-file-option .name { color: #8b8b8b; font-size: 11px; }
        file[path] { display: none !important; }
        .bg-chat-file-wrapper { margin: 6px 0; }
        .bg-chat-file-chip { display: inline-flex; align-items: center; gap: 6px; border: 1px solid #2a4a67; background: #1f3347; color: #dbeafe; border-radius: 8px; padding: 6px 12px; cursor: pointer; font-size: 13px; user-select: none; }
        .bg-chat-file-chip:hover { background: #253d54; }
        .bg-chat-file-chip .chip-toggle { font-size: 10px; transition: transform 0.2s; display: inline-block; }
        .bg-chat-file-chip.expanded .chip-toggle { transform: rotate(90deg); }
        .bg-chat-file-chip .chip-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .bg-chat-file-chip .chip-truncated { opacity: 0.6; font-size: 11px; }
        .bg-chat-file-content { display: none; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; margin: 4px 0 8px; font-family: monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; color: #e6edf3; }
        .bg-chat-file-content.visible { display: block; }
        /* Prevent iOS Safari auto-zoom on textarea focus (triggers when font-size < 16px) */
        main.content textarea { font-size: max(16px, 1em) !important; }
        /* Compose + header compact layout tweaks */
        .bg-chat-compose-has-send { position: relative; }
        .bg-chat-compose-has-send textarea { padding-right: 44px; }
        .bg-chat-send-btn {
          position: absolute;
          right: 6px;
          bottom: 6px;
          margin: 0;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          padding: 6px;
          min-width: 32px;
          min-height: 32px;
        }
        .bg-send-icon svg { width: 16px; height: 16px; display: block; }
        .bg-send-label {
          position: absolute;
          width: 1px;
          height: 1px;
          overflow: hidden;
          clip: rect(0, 0, 0, 0);
          white-space: nowrap;
        }
        .bg-new-session-icon { display: inline-flex; align-items: center; }
        .bg-new-session-icon svg { width: 16px; height: 16px; display: block; }
        .bg-new-session-label {
          position: absolute;
          width: 1px;
          height: 1px;
          overflow: hidden;
          clip: rect(0, 0, 0, 0);
          white-space: nowrap;
        }
        #better-gateway-ide-frame, #better-gateway-cli-frame { min-height: 0; }
      `;
      document.head.appendChild(style);
    }

    mentionState.composer.style.position = mentionState.composer.style.position || "relative";

    textarea.addEventListener("input", function () {
      refreshMentionPicker();
    });
    textarea.addEventListener("click", refreshMentionPicker);
    textarea.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" && mentionState.pickerOpen) {
        event.preventDefault();
        mentionState.activeIndex = (mentionState.activeIndex + 1) % mentionState.pickerItems.length;
        renderMentionPicker();
        return;
      }
      if (event.key === "ArrowUp" && mentionState.pickerOpen) {
        event.preventDefault();
        mentionState.activeIndex = (mentionState.activeIndex - 1 + mentionState.pickerItems.length) % mentionState.pickerItems.length;
        renderMentionPicker();
        return;
      }
      if (event.key === "Escape" && mentionState.pickerOpen) {
        event.preventDefault();
        closeMentionPicker();
        return;
      }

      if (event.key === "Backspace" && !textarea.value && mentionState.selected.length > 0) {
        mentionState.selected.pop();
        renderMentionChips();
        return;
      }
    }, true);

    renderMentionChips();
  }

  function transformFileBlocksInChat() {
    var chatArea = document.querySelector("main.content");
    if (!chatArea) return;

    // Case 1: <file> rendered as DOM elements (hidden by CSS, replaced with chip)
    var fileEls = chatArea.querySelectorAll("file[path]:not([data-bg-chip])");
    fileEls.forEach(function (fileEl) {
      if (fileEl.closest("textarea, input, .better-gateway-chat-file-chips, .better-gateway-chat-file-picker")) return;
      fileEl.setAttribute("data-bg-chip", "true");

      var path = fileEl.getAttribute("path") || "";
      var content = fileEl.textContent || "";
      var truncated = fileEl.getAttribute("truncated") === "true";

      var wrapper = document.createElement("div");
      wrapper.className = "bg-chat-file-wrapper";

      var chip = document.createElement("div");
      chip.className = "bg-chat-file-chip";
      chip.innerHTML = '<span class="chip-toggle">\u25B6</span>'
        + '<span class="chip-path">' + escapeHtml(path) + '</span>'
        + (truncated ? '<span class="chip-truncated">(truncated)</span>' : '');

      var contentPanel = document.createElement("pre");
      contentPanel.className = "bg-chat-file-content";
      contentPanel.textContent = content;

      chip.addEventListener("click", function () {
        chip.classList.toggle("expanded");
        contentPanel.classList.toggle("visible");
      });

      wrapper.appendChild(chip);
      wrapper.appendChild(contentPanel);
      fileEl.parentNode.insertBefore(wrapper, fileEl);
    });

    // Case 2: <file> escaped as HTML entities in text
    var candidates = chatArea.querySelectorAll("p:not([data-bg-files-done]), div:not([data-bg-files-done]), span:not([data-bg-files-done])");
    var escapedRe = /&lt;file path=&quot;([^&]*)&quot;(?:\s*truncated=&quot;(true)&quot;)?&gt;\n?([\s\S]*?)\n?&lt;\/file&gt;/g;
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (el.closest(".bg-chat-file-wrapper, .better-gateway-chat-file-chips, .better-gateway-chat-file-picker, textarea, input")) continue;
      var html = el.innerHTML;
      if (!escapedRe.test(html)) continue;
      escapedRe.lastIndex = 0;
      el.setAttribute("data-bg-files-done", "true");
      el.innerHTML = html.replace(escapedRe, function (_match, path, truncated, content) {
        var decoded = content.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
        var t = truncated === "true";
        return '<div class="bg-chat-file-wrapper">'
          + '<div class="bg-chat-file-chip"><span class="chip-toggle">\u25B6</span>'
          + '<span class="chip-path">' + escapeHtml(path) + '</span>'
          + (t ? '<span class="chip-truncated">(truncated)</span>' : '')
          + '</div>'
          + '<pre class="bg-chat-file-content">' + escapeHtml(decoded) + '</pre>'
          + '</div>';
      });
      el.querySelectorAll(".bg-chat-file-chip:not([data-bg-bound])").forEach(function (chip) {
        chip.setAttribute("data-bg-bound", "true");
        chip.addEventListener("click", function () {
          chip.classList.toggle("expanded");
          var panel = chip.nextElementSibling;
          if (panel && panel.classList.contains("bg-chat-file-content")) {
            panel.classList.toggle("visible");
          }
        });
      });
    }
  }

  function enhanceHeaderLayout() {
    try {
      if (typeof document === "undefined") return;

      // Bail early if we already injected the header proxy button.
      if (document.getElementById("bg-header-new-session-btn")) return;

      // Prefer the chat controls bar (session selector + refresh + thinking + focus).
      const chatControls = document.querySelector('.chat-controls');
      const focusButton = chatControls
        ? chatControls.querySelector('button[title*="focus mode" i], button[aria-label*="focus mode" i]')
        : null;

      if (!chatControls || !focusButton) return;

      // The gateway renders .chat-compose__actions with two buttons:
      //   [0] abortButton: "New session" | "Stop"  (Lit manages the text)
      //   [1] sendButton:  "Send" | "Queue" + <kbd>
      // We do NOT move the original button — that breaks Lit's text-node binding.
      // Instead we create a new proxy button in the header.
      var actionsDiv = document.querySelector("main.content .chat-compose__actions");
      if (!actionsDiv) return;
      var abortButton = actionsDiv.querySelectorAll("button")[0];
      if (!abortButton) return;

      // Create a new proxy "New session" button for the header.
      var headerBtn = document.createElement("button");
      headerBtn.id = "bg-header-new-session-btn";
      headerBtn.type = "button";
      headerBtn.className = "btn btn--sm btn--icon";
      headerBtn.setAttribute("aria-label", "New session");
      headerBtn.title = "New session";

      var nsIconSpan = document.createElement("span");
      nsIconSpan.className = "bg-new-session-icon";
      nsIconSpan.setAttribute("aria-hidden", "true");
      nsIconSpan.innerHTML = NEW_SESSION_ICON_SVG;

      var nsLabelSpan = document.createElement("span");
      nsLabelSpan.className = "bg-new-session-label";
      nsLabelSpan.textContent = "New session";

      headerBtn.appendChild(nsIconSpan);
      headerBtn.appendChild(nsLabelSpan);

      // Only fire when NOT streaming (abort button text is "New session", not "Stop").
      headerBtn.addEventListener("click", function () {
        var actions = document.querySelector("main.content .chat-compose__actions");
        var abort = actions ? actions.querySelectorAll("button")[0] : null;
        if (abort && !/stop/i.test((abort.textContent || "").trim())) {
          abort.click();
        }
      });

      chatControls.insertBefore(headerBtn, focusButton.nextSibling);
    } catch (_error) {
      // Non-fatal; header tweaks are best-effort only.
    }
  }

  function enhanceComposeLayout() {
    if (typeof document === "undefined") return;

    var main = document.querySelector("main.content");
    if (!main) return;

    // Locate the chat compose field container.
    var formTextarea = main.querySelector("form textarea");
    var field = main.querySelector(".chat-compose") ||
      main.querySelector(".chat-compose__field") ||
      (formTextarea ? formTextarea.parentElement : null);
    if (!field || field.dataset.bgComposeEnhanced === "true") return;

    var textarea = field.querySelector("textarea") || main.querySelector("textarea");
    if (!textarea) return;

    // The gateway renders .chat-compose__actions with two Lit-managed buttons:
    //   [0] abortButton: "New session" | "Stop"  — text changes when streaming starts/ends
    //   [1] sendButton:  "Send" | "Queue" (+ <kbd>↵</kbd>) — Lit may also update text
    // We must NOT move or clear these buttons — that severs Lit's DOM text-node bindings.
    // Instead we hide the whole actions div and create our own proxy button inside the field.
    var actionsDiv = field.querySelector(".chat-compose__actions");
    if (!actionsDiv) return;

    var buttons = actionsDiv.querySelectorAll("button");
    var abortButton = buttons[0]; // "New session" | "Stop"
    var sendButton  = buttons[1]; // "Send" | "Queue"
    if (!sendButton) return;

    // Guard: if our proxy already lives in this field, just mark enhanced and bail.
    if (field.querySelector("#bg-compose-send-stop-btn")) {
      field.dataset.bgComposeEnhanced = "true";
      return;
    }

    // Mark enhanced before any async work.
    field.dataset.bgComposeEnhanced = "true";

    // Hide the original actions bar entirely.
    actionsDiv.style.display = "none";

    // Find the field label (wraps the textarea) — proxy button goes here.
    var fieldLabel =
      field.querySelector(".chat-compose__field") ||
      field.querySelector("label.field") ||
      field;

    if (fieldLabel && !fieldLabel.classList.contains("bg-chat-compose-has-send")) {
      fieldLabel.classList.add("bg-chat-compose-has-send");
    }

    // --- Build the proxy Send / Stop button ---
    var proxyBtn = document.createElement("button");
    proxyBtn.type = "button";
    proxyBtn.id = "bg-compose-send-stop-btn";
    proxyBtn.className = "btn btn--primary btn--icon bg-chat-send-btn";
    proxyBtn.setAttribute("aria-label", "Send message");
    proxyBtn.title = "Send message";

    var sendIconSpan = document.createElement("span");
    sendIconSpan.className = "bg-send-icon";
    sendIconSpan.setAttribute("aria-hidden", "true");
    sendIconSpan.innerHTML = SEND_ICON_SVG;

    var proxyLabelSpan = document.createElement("span");
    proxyLabelSpan.className = "bg-send-label";
    proxyLabelSpan.textContent = "Send message";

    proxyBtn.appendChild(sendIconSpan);
    proxyBtn.appendChild(proxyLabelSpan);

    try {
      if (fieldLabel) fieldLabel.appendChild(proxyBtn);
    } catch (_e) {}

    // --- State machine: idle (Send) ↔ streaming (Stop) ---
    var isStreaming = false;

    function updateProxyState() {
      // Lit updates abortButton's text between "New session" and "Stop".
      // We use that as the streaming signal.
      var abortText = abortButton
        ? (abortButton.textContent || "").replace(/\s+/g, " ").trim()
        : "";
      isStreaming = /^stop$/i.test(abortText);

      if (isStreaming) {
        sendIconSpan.innerHTML = STOP_ICON_SVG;
        proxyBtn.setAttribute("aria-label", "Stop response");
        proxyBtn.title = "Stop response";
        proxyBtn.disabled = abortButton ? abortButton.disabled : false;
      } else {
        sendIconSpan.innerHTML = SEND_ICON_SVG;
        proxyBtn.setAttribute("aria-label", "Send message");
        proxyBtn.title = "Send message";
        proxyBtn.disabled = sendButton.disabled;
      }
    }

    // Proxy click: delegate to the appropriate hidden gateway button.
    proxyBtn.addEventListener("click", function () {
      if (isStreaming) {
        if (abortButton && !abortButton.disabled) abortButton.click();
      } else {
        if (sendButton && !sendButton.disabled) sendButton.click();
      }
    });

    // Watch abortButton for "New session" ↔ "Stop" text changes (streaming signal).
    if (abortButton) {
      new MutationObserver(updateProxyState)
        .observe(abortButton, { childList: true, characterData: true, subtree: true });
    }

    // Mirror the send button's disabled state onto the proxy (e.g. when disconnected).
    new MutationObserver(function () {
      if (!isStreaming) proxyBtn.disabled = sendButton.disabled;
    }).observe(sendButton, { attributes: true, attributeFilter: ["disabled"] });

    // Set initial state.
    updateProxyState();
  }


  function startChatComposerEnhancer() {
    fetchWorkspaceFiles();
    enhanceHeaderLayout();
    enhanceComposeLayout();
    attachChatComposerEnhancements();

    // Window-level capture handler fires before any framework handlers
    // (capture phase: window → document → ... → textarea), preventing
    // the gateway from seeing Enter as a "send message" action.
    window.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey) return;
      var target = event.target;
      if (!target || target.tagName !== "TEXTAREA") return;
      if (!target.closest || !target.closest("main.content")) return;

      var liveRange = findMentionRange(target.value, target.selectionStart || 0);
      if (!mentionState.pickerOpen && !liveRange) return;

      mentionState.textarea = target;
      if (!mentionState.pickerOpen && liveRange) {
        refreshMentionPicker();
      }

      var selected = mentionState.pickerItems[mentionState.activeIndex] || mentionState.pickerItems[0];
      if (!selected) return;

      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();

      selectMentionFile(selected.path);
    }, true);

    transformFileBlocksInChat();

    if (!document.body) return;
    const observer = new MutationObserver(function () {
      try {
        enhanceHeaderLayout();
        enhanceComposeLayout();
        attachChatComposerEnhancements();
        transformFileBlocksInChat();
      } catch (_error) {}
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function closeAllAndReconnect() {
    if (!lastConnectionArgs) return;
    updateStatus("reconnecting", "Reconnecting\u2026");
    if (activeConnections.size > 0) {
      pendingForcedReconnect = true;
      activeConnections.forEach(function (ws) { ws.close(); });
    } else {
      var args = lastConnectionArgs;
      setTimeout(function () {
        try {
          new window.WebSocket(args.url, args.protocols);
        } catch (e) {
          console.error("[BetterGateway] Reconnection failed:", e);
        }
      }, config.reconnectIntervalMs);
    }
  }

  function wrapWebSocket(OriginalWebSocket) {
    function BetterWebSocket(url, protocols) {
      const ws = new OriginalWebSocket(url, protocols);
      const wrappedWs = ws;
      lastConnectionArgs = { url, protocols };

      activeConnections.add(wrappedWs);

      ws.addEventListener("open", function () {
        reconnectAttempts = 0;
        updateStatus("connected", "Connected");
      });

      ws.addEventListener("message", function (event) {
        if (config.autoReconnectOnGap && typeof event.data === "string"
            && event.data.includes("event gap detected")) {
          var now = Date.now();
          if (now - lastPageReloadAt < 10000) {
            console.warn("[BetterGateway] Event gap detected but reload guard active, skipping");
            return;
          }
          lastPageReloadAt = now;
          console.warn("[BetterGateway] Event gap detected, reloading page as server recommends");
          window.location.reload();
        }
      });

      ws.addEventListener("close", function (event) {
        activeConnections.delete(wrappedWs);

        if (pendingForcedReconnect) {
          pendingForcedReconnect = false;
          reconnectAttempts = 0;
          updateStatus("reconnecting", "Reconnecting\u2026");
          setTimeout(function () {
            try {
              new BetterWebSocket(url, protocols);
            } catch (e) {
              console.error("[BetterGateway] Forced reconnection failed:", e);
            }
          }, config.reconnectIntervalMs);
          return;
        }

        if (!event.wasClean && reconnectAttempts < config.maxReconnectAttempts) {
          reconnectAttempts++;
          updateStatus(
            "reconnecting",
            "Reconnecting (" + reconnectAttempts + "/" + config.maxReconnectAttempts + ")..."
          );

          setTimeout(function () {
            try {
              new BetterWebSocket(url, protocols);
            } catch (e) {
              console.error("[BetterGateway] Reconnection failed:", e);
            }
          }, config.reconnectIntervalMs);
        } else if (reconnectAttempts >= config.maxReconnectAttempts) {
          updateStatus("failed", "Connection failed");
        } else {
          updateStatus("disconnected", "Disconnected");
        }
      });

      ws.addEventListener("error", function () {
        updateStatus("disconnected", "Connection error");
      });

      const originalSend = ws.send && ws.send.bind(ws);
      if (originalSend) {
        ws.send = function (data) {
          try {
            if (typeof data === "string" && mentionState.selected.length > 0) {
              const frame = JSON.parse(data);
              if (frame && frame.type === "req" && frame.method === "chat.send" && frame.params) {
                const fileRefs = prepareFileRefsForMessage(mentionState.selected);
                mentionState.selected = [];
                renderMentionChips();
                if (fileRefs.length > 0) {
                  if (typeof frame.params.message === "string") {
                    frame.params.message = buildMessageWithFileRefs(frame.params.message, fileRefs);
                  } else if (typeof frame.params.text === "string") {
                    frame.params.text = buildMessageWithFileRefs(frame.params.text, fileRefs);
                  } else if (typeof frame.params.input === "string") {
                    frame.params.input = buildMessageWithFileRefs(frame.params.input, fileRefs);
                  }
                  data = JSON.stringify(frame);
                }
              }
            }
          } catch (error) {
            // non-JSON frame or parse issues; pass through untouched
          }
          return originalSend(data);
        };
      }

      return ws;
    }

    BetterWebSocket.prototype = OriginalWebSocket.prototype;
    BetterWebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    BetterWebSocket.OPEN = OriginalWebSocket.OPEN;
    BetterWebSocket.CLOSING = OriginalWebSocket.CLOSING;
    BetterWebSocket.CLOSED = OriginalWebSocket.CLOSED;

    return BetterWebSocket;
  }

  window.WebSocket = wrapWebSocket(originalWebSocket);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      updateStatus("connected", "Ready");
      tryInjectIdeNavItem();
      setupIdeHotkeys();
      startChatComposerEnhancer();
    });
  } else {
    updateStatus("connected", "Ready");
    tryInjectIdeNavItem();
    setupIdeHotkeys();
    startChatComposerEnhancer();
  }

  window.addEventListener("online", function () {
    updateStatus("connected", "Back online");
  });

  window.addEventListener("offline", function () {
    updateStatus("disconnected", "Offline");
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      hiddenSince = Date.now();
    } else if (config.autoReconnectOnVisibility && hiddenSince !== null) {
      var away = Date.now() - hiddenSince;
      hiddenSince = null;
      if (away >= config.visibilityReconnectThresholdMs) {
        console.log("[BetterGateway] Tab was hidden for " + Math.round(away / 1000) + "s, forcing reconnect");
        closeAllAndReconnect();
      }
    } else {
      hiddenSince = null;
    }
  });

  window.__BETTER_GATEWAY_INJECT_VERSION__ = INJECT_VERSION;
  console.log("[BetterGateway] Auto-reconnect enabled", { ...config, injectVersion: INJECT_VERSION });
})();
