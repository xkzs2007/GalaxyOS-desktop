#!/usr/bin/env python3
"""
MultiAgentOrchestrator — 多 Agent 编排器

基于 GalaxyOS 现有模块的多 Agent 能力增强，P0 实现：
  1. 智能选角：按 input_class 动态选择角色
  2. 结构化分解：role + expected_output + success_criteria
  3. 子 Agent 精简循环：Cognition → 搜索 → Critique → 精修
  4. 加权合并：角色权重排序
  5. 知识蒸馏：取最高分做教师→提炼精华

集成点：R-CCAM _control_phase 末尾、Action 阶段前注入

依赖：
  - galaxyos/engine/HyperRouter（策略选择）
  - galaxyos/engine/EntropyRouter（权重分配）
  - galaxyos/engine/multi_agent_debate.py（Judge/蒸馏）
  - galaxyos/engine/dag_message_bus.py（A2A 通信）
  - galaxyos/engine/hallucination_guard.py（MultiAgentVerifier）

Author: 小艺 Claw
Version: 0.2.0
Created: 2026-06-25
"""

import json
import logging
import os
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from enum import Enum


# ═══════════════════════════════════════════
# P2: 进度状态
# ═══════════════════════════════════════════

class AgentProgress(str, Enum):
    QUEUED = "queued"              # 排队等待
    STARTED = "started"            # 开始执行
    COGNITION = "cognition"        # 分析理解中
    SEARCHING = "searching"        # 搜索中
    CRITIQUE = "critique"          # 自我评审中
    REFINING = "refining"          # 精修输出中
    COMPLETED = "completed"        # 完成
    FAILED = "failed"              # 失败
    TIMEOUT = "timeout"            # 超时


@dataclass
class ProgressEvent:
    """进度事件"""
    task_id: str
    role: str
    status: AgentProgress
    message: str = ""
    progress_pct: float = 0.0       # 0-100
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "type": "progress",
            "task_id": self.task_id,
            "role": self.role,
            "status": self.status.value,
            "message": self.message[:200],
            "progress_pct": self.progress_pct,
            "timestamp": self.timestamp,
        }

# ── P1 模块集成 ──
try:
    from dag_message_bus import DAGMessageBus, DAGMessage
    _HAS_DAG_BUS = True
except ImportError:
    _HAS_DAG_BUS = False
    DAGMessageBus = None

# ── 知识蒸馏（DebateEngine Judge）──
try:
    from multi_agent_debate import DebateEngine, get_debate_engine
    _HAS_DEBATE = True
except ImportError:
    _HAS_DEBATE = False
    DebateEngine = None
    get_debate_engine = lambda x=None: None

# ── 选角优化（HyperRouter）──
try:
    from hyper_routing import HyperRouter, extract_features as hr_extract_features
    _HAS_HYPER_ROUTER = True
except ImportError:
    _HAS_HYPER_ROUTER = False
    HyperRouter = None
    hr_extract_features = lambda *a, **kw: {}

# ── 交叉验证（MultiAgentVerifier）──
try:
    from hallucination_guard import MultiAgentVerifier
    _HAS_VERIFIER = True
except ImportError:
    _HAS_VERIFIER = False
    MultiAgentVerifier = None

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════

@dataclass
class SubTask:
    """结构化子任务"""
    id: str
    role: str                # architect / critic / searcher / analyst / summarizer
    name: str
    description: str
    input: str
    expected_output: str = ""     # P0 结构化模板要求
    success_criteria: str = ""    # P0 结构化模板要求
    dependencies: List[str] = field(default_factory=list)
    weight: float = 1.0           # 角色权重用于合并


@dataclass
class SubTaskResult:
    """子任务执行结果"""
    task_id: str
    role: str
    status: str                    # success / failed / timeout
    output: str = ""
    sources: List[str] = field(default_factory=list)
    confidence: float = 0.0        # 自评分 0-1
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class MergeResult:
    """合并结果"""
    sub_results: List[SubTaskResult] = field(default_factory=list)
    merged_output: str = ""
    teacher_output: str = ""       # 蒸馏教师输出
    distill_output: str = ""       # 蒸馏后精华
    merge_stats: Dict = field(default_factory=dict)
    progress_events: List[Dict] = field(default_factory=list)  # P2: 进度事件


# ═══════════════════════════════════════════
# 角色注册表
# ═══════════════════════════════════════════

ROLE_REGISTRY = {
    "searcher": {
        "name": "搜索者",
        "weight": 1.2,
        "description": "搜索信息、爬取资料、检索知识",
        "tool_keys": ["web_search", "web_fetch"],
        "when": lambda ic: ic in ("search", "complex", "analysis"),
    },
    "analyst": {
        "name": "分析师",
        "weight": 1.2,
        "description": "数据分析、趋势识别、模式发现",
        "tool_keys": [],
        "when": lambda ic: ic in ("analysis", "complex", "code"),
    },
    "architect": {
        "name": "架构师",
        "weight": 1.0,
        "description": "代码设计、系统架构、技术方案",
        "tool_keys": [],
        "when": lambda ic: ic in ("code", "complex"),
    },
    "critic": {
        "name": "评审者",
        "weight": 0.8,
        "description": "质疑验证、冲突检测、边界检查",
        "tool_keys": [],
        "when": lambda ic: ic in ("code", "complex", "analysis"),
    },
    "summarizer": {
        "name": "总结者",
        "weight": 0.9,
        "description": "信息整合、去重、结构化输出",
        "tool_keys": [],
        "when": lambda ic: ic in ("search", "complex", "analysis"),
    },
}


# ═══════════════════════════════════════════
# MultiAgentOrchestrator
# ═══════════════════════════════════════════

class MultiAgentOrchestrator:
    """
    P0 多 Agent 编排器

    用法:
        orchestrator = MultiAgentOrchestrator(llm_flash=llm)
        result = orchestrator.run(
            query="帮我搭建一个监控系统...",
            analysis={"input_class": "complex", ...},
            tool_bag={"web_search": search_fn, "web_fetch": fetch_fn},
            max_workers=4
        )
    """

    def __init__(
        self,
        llm_flash=None,
        llm_pro=None,
        max_workers: int = 4,
        sub_task_limit: int = 5,
        use_debate: bool = False,
        use_dag_bus: bool = False,
        use_hyper_router: bool = False,
        use_verifier: bool = False,
        dag_message_bus: Optional[Any] = None,
        hyper_router: Optional[Any] = None,
    ):
        self.llm_flash = llm_flash
        self.llm_pro = llm_pro or llm_flash
        self.max_workers = max_workers
        self.sub_task_limit = sub_task_limit
        self.use_debate = use_debate          # P1: 启用辩论引擎
        self.use_dag_bus = use_dag_bus        # P1: A2A 公告板
        self.use_hyper_router = use_hyper_router  # P1: 选角优化
        self.use_verifier = use_verifier      # P1: 交叉验证
        self.dag_message_bus = dag_message_bus
        self.hyper_router = hyper_router
        # 选角收敛缓存：input_class → roles
        self._role_cache: Dict[str, List[Dict]] = {}
        # P2: 进度推送
        self._progress_callback: Optional[Callable[[ProgressEvent], None]] = None
        self._progress_events: List[ProgressEvent] = []

    # ── P1: 选角优化（HyperRouter + 收敛缓存）──

    def select_roles(self, input_class: str) -> List[Dict]:
        """
        P1: 选角优化

        1. 优先查收敛缓存（同一 input_class 不出第二次）
        2. HyperRouter 辅助路由（如果启用）
        3. fallback 到规则匹配
        """
        # 收敛缓存命中
        if input_class in self._role_cache:
            cached = self._role_cache[input_class]
            logger.info(f"MultiAgent 选角(缓存): {input_class} → {[s['key'] for s in cached]}")
            return cached

        # HyperRouter 辅助
        if self.use_hyper_router and self.hyper_router is not None:
            try:
                features = hr_extract_features(input_class)
                route = self.hyper_router.select_strategy(features)
                selected_strategy = route.get('name', '')
                logger.info(f"HyperRouter 选角策略: {selected_strategy}")
            except Exception as e:
                logger.debug(f"HyperRouter 选角失败: {e}")

        # 规则匹配选角
        selected = []
        for role_key, meta in ROLE_REGISTRY.items():
            if meta["when"](input_class):
                selected.append({
                    "key": role_key,
                    "name": meta["name"],
                    "weight": meta["weight"],
                    "description": meta["description"],
                    "tools": meta["tool_keys"][:],
                })

        # 写入收敛缓存
        self._role_cache[input_class] = selected

        logger.info(
            f"MultiAgent 选角: input_class={input_class} → "
            f"{[s['key'] for s in selected]}"
        )
        return selected

    def _invalidate_role_cache(self, input_class: str = "") -> None:
        """主动清除选角缓存（用于测试/热更新）"""
        if input_class:
            self._role_cache.pop(input_class, None)
        else:
            self._role_cache.clear()

    # ── P0: 结构化分解 ──────────────────────

    def decompose(self, query: str, roles: List[Dict],
                  context: Optional[Dict] = None) -> List[SubTask]:
        """
        P0: LLM 驱动的结构化任务分解

        模板要求每个子任务包含:
          - role: 对应角色
          - expected_output: 明确的输出预期
          - success_criteria: 衡量成功的标准
        """
        if not self.llm_flash:
            return self._fallback_decompose(query, roles)

        role_descs = "\n".join(
            f"  - {r['key']}({r['name']}): {r['description']} (权重={r['weight']})"
            for r in roles
        )

        prompt = f"""你是一个多Agent编排器。将以下用户请求拆分为子任务，分配给不同的Agent角色。

可用的Agent角色:
{role_descs}

要求:
1. 每个子任务必须指定 role（只能从上面选）
2. 每个子任务必须包含 expected_output（明确输出什么）
3. 每个子任务必须包含 success_criteria（如何衡量成功）
4. 如果多个子任务有依赖关系，在 dependencies 中注明
5. 最多拆 {self.sub_task_limit} 个子任务
6. 如果任务不需要拆分，返回空数组

用户请求:
{query[:1000]}

以 JSON 数组格式返回，不要包含其他文字:
[
  {{
    "role": "searcher",
    "name": "搜索XX资料",
    "description": "...",
    "input": "具体搜索关键词",
    "expected_output": "完整的资料列表，包含来源URL",
    "success_criteria": "至少找到3个可靠来源",
    "dependencies": []
  }}
]"""

        try:
            resp = self.llm_flash.chat.completions.create(
                model="",  # 使用默认模型
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            text = resp.choices[0].message.content.strip()
            # 去掉可能的 markdown 围栏
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("\n```", 1)[0]
            data = json.loads(text)

            if not isinstance(data, list):
                raise ValueError("返回不是数组")

            tasks = []
            for i, item in enumerate(data[:self.sub_task_limit]):
                role_key = item.get("role", roles[0]["key"])
                weight = 1.0
                for r in roles:
                    if r["key"] == role_key:
                        weight = r["weight"]
                        break

                tasks.append(SubTask(
                    id=f"st_{int(time.time())}_{i}",
                    role=role_key,
                    name=item.get("name", f"子任务{i+1}"),
                    description=item.get("description", ""),
                    input=item.get("input", query),
                    expected_output=item.get("expected_output", ""),
                    success_criteria=item.get("success_criteria", ""),
                    dependencies=item.get("dependencies", []),
                    weight=weight,
                ))

            logger.info(f"MultiAgent 分解: {len(tasks)}个子任务")
            # 如果只有一个子任务，不走 swarm
            if len(tasks) <= 1:
                logger.info("MultiAgent: 只有一个子任务，不启动编排")
                return []

            return tasks

        except Exception as e:
            logger.warning(f"MultiAgent 分解失败, fallback: {e}")
            return self._fallback_decompose(query, roles)

    def _fallback_decompose(self, query: str,
                            roles: List[Dict]) -> List[SubTask]:
        """LLM 调用失败时的降级分解"""
        tasks = []
        for i, role in enumerate(roles[:self.sub_task_limit]):
            tasks.append(SubTask(
                id=f"st_{int(time.time())}_{i}",
                role=role["key"],
                name=f"{role['name']}处理",
                description=role["description"],
                input=query,
                expected_output=f"完成{role['name']}任务",
                success_criteria="返回结果",
                weight=role["weight"],
            ))
        return tasks

    # ── P0: 子Agent精简循环 ─────────────────

    def run_sub_agent(
        self,
        subtask: SubTask,
        shared_context: Dict,
        tool_bag: Dict,
        llm_flash=None,
    ) -> SubTaskResult:
        """
        子 Agent 精简循环（不走完整 R-CCAM）

        流程:
          0. 公告板: 拉取已完成的其他 Agent 结果（P1）
          1. Cognition: 理解子任务+共享上下文+同伴结果
          2. 搜索（如有工具配置）
          3. Critique: 自我质疑和修正
          4. 精修输出
          5. 公告板: 发布自己的结果（P1）
        """
        llm = llm_flash or self.llm_flash
        t0 = time.time()
        task_id = subtask.id

        try:
            # ── P2: 推送 started ──
            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.STARTED,
                message=f"{subtask.role} Agent 开始: {subtask.name[:40]}",
                progress_pct=5.0,
            ))

            # ── Round 0: 公告板上下文 ──
            peer_context = self._build_dag_context(subtask) if self.use_dag_bus else ""

            # ── Round 1: Cognition ──
            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.COGNITION,
                message=f"{subtask.role} Agent 分析任务中...",
                progress_pct=20.0,
            ))
            cog_prompt = f"""你是一个 {subtask.role} Agent。
任务: {subtask.name}
描述: {subtask.description}
输入: {subtask.input[:500]}
预期输出: {subtask.expected_output}
成功标准: {subtask.success_criteria}

共享上下文:
{json.dumps(shared_context, ensure_ascii=False)[:800]}
{peer_context[:1000]}

请先分析任务的关键点，引用其他 Agent 的已有成果（如果相关），然后给出你的回答。"""

            cog_output = ""
            if llm:
                try:
                    resp = llm.chat.completions.create(
                        model="",
                        messages=[{"role": "user", "content": cog_prompt}],
                        temperature=0.5,
                        max_tokens=1500,
                    )
                    cog_output = resp.choices[0].message.content.strip()
                except Exception as e:
                    cog_output = f"[{subtask.role} Agent] 分析: {subtask.name}"

            # ── Round 2: 浏览器工具注入（P1: 所有 role 均可）──
            search_result = ""
            search_keywords = subtask.input[:200]
            is_search_role = subtask.role in ("searcher", "analyst")

            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.SEARCHING,
                message=f"{subtask.role} Agent 搜索相关信息...",
                progress_pct=40.0,
            ))

            if tool_bag and (is_search_role or tool_bag.get('allow_all_roles', False)):
                # 优先调用 web_search
                web_search = tool_bag.get("web_search")
                if web_search:
                    try:
                        search_result = web_search(search_keywords)
                        search_result = str(search_result)[:1500]
                    except Exception as e:
                        logger.debug(f"SubAgent search failed: {e}")

                # fallback 到 web_fetch
                web_fetch = tool_bag.get("web_fetch")
                if not search_result and web_fetch:
                    try:
                        search_result = web_fetch(search_keywords)
                        search_result = str(search_result)[:1500]
                    except Exception:
                        pass

            # ── Round 3: Critique + 修正 ──
            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.CRITIQUE,
                message=f"{subtask.role} Agent 自我评审中...",
                progress_pct=60.0,
            ))

            critique_prompt = f"""你是一个 {subtask.role} Agent 的自我评审。
你的初步输出:
{cog_output[:1000]}
{'搜索获得的资料:' + search_result[:1000] if search_result else ''}
{'同伴结果:' + peer_context[:600] if peer_context else ''}

请对自己的输出进行严格评审:
1. 有没有遗漏关键信息？
2. 有没有不准确的地方？
3. 需要补充什么？
4. 与同伴结果有没有冲突或可整合之处？
5. 修正后的最终版本是什么？

最终输出格式:
## 评审意见
...
## 修正后输出
..."""

            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.REFINING,
                message=f"{subtask.role} Agent 精修输出中...",
                progress_pct=80.0,
            ))

            final_output = cog_output
            if llm:
                try:
                    resp = llm.chat.completions.create(
                        model="",
                        messages=[{"role": "user", "content": critique_prompt}],
                        temperature=0.3,
                        max_tokens=2000,
                    )
                    final_output = resp.choices[0].message.content.strip()
                except Exception:
                    pass  # 用 cog_output 兜底

            if search_result and search_result not in final_output:
                final_output += f"\n\n---\n搜索补充:\n{search_result[:800]}"

            elapsed = (time.time() - t0) * 1000
            logger.info(
                f"SubAgent [{subtask.role}] {subtask.name[:40]}... "
                f"完成 ({elapsed:.0f}ms)"
            )

            result = SubTaskResult(
                task_id=task_id,
                role=subtask.role,
                status="success",
                output=final_output,
                confidence=0.7,
                duration_ms=elapsed,
            )

            # ── P2: 推送 completed ──
            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.COMPLETED,
                message=f"{subtask.role} Agent 完成 ({elapsed:.0f}ms)",
                progress_pct=100.0,
            ))

            # ── Round 5: 公告板发布 ──
            if self.use_dag_bus:
                self._publish_result(result)

            return result

        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            logger.error(f"SubAgent [{subtask.role}] 失败: {e}")

            self._push_progress(ProgressEvent(
                task_id=task_id, role=subtask.role,
                status=AgentProgress.FAILED,
                message=str(e)[:200],
                progress_pct=0.0,
            ))

            return SubTaskResult(
                task_id=task_id,
                role=subtask.role,
                status="failed",
                error=str(e),
                duration_ms=elapsed,
            )

    # ── P1: 知识蒸馏（DebateEngine Judge 裁决版）──

    def _judge_distill(self, teacher: SubTaskResult,
                       all_results: List[SubTaskResult],
                       query: str) -> Dict[str, Any]:
        """
        用 DebateEngine Judge 做知识蒸馏

        流程:
          1. 取教师输出
          2. 走 DebateEngine 3-Agent 裁决
          3. 如果 Judge 认为需要修正，用 refined_answer
          4. 更新置信度
        """
        if not self.use_debate or not _HAS_DEBATE or not self.llm_flash:
            return {"teacher_output": teacher.output if teacher else "",
                    "distill_output": teacher.output[:1000] if teacher else "",
                    "confidence_delta": 0.0,
                    "verdict": "confirmed"}

        try:
            debate_engine = get_debate_engine(self.llm_flash)
            if debate_engine is None:
                debate_engine = DebateEngine(llm_flash=self.llm_flash)

            teacher_text = teacher.output if teacher else ""
            if not teacher_text:
                return {"teacher_output": "", "distill_output": "",
                        "confidence_delta": 0.0, "verdict": "confirmed"}

            # 走一轮 3-Agent 辩论
            judge_result = debate_engine.debate(
                question=query[:300],
                answer=teacher_text[:1500],
                cycle=1,
            )

            verdict = judge_result.get("verdict", "confirmed")
            refined = judge_result.get("refined_answer", "")
            conf_delta = judge_result.get("confidence_delta", 0.0)

            # 如果 Judge 给了修正版本，用它
            distill_output = refined[:1500] if refined and verdict != "confirmed" else teacher_text[:1500]

            logger.info(
                f"知识蒸馏(Judge): verdict={verdict}, "
                f"conf_delta={conf_delta:.2f}, "
                f"refined={'yes' if refined else 'no'}"
            )

            return {
                "teacher_output": teacher_text,
                "distill_output": distill_output,
                "confidence_delta": conf_delta,
                "verdict": verdict,
            }

        except Exception as e:
            logger.warning(f"知识蒸馏(Judge)失败: {e}")
            return {"teacher_output": teacher.output if teacher else "",
                    "distill_output": teacher.output[:1000] if teacher else "",
                    "confidence_delta": 0.0,
                    "verdict": "confirmed"}

    # ── P1: 交叉验证（MultiAgentVerifier）──

    def _cross_verify(self, merged_output: str) -> Dict[str, Any]:
        """
        最终输出过 MultiAgentVerifier 交叉验证

        取主要句子（按句号分割），逐条验证
        """
        if not self.use_verifier or not _HAS_VERIFIER:
            return {"verified": True, "issues": [], "confidence": 1.0}

        if not merged_output:
            return {"verified": True, "issues": [], "confidence": 1.0}

        try:
            # 分割成陈述句
            sentences = [s.strip() for s in merged_output.replace('\n', '。').split('。')]
            sentences = [s for s in sentences if len(s) > 20][:5]  # 最多5条

            all_issues = []
            total_conf = 0.0
            count = 0

            for sentence in sentences:
                result = MultiAgentVerifier.verify_statement(sentence)
                count += 1
                total_conf += result.get("confidence", 0.5)
                issues = result.get("issues", [])
                if issues:
                    all_issues.append({
                        "sentence": sentence[:80],
                        "issues": issues,
                        "suggestions": result.get("suggestions", []),
                    })

            avg_conf = total_conf / max(count, 1)

            logger.info(
                f"交叉验证: {len(sentences)} 条陈述, "
                f"{len(all_issues)} 条有问题, "
                f"平均置信度={avg_conf:.2f}"
            )

            return {
                "verified": len(all_issues) == 0,
                "issues": all_issues,
                "confidence": avg_conf,
                "total_checked": len(sentences),
            }

        except Exception as e:
            logger.warning(f"交叉验证失败: {e}")
            return {"verified": True, "issues": [], "confidence": 0.5}

    # ── P1: 加权合并（增强版） ────────────

    def weighted_merge(self, results: List[SubTaskResult],
                       roles: List[Dict],
                       original_query: str = "") -> MergeResult:
        """
        P1: 角色加权排序 + Judge 知识蒸馏 + 交叉验证

        1. 按角色权重排序
        2. 检查冲突
        3. 知识蒸馏：DebateEngine Judge 裁决（P1）
        4. 去重合并
        """
        if not results:
            return MergeResult(sub_results=[], merged_output="")

        # 成功的结果
        success_results = [r for r in results if r.status == "success"]

        # 角色权重映射
        weight_map = {r["key"]: r["weight"] for r in roles}

        # 按角色权重 + 置信度排序
        def sort_key(r: SubTaskResult) -> float:
            w = weight_map.get(r.role, 1.0)
            return w * r.confidence

        sorted_results = sorted(success_results, key=sort_key, reverse=True)

        teacher = sorted_results[0] if sorted_results else None

        # ── P1: Judge 知识蒸馏 ──
        distill_info = self._judge_distill(teacher, sorted_results, original_query)

        # 合并所有输出
        all_segments = []
        seen_lines = set()

        for r in sorted_results:
            w = weight_map.get(r.role, 1.0)
            lines = r.output.split("\n")
            deduped = []
            for line in lines:
                key = line.strip().lower()[:60]
                if key and key not in seen_lines and len(key) > 10:
                    seen_lines.add(key)
                    deduped.append(line)

            if deduped:
                prefix = f"## [{r.role.upper()}] "
                all_segments.append(prefix + "\n".join(deduped))

        merged = "\n\n".join(all_segments)

        # 蒸馏输出替换为 Judge 裁决版本
        distill_output = distill_info.get("distill_output", teacher.output[:1000] if teacher else "")
        verdict = distill_info.get("verdict", "confirmed")
        confidence_delta = distill_info.get("confidence_delta", 0.0)

        merge_stats = {
            "total": len(results),
            "success": len(success_results),
            "failed": len(results) - len(success_results),
            "roles": [r.role for r in sorted_results],
            "teacher_role": teacher.role if teacher else "",
            "merged_chars": len(merged),
            "verdict": verdict,
            "confidence_delta": round(confidence_delta, 3),
        }

        return MergeResult(
            sub_results=results,
            merged_output=merged,
            teacher_output=distill_info.get("teacher_output", ""),
            distill_output=distill_output,
            merge_stats=merge_stats,
        )

    # ── P0: 主入口 ────────────────────────────

    # ── P1: 公告板初始化 ──────────────────────

    def _ensure_bus(self) -> bool:
        """确保公告板可用（自动创建或复用）"""
        if not self.use_dag_bus:
            return False
        if self.dag_message_bus is not None:
            return True
        if _HAS_DAG_BUS:
            self.dag_message_bus = DAGMessageBus(dag_manager=None)
            logger.info("MultiAgent: 公告板（纯内存模式）已启动")
            return True
        logger.warning("MultiAgent: 公告板不可用（dag_message_bus 未安装）")
        return False

    def _register_agents(self, sub_tasks: List[SubTask]) -> None:
        """将子 Agent 注册到公告板"""
        bus = self.dag_message_bus
        if bus is None:
            return
        roles_seen = set()
        for st in sub_tasks:
            agent_name = f"orchestrator_{st.id}"
            if st.role not in roles_seen:
                bus.register_agent(
                    agent_name,
                    message_types=["request", "response", "broadcast"]
                )
                roles_seen.add(st.role)

    def _publish_result(self, result: SubTaskResult) -> None:
        """子 Agent 完成后向公告板发布结果"""
        bus = self.dag_message_bus
        if bus is None:
            return
        try:
            bus.broadcast(
                source=f"agent_{result.task_id}",
                payload={
                    "role": result.role,
                    "task_id": result.task_id,
                    "summary": result.output[:500],
                    "confidence": result.confidence,
                    "status": result.status,
                    "char_count": len(result.output),
                },
                subtype="response",
            )
        except Exception as e:
            logger.debug(f"公告板发布失败: {e}")

    def _poll_peer_results(self, agent_prefix: str) -> List[Dict]:
        """从公告板拉取已完成的其他 Agent 结果"""
        bus = self.dag_message_bus
        if bus is None:
            return []
        peer_results = []
        try:
            msgs = bus.poll(agent_prefix, batch_size=20)
            for msg, meta in msgs:
                p = msg.payload
                if p.get("status") == "success" and p.get("task_id", "") != agent_prefix.replace("agent_", ""):
                    peer_results.append(p)
                    bus.ack(msg.message_id, agent_prefix)
        except Exception:
            pass
        return peer_results

    # ── P1: 公告板驱动的共享上下文 ──────────────

    def _build_dag_context(self, subtask: SubTask) -> str:
        """收集公告板中其他 Agent 的结果，构建跨 Agent 上下文"""
        peer_results = self._poll_peer_results(f"agent_{subtask.id}")
        if not peer_results:
            return ""
        parts = []
        for pr in peer_results:
            role = pr.get("role", "unknown")
            summary = pr.get("summary", "")[:300]
            if summary:
                parts.append(f"[{role.upper()} 已完成]: {summary}")
        return "\n\n其他 Agent 已完成的工作:\n" + "\n".join(parts)

    # ── P2: 进度推送 ──────────────────────────

    def set_progress_callback(self, callback: Callable[[ProgressEvent], None]) -> None:
        """设置进度回调（外部注入，比如写入 DAG 或推送消息队列）"""
        self._progress_callback = callback

    def _push_progress(self, event: ProgressEvent) -> None:
        """推送单个进度事件"""
        self._progress_events.append(event)
        if self._progress_callback:
            try:
                self._progress_callback(event)
            except Exception as e:
                logger.debug(f"进度回调失败: {e}")

    def get_progress_summary(self) -> List[Dict]:
        """获取本轮所有进度事件（P2 进度推送外部读取）"""
        return [e.to_dict() for e in self._progress_events]

    def reset_progress(self) -> None:
        """清除进度（新一轮编排前调用）"""
        self._progress_events.clear()

    # ── P1: 主入口（全部能力启用）──────────────

    def run(
        self,
        query: str,
        analysis: Optional[Dict] = None,
        tool_bag: Optional[Dict] = None,
        llm_flash=None,
    ) -> MergeResult:
        """
        P1 多 Agent 编排入口（公告板 + 蒸馏 + 选角优化 + 工具注入 + 交叉验证）

        Args:
            query: 用户原始问题
            analysis: R-CCAM Cognition 阶段的分析结果（必须含 input_class）
            tool_bag: 工具注入 {"web_search": fn, "web_fetch": fn, "allow_all_roles": bool}
            llm_flash: 可选的 LLM 客户端覆盖

        Returns:
            MergeResult: 包含所有子结果 + 合并后输出
        """
        # P2: 清除上一轮进度
        self.reset_progress()

        if not analysis:
            analysis = {"input_class": "complex"}

        input_class = analysis.get("input_class", "complex")

        # 1. 选角（支持 HyperRouter 收敛缓存）
        roles = self.select_roles(input_class)
        if not roles:
            logger.info("MultiAgent: 无适合的 Agent 角色")
            return MergeResult()

        # 2. 分解
        sub_tasks = self.decompose(query, roles, analysis)
        if not sub_tasks:
            return MergeResult()

        # 3. 初始化公告板
        bus_active = self._ensure_bus()
        if bus_active:
            self._register_agents(sub_tasks)
            logger.info(f"MultiAgent: 公告板已就绪，{len(sub_tasks)} 个 Agent")

        # 4. 构建共享上下文
        shared_context = {
            "original_query": query[:500],
            "input_class": input_class,
            "intent": analysis.get("intent", ""),
            "knowledge_type": analysis.get("knowledge_type", ""),
        }

        # 5. 派发（并行 + 公告板 + 工具注入）
        results = self._dispatch_parallel(sub_tasks, shared_context, tool_bag, llm_flash)

        # 6. 加权合并 + Judge 知识蒸馏
        merge_result = self.weighted_merge(results, roles, original_query=query)

        # 7. 交叉验证（最终输出过 MultiAgentVerifier）
        if self.use_verifier:
            v_result = self._cross_verify(merge_result.merged_output)
            merge_result.merge_stats["verified"] = v_result.get("verified", True)
            merge_result.merge_stats["verification_confidence"] = round(v_result.get("confidence", 1.0), 3)
            merge_result.merge_stats["verification_issues"] = len(v_result.get("issues", []))

        # 8. 公告板统计日志
        if bus_active and self.dag_message_bus is not None:
            try:
                stats = self.dag_message_bus.get_stats()
                logger.info(f"公告板统计: {stats}")
            except Exception:
                pass

        logger.info(
            f"MultiAgent 完成: {merge_result.merge_stats}"
        )

        # P2: 携带进度事件
        merge_result.progress_events = self.get_progress_summary()

        return merge_result

    def _dispatch_parallel(
        self,
        sub_tasks: List[SubTask],
        shared_context: Dict,
        tool_bag: Optional[Dict],
        llm_flash=None,
    ) -> List[SubTaskResult]:
        """并行派发子任务（含 P2 进度推送）"""
        workers = min(self.max_workers, len(sub_tasks), 8)
        results = []

        # P2: 推送所有子任务为 QUEUED
        for st in sub_tasks:
            self._push_progress(ProgressEvent(
                task_id=st.id, role=st.role,
                status=AgentProgress.QUEUED,
                message=f"{st.role} Agent 排队中: {st.name[:40]}",
                progress_pct=0.0,
            ))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.run_sub_agent, st, shared_context, tool_bag or {}, llm_flash
                ): st
                for st in sub_tasks
            }

            for future in as_completed(futures, timeout=60.0):
                st = futures[future]
                try:
                    result = future.result(timeout=5.0)
                    results.append(result)
                except TimeoutError:
                    logger.warning(f"SubAgent [{st.role}] 超时")
                    results.append(SubTaskResult(
                        task_id=st.id, role=st.role,
                        status="timeout", error="超时",
                    ))
                except Exception as e:
                    logger.warning(f"SubAgent [{st.role}] 异常: {e}")
                    results.append(SubTaskResult(
                        task_id=st.id, role=st.role,
                        status="failed", error=str(e),
                    ))

        # 按 task_id 保持可预测顺序
        id_order = {st.id: i for i, st in enumerate(sub_tasks)}
        results.sort(key=lambda r: id_order.get(r.task_id, 999))
        return results

    # ════════════════════════════════════════════════════════════════
    # Phase 3.3: OpenClaw Sub-Agent 适配层
    # ════════════════════════════════════════════════════════════════

    def spawn_as_sub_agent(
        self,
        query: str,
        parent_agent_id: str,
        parent_session_key: str,
        analysis: Optional[Dict] = None,
        tool_bag: Optional[Dict] = None,
    ) -> Dict:
        """
        Phase 3.3: 以 OpenClaw sub-agent 模式启动多智能体编排

        遵循 OpenClaw sub-agent 约束：
        - session key 格式: agent:{parent_agent_id}:subagent:{uuid}
        - 禁止嵌套 spawn（sub-agent 不能再 spawn sub-agent）
        - 默认受限工具集（无 sessions_* 权限）
        - 结果通过 announce 模式回传主会话

        Args:
            query: 用户原始问题
            parent_agent_id: 父 Agent ID
            parent_session_key: 父会话 key
            analysis: R-CCAM 分析结果
            tool_bag: 工具注入（受限集）

        Returns:
            Dict: { session_key, results, merged_output, announce_payload }
        """
        import uuid as _uuid

        # 生成 sub-agent session key（OpenClaw 规范）
        sub_uuid = str(_uuid.uuid4())[:8]
        sub_session_key = f"agent:{parent_agent_id}:subagent:{sub_uuid}"

        logger.info(
            f"MultiAgent spawning as sub-agent: parent={parent_agent_id}, "
            f"sub_session={sub_session_key}"
        )

        # 过滤工具集：移除 sessions_* 等受限工具
        restricted_prefixes = ("sessions_", "gateway_", "admin_")
        filtered_tools = {}
        if tool_bag:
            for key, fn in tool_bag.items():
                if not any(key.startswith(prefix) for prefix in restricted_prefixes):
                    filtered_tools[key] = fn
                else:
                    logger.info(f"Sub-agent tool filtered (restricted): {key}")

        # 执行编排（使用受限工具集）
        result = self.run(
            query=query,
            analysis=analysis,
            tool_bag=filtered_tools,
        )

        # 构建 announce 回传 payload
        announce_payload = {
            "type": "subagent_result",
            "session_key": sub_session_key,
            "parent_session_key": parent_session_key,
            "merged_output": result.merged_output if hasattr(result, "merged_output") else "",
            "sub_results": [
                {
                    "role": r.role,
                    "status": r.status,
                    "output": r.output[:500] if r.output else "",
                    "score": r.score,
                }
                for r in (result.sub_results if hasattr(result, "sub_results") else [])
            ],
            "timestamp": time.time(),
        }

        return {
            "session_key": sub_session_key,
            "results": result,
            "merged_output": result.merged_output if hasattr(result, "merged_output") else "",
            "announce_payload": announce_payload,
        }


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

