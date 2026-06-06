#!/usr/bin/env python3
"""
Graph of Thoughts (GoT) — 图结构推理引擎

Besta et al. (2023) arXiv:2308.09687

核心机制:
1. 问题分解 — 将复杂问题拆解为若干子节点（思维步骤）
2. 图拓扑 — 子节点间可并行/串行/条件依赖
3. 聚合 — 叶节点结果聚合为最终回答
4. 回溯 — 低分路径自动回溯重试

适用场景:
- 复杂推理（多步逻辑链）
- 多维度分析（优缺点/对比等）
- 需要分支探索的问题
"""

import json
import os
import time
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum

logger = logging.getLogger(__name__)


class NodeType(Enum):
    LEAF = "leaf"
    AND = "and"       # 所有子节点完成后聚合
    OR = "or"         # 任一子节点完成后即可
    VALIDATE = "validate"  # 验证节点


@dataclass
class ThoughtNode:
    id: str
    content: str = ""
    result: str = ""
    score: float = 0.0
    node_type: NodeType = NodeType.LEAF
    children: List[str] = field(default_factory=list)  # 子节点 ID
    depth: int = 0
    retry_count: int = 0
    max_retries: int = 2


class GraphOfThoughts:
    """图推理引擎"""

    def __init__(self, llm_flash=None, max_workers: int = 3):
        self.llm_flash = llm_flash
        self.pool = ThreadPoolExecutor(max_workers=max_workers)

    def solve(self, question: str) -> Dict[str, Any]:
        """
        主入口: 分解 → 图探索 → 聚合

        Returns:
            {
                "answer": 最终回答,
                "nodes": [节点列表],
                "node_count": 节点数,
                "path_depth": 最大深度,
                "time_ms": 耗时,
                "retries": 重试次数,
            }
        """
        start = time.time()
        result = {"answer": "", "nodes": [], "node_count": 0, "path_depth": 0,
                  "time_ms": 0, "retries": 0, "decomposition": [], "aggregation_log": ""}

        if not self.llm_flash:
            return result

        try:
            # Step 1: 问题分解
            decomposition = self._decompose(question)
            result["decomposition"] = decomposition

            if not decomposition:
                return result

            # Step 2: 构建图
            nodes: Dict[str, ThoughtNode] = {}
            node_id_counter = [0]

            def _new_id() -> str:
                node_id_counter[0] += 1
                return f"gt_{node_id_counter[0]}"

            # 创建根节点（空，用于聚合）
            root = ThoughtNode(id=_new_id(), content=question, node_type=NodeType.AND, depth=0)
            nodes[root.id] = root

            # 第一层：主问题子问题
            for sub_q in decomposition[:4]:
                node = ThoughtNode(id=_new_id(), content=sub_q, node_type=NodeType.LEAF, depth=1)
                nodes[node.id] = node
                root.children.append(node.id)

            # Step 3: 并行执行所有 leaf 节点
            total_retries = 0
            leaf_nodes = [n for n in nodes.values() if n.node_type == NodeType.LEAF]

            # 先并行跑所有 leaf
            futures = {}
            for node in leaf_nodes:
                futures[self.pool.submit(self._execute_leaf, node, question)] = node.id

            for fut in as_completed(futures):
                nid = futures[fut]
                try:
                    node_result = fut.result(timeout=12)
                    if nid in nodes:
                        nodes[nid].result = node_result["result"]
                        nodes[nid].score = node_result["score"]
                        nodes[nid].retry_count = node_result.get("retry_count", 0)
                        total_retries += node_result.get("retry_count", 0)
                except Exception as e:
                    logger.warning(f"GoT 节点 {nid} 执行失败: {e}")

            # Step 4: 聚合
            agg = self._aggregate(question, [nodes[cid] for cid in root.children if cid in nodes])

            result["answer"] = agg.get("answer", "")
            result["aggregation_log"] = agg.get("log", "")

            # Step 5: 回溯优化（如果聚合结果过低）
            if agg.get("avg_score", 0) < 5 and total_retries < 2:
                # 挑最低分的节点重试
                sorted_nodes = sorted(
                    [nodes[cid] for cid in root.children if cid in nodes],
                    key=lambda n: n.score
                )
                for low_node in sorted_nodes[:2]:
                    if low_node.score < 5 and low_node.retry_count < low_node.max_retries:
                        retry = self._execute_leaf(low_node, question, retry=True)
                        if retry.get("result"):
                            low_node.result = retry["result"]
                            low_node.score = retry["score"]
                            low_node.retry_count += 1
                            total_retries += 1

                # 重新聚合
                if total_retries > 0:
                    agg2 = self._aggregate(question, [nodes[cid] for cid in root.children if cid in nodes])
                    if agg2.get("avg_score", 0) > agg.get("avg_score", 0):
                        result["answer"] = agg2.get("answer", "")
                        result["aggregation_log"] += " | [回溯优化] " + agg2.get("log", "")
                        result["retries"] = total_retries
            else:
                result["retries"] = total_retries

            node_list = [{"id": n.id, "content": n.content[:80], "result": n.result[:200],
                          "score": n.score, "depth": n.depth, "type": n.node_type.value}
                         for n in nodes.values()]
            result["nodes"] = node_list
            result["node_count"] = len(node_list)
            result["path_depth"] = max((n.depth for n in nodes.values()), default=0)

        except Exception as e:
            logger.warning(f"GoT 异常: {e}")

        result["time_ms"] = int((time.time() - start) * 1000)
        return result

    # ─────────────────── 内部 ───────────────────

    def _decompose(self, question: str) -> List[str]:
        """问题分解"""
        prompt = (
            f"请将以下复杂问题拆解为2-4个独立的子问题，每个子问题应可直接回答。\n"
            f"直接输出子问题列表，每行一个。不要多余文字。\n\n"
            f"问题: {question[:400]}"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.2,
            )
            text = rsp.choices[0].message.content.strip()
            subs = [s.strip().lstrip('0123456789.-*· ').strip('"\'【】') for s in text.split('\n') if s.strip()]
            # 过滤太短或无效的行
            subs = [s for s in subs if len(s) > 5 and '?' in s or len(s) > 8]
            return subs[:4]
        except Exception as e:
            logger.warning(f"GoT 分解失败: {e}")
            return [question]

    def _execute_leaf(self, node: ThoughtNode, original_question: str, retry: bool = False) -> Dict:
        """执行单个思维节点"""
        ctx = "\n(注意: 请确保回答充分且准确，之前的结果不够理想)" if retry else ""
        prompt = (
            f"请独立回答以下子问题（作为复杂问题分析的一部分）:\n\n"
            f"子问题: {node.content}\n"
            f"原始问题参考: {original_question[:200]}\n\n"
            f"回答（直接、简洁、事实性）:{ctx}"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.3,
            )
            result = rsp.choices[0].message.content.strip()

            # 自评分
            score = 7.0
            score_rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content":
                    f"评分(1-10): 以下回答的完整度和准确性\n回答: {result[:300]}\n只返回数字"}],
                max_tokens=5, temperature=0.1,
            )
            sc_text = score_rsp.choices[0].message.content.strip()
            m = re.search(r'\d+', sc_text)
            if m:
                score = min(10, max(1, float(m.group())))

            return {"result": result, "score": score, "retry_count": 1 if retry else 0}
        except Exception as e:
            logger.warning(f"GoT 节点执行失败 '{node.content[:50]}': {e}")
            return {"result": "", "score": 0, "retry_count": 0}

    def _aggregate(self, question: str, child_nodes: List[ThoughtNode]) -> Dict:
        """聚合子节点结果"""
        valid = [n for n in child_nodes if n.result.strip()]
        if not valid:
            return {"answer": "", "avg_score": 0, "log": "no_valid_nodes"}

        avg_score = sum(n.score for n in valid) / len(valid)
        parts = "\n\n".join(f"[子问题] {n.content}\n[分析] {n.result[:500]}" for n in valid)

        prompt = (
            f"请将以下子问题分析结果整合为对原始问题的完整回答。\n\n"
            f"原始问题: {question[:300]}\n\n"
            f"子问题分析:\n{parts[:4000]}\n\n"
            f"整合回答（综合所有信息，连贯、完整）:"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000, temperature=0.3,
            )
            answer = rsp.choices[0].message.content.strip()
            return {"answer": answer, "avg_score": avg_score, "log": f"aggregated_{len(valid)}_nodes_avg{avg_score:.1f}"}
        except Exception as e:
            logger.warning(f"GoT 聚合失败: {e}")
            return {"answer": valid[0].result if valid else "", "avg_score": avg_score, "log": f"aggregate_fallback"}


# ── 全局实例 ──
_instance = None

def get_got_engine(llm_flash=None) -> GraphOfThoughts:
    global _instance
    if _instance is None:
        _instance = GraphOfThoughts(llm_flash)
    elif llm_flash and _instance.llm_flash is None:
        _instance.llm_flash = llm_flash
    return _instance


if __name__ == "__main__":
    gt = GraphOfThoughts()
    print("GraphOfThoughts 加载成功 (图分解→并行→聚合)")
