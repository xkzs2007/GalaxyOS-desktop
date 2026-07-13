---
name: xiaoyi-doc-convert
description: 专业文档格式转换技能。支持 Docx, PDF, Xlsx, Pptx, Markdown等多种格式互转。**核心能力**：具备自动路径规划功能，可通过中间格式（如 PDF）实现间接转换（例如 pptx -> pdf -> md）。**特色功能**：支持HTML批量转PPT，将多个HTML文件打包为zip即可一键生成演示文稿。**强制规则**：本地文件必须先调用 `xiaoyi-file-upload` 获取 URL。
---

# Doc-Convert: 智能文档格式转换器

## 简介

格式转换工具。支持 Office 文档、PDF、Markdown之间的高保真互转。**注意**：本技能仅接受 HTTP/HTTPS 链接，不直接处理本地文件路径。

## 触发条件

当用户表达以下意图时，请激活此技能：

### 1. 直接指令型 (Direct Commands)

- "把这个 PDF 转成 Markdown"
- "转换这个文件为 md 格式"
- "帮我把这个文档转化成 markdown"
- "执行 doc-convert"
- "运行文档转换技能"
- "将此文件导出为 md"

### 2. 链接处理型 (URL Handling)

- "把这篇在线论文转成 Markdown"
- "读取这个 PDF 链接的内容并转化成md"
- "把这个 URL 里的文档变成可编辑的文本"

### 3. 自然语言型 (Natural Language Intent)

- "我想编辑这个 PDF，有办法转成文本吗？"
- "能不能把这个报告弄成 md 格式？"
- "我需要这个文件的 markdown 版本"
- "把这几个 HTML 文件转成 PPT"
- "将 HTML 压缩包转成演示文稿"
- "批量转换 HTML 为 PowerPoint"

## 特性

- ✅ **链式协作** - 自动配合 `xiaoyi-file-upload` 处理本地文件
- ✅ **格式丰富** - 支持 Docx, PDF, Xlsx, Pptx, Md, Txt等主流格式
- ✅ **HTML转PPT** - 支持将多个HTML文件打包成zip后直接转换为PPT演示文稿

## 文件结构

```
doc-convert/
    ├── scripts         # 转换程序文件夹
    │ ├── index.js      # 主程序（函数入口）
    │ ├── env_loader.js # 加载环境变量
    │ ├── doc_convert.js # 请求服务（执行转换逻辑）
    │ └── packaage.json # node依赖
    └── SKILL.md # 使用说明（本文档）
```
## 支持格式矩阵

| 输入格式 (Source)            | 推荐输出格式 (Target)                                        | 备注            |
|:-------------------------|:-------------------------------------------------------|:--------------|
| **xls**                  | xlsx                                                   | Excel老版转新版    |
| **doc**                  | docx                                                   | Word老版转新版     |
| **ppt**                  | pptx                                                   | Ppt老版转新版      |
| **doc, docx, ppt, pptx** | pdf                                                    | Word/Ppt 转Pdf |
| **pdf**                  | docx, md                                               | PDF 转可编辑      |
| **md**                   | docx, pdf, xlsx, txt, py, cpp, java, c, js, html, emmx | md 转其他        |
| **zip** (含HTML文件)      | pptx                                                   | HTML批量转PPT     |

##  自动规划逻辑：
当用户请求 `Source -> Target` 时：
检查矩阵是否支持直达？
- ✅ **是**：执行单步转换。
- ❌ **否**：启动**多步规划引擎**。
  - 寻找中间格式 `X`，使得 `Source -> X` 且 `X -> Target` 均合法。
  - *典型场景*：`pptx` 转 `md` 无直达 ➔ 自动规划：`pptx` → `pdf` → `md`。
  - *典型场景*：`doc` 转 `md` 无直达 ➔ 自动规划：`doc` → `docx` → `pdf` → `md` (或 doc->docx->md 若支持)。


## 核心逻辑

本技能执行实际的格式转换操作。**它不直接处理本地文件上传**。
**标准工作流**：

### 第一阶段：智能规划 (Planning)
1. **解析意图**：提取 `源文件` (URL 或 本地路径) 和 `目标格式`。
2. **文件预处理**：若是本地文件，调用 `xiaoyi-file-upload` 获得 `current_url`。
3. **路径计算**：
    - 查询【支持格式矩阵】。
    - 生成执行计划列表 `Plan = [Step1, Step2, ...]`。
    - *示例计划*：`[ {from: 'pptx', to: 'pdf'}, {from: 'pdf', to: 'md'} ]`。
    - 若无法规划出合法路径，立即停止并告知用户支持的格式范围。

### 第二阶段：链式执行 (Execution Loop)
对 `Plan` 中的每一步进行循环处理：
1. **执行转换**：调用底层脚本 `node index.js <input_url> <target_format>`。
2. **获取结果**：捕获返回的新文件 URL (`output_url`)。
3. **状态更新**：将 `output_url` 设为下一步的 `input_url`。
4. **进度反馈**：(可选) 向用户通报当前进度（如：“已完成第 1/2 步：PPT 转 PDF”）。

### 第三阶段：最终交付 (Delivery)
1. **下载文件**：当所有步骤完成，使用最终 `output_url` 下载文件到本地。
2. **智能命名**：文件名格式为 `原文件名_converted.扩展名` (避免覆盖原文件)。

## 使用方法

### 前置准备
# 首次运行前，务必先在脚本所在目录执行以下命令安装依赖
```bash
cd /path/to/current/skill/scripts  # 切换到脚本目录（根据实际路径调整）
npm install                       # 安装所需依赖包（仅首次运行需要）
````

### 命令行调用
```bash
# 基本用法：node index.js <file_url> <target_format>
# 示例：将在线 PDF 转为 Word
node /path/to/current/skill/scripts/index.js "https://example.com/report.pdf" "docx"

# 示例：将在线 Markdown 转为 PDF
node /path/to/current/skill/index.js "https://example.com/notes.md" "pdf"

# 示例：将 HTML zip 包转为 PPT（zip包内需包含HTML文件）
node /path/to/current/skill/index.js "https://example.com/slides.zip" "pptx"
```

### HTML 转 PPT 特别说明

当需要将 HTML 文件转换为 PPT 时：
1. 将多个 HTML 文件打包成一个 zip 压缩包
2. zip 包中的每个 HTML 文件将被转换为一页 PPT 幻灯片
3. 上传 zip 包到 NSP 获取下载链接
4. 调用转换：`node index.js "<zip_url>" "pptx"`

### ⚠️ 重要执行协议 
必须先进行规划，再按规划路径执行脚本
