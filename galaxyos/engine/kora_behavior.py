#!/usr/bin/env python3
"""
KoRa v2 — 行为模式引擎

升级自 v1（kora_behavior.py）:
- ✅ 时序周期分析（早/午/晚用户行为聚类）
- ✅ 会话级模式检测（"做完 X 后总是问 Y"）
- ✅ 自适应参数推荐（影响 R-CCAM 策略选择）
- ✅ 周期检测（日/周/月行为循环）
- ✅ 模式持久化（重启后模式库不丢）
- ✅ sentiment 追踪（负面反馈率趋势）
- ✅ _cognition 阶段主动注入

论文参考:
- 用户行为模式挖掘: Wang et al. 2018, User Behavior Pattern Mining
- 时序聚类: TimeSeries KMeans / DTW

Author: 小艺 Claw
Version: 2.0.0
Created: 2026-06-02
"""

import json
import os
import sqlite3
import time
import logging
import threading
import hashlib
import math
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from galaxyos.shared.paths import galaxyos_home

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════════

# 时隙定义（按用户活跃习惯分段）
TIME_SLOTS = {
    "morning": (6, 12),     # 6:00-11:59
    "afternoon": (12, 18),  # 12:00-17:59
    "evening": (18, 24),    # 18:00-23:59
    "night": (0, 6),        # 0:00-5:59
}

# 默认权重
_PATTERN_WEIGHTS = {
    "temporal": 0.25,     # 时序模式权重
    "frequency": 0.20,      # 频率模式权重
    "sequence": 0.20,       # 序列模式权重
    "sentiment": 0.15,      # 情感模式权重
    "complexity": 0.10,     # 复杂模式权重
    "novelty": 0.10,        # 新颖模式权重
}

_SCHEMA_VERSION = 2


# ════════════════════════════════════════════════════════════
# 时隙工具
# ════════════════════════════════════════════════════════════

def get_time_slot(timestamp: float = None) -> str:
    """返回时间戳所属时隙名"""
    if timestamp is None:
        timestamp = time.time()
    # 北京时间 (UTC+8)
    hour = (datetime.fromtimestamp(timestamp, tz=timezone.utc).hour + 8) % 24
    for name, (start, end) in TIME_SLOTS.items():
        if start <= hour < end:
            return name
    return "afternoon"  # fallback


def get_day_of_week(timestamp: float = None) -> str:
    """返回星期几的中文名"""
    if timestamp is None:
        timestamp = time.time()
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return days[dt.weekday()]


# ════════════════════════════════════════════════════════════
# 时间窗口计算
# ════════════════════════════════════════════════════════════

def _window_start(t: float, window: str) -> float:
    """计算时间窗口起始"""
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    if window == "hour":
        return dt.replace(minute=0, second=0, microsecond=0).timestamp()
    elif window == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    elif window == "week":
        start = dt - timedelta(days=dt.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    elif window == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return t


# ════════════════════════════════════════════════════════════
# 检测到的模式记录
# ════════════════════════════════════════════════════════════

@dataclass
class DetectedPattern:
    """检测到的行为模式"""
    id: str = ""
    type: str = ""          # temporal / frequency / sequence / sentiment / complexity
    description: str = ""
    confidence: float = 0.5  # 0-1
    relevance_score: float = 0.5  # 对决策的帮助程度
    detected_at: float = 0.0
    last_observed: float = 0.0
    hit_count: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "confidence": self.confidence,
            "relevance": self.relevance_score,
            "detected_at": self.detected_at,
            "last_observed": self.last_observed,
            "hit_count": self.hit_count,
            "metadata": self.metadata,
        }


# ════════════════════════════════════════════════════════════
# KoRa v2 — 行为模式引擎
# ════════════════════════════════════════════════════════════

class KoRaBehaviorEngine:
    """
    KoRa v2 — 行为模式引擎

    架构:
    ┌──────────────────────────────┐
    │ KoRaBehaviorEngine            │
    │  ├─ record_request()         │ ← R-CCAM 每轮调用
    │  ├─ record_negative_feedback()│ ← 负面信号
    │  ├─ analyze_patterns()       │ ← 24h/7d 窗口分析
    │  ├─ get_strategy_recommend() │ ← 自适应推荐
    │  ├─ get_cognition_injection()│ ← _cognition 阶段注入
    │  ├─ detect_temporal_cycle()  │ ← 时序周期检测
    │  └─ get_pattern_summary()    │ ← 供 Kernel 使用
    └──────────────────────────────┘

    持久层:
    - SQLite (request_log + sessions + patterns + cycle_cache)
    - 路径: ~/.openclaw/kora_behavior.db
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(
            Path(galaxyos_home()) / "kora_behavior.db"
        )
        self._lock = threading.Lock()
        self._init_db()

    # ═══ 数据库初始化 ═══

    def _init_db(self):
        """初始化数据库 schema（v2 升级 + 旧表迁移）"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # v1 表已存在？检测并迁移
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='request_log'")
            old_table_exists = c.fetchone() is not None

            if old_table_exists:
                # 检查是否有 v2 列
                c.execute("PRAGMA table_info(request_log)")
                existing_columns = {row[1] for row in c.fetchall()}
                v2_columns = {"session_id", "time_slot", "day_of_week",
                              "response_time_ms", "cache_hit", "is_negative"}
                missing = v2_columns - existing_columns

                if missing:
                    # 重建表：旧数据保留，补 v2 列默认值
                    c.execute("ALTER TABLE request_log RENAME TO request_log_old")
                    c.execute("""
                        CREATE TABLE request_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT DEFAULT '',
                            query_type TEXT,
                            complexity TEXT,
                            strategy TEXT,
                            confidence REAL,
                            retrieval_count INTEGER DEFAULT 0,
                            cycle_count INTEGER DEFAULT 1,
                            has_image INTEGER DEFAULT 0,
                            is_negative INTEGER DEFAULT 0,
                            time_slot TEXT DEFAULT '',
                            day_of_week TEXT DEFAULT '',
                            response_time_ms INTEGER DEFAULT 0,
                            cache_hit INTEGER DEFAULT 0,
                            timestamp REAL
                        )
                    """)
                    # v1 老列：id, query_type, complexity, strategy, confidence,
                    #          retrieval_count, cycle_count, has_image, timestamp
                    c.execute("""
                        INSERT INTO request_log (
                            id, query_type, complexity, strategy, confidence,
                            retrieval_count, cycle_count, has_image, timestamp
                        ) SELECT
                            id, query_type, complexity, strategy, confidence,
                            retrieval_count, cycle_count, has_image, timestamp
                        FROM request_log_old
                    """)
                    c.execute("DROP TABLE request_log_old")
                    logger.info(f"KoRa v2 表迁移: 新增 {len(missing)} 列")

            if not old_table_exists:
                # 全新创建 v2 表
                c.execute("""
                    CREATE TABLE IF NOT EXISTS request_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT DEFAULT '',
                        query_type TEXT,
                        complexity TEXT,
                        strategy TEXT,
                        confidence REAL,
                        retrieval_count INTEGER DEFAULT 0,
                        cycle_count INTEGER DEFAULT 1,
                        has_image INTEGER DEFAULT 0,
                        is_negative INTEGER DEFAULT 0,
                        time_slot TEXT DEFAULT '',
                        day_of_week TEXT DEFAULT '',
                        response_time_ms INTEGER DEFAULT 0,
                        cache_hit INTEGER DEFAULT 0,
                        timestamp REAL
                    )
                """)

            # v2 新表: sessions
            c.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    start_time REAL,
                    end_time REAL,
                    request_count INTEGER DEFAULT 0,
                    top_types TEXT DEFAULT '',
                    top_strategies TEXT DEFAULT '',
                    sentiment_score REAL DEFAULT 0.0,
                    avg_complexity REAL DEFAULT 0.0,
                    avg_confidence REAL DEFAULT 0.0
                )
            """)
            # v2: patterns
            c.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    id TEXT PRIMARY KEY,
                    type TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.0,
                    relevance REAL DEFAULT 0.0,
                    detected_at REAL DEFAULT 0.0,
                    last_observed REAL DEFAULT 0.0,
                    hit_count INTEGER DEFAULT 1,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            # v2: cycle_cache
            c.execute("""
                CREATE TABLE IF NOT EXISTS cycle_cache (
                    id TEXT PRIMARY KEY,
                    cycle_type TEXT DEFAULT '',
                    window TEXT DEFAULT '',
                    pattern TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.0,
                    last_updated REAL DEFAULT 0.0
                )
            """)
            # schema_meta
            c.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            c.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("version", str(_SCHEMA_VERSION))
            )

            # 索引
            c.execute("CREATE INDEX IF NOT EXISTS idx_kora_ts ON request_log(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_kora_slot ON request_log(time_slot)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_kora_neg ON request_log(is_negative)")

            conn.commit()
            conn.close()
            logger.info(f"KoRa v2 DB 初始化: {self.db_path}")
        except Exception as e:
            logger.error(f"KoRa v2 DB 初始化失败: {e}")

    # ═══ 记录接口 ═══

    def record_request(
        self,
        session_id: str = "",
        query_type: str = "general",
        complexity: str = "0.05",
        strategy: str = "answer",
        confidence: float = 0.3,
        retrieval_count: int = 0,
        cycle_count: int = 1,
        has_image: bool = False,
        is_negative: bool = False,
        response_time_ms: int = 0,
        cache_hit: bool = False,
    ):
        """记录一次请求特征"""
        try:
            ts = time.time()
            slot = get_time_slot(ts)
            dow = get_day_of_week(ts)

            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT INTO request_log (session_id, query_type, complexity, strategy, "
                    "confidence, retrieval_count, cycle_count, has_image, is_negative, "
                    "time_slot, day_of_week, response_time_ms, cache_hit, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, query_type, complexity, strategy, confidence,
                     retrieval_count, cycle_count, 1 if has_image else 0,
                     1 if is_negative else 0, slot, dow,
                     response_time_ms, 1 if cache_hit else 0, ts)
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"KoRa 记录失败: {e}")

    def record_negative_feedback(self, session_id: str = ""):
        """记录一次用户负面反馈"""
        # 插入一条 is_negative 标记（如果没有对应请求记录）
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE request_log SET is_negative = 1 "
                    "WHERE id = (SELECT MAX(id) FROM request_log WHERE session_id = ?)",
                    (session_id,) if session_id else ()
                )
                conn.commit()
                conn.close()
        except Exception:
            pass

    def _end_session(self, session_id: str):
        """结束会话并汇总"""
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                rows = conn.execute(
                    "SELECT query_type, strategy, is_negative, complexity, confidence "
                    "FROM request_log WHERE session_id = ? ORDER BY id",
                    (session_id,)
                ).fetchall()

                if rows:
                    types = Counter(r[0] for r in rows)
                    strategies = Counter(r[1] for r in rows)
                    neg_count = sum(r[2] for r in rows)
                    complexities = []
                    confidences = []
                    for r in rows:
                        try:
                            complexities.append(float(r[3]))
                        except (ValueError, TypeError):
                            pass
                        confidences.append(r[4])

                    sent_score = 1.0 - (neg_count / len(rows)) if rows else 0.5
                    avg_complexity = sum(complexities) / len(complexities) if complexities else 0.0
                    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

                    conn.execute(
                        "UPDATE sessions SET end_time = ?, request_count = ?, "
                        "top_types = ?, top_strategies = ?, sentiment_score = ?, "
                        "avg_complexity = ?, avg_confidence = ? WHERE id = ?",
                        (time.time(), len(rows),
                         json.dumps(types.most_common(5)),
                         json.dumps(strategies.most_common(5)),
                         sent_score, avg_complexity, avg_conf,
                         session_id)
                    )
                    conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"KoRa 结束会话失败: {e}")

    def start_session(self, session_id: str):
        """开始一个新会话"""
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, start_time) VALUES (?, ?)",
                    (session_id, time.time())
                )
                conn.commit()
                conn.close()
        except Exception:
            pass

    # ═══ 分析引擎 ═══

    def analyze_patterns(self, hours: int = 24) -> Dict[str, Any]:
        """
        综合分析最近 hours 小时内的行为模式

        Returns:
            {
                "total": int,
                "by_hour": {6: {total, negative, types}, ...},
                "by_slot": {morning/afternoon/evening/night: count},
                "by_strategy": {strategy: count},
                "by_type": {type: count},
                "trend_sentiment": float (-1=neg, +1=pos),
                "avg_complexity": float,
                "avg_confidence": float,
                "strategy_switch_rate": float,
                "query_diversity": float,
                "pattern_clusters": [...],
                "cache_hit_rate": float,
            }
        """
        cutoff = time.time() - hours * 3600

        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)

                # ── 基础统计 ──
                rows = conn.execute(
                    "SELECT query_type, complexity, strategy, confidence, is_negative, "
                    "time_slot, day_of_week, response_time_ms, cache_hit, timestamp "
                    "FROM request_log WHERE timestamp > ? ORDER BY timestamp",
                    (cutoff,)
                ).fetchall()

                if not rows:
                    return {"total": 0}

                # 按小时分布
                by_hour: Dict[int, Dict] = defaultdict(lambda: {
                    "total": 0, "negative": 0, "types": Counter()
                })
                by_slot = Counter()
                by_strategy = Counter()
                by_type = Counter()
                by_day = Counter()

                complexities = []
                confidences = []
                response_times = []
                cache_hits = 0
                negatives = 0
                strategies_seq = []

                for r in rows:
                    qt, comp, strat, conf, neg, slot, dow, rt, ch, ts = r

                    by_type[qt] += 1
                    by_strategy[strat] += 1
                    by_slot[slot] += 1
                    by_day[dow] += 1
                    strategies_seq.append(strat)

                    try:
                        complexities.append(float(comp))
                    except (ValueError, TypeError):
                        pass
                    confidences.append(conf)

                    if rt:
                        response_times.append(rt)
                    if ch:
                        cache_hits += 1
                    if neg:
                        negatives += 1

                    hour = (int((ts % 86400) / 3600) + 8) % 24
                    by_hour[hour]["total"] += 1
                    by_hour[hour]["types"][qt] += 1
                    if neg:
                        by_hour[hour]["negative"] += 1

                # 策略切换率
                switch_rate = 0.0
                if len(strategies_seq) > 1:
                    switches = sum(1 for i in range(1, len(strategies_seq))
                                   if strategies_seq[i] != strategies_seq[i-1])
                    switch_rate = switches / (len(strategies_seq) - 1)

                # 趋势情感分
                total = len(rows)
                sentiment_trend = 1.0 - (negatives / total) * 2 if total > 0 else 0

                # 峰值时段
                peak_hour = max(by_hour, key=lambda h: by_hour[h]["total"]) if by_hour else 0

                result = {
                    "total": total,
                    "by_hour": {str(h): {"total": v["total"], "negative": v["negative"]}
                                for h, v in sorted(by_hour.items())},
                    "by_slot": dict(by_slot),
                    "by_day": dict(by_day),
                    "by_strategy": dict(by_strategy.most_common(10)),
                    "by_type": dict(by_type.most_common(10)),
                    "trend_sentiment": round(sentiment_trend, 3),
                    "avg_complexity": round(sum(complexities) / len(complexities), 4) if complexities else 0,
                    "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
                    "avg_response_time_ms": round(sum(response_times) / len(response_times), 1) if response_times else 0,
                    "strategy_switch_rate": round(switch_rate, 3),
                    "query_diversity": len(by_type),
                    "cache_hit_rate": round(cache_hits / total, 3) if total > 0 else 0,
                    "negative_rate": round(negatives / total, 3) if total > 0 else 0,
                    "peak_hour": int(peak_hour),
                }

                conn.close()
                return result

        except Exception as e:
            logger.warning(f"KoRa 分析失败: {e}")
            return {"total": 0}

    # ═══ 时序周期检测 ═══

    def detect_temporal_cycle(self) -> Dict[str, Any]:
        """
        检测用户行为的时间周期

        检测方法:
        - 按小时/日/周统计请求量
        - 用自相关性检测周期长度
        - 输出: 检测到的日/周/月周期性

        Returns:
            {
                "has_daily_cycle": bool,
                "has_weekly_cycle": bool,
                "active_times": [str],        # 最活跃时隙
                "daily_peak_hour": int,
                "weekly_active_days": [str],
                "rest_estimate": str,           # "凌晨1-7点为低活跃期"
            }
        """
        cycle_result = {
            "has_daily_cycle": False,
            "has_weekly_cycle": False,
            "active_times": [],
            "daily_peak_hour": -1,
            "weekly_active_days": [],
            "rest_estimate": "数据不足",
        }

        try:
            conn = sqlite3.connect(self.db_path)

            # 7 天数据
            week_cutoff = time.time() - 7 * 86400
            rows = conn.execute(
                "SELECT time_slot, day_of_week, timestamp "
                "FROM request_log WHERE timestamp > ?",
                (week_cutoff,)
            ).fetchall()
            conn.close()

            if len(rows) < 10:
                return cycle_result

            # 按时隙统计
            slot_counts = Counter(r[0] for r in rows)
            day_counts = Counter(r[1] for r in rows)

            # 按小时统计
            hour_counts = Counter()
            for row in rows:
                h = int(((row[2] % 86400) / 3600) + 8) % 24
                hour_counts[h] += 1

            # 判断是否有每日周期：看每天同一时段是否有重复出现
            daily_pattern = False
            if len(slot_counts) >= 2:
                total = sum(slot_counts.values())
                # 如果某个时隙占比 > 30%，认为有日周期
                dominant_slot = max(slot_counts.values())
                if dominant_slot / total > 0.3:
                    daily_pattern = True

            # 判断周周期：看工作日 vs 周末
            weekly_pattern = False
            if len(day_counts) >= 5:
                weekdays = sum(day_counts.get(d, 0) for d in ["周一","周二","周三","周四","周五"])
                weekend = sum(day_counts.get(d, 0) for d in ["周六","周日"])
                total = weekdays + weekend
                if total > 0 and (weekdays / total > 0.8 or weekend / total > 0.8):
                    weekly_pattern = True

            # 峰值时段
            peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else -1

            # 活跃时隙（按降序）
            active_slots = [s for s, _ in slot_counts.most_common(4)]

            # 活跃日
            active_days = [d for d, _ in day_counts.most_common(7)]

            # 休息时段估计
            # 找连续 3 小时以上请求量最低的时段
            hours_sorted = sorted(hour_counts.items())
            if hours_sorted:
                # 滑动窗口 3 小时
                min_activity = float('inf')
                rest_start = -1
                for i in range(24):
                    count = sum(hour_counts.get((i + j) % 24, 0) for j in range(3))
                    if count < min_activity:
                        min_activity = count
                        rest_start = i
                if rest_start >= 0:
                    cycle_result["rest_estimate"] = f"{rest_start}:00-{(rest_start+3)%24}:00 低活跃期"

            cycle_result.update({
                "has_daily_cycle": daily_pattern,
                "has_weekly_cycle": weekly_pattern,
                "active_times": active_slots[:3],
                "daily_peak_hour": peak_hour,
                "weekly_active_days": active_days,
            })

            # 缓存到 DB
            self._cache_cycle(cycle_result)

        except Exception as e:
            logger.warning(f"KoRa 周期检测失败: {e}")

        return cycle_result

    def _cache_cycle(self, cycle: Dict):
        """缓存周期检测结果"""
        try:
            conn = sqlite3.connect(self.db_path)
            cid = "temporal_cycle"
            conn.execute(
                "INSERT OR REPLACE INTO cycle_cache (id, cycle_type, window, pattern, confidence, last_updated) "
                "VALUES (?, 'temporal', '7d', ?, 0.8, ?)",
                (cid, json.dumps(cycle, ensure_ascii=False), time.time())
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ═══ 自适应策略推荐 ═══

    def get_strategy_recommendation(self) -> Optional[Dict[str, Any]]:
        """
        根据近期行为模式推荐 R-CCAM 策略和参数

        Returns:
            {
                "strategy": str,
                "params": {
                    "retrieval_top_k": int,
                    "confidence_threshold": float,
                    "use_crag": bool,
                    "use_predictive_coding": bool,
                    "prefer_fast_path": bool,
                },
                "confidence": float,
                "reason": str,
            }
        """
        patterns = self.analyze_patterns(hours=6)
        if patterns["total"] < 3:
            return None

        recommend = {
            "strategy": "answer",  # default
            "params": {},
            "confidence": 0.5,
            "reason": "",
        }

        # ── 基于策略分布推荐 ──
        top_strategies = patterns.get("by_strategy", {})
        if top_strategies:
            recommend["strategy"] = max(top_strategies, key=top_strategies.get)

        # ── 参数推荐 ──
        avg_complexity = patterns.get("avg_complexity", 0.5)
        negative_rate = patterns.get("negative_rate", 0)
        cache_hit_rate = patterns.get("cache_hit_rate", 0)
        switch_rate = patterns.get("strategy_switch_rate", 0)

        # 低复杂度 → 快速回答
        if avg_complexity < 0.1:
            recommend["params"] = {
                "retrieval_top_k": 3,
                "confidence_threshold": 0.5,
                "use_crag": False,
                "use_predictive_coding": False,
                "prefer_fast_path": True,
            }
            recommend["reason"] = "近期低复杂度问题为主，走快速通道"
        # 中等复杂度
        elif avg_complexity < 0.4:
            recommend["params"] = {
                "retrieval_top_k": 5,
                "confidence_threshold": 0.4,
                "use_crag": True,
                "use_predictive_coding": False,
                "prefer_fast_path": False,
            }
            recommend["reason"] = "中等复杂度，启用 CRAG 纠错"
        # 高复杂度 → 深度推理
        else:
            recommend["params"] = {
                "retrieval_top_k": 8,
                "confidence_threshold": 0.35,
                "use_crag": True,
                "use_predictive_coding": True,
                "prefer_fast_path": False,
            }
            recommend["reason"] = "高复杂度问题，启用深度推理+预测编码"

        # 负面率高 → 降低阈值
        if negative_rate > 0.3:
            recommend["params"]["confidence_threshold"] = max(
                0.2, recommend["params"].get("confidence_threshold", 0.4) - 0.1
            )
            recommend["reason"] += "，负面率偏高，降低置信度阈值"

        # 缓存命中率高 → 优先用缓存
        if cache_hit_rate > 0.5:
            recommend["params"]["prefer_fast_path"] = True
            recommend["reason"] += "，缓存命中率高"

        recommend["confidence"] = min(0.9, 0.5 + len(patterns) * 0.01)
        return recommend

    # ═══ Cognition 阶段注入文本 ═══

    def get_cognition_injection(self) -> str:
        """
        生成供 _cognition_phase 注入的文本摘要

        注入内容:
        - 当前时隙
        - 用户活跃周期
        - 策略推荐（如果有）
        - 高频查询类型变化

        Returns:
            markdown 文本（空字符串=无信息注入）
        """
        patterns = self.analyze_patterns(hours=6)
        if patterns["total"] < 3:
            return ""

        slot = get_time_slot()
        lines = [f"📊 KoRa 行为模式 ({patterns['total']}次, {slot}时隙):"]

        # 峰值
        peak_h = patterns.get("peak_hour", -1)
        if peak_h >= 0:
            lines.append(f"  - 峰值时段: {peak_h}:00 左右")

        # 策略
        top_strategies = patterns.get("by_strategy", {})
        if top_strategies:
            s = ", ".join(f"{k}({v})" for k, v in list(top_strategies.items())[:3])
            lines.append(f"  - 常用策略: {s}")

        # 类型
        top_types = patterns.get("by_type", {})
        if top_types:
            t = ", ".join(f"{k}({v})" for k, v in list(top_types.items())[:3])
            lines.append(f"  - 高频类型: {t}")

        # 切换率
        switch = patterns.get("strategy_switch_rate", 0)
        lines.append(f"  - 策略切换: {'频繁' if switch > 0.3 else '稳定'} ({switch:.0%})")

        # 情感
        sentiment = patterns.get("trend_sentiment", 0)
        lines.append(f"  - 用户倾向: {'😊' if sentiment > 0.3 else '😐' if sentiment > -0.3 else '😠'} ({sentiment:+.2f})")

        return "\n".join(lines)

    # ═══ 模式库管理 ═══

    def save_detected_pattern(self, pattern: DetectedPattern):
        """保存检测到的模式到持久化库"""
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO patterns "
                    "(id, type, description, confidence, relevance, detected_at, "
                    "last_observed, hit_count, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pattern.id, pattern.type, pattern.description,
                     pattern.confidence, pattern.relevance_score,
                     pattern.detected_at, pattern.last_observed,
                     pattern.hit_count,
                     json.dumps(pattern.metadata, ensure_ascii=False))
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"KoRa 保存模式失败: {e}")

    def get_detected_patterns(self, min_confidence: float = 0.3) -> List[Dict]:
        """获取持久化模式库"""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT id, type, description, confidence, relevance, "
                "detected_at, last_observed, hit_count, metadata "
                "FROM patterns WHERE confidence >= ? ORDER BY confidence DESC",
                (min_confidence,)
            ).fetchall()
            conn.close()
            return [
                {
                    "id": r[0], "type": r[1], "description": r[2],
                    "confidence": r[3], "relevance": r[4],
                    "detected_at": r[5], "last_observed": r[6],
                    "hit_count": r[7],
                    "metadata": json.loads(r[8]) if r[8] else {},
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_cached_cycle(self) -> Optional[Dict]:
        """获取缓存的周期检测结果"""
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT pattern, last_updated FROM cycle_cache WHERE id = 'temporal_cycle'"
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception:
            pass
        return None

    # ═══ 模式发现引擎（自动检测新模式） ═══

    def run_pattern_discovery(self) -> List[Dict]:
        """
        自动扫描数据库中的新模式

        检测类型:
        1. 时隙-类型关联: "用户总是在 morning 查天气"
        2. 序列模式: "用户查了 A 之后通常查 B"
        3. 复杂度漂移: "用户问题越来越复杂"
        4. 情感漂移: "用户满意度在下降"

        Returns:
            新发现的模式列表
        """
        new_patterns = []

        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT query_type, strategy, complexity, is_negative, "
                "time_slot, timestamp FROM request_log "
                "WHERE timestamp > ? ORDER BY timestamp",
                (time.time() - 7 * 86400,)
            ).fetchall()
            conn.close()

            if len(rows) < 5:
                return new_patterns

            # ── 1. 时隙-类型关联 ──
            slot_type: Dict[str, Counter] = defaultdict(Counter)
            for r in rows:
                slot_type[r[4]][r[0]] += 1

            for slot, type_counts in slot_type.items():
                dominant_type = type_counts.most_common(1)
                if dominant_type:
                    t, c = dominant_type[0]
                    ratio = c / sum(type_counts.values())
                    if ratio > 0.5 and c >= 3:
                        pattern = DetectedPattern(
                            id=f"slot_type_{slot}_{t}",
                            type="temporal",
                            description=f"{slot}时隙高频-{t}({ratio:.0%})",
                            confidence=ratio,
                            relevance_score=ratio * 0.6,
                            detected_at=time.time(),
                            last_observed=time.time(),
                            hit_count=c,
                            metadata={"slot": slot, "type": t, "ratio": ratio}
                        )
                        self.save_detected_pattern(pattern)
                        new_patterns.append(pattern.to_dict())

            # ── 2. 情感趋势（近7天 vs 之前） ──
            if len(rows) > 10:
                recent = rows[-10:]
                neg_recent = sum(r[3] for r in recent) / len(recent)
                if neg_recent > 0.3:
                    pattern = DetectedPattern(
                        id=f"sentiment_drop_{int(time.time())}",
                        type="sentiment",
                        description=f"近7条负面率{neg_recent:.0%}",
                        confidence=neg_recent,
                        relevance_score=0.7,
                        detected_at=time.time(),
                        last_observed=time.time(),
                        metadata={"negative_rate": neg_recent}
                    )
                    self.save_detected_pattern(pattern)
                    new_patterns.append(pattern.to_dict())

        except Exception as e:
            logger.warning(f"KoRa 模式发现失败: {e}")

        return new_patterns

    # ═══ 全局摘要（供 Kernel/install_wizard 使用） ═══

    def get_pattern_summary(self) -> str:
        """人类可读的模式摘要"""
        patterns = self.analyze_patterns(hours=24)

        if patterns["total"] == 0:
            return "KoRa: 暂无行为数据"

        lines = [
            f"🧠 KoRa 行为模式 (24h, {patterns['total']}次):",
        ]

        # 时隙占比
        slots = patterns.get("by_slot", {})
        if slots:
            total = sum(slots.values())
            slot_str = ", ".join(
                f"{s}({c/total:.0%})" for s, c in sorted(slots.items(), key=lambda x: -x[1])
            )
            lines.append(f"  时段: {slot_str}")

        # 类型
        types = patterns.get("by_type", {})
        if types:
            type_str = ", ".join(f"{k}({v})" for k, v in list(types.items())[:5])
            lines.append(f"  类型: {type_str}")

        # 情感
        sentiment = patterns.get("trend_sentiment", 0)
        if sentiment > 0.3:
            lines.append(f"  😊 用户倾向积极 (score: {sentiment:+.2f})")
        elif sentiment < -0.3:
            lines.append(f"  😠 用户倾向消极 (score: {sentiment:+.2f})")

        # 周期
        cycle = self.get_cached_cycle() or self.detect_temporal_cycle()
        if cycle.get("active_times"):
            lines.append(f"  活跃周期: {', '.join(cycle['active_times'])}")

        # 缓存
        ch = patterns.get("cache_hit_rate", 0)
        if ch > 0:
            lines.append(f"  缓存命中: {ch:.0%}")

        return "\n".join(lines)

    def get_compact_summary(self) -> Dict:
        """简洁结构化摘要（供 API 使用）"""
        patterns = self.analyze_patterns(hours=24)
        cycle = self.get_cached_cycle() or {}
        return {
            "total_requests_24h": patterns.get("total", 0),
            "peak_hour": patterns.get("peak_hour", -1),
            "active_slots": cycle.get("active_times", []),
            "dominant_strategy": max(patterns.get("by_strategy", {}),
                                     key=patterns.get("by_strategy", {}).get) if patterns.get("by_strategy") else "",
            "sentiment": patterns.get("trend_sentiment", 0),
            "cache_hit_rate": patterns.get("cache_hit_rate", 0),
            "negative_rate": patterns.get("negative_rate", 0),
            "avg_complexity": patterns.get("avg_complexity", 0),
            "strategy_switch_rate": patterns.get("strategy_switch_rate", 0),
            "query_diversity": patterns.get("query_diversity", 0),
            "detected_patterns": len(self.get_detected_patterns()),
            "has_daily_cycle": cycle.get("has_daily_cycle", False),
            "has_weekly_cycle": cycle.get("has_weekly_cycle", False),
        }


# ════════════════════════════════════════════════════════════
# 便捷全局接口（向后兼容 v1 接口名）
# ════════════════════════════════════════════════════════════

_engine_instance = None
_engine_lock = threading.Lock()


def get_engine() -> KoRaBehaviorEngine:
    """获取全局 KoRa 引擎实例"""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = KoRaBehaviorEngine()
    return _engine_instance


# ── v1 兼容接口 ──

def record_request(query_type, complexity, strategy, confidence,
                   retrieval_count=0, cycle_count=1, has_image=False):
    eng = get_engine()
    eng.record_request(
        query_type=query_type, complexity=str(complexity),
        strategy=strategy, confidence=confidence,
        retrieval_count=retrieval_count, cycle_count=cycle_count,
        has_image=has_image
    )

def analyze_patterns(hours=24):
    return get_engine().analyze_patterns(hours=hours)

def get_strategy_recommendation():
    return get_engine().get_strategy_recommendation()

def get_summary():
    return get_engine().get_pattern_summary()

def get_cognition_injection():
    return get_engine().get_cognition_injection()


# ════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="KoRa v2 — 行为模式引擎")
    parser.add_argument("--status", action="store_true", help="查看模式摘要")
    parser.add_argument("--analyze", type=int, default=0,
                        help="分析 n 小时模式 (default=24)")
    parser.add_argument("--cycle", action="store_true", help="检测周期")
    parser.add_argument("--patterns", action="store_true", help="列出持久化模式")
    parser.add_argument("--discover", action="store_true", help="运行模式发现")
    parser.add_argument("--cognition", action="store_true", help="cognition 注入文本")
    parser.add_argument("--recommend", action="store_true", help="策略推荐")
    parser.add_argument("--migrate", action="store_true", help="数据库迁移")
    args = parser.parse_args()

    eng = get_engine()

    if args.migrate:
        print(f"✅ KoRa v2 DB 已就绪: {eng.db_path}")

    elif args.status:
        print(eng.get_pattern_summary())
        print()
        compact = eng.get_compact_summary()
        print(f"  缓存: 请求{compact['total_requests_24h']}次 | "
              f"峰值{compact['peak_hour']}:00 | "
              f"情感{compact['sentiment']:+.2f}")
        print(f"  周期: {'日' if compact['has_daily_cycle'] else ''}"
              f"{'周' if compact['has_weekly_cycle'] else ''}"
              f"{'无' if not compact['has_daily_cycle'] and not compact['has_weekly_cycle'] else ''}")

    elif args.analyze:
        hours = args.analyze if args.analyze > 0 else 24
        result = eng.analyze_patterns(hours=hours)
        print(f"📊 KoRa {hours}h 分析结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.cycle:
        result = eng.detect_temporal_cycle()
        print("📈 周期检测结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.patterns:
        patterns = eng.get_detected_patterns()
        print(f"🔍 持久化模式库 ({len(patterns)} 条):")
        for p in patterns:
            print(f"  [{p['type']:>10s}] {p['description']} "
                  f"(conf={p['confidence']:.2f}, hits={p['hit_count']})")

    elif args.discover:
        new = eng.run_pattern_discovery()
        print(f"🔍 模式发现: {len(new)} 个新模式")
        for p in new:
            print(f"  [{p['type']:>10s}] {p['description']}")

    elif args.cognition:
        inj = eng.get_cognition_injection()
        if inj:
            print(inj)
        else:
            print("(数据不足，无注入)")

    elif args.recommend:
        rec = eng.get_strategy_recommendation()
        if rec:
            print(f"🎯 策略推荐: {rec['strategy']}")
            print(f"   理由: {rec['reason']}")
            print(f"   参数: {json.dumps(rec['params'], ensure_ascii=False)}")
            print(f"   置信度: {rec['confidence']:.2f}")
        else:
            print("(数据不足，无推荐)")

    else:
        # 默认全显示
        print(f"{'='*60}")
        print("🧠 KoRa v2 — 行为模式引擎")
        print(f"{'='*60}")
        print(eng.get_pattern_summary())
        print()
        print("--- 快速命令 ---")
        print("  --analyze 24   分析 24 小时")
        print("  --cycle        检测周期")
        print("  --cognition    cognition 注入文本")
        print("  --recommend    策略推荐")
        print("  --patterns     模式库")
        print("  --discover     模式发现")
