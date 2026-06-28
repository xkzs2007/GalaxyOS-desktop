# 🌌 GalaxyOS — OpenClaw 认知增强引擎

> 为 AI Assistant 提供记忆、检索、推理、验证、自进化的全套认知能力
>
> **v8.6.0** · OpenClaw 深度集成改造（全 4 阶段落地）

---

## 总览

`GalaxyOS` 是 **OpenClaw 的底层认知增强引擎**。同时占据两个核心插槽：

| 插槽 | 注册 ID | 接管能力 |
|------|---------|----------|
| `contextEngine` | `claw-core-engine` | 上下文组装 / 压缩 / 摄入（ownsCompaction=true） |
| `memory` | `galaxyos` | 记忆检索 / 写入 / flushPlan / publicArtifacts |

## 核心能力

| 能力 | 说明 |
|------|------|
| **液态神经记忆** | LTC 突触 + CfC 推理 + NCP 神经电路 + 仿生遗忘曲线 |
| **DAG 上下文** | SQLite 持久化 + 摘要节点回溯 + 时间衰减排序 |
| **COSPLAY 自演化** | 从执行轨迹学习技能合约 → ProtoSkill → 成熟 Skill |
| **LFM 技能库** | 5 维评分（质量/复用/合约/一致/探索）+ 合并·拆分·精修·淘汰 |
| **R-CCAM 认知循环** | Retrieval→Cognition→Control→Action→Memory 五阶段 |
| **MultiAgent 协同** | 5 角色 + 公告板 + Judge 蒸馏 + 交叉验证 |
| **OpenClaw 深度集成** | 9 钩子 + 15 工具（含 policy）+ 跨平台 Rust |

## 快速开始

```bash
# 1. 克隆 + 装依赖
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git
cd GalaxyOS
pip install -r requirements.txt
cd extensions/galaxyos && pnpm install && cd ../..

# 2. ⚠️ 必须：编辑 ~/.openclaw/openclaw.json 指定插槽
# 见下方"必需配置"

# 3. 重启 Gateway
supervisorctl restart openclaw-gateway

# 4. 验证
python3.12 -m galaxyos.scripts.install_wizard --check
openclaw doctor
```

## 必需配置

⚠️ **不配 = 不生效**。OpenClaw 的插槽是运行时独占的，GalaxyOS 装了不等于被选中：

```json
{
  "plugins": {
    "slots": {
      "contextEngine": "claw-core-engine",
      "memory": "galaxyos"
    },
    "entries": {
      "claw-core-engine": { "enabled": true },
      "galaxyos": { "enabled": true }
    }
  }
}
```

默认走 `legacy` + `memory-core`，所有 GalaxyOS 能力不生效。

详见 [OpenClaw Context Engine 文档](https://docs.openclaw.ai/concepts/context-engine) 和 [Memory 文档](https://docs.openclaw.ai/concepts/memory)。

## 9 个生命周期钩子

| 钩子 | 触发 | 用途 |
|------|------|------|
| `gateway_start` | Gateway 启动 | 注册 lane type + heartbeat + cron |
| `gateway_stop` | Gateway 停止 | 统一关闭所有组件 |
| `before_tool_call` | 工具调用前 | 记录调用前状态给 BoundaryDetector |
| `after_tool_call` | 工具调用后 | 幂等捕获结果，更新 Skill Bank + engram + DAG |
| `before_compaction` | 压缩前 | 高价值上下文持久化到 engram + DAG |
| `after_compaction` | 压缩后 | 向量索引同步 |
| `before_agent_reply` | Agent 回复前 | 异步触发 R-CCAM 认知循环 |
| `agent_end` | Agent 回复后 | L0 日志 + 关键词追踪 + 持久化记忆 |
| `before_prompt_build` | 提示构建前 | R-CCAM 注入 + 动态锚定 + 记忆验证 |

## 15 个工具

所有工具都声明 `policy` 字段（channels / roles / rateLimit）：

| 工具 | 用途 | Rate Limit |
|------|------|-----------|
| `galaxy_pool` | GalaxyPool 状态查询 | 30/min |
| `claw_rccam_progress` | R-CCAM 实时进度 | 60/min |
| `claw_recall` | 深度语义记忆检索 | 60/min |
| `claw_lobster` | Lobster 管道执行 | 20/min |
| `claw_health` | 系统健康检查 | 30/min |
| `claw_vector_info` | 向量计算能力 | 30/min |
| `claw_events` | 事件日志查询 | 60/min |
| `claw_store` | 记忆存储 | 30/min |
| `claw_verify` | 幻觉验证 | 30/min |
| `claw_rccam` | R-CCAM 认知循环 | 20/min |
| `claw_save_memory` | 记忆持久化 | 30/min |
| `claw_compile_skill` | Skill 编译（SkVM） | 10/min |
| `claw_asset_search` | KnowledgeAsset 搜索 | 60/min |
| `claw_asset_register` | KnowledgeAsset 注册 | 20/min |
| `claw_node_invoke` | Node 外设调用 | 10/min |

## 架构

```
┌────────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                         │
│  (12+ 消息平台 / 7 层纵深防御 / Lane Queue)                  │
└────────────────────────────────────────────────────────────┘
                             │
   ┌─────────────────────────┼─────────────────────────┐
   ▼                         ▼                         ▼
┌────────────┐      ┌──────────────┐         ┌──────────────┐
│contextEngine│     │  memory slot │         │   hooks      │
│claw-core-eng│     │   galaxyos   │         │ 9 lifecycle  │
└──────┬─────┘      └──────┬───────┘         └──────┬───────┘
       └────────────────────┼───────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────┐
│              GalaxyOS v8.6.0 — 8 大子系统                    │
├────────────────────────────────────────────────────────────┤
│  1. 液态神经核心 (LTC/CfC/NCP/SSM)                          │
│  2. DAG 上下文 (SQLite + 摘要回溯 + 时间衰减)                │
│  3. COSPLAY 适配 (边界检测 + 合约学习 + 毕业)                │
│  4. LFM 技能库 (ProtoSkill → Skill + 5 维评分)              │
│  5. R-CCAM 认知循环 (5 阶段 + 元认知调节)                    │
│  6. MultiAgent 编排 (5 角色 + 公告板 + 蒸馏)                 │
│  7. 防幻觉 10 重检测 (Self-RAG/CRAG/CoVe)                    │
│  8. Rust 跨平台 (Linux/Windows × x64/ARM64)                  │
└────────────────────────────────────────────────────────────┘
```

## 安全模型

4 层防护：

1. **工具策略** — 14 工具全部声明 channels/roles/rateLimit
2. **Skill Bank 合约扫描** — `injection_scanner.py` 3 级检测（高/中/低风险）
3. **Channel 感知** — 群聊场景记忆写入降级为只读
4. **结构化 Session Key** — `workspace:channel:userId` 隔离

## 跨平台 Rust 扩展

`lfm_server.rs` 条件编译：
- **Linux/macOS**：UDS (Unix Domain Socket)
- **Windows**：TCP localhost（自动分配端口）

4 目标交叉编译：
```bash
make native-cross                  # 安装 target
make native-build-linux-x64        # Linux x86_64
make native-build-linux-arm64      # Linux ARM64
make native-build-win-x64          # Windows x86_64
make native-build-win-arm64        # Windows ARM64
make native-package                # 打包 4 平台 tar.gz
make native-install-prebuilt       # 自动检测平台安装
```

## 目录结构

```
GalaxyOS/
├── extensions/galaxyos/
│   ├── index.js                 # 主插件（~5200 行）— 9 钩子 / 15 工具 / 2 插槽
│   ├── openclaw.plugin.json     # 插件契约
│   ├── clawhub.json             # ClawHub 发布清单（9 技能 ≤97 字符）
│   ├── scripts/                 # Python 脚本
│   │   ├── injection_scanner.py # Skill Bank 内容扫描器
│   │   ├── lfm_skill_bank.py    # LFM 技能库
│   │   ├── multi_agent_orchestrator.py
│   │   ├── dag_context_manager.py
│   │   └── ...
│   └── native/                  # Rust 跨平台原生扩展
├── galaxyos/                    # 统一 Python 包
│   ├── engine/
│   ├── privileged/
│   │   └── acp_server.py        # 含 3 个调试端点
│   └── ...
├── skills/                      # 技能库（60+ 个）
├── libs/                        # 预编译包
├── tests/                       # 137 测试用例
├── requirements.txt
├── pyproject.toml
├── Makefile                     # 含跨平台编译目标
├── SKILL.md
├── CHANGELOG.md
└── VERSION                      # 8.6.0
```

## 版本历史

### v8.6.0 (2026-06-28) — OpenClaw 深度集成改造

**Phase 1 核心断链修复**：5 个新钩子 + 结构化 session key + 幂等层 + lane 声明
**Phase 2 安全与隔离加固**：14 工具 policy + 内容扫描器 + 群聊只读降级
**Phase 3 系统对齐**：SKILL.md 输出 + Heartbeat/Cron + Sub-Agent + ACP 调试端点
**Phase 4 生态融合**：Node 系统集成 + ClawHub 发布 + progressive disclosure

**Rust 跨平台**：Unix/Windows 条件编译 + 4 目标交叉编译 + 预编译包打包

**安装向导增强**：检查 slots 配置 + 给出完整 openclaw.json 示例

### v8.5.3 (2026-06-25) — MultiAgent P1+P2 全量

### v8.4.2 (2026-06-23) — enhanced_recall 8 阶段 + SkillGraph

## 生态

- **[OpenClaw](https://github.com/openclaw/openclaw)** — AI Assistant 框架
- **[ClawHub](https://cnb.cool)** — 技能包市场

## 开发

| 资源 | 说明 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| `make test` | 运行测试 |
| `make native` | 编译 Rust 扩展 |
| `python3.12 -m galaxyos.scripts.install_wizard --check` | 自检 |

## 许可证

MIT License — 详见 [LICENSE](LICENSE)
