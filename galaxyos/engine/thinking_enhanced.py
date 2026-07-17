#!/usr/bin/env python3
"""
增强思考引擎 (Thinking Enhanced)

结合三个论文方向：
1. Reflexion — 失败→原因→修复三元组持久化，同类问题避免重复踩坑
2. Self-Refine — 多轮迭代精炼，直到质量达标或达到最大轮次
3. Multi-Path — 并行走多个推理路径，Flash 选出最优再交给 Pro 精加工

所有复杂 NLP 任务（指代消解、对比检测、语义分析）通过 Flash API 完成，
手搓规则降级为基础 0ms 任务（分词、关键词抽取）。

Author: GalaxyOS
Version: 1.0.0
Created: 2026-05-14
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from galaxyos.shared.paths import workspace

# ── 尝试加载 Flash（xiaoYiClawLLM 的 llm_flash 复用 ──
try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

_WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", workspace())

# ==================== 1. Reflexion（反思系统） ====================

@dataclass
class ReflexionEntry:
    """反思记录"""
    id: str
    question: str
    answer_snippet: str
    failure_pattern: str       # 失败模式（如"幻觉"、"遗漏"、"矛盾"）
    root_cause: str            # 根因分析
    fix_strategy: str          # 修复策略
    confidence_drop: float     # 置信度下降幅度
    created_at: str = ""
    hit_count: int = 0         # 被复用次数


class ReflexionEngine:
    """
    Reflexion 反思引擎

    Shinn et al. (2023)

    核心机制：
    - 当 R-CCAM Judge 低分时，分析失败原因，存储反思三元组
    - 下次遇到相似问题，注入反思经验，避免重复犯错
    """

    def __init__(self):
        self.reflexions_path = Path(_WORKSPACE) / ".learnings" / "reflexions.jsonl"
        self.reflexions_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.reflexions_path.exists():
            self.reflexions_path.touch()
        self._cache: List[ReflexionEntry] = []
        self._load()

    def _load(self):
        try:
            with open(self.reflexions_path, "r") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        self._cache.append(ReflexionEntry(**data))
        except Exception:
            pass

    def _save(self, entry: ReflexionEntry):
        try:
            with open(self.reflexions_path, "a") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            self._cache.append(entry)
        except Exception:
            pass

    def record(
        self,
        question: str,
        answer: str,
        scores: Dict[str, float],
        flash_client=None
    ):
        """
        记录一次反思（当 Judge 评分低时自动调用）

        Args:
            question: 用户问题
            answer: AI回答原文
            scores: Judge 评分 (faithfulness/relevance/completeness)
            flash_client: Flash API 客户端（用于分析失败原因）
        """
        # 分析失败模式
        failure_pattern = "unknown"
        root_cause = ""
        fix_strategy = ""

        if flash_client:
            try:
                analysis_prompt = f"""分析以下回答存在的问题：

问题：{question[:200]}
回答：{answer[:300]}
评分：忠实度{scores.get('faithfulness',5)}/10, 相关性{scores.get('relevance',5)}/10, 完整性{scores.get('completeness',5)}/10

请返回JSON格式的分析：
```json
{{
  "failure_pattern": "幻觉|遗漏|矛盾|冗余|偏离|其他",
  "root_cause": "简要根因分析（20字内）",
  "fix_strategy": "改进策略（20字内）"
}}
```"""
                rsp = flash_client.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[{"role": "user", "content": analysis_prompt}],
                    max_tokens=200, temperature=0.1,
                )
                text = rsp.choices[0].message.content.strip()
                # 提取 JSON
                import re
                jm = re.search(r'\{.*\}', text, re.DOTALL)
                if jm:
                    data = json.loads(jm.group())
                    failure_pattern = data.get("failure_pattern", "unknown")
                    root_cause = data.get("root_cause", "")
                    fix_strategy = data.get("fix_strategy", "")
            except Exception:
                pass

        # 计算置信度下降
        avg = (scores.get("faithfulness",5) + scores.get("relevance",5) + scores.get("completeness",5)) / 3
        confidence_drop = round((10 - avg) / 10, 2)

        entry = ReflexionEntry(
            id=f"RFX-{int(time.time())}-{os.urandom(4).hex()}",
            question=question[:200],
            answer_snippet=answer[:200],
            failure_pattern=failure_pattern,
            root_cause=root_cause,
            fix_strategy=fix_strategy,
            confidence_drop=confidence_drop,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save(entry)
        return entry

    def retrieve(self, query: str, top_k: int = 3) -> List[ReflexionEntry]:
        """
        检索相关反思经验（关键词匹配）

        Args:
            query: 当前问题
            top_k: 返回条数

        Returns:
            相关的反思记录列表
        """
        if not self._cache or not query:
            return []

        query_words = set(query.lower())
        scored = []
        for entry in self._cache:
            q_words = set(entry.question.lower())
            overlap = len(query_words & q_words)
            if overlap > 0:
                scored.append((overlap / max(len(q_words), 1), entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [e for s, e in scored[:top_k] if s > 0.05]
        for e in results:
            e.hit_count += 1

        return results

    def format_context(self, entries: List[ReflexionEntry]) -> str:
        """将反思记录格式化为注入上下文"""
        if not entries:
            return ""
        parts = []
        for e in entries:
            parts.append(
                f"[反思经验] 问题: {e.question[:60]} | "
                f"{e.failure_pattern}: {e.root_cause} → {e.fix_strategy}"
            )
        return "\n".join(parts)


# ==================== 2. Self-Refine（迭代精炼） ====================

class SelfRefineLoop:
    """
    Self-Refine 迭代精炼循环

    Madaan et al. (2023)

    核心机制：
    1. 生成回答
    2. Judge 评分
    3. 如果有维度 < 7，生成反馈
    4. 根据反馈修正回答
    5. 重复直到全部达标或达到最大轮次
    """

    MAX_REFINE_ITERATIONS = 3

    def __init__(self, llm_flash=None):
        self.llm_flash = llm_flash
        self.refine_prompt_template = """你之前对以下问题给出了回答。请根据自我反馈优化它。

问题: {question}

原始回答: {answer}

自我反馈:
{feedback}

请给出优化后的回答。直接输出优化结果，不要解释。"""

    def refine(
        self,
        question: str,
        initial_answer: str,
        judge_func=None
    ) -> Tuple[str, List[Dict]]:
        """
        迭代精炼回答

        Args:
            question: 用户问题
            initial_answer: 初始回答
            judge_func: 评分函数，返回 (scores_dict, feedback_text)

        Returns:
            (最终回答, 各轮评分历史)
        """
        if not self.llm_flash or not judge_func:
            return initial_answer, []

        current_answer = initial_answer
        history = []

        for i in range(self.MAX_REFINE_ITERATIONS):
            scores, feedback = judge_func(question, current_answer)
            history.append({"iteration": i + 1, "scores": scores, "feedback": feedback})

            # 检查是否全部达标
            if all(s >= 7 for s in [scores.get("faithfulness", 0), scores.get("relevance", 0), scores.get("completeness", 0)]):
                break

            if i == self.MAX_REFINE_ITERATIONS - 1:
                break

            # 根据反馈修正
            try:
                refine_prompt = self.refine_prompt_template.format(
                    question=question[:300],
                    answer=current_answer[:1000],
                    feedback=feedback[:500],
                )
                rsp = self.llm_flash.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[{"role": "user", "content": refine_prompt}],
                    max_tokens=1500, temperature=0.3,
                )
                refined = rsp.choices[0].message.content.strip()
                if refined and len(refined) > 10:
                    current_answer = refined
            except Exception:
                break

        return current_answer, history


# ==================== 3. Multi-Path 多路径探索 ====================

class MultiPathExplorer:
    """
    多路径并行探索（Tree-of-Thought 风格）

    核心机制：
    1. 将问题拆为 3 个不同视角
    2. Flash 并行探索各视角
    3. Flash 评分选最佳路径
    4. 最佳路径交给 Pro/Flash 精加工输出
    """

    PERSPECTIVES = [
        "从事实性角度分析",
        "从用户实际需求角度给出实用建议",
        "将问题拆解为子步骤逐步推理",
    ]

    def __init__(self, llm_flash=None):
        self.llm_flash = llm_flash

    def explore(self, question: str) -> Dict[str, Any]:
        """
        多路径探索

        Args:
            question: 用户问题

        Returns:
            {
                "best_answer": 最优路径的回答,
                "paths": [{perspective, reasoning, score}, ...],
                "path_count": 探索路径数,
            }
        """
        result = {"paths": [], "best_answer": "", "path_count": 0}

        if not self.llm_flash:
            return result

        paths = []
        for perspective in self.PERSPECTIVES:
            try:
                prompt = f"请从以下视角回答用户问题：\n\n视角：{perspective}\n问题：{question}\n\n回答："
                rsp = self.llm_flash.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500, temperature=0.7,
                )
                reasoning = rsp.choices[0].message.content.strip()
                paths.append({"perspective": perspective, "reasoning": reasoning[:300]})
            except Exception:
                paths.append({"perspective": perspective, "reasoning": ""})

        if not paths:
            return result

        # Flash 评分选择最优路径
        try:
            judge_prompt = "以下是3个AI助手对同一问题的不同回答路径。请选择最佳路径（返回JSON格式）：\n"
            for i, p in enumerate(paths):
                judge_prompt += f"\n路径{i+1}: {p['perspective']}\n回答: {p['reasoning'][:200]}\n"
            judge_prompt += '\n请返回JSON: {"best_path": 1, "reason": "简要原因"}'

            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": judge_prompt}],
                max_tokens=200, temperature=0.1,
            )
            text = rsp.choices[0].message.content.strip()
            import re
            jm = re.search(r'\{.*\}', text, re.DOTALL)
            if jm:
                import json as _json
                judge_result = _json.loads(jm.group())
                best_idx = judge_result.get("best_path", 1) - 1
                if 0 <= best_idx < len(paths):
                    paths[best_idx]["score"] = 1.0
                    result["best_path_reason"] = judge_result.get("reason", "")
        except Exception:
            # 默认选第一条
            if paths:
                paths[0]["score"] = 1.0

        result["paths"] = paths
        result["path_count"] = len([p for p in paths if p["reasoning"]])
        result["best_answer"] = paths[0]["reasoning"] if paths else ""

        return result


# ==================== 4. Flash NLP 路由器 ====================

class FlashNLP:
    """
    通过 Flash API 完成复杂 NLP 任务

    手搓规则只做 0ms 层（分词、关键词抽取），
    语义理解全部交给 Flash。
    """

    def __init__(self, llm_flash=None):
        self.llm_flash = llm_flash

    def resolve_coref(self, text: str, context: str = "") -> Dict[str, str]:
        """
        指代消解（Flash 版）

        比就近原则规则准确得多，能理解语义上下文。
        """
        if not self.llm_flash:
            from nlp_enhanced import CoreferenceResolver
            return CoreferenceResolver().resolve(text, context)

        try:
            prompt = f"""分析以下对话中代词指代的对象。

上下文: {context[:200] if context else '无'}
当前句子: {text}

返回JSON格式：{{"代词": "指代对象", ...}}
注意：只返回存在的代词和它指代的对象。
示例：{{"它": "DAG上下文管理器", "他": "用户"}}"""

            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200, temperature=0.1,
            )
            raw = rsp.choices[0].message.content.strip()
            import re
            jm = re.search(r'\{.*\}', raw, re.DOTALL)
            if jm:
                return json.loads(jm.group())
        except Exception:
            pass

        return {}

    def detect_comparison(self, text: str) -> Optional[Dict]:
        """
        对比检测（Flash 版）

        比模板匹配全面得多，覆盖各种中文比较表达。
        """
        if not self.llm_flash:
            from nlp_enhanced import ComparisonDetector
            r = ComparisonDetector().detect(text)
            if r:
                return asdict(r)
            return None

        try:
            prompt = f"""检测以下句子是否包含比较或对比关系。

句子: {text}

如果是比较句，返回JSON：
{{"has_comparison": true, "subject_a": "主体A", "subject_b": "主体B", "dimension": "比较维度", "relation": ">/</=/>="}}
如果不是，返回：{{"has_comparison": false}}"""

            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200, temperature=0.1,
            )
            raw = rsp.choices[0].message.content.strip()
            import re
            jm = re.search(r'\{.*\}', raw, re.DOTALL)
            if jm:
                data = json.loads(jm.group())
                if data.get("has_comparison"):
                    return data
        except Exception:
            pass

        return None

    def analyze_intent(self, text: str) -> Dict:
        """
        意图分析（Flash 版）

        替代 IntelligentThinkingTrigger 的关键词分类，
        用 Flash 理解真实意图。
        """
        if not self.llm_flash:
            return {"intent": "query", "confidence": 0.5}

        try:
            prompt = f"""分析用户输入的意图类型。

输入: {text[:200]}

返回JSON：
{{"intent": "query|action|compare|analyze|clarify|confirm|other", "confidence": 0.8, "complexity": "low|medium|high", "brief": "一句话概括（10字内）"}}"""

            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200, temperature=0.1,
            )
            raw = rsp.choices[0].message.content.strip()
            import re
            jm = re.search(r'\{.*\}', raw, re.DOTALL)
            if jm:
                return json.loads(jm.group())
        except Exception:
            pass

        return {"intent": "query", "confidence": 0.5}


# ==================== 集成入口 ====================

class ThinkingEnhanced:
    """增强思考引擎集成入口

    除 Reflexion / Self-Refine / MultiPath / Flash NLP 外，
    新增内在元认知进化：
      - introspect() — 从体验数据归纳经验模式
      - evolve() — 输出进化建议供外在元认知层消费

    == 体验 vs 非体验 ==
    喂给 introspect 的数据只包括系统亲身经历的：
      · reflexions.jsonl    — 失败→根因→修复（核心体验）
      · verified_memories   — 用户验证过的记忆（含正确/纠正标记）
      · implicit_preferences— 用户隐式反馈（满意/打断/沉默）
      · performance_metrics — 内部执行成功/失败率
    以下数据**不**喂：
      · 网络搜索结果           — 外部知识
      · 普通闲聊（无任务执行） — 碎片交流
      · SOUL.md / IDENTITY.md — 预设定义，非体验
    """

    def __init__(self, llm_flash=None):
        self.reflexion = ReflexionEngine()
        self.refine = SelfRefineLoop(llm_flash)
        self.multipath = MultiPathExplorer(llm_flash)
        self.flash_nlp = FlashNLP(llm_flash)
        self._llm_flash = llm_flash

    def _load_experience_data(self) -> Dict[str, list]:
        """从过滤后的数据源加载体验样本"""
        ws = _WORKSPACE
        data: Dict[str, list] = {"reflexions": [], "verified": [], "implicit": [], "performance": []}

        # 1. reflexions.jsonl — 失败→根因→修复
        rfx_path = os.path.join(ws, ".learnings", "reflexions.jsonl")
        if os.path.exists(rfx_path):
            with open(rfx_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data["reflexions"].append(json.loads(line))
                        except Exception:
                            pass

        # 2. verified_memories.jsonl — 用户验证结果
        vm_path = os.path.join(ws, ".learnings", "verified_memories.jsonl")
        if os.path.exists(vm_path):
            with open(vm_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data["verified"].append(json.loads(line))
                        except Exception:
                            pass

        # 3. implicit_preferences.jsonl — 隐式反馈
        ip_path = os.path.join(ws, ".learnings", "implicit_preferences.jsonl")
        if os.path.exists(ip_path):
            with open(ip_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data["implicit"].append(json.loads(line))
                        except Exception:
                            pass

        # 4. performance_metrics.jsonl — 执行性能
        pm_path = os.path.join(ws, ".learnings", "performance_metrics.jsonl")
        if os.path.exists(pm_path):
            with open(pm_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data["performance"].append(json.loads(line))
                        except Exception:
                            pass

        return data

    def introspect(self, max_samples: int = 30) -> Dict[str, Any]:
        """
        内在元认知：从体验数据归纳经验模式

        读取过滤后的数据源，用 Flash 归纳出可复用的经验模式。
        """
        if not self._llm_flash:
            return {"success": False, "reason": "Flash 客户端不可用"}

        data = self._load_experience_data()
        total = sum(len(v) for v in data.values())
        if total == 0:
            return {"success": False, "reason": "无体验数据"}

        # 采样：每种类型最多取 max_samples/4 条
        import random
        sample = {}
        for key, items in data.items():
            _limit = max(max_samples // 4, 5)
            sample[key] = items[-_limit:] if len(items) <= _limit else random.sample(items, _limit)

        # 构造归纳提示
        prompt_lines = ["你是一个AI Agent的内在元认知系统。请从以下体验数据中归纳出可复用的经验模式。"]
        prompt_lines.append("\n注意：这些是系统亲身经历的真实体验，不是外部知识。")
        prompt_lines.append("归纳时关注：哪些模式反复出现？哪些行为有效/无效？应该怎么调整？")
        prompt_lines.append("\n=== 失败-根因-修复记录 ===")
        for r in sample["reflexions"][:8]:
            p = r.get("failure_pattern", "?")[:30]
            q = r.get("question", "")[:60]
            rc = r.get("root_cause", "")[:60]
            fs = r.get("fix_strategy", "")[:60]
            prompt_lines.append(f"  [{p}] 问题:{q} | 根因:{rc} | 修复:{fs}")

        prompt_lines.append("\n=== 用户验证记录（正确/纠正标记） ===")
        for v in sample["verified"][:8]:
            content = v.get("content", "")[:80]
            conf = v.get("confidence", 0)
            status = "✅" if conf >= 0.7 else "❌" if conf < 0.3 else "⚠️"
            prompt_lines.append(f"  {status} {content}")

        prompt_lines.append("\n=== 隐式偏好信号 ===")
        for ip in sample["implicit"][:5]:
            signal = ip.get("signal", "")[:60]
            ctx = ip.get("context", "")[:60]
            prompt_lines.append(f"  信号:{signal} | 上下文:{ctx}")

        prompt_lines.append("\n=== 执行性能 ===")
        for pm in sample["performance"][:5]:
            op = pm.get("operation", "")[:40]
            ok = pm.get("success", False)
            ms = pm.get("duration_ms", 0)
            prompt_lines.append(f"  {'✅' if ok else '❌'} {op} ({ms}ms)")

        prompt_lines.append("""
\n你正在以用户的视角审视这些体验数据。用户（xkzs2007）是一位工程严谨主义者，其核心特质如下：

**用户视角特征：**
- 论文驱动的技术改进（不可验证的经验主义不认可）
- 完成即启用、识别即全量推进
- 先改代码验证通过后写文档（代码先于方案讨论）
- 单一路径权威（两套机制做同一件事→保留更成熟的一个，完全移除另一个）
- 融合合并非直接替换
- 学术-工程全闭环实践者

请按以下方式分析上述体验数据：

**步骤1 — 矛盾分析：**
找出用户反馈中反复出现的结构性矛盾。比如：同一类问题反复被纠正，说明什么深层冲突？是否某个行为模式在不同场景下效果相反？

**步骤2 — 第一性原理：**
用户反复纠正的根本原因是什么？不是表层现象。回到最基础的问题："系统存在的根本价值是什么？我的行为与这个价值一致吗？"

**步骤3 — 系统思维：**
如果调整了某个行为模式，整个交互系统的其他部分会怎么变？哪个局部调整能撬动整体体验提升？

**步骤4 — 批评与自我批评：**
上述分析本身有没有问题？有没有偷懒的归因？有没有漏掉关键信号？

**步骤5 — 费曼技巧总结：**
用简洁、有立场的话总结出最多3条可执行的经验模式。

输出JSON格式：
{\
  "patterns": [
    {\
      "scenario": "什么场景下发生",
      "pattern": "反复出现的规律（矛盾分析结果）",
      "first_principles_cause": "根本原因",
      "suggestion": "应该怎么调整",
      "activate": "参数|人格|知识|无",
      "confidence": "低|中|高",
      "evidence": "基于哪些数据得出"
    }
  ],
  "system_impact": "调整后的系统变化",
  "self_critique": "本次分析的局限性"
}
""")

        prompt = "\n".join(prompt_lines)

        try:
            rsp = self._llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.2,
            )
            text = rsp.choices[0].message.content.strip()
            import re
            jm = re.search(r'\{.*\}', text, re.DOTALL)
            if jm:
                result = json.loads(jm.group())
                result["success"] = True
                result["_experience_count"] = {
                    "reflexions": len(data["reflexions"]),
                    "verified": len(data["verified"]),
                    "implicit": len(data["implicit"]),
                    "performance": len(data["performance"]),
                    "total": total,
                }
                return result
        except Exception as e:
            return {"success": False, "reason": f"归纳失败: {e}"}

        return {"success": False, "reason": "无法解析归纳结果"}

    def evolve(self) -> Dict[str, Any]:
        """
        内在元认知进化：运行 introspect 后将结果写入 .learnings/

        写出的数据不修改 DAG/人格文件，仅作为进化建议供外在元认知层消费。
        """
        result = self.introspect()
        if not result.get("success"):
            return result

        # 写入 self_evolution.jsonl（追加模式）
        evo_path = os.path.join(_WORKSPACE, ".learnings", "self_evolution.jsonl")
        os.makedirs(os.path.dirname(evo_path), exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "patterns": result.get("patterns", []),
            "experience_count": result.get("_experience_count", {}),
        }
        try:
            with open(evo_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            result["written_to"] = evo_path
            result["success"] = True
        except Exception as e:
            result["success"] = False
            result["reason"] = f"写入失败: {e}"

        return result


# ==================== 便捷接口 ====================

_thinking = None

def get_thinking_enhanced(llm_flash=None) -> ThinkingEnhanced:
    global _thinking
    if _thinking is None:
        _thinking = ThinkingEnhanced(llm_flash)
    return _thinking


if __name__ == "__main__":
    print("=== 测试: Reflexion 存储与检索 ===")
    te = ThinkingEnhanced()
    te.reflexion.record(
        question="GalaxyOS的记忆系统是什么样的",
        answer="是基于BERT的中文NLP系统",
        scores={"faithfulness": 4, "relevance": 6, "completeness": 5},
    )
    results = te.reflexion.retrieve("GalaxyOS记忆系统架构")
    print(f"检索到 {len(results)} 条反思经验")
    print(te.reflexion.format_context(results))

    print("\n=== 测试: MultiPath 探索 ===")
    # 无 Flash 时的降级测试
    result = te.multipath.explore("Python和Java的对比")
    print(f"探索路径数: {result['path_count']}")

    print("\n=== 测试: Flash NLP (无Flash降级) ===")
    cr = te.flash_nlp.resolve_coref("它好用吗", "GalaxyOS")
    print(f"指代消解: {cr}")
    cmp = te.flash_nlp.detect_comparison("GalaxyOS比腾讯云插件更方便")
    print(f"对比检测: {'有' if cmp else '无'}")

    print("\n✅ 基础测试完成（Flash 版需运行时注入 llm_flash 客户端）")
