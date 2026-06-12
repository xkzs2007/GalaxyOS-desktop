---
name: handoff
argument-hint: "下一个会话要用来做什么？"
description: 会话交接。将当前对话压缩为交接文档，供后续会话或其他 AI 继续。触发词：交接、handoff、会话交接、转交。
tags: [效率, 交接, 会话]
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save it to a path produced by `mktemp -t handoff-XXXXXX.md` (read the file before you write to it).

Suggest the skills to be used, if any, by the next session.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.