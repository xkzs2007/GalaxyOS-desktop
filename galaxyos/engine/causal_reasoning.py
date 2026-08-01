#!/usr/bin/env python3
"""
因果推理模块 — Causal Chain of Thought Reasoning (论文级升级)

Zhang et al. (2024) — CausalCoT
Pearl (2009) Causality — 因果三阶: 关联 → 干预 → 反事实

核心增强 (2026-05-27):
  A. CausalGraph 数据结构 — 带方向、强度、Pearl 3 层划分
  B. 三层推理: 关联层 (已有) + 干预层 (新增) + 反事实层 (增强)
  C. get_causal_graph() — 返回完整因果图快照

用法:
    cr = CausalReasoning(llm_flash=flash)
    result = cr.analyze("因为下雨所以路滑，路滑所以车祸多")
    graph = cr.get_causal_graph()
"""

import json
import time
import logging
import re
from typing import List, Dict, Any, Set

logger = logging.getLogger(__name__)

# ─── Prompts ──────────────────────────────────────────────

_CAUSAL_EXTRACT_PROMPT = """分析以下文本中的因果关系。
文本: {text}

请提取所有因果对，格式如下:
{{
  "causal_pairs": [
    {{"cause": "原因", "effect": "结果", "direction": "direct", "strength": 0.8}},
    {{"cause": "原因2", "effect": "结果2", "direction": "indirect", "strength": 0.6}}
  ],
  "confidence": 0.85
}}

方向说明: direct=直接因果, indirect=间接因果
强度范围: 0.0~1.0, 越高越确定"""

_INTERVENTIONAL_PROMPT = """基于下面的因果关系，做干预分析。
因果关系: {causal_text}

干预问题: {question}

请用以下格式回答:
{{
  "intervention": "改变的变量",
  "expected_effect": "预期结果会怎样变化",
  "reasoning": "推理过程",
  "confidence": 0.8
}}"""

_COUNTERFACTUAL_PROMPT = """基于下面的因果关系，做反事实推理。
因果关系: {causal_text}
假设条件: {hypothesis}

反事实分析: 如果假设成立，结果会有什么不同？请给出具体推理过程。
考虑整条因果链路的影响，不只是单一因果对。"""


# ─── CausalGraph 内部数据结构 ────────────────────────────

class CausalGraph:
    """
    Pearl 因果三层的图数据结构

    层级 (Pearl Causal Hierarchy):
      - associational:   P(Y|X) — 观察到的关联
      - interventional:  P(Y|do(X)) — 干预操作
      - counterfactual:  P(Y_x|X',Y') — 反事实推断
    """

    def __init__(self):
        self.edges: List[Dict] = []
        self.variables: Set[str] = set()
        self.levels: Dict[str, List] = {
            "associational": [],
            "interventional": [],
            "counterfactual": [],
        }

    def add_edge(self, cause: str, effect: str,
                 direction: str = "direct",
                 strength: float = 0.5,
                 level: str = "associational") -> None:
        """添加因果边"""
        self.edges.append({
            "cause": cause,
            "effect": effect,
            "direction": direction,
            "strength": min(max(strength, 0.0), 1.0),
            "level": level,
        })
        self.variables.add(cause)
        self.variables.add(effect)
        if level in self.levels:
            self.levels[level].append({
                "cause": cause,
                "effect": effect,
            })

    def add_interventional(self, cause: str, effect: str,
                           strength: float = 0.5) -> None:
        """添加干预层因果"""
        self.add_edge(cause, effect, "direct", strength, "interventional")

    def add_counterfactual(self, cause: str, effect: str,
                           strength: float = 0.5) -> None:
        """添加反事实层因果"""
        self.add_edge(cause, effect, "indirect", strength, "counterfactual")

    def get_causal_chains(self, from_var: str,
                          to_var: str) -> List[List[Dict]]:
        """获取两个变量间的所有因果路径"""
        chains = []
        visited = set()

        def dfs(current: str, path: List[Dict]):
            if current == to_var:
                chains.append(list(path))
                return
            if current in visited:
                return
            visited.add(current)
            for edge in self.edges:
                if edge["cause"] == current and edge["effect"] not in visited:
                    path.append(edge)
                    dfs(edge["effect"], path)
                    path.pop()
            visited.remove(current)

        dfs(from_var, [])
        return chains

    def to_dict(self) -> Dict:
        """序列化为可 JSON 序列化的字典"""
        return {
            "variables": sorted(list(self.variables)),
            "edges": self.edges,
            "levels": {k: v for k, v in self.levels.items()},
        }

    def __repr__(self) -> str:
        return f"CausalGraph(variables={len(self.variables)}, edges={len(self.edges)})"


# ─── CausalReasoning 主类 ────────────────────────────────

class CausalReasoning:
    """因果推理引擎 — 论文级三阶因果推理"""

    def __init__(self, llm_flash=None):
        self.llm = llm_flash
        self._causal_graph = CausalGraph()

    def analyze(self, text: str) -> Dict[str, Any]:
        """
        因果分析主入口 — Pearl 三层推理

        Returns:
            causes: List[str] 原因列表
            effects: List[str] 结果列表
            links: List[Tuple] 因果链
            graph: Dict 因果图结构化
            causal_graph: Dict CausalGraph 完整快照
            interventional: Optional[str] 干预层分析
            counterfactual: Optional[str] 反事实层推理
        """
        if not self.llm or not text.strip():
            return {"causes": [], "effects": [], "links": [],
                    "graph": {"nodes": [], "edges": []},
                    "causal_graph": self._causal_graph.to_dict(),
                    "error": "no_input"}

        t0 = time.time()

        # 1. 关联层: 从文本提取因果对
        causal = self._extract_causal(text)

        # 2. 干预层: 对前 2 个因果对做 do-operator 分析
        interventional_results = []
        pairs = causal.get("causal_pairs", [])
        for pair in pairs[:2]:
            cause = pair.get("cause", "")
            effect = pair.get("effect", "")
            if cause and effect:
                question = f"如果改变 {cause}，{effect} 会怎样？"
                iv = self._interventional(text, cause, effect)
                interventional_results.append({
                    "cause": cause,
                    "effect": effect,
                    "question": question,
                    "result": iv,
                })

        # 3. 反事实层: 在因果图上做全链路推导
        cf_result = None
        if pairs:
            top_pair = pairs[0]
            hypothesis = f"如果 {top_pair['cause']} 没有发生"
            cf_result = self._counterfactual(text, causal, hypothesis)

        # 4. 构建完整因果图
        graph_dict = causal.get("graph", {"nodes": [], "edges": []})

        return {
            "causes": causal.get("causes", []),
            "effects": causal.get("effects", []),
            "links": causal.get("links", []),
            "causal_pairs": causal.get("causal_pairs", []),
            "confidence": causal.get("confidence", 0),
            "graph": graph_dict,
            "causal_graph": self._causal_graph.to_dict(),
            "interventional": interventional_results,
            "counterfactual": cf_result,
            "time_ms": round((time.time() - t0) * 1000, 1),
        }

    def _extract_causal(self, text: str) -> Dict[str, Any]:
        """关联层 — 用 LLM 提取因果结构"""
        try:
            prompt = _CAUSAL_EXTRACT_PROMPT.format(text=text[:2000])
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500, temperature=0.1,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # 先尝试整体 parse（最干净）
            data = None
            try:
                data = json.loads(raw)
            except Exception:
                pass
            if data is None:
                # 尝试从 markdown 代码块中提取
                jm = re.search(r'```(?:json)?\s*(\{.*\})\s*```', raw, re.DOTALL)
                if jm:
                    try:
                        data = json.loads(jm.group(1))
                    except Exception:
                        pass
            if data is None:
                # 花括号平衡提取（兜底，处理嵌套 JSON 的懒匹配问题）
                start = raw.find('{')
                if start >= 0:
                    depth = 0
                    for i in range(start, len(raw)):
                        if raw[i] == '{': depth += 1
                        elif raw[i] == '}': depth -= 1
                        if depth == 0:
                            try:
                                data = json.loads(raw[start:i+1])
                            except Exception:
                                pass
                            break
            if data:
                pairs = data.get("causal_pairs", [])
                confidence = data.get("confidence", 0.5)

                causes = [p["cause"] for p in pairs if "cause" in p]
                effects = [p["effect"] for p in pairs if "effect" in p]
                links = [[p.get("cause", ""), p.get("effect", ""),
                          p.get("direction", "direct")]
                         for p in pairs]

                # 填充 CausalGraph（关联层）
                for pair in pairs:
                    self._causal_graph.add_edge(
                        cause=pair.get("cause", ""),
                        effect=pair.get("effect", ""),
                        direction=pair.get("direction", "direct"),
                        strength=pair.get("strength", 0.5),
                        level="associational",
                    )
            else:
                causes = [raw[:300]]
                effects = []
                links = []
                pairs = []
                confidence = 0.3

            graph = self._build_graph(causes, effects, links)
            return {
                "causes": causes, "effects": effects, "links": links,
                "causal_pairs": pairs, "confidence": confidence,
                "graph": graph,
            }
        except Exception as e:
            logger.warning(f"Causal extraction error: {e}")
            return {"causes": [], "effects": [], "links": [],
                    "causal_pairs": [], "confidence": 0,
                    "graph": {"nodes": [], "edges": []}}

    def _interventional(self, text: str, cause: str,
                        effect: str) -> str:
        """干预层 — do-operator: 如果改变 cause, effect 会怎样?"""
        try:
            causal_text = json.dumps({
                "text": text[:300],
                "intervention_variable": cause,
                "target_variable": effect,
            }, ensure_ascii=False)
            question = f"如果改变「{cause}」，「{effect}」会怎样变化？"
            prompt = _INTERVENTIONAL_PROMPT.format(
                causal_text=causal_text[:1500],
                question=question,
            )
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.2,
            )
            result = (resp.choices[0].message.content or "").strip()[:800]

            # 填充 CausalGraph（干预层）
            self._causal_graph.add_interventional(cause, effect, 0.7)
            return result
        except Exception as e:
            logger.warning(f"Interventional error: {e}")
            return ""

    def _counterfactual(self, text: str, causal: Dict,
                        hypothesis: str) -> str:
        """反事实层 — 在因果图上做全链路推导"""
        try:
            # 构建包含完整因果图的上下文
            causal_text = json.dumps({
                "text": text[:500],
                "causes": causal.get("causes", []),
                "effects": causal.get("effects", []),
                "causal_pairs": causal.get("causal_pairs", []),
                "causal_graph": self._causal_graph.to_dict(),
            }, ensure_ascii=False)

            prompt = _COUNTERFACTUAL_PROMPT.format(
                causal_text=causal_text[:2000],
                hypothesis=hypothesis,
            )
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500, temperature=0.3,
            )
            result = (resp.choices[0].message.content or "").strip()[:1200]

            # 填充 CausalGraph（反事实层）
            for pair in causal.get("causal_pairs", [])[:2]:
                self._causal_graph.add_counterfactual(
                    pair.get("cause", ""),
                    pair.get("effect", ""),
                    0.6,
                )
            return result
        except Exception as e:
            logger.warning(f"Counterfactual error: {e}")
            return ""

    # ──────────── 公开接口 ────────────

    def get_causal_graph(self) -> Dict:
        """返回当前因果图快照（用于可视化/调试）"""
        return self._causal_graph.to_dict()

    def get_causal_chains(self, from_var: str,
                          to_var: str) -> List[List[Dict]]:
        """获取两个变量间的所有因果路径"""
        return self._causal_graph.get_causal_chains(from_var, to_var)

    # ──────────── 内部工具 ────────────

    @staticmethod
    def _build_graph(causes: List[str], effects: List[str],
                     links: List[list]) -> Dict:
        """将因果链转为图结构"""
        nodes = []
        seen = set()
        for c in causes:
            if c not in seen:
                nodes.append({"id": c, "type": "cause"})
                seen.add(c)
        for e in effects:
            if e not in seen:
                nodes.append({"id": e, "type": "effect"})
                seen.add(e)
        edges = []
        for link in links:
            if len(link) >= 2:
                edges.append({
                    "from": link[0], "to": link[1],
                    "type": link[2] if len(link) >= 3 else "direct",
                })
        return {"nodes": nodes, "edges": edges}


# 全局单例
_instance = None


def get_causal_reasoning(llm_flash=None) -> CausalReasoning:
    global _instance
    if _instance is None:
        _instance = CausalReasoning(llm_flash)
    elif llm_flash and _instance.llm is None:
        _instance.llm = llm_flash
    return _instance


if __name__ == "__main__":
    cr = CausalReasoning()
    print("CausalReasoning loaded (论文级). Use analyze(text) for 3-level causal reasoning.")
    print(f"  CausalGraph class: {CausalGraph.__doc__.split(chr(10))[0] if CausalGraph.__doc__ else ''}")
