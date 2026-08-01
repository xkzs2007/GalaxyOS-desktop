# GalaxyOS API 速查

核心模块的公开 API 一览。适合新维护者快速了解各模块能力。

## 主入口

### `GalaxyOSMCPServer` (`galaxyos/kernel/mcp_server_entry.py`)

MCP Server 统一入口，启动认知内核并注册所有工具。

```python
# 启动 MCP Server
# GALAXYOS_MODE=desktop python -m galaxyos.kernel.mcp_server_entry

from galaxyos.kernel.mcp_server_entry import GalaxyOSMCPServer

server = GalaxyOSMCPServer(config=None)  # 自动从 config/llm_config.json 加载

# 记忆
server.remember(content, metadata=None, vector=None, source='user') → str
server.recall(query, top_k=10, use_crag=True) → List[Dict]
server.forget(memory_id) → int

# 实体
server.get_entity(entity_name) → Dict
server.link(src, dst, relation, bidirectional=True) → bool

# 学习
server.learn(feedback) → bool
server.classify_knowledge(content) → Dict

# R-CCAM 认知循环
server.process(user_input) → str

# 系统
server.health_check() → Dict
server.get_status() → Dict
```

---

## 检索层

### `retrieval_hub` (`galaxyos/engine/retrieval_hub.py`)

7 通道统一检索入口。

```python
from galaxyos.engine.retrieval_hub import retrieval_hub

retrieval_hub(query, top_k=10, enable_web=False) → List[Dict]
```

### `HybridSearcher` (`galaxyos/engine/hybrid_search.py`)

Dense + Sparse 混合检索。

```python
from galaxyos.engine.hybrid_search import HybridSearcher, BM25Index

searcher = HybridSearcher(embedding_client=client)
searcher.add_documents([("id", "content", metadata)])
searcher.search("query", top_k=10) → List
```

### `UnifiedVectorStore` (`galaxyos/engine/unified_vector_store.py`)

统一向量存储 (FAISS / HNSW / SQLite-vec)。

```python
from galaxyos.engine.unified_vector_store import UnifiedVectorStore

store = UnifiedVectorStore(backend="hnswlib", dim=1024)
store.add_vectors(vectors, metadata, content)
store.search(query_vector, top_k=10) → List
store.delete(doc_id)
```

### `ANNSelector` (`galaxyos/engine/ann_selector.py`)

动态 ANN 索引选择 (<5000 自动 HNSWFlat)。

```python
from galaxyos.engine.ann_selector import ANNSelector

sel = ANNSelector(n_vectors=5000, dim=1024)
sel.build_index(vectors)
sel.search(query, top_k=10) → (indices, distances)
```

---

## 检索增强 (RAG)

### `CRAG` (`galaxyos/engine/crag.py`)

纠错检索增强生成。

```python
from galaxyos.engine.crag import CRAG

crag = CRAG()
result = crag.process("query") → CRAGResult
# result.answer, result.confidence, result.steps
```

### `CRAGPipeline` (`galaxyos/engine/crag_pipeline.py`)

完整 RAG 管线。

```python
from galaxyos.engine.crag_pipeline import CRAGPipeline

pipeline = CRAGPipeline(enable_query_rewrite=True, enable_self_rag=True)
result = pipeline.run("query") → CRAGResult
```

### `RAGQueryOptimizer` (`galaxyos/engine/rag_optimizer.py`)

查询优化 (HyDE + 分解 + 扩展)。

```python
from galaxyos.engine.rag_optimizer import RAGQueryOptimizer, QueryExpander

optimizer = RAGQueryOptimizer(use_hyde=True)
optimized = optimizer.optimize("query")

expander = QueryExpander()
expanded = expander.expand("ML")
```

---

## 记忆系统

### `ConsolidationEngine` (`galaxyos/engine/memory_consolidation.py`)

记忆巩固 (CLS 固化 + 睡眠回放 + 干扰合并)。

```python
from galaxyos.engine.memory_consolidation import ConsolidationEngine

engine = ConsolidationEngine(workspace_path="/path")
engine.consolidate_from_dag() → Dict
engine.replay_and_consolidate() → Dict
engine.detect_and_manage_interference("content") → Dict
```

### `BioRhythmSleepConsolidator` (`galaxyos/engine/biorhythm_sleep_consolidation.py`)

仿生睡眠巩固 (NREM-SWR/REM/DEEP-SLEEP 5 阶段)。

```python
from galaxyos.engine.biorhythm_sleep_consolidation import BioRhythmSleepConsolidator

sleep = BioRhythmSleepConsolidator(workspace_path="/path")
sleep.run_full_sleep_cycle() → Dict
sleep.get_dream_logs(limit=10) → List
```

### `EmotionMemoryManager` (`galaxyos/engine/emotion_memory.py`)

情感记忆管理。

```python
from galaxyos.engine.emotion_memory import EmotionMemoryManager

mgr = EmotionMemoryManager(workspace_path="/path")
mgr.process_message("I'm happy!") → Dict
mgr.get_emotion_stats() → Dict
```

---

## 认知与推理

### `CognitiveMap` (`galaxyos/engine/cognitive_map.py`)

AriGraph 空间认知地图。

```python
from galaxyos.engine.cognitive_map import CognitiveMap

cmap = CognitiveMap(dim=256)
cmap.add_anchor(node_id="n1", context="fact", session_key="s1") → str
cmap.get_nearby_anchors(vector, k=5) → List[SpatialAnchor]
cmap.spatial_similarity(v1, v2) → float
```

### `ChainOfVerificationEngine` (`galaxyos/engine/chain_of_verification.py`)

CoVe 自验证引擎。

```python
from galaxyos.engine.chain_of_verification import ChainOfVerificationEngine

cove = ChainOfVerificationEngine(llm_flash=client)
result = cove.verify_and_refine(answer, query, context) → VerificationResult
```

### `ThinkingEnhanced` (`galaxyos/engine/thinking_enhanced.py`)

增强思考引擎 (Reflexion + SelfRefine + MultiPath)。

```python
from galaxyos.engine.thinking_enhanced import (
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

### `EnhancedHallucinationGuard` (`galaxyos/engine/enhanced_hallucination_guard.py`)

10 重交叉验证 + 渐进式验证。

```python
from galaxyos.engine.enhanced_hallucination_guard import EnhancedHallucinationGuard

guard = EnhancedHallucinationGuard(workspace_path="/path")
guard.determine_verification_level(confidence=0.5) → VerificationLevel
guard.verify_with_cross_validation("statement") → Dict
```

### `AdaptiveHallucinationParams` (`galaxyos/engine/adaptive_hallucination_params.py`)

自适应防幻觉参数调优。

```python
from galaxyos.engine.adaptive_hallucination_params import AdaptiveHallucinationParams

params = AdaptiveHallucinationParams()
params.classify_query("is this true?") → QueryType
params.get_verification_level(confidence=0.7, thresholds=t) → VerificationLevel
```

---

## 工具与基础设施

### `ConversationManager` (`galaxyos/engine/conversation.py`)

多用户对话管理。

```python
from galaxyos.engine.conversation import ConversationManager, Conversation

mgr = ConversationManager()
conv = mgr.create_conversation("user_id")
conv.add_message(role="user", content="hello")
conv.get_history() → List[Dict]
```

### `DAGContextManager` (`galaxyos/engine/dag_context_manager.py`)

DAG 上下文管理器。

```python
from galaxyos.engine.dag_context_manager import DAGContextManager, DAGNode

mgr = DAGContextManager(db_path="dag.db")
mgr.add_message(session_key="s1", role="user", content="hello") → str
mgr.add_cognition_subtree(forest_type="user", content="fact") → str
```

### `ModelRouter` (`galaxyos/engine/model_router.py`)

模型路由 + 断路器。

```python
from galaxyos.engine.model_router import CircuitBreaker, ComplexityClassifier

cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
cb.allow_request() → bool
cb.record_failure() / cb.record_success()

cls = ComplexityClassifier()
cls.classify("complex query") → QueryComplexity
```

### `AutoTuner` (`galaxyos/engine/auto_tuner.py`)

参数自动调优。

```python
from galaxyos.engine.auto_tuner import AutoTuner

tuner = AutoTuner(param_space={"top_k": [10, 20, 50]})
tuner.optimize(lambda params: benchmark(params)) → Dict
```

---

## R-CCAM 状态对象

### `PhaseState` (`galaxyos/engine/rccam_state.py`)

五阶段状态传递（从 God Object 提取）。

```python
from galaxyos.engine.rccam_state import PhaseState

state = PhaseState("user input")
# Retrieval → Cognition → Control → Action → Memory
state.retrieved_memories, state.analysis, state.strategy, ...
```

---

## 安装与部署

模型下载和依赖安装通过 CI 工作流自动完成：

```bash

# ONNX 模型（CI 从 onnx-community/bge-small-zh-v1.5-ONNX 下载）
# 文件：model.onnx + model.onnx_data + tokenizer.json

# 核心依赖
pip install -r requirements-core.txt
pip install "openjiuwen @ git+https://github.com/openJiuwen-ai/agent-core@v0.1.16"
```

---

## 配置

`config/llm_config.json` 管理所有外部 API：

| 字段 | 用途 | 提供商 |
|------|------|--------|
| `llm` | 对话推理 | DeepSeek v4-flash |
| `llm_pro` | 批量处理 | DeepSeek v4-pro |
| `embedding` | 文本向量 (bge-m3) | 硅基流动 |
| `rerank` | 结果重排 (bge-reranker-v2-m3) | 硅基流动 |
| `vlm` | 图像理解 | 硅基流动 |

模板: `config/llm_config.example.json`

---

## 安全与隔离

### openJiuwen Rails + PermissionEngine

基于 openJiuwen agent-core 的安全模型，提供多层防护：

```python
from openjiuwen.security import PermissionEngine, RailGuard

# PermissionEngine：工具级权限控制
engine = PermissionEngine(policy="config/permissions.json")
engine.check_permission(tool_name="memory_search", context=session) → bool
engine.grant(role="user", tools=["memory_search", "recall"]) → None

# RailGuard：输入/输出护栏
rail = RailGuard(rules=["no_injection", "no_data_exfiltration"])
rail.scan_input(user_input) → ScanResult
# result.safe / result.score / result.risk_level (safe/low/medium/high)
rail.scan_output(agent_output) → ScanResult
# 高风险 → 阻断；中风险 → 审核队列；低风险 → 放行
```

### `ReviewQueue` / `ProvenanceStore`

```python
from openjiuwen.security import get_review_queue, get_provenance_store

rq = get_review_queue()
rq.list_pending()        # 待审核技能列表
rq.size()                # 队列大小

ps = get_provenance_store()
ps.record(name, info)    # 记录技能来源
ps.find_contaminated(min_score=0.5)  # 查找污染技能（回滚用）
```

---

## MultiAgent 编排

### openJiuwen Agent-Core 编排

基于 openJiuwen agent-core 的多智能体编排，支持子代理派生和会话隔离：

```python
from openjiuwen.orchestration import AgentOrchestrator

orch = AgentOrchestrator(llm_flash=llm, max_workers=4)

# 常规编排
result = orch.run(query="...", analysis={"input_class": "complex"}, tool_bag={...})

# Sub-Agent 模式（遵循 session key 格式/受限工具集/announce 回传）
result = orch.spawn_as_sub_agent(
    query="...",
    parent_agent_id="agent-1",
    parent_session_key="ws:dm:user1",
    analysis={"input_class": "complex"},
    tool_bag={"web_search": search_fn},
)
# result["session_key"] → "agent:agent-1:subagent:xxxx"
# result["announce_payload"] → 回传主会话的 payload
```

---

## ACP 调试端点

### `ACPServer` (`galaxyos/privileged/acp_server.py`)

```python
from galaxyos.privileged.acp_server import ACPServer

server = ACPServer()
# 原有 5 工具: memory_search / memory_add / query_rewrite / rrf_fusion / embedding_encode
# 调试端点:
#   debug_dag_visualize  → DAG 节点+边列表（编辑器可视化）
#   debug_engram_inspect → engram 记忆检查
#   debug_skill_bank_status → Skill Bank 状态 + 审核队列 + 来源追溯
```

---

> 更多细节见 `SKILL.md` 架构文档和 `README.md`。
