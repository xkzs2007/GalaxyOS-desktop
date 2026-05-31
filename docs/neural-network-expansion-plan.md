# 神经网络算法扩展计划

创建时间：2026-04-21
目标：将 5 篇核心论文的思想全部实现到系统中

---

## 阶段规划

### Phase 1: MemGPT 风格记忆管理（1 周）

**目标**：实现两级内存架构 + 自主内存管理

**模块**：
1. `memgpt_memory.py` - MemGPT 风格记忆管理器
2. `memory_functions.py` - 内存管理函数（append/replace/insert/search）
3. `context_compressor.py` - 上下文压缩器
4. `memory_bank.py` - 记忆银行（无限容量存储）

**核心功能**：
- Core Memory：始终在上下文中的关键信息
- Working Memory：当前对话上下文
- Archival Memory：历史记忆（向量检索）
- 自主内存管理：LLM 自己决定读写

**验收标准**：
- [ ] Core Memory 自动维护
- [ ] Working Memory 自动压缩
- [ ] Archival Memory 向量检索
- [ ] 内存管理函数可被 LLM 调用

---

### Phase 2: Generative Agents 反思机制（1 周）

**目标**：实现记忆流 + 反思 + 规划

**模块**：
1. `memory_stream.py` - 记忆流（带重要性评分）
2. `retrieval_formula.py` - 检索公式（时效性 × 相关性 × 重要性）
3. `reflection_engine.py` - 反思引擎
4. `planning_engine.py` - 规划引擎

**核心功能**：
- 记忆流：每条记忆带重要性评分
- 检索公式：recency + relevance + importance
- 反思：定期从记忆中提取洞察
- 规划：基于记忆生成行动计划

**验收标准**：
- [ ] 记忆流带重要性评分
- [ ] 检索公式实现
- [ ] 反思机制自动触发
- [ ] 规划机制生成行动计划

---

### Phase 3: Self-RAG 检索决策（3 天）

**目标**：实现检索必要性判断 + 质量评估

**模块**：
1. `isrel_predictor.py` - 检索必要性预测器
2. `issup_predictor.py` - 检索支持度预测器
3. `isuse_predictor.py` - 生成可靠性预测器
4. `self_rag.py` - Self-RAG 主控制器

**核心功能**：
- IsREL：判断是否需要检索
- IsSUP：判断检索结果是否相关
- IsUSE：判断生成内容是否可靠
- 迭代优化：不可靠时重新生成

**验收标准**：
- [ ] IsREL 预测准确率 > 80%
- [ ] IsSUP 预测准确率 > 80%
- [ ] IsUSE 预测准确率 > 80%
- [ ] 迭代优化减少幻觉

---

### Phase 4: CRAG 纠错检索（3 天）

**目标**：实现检索评估 + 知识精炼 + 知识补充

**模块**：
1. `retrieval_evaluator.py` - 检索评估器
2. `knowledge_refiner.py` - 知识精炼器
3. `knowledge_augmentor.py` - 知识补充器
4. `crag.py` - CRAG 主控制器

**核心功能**：
- 检索评估：分析检索结果质量
- 知识精炼：提取关键片段
- 知识补充：Web 搜索补充
- 多策略选择：根据评估结果选择策略

**验收标准**：
- [ ] 检索评估准确
- [ ] 知识精炼有效
- [ ] Web 搜索集成
- [ ] 多策略自动切换

---

### Phase 5: GraphSAGE/GAT 知识图谱推理（1 周）

**目标**：实现图神经网络增强的知识图谱

**模块**：
1. `graph_constructor.py` - 图构建器
2. `graphsage_layer.py` - GraphSAGE 层
3. `gat_layer.py` - GAT 层
4. `knowledge_graph_gnn.py` - GNN 知识图谱
5. `relation_predictor.py` - 关系预测器

**核心功能**：
- 图构建：从记忆中构建知识图谱
- GraphSAGE：邻居聚合
- GAT：注意力聚合
- 关系预测：预测隐含关系
- 实体检索：基于 GNN 的实体检索

**验收标准**：
- [ ] 知识图谱自动构建
- [ ] GraphSAGE 聚合实现
- [ ] GAT 注意力实现
- [ ] 关系预测准确率 > 70%

---

## 集成计划

### 统一入口

```python
class NeuralMemorySystem:
    """神经网络记忆系统统一入口"""
    
    def __init__(self):
        # Phase 1: MemGPT
        self.memgpt = MemGPTMemory()
        
        # Phase 2: Generative Agents
        self.memory_stream = MemoryStream()
        self.reflection_engine = ReflectionEngine()
        self.planning_engine = PlanningEngine()
        
        # Phase 3: Self-RAG
        self.self_rag = SelfRAG()
        
        # Phase 4: CRAG
        self.crag = CRAG()
        
        # Phase 5: GNN
        self.knowledge_graph = KnowledgeGraphGNN()
    
    def remember(self, content: str, importance: float = None):
        """记忆存储"""
        # 1. 添加到记忆流
        self.memory_stream.add(content, importance)
        
        # 2. MemGPT 管理
        self.memgpt.add_to_archival(content)
        
        # 3. 更新知识图谱
        self.knowledge_graph.add_from_text(content)
    
    def recall(self, query: str) -> List[str]:
        """记忆检索"""
        # 1. Self-RAG 判断是否需要检索
        if not self.self_rag.need_retrieval(query):
            return []
        
        # 2. CRAG 检索
        docs = self.crag.retrieve(query)
        
        # 3. 记忆流检索
        memories = self.memory_stream.retrieve(query)
        
        # 4. 知识图谱检索
        entities = self.knowledge_graph.query(query)
        
        # 5. 融合结果
        return self._merge_results(docs, memories, entities)
    
    def reflect(self):
        """反思"""
        # 1. 获取最近记忆
        recent = self.memory_stream.get_recent(100)
        
        # 2. 生成反思
        insights = self.reflection_engine.reflect(recent)
        
        # 3. 存储反思结果
        for insight in insights:
            self.remember(f"[反思] {insight}", importance=0.9)
    
    def plan(self) -> List[Action]:
        """规划"""
        # 1. 获取相关记忆
        memories = self.memory_stream.retrieve("今天的计划")
        
        # 2. 生成计划
        return self.planning_engine.plan(memories)
```

---

## 时间线

| 阶段 | 开始日期 | 结束日期 | 状态 |
|------|---------|---------|------|
| Phase 1: MemGPT | 2026-04-22 | 2026-04-28 | 待开始 |
| Phase 2: Generative Agents | 2026-04-29 | 2026-05-05 | 待开始 |
| Phase 3: Self-RAG | 2026-05-06 | 2026-05-08 | 待开始 |
| Phase 4: CRAG | 2026-05-09 | 2026-05-11 | 待开始 |
| Phase 5: GraphSAGE/GAT | 2026-05-12 | 2026-05-18 | 待开始 |

**总计**：约 4 周

---

## 依赖关系

```
Phase 1 (MemGPT)
    │
    ├──→ Phase 2 (Generative Agents) ──→ Phase 5 (GNN)
    │         │
    │         └──→ Phase 3 (Self-RAG)
    │                   │
    │                   └──→ Phase 4 (CRAG)
    │
    └──→ Phase 5 (GNN) 可以并行
```

**关键路径**：Phase 1 → Phase 2 → Phase 3 → Phase 4

---

## 资源需求

### 计算资源
- 向量计算：已有 Qwen3-Embedding-8B
- 图神经网络：需要 PyTorch Geometric
- LLM：已有小艺 GLM5

### 存储资源
- 记忆流：SQLite + 向量库
- 知识图谱：Neo4j 或 SQLite
- 模型缓存：约 2GB

### 开发资源
- Python 3.12
- PyTorch 2.x
- PyTorch Geometric
- NumPy, Pandas

---

## 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM 调用延迟 | 高 | 缓存 + 异步 |
| 向量计算开销 | 中 | 批处理 + GPU |
| 知识图谱构建复杂 | 中 | 渐进式构建 |
| GNN 训练数据不足 | 中 | 预训练 + 微调 |

---

## 下一步

1. 确认计划可行性
2. 开始 Phase 1 实现
3. 每个阶段完成后验收
4. 最终集成测试
