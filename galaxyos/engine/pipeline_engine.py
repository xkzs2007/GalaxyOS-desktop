"""
PipelineEngine — GalaxyOS 模块流水线引擎

解决的问题:
  context_assemble 里有 16+ 模块靠手工 try/except 堆叠，
  13 个核心神经网络模块（~7168行）0调用,
  模块的产出不被其他模块消费，形成"死数据"。

设计:
  每个模块注册 inputs / outputs / fn,
  引擎自动推导依赖图 → 拓扑排序 → 并行/串行调度 →
  自动将每个模块的产出广播给下游消费者。

用法:
  engine = PipelineEngine()
  engine.register("reranker", inputs=["query", "raw_results"], outputs=["reranked"], fn=reranker_fn)
  engine.register("crag", inputs=["reranked"], outputs=["quality"], fn=crag_fn)
  result = engine.run(query="...", raw_results=[...])
  # → 自动 reranker → crag → ...
"""

import inspect
import time
import logging
import threading
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("galaxyos.pipeline")


class Stage:
    """流水线中的一个模块/阶段"""

    def __init__(
        self,
        module_id: str,
        inputs: List[str],
        outputs: List[str],
        fn: Callable,
        critical: bool = True,
        timeout: float = 10.0,
        description: str = "",
    ):
        self.module_id = module_id
        self.inputs = inputs          # 需要的输入字段名
        self.outputs = outputs        # 产出的字段名
        self.fn = fn                  # 执行函数 fn(context: dict) -> dict
        self.critical = critical       # True=串行关键路径, False=后台异步
        self.timeout = timeout
        self.description = description

    def __repr__(self):
        return f"Stage({self.module_id})"


class PipelineEngine:
    """
    流水线引擎。
    用法:
        engine = PipelineEngine()
        engine.register("embed", inputs=["query"], outputs=["vector"], ...)
        engine.register("rerank", inputs=["vector"], outputs=["reranked"], ...)
        results = engine.run({"query": "..."})
    """

    def __init__(self):
        self._stages: Dict[str, Stage] = {}
        self._output_to_stage: Dict[str, str] = {}  # output_name → stage_id
        
        # 自动推导的依赖图
        self._dependency_graph: Dict[str, List[str]] = {}  # stage_id → [dep_stage_ids]
        self._consumers: Dict[str, List[str]] = {}          # output_name → [consumer_stage_ids]
        
        self._built = False
        self._lock = threading.Lock()

    # ─── 注册 ───────────────────────────────────────────

    def register(
        self,
        module_id: str,
        inputs: List[str],
        outputs: List[str],
        fn: Callable,
        critical: bool = True,
        timeout: float = 10.0,
        description: str = "",
    ) -> "PipelineEngine":
        """注册一个模块到流水线。返回 self 支持链式调用。"""
        if module_id in self._stages:
            logger.warning(f"[pipeline] 覆盖注册: {module_id}")
        
        self._stages[module_id] = Stage(
            module_id=module_id,
            inputs=inputs,
            outputs=outputs,
            fn=fn,
            critical=critical,
            timeout=timeout,
            description=description,
        )
        # 反向索引：output → 产出它的 module
        for out in outputs:
            self._output_to_stage[out] = module_id
        
        self._built = False
        return self

    # ─── 依赖推导 ───────────────────────────────────────

    def _build_graph(self):
        """推导依赖图和消费者映射。"""
        self._dependency_graph = {}
        self._consumers = defaultdict(list)

        for mod_id, stage in self._stages.items():
            deps = []
            for inp in stage.inputs:
                # 这个输入是谁产的？
                if inp in self._output_to_stage:
                    deps.append(self._output_to_stage[inp])
                # else: 输入来自外部（context 中直接提供），不是依赖
            self._dependency_graph[mod_id] = list(set(deps))

        # 消费者索引：每个 output → 谁消费它
        for mod_id, stage in self._stages.items():
            for inp in stage.inputs:
                if inp in self._output_to_stage:
                    producer = self._output_to_stage[inp]
                    self._consumers[producer].append(mod_id)

        # 检测环
        self._detect_cycles()

        # 标记孤立模块（无下游消费者）
        all_consumed = set()
        for consumer_list in self._consumers.values():
            all_consumed.update(consumer_list)
        for mod_id in self._stages:
            if mod_id not in all_consumed:
                logger.info(f"[pipeline] 🟡 孤立模块（无下游消费者）: {mod_id}")

        self._built = True

    def _detect_cycles(self):
        """DFS 检测环。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {mod_id: WHITE for mod_id in self._stages}

        def dfs(node, path):
            color[node] = GRAY
            for dep in self._dependency_graph.get(node, []):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    cycle = path + [dep]
                    logger.warning(f"[pipeline] ⚠️ 检测到循环依赖: {' → '.join(cycle)}")
                    raise ValueError(f"循环依赖: {' → '.join(cycle)}")
                if color[dep] == WHITE:
                    dfs(dep, path + [dep])
            color[node] = BLACK

        for mod_id in list(color.keys()):
            if color[mod_id] == WHITE:
                dfs(mod_id, [mod_id])

    def _topological_sort(self, available_outputs: Set[str]) -> List[Stage]:
        """基于当前可用输入做拓扑排序。"""
        if not self._built:
            self._build_graph()

        # 计算入度（只考虑本批次可运行的模块）
        in_degree = {}
        for mod_id in self._stages:
            deps = [d for d in self._dependency_graph.get(mod_id, [])
                    if d in self._stages]
            # 检查这个 dep 的产出是否已就绪
            unsatisfied = []
            for d in deps:
                dep_stage = self._stages[d]
                if not any(o in available_outputs for o in dep_stage.outputs):
                    unsatisfied.append(d)
            in_degree[mod_id] = len(unsatisfied)

        queue = deque([m for m, d in in_degree.items() if d == 0])
        sorted_stages = []

        while queue:
            mod_id = queue.popleft()
            sorted_stages.append(self._stages[mod_id])
            # 这个模块产出后，哪些模块的依赖满足了
            for out in self._stages[mod_id].outputs:
                available_outputs.add(out)
            # 下游模块重新计算入度
            for consumer in self._consumers.get(mod_id, []):
                if consumer in in_degree:
                    # 检查消费者的所有依赖是否全部满足
                    need_all = set(self._dependency_graph.get(consumer, []))
                    have_all = True
                    for d in need_all:
                        dep_stage = self._stages[d]
                        if not any(o in available_outputs for o in dep_stage.outputs):
                            have_all = False
                            break
                    if have_all and consumer not in [s.module_id for s in sorted_stages]:
                        queue.append(consumer)

        # 检查是否有未调度的模块（死锁）
        scheduled_ids = {s.module_id for s in sorted_stages}
        unscheduled = set(self._stages.keys()) - scheduled_ids
        if unscheduled:
            logger.warning(f"[pipeline] ⚠️ 以下模块因依赖未满足无法调度: {unscheduled}")

        return sorted_stages

    # ─── 执行 ───────────────────────────────────────────

    def run(
        self,
        context: Dict[str, Any],
        stages_filter: Optional[List[str]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        运行流水线。

        Args:
            context: 初始上下文，包含 query 等外部输入
            stages_filter: 只运行这些模块（None=全部）
            timeout: 整体超时

        Returns:
            enriched context（包含所有模块的产出）
        """
        if not self._built:
            self._build_graph()

        ctx = dict(context)
        # 确保 key 存在
        ctx.setdefault("errors", {})
        ctx.setdefault("timings", {})

        # 确定要运行的模块
        target_ids = stages_filter or list(self._stages.keys())
        runnable = [self._stages[m] for m in target_ids if m in self._stages]

        # 拓扑排序
        available = set(ctx.keys())
        sorted_stages = self._topological_sort(available)
        sorted_stages = [s for s in sorted_stages if s.module_id in target_ids]

        logger.info(f"[pipeline] 调度 {len(sorted_stages)} 个模块: {[s.module_id for s in sorted_stages]}")

        # 分离关键路径和后台路径
        critical_path = [s for s in sorted_stages if s.critical]
        background = [s for s in sorted_stages if not s.critical]

        # 串行执行关键路径
        for stage in critical_path:
            self._execute_stage(stage, ctx)

        # 后台启动非关键模块
        bg_threads = []
        for stage in background:
            t = threading.Thread(
                target=self._execute_stage,
                args=(stage, ctx),
                daemon=True,
            )
            t.start()
            bg_threads.append(t)

        # 等待所有后台（最多 timeout）
        for t in bg_threads:
            t.join(timeout=max(1.0, timeout / len(bg_threads) if bg_threads else timeout))

        return ctx

    def _execute_stage(self, stage: Stage, ctx: Dict[str, Any]):
        """执行单个模块。"""
        start = time.time()
        mod_id = stage.module_id

        # 检查输入是否就绪
        missing = [i for i in stage.inputs if i not in ctx and i not in self._output_to_stage]
        if missing:
            ctx["errors"][mod_id] = f"缺少输入: {missing}"
            logger.debug(f"[pipeline] {mod_id} 跳过（缺 {missing}）")
            return

        # 检查是否有外部输入（不在 output 映射中的 inputs）
        has_input = all(
            i in ctx for i in stage.inputs
        )
        if not has_input:
            ctx["errors"][mod_id] = "输入未就绪"
            return

        logger.debug(f"[pipeline] ▶ {mod_id} 开始")

        try:
            # 构建输入子集：只传这个模块需要的
            kwargs = {k: ctx.get(k) for k in stage.inputs if k in ctx}
            result = stage.fn(**kwargs)

            if result is not None and isinstance(result, dict):
                for key, value in result.items():
                    if key in stage.outputs or key.startswith("_"):
                        ctx[key] = value
                    else:
                        ctx[f"_{mod_id}_{key}"] = value

            elapsed = time.time() - start
            ctx["timings"][mod_id] = round(elapsed, 3)
            logger.debug(f"[pipeline] ✅ {mod_id} 完成（{elapsed:.2f}s）")

        except Exception as e:
            elapsed = time.time() - start
            ctx["errors"][mod_id] = f"{type(e).__name__}: {e}"
            ctx["timings"][mod_id] = round(elapsed, 3)
            
            if stage.critical:
                logger.error(f"[pipeline] ❌ 关键模块 {mod_id} 失败: {e}")
                raise
            else:
                logger.warning(f"[pipeline] ⚠️ 非关键模块 {mod_id} 失败: {e}")

    # ─── 内省 ───────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """返回流水线状态报告。"""
        if not self._built:
            self._build_graph()
        
        return {
            "total_stages": len(self._stages),
            "stages": {
                mod_id: {
                    "inputs": stage.inputs,
                    "outputs": stage.outputs,
                    "depends_on": self._dependency_graph.get(mod_id, []),
                    "consumed_by": self._consumers.get(mod_id, []),
                    "critical": stage.critical,
                    "description": stage.description,
                }
                for mod_id, stage in self._stages.items()
            },
            "orphan_modules": [  # 无下游消费者的模块
                mod_id for mod_id in self._stages
                if mod_id not in set(sum(self._consumers.values(), []))
            ],
        }

    def reset(self):
        """清空所有注册。"""
        self._stages.clear()
        self._output_to_stage.clear()
        self._dependency_graph.clear()
        self._consumers.clear()
        self._built = False
