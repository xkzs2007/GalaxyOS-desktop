"""
层次化记忆管理引擎 —— 记忆调度 + 遗忘决策

论文参考: MemGPT: Towards LLMs as Operating Systems (2024)
https://arxiv.org/abs/2310.08560

核心改进:
  1. 将记忆分为工作集(Working Set)、近期集(Recent Set)、归档集(Archive Set)
  2. 智能调度: LLM 自主决定何时翻记忆、合并、遗忘
  3. 遗忘策略: 不再全量召回，而是按重要性+时效性+关联性加权

与现有系统关系:
  - 接入 R-CCAM _memory_phase: 在记忆阶段之前执行记忆调度
  - 与 ConsolidationEngine 互补: 巩固是"写入"优化，层次管理是"读出"优化
  - 替代全量召回策略: 从"所有记忆都重要"变为"选择重要记忆"

Author: 小艺 Claw
"""

import json
import os
import time
import sqlite3
import logging
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from galaxyos.shared.paths import galaxyos_home

logger = logging.getLogger(__name__)

# ─── 常量 ───

# 记忆层级阈值
WORKING_SET_SIZE = 20       # 工作集: 最近/最重要的 20 条
RECENT_SET_MAX = 200        # 近期集: 保留最近 200 条
ARCHIVE_SET_HOURS = 72      # 归档集: 超过 72 小时未访问的

# 遗忘判定
FORGET_IMPORTANCE_MIN = 3.0   # 重要性低于此值可遗忘
FORGET_ACCESS_DAYS = 14      # 超过 14 天未访问可遗忘
FORGET_SIMILARITY = 0.85     # 相似度高于此值视为重复

# 触发条件
AUTO_MERGE_THRESHOLD = 0.82  # 两条记忆相似度高于此值自动合并


@dataclass
class MemoryEntry:
    """记忆条目 (v7.1: session_id 字段用于会话隔离)"""
    id: str = ""
    content: str = ""
    importance: float = 5.0        # 1-10
    created_at: float = 0.0
    last_accessed: float = 0.0
    access_count: int = 0
    level: str = "working"         # working / recent / archive
    source: str = ""               # conversation / reflection / consolidated
    session_id: str = ""           # v7.1: 所属会话 ID
    embedding: List[float] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    merged_from: List[str] = field(default_factory=list)

    def age_hours(self) -> float:
        """距离创建的小时数"""
        return (time.time() - self.created_at) / 3600

    def recency_score(self) -> float:
        """时效性得分 (0-1), 越新越高"""
        hours = self.age_hours()
        if hours < 1:
            return 1.0
        elif hours < 24:
            return 0.8
        elif hours < 72:
            return 0.5
        elif hours < 168:  # 7天
            return 0.2
        else:
            return 0.05

    def frequency_score(self) -> float:
        """访问频次得分 (0-1)"""
        return min(1.0, self.access_count / 10)

    def composite_score(self) -> float:
        """
        综合得分: 重要性 × 0.5 + 时效性 × 0.3 + 频次 × 0.2
        用于决定哪些记忆保留在工作集中
        """
        imp = self.importance / 10.0
        return imp * 0.5 + self.recency_score() * 0.3 + self.frequency_score() * 0.2


class HierarchicalMemoryManager:
    """
    层次化记忆管理器

    用法:
        mgr = HierarchicalMemoryManager(db_path="memory.db")
        mgr.add("今天的会议讨论了API设计")
        relevant = mgr.recall("API设计讨论")
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        llm_flash=None,
        embedding_fn=None  # 向量化函数
    ):
        self.llm_flash = llm_flash
        self.embedding_fn = embedding_fn or self._default_embedding
        self.working_set: List[MemoryEntry] = []
        self.recent_set: List[MemoryEntry] = []
        self.archive_set: List[MemoryEntry] = []

        # SQLite 持久化
        self.db_path = db_path or os.path.join(
            galaxyos_home(),
            "hierarchical_memory.db"
        )
        self._init_db()

        # 启动时加载
        self._load_from_db()

    # ─── 公开接口 ───

    def add(
        self,
        content: str,
        importance: Optional[float] = None,
        source: str = "conversation",
        tags: Optional[List[str]] = None,
        session_id: str = "",
    ) -> str:
        """添加新记忆 (v7.1: session_id 分区)"""
        # 自动评估重要性
        if importance is None:
            importance = self._estimate_importance(content)

        entry = MemoryEntry(
            id=self._gen_id(content),
            content=content,
            importance=importance,
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=1,
            level="working",
            source=source,
            session_id=session_id,
            tags=tags or [],
        )

        # 重复检测 + 自动合并
        existing = self._find_similar(content)
        if existing and existing.importance < importance:
            existing.merged_from.append(entry.id)
            existing.content = self._merge_contents(existing.content, content)
            existing.importance = max(existing.importance, importance)
            existing.last_accessed = time.time()
            existing.access_count += 1
            self._save_to_db(existing)
            return existing.id

        # 添加到工作集
        self.working_set.insert(0, entry)
        if len(self.working_set) > WORKING_SET_SIZE:
            self._demote_to_recent(self.working_set.pop())

        self._save_to_db(entry)
        return entry.id

    def recall(
        self,
        query: str,
        top_k: int = 5,
        session_id: str = "",
    ) -> List[Dict]:
        """
        按层次检索记忆 (v7.1: session_id 过滤)

        检索顺序:
          1. 工作集 (高优先级)
          2. 近期集 (中优先级)
          3. 归档集 (低优先级)
        session_id="" 时不过滤（向后兼容）
        """
        def _by_session(mems):
            if not session_id:
                return mems
            return [m for m in mems if not m.session_id or m.session_id == session_id]

        results = []

        # 1. 工作集
        ws = _by_session(self.working_set)
        if ws:
            scored = self._score_memories(query, ws)
            for mem, score in scored[:top_k]:
                mem.access_count += 1
                mem.last_accessed = time.time()
                results.append({
                    "content": mem.content,
                    "importance": mem.importance,
                    "level": "working",
                    "score": round(score, 3),
                    "source": mem.source
                })

        if len(results) >= top_k:
            return results[:top_k]

        # 2. 近期集 + 归档集
        candidates = _by_session(self.recent_set + self.archive_set)
        if candidates:
            scored = self._score_memories(query, candidates)
            for mem, score in scored:
                if isinstance(mem, MemoryEntry):
                    mem.access_count += 1
                    mem.last_accessed = time.time()
                    results.append({
                        "content": mem.content,
                        "importance": mem.importance,
                        "level": mem.level,
                        "score": round(score, 3),
                        "source": mem.source
                    })
                    if len(results) >= top_k:
                        break

        # 更新被命中的记忆访问记录
        self._schedule_level_check()

        return results[:top_k]

    def schedule(self) -> Dict[str, Any]:
        """
        记忆调度：执行层次管理

        由 R-CCAM _memory_phase 或后台线程定时调用
        """
        report = {
            "working_set_size": len(self.working_set),
            "recent_set_size": len(self.recent_set),
            "archive_set_size": len(self.archive_set),
            "forgotten": 0,
            "merged": 0,
            "promoted": 0,
        }

        try:
            # 1. 检测工作集 → 降级到近期集
            demoted = []
            self.working_set = [
                m for m in self.working_set
                if not self._should_demote(m) or demoted.append(m) is None
            ]
            for m in demoted:
                m.level = "recent"
                self.recent_set.insert(0, m)
            report["working_set_size"] = len(self.working_set)

            # 2. 近期集 → 归档集
            archived = []
            self.recent_set = [
                m for m in self.recent_set
                if m.age_hours() < 24 or len(self.recent_set) <= RECENT_SET_MAX
                or (not self._should_archive(m) or archived.append(m) is None)
            ]
            for m in archived:
                m.level = "archive"
                self.archive_set.append(m)
            report["recent_set_size"] = len(self.recent_set)

            # 3. 归档集 → 遗忘
            forgotten = []
            self.archive_set = [
                m for m in self.archive_set
                if not self._should_forget(m) or forgotten.append(m) is None
            ]
            report["forgotten"] = len(forgotten)
            report["archive_set_size"] = len(self.archive_set)

            # 4. 重复检测 + 自动合并
            merged = self._merge_duplicates()
            report["merged"] = merged

            # 5. 高重要性记忆提升到工作集
            promoted = self._promote_important()
            report["promoted"] = promoted

            # 6. 同步到数据库
            self._sync_all_to_db()

            return report

        except Exception as e:
            logger.error(f"记忆调度失败: {e}")
            return report

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "working": len(self.working_set),
            "recent": len(self.recent_set),
            "archive": len(self.archive_set),
            "total": len(self.working_set) + len(self.recent_set) + len(self.archive_set)
        }

    # ─── 内部方法 ───

    def _estimate_importance(self, content: str) -> float:
        """自动评估记忆重要性 (1-10)"""
        if not self.llm_flash:
            # 启发式: 按长度和关键词
            score = 5.0
            if len(content) > 50:
                score += 1
            if any(kw in content for kw in ['错误', '问题', '关键', '重要', '架构', '设计']):
                score += 2
            if any(kw in content for kw in ['决定', '确认', '修改', '修复', '升级']):
                score += 1
            return min(10.0, score)

        try:
            resp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content":
                    f"评估以下内容的重要性(1-10分)，只输出一个数字:\n{content[:300]}"}],
                max_tokens=10,
                temperature=0.1
            )
            score = float(resp.choices[0].message.content.strip())
            return max(1.0, min(10.0, score))
        except Exception:
            return 5.0

    def _score_memories(self, query: str, memories: List[MemoryEntry]) -> List[Tuple[MemoryEntry, float]]:
        """对记忆按相关性评分"""
        scored = []
        query_lower = query.lower()
        query_tokens = set(query_lower.split())

        for m in memories:
            content_lower = m.content.lower()
            # 关键词匹配
            token_match = sum(1 for t in query_tokens if t in content_lower and len(t) >= 2)
            # 综合评分
            relevance = min(1.0, token_match / max(len(query_tokens), 1))
            score = relevance * 0.6 + m.composite_score() * 0.4
            scored.append((m, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _should_demote(self, mem: MemoryEntry) -> bool:
        """是否降级到近期集"""
        return (
            mem.composite_score() < 0.3
            and mem.age_hours() > 4
        )

    def _should_archive(self, mem: MemoryEntry) -> bool:
        """是否归档"""
        return mem.age_hours() > 24

    def _should_forget(self, mem: MemoryEntry) -> bool:
        """是否遗忘"""
        days_old = mem.age_hours() / 24
        return (
            mem.importance < FORGET_IMPORTANCE_MIN
            and days_old > FORGET_ACCESS_DAYS
            and mem.composite_score() < 0.1
        )

    def _find_similar(self, content: str) -> Optional[MemoryEntry]:
        """查找相似记忆（用于重复检测）"""
        all_mems = self.working_set + self.recent_set + self.archive_set
        for mem in all_mems:
            # 简单 Jaccard 相似度
            words_a = set(content.lower().split())
            words_b = set(mem.content.lower().split())
            if not words_a or not words_b:
                continue
            jaccard = len(words_a & words_b) / len(words_a | words_b)
            if jaccard >= AUTO_MERGE_THRESHOLD:
                return mem
        return None

    def _merge_contents(self, old: str, new: str) -> str:
        """合并两条相似记忆"""
        if len(new) > len(old):
            return f"{new}\n(合并自: {old[:100]})"
        return f"{old}\n(补充: {new[:100]})"

    def _promote_important(self) -> int:
        """将高重要性记忆提升到工作集"""
        promoted = 0
        candidates = [
            m for m in self.recent_set + self.archive_set
            if m.importance >= 8.0 and m not in self.working_set
        ]
        for m in candidates[:5]:  # 最多提升 5 条
            m.level = "working"
            self.working_set.append(m)
            if m in self.recent_set:
                self.recent_set.remove(m)
            elif m in self.archive_set:
                self.archive_set.remove(m)
            promoted += 1
        return promoted

    def _merge_duplicates(self) -> int:
        """合并重复记忆"""
        merged = 0
        seen = {}  # 用内容哈希去重
        for bucket in [self.working_set, self.recent_set, self.archive_set]:
            surviving = []
            for mem in bucket:
                key = self._content_hash(mem.content)
                if key in seen:
                    existing = seen[key]
                    existing.merged_from.append(mem.id)
                    if mem.importance > existing.importance:
                        existing.content = mem.content
                        existing.importance = mem.importance
                    existing.access_count += mem.access_count
                    merged += 1
                else:
                    seen[key] = mem
                    surviving.append(mem)
            bucket.clear()
            bucket.extend(surviving)
        return merged

    def _content_hash(self, content: str) -> str:
        """内容哈希（用于去重）"""
        # 只对核心关键词哈希
        words = sorted(set(content.lower().split()))
        core = ' '.join(w for w in words if len(w) >= 3)[:200]
        return hashlib.md5(core.encode()).hexdigest()

    def _gen_id(self, content: str) -> str:
        """生成记忆 ID"""
        raw = f"{content}{time.time_ns()}"
        return f"hm_{hashlib.md5(raw.encode()).hexdigest()[:16]}"

    def _schedule_level_check(self):
        """检查是否需要执行调度"""
        if len(self.working_set) > WORKING_SET_SIZE * 1.5:
            self.schedule()

    # ─── 持久化 ───

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hierarchical_memory (
                    id TEXT PRIMARY KEY,
                    content TEXT,
                    importance REAL,
                    created_at REAL,
                    last_accessed REAL,
                    access_count INTEGER,
                    level TEXT,
                    source TEXT,
                    tags TEXT,
                    merged_from TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记忆DB初始化失败: {e}")

    def _save_to_db(self, entry: MemoryEntry):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO hierarchical_memory
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id, entry.content, entry.importance,
                entry.created_at, entry.last_accessed or entry.created_at,
                entry.access_count, entry.level, entry.source,
                json.dumps(entry.tags), json.dumps(entry.merged_from)
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"写入DB失败: {e}")

    def _sync_all_to_db(self):
        """同步所有层级到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM hierarchical_memory")
            all_mems = self.working_set + self.recent_set + self.archive_set
            for m in all_mems:
                conn.execute(
                    "INSERT INTO hierarchical_memory VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (m.id, m.content, m.importance, m.created_at,
                     m.last_accessed or m.created_at, m.access_count,
                     m.level, m.source, json.dumps(m.tags),
                     json.dumps(m.merged_from))
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"批量同步失败: {e}")

    def _load_from_db(self):
        """从数据库加载"""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("SELECT * FROM hierarchical_memory").fetchall()
            conn.close()
            for row in rows:
                entry = MemoryEntry(
                    id=row[0], content=row[1], importance=row[2],
                    created_at=row[3], last_accessed=row[4] or row[3],
                    access_count=row[5] or 0, level=row[6] or "archive",
                    source=row[7] or "", tags=json.loads(row[8] or "[]"),
                    merged_from=json.loads(row[9] or "[]")
                )
                if entry.level == "working":
                    self.working_set.append(entry)
                elif entry.level == "recent":
                    self.recent_set.append(entry)
                else:
                    self.archive_set.append(entry)
        except Exception as e:
            logger.debug(f"加载DB失败(首次运行正常): {e}")

    def _default_embedding(self, text: str) -> List[float]:
        return []
