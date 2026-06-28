# GalaxyOS API 速查

核心模块的公开 API 一览。适合新维护者快速了解各模块能力。

## 主入口

### `XiaoYiClawLLM` (`services/xiaoyi_claw_api.py`)

统一 API 入口，整合所有底层能力。

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

### 便捷函数 (`services/claw_helpers.py`)

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

## 安装向导

```bash
python scripts/install_wizard.py --check   # 系统体检
python scripts/install_wizard.py --deps    # 依赖检查
python scripts/install_wizard.py --test    # 运行测试
python scripts/install_wizard.py --all     # 全量体检
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

## 安全与隔离（v8.6.0 新增）

### `InjectionScanner` (`extensions/galaxyos/scripts/injection_scanner.py`)

Skill Bank 合约内容扫描器，检测 prompt injection 特征。

```python
from injection_scanner import get_scanner, scan_before_graduate

scanner = get_scanner()
result = scanner.scan(contract_text)
# result.risky / result.score / result.risk_level (safe/low/medium/high)

# 毕业前扫描（集成在 LfmSkillBank.promote_proto_skills 中）
result = scan_before_graduate(proto_skill, contract)
# 高风险 → 隔离不毕业；中风险 → 审核队列；低风险 → 放行
```

### `ReviewQueue` / `ProvenanceStore`

```python
from injection_scanner import get_review_queue, get_provenance_store

rq = get_review_queue()
rq.list_pending()        # 待审核技能列表
rq.size()                # 队列大小

ps = get_provenance_store()
ps.record(name, info)    # 记录技能来源
ps.find_contaminated(min_score=0.5)  # 查找污染技能（回滚用）
```

---

## MultiAgent 编排（v8.5.3 + v8.6.0 增强）

### `MultiAgentOrchestrator` (`extensions/galaxyos/scripts/multi_agent_orchestrator.py`)

```python
from multi_agent_orchestrator import MultiAgentOrchestrator

orch = MultiAgentOrchestrator(llm_flash=llm, max_workers=4)

# 常规编排
result = orch.run(query="...", analysis={"input_class": "complex"}, tool_bag={...})

# v8.6.0: OpenClaw Sub-Agent 模式（遵循 session key 格式/受限工具集/announce 回传）
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

## LFM Skill Bank（v8.5.2 + v8.6.0 增强）

### `LfmSkillBank` (`extensions/galaxyos/scripts/lfm_skill_bank.py`)

```python
from lfm_skill_bank import get_skill_bank, feed_memory_to_skill_bank

bank = get_skill_bank()
bank.discover_proto_skills()    # 从 segments 聚类发现 ProtoSkill
bank.promote_proto_skills()     # 毕业（含 injection_scanner 扫描 + SKILL.md 输出）
bank.run_maintenance()          # Merge/Split/Refine/Retire
bank.retrieve_skills(query_embedding=emb, top_k=5)  # 检索

# v8.6.0: 毕业后自动输出 SKILL.md 到 workspace/skills/
# bank._export_skill_md(skill, proto_skill) → path
```

---

## ACP 调试端点（v8.6.0 新增）

### `ACPServer` (`galaxyos/privileged/acp_server.py`)

```python
from acp_server import ACPServer

server = ACPServer()
# 原有 5 工具: memory_search / memory_add / query_rewrite / rrf_fusion / embedding_encode
# v8.6.0 新增 3 个调试端点:
#   debug_dag_visualize  → DAG 节点+边列表（编辑器可视化）
#   debug_engram_inspect → engram 记忆检查
#   debug_skill_bank_status → Skill Bank 状态 + 审核队列 + 来源追溯
```

---

## 跨平台 Rust 扩展（v8.6.0 新增）

### `lfm_server` (`extensions/galaxyos/native/src/bin/lfm_server.rs`)

条件编译跨平台 IPC：
- **Unix**（Linux/macOS）: UDS (`UnixListener`)
- **Windows**: TCP localhost (`TcpListener`, 自动分配端口)

```bash
# 4 目标交叉编译
make native-build-linux-x64
make native-build-linux-arm64
make native-build-win-x64
make native-build-win-arm64

# 打包 + 安装
make native-package
make native-install-prebuilt   # 自动检测平台
```

---

> 更多细节见 `SKILL.md` 架构文档和 `README.md`。
