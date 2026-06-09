#!/usr/bin/env python3
"""
Plan-and-Solve 规划执行引擎 (论文级升级)

Wang et al. (ACL 2023) arXiv:2305.04091 — Plan-and-Solve Prompting
Self-Refine (NeurIPS 2023) arXiv:2303.17651

核心增强 (2026-05-27):
  A. 结构化执行计划 — 每步带 type + deps，支持 DAG 并行
  B. 步骤级验证 — _verify_step 每个步骤完成后验证
  C. DAG 顺序执行 — 按依赖图调度，无依赖步骤可并行标记

用法:
    ps = PlanSolve(llm_flash=flash)
    result = ps.execute("如何在贵州兴义搭建 PyTorch 环境")
    # {"plan": [...], "steps": [...], "answer": "...", "steps": [...]}
"""

import json
import time
import logging
import re
from typing import List, Dict, Optional, Any, Set

logger = logging.getLogger(__name__)

_PLAN_PROMPT = """分析以下用户问题，制定一个结构化的执行计划。

问题: {query}

请按以下 JSON 格式输出:

[
  {{
    "step": 1,
    "name": "分析需求",
    "description": "做什么",
    "type": "retrieve",
    "deps": []
  }},
  ...
]

步骤类型:
  - retrieve: 检索信息/回忆上下文
  - compute: 计算/推理/分析
  - verify: 验证/检查一致性
  - transform: 转换格式/整合输出

依赖规则:
  - deps 是前驱步骤的 step 序号列表
  - 简单问题 (长度<15): 1-2步
  - 中等复杂: 2-4步
  - 复杂: 3-6步
  - 每步明确 type 和 deps

示例: 分析"因为下雨所以路滑，路滑所以车祸多"的因果
[
  {{"step": 1, "name": "提取因果变量", "description": "从文本中找出因果变量", "type": "retrieve", "deps": []}},
  {{"step": 2, "name": "构建因果链", "description": "链接因果变量形成链", "type": "compute", "deps": [1]}},
  {{"step": 3, "name": "验证因果逻辑", "description": "检查因果链的合理性", "type": "verify", "deps": [2]}}
]
"""

_VERIFY_PROMPT = """验证以下步骤的执行结果是否符合预期。

步骤: {step_name}
步骤描述: {step_description}
预期结果/上下文: {context}
实际结果: {result}

请评估:
1. 结果是否回答了步骤目标？ (是/否/部分)
2. 结果是否包含关键信息？ (是/否)
3. 置信度 (0~1)

格式: {{"pass": true, "confidence": 0.8, "reason": "..."}}
"""


class PlanSolve:
    """规划执行引擎 — DAG 调度 + 步骤级验证"""

    def __init__(self, llm_flash=None, max_retries: int = 2):
        self.llm = llm_flash
        self.max_retries = max_retries

    def execute(self, query: str, context: str = "") -> Dict[str, Any]:
        """执行: 计划 → DAG 执行 → 步骤验证 → 综合 → 精炼"""
        if not self.llm:
            return {"plan": [{"step": 1, "name": "direct",
                              "type": "compute", "deps": []}],
                    "steps": [], "answer": "", "error": "no_llm"}

        t0 = time.time()

        # 1. 制定结构化计划
        plan = self._make_plan(query)

        # 2. DAG 顺序执行 + 步骤级验证
        steps = self._execute_dag(query, plan, context)

        # 3. 综合所有步骤结果
        answer = self._synthesize(query, steps)

        # 4. Self-Refine
        refined = self._self_refine(query, answer, steps)

        return {
            "plan": plan,
            "steps": steps,
            "answer": answer,
            "refined_answer": refined,
            "step_count": len(plan),
            "time_ms": round((time.time() - t0) * 1000, 1),
        }

    def _make_plan(self, query: str) -> List[Dict]:
        """制定结构化执行计划 (带 type + deps)"""
        try:
            prompt = _PLAN_PROMPT.format(query=query[:1000])
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600, temperature=0.2,
            )
            raw = (resp.choices[0].message.content or "").strip()
            jm = re.search(r'\[.*?\]', raw, re.DOTALL)
            if jm:
                plan = json.loads(jm.group())
                if isinstance(plan, list):
                    # 校验每个 step 格式
                    validated = []
                    for step in plan:
                        validated.append({
                            "step": step.get("step", len(validated) + 1),
                            "name": step.get("name", f"step_{step.get('step', 0)}"),
                            "description": step.get("description", ""),
                            "type": step.get("type", "compute"),
                            "deps": step.get("deps", []),
                        })
                    return validated[:8]  # 最多 8 步
        except Exception as e:
            logger.warning(f"Plan error: {e}")

        # 兜底: 单步计划
        return [{"step": 1, "name": "analyze", "description": "分析问题",
                 "type": "compute", "deps": []}]

    def _execute_dag(self, query: str, plan: List[Dict],
                     context: str) -> List[Dict]:
        """
        按 DAG 顺序执行计划

        调度策略:
          - 建立步骤间的依赖图
          - 当前步骤的所有依赖已完成才执行
          - 无依赖的步骤标记为并行
        """
        step_map = {s["step"]: s for s in plan}
        completed: Set[int] = set()
        steps_result = []
        max_rounds = len(plan) * 2

        for _round in range(max_rounds):
            # 找出当前可执行的步骤 (所有依赖已完成的)
            ready = []
            for s in plan:
                sid = s["step"]
                if sid in completed:
                    continue
                if any(d not in completed for d in s.get("deps", [])):
                    continue
                ready.append(s)

            if not ready:
                break  # 全部完成或死锁

            for step_def in ready:
                sid = step_def["step"]
                name = step_def["name"]
                desc = step_def["description"]
                stype = step_def["type"]
                deps = step_def.get("deps", [])

                # 判断是否可并行
                can_parallel = len(deps) == 0 or all(
                    rd["step"] in completed for rd in ready
                    if rd["step"] != sid and rd["step"] in deps
                )

                # 执行步骤 (多次重试)
                result = ""
                verified = False
                retries = 0
                while retries <= self.max_retries and not verified:
                    result = self._simulate_step(
                        query, name, desc, stype, context,
                        steps_result, deps,
                    )
                    # 步骤级验证
                    v = self._verify_step(
                        step_def, context, result,
                    )
                    verified = v.get("pass", False)
                    if verified:
                        break
                    retries += 1

                # 如果验证失败但已耗尽重试，用当前结果
                step_entry = {
                    "step": sid,
                    "name": name,
                    "type": stype,
                    "deps": deps,
                    "parallel": can_parallel,
                    "result": result or "(no result)",
                    "retries": retries,
                    "verified": verified,
                }
                steps_result.append(step_entry)
                completed.add(sid)

        return steps_result

    def _simulate_step(self, query: str, name: str, desc: str,
                       stype: str, context: str,
                       prior_steps: List[Dict],
                       deps: List[int]) -> str:
        """执行单步骤 (用 LLM 模拟)"""
        try:
            prior_summary = "\n".join(
                f"Step {s['step']} ({s['name']}): {s['result'][:200]}"
                for s in prior_steps
                if s["step"] in deps
            )

            prompt = (
                f"用户问题: {query[:500]}\n"
                f"{'前置结果:\n' + prior_summary + '\n' if prior_summary else ''}"
                f"{'上下文: ' + context[:300] + '\n' if context else ''}"
                f"\n当前步骤: {name}\n"
                f"步骤描述: {desc}\n"
                f"步骤类型: {stype}\n"
                f"\n请执行此步骤，输出结果:"
            )
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.3,
            )
            return (resp.choices[0].message.content or "").strip()[:800]
        except Exception as e:
            logger.warning(f"Step execution error [{name}]: {e}")
            return f"(步骤执行异常: {e})"

    def _verify_step(self, step_def: Dict, context: str,
                     result: str) -> Dict:
        """步骤级验证 — 检查结果是否符合预期"""
        if not result or len(result) < 5:
            return {"pass": False, "confidence": 0.0,
                    "reason": "结果为空或过短"}

        try:
            prompt = _VERIFY_PROMPT.format(
                step_name=step_def.get("name", ""),
                step_description=step_def.get("description", ""),
                context=context[:300] or "(无上下文)",
                result=result[:500],
            )
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200, temperature=0.1,
            )
            raw = (resp.choices[0].message.content or "").strip()
            jm = re.search(r'\{.*?\}', raw, re.DOTALL)
            if jm:
                data = json.loads(jm.group())
                return {
                    "pass": data.get("pass", False),
                    "confidence": data.get("confidence", 0.5),
                    "reason": data.get("reason", ""),
                }
        except Exception as e:
            logger.warning(f"Verify error: {e}")

        # 兜底: 基本有效性检查
        return {"pass": len(result) > 20, "confidence": 0.4,
                "reason": "默认验证"}

    def _synthesize(self, query: str, steps: List[Dict]) -> str:
        """综合所有步骤结果"""
        if not steps:
            return ""
        step_summary = "\n".join(
            f"Step {s['step']} ({s['name']}, {s['type']}): {s['result'][:200]}"
            for s in steps
        )
        try:
            prompt = (
                f"基于以下计划执行结果，回答用户问题。\n\n"
                f"问题: {query[:500]}\n\n"
                f"执行过程:\n{step_summary}\n\n"
                f"综合回答:"
            )
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600, temperature=0.3,
            )
            return (resp.choices[0].message.content or "").strip()[:2000]
        except Exception as e:
            logger.warning(f"Synthesize error: {e}")
            return ""

    def _self_refine(self, query: str, answer: str,
                     steps: List[Dict]) -> str:
        """Self-Refine: 自我评估 + 精炼"""
        if not answer:
            return ""
        step_summary = "\n".join(
            f"Step {s['step']} ({s['name']}): 已验证={s.get('verified', False)}, "
            f"重试={s.get('retries', 0)}次"
            for s in steps
        )
        try:
            prompt = (
                f"请评估以下回答的质量，并给出精炼版本。\n\n"
                f"问题: {query[:500]}\n"
                f"回答: {answer[:1500]}\n"
                f"执行统计:\n{step_summary}\n\n"
                f"评估标准: 完整度、准确度、清晰度\n"
                f"精炼后的回答:"
            )
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600, temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()[:2000]
        except Exception:
            return answer


_instance = None


def get_plan_solve(llm_flash=None) -> PlanSolve:
    global _instance
    if _instance is None:
        _instance = PlanSolve(llm_flash)
    elif llm_flash and _instance.llm is None:
        _instance.llm = llm_flash
    return _instance


if __name__ == "__main__":
    ps = PlanSolve()
    print("PlanSolve loaded (论文级). Use execute(query) for DAG-based plan-and-solve.")
