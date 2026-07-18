# ADR-012: 彻底移除 JiuwenSwarm

## 状态

已采纳

## 背景

GalaxyOS 原本使用 JiuwenSwarm（AgentServer:18092 + Gateway:19000）作为 Agent 集群运行时。项目已迁移到双进程架构（MCP Server + AgentCore Bridge），仅保留 openJiuwen agent-core 作为进程内调用。JiuwenSwarm 的所有功能已被替代：

- AgentServer → AgentCore Bridge（进程内调用）
- Gateway WebSocket → MCP Server SSE `/agent-chat` 端点
- JiuwenSwarm 前端 → TokUI React + EUI-NEO C++ DSL

## 决策

从代码库、CI/CD 工作流、Docker 镜像、安装向导中彻底移除 JiuwenSwarm 相关内容。

## 替代方案

1. **保留 JiuwenSwarm 作为可选后端** → 增加维护成本，与双进程架构冲突
2. **渐进式移除** → 延长迁移期，代码中残留更多历史包袱

## 理由

- 双进程架构已完整替代三进程架构
- SSE 端点比 WebSocket 网关更简单可靠
- 移除后构建时间减少约 40%（无需克隆/构建 JiuwenSwarm 前端）
- 依赖减少：移除 `pip install jiuwenswarm`、`npm install`、Node.js 等

## 后果

- 不再支持 JiuwenSwarm 集群模式
- Gateway WebSocket 通信不再可用
- `Dockerfile.gateway` 已删除
- 安装向导 `--ci` 模式跳过 OpenClaw/JiuwenSwarm 检查