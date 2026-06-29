# GalaxyOS API 速查

> GalaxyOS v9.4 — 核心模块的公开 API 一览。适合新维护者快速了解各模块能力。
>
> 架构总览见 [`README.md`](../README.md)；harness 详细 docstring 见 `galaxyos/harness/`。

---

## 主入口

### `create_galaxy_agent` (`galaxyos/harness/factory.py`) — v9.0

**v9.0 新加**：harness 顶层入口，对标 openJiuwen `create_deep_agent()`。

```python
from galaxyos.harness import create_galaxy_agent

agent = create_galaxy_agent(
    name="assistant",
    model="lfm2.5-1.2b-instruct",   # 或 "anthropic/claude-3-5-sonnet"
    workspace_dir="~/.galaxyos/workspace",
    tools=["shell", "read_file", "write_file", "grep", "diff", "list_files"],
    memory="vector",                 # vector | liquid | mock
    skill_graph=True,
    max_iterations=20,
    temperature=0.7,
    system_prompt="你是一个专业的编程助手",
)
result = await agent.run("你好")
print(result["result"], result.get("skills_used"))
```

环境变量（未显式传时读取）：

| 变量 | 默认 |
|------|------|
| `GALAXYOS_AGENT_NAME` | `"galaxy-agent"` |
| `GALAXYOS_AGENT_MODEL` | `"lfm2.5-1.2b-instruct"` |
| `GALAXYOS_HOME` | `~/.galaxyos` |

### `SidecarHandlers` (`desktop-shell/python/galaxyos_sidecar.py`) — v9.0

桌面端 sidecar 的 RPC 处理层。30+ 方法，zmq + SSE 双传输复用。

```python
import sys; sys.path.insert(0, "desktop-shell/python")
from galaxyos_sidecar import SidecarHandlers

h = SidecarHandlers()
# 健康检查
status = h.health({})
# 多 slot LLM 配置（v9.2-v9.4）
h.set_config({
    "llm":       {"enabled": True, "provider": "anthropic", "api_key": "sk-...",
                  "model": "claude-3-5-sonnet-20241022"},
    "embedding": {"enabled": False},   # 走 BoW fallback
    "rerank":    {"enabled": False},   # 不重排
    "vlm":       {"enabled": False},   # 图片附件显示 "VLM 未配置"
})
# LLM provider 目录
catalog = h.list_providers({})["providers"]   # 11 个
```

### `MultiSlotRouter` (`desktop-shell/python/llm_providers.py`) — v9.2

**v9.2-v9.4 新加**：5 slot 独立 provider 路由。LLM 必填，其余可选。

```python
from llm_providers import MultiSlotRouter, build_llm_backend, MAINSTREAM_PROVIDERS

router = MultiSlotRouter()  # 5 slot, 全部默认 disabled
router.set_slot("llm", {"provider": "anthropic", "api_key": "sk-...",
                         "model": "claude-3-5-sonnet"})
router.set_slot("embedding", {"provider": "openai", "api_key": "sk-...",
                              "model": "text-embedding-3-small"})
# 启用 / 禁用
router.is_enabled("vlm")           # False
router.disable_slot("embedding")   # 强制回退到 BoW / mock
# 后端工厂
backend = build_llm_backend({"provider": "deepseek", "api_key": "sk-...",
                             "model": "deepseek-chat"})
# 主目录
for p in MAINSTREAM_PROVIDERS:
    print(p)  # (id, name, default_model, hint)
```

### `SidecarBackend` (`galaxyos/harness/sidecar_bridge.py`) — v9.1

**v9.1 新加**：in-process 桥接 DeepAgent ↔ SidecarHandlers。

```python
from galaxyos.harness.sidecar_bridge import (
    build_sidecar_backend, build_provider_backend, ProviderBackendWrapper,
)

# 1. 走 SidecarHandlers 完整栈（76 skills + MeMo + ACRouter）
backend = build_sidecar_backend(model="qwen-2.5")
result = await backend.chat([{"role": "user", "content": "hi"}])

# 2. 直连 provider（headless / CI 用）
direct = build_provider_backend({"provider": "anthropic", "api_key": "sk-...",
                                 "model": "claude-3-5-sonnet"})
result = await direct.chat([{"role": "user", "content": "hi"}])
```

### `TokUI DSL` (`desktop-shell/python/tokui_dsl.py`) — v9.3

**v9.3 新加**：21 builder 流式 UI DSL。

```python
from tokui_dsl import (
    bubble, md, p, progress, upd, callout, stat, code, tag, source,
    quick_reply, suggestion, latency, diff, artifact, welcome,
    tool_result, loop_progress, plan_step,
)

# 流式输出
ui.feed(bubble(role="ai", model="GalaxyOS"))
ui.feed(md("# Hello\n\n这是**加粗**文字。"))
ui.feed(progress("loading", percent=42))
ui.feed(plan_step("step-1", "检索", status="running"))
ui.feed(plan_step("step-1", "检索", status="success"))  # upd 翻转
ui.feed(callout("完成", v="success"))
ui.feed(tool_result("recall", status="done", duration=0.5))
```

### `XiaoYiClawLLM` (`services/xiaoyi_claw_api.py`) — 旧入口

> v8.6 时期的主入口。v9 起推荐用 `create_galaxy_agent()` 或 `SidecarHandlers`。
> 此 API 保留是为了向后兼容，新代码不应再使用。

```python
from services.xiaoyi_claw_api import XiaoYiClawLLM

claw = XiaoYiClawLLM(config=None)   # 自动从 config/llm_config.json 加载

# 记忆
claw.remember(content, metadata=None, vector=None, source='user') → str
claw.recall(query, top_k=10, use_crag=True) → List[Dict]
claw.forget(memory_id) → int

# 实体
claw.get_entity(entity_name) → Dict
claw.link(src, dst, relation, bidirectional=True) → bool

# 学习
claw.learn(feedback) → bool
claw.classify_knowledge(content) → Dict

# R-CCAM 认知循环
claw.process(user_input) → str

# 系统
claw.health_check() → Dict
claw.get_status() → Dict
```

### 便捷函数 (`services/claw_helpers.py`) — 旧便捷入口

```python
from services.claw_helpers import remember, recall, forget, get_entity, learn

remember("content") → str
recall("query") → List[Dict]
forget("memory_id") → int
get_entity("name") → Dict
learn({"key": "value"}) → bool
```

---

## 检索层

### `retrieval_hub` (`services/retrieval_hub.py`)

7 通道统一检索入口。

```python
from services.retrieval_hub import retrieval_hub

retrieval_hub(query, top_k=10, enable_web=False) → List[Dict]
```

### `HybridSearcher` (`services/hybrid_search.py`)

Dense + Sparse 混合检索。

```python
from services.hybrid_search import HybridSearcher, BM25Index

searcher = HybridSearcher(embedding_client=client)
searcher.add_documents([("id", "content", metadata)])
searcher.search("query", top_k=10) → List
```

### `UnifiedVectorStore` (`services/unified_vector_store.py`)

统一向量存储 (FAISS / HNSW / SQLite-vec)。

```python
from services.unified_vector_store import UnifiedVectorStore

store = UnifiedVectorStore(backend="hnswlib", dim=1024)
store.add_vectors(vectors, metadata, content)
store.search(query_vector, top_k=10) → List
store.delete(doc_id)
```

### `ANNSelector` (`services/ann_selector.py`)

动态 ANN 索引选择 (<5000 自动 HNSWFlat)。

```python
from services.ann_selector import ANNSelector

sel = ANNSelector(n_vectors=5000, dim=1024)
sel.build_index(vectors)
sel.search(query, top_k=10) → (indices, distances)
```

---

## 检索增强 (RAG)

### `CRAG` (`services/crag.py`)

纠错检索增强生成。

```python
from services.crag import CRAG

crag = CRAG()
result = crag.process("query") → CRAGResult
# result.answer, result.confidence, result.steps
```

### `CRAGPipeline` (`services/crag_pipeline.py`)

完整 RAG 管线。

```python
from services.crag_pipeline import CRAGPipeline

pipeline = CRAGPipeline(enable_query_rewrite=True, enable_self_rag=True)
result = pipeline.run("query") → CRAGResult
```

### `RAGQueryOptimizer` (`services/rag_optimizer.py`)

查询优化 (HyDE + 分解 + 扩展)。

```python
from services.rag_optimizer import RAGQueryOptimizer, QueryExpander

optimizer = RAGQueryOptimizer(use_hyde=True)
optimized = optimizer.optimize("query")

expander = QueryExpander()
expanded = expander.expand("ML")
```

---

## 记忆系统

### `ConsolidationEngine` (`services/memory_consolidation.py`)

记忆巩固 (CLS 固化 + 睡眠回放 + 干扰合并)。

```python
from services.memory_consolidation import ConsolidationEngine

engine = ConsolidationEngine(workspace_path="/path")
engine.consolidate_from_dag() → Dict
engine.replay_and_consolidate() → Dict
engine.detect_and_manage_interference("content") → Dict
```

### `BioRhythmSleepConsolidator` (`services/biorhythm_sleep_consolidation.py`)

仿生睡眠巩固 (NREM-SWR/REM/DEEP-SLEEP 5 阶段)。

```python
from services.biorhythm_sleep_consolidation import BioRhythmSleepConsolidator

sleep = BioRhythmSleepConsolidator(workspace_path="/path")
sleep.run_full_sleep_cycle() → Dict
sleep.get_dream_logs(limit=10) → List
```

### `EmotionMemoryManager` (`services/emotion_memory.py`)

情感记忆管理。

```python
from services.emotion_memory import EmotionMemoryManager

mgr = EmotionMemoryManager(workspace_path="/path")
mgr.process_message("I'm happy!") → Dict
mgr.get_emotion_stats() → Dict
```

---

## 认知与推理

### `CognitiveMap` (`services/cognitive_map.py`)

AriGraph 空间认知地图。

```python
from services.cognitive_map import CognitiveMap

cmap = CognitiveMap(dim=256)
cmap.add_anchor(node_id="n1", context="fact", session_key="s1") → str
cmap.get_nearby_anchors(vector, k=5) → List[SpatialAnchor]
cmap.spatial_similarity(v1, v2) → float
```

### `ChainOfVerificationEngine` (`services/chain_of_verification.py`)

CoVe 自验证引擎。

```python
from services.chain_of_verification import ChainOfVerificationEngine

cove = ChainOfVerificationEngine(llm_flash=client)
result = cove.verify_and_refine(answer, query, context) → VerificationResult
```

### `ThinkingEnhanced` (`services/thinking_enhanced.py`)

增强思考引擎 (Reflexion + SelfRefine + MultiPath)。

```python
from services.thinking_enhanced import (
    ReflexionEngine, SelfRefineLoop, MultiPathExplorer
)

# Reflexion 反思
ref = ReflexionEngine()
ref.record(question, answer, scores)
ref.retrieve(query, top_k=3) → List[ReflexionEntry]

# 自精炼
loop = SelfRefineLoop()
refined, history = loop.refine(question, initial_answer)

# 多路径探索
explorer = MultiPathExplorer()
paths = explorer.explore(question) → Dict
```

---

## 防幻觉

### `EnhancedHallucinationGuard` (`services/enhanced_hallucination_guard.py`)

10 重交叉验证 + 渐进式验证。

```python
from services.enhanced_hallucination_guard import EnhancedHallucinationGuard

guard = EnhancedHallucinationGuard(workspace_path="/path")
guard.determine_verification_level(confidence=0.5) → VerificationLevel
guard.verify_with_cross_validation("statement") → Dict
```

### `AdaptiveHallucinationParams` (`services/adaptive_hallucination_params.py`)

自适应防幻觉参数调优。

```python
from services.adaptive_hallucination_params import AdaptiveHallucinationParams

params = AdaptiveHallucinationParams()
params.classify_query("is this true?") → QueryType
params.get_verification_level(confidence=0.7, thresholds=t) → VerificationLevel
```

---

## 工具与基础设施

### `ConversationManager` (`services/conversation.py`)

多用户对话管理。

```python
from services.conversation import ConversationManager, Conversation

mgr = ConversationManager()
conv = mgr.create_conversation("user_id")
conv.add_message(role="user", content="hello")
conv.get_history() → List[Dict]
```

### `DAGContextManager` (`services/dag_context_manager.py`)

DAG 上下文管理器。

```python
from services.dag_context_manager import DAGContextManager, DAGNode

mgr = DAGContextManager(db_path="dag.db")
mgr.add_message(session_key="s1", role="user", content="hello") → str
mgr.add_cognition_subtree(forest_type="user", content="fact") → str
```

### `ModelRouter` (`services/model_router.py`)

模型路由 + 断路器。

```python
from services.model_router import CircuitBreaker, ComplexityClassifier

cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
cb.allow_request() → bool
cb.record_failure() / cb.record_success()

cls = ComplexityClassifier()
cls.classify("complex query") → QueryComplexity
```

### `AutoTuner` (`services/auto_tuner.py`)

参数自动调优。

```python
from services.auto_tuner import AutoTuner

tuner = AutoTuner(param_space={"top_k": [10, 20, 50]})
tuner.optimize(lambda params: benchmark(params)) → Dict
```

---

## R-CCAM 状态对象

### `PhaseState` (`services/rccam_state.py`)

五阶段状态传递（从 God Object 提取）。

```python
from services.rccam_state import PhaseState

state = PhaseState("user input")
# Retrieval → Cognition → Control → Action → Memory
state.retrieved_memories, state.analysis, state.strategy, ...
```

---

## MultiAgent 编排

### `MultiAgentOrchestrator` (`services/multi_agent_orchestrator.py`)

5 角色 + 公告板 + Judge 蒸馏 + 交叉验证。

```python
from services.multi_agent_orchestrator import MultiAgentOrchestrator

orch = MultiAgentOrchestrator(llm_flash=llm, max_workers=4)
result = orch.run(query="...", analysis={"input_class": "complex"}, tool_bag={...})
# result["answer"], result["announce_payload"], result["judge_score"]
```

---

## LFM Skill Bank

### `LfmSkillBank` (`galaxyos/engine/lfm_skill_bank.py`)

5 维评分技能库（质量/复用/合约/一致/探索）。

```python
from galaxyos.engine.lfm_skill_bank import get_skill_bank, feed_memory_to_skill_bank

bank = get_skill_bank()
bank.discover_proto_skills()    # 从 segments 聚类发现 ProtoSkill
bank.promote_proto_skills()     # 毕业为成熟 Skill
bank.run_maintenance()          # Merge/Split/Refine/Retire
bank.retrieve_skills(query_embedding=emb, top_k=5)  # 检索
```

---

## 安装向导

```bash
python scripts/install_wizard.py --check   # 系统体检
python scripts/install_wizard.py --deps    # 依赖检查
python scripts/install_wizard.py --test    # 运行测试
python scripts/install_wizard.py --all     # 全量体检
```

---

## 配置（v9.2-v9.4 多 slot）

GalaxyOS **不绑定任何远端 LLM**。`MultiSlotRouter` 管理 5 个独立 slot：

| Slot | 必填？ | 用途 | 未配置回退 |
|------|--------|------|-----------|
| `llm` | ✅ | 主对话推理 | 无（必须） |
| `llm_pro` | ❌ | 复杂任务升级 | `llm` slot |
| `embedding` | ❌ | 向量检索 | BoW 检索（`ac_router.py`） |
| `rerank` | ❌ | 检索重排 | 原始 top-k |
| `vlm` | ❌ | 图片 OCR / 多模态 | "VLM 未配置" 提示 |

**provider 目录**（`MAINSTREAM_PROVIDERS`）：

| 类别 | Provider | 默认模型 |
|------|----------|----------|
| 主流 | openai / deepseek / qwen / anthropic / google | 各自旗舰 |
| 托管 | siliconflow / openrouter | 多模型聚合 |
| 本地 | ollama / vllm | 开源 LLM |
| 自定义 | custom (OpenAI 兼容) | 用户填 |
| 离线 | mock | mock-1（脱机回声） |

**配置方式**：

- **Settings UI**：桌面端 `Settings` 模态框 4 tab（LLM/Embedding/Rerank/VLM）+ 启用复选框
- **CLI / lib**：直接调 `MultiSlotRouter.set_slot(slot, spec)`
- **环境变量**：`LLM_API_KEY` / `LLM_API_BASE` / `DEEPSEEK_API_KEY`（向后兼容）

---

## 测试运行

```bash
# 全部
python3.12 -m pytest tests/

# v9.x 新测试
python3.12 -m pytest tests/test_llm_providers.py tests/test_sidecar_set_config.py \
  tests/test_harness_sidecar_bridge.py

# 单文件
python3.12 -m pytest tests/test_rccam_state.py -v
```

---

> 更多细节见 [`README.md`](../README.md) 架构总览，以及 `galaxyos/harness/` 顶层 docstring。
