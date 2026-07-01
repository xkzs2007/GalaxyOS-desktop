// renderer/src/tokui/dsl-inspector.js — DSL Inspector (P1).
//
// Accumulates TokUI DSL fed through the chat stream and renders it
// in the details panel as a line-by-line source view. Click a DSL line
// to highlight the corresponding rendered DOM element in the chat.
//
// Architecture:
//   - feed.js calls dslInspect.record(chunk) on every feed()
//   - The inspector maintains a line → element mapping
//   - Renders in details panel as [card] with clickable lines
//   - Click line → overlay on the matching DOM element
//
// Usage:
//   import dslInspector from './dsl-inspector.js';
//   dslInspector.record('[bubble role:user]');
//   dslInspector.render('details-host');
//   // Or: dslInspector.clear() to reset after session switch

import { getInstance } from './runtime.js';
import { escapeDsl } from '../utils.js';

// ── State ──────────────────────────────────────────────────────

let _dslChunks = [];
let _active = false;

// ── Public API ──────────────────────────────────────────────────

const dslInspector = {
  /** Enable/disable DSL accumulation */
  setActive(active) {
    _active = active;
  },

  /** Check if currently active */
  isActive() {
    return _active;
  },

  /** Record a DSL fragment. Called from feed.js. */
  record(chunk) {
    if (!_active) return;
    _dslChunks.push(chunk);
  },

  /** Clear accumulated DSL. */
  clear() {
    _dslChunks = [];
  },

  /** Get full accumulated DSL as a single string. */
  getSource() {
    return _dslChunks.join('');
  },

  /** Get line count. */
  lineCount() {
    return this.getSource().split('\n').length;
  },

  /**
   * Render the DSL inspector into a container.
   * @param {string|HTMLElement} container - '#details-host' or element
   */
  render(container) {
    const host = typeof container === 'string'
      ? document.getElementById(container)
      : container;
    if (!host) return;

    const ui = getInstance();
    if (!ui) return;

    const source = this.getSource();
    const lines = source.split('\n');
    const lineCount = lines.length;

    host.innerHTML = '';

    if (!source.trim()) {
      ui.startStream(host);
      ui.feed(`[card tt:"🔍 DSL Inspector"][empty tt:"暂无 DSL" desc:"发送一条消息开始捕获" i:code][/card]`);
      ui.endStream();
      return;
    }

    ui.startStream(host);

    // Header with stats
    ui.feed(`[card tt:"🔍 DSL Inspector · ${lineCount} 行 · ${_dslChunks.length} 片段" v:highlight]`);
    ui.feed(`  [row]`);
    ui.feed(`    [btn tx:"清除" clk:onDslInspectorClear sm v:muted]`);
    ui.feed(`    [btn tx:"${_active ? '🟢 已启用' : '⚫ 已禁用'}" clk:onDslInspectorToggle sm v:muted]`);
    ui.feed(`  [/row]`);
    ui.feed(`  [dv]`);

    // Render each line as a clickable [p] with line number
    for (let i = 0; i < Math.min(lines.length, 200); i++) {
      const lineNum = i + 1;
      const line = lines[i];
      const escaped = escapeDsl(line.slice(0, 120)) + (line.length > 120 ? '…' : '');
      const indent = line.match(/^(\s*)/)?.[1]?.length || 0;
      const indentStr = '·'.repeat(Math.min(indent / 2, 6));

      ui.feed(`    [span clk:onDslInspectorLineClick act:${lineNum} v:muted sm]${String(lineNum).padStart(3, ' ')} ${indentStr}${escaped || ' '}[/span]`);
    }

    if (lines.length > 200) {
      ui.feed(`    [p v:muted sm]… 仅显示前 200 行 (共 ${lines.length} 行)[/p]`);
    }

    ui.feed(`[/card]`);
    ui.endStream();
  },

  /**
   * Highlight the DOM element for a given DSL line number.
   * Draws a fixed overlay box around the element.
   */
  highlightLine(lineNum) {
    if (!_active) return;
    const source = this.getSource();
    const lines = source.split('\n');
    const targetLine = lines[lineNum - 1];
    if (!targetLine) return;

    // Try to extract component tag from the line
    const tagMatch = targetLine.match(/\[(\/?)(\w[\w-]*)/);
    if (!tagMatch) return;

    const tagName = tagMatch[2];
    const isClose = tagMatch[1] === '/';

    // Look for the DOM element with data-tokui-tag attribute
    const container = document.getElementById('tokui-container');
    if (!container) return;

    // Try to find elements by data-tokui-tag that match this line's tag
    const elements = container.querySelectorAll(`[data-tokui-tag*="${tagName}"]`);
    let targetEl = null;
    if (elements.length > 0) {
      // Pick the most recent one that isn't already highlighted
      for (const el of [...elements].reverse()) {
        if (!el._inspectorHighlighted) {
          targetEl = el;
          break;
        }
      }
      if (!targetEl) targetEl = elements[elements.length - 1];
    }

    if (!targetEl) return;

    // Clear previous highlights
    this.clearHighlights(container);

    // Draw overlay
    const rect = targetEl.getBoundingClientRect();
    const overlay = document.createElement('div');
    overlay.className = 'dsl-inspector-overlay';
    overlay.style.cssText = [
      'position: fixed',
      'z-index: 10002',
      'pointer-events: none',
      'border: 2px solid #6366f1',
      'border-radius: 4px',
      'background: rgba(99,102,241,0.08)',
      `left: ${rect.left - 4}px`,
      `top: ${rect.top - 4}px`,
      `width: ${rect.width + 8}px`,
      `height: ${rect.height + 8}px`,
      'transition: all 0.15s ease',
    ].join(';');
    document.body.appendChild(overlay);

    // Auto-remove after 3s
    setTimeout(() => {
      overlay.style.opacity = '0';
      setTimeout(() => overlay.remove(), 200);
    }, 2800);

    // Scroll the element into view
    targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  },

  /** Remove all inspector overlays. */
  clearHighlights(container) {
    document.querySelectorAll('.dsl-inspector-overlay').forEach((el) => el.remove());
    if (container) {
      container.querySelectorAll('[data-tokui-tag]').forEach((el) => {
        el._inspectorHighlighted = false;
      });
    }
  },
};

export default dslInspector;
