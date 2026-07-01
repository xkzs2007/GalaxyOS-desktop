# CodeMirror 6 集成测试报告

## 测试环境
- 运行环境：Headless Chromium (Puppeteer)
- 测试日期：2026-07-01
- 测试页面：test/code-editor-test.html
- 依赖来源：esm.sh CDN（codemirror@6.0.2, @codemirror/state@6.0, @codemirror/theme-one-dark@6.0）

## 测试结果

| 测试项 | 状态 | 说明 |
|--------|:----:|------|
| CodeMirror core 导入 | ✅ | `EditorView`, `minimalSetup`, `basicSetup` 3 个导出正确 |
| @codemirror/state 导入 | ✅ | `EditorState` 可用，解决 readOnly facet 依赖 |
| CodeMirror 编辑器挂载 | ✅ | `.cm-editor` DOM 元素存在，编辑器正常渲染 |
| setValue/getValue | ✅ | dispatch changes 写入成功，内容验证通过 |
| 文档长度检查 | ✅ | 初始 165 字符，更新后 "updated!" |
| 版本锁定验证 | ✅ | 6.0.2 正确（此前 6.65.7 为 CM5 legacy，已修复） |

## 发现的问题与修复

1. **esm.sh 版本解析歧义**
   - `codemirror@6` → esm.sh 解析为 `6.65.7`（CM5 遗留代码），导出为空
   - 修复：锁定 `codemirror@6.0.2`（真正的 CM6 metapackage）

2. **缺失模块依赖**
   - `EditorState.readOnly` 不在 codemirror 核心导出中
   - 修复：添加 `@codemirror/state@6.0` 导入

3. **404 资源路径**
   - `codemirror@6.0.2/dist/index.css` → 404
   - `@codemirror/legacy-modes/mode/yaml@6.0` → 404
   - 修复：CSS → `@codemirror/view@6.0/dist/index.css`；YAML → `@codemirror/lang-yaml@6.0`

4. **语言包导出确认**
   | 语言 | 导出函数 | 验证 |
   |------|----------|------|
   | python | `python` | ✅ |
   | javascript | `javascript` | ✅ |
   | json | `json` | ✅ |
   | markdown | `markdown` | ✅ |
   | html | `html` | ✅ |
   | css | `css` | ✅ |
   | sql | `sql` | ✅ |
   | xml | `xml` | ✅ |
   | yaml | `yaml` | ✅ |

## 截图
见 test/screenshot.png — 测试页面通过 Puppeteer 捕获，显示所有绿色通过状态。

## 使用说明
在 GalaxyOS Desktop 中：
- Ctrl+K 打开命令面板 → 选择「代码编辑器」
- 或命令：`galaxy.openCodeEditor()`
- 语言：python / javascript / json / markdown / html / css / sql / xml / yaml
- 保存/运行按钮通过 `onSave` / `onRun` 回调与后端交互
