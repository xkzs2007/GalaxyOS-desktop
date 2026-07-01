// renderer/src/tokui/dsl-inspector.js — DSL Inspector (P1 → P0: v9.6 升级).
//
// v9.6: 从 test_tokui_vue 搬入三个核心能力：
//   1. DSL 语法高亮 — tag/attr/value/string/comment 着色
//   2. 贪心对齐算法 — DSL 行号 → 渲染 DOM 元素精准映射
//   3. rAF 节流 — 避免流式推送时面板频繁重绘
//
// Usage:
//   import dslInspector from './dsl-inspector.js';
//   dslInspector.setActive(true);
//   dslInspector.record('[bubble role:user]');
//   dslInspector.render('details-host');
//   dslInspector.clear();

import { getInstance } from './runtime.js';

// ── State ──────────────────────────────────────────────────────

let _dslSource = '';          // 累积的原始 DSL 字符串
let _active = false;
let _displayRaf = 0;          // rAF id for throttled display
let _renderHost = null;       // cached host element for live update
let _autoScroll = true;       // auto-scroll to bottom during streaming
let _domMapping = null;       // cached line → element mapping (lazy built)
let _styleInjected = false;   // whether CSS has been injected

// ── DSL 语法高亮（移植自 test_tokui_vue highlightDsl）──

const ESC = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function highlightDsl(raw) {
  if (!raw) return ' ';
  let out = '';
  let i = 0;
  const n = raw.length;
  let afterBracket = false;
  const span = (cls, txt) => `<span class="dsl-${cls}">${ESC(txt)}</span>`;
  while (i < n) {
    const ch = raw[i];
    // Comment: ; onward to EOL
    if (ch === ';') {
      out += span('cmt', raw.slice(i));
      break;
    }
    // Quoted string
    if (ch === '"') {
      let j = raw.indexOf('"', i + 1);
      if (j < 0) j = n;
      out += span('str', raw.slice(i, j + 1));
      i = j + 1;
      continue;
    }
    // Open bracket
    if (ch === '[') {
      out += span('pun', '[');
      i++;
      afterBracket = true;
      continue;
    }
    // Close bracket
    if (ch === ']') {
      out += span('pun', ']');
      i++;
      afterBracket = false;
      continue;
    }
    // Whitespace
    if (/\s/.test(ch)) {
      out += ESC(ch);
      i++;
      continue;
    }
    // Token: scan until whitespace / bracket / quote
    let j = i + 1;
    while (j < n && !/[\s[\]"]/.test(raw[j])) j++;
    const tok = raw.slice(i, j);
    i = j;
    const colon = tok.indexOf(':');
    if (colon > 0) {
      // key:value pair
      out += span('key', tok.slice(0, colon)) + span('pun', ':') + span('val', tok.slice(colon + 1));
      afterBracket = false;
    } else if (afterBracket) {
      // first token after [ → component type
      out += span('type', tok);
      afterBracket = false;
    } else {
      // plain text content
      out += span('txt', tok);
    }
  }
  return out || ' ';
}

// ── 贪心对齐算法（移植自 test_tokui_vue frameLine）──────

/**
 * Map a DSL source line index to a rendered DOM element using a
 * greedy-alignment stack algorithm. Handles:
 *   - [upd] tags (no DOM element produced)
 *   - Auto-generated container DOM nodes (skipped via data-tokui-tag mismatch)
 *   - Open/close tag pairing for accurate sequence numbering
 *
 * Returns the matching HTMLElement or null.
 */
function mapLineToElement(lineIdx) {
  if (lineIdx < 0 || !_dslSource) return null;

  const container = document.getElementById('tokui-container');
  if (!container) return null;

  const lines = _dslSource.replace(/\n+$/, '').split('\n');
  if (lineIdx >= lines.length) return null;

  // ── Phase 1: extract all tags with their kind (open/close/upd) ─
  const tags = [];
  // Regex 从 test_tokui_vue 直接移植
  const TAG_RE = /\[\/?[a-zA-Z][\w-]*[^\[\]]*\]/g;
  lines.forEach((ln, li) => {
    let m;
    TAG_RE.lastIndex = 0;
    while ((m = TAG_RE.exec(ln))) {
      const tk = m[0];
      const isClose = /^\[\/[a-zA-Z]/.test(tk);
      const type = tk
        .replace(/^\[\/?/, '')
        .match(/^[a-zA-Z][\w-]*/)?.[0];
      if (type) tags.push({ line: li, type, close: isClose });
    }
  });

  if (!tags.length) return null;

  // ── Phase 2: build open/close stack → sequence numbers ─
  const openSeq = [];
  const stack = [];
  const lineRep = new Map();  // line → sequence number

  for (const tg of tags) {
    if (tg.close) {
      // Find matching open tag in stack (walk backwards)
      let k = stack.length - 1;
      while (k >= 0 && stack[k].type !== tg.type) k--;
      if (k >= 0) {
        // Close tag maps to same sequence number as its open tag
        if (!lineRep.has(tg.line)) lineRep.set(tg.line, stack[k].seq);
        stack.length = k;  // pop everything above (including match)
      }
    } else {
      if (tg.type === 'upd') continue;  // [upd] produces no DOM element
      const seq = openSeq.length;
      openSeq.push(tg.type);
      stack.push({ type: tg.type, seq });
      if (!lineRep.has(tg.line)) lineRep.set(tg.line, seq);
    }
  }

  // ── Phase 3: collect DOM elements, then greedy-align ─
  const allDom = Array.from(container.querySelectorAll('[data-tokui-tag]'));
  const seqToDom = new Array(openSeq.length).fill(null);
  let j = 0;
  for (let i = 0; i < openSeq.length; i++) {
    // Skip auto-generated DOM nodes whose tag doesn't match the open sequence
    while (j < allDom.length && allDom[j].dataset.tokuiTag !== openSeq[i]) j++;
    if (j < allDom.length) {
      seqToDom[i] = allDom[j];
      j++;
    }
  }

  const seq = lineRep.get(lineIdx);
  if (seq === undefined || seq >= seqToDom.length || !seqToDom[seq]) return null;
  return seqToDom[seq];
}

// ── Fixed overlay for highlighted element ──────────────────────

let _overlayEl = null;

function ensureOverlay() {
  if (_overlayEl) return _overlayEl;
  const ov = document.createElement('div');
  ov.className = 'dsl-inspector-overlay';
  ov.style.cssText = 'display:none; opacity:0;';
  const label = document.createElement('span');
  label.className = 'dsl-inspector-overlay-label';
  ov.appendChild(label);
  document.body.appendChild(ov);
  _overlayEl = ov;
  return ov;
}

function placeOverlay(targetEl) {
  const ov = ensureOverlay();
  if (!targetEl) {
    ov.style.display = 'none';
    ov.style.opacity = '0';
    return;
  }
  const r = targetEl.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) {
    ov.style.display = 'none';
    return;
  }
  ov.style.top = `${r.top}px`;
  ov.style.left = `${r.left}px`;
  ov.style.width = `${r.width}px`;
  ov.style.height = `${r.height}px`;
  ov.style.display = 'block';
  ov.style.opacity = '1';
  // Read the tag name from the DOM element for the overlay label
  const label = ov.querySelector('.dsl-inspector-overlay-label');
  if (label) {
    const tagName = targetEl.dataset.tokuiTag || '';
    label.textContent = tagName ? `[${tagName}]` : '';
  }
  // Scroll target into view if off-screen
  const ra = document.getElementById('tokui-container');
  if (ra) {
    const cr = ra.getBoundingClientRect();
    if (r.top < cr.top + 20 || r.bottom > cr.bottom - 20) {
      ra.scrollTop += r.top - cr.top - 60;
    }
  }
}

function clearOverlay() {
  const ov = _overlayEl;
  if (ov) {
    ov.style.display = 'none';
    ov.style.opacity = '0';
  }
}

// ── Public API ──────────────────────────────────────────────────

/** Inject DSL syntax highlighting CSS once. */
function injectStyles() {
  if (_styleInjected) return;
  if (document.getElementById('dsl-inspector-css')) return;
  const style = document.createElement('style');
  style.id = 'dsl-inspector-css';
  style.textContent = `
    /* DSL syntax highlighting — ported from test_tokui_vue */
    .dsl-type { color: #e06c75; font-weight: 600; }          /* component tag */
    .dsl-key  { color: #61afef; }                            /* attr key */
    .dsl-pun  { color: #abb2bf; }                            /* [ ] : */
    .dsl-val  { color: #98c379; }                            /* attr value */
    .dsl-str  { color: #e5c07b; }                            /* quoted string */
    .dsl-txt  { color: #abb2bf; }                            /* plain text */
    .dsl-cmt  { color: #5c6370; font-style: italic; }         /* ; comment */
    /* Overlay — refined fixed-position box for element highlighting */
    .dsl-inspector-overlay {
      position: fixed;
      z-index: 10002;
      pointer-events: none;
      border: 2px solid #6366f1;
      border-radius: 4px;
      background: rgba(99, 102, 241, 0.08);
      transition: opacity 0.15s ease;
    }
    .dsl-inspector-overlay-label {
      position: absolute;
      top: -22px;
      left: -2px;
      background: #6366f1;
      color: #fff;
      font-size: 11px;
      padding: 2px 6px;
      border-radius: 3px 3px 0 0;
      white-space: nowrap;
      font-family: monospace;
    }
  `;
  document.head.appendChild(style);
  _styleInjected = true;
}

// ── Public methods ─────────────────────────────────────────────

const dslInspector = {
  setActive(active) {
    _active = active;
    if (!active) {
      this.clear();
      clearOverlay();
    }
  },

  isActive() {
    return _active;
  },

  /** Record a DSL fragment. Called from feed.js. */
  record(chunk) {
    if (!_active) return;
    _dslSource += chunk;
    _domMapping = null;  // invalidate cached mapping
    this._scheduleDisplay();
  },

  clear() {
    _dslSource = '';
    _domMapping = null;
    clearOverlay();
    if (_displayRaf) {
      cancelAnimationFrame(_displayRaf);
      _displayRaf = 0;
    }
    // Immediate re-render to show empty state
    this._renderNow();
  },

  getSource() {
    return _dslSource;
  },

  lineCount() {
    return _dslSource ? _dslSource.split('\n').length : 0;
  },

  /**
   * Render the DSL inspector into a container.
   * @param {string|HTMLElement} container - '#details-host' or element
   */
  render(container) {
    _renderHost = typeof container === 'string'
      ? document.getElementById(container)
      : container;
    this._renderNow();
  },

  // ── Internal: throttled re-render (rAF) ───────────────────

  _scheduleDisplay() {
    if (_displayRaf) return;  // already scheduled
    _displayRaf = requestAnimationFrame(() => {
      _displayRaf = 0;
      this._renderNow();
    });
  },

  _renderNow() {
    const host = _renderHost;
    if (!host) return;

    const ui = getInstance();
    if (!ui) return;

    // Inject CSS on first render
    injectStyles();

    const source = _dslSource;
    const lines = source ? source.replace(/\n+$/, '').split('\n') : [];

    host.innerHTML = '';

    // Empty / inactive state
    if (!source || !source.trim()) {
      ui.startStream(host);
      ui.feed(`[card tt:"🔍 DSL Inspector" v:highlight]`);
      ui.feed(`  [btngroup]`);
      ui.feed(`    [btn tx:"${_active ? '🟢 已启用' : '⚫ 已禁用'}" clk:onDslInspectorToggle sm v:muted]`);
      ui.feed(`    [btn tx:"清除" clk:onDslInspectorClear sm v:muted]`);
      if (_autoScroll) {
        ui.feed(`    [btn tx:"📌 自动滚屏:开" clk:onDslInspectorToggleScroll sm v:muted]`);
      } else {
        ui.feed(`    [btn tx:"📌 自动滚屏:关" clk:onDslInspectorToggleScroll sm v:muted]`);
      }
      ui.feed(`  [/btngroup]`);
      if (!_active) {
        ui.feed(`  [callout t:info tt:"DSL Inspector"]按 Ctrl+Shift+D 启用 DSL 捕获。发送一条消息后，此处将实时显示接收到的 DSL 碎片（含行号与语法着色）。[/callout]`);
      } else {
        ui.feed(`  [empty tt:"等待 DSL" desc:"发送一条消息开始捕获" i:code]`);
      }
      ui.feed(`[/card]`);
      ui.endStream();
      return;
    }

    ui.startStream(host);

    // Header with stats + controls
    const lineCount = lines.length;
    ui.feed(`[card tt:"🔍 DSL Inspector · ${lineCount} 行" v:highlight]`);
    ui.feed(`  [btngroup]`);
    ui.feed(`    [btn tx:"${_active ? '🟢 已启用' : '⚫ 已禁用'}" clk:onDslInspectorToggle sm v:muted]`);
    ui.feed(`    [btn tx:"清除" clk:onDslInspectorClear sm v:muted]`);
    if (_autoScroll) {
      ui.feed(`    [btn tx:"📌 自动滚屏:开" clk:onDslInspectorToggleScroll sm v:muted]`);
    } else {
      ui.feed(`    [btn tx:"📌 自动滚屏:关" clk:onDslInspectorToggleScroll sm v:muted]`);
    }
    ui.feed(`    [span sm v:muted]点击行 → 左侧定位组件[/span]`);
    ui.feed(`  [/btngroup]`);
    ui.feed(`  [dv]`);

    // ── Render syntax-highlighted DSL lines ──────────────────
    // TokUI doesn't support inline HTML, so we render each line as a
    // separate [span] with clk:onDslInspectorLineClick for interactivity.
    // The actual syntax highlighting is done via DOM manipulation AFTER
    // TokUI renders the spans, using innerHTML injection.
    const MAX_LINES = 300;
    const showLines = Math.min(lines.length, MAX_LINES);

    for (let i = 0; i < showLines; i++) {
      const lineNum = i + 1;
      const line = lines[i];
      // Use a unique id for DOM targeting
      ui.feed(`    [span id:dsl-line-${i} clk:onDslInspectorLineClick act:${lineNum} v:muted sm][/span]`);
    }

    if (lines.length > MAX_LINES) {
      ui.feed(`    [p v:muted sm]… 仅显示前 ${MAX_LINES} 行 (共 ${lines.length} 行)[/p]`);
    }

    ui.feed(`[/card]`);
    ui.endStream();

    // ── v9.6: Inject syntax-highlighted HTML after TokUI renders ─
    this._injectHighlighting(lines, showLines);

    // ── v9.6: Auto-scroll details panel during streaming ────
    if (_autoScroll && _active) {
      requestAnimationFrame(() => {
        const scrollHost = host.closest?.('.details-scroll') || host.parentElement;
        if (scrollHost) {
          scrollHost.scrollTop = scrollHost.scrollHeight;
        }
      });
    }
  },

  /**
   * v9.6: Post-render injection of syntax-highlighted DSL into
   * the TokUI-generated spans. Uses requestAnimationFrame to wait
   * for TokUI's DOM to settle.
   */
  _injectHighlighting(lines, showLines) {
    const host = _renderHost;
    if (!host) return;

    // Wait one frame for TokUI DOM to be ready
    requestAnimationFrame(() => {
      for (let i = 0; i < showLines; i++) {
        const spanEl = host.querySelector(`#dsl-line-${i}`);
        if (!spanEl) continue;

        const line = lines[i];
        const indent = line.match(/^(\s*)/)?.[1]?.length || 0;
        const indentStr = '·'.repeat(Math.min(indent / 2, 6));
        const numPad = String(i + 1).padStart(3, ' ');
        const highlighted = highlightDsl(line.slice(0, 200)
          + (line.length > 200 ? '…' : ''));

        spanEl.innerHTML = `${numPad} ${indentStr}${highlighted}`;
        spanEl.style.fontFamily = "'SF Mono', 'Cascadia Code', 'Fira Code', monospace";
        spanEl.style.fontSize = '12px';
        spanEl.style.lineHeight = '1.6';
        spanEl.style.display = 'block';
        spanEl.style.cursor = 'pointer';
      }
    });
  },

  /**
   * Highlight the DOM element for a given DSL line number.
   * Uses the greedy-alignment algorithm for precise mapping.
   */
  highlightLine(lineNum) {
    if (!_active) return;

    const lineIdx = lineNum - 1;
    const element = mapLineToElement(lineIdx);

    if (!element) {
      clearOverlay();
      return;
    }

    placeOverlay(element);

    // Auto-clear after 3s
    clearTimeout(this._overlayTimer);
    this._overlayTimer = setTimeout(() => {
      clearOverlay();
    }, 3000);
  },

  /** Toggle auto-scroll during streaming. */
  toggleAutoScroll() {
    _autoScroll = !_autoScroll;
    return _autoScroll;
  },
};

export default dslInspector;
