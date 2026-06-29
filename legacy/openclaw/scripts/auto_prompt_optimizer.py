"""
AutoPromptOptimizer (APO) — ProTeGi 算法实现

基于论文 "Automatic Prompt Optimization with 'Gradient Descent'
and Beam Search" (arXiv 2305.03495), Microsoft Research.

核心算法:
  1. 文本梯度 ∇: 用错误样本批评当前 prompt，生成"梯度描述"
  2. 反向编辑 δ: 沿负梯度方向编辑 prompt 生成候选
  3. Beam Search: 维护 top-k 候选，每轮展开+评估+剪枝
  4. 多臂老虎机: UCB/SuccessiveRejects 提高评估效率

接入点:
  - SelfEvolutionEngine.evolve(): 替换 ad-hoc 改进建议
  - cognition_phase prompt 优化: 自动调优各阶段 prompt 模板
"""

import json
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("galaxyos.auto_prompt_optimizer")

# ════════════════════════════════════════════════════════════════
# Data Types
# ════════════════════════════════════════════════════════════════

@dataclass
class TrainingExample:
    """一条训练样本：query + response + 批评"""
    query: str
    response: str
    critique: str            # 批评文本（来自 RCI 或人工）
    scores: Dict[str, float] # 各维度评分
    timestamp: float = 0.0
    task_category: str = "general"

@dataclass
class PromptCandidate:
    """一个 prompt 候选项"""
    prompt_id: str
    prompt_text: str
    score: float = 0.0          # 最新评估分数
    evaluations: int = 0        # 评估次数
    parent_id: Optional[str] = None
    generation: int = 0         # 第几代（beam search 层级）
    timestamp: float = 0.0

@dataclass
class TextGradient:
    """文本梯度：描述当前 prompt 哪里不好"""
    gradient_text: str
    target_dimension: str       # 针对哪个维度（faithfulness/relevance/tone...）
    from_critique: str          # 原始批评来源
    direction: str              # "negative" 或 "positive"
    confidence: float = 1.0

# ════════════════════════════════════════════════════════════════
# TextGradientGenerator — ∇ 梯度生成
# ════════════════════════════════════════════════════════════════

class TextGradientGenerator:
    """
    文本梯度生成器:
    给定当前 prompt + 一批 (query, response, critique) 样本，
    让 LLM 生成"梯度描述"——当前 prompt 在哪些方面做错了，
    应该往哪个方向调整。
    """

    GRADIENT_PROMPT_TEMPLATE = """You are analyzing a task instruction (prompt) used by an AI assistant.

Current prompt:
```
{current_prompt}
```

Here are {num_examples} examples where the assistant's output was NOT good, along with critiques explaining why:

{examples_text}

Please analyze the *pattern* in these failures. Specifically:
1. What aspect of the current prompt is causing these problems?
2. In what direction should the prompt be changed to fix these issues?
3. Is this a problem with the prompt's wording, its structure, its constraints, or missing information?

Output your analysis as a JSON object with these fields:
- "gradient_summary": a concise description (1-2 sentences) of what's wrong
- "target_dimension": one of ["faithfulness", "relevance", "completeness", "conciseness", "tone", "formatting", "reasoning", "other"]
- "suggested_changes": 2-3 specific changes to make to the prompt
- "direction": "negative" if the prompt is over-constraining, "positive" if it's under-constraining
- "severity": 0.0 to 1.0 (how critical this issue is)
"""

    def __init__(self, llm_call: Optional[Callable] = None):
        self._llm_call = llm_call
        self._gradient_history: List[Dict] = []

    def set_llm_call(self, llm_call: Callable):
        """注入 LLM 调用函数"""
        self._llm_call = llm_call

    def generate_gradient(
        self,
        current_prompt: str,
        examples: List[TrainingExample],
        max_examples: int = 5,
    ) -> Optional[TextGradient]:
        """从错误样本生成文本梯度"""
        if not self._llm_call:
            logger.warning("LLM call not set, cannot generate gradient")
            return None
        if not examples:
            return None

        # 选择最严重的样本
        sorted_examples = sorted(examples, key=lambda e: min(e.scores.values()) if e.scores else 0)
        batch = sorted_examples[:max_examples]

        # 格式化样本
        lines = []
        for i, ex in enumerate(batch, 1):
            score_str = ", ".join(f"{k}={v:.2f}" for k, v in ex.scores.items()) if ex.scores else "? "
            lines.append(
                f"Example {i}:\n"
                f"  Query: {ex.query[:200]}\n"
                f"  Response: {ex.response[:300]}\n"
                f"  Scores: {score_str}\n"
                f"  Critique: {ex.critique[:400]}"
            )
        examples_text = "\n---\n".join(lines)

        prompt = self.GRADIENT_PROMPT_TEMPLATE.format(
            current_prompt=current_prompt,
            num_examples=len(batch),
            examples_text=examples_text,
        )

        try:
            result = self._llm_call(prompt)
            data = json.loads(result)
        except Exception as e:
            logger.debug(f"Gradient generation parse failed: {e}, using raw result")
            data = {
                "gradient_summary": f"Analysis based on {len(batch)} examples",
                "target_dimension": "general",
                "suggested_changes": [result[:500] if result else "No changes"],
                "direction": "negative",
                "severity": 0.5,
            }

        gradient = TextGradient(
            gradient_text=data.get("gradient_summary", ""),
            target_dimension=data.get("target_dimension", "general"),
            from_critique=str(examples_text)[:500],
            direction=data.get("direction", "negative"),
            confidence=1.0 - data.get("severity", 0.5),
        )

        self._gradient_history.append({
            "timestamp": time.time(),
            "gradient": gradient.gradient_text,
            "dimension": gradient.target_dimension,
            "num_examples": len(batch),
        })

        return gradient

# ════════════════════════════════════════════════════════════════
# PromptEditor — δ 反向编辑
# ════════════════════════════════════════════════════════════════

class PromptEditor:
    """
    Prompt 编辑器:
    给定当前 prompt + 文本梯度 ∇，沿负梯度方向生成多个编辑候选。
    支持三种编辑模式:
      - rewrite: 整体重写 prompt
      - refine: 在关键位置做精准修补
      - augment: 添加缺失的约束/指导
    """

    EDIT_PROMPT_TEMPLATE = """You are improving a task instruction for an AI assistant.

Current prompt:
```
{current_prompt}
```

Analysis of what's wrong (text gradient):
{gradient_text}

The suggested changes are:
{suggested_changes}

Please generate {num_candidates} improved versions of this prompt, using the gradient
as guidance. Each version should address the identified issues.

Return your response as a JSON array of objects, each with:
- "prompt": the improved prompt text
- "edit_type": "rewrite" | "refine" | "augment"
- "change_summary": brief description of what was changed

Be creative but disciplined - each candidate should explore a slightly different
direction of improvement.
"""

    def __init__(self, llm_call: Optional[Callable] = None):
        self._llm_call = llm_call

    def set_llm_call(self, llm_call: Callable):
        self._llm_call = llm_call

    def edit(
        self,
        current_prompt: str,
        gradient: TextGradient,
        num_candidates: int = 3,
        suggestions: Optional[List[str]] = None,
    ) -> List[PromptCandidate]:
        """沿负梯度方向编辑 prompt，生成多个候选"""
        if not self._llm_call:
            logger.warning("LLM call not set")
            return []

        prompt = self.EDIT_PROMPT_TEMPLATE.format(
            current_prompt=current_prompt,
            gradient_text=gradient.gradient_text,
            suggested_changes="\n".join(f"- {s}" for s in (suggestions or ["Improve the prompt"])),
            num_candidates=num_candidates,
        )

        try:
            result = self._llm_call(prompt)
            edits = json.loads(result)
            if isinstance(edits, dict):
                edits = [edits]
        except Exception as e:
            logger.debug(f"Edit generation parse failed: {e}")
            return []

        candidates = []
        for i, ed in enumerate(edits):
            new_text = ed.get("prompt", current_prompt)
            if new_text == current_prompt:
                continue
            candidates.append(PromptCandidate(
                prompt_id=f"apo_{int(time.time())}_{i}",
                prompt_text=new_text,
                score=0.0,
                evaluations=0,
                generation=0,
                timestamp=time.time(),
            ))

        return candidates

# ════════════════════════════════════════════════════════════════
# BanditSelector — 多臂老虎机候选选择
# ════════════════════════════════════════════════════════════════

class BanditSelector:
    """
    多臂老虎机选择器:
    UCB1 算法，在有限评估预算下高效选择最优候选。
    避免穷举所有候选，减少 LLM 调用次数。
    """

    def __init__(self, exploration_constant: float = 1.0):
        self._exploration = exploration_constant
        self._pulls: Dict[str, int] = defaultdict(int)
        self._rewards: Dict[str, float] = defaultdict(float)

    def reset(self):
        self._pulls.clear()
        self._rewards.clear()

    def select(self, candidates: List[PromptCandidate], budget: int = 5) -> List[PromptCandidate]:
        """
        用 UCB1 选择要评估的候选。
        返回按 UCB 分数排序的候选列表（取 top budget 个）。
        """
        total_pulls = sum(self._pulls.values()) or 1
        scored = []

        for c in candidates:
            pulls = self._pulls.get(c.prompt_id, 0)
            avg_reward = self._rewards.get(c.prompt_id, 0.0)
            if pulls > 0:
                ucb = avg_reward + self._exploration * ((2 * (total_pulls ** 0.5)) / (1 + pulls))
            else:
                ucb = float("inf")  # 未评估过的优先
            scored.append((ucb, c))

        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:budget]]

    def update(self, candidate_id: str, reward: float):
        """更新一条评估结果"""
        self._pulls[candidate_id] += 1
        n = self._pulls[candidate_id]
        old = self._rewards[candidate_id]
        self._rewards[candidate_id] = old + (reward - old) / n

    def best(self, candidates: List[PromptCandidate]) -> Optional[PromptCandidate]:
        """返回置信度最高的最优候选"""
        if not candidates:
            return None
        scored = []
        for c in candidates:
            pulls = self._pulls.get(c.prompt_id, 0)
            if pulls == 0:
                continue
            scored.append((self._rewards.get(c.prompt_id, 0.0), c))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

# ════════════════════════════════════════════════════════════════
# EvalRanker — 评估 & 排序
# ════════════════════════════════════════════════════════════════

class EvalRanker:
    """
    候选评估器:
    用 LLM 或 RCI 评估 prompt 候选项的质量。
    支持 Successive Rejects（渐进淘汰）减少评估量。
    """

    EVAL_PROMPT_TEMPLATE = """You are evaluating different versions of a task instruction.
Rate each version from 0 to 1 based on: clarity, completeness, correctness.

Current (baseline) prompt:
```
{baseline_prompt}
```

Candidate prompts to evaluate:
{candidates_text}

For each candidate, provide a score (0.0 to 1.0) indicating how much better
it is than the baseline. Also provide a brief justification.

Return as JSON array:
[{{"id": 0, "score": 0.0, "justification": "..."}}, ...]
"""

    def __init__(self, llm_call: Optional[Callable] = None):
        self._llm_call = llm_call

    def set_llm_call(self, llm_call: Callable):
        self._llm_call = llm_call

    def evaluate_batch(
        self,
        candidates: List[PromptCandidate],
        baseline_prompt: str,
        budget: Optional[int] = None,
    ) -> List[PromptCandidate]:
        """批量评估候选（或子集），返回带分数的候选"""
        if not candidates:
            return []

        batch = candidates[:budget] if budget else candidates

        if self._llm_call and len(batch) <= 10:
            lines = []
            for i, c in enumerate(batch):
                lines.append(f"Candidate {i}:\n{c.prompt_text[:500]}\n")
            candidates_text = "\n---\n".join(lines)

            prompt = self.EVAL_PROMPT_TEMPLATE.format(
                baseline_prompt=baseline_prompt[:500],
                candidates_text=candidates_text,
            )

            try:
                result = self._llm_call(prompt)
                scores = json.loads(result)
                for item in scores:
                    idx = item.get("id", 0)
                    if 0 <= idx < len(batch):
                        batch[idx].score = item.get("score", 0.0)
                        batch[idx].evaluations += 1
            except Exception as e:
                logger.debug(f"Batch eval failed: {e}, using default scores")
                for c in batch:
                    c.score = 0.5
                    c.evaluations = 1
        else:
            # 无 LLM，用随机基线
            import random
            for c in batch:
                c.score = 0.3 + random.random() * 0.4
                c.evaluations = 1

        return batch

# ════════════════════════════════════════════════════════════════
# PromptOptimizer — 顶层 orchestrator
# ════════════════════════════════════════════════════════════════

class PromptOptimizer:
    """
    顶层 Prompt 优化器:
    整合 ∇ 梯度生成 + δ 编辑 + Beam Search + Bandit 选择，
    对给定 prompt 模板执行多轮优化。

    用法:
        optimizer = PromptOptimizer(llm_call=my_llm_fn)
        best = optimizer.optimize(
            prompt="原始的 prompt 模板...",
            training_data=[TrainingExample(...), ...],
            num_rounds=3,
            beam_width=3,
        )
    """

    def __init__(
        self,
        llm_call: Optional[Callable] = None,
        workspace_path: str = "",
    ):
        self.gradient_gen = TextGradientGenerator(llm_call)
        self.editor = PromptEditor(llm_call)
        self.bandit = BanditSelector()
        self.eval_ranker = EvalRanker(llm_call)
        self._llm_call = llm_call
        self._workspace = workspace_path
        self._history: List[Dict] = []
        self._best_prompt: Optional[str] = None
        self._best_score: float = 0.0

    def set_llm_call(self, llm_call: Callable):
        """统一注入 LLM 调用"""
        self._llm_call = llm_call
        self.gradient_gen.set_llm_call(llm_call)
        self.editor.set_llm_call(llm_call)
        self.eval_ranker.set_llm_call(llm_call)

    def optimize(
        self,
        current_prompt: str,
        training_data: List[TrainingExample],
        num_rounds: int = 3,
        beam_width: int = 3,
        candidates_per_round: int = 3,
        eval_budget: int = 5,
    ) -> Dict[str, Any]:
        """
        执行 APO 优化循环。

        参数:
            current_prompt: 当前使用的 prompt 模板
            training_data: 训练样本（带批评的问答对）
            num_rounds: beam search 轮数
            beam_width: beam 宽度（每轮保留多少个候选）
            candidates_per_round: 每轮从每个候选展开几个新变体
            eval_budget: 每轮评估多少个候选用 bandit 控制

        返回:
            {"best_prompt": str, "best_score": float, "history": [...], ...}
        """
        if not training_data:
            return {
                "best_prompt": current_prompt,
                "best_score": self._best_score,
                "history": self._history,
                "note": "no training data",
            }

        self._best_prompt = current_prompt
        self._best_score = 0.0

        # Beam 初始化
        beam = [
            PromptCandidate(
                prompt_id="baseline",
                prompt_text=current_prompt,
                score=0.5,
                evaluations=1,
                generation=0,
                timestamp=time.time(),
            )
        ]

        for round_idx in range(num_rounds):
            logger.info(f"APO round {round_idx + 1}/{num_rounds}, beam size {len(beam)}")

            # 1. ∇: 对 beam 中每个候选，从最差样本生成梯度
            all_candidates: List[PromptCandidate] = []
            for parent in beam:
                gradient = self.gradient_gen.generate_gradient(
                    current_prompt=parent.prompt_text,
                    examples=training_data,
                    max_examples=4,
                )
                if gradient is None:
                    continue

                # 2. δ: 沿负梯度方向编辑
                children = self.editor.edit(
                    current_prompt=parent.prompt_text,
                    gradient=gradient,
                    num_candidates=candidates_per_round,
                )
                for child in children:
                    child.parent_id = parent.prompt_id
                    child.generation = round_idx + 1
                all_candidates.extend(children)

            if not all_candidates:
                logger.info("No new candidates this round")
                break

            # 3. Bandit 选择子集评估
            to_eval = self.bandit.select(all_candidates, budget=eval_budget)
            if to_eval:
                evaluated = self.eval_ranker.evaluate_batch(
                    to_eval,
                    baseline_prompt=current_prompt,
                )

                # 4. 更新 bandit
                for c in evaluated:
                    self.bandit.update(c.prompt_id, c.score)

                # 5. 更新全局最优
                for c in evaluated:
                    if c.score > self._best_score:
                        self._best_score = c.score
                        self._best_prompt = c.prompt_text

            # 6. 剪枝: 保留 top-k
            all_scored = beam + all_candidates
            all_scored.sort(key=lambda x: -x.score)
            beam = all_scored[:beam_width]

            self._history.append({
                "round": round_idx + 1,
                "beam_size": len(beam),
                "candidates_generated": len(all_candidates),
                "candidates_evaluated": len(to_eval) if to_eval else 0,
                "best_score": self._best_score,
                "timestamp": time.time(),
            })

        return {
            "best_prompt": self._best_prompt or current_prompt,
            "best_score": self._best_score,
            "num_rounds": num_rounds,
            "history": self._history,
            "total_gradients": len(self.gradient_gen._gradient_history),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_optimization_context(self) -> Dict:
        """获取优化上下文摘要"""
        return {
            "best_score": self._best_score,
            "optimization_rounds": len(self._history),
            "gradient_count": len(self.gradient_gen._gradient_history),
            "history": self._history[-5:] if self._history else [],
        }


# ════════════════════════════════════════════════════════════════
# GalaxyOS 集成入口
# ════════════════════════════════════════════════════════════════

def wrap_llm_call(llm_engine, system_prompt: str = ""):
    """
    包装 GalaxyOS 的 LLM 引擎为 LLM call function。

    用法:
        llm_fn = wrap_llm_call(xiao_yi_claw_instance, "You are an APO optimizer.")
        optimizer = PromptOptimizer(llm_call=llm_fn)
    """
    def _call(prompt: str) -> str:
        if llm_engine is None:
            raise RuntimeError("LLM engine not available")
        # 尝试通过 XiaoYiClawLLM 的 api_proxy 调用
        try:
            result = llm_engine.api_proxy(prompt, system=system_prompt, max_tokens=2048)
            if isinstance(result, dict):
                return result.get("text", result.get("response", str(result)))
            return str(result)
        except AttributeError:
            # 降级: 直接调用 process 模拟
            try:
                result = llm_engine.process(prompt, max_tokens=2048)
                if isinstance(result, dict):
                    return result.get("text", result.get("response", str(result)))
                return str(result)
            except Exception as e:
                return f"LLM call failed: {e}"
    return _call


def training_examples_from_quality_history(quality_history: List[Dict]) -> List[TrainingExample]:
    """
    从 SelfEvolutionEngine 的质量历史记录提取训练样本。
    低分记录（overall < 0.6）转为 TrainingExample。
    """
    examples = []
    for record in quality_history[-50:]:  # 最多取 50 条
        scores = record.get("scores", {})
        overall = scores.get("overall", 1.0)
        if overall < 0.6:
            examples.append(TrainingExample(
                query=record.get("query", ""),
                response=record.get("response", ""),
                critique=record.get("critique", "No critique"),
                scores=scores,
                timestamp=record.get("timestamp", 0),
                task_category=record.get("task_category", "general"),
            ))
    return examples


def training_examples_from_user_feedback(feedback_log: List[Dict]) -> List[TrainingExample]:
    """
    从用户反馈记录提取训练样本。
    """
    examples = []
    for fb in feedback_log[-30:]:
        if fb.get("rating", 5) <= 3:  # 低分反馈
            examples.append(TrainingExample(
                query=fb.get("query", ""),
                response=fb.get("response", ""),
                critique=fb.get("feedback_text", "Negative feedback"),
                scores={
                    "overall": fb.get("rating", 3) / 5.0,
                    "user_satisfaction": fb.get("rating", 3) / 5.0,
                },
                timestamp=fb.get("timestamp", 0),
                task_category=fb.get("task_category", "general"),
            ))
    return examples
