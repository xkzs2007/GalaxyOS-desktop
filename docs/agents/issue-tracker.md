# Issue 追踪器：GitHub

本仓库的 Issue 和 PRD 存放在 GitHub Issues 中。所有操作使用 `gh` CLI。

## 约定

- **创建 Issue**：`gh issue create --title "..." --body "..."`。多行正文使用 heredoc。
- **读取 Issue**：`gh issue view <number> --comments`，通过 `jq` 过滤评论并获取标签。
- **列出 Issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，配合 `--label` 和 `--state` 过滤。
- **评论**：`gh issue comment <number> --body "..."`
- **添加/移除标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

从 `git remote -v` 推断仓库——在克隆目录内运行 `gh` 时会自动识别。

## Pull Request 作为分诊入口

**PR 作为请求入口：否。** _(如需将外部 PR 视为功能请求，可改为 yes；`/triage` 会读取此标志。)_

设为 `yes` 时，PR 使用与 Issue 相同的标签和状态流转，对应 `gh pr` 命令：

- **读取 PR**：`gh pr view <number> --comments`，`gh pr diff <number>` 获取差异。
- **列出待分诊的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，仅保留 `authorAssociation` 为 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR` 或 `NONE` 的 PR（排除 `OWNER`/`MEMBER`/`COLLABORATOR`）。
- **评论/标签/关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 Issue 和 PR 共享编号空间，裸 `#42` 可能是任一类型——先用 `gh pr view 42` 尝试，回退到 `gh issue view 42`。

## 当 skill 要求"发布到 Issue 追踪器"

创建 GitHub Issue。

## 当 skill 要求"获取相关工单"

运行 `gh issue view <number> --comments`。

## 寻路操作

供 `/wayfinder` 使用。**地图**是一个带有**子 Issue** 作为工单的单一 Issue。

- **地图**：标记为 `wayfinder:map` 的单一 Issue，包含 Notes / Decisions-so-far / Fog 正文。`gh issue create --label wayfinder:map`。
- **子工单**：通过 GitHub sub-issue 关联到地图的 Issue（`gh api` 调用 sub-issues 端点）。若 sub-issues 不可用，在地图正文中以任务列表添加子项，并在子项正文顶部写 `Part of #<map>`。标签：`wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。认领后分配给驱动开发者。
- **阻塞关系**：GitHub **原生 Issue 依赖**——规范的、UI 可见的表示。通过 `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` 添加边，其中 `<blocker-db-id>` 是阻塞项的数字 **数据库 id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，而非 `#number` 或 `node_id`）。GitHub 通过 `issue_dependencies_summary.blocked_by` 报告（仅开放阻塞项——实时门控）。若依赖不可用，回退到子项正文顶部的 `Blocked by: #<n>, #<n>` 行。当所有阻塞项关闭时，工单解除阻塞。
- **前沿查询**：列出地图的开放子项（`gh issue list --state open`，限定为地图的 sub-issues / 任务列表），排除有开放阻塞项（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 行中有开放 Issue）或已有指派人的项；按地图顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`——会话首次写入。
- **解决**：`gh issue comment <n> --body "<answer>"`，然后 `gh issue close <n>`，最后在地图的 Decisions-so-far 中追加上下文指针（gist + 链接）。