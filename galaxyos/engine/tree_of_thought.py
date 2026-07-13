#!/usr/bin/env python3
"""
Tree-of-Thought (ToT) — 多路径探索 + 回溯推理引擎

Yao et al. (2023) arXiv:2305.10601

核心机制:
1. 多分支生成 — 每层探索多个候选思考方向
2. 启发式评估 — 快速评分每个候选的质量
3. BFS/DFS 搜索 — 系统化遍历思考树
4. 回溯剪枝 — 低分路径自动回溯重试
5. 自我精炼 — 最终路径经过一轮优化

适用场景:
- 复杂推理（需要多步探索的问题）
- 需要权衡多个方向的分析
- 不确定场景下的决策推理
"""

import json
import time
import logging
import re
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ============================================================================
# ToT 节点数据模型
# ============================================================================

@dataclass
class ThoughtNode:
    """ToT 单一思考节点"""
    thought: str                     # 思考内容
    value: float = 0.0               # 评估分数 [0, 1]
    depth: int = 0                   # 在树中的深度
    parent_idx: Optional[int] = None # 父节点索引
    children_indices: List[int] = field(default_factory=list)  # 子节点索引
    is_backtrack: bool = False       # 是否为回溯标记
    refined: str = ""                # 精炼后的版本


@dataclass
class SearchPath:
    """完整路径"""
    steps: List[ThoughtNode] = field(default_factory=list)
    score: float = 0.0


class TreeOfThought:
    """
    Tree-of-Thought 多路径搜索

    以 BFS 方式逐层探索：每层从当前活跃路径生成 max_branches 个候选
    → 逐个评估（Flash prompt）→ 保留 top-k → 继续下一层
    → 低于阈值则回溯，探索其他分支
    """

    def __init__(
        self,
        llm_flash=None,
        llm_flash_model: str = "deepseek-v4-flash",
        max_branches: int = 3,
        max_depth: int = 3,
        top_k: int = 2,
        eval_threshold: float = 0.3,
        temperature_generate: float = 0.8,
        temperature_eval: float = 0.1,
    ):
        """
        Args:
            llm_flash: LLM Flash client (OpenAI-compatible)
            llm_flash_model: LLM 模型名
            max_branches: 每层最多生成分支数
            max_depth: 最大思考深度
            top_k: 每层保留最佳分支数
            eval_threshold: 评估阈值，低于则触发回溯
            temperature_generate: 生成时的温度（越高越多样）
            temperature_eval: 评估时的温度（越低越稳定）
        """
        self.llm_flash = llm_flash
        self.llm_flash_model = llm_flash_model
        self.max_branches = max_branches
        self.max_depth = max_depth
        self.top_k = min(top_k, max_branches)
        self.eval_threshold = eval_threshold
        self.temperature_generate = temperature_generate
        self.temperature_eval = temperature_eval

        # 用本地缓存，不引入外部 API
        self._generation_cache: Dict[str, str] = {}
        self._eval_cache: Dict[str, float] = {}

        # 完整的探索记录
        self._all_nodes: List[ThoughtNode] = []
        self._exploration_trace: List[Dict] = []
        self._backtrack_count: int = 0

    # ========================================================================
    # 主入口
    # ========================================================================

    def search(self, query: str, context: str = "") -> Dict:
        """
        ToT 搜索主入口

        Returns:
            {
                "best_path": [{"thought": str, "value": float, "depth": int}, ...],
                "all_paths": [...],
                "best_score": float,
                "total_nodes_explored": int,
            }
        """
        self._all_nodes = []
        self._exploration_trace = []
        self._backtrack_count = 0

        # 1. 从根状态开始（query + context 合为根思考）
        root_thought = f"问题: {query}"
        if context:
            root_thought += f"\n背景: {context}"

        root_node = ThoughtNode(
            thought=root_thought,
            value=1.0,  # 根节点满值
            depth=0,
        )
        self._all_nodes.append(root_node)

        # 2. BFS 搜索
        start_time = time.time()
        best_path = self._bfs_search(query, context)
        elapsed = time.time() - start_time

        # 3. 如果 BFS 没找到路径，降级为 DFS
        if not best_path or (best_path and best_path[-1].value < self.eval_threshold):
            logger.warning(f"BFS 未找到优质路径 (best={best_path[-1].value if best_path else 'N/A'}), 降级为 DFS")
            best_path = self._dfs_search(query, context)

        # 4. 自我精炼最终路径
        if best_path and best_path[-1].value >= self.eval_threshold:
            best_path = self._self_refine(best_path, query)

        # 5. 构建返回格式
        best_score = best_path[-1].value if best_path else 0.0
        all_paths = self._extract_all_paths()

        return {
            "best_path": [
                {
                    "thought": n.thought,
                    "value": n.value,
                    "depth": n.depth,
                }
                for n in best_path
            ] if best_path else [],
            "all_paths": [
                [
                    {
                        "thought": n.thought,
                        "value": n.value,
                        "depth": n.depth,
                    }
                    for n in path.steps
                ]
                for path in all_paths
            ],
            "best_score": best_score,
            "total_nodes_explored": len(self._all_nodes),
            "backtrack_count": self._backtrack_count,
            "elapsed_seconds": round(elapsed, 3),
            "search_strategy": "bfs_with_dfs_fallback",
        }

    # ========================================================================
    # BFS 搜索
    # ========================================================================

    def _bfs_search(self, query: str, context: str) -> List[ThoughtNode]:
        """BFS 层次探索"""
        current_frontier = [(0, 0.0)]  # (node_idx, cumulative_score)
        best_leaf = None
        best_leaf_score = -1.0

        for depth in range(1, self.max_depth + 1):
            if not current_frontier:
                logger.info(f"BFS depth={depth}: 无活跃节点，搜索终止")
                break

            next_frontier = []
            logger.info(f"BFS depth={depth}: 当前前沿 {len(current_frontier)} 节点")

            for node_idx, cum_score in current_frontier:
                node = self._all_nodes[node_idx]

                # 生成候选思考
                thoughts = self._generate_thoughts(
                    state=node.thought,
                    query=query,
                    count=self.max_branches,
                )

                if not thoughts:
                    continue

                # 评估每个候选
                evaluated = []
                for t in thoughts:
                    val = self._evaluate(t, query)
                    evaluated.append((t, val, cum_score + val))

                # 排序，保留 top-k
                evaluated.sort(key=lambda x: x[2], reverse=True)
                kept = evaluated[:self.top_k]

                for t, val, new_cum in kept:
                    child_idx = len(self._all_nodes)
                    child = ThoughtNode(
                        thought=t,
                        value=val,
                        depth=depth,
                        parent_idx=node_idx,
                    )
                    self._all_nodes.append(child)
                    node.children_indices.append(child_idx)
                    next_frontier.append((child_idx, new_cum))

                    # 追踪
                    self._exploration_trace.append({
                        "depth": depth,
                        "parent_idx": node_idx,
                        "child_idx": child_idx,
                        "thought": t[:120],
                        "value": val,
                        "action": "expand",
                    })

                    # 更新最好叶子
                    if val > best_leaf_score:
                        best_leaf_score = val
                        best_leaf = child_idx

                # 检查是否所有分支都低于阈值 — 触发回溯
                if not evaluated or evaluated[0][1] < self.eval_threshold:
                    self._backtrack_count += 1
                    self._exploration_trace.append({
                        "depth": depth,
                        "parent_idx": node_idx,
                        "thought": node.thought[:80],
                        "action": "backtrack",
                        "reason": f"all_branches_below_threshold({self.eval_threshold})",
                    })
                    # 标记回溯节点
                    bt_node = ThoughtNode(
                        thought=f"[回溯] depth={depth}: 当前路径评分不足",
                        value=0.0,
                        depth=depth,
                        parent_idx=node_idx,
                        is_backtrack=True,
                    )
                    self._all_nodes.append(bt_node)

            current_frontier = next_frontier

            # 早停：如果当前层有高评分路径且足够好
            if current_frontier and current_frontier[0][1] >= self.max_depth * 0.8:
                logger.info(f"BFS depth={depth} 早停: 已有高评分路径 {current_frontier[0][1]:.3f}")
                break

        # 重建最佳路径
        if best_leaf is not None:
            path = self._reconstruct_path(best_leaf)
            if path:
                return path

        # 若未找到，返回评分最高的叶子
        if current_frontier:
            best_leaf_idx = max(current_frontier, key=lambda x: x[1])[0]
            return self._reconstruct_path(best_leaf_idx)

        return [root for root in self._all_nodes if root.depth == 0]

    # ========================================================================
    # DFS 搜索（BFS 兜底）
    # ========================================================================

    def _dfs_search(self, query: str, context: str) -> List[ThoughtNode]:
        """DFS 深度优先（BFS 找不到优质路径时的兜底）"""
        # 清空之前 BFS 的结果，从根重新开始深度优先
        root = self._all_nodes[0] if self._all_nodes else self._all_nodes[0]

        # 简单起见：用根状态直接生成完整路径
        thoughts = self._generate_thoughts(
            state=root.thought,
            query=query,
            count=self.max_branches * 2,
        )

        best_path = None
        best_val = -1.0

        for t in thoughts[:3]:  # 只试前 3 个
            val = self._evaluate(t, query)
            path = [root, ThoughtNode(thought=t, value=val, depth=1)]
            if val > best_val:
                best_val = val
                best_path = path

        if best_path:
            return best_path

        # 最后的兜底
        simple_thought = self._generate_simple_answer(query)
        return [
            root,
            ThoughtNode(thought=simple_thought, value=0.5, depth=1),
        ]

    # ========================================================================
    # LLM 调用的核心方法
    # ========================================================================

    def _generate_thoughts(self, state: str, query: str, count: int) -> List[str]:
        """
        给定当前状态，生成下 N 步可能的思考

        使用 Flash LLM prompt 生成。
        不使用外部搜索 API，仅 LLM 内部知识。
        """
        cache_key = f"gen_{query}_{state[:200]}_{count}"
        if cache_key in self._generation_cache:
            # cache 只是标记，返回不同结果需要重新生成
            pass

        prompt = (
            f"你正在解决以下问题。当前已经有的思考:\n\n"
            f"{state[:800]}\n\n"
            f"请从当前状态出发，给出 {count} 个不同的、具体的下步思考方向。\n"
            f"每个思考应该是对解决问题的具体推进，而不是泛泛的建议。\n\n"
            f"仅返回编号列表，每行一个思考，不要其他内容:\n"
            f"1. \n2. \n3. \n"
        )

        thoughts = self._llm_generate(prompt, temperature=self.temperature_generate)
        parsed = self._parse_numbered_list(thoughts, count)
        return parsed[:count]

    def _evaluate(self, thought: str, query: str) -> float:
        """
        评估某步思考的质量（0~1）

        用 Flash prompt: "这个思考对解决问题有多大帮助？"
        """
        cache_key = f"eval_{query}_{thought[:150]}"
        if cache_key in self._eval_cache:
            return self._eval_cache[cache_key]

        prompt = (
            f"问题: {query[:300]}\n\n"
            f"下面这个思考步骤对解决问题有多大帮助？\n"
            f"思考: {thought[:300]}\n\n"
            f"请严格只用 0.0 到 1.0 之间的一个数字评分。\n"
            f"0.0 = 完全没有帮助, 1.0 = 非常关键。\n"
            f"仅返回数字，不要其他内容:"
        )

        response = self._llm_generate(prompt, temperature=self.temperature_eval)
        val = self._parse_score(response)
        self._eval_cache[cache_key] = val
        return val

    def _self_refine(self, path: List[ThoughtNode], query: str) -> List[ThoughtNode]:
        """
        对最佳路径进行一轮精炼

        将路径所有步骤输入 LLM，要求整合为连贯推理链。
        """
        if not path:
            return path

        steps_text = "\n".join([
            f"步骤 {i+1}: {n.thought}"
            for i, n in enumerate(path)
        ])

        prompt = (
            f"问题: {query[:300]}\n\n"
            f"以下是对该问题的推理步骤链:\n\n"
            f"{steps_text}\n\n"
            f"请将以上推理步骤精炼为一条逻辑连贯、表达清晰的分析。\n"
            f"保留关键逻辑和结论，去除冗余。\n"
            f"精炼结果:"
        )

        refined = self._llm_generate(prompt, temperature=0.3)

        if refined and len(refined) > 10:
            # 精炼后的最终步骤
            refined_node = ThoughtNode(
                thought=refined,
                value=path[-1].value * 1.05,  # 略提分
                depth=path[-1].depth + 1,
                parent_idx=self._all_nodes.index(path[-1]) if path[-1] in self._all_nodes else None,
                refined=refined,
            )
            self._all_nodes.append(refined_node)
            return path + [refined_node]

        return path

    def _generate_simple_answer(self, query: str) -> str:
        """生成简单回答（兜底用）"""
        prompt = (
            f"问题: {query[:400]}\n\n"
            f"请给出一个直接、具体的回答。"
        )
        return self._llm_generate(prompt, temperature=0.3)

    # ========================================================================
    # 辅助方法
    # ========================================================================

    def _reconstruct_path(self, leaf_idx: int) -> List[ThoughtNode]:
        """从叶子节点回溯重建整条路径"""
        path = []
        current_idx = leaf_idx

        while current_idx is not None:
            node = self._all_nodes[current_idx]
            if node.is_backtrack:
                current_idx = node.parent_idx
                continue
            path.append(node)
            current_idx = node.parent_idx

        path.reverse()
        return path

    def _extract_all_paths(self) -> List[SearchPath]:
        """提取所有从根到叶子的路径"""
        leaves = [n for n in self._all_nodes
                  if not n.children_indices and not n.is_backtrack]

        paths = []
        for leaf in leaves:
            path_nodes = self._reconstruct_path(self._all_nodes.index(leaf))
            if len(path_nodes) > 1:  # 至少包含根 + 1 个思考
                avg_score = sum(n.value for n in path_nodes) / len(path_nodes)
                paths.append(SearchPath(steps=path_nodes, score=avg_score))

        paths.sort(key=lambda p: p.score, reverse=True)
        return paths

    def _backtrack(self, path: List[ThoughtNode], history: List[List[ThoughtNode]]) -> List[ThoughtNode]:
        """
        回溯：当前路径走不通，换一条

        公开接口，供外部调用。
        """
        if not history:
            return path

        self._backtrack_count += 1

        # 尝试从历史中找一条未完全探索的路径
        for alt_path in history:
            if alt_path and alt_path[-1].value >= self.eval_threshold:
                logger.info(f"回溯: 切换到历史路径, score={alt_path[-1].value:.3f}")
                return alt_path

        return path

    def _llm_generate(self, prompt: str, temperature: float = 0.7) -> str:
        """
        通过 Flash LLM 生成文本

        如果 llm_flash 不可用，使用规则兜底。
        """
        if not self.llm_flash:
            logger.warning("llm_flash 未初始化，使用规则兜底")
            return self._rule_generate(prompt, temperature)

        try:
            import openai
            response = self.llm_flash.chat.completions.create(
                model=self.llm_flash_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
                temperature=temperature,
            )
            text = response.choices[0].message.content.strip()
            return text
        except Exception as e:
            logger.warning(f"LLM 调用失败: {e}")
            return self._rule_generate(prompt, temperature)

    def _rule_generate(self, prompt: str, temperature: float) -> str:
        """规则兜底生成（无 LLM 时使用）"""
        # 解析 prompt 中是否有问题
        # 简单模板生成
        lines = prompt.split('\n')
        # 找问题描述
        query = ""
        for line in lines:
            if line.startswith("问题:") or line.startswith("你正在解决"):
                query = line[:100]

        # 根据不同类型 prompt 生成对应回复
        if "请给出" in prompt or "精炼" in prompt:
            return "基于当前分析，建议优先考虑实际可行的方案。关键在于验证假设、分步推进。"

        if "评估" in prompt or "评分" in prompt:
            return "0.6"

        if "下步思考" in prompt or "不同的" in prompt:
            thoughts = []
            count = 3
            for i in range(count):
                thoughts.append(f"{i+1}. 进一步分析问题的关键约束条件和可用资源")
            return "\n".join(thoughts)

        return "针对问题进行分析，需要考虑多个维度。"

    # ========================================================================
    # 解析辅助
    # ========================================================================

    def _parse_numbered_list(self, text: str, expected: int) -> List[str]:
        """解析带编号的列表"""
        items = []
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # 匹配 "1. xxx" 或 "1、xxx" 或 "- xxx"
            match = re.match(r'^[\d]+[\.\、\)]\s*(.+)$', line)
            if match:
                items.append(match.group(1).strip())
            elif line.startswith('- ') or line.startswith('* '):
                items.append(line[2:].strip())

        # 如果没解析到，尝试按换行分割
        if len(items) < 2:
            items = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 10]

        return items[:expected]

    def _parse_score(self, text: str) -> float:
        """解析评分（0.0 ~ 1.0）"""
        if not text:
            return 0.5

        # 匹配浮点数
        matches = re.findall(r'(\d+\.?\d*)', text)
        for m in matches:
            try:
                val = float(m)
                if 0.0 <= val <= 1.0:
                    return val
                elif 0 <= val <= 100:
                    return val / 100.0
            except ValueError:
                continue

        # 默认值
        return 0.5

    # ========================================================================
    # 公共接口
    # ========================================================================

    def get_thinking_trace(self) -> List[Dict]:
        """返回完整的树结构探索记录"""
        return self._exploration_trace

    def set_llm(self, llm_flash, model: str = "deepseek-v4-flash"):
        """设置/更新 LLM 客户端"""
        self.llm_flash = llm_flash
        self.llm_flash_model = model

    def get_stats(self) -> Dict:
        """获取搜索统计"""
        return {
            "total_nodes": len(self._all_nodes),
            "max_depth": self.max_depth,
            "max_branches": self.max_branches,
            "backtrack_count": self._backtrack_count,
            "exploration_trace_length": len(self._exploration_trace),
            "generation_cache_size": len(self._generation_cache),
            "eval_cache_size": len(self._eval_cache),
        }


# ============================================================================
# 便捷实例化
# ============================================================================

def get_tree_of_thought(
    llm_flash=None,
    max_branches: int = 3,
    max_depth: int = 3,
) -> TreeOfThought:
    """
    获取 ToT 实例

    尝试从 xiaoyi_claw_api 获取已初始化的 LLM Flash 客户端。
    """
    if llm_flash is None:
        try:
            from xiaoyi_claw_api import get_global_xiaoyi_claw
            xc = get_global_xiaoyi_claw()
            if xc and hasattr(xc, 'llm_flash') and xc.llm_flash:
                llm_flash = xc.llm_flash
                model = getattr(xc, '_llm_flash_model', 'deepseek-v4-flash')
                return TreeOfThought(
                    llm_flash=llm_flash,
                    llm_flash_model=model,
                    max_branches=max_branches,
                    max_depth=max_depth,
                )
        except Exception as e:
            logger.warning(f"从 xiaoyi_claw_api 获取 llm_flash 失败: {e}")

    return TreeOfThought(
        llm_flash=llm_flash,
        max_branches=max_branches,
        max_depth=max_depth,
    )


# ============================================================================
# 快速测试
# ============================================================================

def test_tot():
    """快速验证"""
    tot = TreeOfThought()
    result = tot.search(
        "如何在华为云EulerOS上优化PyTorch性能",
        context="CPU环境，AVX-512可用"
    )
    assert 'best_path' in result, f"Missing best_path, got keys: {list(result.keys())}"
    assert len(result['best_path']) > 0, "Empty best_path"
    print(f"Best path: {len(result['best_path'])} steps, score={result['best_score']}")
    print(f"Total nodes: {result['total_nodes_explored']}, backtracks: {result['backtrack_count']}")
    print(f"Elapsed: {result.get('elapsed_seconds', 'N/A')}s")
    return result


if __name__ == "__main__":
    test_tot()

__all__ = [
    'TreeOfThought',
    'ThoughtNode',
    'SearchPath',
    'get_tree_of_thought',
]
