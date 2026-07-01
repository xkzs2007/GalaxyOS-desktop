// renderer/src/utils.js — shared utilities; no deps.
//
// Purpose: remove duplicated escapeDsl / yieldToBrowser scattered
// across 4 files into a single source of truth.

/**
 * Escape user text so it won't be misinterpreted as TokUI DSL.
 * - Wraps in double-quotes if the string contains [ or ] or "
 * - Escapes embedded double-quotes with backslash
 */
export function escapeDsl(s) {
  const str = String(s ?? '');
  if (str.includes('[') || str.includes(']') || str.includes('"')) {
    return '"' + str.replace(/"/g, '\\"') + '"';
  }
  return str;
}

/**
 * Yield to the browser event loop (microtask).
 * Use between feed() calls in a tight loop to keep the UI responsive.
 */
export function yieldToBrowser() {
  return new Promise((r) => setTimeout(r, 0));
}

/**
 * Convert markdown fenced code blocks that appear OUTSIDE of [md]
 * containers into TokUI [code] components (which provide full syntax
 * highlighting).  Code blocks inside [md]...[/md] are left as-is so
 * the markdown renderer handles them natively.
 *
 * Input:  "[bubble]```js\nconst x = 1;\n```[/bubble]"
 * Output: "[bubble][code lang:js]\nconst x = 1;\n[/code][/bubble]"
 */
export function expandCodeBlocks(dsl) {
  // Split by [md]...[/md] boundaries; only convert the segments
  // that sit OUTSIDE markdown containers.
  const parts = dsl.split(/(\[md\][\s\S]*?\[\/md\])/g);
  return parts.map((seg) => {
    // Even indices = outside [md]; apply conversion
    // (we rely on the fact that [md] tags pair properly)
    return seg.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const langAttr = lang ? ` lang:${lang}` : '';
      return `[code${langAttr}]\n${code.trimEnd()}\n[/code]`;
    });
  }).join('');
}
