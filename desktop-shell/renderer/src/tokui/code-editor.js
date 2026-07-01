// renderer/src/tokui/code-editor.js — CodeMirror 6 编辑器（TokUI DSL 集成）
//
// 架构：
//   TokUI 负责外壳（[card] + [btngroup] toolbar），CodeMirror 接管编辑区。
//   DSL 渲染 → requestAnimationFrame → mount CodeMirror on placeholder。
//
// 用法:
//   import { renderCodeEditor, getEditor } from './code-editor.js';
//
//   // 1) 渲染 TokUI 外壳 + 挂载 CodeMirror
//   await renderCodeEditor('#details-host', {
//     id: 'main-editor',
//     lang: 'python',
//     value: 'print("hello")',
//     readonly: false,
//     onSave: (code) => galaxy.saveFile?.(path, code),
//     onRun:  (code) => galaxy.runCode?.(code, lang),
//   });
//
//   // 2) 外部获取 editor 实例
//   const ed = getEditor('main-editor');
//   ed.setValue('new code');
//
// 依赖：
//   CodeMirror 6 从 esm.sh CDN 按需动态 import（零构建，首屏不加载）
//   语言包按需导入（python / javascript / json / markdown / html / css / sql / yaml / xml）

import { getInstance, registerHandler } from './runtime.js';

// ── State ──────────────────────────────────────────────────────
const _editors = new Map();       // id → { view, host, options }
let _cmLoaded = false;           // CodeMirror core loaded?
let _cmModules = null;           // cached core imports
const _loadedLangs = new Set();  // already loaded language packages

// ── Language package lazy-loader ──────────────────────────────

const LANG_PKGS = {
  python:     () => import('https://esm.sh/@codemirror/lang-python@6.0'),
  javascript: () => import('https://esm.sh/@codemirror/lang-javascript@6.0'),
  json:       () => import('https://esm.sh/@codemirror/lang-json@6.0'),
  markdown:   () => import('https://esm.sh/@codemirror/lang-markdown@6.0'),
  html:       () => import('https://esm.sh/@codemirror/lang-html@6.0'),
  css:        () => import('https://esm.sh/@codemirror/lang-css@6.0'),
  sql:        () => import('https://esm.sh/@codemirror/lang-sql@6.0'),
  xml:        () => import('https://esm.sh/@codemirror/lang-xml@6.0'),
  yaml:       () => import('https://esm.sh/@codemirror/lang-yaml@6.0'),
};

/** Load CodeMirror core (once) */
async function loadCodeMirror() {
  if (_cmLoaded) return _cmModules;
  try {
    const [mod, themeMod, stateMod] = await Promise.all([
      import('https://esm.sh/codemirror@6.0.2'),
      import('https://esm.sh/@codemirror/theme-one-dark@6.0'),
      import('https://esm.sh/@codemirror/state@6.0'),
    ]);
    _cmModules = {
      EditorView: mod.EditorView,
      minimalSetup: mod.minimalSetup,
      basicSetup: mod.basicSetup,
      EditorState: stateMod.EditorState,
      oneDark: themeMod?.oneDark,
    };
    _cmLoaded = true;
    return _cmModules;
  } catch (e) {
    console.error('[code-editor] CodeMirror CDN load failed:', e.message);
    throw new Error('CodeMirror 加载失败，请检查网络连接');
  }
}

/** Load a language extension (cached by browser module cache) */
async function loadLang(lang) {
  const loader = LANG_PKGS[lang];
  if (!loader) return []; // unsupported language, fall back to plain text
  try {
    const mod = await loader();
    _loadedLangs.add(lang);
    // Each package exports its language function under a different name
    const fn = mod[lang] || mod[`${lang}Language`] || mod.default;
    return typeof fn === 'function' ? [fn()] : [];
  } catch (e) {
    console.warn(`[code-editor] failed to load lang "${lang}":`, e.message);
    return [];
  }
}

// ── Inject CodeMirror CSS ─────────────────────────────────────

let _cssInjected = false;

function injectCMCSS() {
  if (_cssInjected) return;
  if (document.getElementById('codemirror-css')) { _cssInjected = true; return; }
  const link = document.createElement('link');
  link.id = 'codemirror-css';
  link.rel = 'stylesheet';
  link.href = 'https://esm.sh/@codemirror/view@6.0/dist/index.css';
  document.head.appendChild(link);
  _cssInjected = true;
}

// ── Theme mapping ──────────────────────────────────────────────

const THEME_EXT = {
  'dark':        'oneDark',
  'modern-dark': 'oneDark',
  'modern':      null,
  'default':     null,
};

function getThemeExtension() {
  const theme = window.TokUI?.getTheme?.() || 'modern-dark';
  const name = THEME_EXT[theme];
  if (name === 'oneDark' && _cmModules?.oneDark) return _cmModules.oneDark;
  return null;
}

// ── Public API ─────────────────────────────────────────────────

/**
 * Render a code editor using TokUI DSL shell + CodeMirror mount.
 *
 * @param {string|HTMLElement} container - '#details-host' or DOM element
 * @param {object} opts
 * @param {string}  opts.id       - unique editor id (for getEditor)
 * @param {string}  [opts.lang]   - language (python/js/json/...)
 * @param {string}  [opts.value]  - initial code
 * @param {boolean} [opts.readonly]
 * @param {number}  [opts.minHeight] - default 300
 * @param {string}  [opts.title]  - card title
 * @param {Function}[opts.onSave] - (code) => void
 * @param {Function}[opts.onRun]  - (code, lang) => void
 * @param {Function}[opts.onChange] - (code) => void
 */
export async function renderCodeEditor(container, opts = {}) {
  const {
    id = 'code-editor-' + Date.now(),
    lang = 'plain',
    value = '',
    readonly = false,
    minHeight = 300,
    title = '代码编辑器',
    onSave,
    onRun,
    onChange,
  } = opts;

  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  // ── 1) TokUI DSL shell ────────────────────────────────────
  const langLabel = lang === 'plain' ? '纯文本' : lang.toUpperCase();
  const btnSave = onSave ? '[btn tx:"保存" clk:onCodeEditorSave sm v:success]' : '';
  const btnRun  = onRun  ? '[btn tx:"▶ 运行" clk:onCodeEditorRun sm v:accent]' : '';

  ui.startStream(host);
  ui.feed(`[card tt:"${title} · ${langLabel}" v:highlight]`);
  if (btnSave || btnRun) {
    ui.feed(`  [btngroup]${btnSave}${btnRun}[/btngroup]`);
  }
  ui.feed(`  [dv id:${id}-mount class:code-editor-mount][/dv]`);
  ui.feed(`[/card]`);
  ui.endStream();

  // ── 2) Wait TokUI DOM settle → mount CodeMirror ───────────
  requestAnimationFrame(async () => {
    await mountCodeMirror(id, lang, value, readonly, minHeight, { onSave, onRun, onChange });
  });
}

/**
 * Mount CodeMirror directly on a DOM element (no TokUI shell).
 * Useful when the container is managed externally.
 */
export async function mountCodeMirror(id, lang, value, readonly, minHeight, callbacks = {}) {
  const { onSave, onRun, onChange } = callbacks;

  // Load dependencies
  injectCMCSS();
  const [cm, langExts] = await Promise.all([
    loadCodeMirror(),
    loadLang(lang),
  ]);

  const mountEl = document.getElementById(`${id}-mount`);
  if (!mountEl) {
    console.warn(`[code-editor] mount point #${id}-mount not found`);
    return null;
  }

  // Compute extensions
  const extensions = [cm.minimalSetup];
  if (readonly) extensions.push(cm.EditorState.readOnly.of(true));
  extensions.push(...langExts);
  const themeExt = getThemeExtension();
  if (themeExt) extensions.push(themeExt);
  if (onChange) {
    extensions.push(cm.EditorView.updateListener.of((update) => {
      if (update.docChanged) onChange(update.state.doc.toString());
    }));
  }

  // Previous editor with same id? destroy it
  const prev = _editors.get(id);
  if (prev) prev.view.destroy();

  // Create editor
  const view = new cm.EditorView({
    doc: value,
    extensions,
    parent: mountEl,
  });

  // Set min height
  mountEl.style.minHeight = `${minHeight}px`;
  mountEl.style.overflow = 'auto';

  const editor = { view, mountEl, lang, id, onSave, onRun, onChange };
  _editors.set(id, editor);

  // ── Register handlers for this editor ─────────────────────
  if (onSave) {
    registerHandler(`onCodeEditorSave:${id}`, () => {
      const code = view.state.doc.toString();
      onSave(code);
    });
  }
  if (onRun) {
    registerHandler(`onCodeEditorRun:${id}`, () => {
      const code = view.state.doc.toString();
      onRun(code, lang);
    });
  }

  return editor;
}

/** Get editor by id */
export function getEditor(id) {
  return _editors.get(id) || null;
}

/** Set editor content */
export function setEditorValue(id, value) {
  const ed = _editors.get(id);
  if (!ed) return;
  ed.view.dispatch({
    changes: { from: 0, to: ed.view.state.doc.length, insert: value },
  });
}

/** Get editor content */
export function getEditorValue(id) {
  const ed = _editors.get(id);
  return ed ? ed.view.state.doc.toString() : '';
}

/** Destroy editor */
export function destroyEditor(id) {
  const ed = _editors.get(id);
  if (!ed) return;
  ed.view.destroy();
  _editors.delete(id);
}

/** Destroy all editors */
export function destroyAll() {
  for (const [id, ed] of _editors) {
    ed.view.destroy();
    _editors.delete(id);
  }
}

// ── Global command handler (maps from TokUI clk to specific editor) ──

/**
 * Register a fallback handler that dispatches to the "active" editor.
 * TokUI button DSL would use `clk:onCodeEditorSave` — we dispatch to
 * the most recently focused editor.
 */
let _activeEditorId = null;

registerHandler('onCodeEditorSave', () => {
  const ed = _activeEditorId ? _editors.get(_activeEditorId) : null;
  if (ed?.onSave) {
    const code = ed.view.state.doc.toString();
    ed.onSave(code);
  }
});

registerHandler('onCodeEditorRun', () => {
  const ed = _activeEditorId ? _editors.get(_activeEditorId) : null;
  if (ed?.onRun) {
    const code = ed.view.state.doc.toString();
    ed.onRun(code, ed.lang);
  }
});

// Track focus to determine "active" editor
document.addEventListener('focusin', (e) => {
  for (const [id, ed] of _editors) {
    if (ed.mountEl.contains(e.target)) {
      _activeEditorId = id;
      return;
    }
  }
});
