/**
 * IDE Page Generator
 * Creates a full-featured code editor interface using Monaco Editor (CDN)
 */
const DEFAULT_CONFIG = {
    monacoVersion: "0.52.0",
    theme: "vs-dark",
};
/**
 * Language detection from file extension
 */
const EXTENSION_TO_LANGUAGE = {
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    json: "json",
    md: "markdown",
    html: "html",
    htm: "html",
    css: "css",
    scss: "scss",
    less: "less",
    py: "python",
    rb: "ruby",
    rs: "rust",
    go: "go",
    java: "java",
    c: "c",
    cpp: "cpp",
    h: "c",
    hpp: "cpp",
    cs: "csharp",
    php: "php",
    sh: "shell",
    bash: "shell",
    zsh: "shell",
    yaml: "yaml",
    yml: "yaml",
    xml: "xml",
    sql: "sql",
    graphql: "graphql",
    dockerfile: "dockerfile",
    makefile: "makefile",
    toml: "toml",
    ini: "ini",
    txt: "plaintext",
};
/**
 * Generate the IDE page HTML
 */
export function generateIdePage(config = {}) {
    const { monacoVersion, theme } = { ...DEFAULT_CONFIG, ...config };
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Better Gateway IDE</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }

    :root {
      --bg-primary: #1e1e1e;
      --bg-secondary: #252526;
      --bg-tertiary: #2d2d30;
      --bg-hover: #2a2d2e;
      --bg-active: #37373d;
      --border-color: #3c3c3c;
      --text-primary: #cccccc;
      --text-secondary: #858585;
      --text-muted: #6e6e6e;
      --accent: #0078d4;
      --accent-hover: #1c8ae6;
      --success: #4ec9b0;
      --warning: #dcdcaa;
      --error: #f14c4c;
      --scrollbar-bg: #1e1e1e;
      --scrollbar-thumb: #424242;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      height: 100vh;
      overflow: hidden;
    }

    #app {
      display: flex;
      flex-direction: column;
      height: 100vh;
    }

    /* Header / Toolbar */
    #toolbar {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 8px 16px;
      background: var(--bg-secondary);
      border-bottom: 1px solid var(--border-color);
      height: 42px;
    }

    #toolbar .logo {
      font-weight: 600;
      color: var(--accent);
      font-size: 14px;
    }

    #toolbar .separator {
      width: 1px;
      height: 20px;
      background: var(--border-color);
    }

    .toolbar-btn {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      padding: 6px 10px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .toolbar-btn:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
    }

    .toolbar-btn.active {
      background: var(--bg-active);
      color: var(--text-primary);
    }

    #workspace-path {
      margin-left: auto;
      font-size: 12px;
      color: var(--text-muted);
      max-width: 320px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    #save-status {
      font-size: 12px;
      color: var(--text-muted);
      min-width: 72px;
      text-align: right;
    }

    #save-status.saving { color: var(--warning); }
    #save-status.saved { color: var(--success); }
    #save-status.error { color: var(--error); }

    /* Main Layout */
    #main {
      display: flex;
      flex: 1;
      overflow: hidden;
    }

    /* Sidebar */
    #sidebar {
      width: 260px;
      min-width: 200px;
      max-width: 400px;
      background: var(--bg-secondary);
      border-right: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    #sidebar.collapsed {
      width: 0;
      min-width: 0;
      border-right: none;
    }

    #sidebar-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-secondary);
      border-bottom: 1px solid var(--border-color);
    }

    #sidebar-header button {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
    }

    #sidebar-header button:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
    }

    /* File Search */
    #file-search-container {
      padding: 8px 12px;
      border-bottom: 1px solid var(--border-color);
    }

    #file-search {
      width: 100%;
      padding: 6px 10px;
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 4px;
      color: var(--text-primary);
      font-size: 12px;
      outline: none;
    }

    #file-search:focus {
      border-color: var(--accent);
    }

    #file-search::placeholder {
      color: var(--text-muted);
    }

    #open-editors {
      border-bottom: 1px solid var(--border-color);
      max-height: 180px;
      overflow-y: auto;
      padding: 4px 0;
    }

    #open-editors-header {
      padding: 4px 12px;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      color: var(--text-muted);
    }

    #open-editors.empty .open-editor-empty {
      display: block;
    }

    .open-editor-empty {
      display: none;
      padding: 4px 12px 8px 12px;
      font-size: 12px;
      color: var(--text-muted);
    }

    .open-editor-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 4px 12px;
      cursor: pointer;
      font-size: 12px;
      color: var(--text-secondary);
      user-select: none;
    }

    .open-editor-item:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
    }

    .open-editor-item.active {
      background: var(--bg-active);
      color: var(--text-primary);
    }

    .open-editor-item .name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .open-editor-item .close {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      border-radius: 4px;
      padding: 0 4px;
      font-size: 13px;
      line-height: 1.1;
    }

    .open-editor-item .close:hover {
      background: var(--bg-active);
      color: var(--text-primary);
    }

    #file-tree {
      flex: 1;
      overflow-y: auto;
      padding: 8px 0;
    }

    .tree-item {
      display: flex;
      align-items: center;
      padding: 4px 12px;
      cursor: pointer;
      font-size: 13px;
      color: var(--text-primary);
      user-select: none;
    }

    .tree-item:hover {
      background: var(--bg-hover);
    }

    .tree-item.selected {
      background: var(--bg-active);
    }

    .tree-item.directory {
      color: var(--text-secondary);
    }

    .tree-item .icon {
      width: 16px;
      height: 16px;
      margin-right: 6px;
      flex-shrink: 0;
      font-size: 14px;
      text-align: center;
    }

    .tree-item .name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .tree-item .chevron {
      width: 16px;
      margin-right: 2px;
      font-size: 10px;
      color: var(--text-muted);
      transition: transform 0.15s;
    }

    .tree-item .chevron.expanded {
      transform: rotate(90deg);
    }

    .tree-children {
      display: none;
    }

    .tree-children.expanded {
      display: block;
    }

    /* Resize Handle */
    #resize-handle {
      width: 4px;
      cursor: col-resize;
      background: transparent;
    }

    #resize-handle:hover {
      background: var(--accent);
    }

    /* Editor Area */
    #editor-area {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      min-width: 320px;
    }

    /* Tab Bar */
    #tab-bar {
      display: flex;
      align-items: center;
      background: var(--bg-tertiary);
      border-bottom: 1px solid var(--border-color);
      height: 36px;
      overflow-x: auto;
    }

    #tab-bar::-webkit-scrollbar {
      height: 3px;
    }

    .tab {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      height: 100%;
      font-size: 13px;
      color: var(--text-secondary);
      background: transparent;
      border: none;
      border-right: 1px solid var(--border-color);
      cursor: pointer;
      white-space: nowrap;
    }

    .tab:hover {
      background: var(--bg-hover);
    }

    .tab.active {
      background: var(--bg-primary);
      color: var(--text-primary);
      border-bottom: 1px solid var(--bg-primary);
      margin-bottom: -1px;
    }

    .tab.modified .tab-name::after {
      content: " •";
      color: var(--warning);
    }

    .tab .close-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      padding: 2px;
      border-radius: 4px;
      font-size: 14px;
      line-height: 1;
      visibility: hidden;
    }

    .tab:hover .close-btn,
    .tab.active .close-btn {
      visibility: visible;
    }

    .tab .close-btn:hover {
      background: var(--bg-active);
      color: var(--text-primary);
    }

    .tab.dragging {
      opacity: 0.5;
    }

    .tab.drag-over {
      border-left: 2px solid var(--accent);
    }

    /* Tab scroll buttons */
    .tab-scroll-btn {
      background: var(--bg-tertiary);
      border: none;
      color: var(--text-secondary);
      padding: 0 8px;
      cursor: pointer;
      height: 100%;
      font-size: 14px;
    }

    .tab-scroll-btn:hover {
      background: var(--bg-hover);
      color: var(--text-primary);
    }

    .tab-scroll-btn:disabled {
      opacity: 0.3;
      cursor: default;
    }

    /* Editor Container */
    #editor-container {
      flex: 1;
      overflow: hidden;
    }

    /* Welcome Screen */
    #welcome {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--text-muted);
      font-size: 14px;
    }

    #welcome h2 {
      font-size: 24px;
      font-weight: 400;
      margin-bottom: 16px;
      color: var(--text-secondary);
    }

    #welcome .shortcuts {
      margin-top: 24px;
      text-align: left;
    }

    #welcome .shortcut {
      display: flex;
      gap: 12px;
      margin: 8px 0;
    }

    #welcome kbd {
      background: var(--bg-tertiary);
      padding: 2px 8px;
      border-radius: 4px;
      font-family: inherit;
      font-size: 12px;
      min-width: 80px;
      text-align: center;
    }

    /* Loading Overlay */
    #loading {
      position: fixed;
      inset: 0;
      background: var(--bg-primary);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }

    #loading.hidden {
      display: none;
    }

    .spinner {
      width: 40px;
      height: 40px;
      border: 3px solid var(--bg-tertiary);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    /* Context Menu */
    #context-menu {
      position: fixed;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 4px 0;
      min-width: 160px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.4);
      z-index: 1000;
      display: none;
    }

    #context-menu.visible {
      display: block;
    }

    .context-item {
      padding: 6px 12px;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .context-item:hover {
      background: var(--bg-hover);
    }

    .context-separator {
      height: 1px;
      background: var(--border-color);
      margin: 4px 0;
    }

    /* Scrollbar styling */
    ::-webkit-scrollbar {
      width: 10px;
      height: 10px;
    }

    ::-webkit-scrollbar-track {
      background: var(--scrollbar-bg);
    }

    ::-webkit-scrollbar-thumb {
      background: var(--scrollbar-thumb);
      border-radius: 5px;
    }

    ::-webkit-scrollbar-thumb:hover {
      background: #555;
    }

  </style>
</head>
<body>
  <div id="loading">
    <div class="spinner"></div>
  </div>

  <div id="app">
    <div id="toolbar">
      <span class="logo">⚡ Better Gateway IDE</span>
      <span class="separator"></span>
      <button class="toolbar-btn" id="toggle-sidebar" title="Toggle Sidebar (Ctrl+B)">
        ☰ Files
      </button>
      <button class="toolbar-btn" id="new-file-btn" title="New File (Ctrl+N)">
        + New
      </button>
      <button class="toolbar-btn" id="open-folder-btn" title="Open Folder">
        📂 Open Folder
      </button>
      <button class="toolbar-btn" id="refresh-btn" title="Refresh File Tree">
        ↻ Refresh
      </button>
      <span id="workspace-path" title="Current workspace folder">/</span>
      <span id="save-status"></span>
    </div>

    <div id="main">
      <div id="sidebar">
        <div id="sidebar-header">
          <span>Explorer</span>
          <button id="collapse-btn" title="Collapse All">⊟</button>
        </div>
        <div id="file-search-container">
          <input type="text" id="file-search" placeholder="Search files... (Ctrl+P)" />
        </div>
        <div id="open-editors" class="empty">
          <div id="open-editors-header">Open Editors</div>
          <div class="open-editor-empty">No open files</div>
        </div>
        <div id="file-tree"></div>
      </div>

      <div id="resize-handle"></div>

      <div id="editor-area">
        <div id="tab-bar"></div>
        <div id="editor-container">
          <div id="welcome">
            <h2>Better Gateway IDE</h2>
            <p>Open a file from the sidebar to start editing</p>
            <div class="shortcuts">
              <div class="shortcut"><kbd>⌘/Ctrl+S</kbd> <span>Save file</span></div>
              <div class="shortcut"><kbd>⌘/Ctrl+B</kbd> <span>Toggle sidebar</span></div>
              <div class="shortcut"><kbd>⌘/Ctrl+P</kbd> <span>Quick open</span></div>
              <div class="shortcut"><kbd>⌘/Ctrl+W</kbd> <span>Close tab</span></div>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>

  <div id="context-menu">
    <div class="context-item" data-action="new-file">📄 New File</div>
    <div class="context-item" data-action="new-folder">📁 New Folder</div>
    <div class="context-separator"></div>
    <div class="context-item" data-action="rename">✏️ Rename</div>
    <div class="context-item" data-action="delete">🗑️ Delete</div>
  </div>

  <script>
    // Configuration
    const API_BASE = '/better-gateway/api/files';
    const EXTENSION_MAP = ${JSON.stringify(EXTENSION_TO_LANGUAGE)};

    // State
    const state = {
      files: [],
      openTabs: [],
      activeTab: null,
      editor: null,
      models: new Map(), // path -> monaco model
      expandedDirs: new Set(['']),
      unsavedChanges: new Map(), // path -> true
      workspaceRoot: '/',
    };

    // DOM Elements
    const elements = {
      loading: document.getElementById('loading'),
      fileTree: document.getElementById('file-tree'),
      openEditors: document.getElementById('open-editors'),
      tabBar: document.getElementById('tab-bar'),
      editorContainer: document.getElementById('editor-container'),
      welcome: document.getElementById('welcome'),
      sidebar: document.getElementById('sidebar'),
      saveStatus: document.getElementById('save-status'),
      workspacePath: document.getElementById('workspace-path'),
      contextMenu: document.getElementById('context-menu'),
      fileSearch: document.getElementById('file-search'),
    };

    // Search state
    let searchQuery = '';

    // ==================== File API ====================

    function normalizeWorkspaceRoot(path) {
      if (!path || path === '/' || path === '.') return '/';
      let normalized = String(path).trim().split(String.fromCharCode(92)).join('/');

      // Accept absolute workspace paths (e.g. /root/.openclaw/workspace/projects/foo)
      const absWorkspacePrefix = '/root/.openclaw/workspace/';
      if (normalized === '/root/.openclaw/workspace') return '/';
      if (normalized.startsWith(absWorkspacePrefix)) {
        normalized = normalized.slice(absWorkspacePrefix.length);
      }

      while (normalized.startsWith('/')) normalized = normalized.slice(1);
      while (normalized.endsWith('/')) normalized = normalized.slice(0, -1);

      // Accept "workspace/..." alias from prompt/user habit
      if (normalized === 'workspace') return '/';
      if (normalized.startsWith('workspace/')) {
        normalized = normalized.slice('workspace/'.length);
      }

      return normalized || '/';
    }

    function getWorkspaceApiPath() {
      return state.workspaceRoot === '/' ? '/' : state.workspaceRoot;
    }

    function workspaceJoin(name) {
      if (state.workspaceRoot === '/') return name;
      return state.workspaceRoot + '/' + name;
    }

    function updateWorkspacePathLabel() {
      elements.workspacePath.textContent = state.workspaceRoot;
      elements.workspacePath.title = 'Current workspace folder: ' + state.workspaceRoot;
    }

    async function fetchFiles(path = '/') {
      const res = await fetch(\`\${API_BASE}?path=\${encodeURIComponent(path)}&recursive=true\`);
      if (!res.ok) throw new Error('Failed to fetch files');
      const data = await res.json();
      return data.files;
    }

    async function readFile(path) {
      const res = await fetch(\`\${API_BASE}/read?path=\${encodeURIComponent(path)}\`);
      if (!res.ok) throw new Error('Failed to read file');
      const data = await res.json();
      return data.content;
    }

    async function writeFile(path, content) {
      const res = await fetch(\`\${API_BASE}/write\`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, content }),
      });
      if (!res.ok) throw new Error('Failed to write file');
      return res.json();
    }

    async function deleteFile(path) {
      const res = await fetch(\`\${API_BASE}?path=\${encodeURIComponent(path)}\`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error('Failed to delete file');
      return res.json();
    }

    async function createDirectory(path) {
      const res = await fetch(\`\${API_BASE}/mkdir\`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!res.ok) throw new Error('Failed to create directory');
      return res.json();
    }

    // ==================== File Tree ====================

    function buildTree(files) {
      const root = { name: '', children: {}, type: 'directory' };

      for (const file of files) {
        const parts = file.path.split('/').filter(Boolean);
        let current = root;

        for (let i = 0; i < parts.length; i++) {
          const part = parts[i];
          const isLast = i === parts.length - 1;

          if (!current.children[part]) {
            current.children[part] = {
              name: part,
              path: parts.slice(0, i + 1).join('/'),
              type: isLast ? file.type : 'directory',
              size: file.size,
              modified: file.modified,
              children: {},
            };
          }
          current = current.children[part];
        }
      }

      return root;
    }

    function sortTreeChildren(children) {
      return Object.values(children).sort((a, b) => {
        // Directories first
        if (a.type !== b.type) {
          return a.type === 'directory' ? -1 : 1;
        }
        // Then alphabetically
        return a.name.localeCompare(b.name);
      });
    }

    function getFileIcon(name, type) {
      if (type === 'directory') return '📁';
      const ext = name.split('.').pop()?.toLowerCase();
      const icons = {
        ts: '🔷', tsx: '⚛️', js: '🟨', jsx: '⚛️',
        json: '📋', md: '📝', html: '🌐', css: '🎨',
        py: '🐍', rb: '💎', rs: '🦀', go: '🐹',
        sh: '⚙️', bash: '⚙️', yml: '⚙️', yaml: '⚙️',
        png: '🖼️', jpg: '🖼️', gif: '🖼️', svg: '🖼️',
        txt: '📄',
      };
      return icons[ext] || '📄';
    }

    function matchesSearch(name, path) {
      if (!searchQuery) return true;
      const query = searchQuery.toLowerCase();
      return name.toLowerCase().includes(query) || path.toLowerCase().includes(query);
    }

    function hasMatchingDescendants(node) {
      if (!searchQuery) return true;
      if (matchesSearch(node.name, node.path)) return true;
      if (node.type === 'directory' && node.children) {
        return Object.values(node.children).some(child => hasMatchingDescendants(child));
      }
      return false;
    }

    function highlightMatch(text) {
      if (!searchQuery) return text;
      const query = searchQuery.toLowerCase();
      const idx = text.toLowerCase().indexOf(query);
      if (idx === -1) return text;
      return text.slice(0, idx) + '<mark style="background: var(--accent); color: var(--bg-primary); padding: 0 2px; border-radius: 2px;">' + text.slice(idx, idx + query.length) + '</mark>' + text.slice(idx + query.length);
    }

    function renderTree(node, container, depth = 0) {
      const sorted = sortTreeChildren(node.children);

      for (const child of sorted) {
        // Skip items that don't match search (unless they have matching descendants)
        if (searchQuery && !hasMatchingDescendants(child)) {
          continue;
        }

        const item = document.createElement('div');
        item.className = 'tree-item' + (child.type === 'directory' ? ' directory' : '');
        item.style.paddingLeft = (12 + depth * 16) + 'px';
        item.dataset.path = child.path;
        item.dataset.type = child.type;

        // Auto-expand directories when searching
        const isExpanded = searchQuery ? true : state.expandedDirs.has(child.path);
        const displayName = highlightMatch(child.name);

        if (child.type === 'directory') {
          item.innerHTML = \`
            <span class="chevron \${isExpanded ? 'expanded' : ''}">▶</span>
            <span class="icon">\${getFileIcon(child.name, child.type)}</span>
            <span class="name">\${displayName}</span>
          \`;
        } else {
          item.innerHTML = \`
            <span class="icon">\${getFileIcon(child.name, child.type)}</span>
            <span class="name">\${displayName}</span>
          \`;
        }

        container.appendChild(item);

        // Add click handlers
        item.addEventListener('click', () => handleTreeItemClick(child));
        item.addEventListener('contextmenu', (e) => showContextMenu(e, child));

        // Render children if directory and expanded
        if (child.type === 'directory' && Object.keys(child.children).length > 0) {
          const childContainer = document.createElement('div');
          childContainer.className = 'tree-children' + (isExpanded ? ' expanded' : '');
          container.appendChild(childContainer);
          renderTree(child, childContainer, depth + 1);
        }
      }
    }

    function handleTreeItemClick(node) {
      if (node.type === 'directory') {
        // Toggle expanded state
        if (state.expandedDirs.has(node.path)) {
          state.expandedDirs.delete(node.path);
        } else {
          state.expandedDirs.add(node.path);
        }
        refreshFileTree();
      } else {
        openFile(node.path);
      }
    }

    async function refreshFileTree() {
      try {
        state.files = await fetchFiles(getWorkspaceApiPath());
        const tree = buildTree(state.files);
        elements.fileTree.innerHTML = '';
        renderTree(tree, elements.fileTree);
        updateTreeSelection();
      } catch (err) {
        console.error('Failed to refresh file tree:', err);
        // If the saved workspace root doesn't exist, reset to root and retry
        if (state.workspaceRoot !== '/') {
          console.warn('[IDE] Workspace root not found, falling back to /');
          state.workspaceRoot = '/';
          updateWorkspacePathLabel();
          try {
            localStorage.removeItem('workspaceRoot');
            localStorage.removeItem('openTabs');
            localStorage.removeItem('activeTab');
          } catch (_e) { /* ignore */ }
          try {
            state.files = await fetchFiles('/');
            const tree = buildTree(state.files);
            elements.fileTree.innerHTML = '';
            renderTree(tree, elements.fileTree);
            updateTreeSelection();
          } catch (retryErr) {
            console.error('[IDE] Fallback file tree load also failed:', retryErr);
            elements.fileTree.innerHTML = '<div style="padding:16px;color:#888;font-size:12px;">Unable to load files. Check console for details.</div>';
          }
        }
      }
    }

    function updateTreeSelection() {
      document.querySelectorAll('.tree-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.path === state.activeTab);
      });
    }

    function closeAllTabs(force = false) {
      const paths = [...state.openTabs];
      for (const path of paths) {
        if (state.unsavedChanges.has(path) && !force) {
          return false;
        }
      }

      for (const path of paths) {
        const model = state.models.get(path);
        if (model) {
          model.dispose();
          state.models.delete(path);
        }
      }

      state.openTabs = [];
      state.activeTab = null;
      state.unsavedChanges.clear();
      state.editor.setModel(null);
      elements.welcome.style.display = 'flex';
      renderTabs();
      return true;
    }

    async function setWorkspaceRoot(nextRoot) {
      const normalized = normalizeWorkspaceRoot(nextRoot);
      if (normalized === state.workspaceRoot) return;

      const hasDirty = state.unsavedChanges.size > 0;
      if (hasDirty) {
        const ok = confirm('Switch workspace folder? Unsaved changes will be discarded.');
        if (!ok) return;
      }

      closeAllTabs(true);
      searchQuery = '';
      elements.fileSearch.value = '';
      state.expandedDirs.clear();
      state.expandedDirs.add('');
      state.workspaceRoot = normalized;
      updateWorkspacePathLabel();
      await refreshFileTree();
    }

    function renderOpenEditors() {
      const container = elements.openEditors;
      container.innerHTML = '';

      const header = document.createElement('div');
      header.id = 'open-editors-header';
      header.textContent = 'Open Editors';
      container.appendChild(header);

      if (state.openTabs.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'open-editor-empty';
        empty.textContent = 'No open files';
        container.classList.add('empty');
        container.appendChild(empty);
        return;
      }

      container.classList.remove('empty');

      for (const path of state.openTabs) {
        const item = document.createElement('div');
        item.className = 'open-editor-item' + (path === state.activeTab ? ' active' : '');
        item.dataset.path = path;
        const name = path.split('/').pop();
        const icon = getFileIcon(name, 'file');
        const modifiedDot = state.unsavedChanges.has(path) ? ' •' : '';

        item.innerHTML =
          '<span class="icon">' + icon + '</span>' +
          '<span class="name" title="' + path + '">' + name + modifiedDot + '</span>' +
          '<button class="close" title="Close">×</button>';

        item.addEventListener('click', () => switchToTab(path));
        item.querySelector('.close').addEventListener('click', (e) => {
          e.stopPropagation();
          closeTab(path);
        });

        container.appendChild(item);
      }
    }

    // ==================== Tabs ====================

    // Tab drag state
    let draggedTab = null;

    function renderTabs() {
      elements.tabBar.innerHTML = '';
      renderOpenEditors();

      for (const path of state.openTabs) {
        const tab = document.createElement('button');
        tab.className = 'tab' + (path === state.activeTab ? ' active' : '');
        tab.draggable = true;
        tab.dataset.path = path;

        if (state.unsavedChanges.has(path)) {
          tab.classList.add('modified');
        }

        const name = path.split('/').pop();
        tab.innerHTML = \`
          <span class="tab-name">\${name}</span>
          <span class="close-btn" title="Close (Ctrl+W)">×</span>
        \`;

        // Click handlers
        tab.addEventListener('click', (e) => {
          if (e.target.classList.contains('close-btn')) {
            closeTab(path);
          } else {
            switchToTab(path);
          }
        });

        // Middle-click to close
        tab.addEventListener('auxclick', (e) => {
          if (e.button === 1) { // Middle button
            e.preventDefault();
            closeTab(path);
          }
        });

        // Drag and drop for tab reordering
        tab.addEventListener('dragstart', (e) => {
          draggedTab = path;
          tab.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
        });

        tab.addEventListener('dragend', () => {
          tab.classList.remove('dragging');
          draggedTab = null;
          document.querySelectorAll('.tab.drag-over').forEach(t => t.classList.remove('drag-over'));
        });

        tab.addEventListener('dragover', (e) => {
          e.preventDefault();
          if (draggedTab && draggedTab !== path) {
            tab.classList.add('drag-over');
          }
        });

        tab.addEventListener('dragleave', () => {
          tab.classList.remove('drag-over');
        });

        tab.addEventListener('drop', (e) => {
          e.preventDefault();
          tab.classList.remove('drag-over');
          if (draggedTab && draggedTab !== path) {
            // Reorder tabs
            const fromIdx = state.openTabs.indexOf(draggedTab);
            const toIdx = state.openTabs.indexOf(path);
            if (fromIdx !== -1 && toIdx !== -1) {
              state.openTabs.splice(fromIdx, 1);
              state.openTabs.splice(toIdx, 0, draggedTab);
              renderTabs();
            }
          }
        });

        elements.tabBar.appendChild(tab);
      }
    }

    async function openFile(path) {
      // Check if already open
      if (!state.openTabs.includes(path)) {
        state.openTabs.push(path);
      }

      // Switch to tab
      await switchToTab(path);
    }

    async function switchToTab(path) {
      state.activeTab = path;

      // Hide welcome screen
      elements.welcome.style.display = 'none';

      // Get or create model
      let model = state.models.get(path);
      if (!model) {
        try {
          const content = await readFile(path);
          const ext = path.split('.').pop()?.toLowerCase() || '';
          const language = EXTENSION_MAP[ext] || 'plaintext';

          model = monaco.editor.createModel(content, language, monaco.Uri.parse('file:///' + path));
          state.models.set(path, model);

          // Track changes
          model.onDidChangeContent(() => {
            if (!state.unsavedChanges.has(path)) {
              state.unsavedChanges.set(path, true);
              renderTabs();
            }
          });
        } catch (err) {
          console.error('Failed to open file:', err);
          return;
        }
      }

      state.editor.setModel(model);
      renderTabs();
      updateTreeSelection();

      // Restore view state if we have it
      try {
        const viewState = localStorage.getItem('viewState:' + path);
        if (viewState) {
          state.editor.restoreViewState(JSON.parse(viewState));
        }
      } catch (_e) {
        // Corrupted view state in localStorage; ignore and start fresh
      }
    }

    function closeTab(path) {
      const idx = state.openTabs.indexOf(path);
      if (idx === -1) return;

      // Check for unsaved changes
      if (state.unsavedChanges.has(path)) {
        if (!confirm(\`"\${path.split('/').pop()}" has unsaved changes. Close anyway?\`)) {
          return;
        }
      }

      // Remove from tabs
      state.openTabs.splice(idx, 1);

      // Dispose model
      const model = state.models.get(path);
      if (model) {
        model.dispose();
        state.models.delete(path);
      }

      state.unsavedChanges.delete(path);

      // Switch to another tab or show welcome
      if (state.activeTab === path) {
        if (state.openTabs.length > 0) {
          const newIdx = Math.min(idx, state.openTabs.length - 1);
          switchToTab(state.openTabs[newIdx]);
        } else {
          state.activeTab = null;
          state.editor.setModel(null);
          elements.welcome.style.display = 'flex';
        }
      }

      renderTabs();
    }

    // ==================== Save ====================

    async function saveCurrentFile() {
      if (!state.activeTab) return;

      const model = state.models.get(state.activeTab);
      if (!model) return;

      elements.saveStatus.textContent = 'Saving...';
      elements.saveStatus.className = 'saving';

      try {
        await writeFile(state.activeTab, model.getValue());
        state.unsavedChanges.delete(state.activeTab);
        renderTabs();
        elements.saveStatus.textContent = 'Saved';
        elements.saveStatus.className = 'saved';
        setTimeout(() => {
          elements.saveStatus.textContent = '';
          elements.saveStatus.className = '';
        }, 2000);
      } catch (err) {
        elements.saveStatus.textContent = 'Save failed';
        elements.saveStatus.className = 'error';
        console.error('Save failed:', err);
      }
    }

    // ==================== Context Menu ====================

    let contextMenuTarget = null;

    function showContextMenu(e, node) {
      e.preventDefault();
      contextMenuTarget = node;
      elements.contextMenu.style.left = e.clientX + 'px';
      elements.contextMenu.style.top = e.clientY + 'px';
      elements.contextMenu.classList.add('visible');
    }

    function hideContextMenu() {
      elements.contextMenu.classList.remove('visible');
      contextMenuTarget = null;
    }

    async function handleContextAction(action) {
      if (!contextMenuTarget) return;

      const target = contextMenuTarget;
      hideContextMenu();

      switch (action) {
        case 'new-file': {
          const name = prompt('New file name:');
          if (!name) return;
          const dir = target.type === 'directory' ? target.path : target.path.split('/').slice(0, -1).join('/');
          const newPath = dir ? dir + '/' + name : name;
          await writeFile(newPath, '');
          await refreshFileTree();
          openFile(newPath);
          break;
        }
        case 'new-folder': {
          const name = prompt('New folder name:');
          if (!name) return;
          const dir = target.type === 'directory' ? target.path : target.path.split('/').slice(0, -1).join('/');
          const newPath = dir ? dir + '/' + name : name;
          await createDirectory(newPath);
          await refreshFileTree();
          break;
        }
        case 'rename': {
          const newName = prompt('New name:', target.name);
          if (!newName || newName === target.name) return;
          // Would need a rename API endpoint
          alert('Rename not implemented yet');
          break;
        }
        case 'delete': {
          if (!confirm(\`Delete "\${target.name}"?\`)) return;
          await deleteFile(target.path);
          if (state.openTabs.includes(target.path)) {
            closeTab(target.path);
          }
          await refreshFileTree();
          break;
        }
      }
    }

    // ==================== Keyboard Shortcuts ====================

    function setupKeyboardShortcuts() {
      document.addEventListener('keydown', (e) => {
        // Use Cmd on Mac, Ctrl on Windows/Linux
        const modKey = e.metaKey || e.ctrlKey;

        // Cmd/Ctrl+S - Save
        if (modKey && e.key === 's') {
          e.preventDefault();
          saveCurrentFile();
        }

        // Cmd/Ctrl+B - Toggle sidebar
        if (modKey && e.key === 'b') {
          e.preventDefault();
          elements.sidebar.classList.toggle('collapsed');
        }

        // Cmd/Ctrl+W - Close tab
        if (modKey && e.key === 'w') {
          e.preventDefault();
          if (state.activeTab) {
            closeTab(state.activeTab);
          }
        }

        // Ctrl+L - Toggle chat sidebar (forward to parent frame)
        // Ctrl only, NOT Cmd — Cmd+L is browser "focus URL bar"
        if (e.ctrlKey && !e.metaKey && e.key === 'l') {
          e.preventDefault();
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'toggleChat' }, '*');
          }
        }

        // Cmd/Ctrl+P - Focus file search / Quick open
        if (modKey && e.key === 'p') {
          e.preventDefault();
          elements.sidebar.classList.remove('collapsed');
          elements.fileSearch.focus();
          elements.fileSearch.select();
        }


        // Cmd/Ctrl+Tab - Next tab
        if (modKey && e.key === 'Tab' && !e.shiftKey) {
          e.preventDefault();
          if (state.openTabs.length > 1) {
            const idx = state.openTabs.indexOf(state.activeTab);
            const nextIdx = (idx + 1) % state.openTabs.length;
            switchToTab(state.openTabs[nextIdx]);
          }
        }

        // Cmd/Ctrl+Shift+Tab - Previous tab
        if (modKey && e.shiftKey && e.key === 'Tab') {
          e.preventDefault();
          if (state.openTabs.length > 1) {
            const idx = state.openTabs.indexOf(state.activeTab);
            const prevIdx = (idx - 1 + state.openTabs.length) % state.openTabs.length;
            switchToTab(state.openTabs[prevIdx]);
          }
        }

        // Escape - Hide context menu and clear search
        if (e.key === 'Escape') {
          hideContextMenu();
          if (document.activeElement === elements.fileSearch) {
            elements.fileSearch.blur();
            searchQuery = '';
            elements.fileSearch.value = '';
            refreshFileTree();
          }
        }
      });
    }

    function setupFileSearch() {
      elements.fileSearch.addEventListener('input', (e) => {
        searchQuery = e.target.value;
        refreshFileTree();
      });

      elements.fileSearch.addEventListener('keydown', (e) => {
        // Enter key opens first matching file
        if (e.key === 'Enter' && searchQuery) {
          const firstFile = elements.fileTree.querySelector('.tree-item:not(.directory)');
          if (firstFile) {
            openFile(firstFile.dataset.path);
            searchQuery = '';
            elements.fileSearch.value = '';
            elements.fileSearch.blur();
          }
        }
      });
    }

    // ==================== Resize Handle ====================

    function setupResizeHandle() {
      const handle = document.getElementById('resize-handle');
      let isResizing = false;

      handle.addEventListener('mousedown', () => {
        isResizing = true;
        document.body.style.cursor = 'col-resize';
      });

      document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const newWidth = e.clientX;
        if (newWidth >= 200 && newWidth <= 400) {
          elements.sidebar.style.width = newWidth + 'px';
        }
      });

      document.addEventListener('mouseup', () => {
        isResizing = false;
        document.body.style.cursor = '';
      });
    }

    // ==================== Initialize ====================

    function showInitError(message) {
      const el = elements.loading;
      el.innerHTML = '<div style="text-align:center;max-width:420px;padding:24px;">'
        + '<div style="font-size:24px;margin-bottom:16px;">⚠️</div>'
        + '<div style="color:var(--text-primary);margin-bottom:12px;">' + message + '</div>'
        + '<button onclick="location.reload()" style="background:var(--accent);color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:13px;">Retry</button>'
        + '</div>';
    }

    function showLoadingError(message) {
      elements.loading.classList.remove('hidden');
      elements.loading.innerHTML = '<div style="max-width:560px;color:#ddd;font:13px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px 20px;text-align:center">'
        + '<div style="font-size:16px;font-weight:600;margin-bottom:8px">Monaco failed to load</div>'
        + '<div style="opacity:.9">' + message + '</div>'
        + '<button id="ide-retry" style="margin-top:14px;background:#0e639c;border:1px solid #1177bb;color:#fff;border-radius:6px;padding:6px 12px;cursor:pointer">Retry</button>'
        + '</div>';
      const retryBtn = document.getElementById('ide-retry');
      if (retryBtn) retryBtn.addEventListener('click', () => window.location.reload());
    }

    function loadScript(url) {
      return new Promise((resolve, reject) => {
        const existing = document.querySelector('script[data-src="' + url + '"]');
        if (existing) {
          if (window.require) return resolve();
          existing.addEventListener('load', () => resolve(), { once: true });
          existing.addEventListener('error', () => reject(new Error('Failed to load ' + url)), { once: true });
          return;
        }

        const script = document.createElement('script');
        script.src = url;
        script.async = true;
        script.dataset.src = url;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('Failed to load ' + url));
        document.head.appendChild(script);
      });
    }

    // Track which source provided the loader so editor modules use the same origin.
    // The AMD loader caches module state by name, so switching the base path after
    // a failed require() won't retry -- the cached failure is returned immediately.
    let monacoBase = '';

    async function ensureMonacoLoader() {
      if (window.require) {
        console.log('[IDE] AMD loader already present');
        return;
      }
      const sources = [
        { loader: 'https://cdn.jsdelivr.net/npm/monaco-editor@${monacoVersion}/min/vs/loader.js', base: 'https://cdn.jsdelivr.net/npm/monaco-editor@${monacoVersion}/min/vs' },
        { loader: 'https://unpkg.com/monaco-editor@${monacoVersion}/min/vs/loader.js', base: 'https://unpkg.com/monaco-editor@${monacoVersion}/min/vs' },
      ];

      let lastError = null;
      for (const { loader, base } of sources) {
        try {
          console.log('[IDE] Trying loader source:', loader);
          await loadScript(loader);
          if (window.require) {
            console.log('[IDE] Loader succeeded from:', loader);
            monacoBase = base;
            return;
          }
          console.warn('[IDE] Script loaded but window.require not defined:', loader);
        } catch (err) {
          lastError = err;
          console.warn('[IDE] Loader source failed:', loader, err);
        }
      }

      throw lastError || new Error('Monaco AMD loader unavailable');
    }

    function loadMonacoEditor() {
      return new Promise((resolve, reject) => {
        if (!window.require) {
          reject(new Error('Monaco require() loader missing'));
          return;
        }

        if (!monacoBase) {
          reject(new Error('Monaco base path not resolved'));
          return;
        }

        let settled = false;
        const editorTimeout = setTimeout(() => {
          if (!settled) {
            settled = true;
            console.error('[IDE] loadMonacoEditor timed out after 20s, base:', monacoBase);
            reject(new Error('Monaco editor loading timed out (20s). Check network / console for blocked requests.'));
          }
        }, 20000);

        console.log('[IDE] Calling require([vs/editor/editor.main]) with base:', monacoBase);
        window.require.config({ paths: { vs: monacoBase } });
        window.require(['vs/editor/editor.main'], () => {
          if (!settled) {
            settled = true;
            clearTimeout(editorTimeout);
            resolve();
          }
        }, (err) => {
          if (!settled) {
            settled = true;
            clearTimeout(editorTimeout);
            console.error('[IDE] Monaco editor failed to load from:', monacoBase, err);
            reject(err || new Error('Unable to load Monaco editor bundle'));
          }
        });
      });
    }

    async function init() {
      const initTimeout = setTimeout(() => {
        console.error('[IDE] Initialization timed out after 30s');
        showInitError('Editor is taking too long to load. The server may be unreachable.');
      }, 30000);

      try {
        console.log('[IDE] Starting init...');
        await ensureMonacoLoader();
        console.log('[IDE] Loader ready, monacoBase =', monacoBase);

        // Wire up require.onError so AMD-level failures don't go silent
        if (window.require && window.require.config) {
          window.require.config({
            onError: function(err) {
              console.error('[IDE] AMD require.onError:', err);
            }
          });
        }

        await loadMonacoEditor();
        console.log('[IDE] Monaco editor module loaded');

        // Create editor
        state.editor = monaco.editor.create(elements.editorContainer, {
          theme: '${theme}',
          fontSize: 14,
          fontFamily: "'Fira Code', 'Cascadia Code', Consolas, monospace",
          fontLigatures: true,
          minimap: { enabled: true },
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 2,
          wordWrap: 'off',
          lineNumbers: 'on',
          renderLineHighlight: 'all',
          cursorBlinking: 'smooth',
          smoothScrolling: true,
        });

        // Save view state on switch
        state.editor.onDidChangeCursorPosition(() => {
          if (state.activeTab) {
            try {
              const viewState = state.editor.saveViewState();
              localStorage.setItem('viewState:' + state.activeTab, JSON.stringify(viewState));
            } catch (_e) { /* localStorage full or unavailable */ }
          }
        });

        const savedWorkspaceRoot = localStorage.getItem('workspaceRoot');
        state.workspaceRoot = normalizeWorkspaceRoot(savedWorkspaceRoot || '/');
        updateWorkspacePathLabel();

        // Setup UI
        setupKeyboardShortcuts();
        setupResizeHandle();
        setupFileSearch();

        // Context menu handlers
        elements.contextMenu.querySelectorAll('.context-item').forEach(item => {
          item.addEventListener('click', () => handleContextAction(item.dataset.action));
        });
        document.addEventListener('click', hideContextMenu);

        // Toolbar buttons
        document.getElementById('toggle-sidebar').addEventListener('click', () => {
          elements.sidebar.classList.toggle('collapsed');
        });
        document.getElementById('collapse-btn').addEventListener('click', () => {
          state.expandedDirs.clear();
          state.expandedDirs.add('');
          refreshFileTree();
        });
        document.getElementById('new-file-btn').addEventListener('click', async () => {
          const name = prompt('New file name:');
          if (!name) return;
          const newPath = workspaceJoin(name);
          await writeFile(newPath, '');
          await refreshFileTree();
          openFile(newPath);
        });
        document.getElementById('open-folder-btn').addEventListener('click', async () => {
          const input = prompt('Open folder (relative to workspace root):', state.workspaceRoot);
          if (input === null) return;
          await setWorkspaceRoot(input);
        });
        document.getElementById('refresh-btn').addEventListener('click', async () => {
          await refreshFileTree();
        });

        // Load file tree in background (don't block IDE render)
        refreshFileTree().catch(err => {
          console.error('Initial file tree load failed:', err);
        });

        // Show editor shell immediately; async data can continue loading
        elements.loading.classList.add('hidden');

        // Restore open tabs from localStorage
        try {
          const savedTabs = localStorage.getItem('openTabs');
          const savedActive = localStorage.getItem('activeTab');
          if (savedTabs) {
            const tabs = JSON.parse(savedTabs);
            for (const path of tabs) {
              if (state.workspaceRoot !== '/' && !path.startsWith(state.workspaceRoot + '/')) {
                continue;
              }
              state.openTabs.push(path);
            }
            if (savedActive && state.openTabs.includes(savedActive)) {
              await switchToTab(savedActive);
            } else if (state.openTabs.length > 0) {
              await switchToTab(state.openTabs[0]);
            }
            renderTabs();
          }
        } catch (restoreErr) {
          console.warn('[IDE] Failed to restore tabs from localStorage:', restoreErr);
          state.openTabs = [];
          state.activeTab = null;
        }

        // Save tabs on change
        const saveTabs = () => {
          try {
            localStorage.setItem('openTabs', JSON.stringify(state.openTabs));
            localStorage.setItem('activeTab', state.activeTab || '');
            localStorage.setItem('workspaceRoot', state.workspaceRoot);
          } catch (_e) { /* localStorage full or unavailable */ }
        };
        setInterval(saveTabs, 5000);
        window.addEventListener('beforeunload', saveTabs);

      } catch (err) {
        console.error('[IDE] Initialization failed:', err);
        clearTimeout(initTimeout);
        showLoadingError((err && err.message) ? err.message : 'Network or browser policy blocked Monaco assets.');
        return;
      }
      clearTimeout(initTimeout);
      console.log('[IDE] Initialization complete');
    }

    init();
  </script>
</body>
</html>`;
}
export { EXTENSION_TO_LANGUAGE };
//# sourceMappingURL=ide-page.js.map