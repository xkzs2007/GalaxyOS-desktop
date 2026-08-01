#!/usr/bin/env python3
"""
知识编译引擎 — 自动将记忆/片段编译为结构化知识库

仿 Karpathy "LLM as knowledge compiler" 思路:
  raw/ (DAG+记忆) → LLM 编译 → wiki/ (.md 知识库)

跟原生 Karpathy 方案的区别:
  - 他手动: 人读文章 → Obsidian Web Clipper → LLM 编译
  - 我们自动: 惊讶度门控选出"值得编译"的记忆 → LLM 编译

数据流:
  高惊讶度 consolidate 的记忆
  + DAG 摘要节点（depth>=1）
  + SSM 高激活记忆
  → TopicClusterer（按主题聚类）
  → LLM 合成器（写文章）
  → knowledge/ （结构化 .md 文件）

Layer: L4 (知识层)
Author: GalaxyOS
版本: 1.0.0
创建: 2026-06-09
"""

import os
import time
import logging
import re
import hashlib
import threading
from typing import Dict, List, Optional, Any, Set
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import datetime

logger = logging.getLogger("knowledge_compiler")

# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class KnowledgeFragment:
    """一个待编译的知识片段"""
    source: str                  # DAG / MEMORY / SURPRISE
    content: str                 # 原始内容
    topic_hint: str = ""         # 主题提示
    importance: float = 0.5      # 0~1 重要性
    timestamp: float = 0.0       # 时间戳
    source_id: str = ""          # 来源 ID
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "content": self.content[:200],
            "topic_hint": self.topic_hint,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "source_id": self.source_id,
            "tags": self.tags,
        }


@dataclass
class KnowledgeArticle:
    """编译后的知识文章"""
    title: str
    content: str                  # 完整 markdown
    summary: str                  # 摘要
    tags: List[str]
    backlinks: List[str]          # 关联文章 title
    source_fragments: List[str]   # 来源 IDs
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_markdown(self, backlink_articles: List[str]) -> str:
        """渲染为 Obsidian 兼容 markdown"""
        lines = [
            "---",
            f"title: {self.title}",
            f"tags: [{', '.join(self.tags)}]",
            f"created: {datetime.datetime.fromtimestamp(self.created_at).strftime('%Y-%m-%d %H:%M')}",
            f"updated: {datetime.datetime.fromtimestamp(self.updated_at).strftime('%Y-%m-%d %H:%M')}",
            "---",
            "",
            f"{self.content}",
            "",
            "---",
            "## 关联",
        ]
        for bl in self.backlinks:
            if bl in backlink_articles:
                lines.append(f"- [[{bl}]]")
            else:
                lines.append(f"- {bl}")
        for bl in backlink_articles:
            if bl not in self.backlinks:
                lines.append(f"- [[{bl}]]")
        lines.append("")
        return "\n".join(lines)


# ============================================================================
# 主题聚类
# ============================================================================

class TopicClusterer:
    """简单的基于关键词的主题聚类器

    用 TF 风格的词频 + 预定义主题词典做软聚类。
    硬件限制（CPU only，无 GPU），不用 embeddings。
    """

    # 预定义主题词典（可扩展）
    TOPIC_KEYWORDS = {
        "memory_system": ["记忆", "memory", "突触", "synapse", "LTP", "LTD", "巩固", "consolidation"],
        "retrieval": ["检索", "召回", "search", "recall", "查询", "query", "RRF"],
        "neural_network": ["神经网络", "neural", "CfC", "LTC", "GAT", "神经元", "权重"],
        "time_series": ["时间序列", "预测", "SSM", "Mamba", "时序", "趋势", "activation"],
        "gate_mechanism": ["门控", "gate", "惊讶度", "surprise", "Titans", "调制"],
        "architecture": ["架构", "architecture", "DAG", "Layer", "层次", "模块", "组件"],
        "knowledge": ["知识", "knowledge", "wiki", "文档", "编译", "compiler"],
        "communication": ["通信", "A2A", "消息", "message", "路由", "总线", "广播"],
        "math_formula": ["公式", "算法", "algorithm", "损失", "梯度", "Fisher", "EWC"],
        "system_health": ["健康", "监控", "心跳", "heartbeat", "状态", "日志", "错误"],
    }

    def __init__(self, min_term_freq: int = 1):
        self.min_term_freq = min_term_freq

    def _extract_terms(self, text: str) -> Set[str]:
        """提取关键词（中文+英文）"""
        terms = set()
        # 英文词
        for w in re.findall(r'\b[a-zA-Z_]{3,}\b', text):
            terms.add(w.lower())
        # 中文词（按字符 2-gram 简单处理）
        for i in range(len(text) - 1):
            pair = text[i:i+2]
            if '\u4e00' <= pair[0] <= '\u9fff' and '\u4e00' <= pair[1] <= '\u9fff':
                terms.add(pair)
        return terms

    def cluster(self, fragments: List[KnowledgeFragment]) -> Dict[str, List[KnowledgeFragment]]:
        """将碎片聚类到主题

        Returns:
            {topic_name: [fragments]}
        """
        clusters: Dict[str, List[KnowledgeFragment]] = defaultdict(list)

        for frag in fragments:
            text = (frag.content + " " + frag.topic_hint + " " + " ".join(frag.tags)).lower()
            best_topic = "uncategorized"
            best_score = 0

            for topic, keywords in self.TOPIC_KEYWORDS.items():
                score = sum(1 for kw in keywords if kw.lower() in text)
                if score > best_score:
                    best_score = score
                    best_topic = topic

            clusters[best_topic].append(frag)

        return dict(clusters)


# ============================================================================
# 知识编译引擎
# ============================================================================

class KnowledgeCompiler:
    """知识编译引擎

    从多源收集碎片 → 聚类 → 合成文章 → 写 .md 文件

    用法:
        kc = KnowledgeCompiler(knowledge_dir="knowledge")

        # 加入碎片
        kc.add_fragment(source="DAG", content="...", topic_hint="memory")

        # 编译
        articles = kc.compile()

        # 写出
        kc.write_all(articles)
    """

    def __init__(
        self,
        knowledge_dir: str = "knowledge",
        max_fragments_per_compile: int = 50,
        min_articles_per_cluster: int = 2,
        llm_callback = None,
    ):
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

        self.max_fragments = max_fragments_per_compile
        self.min_articles = min_articles_per_cluster
        self.llm_callback = llm_callback  # 合成文章用

        self._fragments: List[KnowledgeFragment] = []
        self._compiled_hashes: Set[str] = set()  # 已编译的内容 hash
        self._clusterer = TopicClusterer()
        self._lock = threading.Lock()

        # 加载已有文章列表
        self._existing_articles: Dict[str, KnowledgeArticle] = {}
        self._scan_existing()

    # ── 碎片管理 ──────────────────────────────────────────────────

    def add_fragment(
        self,
        source: str,
        content: str,
        topic_hint: str = "",
        importance: float = 0.5,
        timestamp: Optional[float] = None,
        source_id: str = "",
        tags: Optional[List[str]] = None,
    ) -> None:
        """添加知识碎片"""
        content_hash = hashlib.md5(content.encode()).hexdigest()[:16]
        if content_hash in self._compiled_hashes:
            return  # 跳过重复

        frag = KnowledgeFragment(
            source=source,
            content=content,
            topic_hint=topic_hint,
            importance=importance,
            timestamp=timestamp or time.time(),
            source_id=source_id,
            tags=tags or [],
        )
        with self._lock:
            self._fragments.append(frag)
            # 只保留最新的 N 条
            if len(self._fragments) > self.max_fragments * 3:
                self._fragments = self._fragments[-self.max_fragments * 3:]

    def add_dag_node(self, node_dict: dict) -> None:
        """从 DAG 节点添加碎片"""
        content = node_dict.get("content", "")
        if not content:
            return
        self.add_fragment(
            source="DAG",
            content=content,
            topic_hint=node_dict.get("metadata", {}).get("topic", ""),
            importance=node_dict.get("priority", 0) / 2.0,
            timestamp=node_dict.get("timestamp", 0),
            source_id=node_dict.get("node_id", ""),
        )

    # ── 编译 ──────────────────────────────────────────────────────

    def compile(self) -> List[KnowledgeArticle]:
        """执行一次编译循环

        1. 取待编译碎片
        2. 聚类
        3. 每组合成文章
        4. 标记已编译
        """
        with self._lock:
            fragments = list(self._fragments)

        if not fragments:
            logger.info("没有新碎片，跳过编译")
            return []

        logger.info(f"编译 {len(fragments)} 条碎片")

        # 聚类
        clusters = self._clusterer.cluster(fragments)
        logger.info(f"聚类: {len(clusters)} 个主题")

        # 合成
        articles = []
        for topic, topic_frags in clusters.items():
            if len(topic_frags) < self.min_articles:
                logger.debug(f"主题 {topic} 碎片不足 ({len(topic_frags)} < {self.min_articles})")
                continue

            article = self._synthesize(topic, topic_frags)
            if article:
                articles.append(article)

        # 标记已编译
        with self._lock:
            for frag in fragments:
                chash = hashlib.md5(frag.content.encode()).hexdigest()[:16]
                self._compiled_hashes.add(chash)

        logger.info(f"合成 {len(articles)} 篇文章")
        return articles

    def _synthesize(self, topic: str, fragments: List[KnowledgeFragment]) -> Optional[KnowledgeArticle]:
        """合成一篇文章（用 LLM 或模板）"""
        # 按重要性排序
        fragments.sort(key=lambda f: -f.importance)

        # 提取摘要内容
        sources = [f"{f.source}({f.source_id})" for f in fragments if f.source_id]
        content_parts = [f.content[:500] for f in fragments[:10]]

        # 合成标题
        topic_title_map = {
            "memory_system": "记忆系统",
            "retrieval": "检索机制",
            "neural_network": "神经网络",
            "time_series": "时序预测",
            "gate_mechanism": "门控机制",
            "architecture": "系统架构",
            "knowledge": "知识库",
            "communication": "通信层",
            "math_formula": "算法公式",
            "system_health": "系统运行",
        }
        title = topic_title_map.get(topic, topic.replace("_", " ").title())

        # 如果有 LLM 回调，用 LLM 合成
        if self.llm_callback:
            try:
                return self._llm_synthesize(title, content_parts, sources, fragments)
            except Exception as e:
                logger.warning(f"LLM 合成失败: {e}，用模板降级")

        # 降级：模板合成
        return self._template_synthesize(title, content_parts, sources, fragments, topic)

    def _llm_synthesize(self, title, contents, sources, fragments) -> KnowledgeArticle:
        """LLM 合成"""
        prompt = f"""请将以下知识碎片合成一篇结构清晰的知识文章。

文章标题：{title}

总碎片数：{len(fragments)}
来源：{', '.join(set(f.source for f in fragments))}

碎片内容（按重要性排序）：
{chr(10).join(f'- {c[:300]}' for c in contents[:8])}

请输出 markdown 格式，包含：
1. 简要摘要
2. 核心要点
3. 关键细节
4. 标签

用中文写。"""

        result = self.llm_callback(prompt)
        # 提取标题、摘要、内容
        summary = result[:300] if len(result) > 300 else result
        tags = list(set(frag.source for frag in fragments[:5]))
        backlinks = [f.source_id for f in fragments[:5] if f.source_id]

        return KnowledgeArticle(
            title=title,
            content=result,
            summary=summary,
            tags=tags,
            backlinks=backlinks[:8],
            source_fragments=sources,
            created_at=time.time(),
            updated_at=time.time(),
        )

    def _template_synthesize(self, title, contents, sources, fragments, topic) -> KnowledgeArticle:
        """模板降级合成"""
        lines = [
            "## 概述",
            "",
            f"本文档由 {len(fragments)} 条碎片合成，覆盖主题 \"{topic}\"。",
            "",
            "## 核心内容",
            "",
        ]
        for i, c in enumerate(contents[:5]):
            lines.append(f"### 片段 {i+1}")
            lines.append("")
            lines.append(c)
            lines.append("")

        lines.append("## 来源")
        lines.append("")
        for s in set(sources):
            lines.append(f"- {s}")
        lines.append("")

        tags = list(set(frag.source for frag in fragments[:5]))
        backlinks = [f.source_id for f in fragments[:5] if f.source_id]

        content = "\n".join(lines)
        return KnowledgeArticle(
            title=title,
            content=content,
            summary=f"关于{topic}的知识文章，由{len(fragments)}条碎片合成",
            tags=tags,
            backlinks=backlinks[:8],
            source_fragments=sources,
            created_at=time.time(),
            updated_at=time.time(),
        )

    # ── 写出 ──────────────────────────────────────────────────────

    def write_all(self, articles: List[KnowledgeArticle]) -> List[str]:
        """写出文章到 knowledge/ 目录

        Returns:
            写出的文件路径列表
        """
        written = []
        all_titles = list(self._existing_articles.keys()) + [a.title for a in articles]

        for article in articles:
            filepath = self.knowledge_dir / f"{article.title.replace(' ', '_')}.md"
            md = article.to_markdown(all_titles)
            filepath.write_text(md, encoding="utf-8")
            self._existing_articles[article.title] = article
            written.append(str(filepath))
            logger.info(f"写出: {filepath}")

        # 更新索引
        index_path = self._write_index(list(self._existing_articles.values()))
        if index_path:
            written.append(index_path)

        return written

    def _write_index(self, articles: List[KnowledgeArticle]) -> Optional[str]:
        """写出索引文件"""
        if not articles:
            return None

        articles.sort(key=lambda a: -a.updated_at)

        lines = [
            "---",
            "title: 知识库索引",
            "tags: [index, knowledge]",
            f"created: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "---",
            "",
            "# 知识库索引",
            "",
            f"共 {len(articles)} 篇文章",
            "",
        ]

        for a in articles:
            updated = datetime.datetime.fromtimestamp(a.updated_at).strftime('%m-%d %H:%M')
            lines.append(f"- [[{a.title}]] — {a.summary[:80]} (更新: {updated})")

        lines.append("")
        index_path = self.knowledge_dir / "_index.md"
        index_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"索引更新: {index_path}")
        return str(index_path)

    # ── 扫描已有 ──────────────────────────────────────────────────

    def _scan_existing(self) -> None:
        """扫描已有文章"""
        if not self.knowledge_dir.exists():
            return
        for f in self.knowledge_dir.glob("*.md"):
            if f.name == "_index.md":
                continue
            content = f.read_text(encoding="utf-8", errors="replace")
            title = f.stem
            self._existing_articles[title] = KnowledgeArticle(
                title=title,
                content=content,
                summary=content[:100],
                tags=[],
                backlinks=[],
                source_fragments=[],
                updated_at=f.stat().st_mtime,
            )

    # ── 清空 ──────────────────────────────────────────────────────

    def clear_fragments(self) -> None:
        """清空碎片队列（编译后调用）"""
        with self._lock:
            self._fragments.clear()

    # ── 统计 ──────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "fragments_pending": len(self._fragments),
                "compiled_hashes": len(self._compiled_hashes),
                "articles_count": len(self._existing_articles),
                "articles": [a.title for a in self._existing_articles.values()],
                "knowledge_dir": str(self.knowledge_dir),
            }


# ============================================================================
# 集成适配器 — 从 GalaxyOS 各模块收集碎片
# ============================================================================

class GalaxyOSKnowledgeAdapter:
    """从 GalaxyOS 现有基础设施收集碎片的适配器"""

    def __init__(self, compiler: KnowledgeCompiler):
        self.compiler = compiler

    def collect_from_memory_gate(self, gate_result: dict) -> None:
        """从记忆门控收集 consolidate 的内容"""
        novel_ids = gate_result.get("novel_ids", [])
        if not novel_ids:
            return
        self.compiler.add_fragment(
            source="SURPRISE",
            content=f"高惊讶度记忆: {', '.join(str(i) for i in novel_ids[:10])}",
            topic_hint="gate_mechanism",
            importance=0.8,
            tags=["gate", "consolidate", "surprise"],
        )

    def collect_from_ssm(self, ssm_prediction: dict) -> None:
        """从 SSM 预测器收集高激活记忆"""
        hot = ssm_prediction.get("hot", [])
        for item in hot[:5]:
            self.compiler.add_fragment(
                source="SSM",
                content=f"高频记忆 {item.get('memory_id', '')}: "
                        f"激活度={item.get('activation', 0)}, "
                        f"趋势={item.get('trend', 'stable')}",
                topic_hint="time_series",
                importance=min(1.0, item.get('activation', 0) / 10),
                tags=["ssm", "hot", item.get('trend', 'stable')],
                source_id=str(item.get('memory_id', '')),
            )

    def collect_from_dag(self, dag_nodes: List[dict]) -> None:
        """从 DAG 收集摘要节点"""
        for node in dag_nodes:
            if node.get("is_summary", False) and node.get("depth", 0) >= 1:
                self.compiler.add_dag_node(node)


# ============================================================================
# 演示
# ============================================================================

def demo():
    """演示知识编译流程"""
    kc = KnowledgeCompiler(knowledge_dir="/tmp/knowledge_demo")

    # 模拟碎片
    fragments_data = [
        ("DAG", "突触网络采用 Hebbian LTP/LTD 规则调整权重", "neural_network", 0.7),
        ("DAG", "LTP 增强的公式: Δw = η * pre * post", "math_formula", 0.8),
        ("MEMORY", "惊讶度门控判断记忆是否需要 consolidate", "gate_mechanism", 0.9),
        ("SURPRISE", "高惊讶度触发 consolidate 流程", "gate_mechanism", 0.6),
        ("SSM", "高频记忆 mem_hot_A 激活度 9.84, 趋势 stable", "time_series", 0.5),
        ("DAG", "GAT 全局图注意力网络处理 3093 个神经元", "neural_network", 0.75),
        ("SSM", "低频记忆 mem_cold_X 激活度 1.95, 趋势 falling", "time_series", 0.3),
        ("DAG", "DAGMessageBus 支持 send/poll/ack 路由", "architecture", 0.6),
        ("MEMORY", "A2A 消息通过 DAG 节点传输", "communication", 0.55),
        ("DAG", "CompositePredictor 融合 SSM + 共现预测", "gate_mechanism", 0.85),
    ]

    for source, content, topic, imp in fragments_data:
        kc.add_fragment(
            source=source,
            content=content,
            topic_hint=topic,
            importance=imp,
        )

    print("=== 碎片收集完成 ===")
    stats = kc.get_stats()
    print(f"待编译: {stats['fragments_pending']} 条")
    print(f"主题: {list(kc._clusterer.TOPIC_KEYWORDS.keys())}")

    # 编译
    articles = kc.compile()
    print(f"\n=== 编译完成: {len(articles)} 篇文章 ===")
    for a in articles:
        print(f"  [{a.title}] {a.summary[:60]}")
        print(f"    标签: {a.tags}")
        print(f"    关联: {a.backlinks[:3]}")

    # 写出
    written = kc.write_all(articles)
    print(f"\n=== 写出 {len(written)} 个文件 ===")
    for w in written:
        size = os.path.getsize(w)
        print(f"  {w} ({size} bytes)")


def main():
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        demo()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
