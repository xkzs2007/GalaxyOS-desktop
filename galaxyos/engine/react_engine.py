"""
ReAct 多步推理引擎 —— 行动-观察循环

论文参考: ReAct: Synergizing Reasoning and Acting in Language Models (Yao 2022)
https://arxiv.org/abs/2210.03629

核心思路:
  R-CCAM 的单次循环处理单轮认知。ReAct 扩展为多步：
  1. 将复杂问题拆解为子任务
  2. 每一步: 思考(Thought)→行动(Action)→观察(Observation)
  3. 观察结果作为下一步的输入
  4. 直到所有子任务完成或达到最大步数

与 R-CCAM 的关系:
  - 接入 _control_phase: 当判定为复杂问题时，触发 ReAct 循环
  - 每一步的 Action 复用现有的 R-CCAM 五阶段
  - 最终结果整合后返回

Author: 小艺 Claw
"""

import json
import re
import time
import logging
from typing import List, Dict, Optional, Any, Callable
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ─── 数据结构 ───

@dataclass
class ReActStep:
    """ReAct 单步记录"""
    thought: str = ""           # 推理思考（当前步在想什么）
    action: str = ""            # 采取的行动
    action_input: str = ""      # 行动参数
    observation: str = ""       # 观察结果
    sub_answer: str = ""        # 这一步的局部答案
    duration_ms: float = 0.0    # 耗时

@dataclass
class ReActPlan:
    """ReAct 规划与执行结果"""
    original_query: str = ""
    sub_tasks: List[str] = field(default_factory=list)  # 子任务列表
    steps: List[ReActStep] = field(default_factory=list)
    final_answer: str = ""
    total_steps: int = 0
    max_steps: int = 8
    success: bool = False
    error: str = ""
    total_duration_ms: float = 0.0


class ReActEngine:
    """
    ReAct 多步推理引擎

    用法:
        engine = ReActEngine(llm_flash=client, llm_pro=client)
        result = engine.execute("分析这段代码的性能瓶颈")
    """

    def __init__(
        self,
        llm_flash=None,      # Flash 模型客户端（推理/规划用）
        llm_pro=None,        # Pro 模型客户端（关键判断用）
        flash_model: str = "deepseek-v4-flash",
        pro_model: str = "deepseek-v4-pro"
    ):
        self.llm_flash = llm_flash
        self.llm_pro = llm_pro
        self.flash_model = flash_model
        self.pro_model = pro_model

    def execute(
        self,
        query: str,
        max_steps: int = 8,
        step_callback: Optional[Callable] = None,
        context: Optional[Dict] = None
    ) -> ReActPlan:
        """
        执行 ReAct 多步推理

        Args:
            query: 用户问题
            max_steps: 最大推理步数
            step_callback: 每步完成后的回调（可用来保存到 DAG 等）
            context: 额外上下文

        Returns:
            ReActPlan: 完整的推理轨迹
        """
        plan = ReActPlan(
            original_query=query,
            max_steps=min(max_steps, 12)
        )
        t0 = time.time()

        try:
            # Phase 1: 任务分解 —— 拆成子任务
            plan.sub_tasks = self._decompose_task(query)
            if not plan.sub_tasks:
                plan.sub_tasks = [query]

            # Phase 2: 逐步执行
            completed = []
            remaining = list(plan.sub_tasks)
            step_idx = 0

            while remaining and step_idx < plan.max_steps:
                current_task = remaining.pop(0)
                step = ReActStep()
                step_start = time.time()

                # 2a: Thought —— 思考当前步怎么做
                step.thought = self._think(current_task, completed, query)

                # 2b: Action —— 决定行动类型
                action, action_input = self._decide_action(
                    current_task, step.thought, context
                )
                step.action = action
                step.action_input = action_input

                # 2c: 执行行动，得到 Observation
                if action == "search":
                    step.observation = self._act_search(action_input)
                    step.sub_answer = step.observation
                elif action == "analyze":
                    step.observation = self._act_analyze(action_input)
                    step.sub_answer = step.observation
                elif action == "compute":
                    step.observation = self._act_compute(action_input)
                    step.sub_answer = step.observation
                elif action == "verify":
                    step.observation = self._act_verify(action_input)
                    step.sub_answer = step.observation
                elif action == "answer":
                    step.sub_answer = self._act_answer(current_task, completed)
                    step.observation = f"已回答: {step.sub_answer[:200]}"
                else:
                    step.sub_answer = self._act_answer(current_task, completed)
                    step.observation = step.sub_answer

                step.duration_ms = round((time.time() - step_start) * 1000, 1)
                plan.steps.append(step)

                # 2d: 判断当前子任务是否完成
                completed.append({
                    "task": current_task,
                    "answer": step.sub_answer,
                    "action": action
                })

                # 回调
                if step_callback:
                    try:
                        step_callback(step, idx=step_idx, total=len(plan.sub_tasks))
                    except Exception:
                        pass

                step_idx += 1

            # Phase 3: 结果整合
            plan.final_answer = self._synthesize(query, completed)
            plan.success = True

        except Exception as e:
            plan.error = str(e)
            logger.error(f"ReAct 执行失败: {e}", exc_info=True)
            if not plan.final_answer:
                plan.final_answer = f"推理中断: {e}"

        plan.total_steps = len(plan.steps)
        plan.total_duration_ms = round((time.time() - t0) * 1000, 1)
        return plan

    # ─── 子方法 ───

    def _decompose_task(self, query: str) -> List[str]:
        """将复杂问题拆解为多个可执行的子任务"""
        if not self.llm_flash:
            return [query]

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"将以下问题拆解为2-5个独立的子任务，每个子任务应该是一个可以单独回答的具体问题。\n"
                    f"每个子任务一行，用数字编号。\n"
                    f"不要额外解释。\n\n问题: {query}"}],
                max_tokens=500,
                temperature=0.1
            )
            text = resp.choices[0].message.content.strip()
            tasks = []
            for line in text.strip().split('\n'):
                line = line.strip()
                # 去掉编号
                line = re.sub(r'^\d+[\.\、\s]+', '', line).strip()
                if line and len(line) > 4:
                    tasks.append(line)
            # 限制最多 6 个子任务
            return tasks[:6] if tasks else [query]
        except Exception as e:
            logger.warning(f"任务分解失败: {e}")
            return [query]

    def _think(self, task: str, completed: List[Dict], query: str) -> str:
        """对当前任务进行推理思考"""
        if not self.llm_flash:
            return ""

        context = ""
        if completed:
            context = "已完成:\n" + "\n".join(
                [f"- {c['task']}: {c['answer'][:200]}" for c in completed[-3:]]
            )

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"整体问题: {query}\n\n"
                    f"{context}\n\n"
                    f"当前任务: {task}\n\n"
                    f"思考: 要回答当前任务，我需要做什么？\n"
                    f"只需要一句话，说出你的下一步行动计划。"}],
                max_tokens=200,
                temperature=0.2
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return "继续分析当前任务"

    def _decide_action(self, task: str, thought: str, context: dict = None) -> tuple:
        """决定下一步行动类型"""
        action_types = {
            "search": ["查询", "搜索", "查找", "了解", "找", "查", "搜索关于"],
            "analyze": ["分析", "对比", "比较", "评估", "总结", "归纳"],
            "compute": ["计算", "统计", "求和", "平均"],
            "verify": ["验证", "确认", "检查"],
            "answer": []  # 兜底
        }

        combined = (task + " " + thought).lower()
        for action, keywords in action_types.items():
            if any(kw in combined for kw in keywords):
                return action, task

        return "answer", task

    def _act_search(self, query: str) -> str:
        """搜索/检索行动"""
        return f"[检索结果] 已处理查询: {query[:100]}"

    def _act_analyze(self, query: str) -> str:
        """分析行动"""
        if not self.llm_flash:
            return f"[分析] {query}"

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"请分析以下内容，给出关键结论:\n{query}\n\n分析结果:"}],
                max_tokens=300,
                temperature=0.3
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"[分析失败] {e}"

    def _act_compute(self, query: str) -> str:
        """计算行动（用 Python 执行简单计算）"""
        import ast
        try:
            # 尝试从 query 中提取数学表达式
            exprs = re.findall(r'[\d+\-*/().%\s]+', query)
            for expr in exprs:
                expr = expr.strip()
                if expr and len(expr) > 1:
                    try:
                        result = eval(expr, {"__builtins__": {}}, {})
                        return f"[计算结果] {expr} = {result}"
                    except Exception:
                        continue
        except Exception:
            pass
        try:
            if self.llm_flash:
                resp = self.llm_flash.chat.completions.create(
                    model=self.flash_model,
                    messages=[{"role": "user", "content":
                        f"计算: {query}\n结果:"}],
                    max_tokens=100,
                    temperature=0.0
                )
                return f"[计算结果] {resp.choices[0].message.content.strip()}"
        except Exception:
            pass
        return "[计算] 无法自动计算"

    def _act_verify(self, query: str) -> str:
        """验证/检查行动"""
        if not self.llm_flash:
            return f"[验证] {query}"

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"验证以下声明是否合理，指出可能的错误:\n{query}\n\n验证结果:"}],
                max_tokens=300,
                temperature=0.2
            )
            return f"[验证结果] {resp.choices[0].message.content.strip()}"
        except Exception as e:
            return f"[验证失败] {e}"

    def _act_answer(self, task: str, completed: List[Dict]) -> str:
        """基于已完成的任务回答当前子问题"""
        if not self.llm_flash:
            return f"[回答] {task}"

        context = "\n".join([f"- {c['task']}: {c['answer'][:300]}" for c in completed])
        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"基于已获得的信息，回答当前问题。\n\n"
                    f"已有信息:\n{context}\n\n"
                    f"当前问题: {task}\n\n回答:"}],
                max_tokens=500,
                temperature=0.3
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"[回答失败] {e}"

    def _synthesize(self, query: str, completed: List[Dict]) -> str:
        """整合所有子任务的结果，生成最终答案"""
        if not self.llm_pro:
            # 回退到简单拼接
            parts = [c['answer'] for c in completed]
            return "\n\n".join(parts)

        context = json.dumps([
            {"子任务": c["task"], "结果": c["answer"][:500]}
            for c in completed
        ], ensure_ascii=False, indent=2)

        try:
            resp = self.llm_pro.chat.completions.create(
                model=self.pro_model,
                messages=[{"role": "user", "content":
                    f"你是一个结果整合专家。将以下多个子任务的分析结果整合成一份完整回答。\n\n"
                    f"原始问题: {query}\n\n"
                    f"子任务结果:\n{context}\n\n"
                    f"请输出整合后的完整回答:"}],
                max_tokens=2000,
                temperature=0.3
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            # Pro 失败，用 Flash 兜底
            try:
                resp = self.llm_flash.chat.completions.create(
                    model=self.flash_model,
                    messages=[{"role": "user", "content":
                        f"整合以下分析结果，回答原始问题。\n问题: {query}\n结果: {context[:3000]}\n\n整合回答:"}],
                    max_tokens=1500,
                    temperature=0.3
                )
                return resp.choices[0].message.content.strip()
            except Exception:
                return "\n\n".join([c["answer"] for c in completed])

    def format_trajectory(self, plan: ReActPlan) -> str:
        """将推理轨迹格式化为可读文本（用于 DAG 存储或展示）"""
        lines = ["## ReAct 推理轨迹"]
        lines.append(f"原始问题: {plan.original_query}")
        lines.append(f"总步数: {plan.total_steps}/{plan.max_steps}")
        lines.append(f"总耗时: {plan.total_duration_ms:.0f}ms")
        lines.append("")

        if plan.sub_tasks:
            lines.append("### 子任务分解")
            for i, task in enumerate(plan.sub_tasks, 1):
                lines.append(f"  {i}. {task}")
            lines.append("")

        lines.append("### 执行步骤")
        for i, step in enumerate(plan.steps, 1):
            lines.append(f"**步骤 {i}**")
            if step.thought:
                lines.append(f"  Thought: {step.thought}")
            lines.append(f"  Action: {step.action}({step.action_input[:100]})")
            if step.observation:
                lines.append(f"  Observation: {step.observation[:200]}")
            if step.sub_answer:
                lines.append(f"  Result: {step.sub_answer[:300]}")
            lines.append(f"  (耗时 {step.duration_ms:.0f}ms)")
            lines.append("")

        lines.append("### 最终答案")
        lines.append(plan.final_answer)
        return "\n".join(lines)

    def to_state_dict(self, plan: ReActPlan) -> Dict:
        """转换为可注入 PhaseState 的字典"""
        return {
            "react_used": plan.success,
            "react_steps": plan.total_steps,
            "react_trajectory": [asdict(s) for s in plan.steps],
            "react_sub_tasks": plan.sub_tasks,
            "react_duration_ms": plan.total_duration_ms
        }
