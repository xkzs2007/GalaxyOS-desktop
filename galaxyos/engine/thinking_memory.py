"""
thinking_memory.py — Springdrift 案例推理（CBR）记忆层

论文:
- Springdrift: An Auditable Persistent Runtime for LLM Agents with Case-Based Memory
  (arXiv 2604.04660)

核心机制:
1. Case-Based Memory：存历史推荐+用户反馈，同类查询优先复用成功案例
2. Hybrid Retrieval：关键词匹配 + tag 相似度（可升级为 embedding）
3. Sensorium：持续感知当前语境（困惑度、历史纠正率、对话轮次）
4. 权重衰减：推荐成功率随时间和负反馈衰减

替换 IntelligentThinkingTrigger 无状态的 analysis_log。
"""

from typing import Dict, Optional, List
from dataclasses import dataclass
import json
import os
import time
import re


CASE_DIR = os.path.join(os.path.dirname(__file__), "thinking_cases")


@dataclass
class ThinkingCase:
    """推荐案例"""
    query: str                                  # 原始查询
    query_hash: str                             # 查询 hash（去重用）
    question_type: str                          # 问题类型
    cognitive_stage: str                        # 认知阶段
    recommended_skills: List[str]               # 推荐的技能列表
    user_adopted: Optional[bool] = None         # 用户是否采纳
    user_feedback: float = 0.5                  # 用户反馈分数 0-1
    timestamp: float = 0.0                      # 推荐时间
    session_id: str = ""                        # 会话 ID
    token_count: int = 0                        # 查询 token 数

    def to_dict(self) -> Dict:
        return {
            "query": self.query,
            "query_hash": self.query_hash,
            "question_type": self.question_type,
            "cognitive_stage": self.cognitive_stage,
            "recommended_skills": self.recommended_skills,
            "user_adopted": self.user_adopted,
            "user_feedback": self.user_feedback,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ThinkingCase":
        return cls(
            query=d.get("query", ""),
            query_hash=d.get("query_hash", ""),
            question_type=d.get("question_type", ""),
            cognitive_stage=d.get("cognitive_stage", ""),
            recommended_skills=d.get("recommended_skills", []),
            user_adopted=d.get("user_adopted"),
            user_feedback=d.get("user_feedback", 0.5),
            timestamp=d.get("timestamp", 0),
            session_id=d.get("session_id", ""),
            token_count=d.get("token_count", 0),
        )


@dataclass
class Sensorium:
    """
    Springdrift Sensorium：持续感知状态

    每轮对话注入到 ThinkingTrigger 中，影响推荐决策。
    """
    # 当前上下文
    session_age: int = 0                    # 当前会话轮次
    query_count: int = 0                    # 本次会话查询总数
    current_complexity: float = 0.0         # 当前查询复杂度
    current_confusion: float = 0.0          # 当前查询困惑度

    # 历史模式
    correction_rate: float = 0.0            # 用户历史纠正率
    skill_adoption_rate: float = 0.5        # 历史推荐采纳率
    last_adopted_skill: str = ""            # 上次被采纳的技能
    last_rejected_skill: str = ""           # 上次被拒绝的技能
    repeated_query_count: int = 0           # 相似查询重复次数（卡住检测）

    # 时间特征
    time_of_day: str = ""                   # 时段（morning/afternoon/night）
    inter_query_gap: float = 0.0            # 与上次查询的时间间隔（秒）

    # 元信息
    session_id: str = ""
    total_cases: int = 0                    # 记忆库案例总数

    def to_dict(self) -> Dict:
        return {
            "session_age": self.session_age,
            "query_count": self.query_count,
            "current_complexity": self.current_complexity,
            "current_confusion": self.current_confusion,
            "correction_rate": self.correction_rate,
            "skill_adoption_rate": self.skill_adoption_rate,
            "last_adopted_skill": self.last_adopted_skill,
            "last_rejected_skill": self.last_rejected_skill,
            "repeated_query_count": self.repeated_query_count,
            "time_of_day": self.time_of_day,
            "inter_query_gap": self.inter_query_gap,
            "session_id": self.session_id,
            "total_cases": self.total_cases,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Sensorium":
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

    def infer_cognitive_stage(self) -> str:
        """从 sensorium 推断用户认知阶段（A-ToM 启发）

        explore  - 初期探索/了解
        analyze  - 深入分析/设计
        verify   - 验证/调试/改进
        stuck    - 卡住/困惑

        返回: explore | analyze | verify | stuck
        """
        # 高困惑度 + 短时间重复查询 → stuck
        if self.current_confusion > 0.5 and self.repeated_query_count >= 2:
            return "stuck"

        # 高复杂度 + 无历史纠正 → analyze
        if self.current_complexity > 0.6 and self.correction_rate < 0.3:
            return "analyze"

        # 低复杂度 + 纠正率高 → verify
        if self.current_complexity < 0.4 and self.correction_rate > 0.3:
            return "verify"

        # 会话早期 + 低复杂度 → explore
        if self.session_age < 3 or self.current_complexity < 0.3:
            return "explore"

        # 默认按复杂性分级
        if self.current_complexity > 0.7:
            return "analyze"
        elif self.current_complexity > 0.4:
            return "analyze" if self.correction_rate < 0.5 else "verify"
        else:
            return "explore"


class ThinkingMemory:
    """
    Springdrift Case-Based Reasoning 记忆层

    用于代替 IntelligentThinkingTrigger 无状态的 analysis_log。
    持久化到 thinking_cases/ 目录，支持跨会话复用。
    """

    def __init__(self, session_id: str = "unknown",
                 storage_dir: Optional[str] = None):
        self.session_id = session_id
        self.storage_dir = storage_dir or CASE_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

        self.cases: List[ThinkingCase] = []
        self.sensorium = Sensorium(session_id=session_id)
        self._load_cases()

    # ── 案例操作 ──

    def add_case(self, case: ThinkingCase):
        """添加新推荐案例"""
        # 去重：相同 query_hash 的替换
        existing = [c for c in self.cases if c.query_hash == case.query_hash]
        if existing:
            idx = self.cases.index(existing[-1])
            self.cases[idx] = case
        else:
            self.cases.append(case)

        # 更新 sensorium
        self.sensorium.total_cases = len(self.cases)
        self.sensorium.query_count += 1
        self.sensorium.session_age += 1

        self._save_session()

    def record_feedback(self, query_hash: str, adopted: bool = True,
                        feedback_score: float = 0.8):
        """记录用户反馈"""
        for c in self.cases:
            if c.query_hash == query_hash:
                c.user_adopted = adopted
                c.user_feedback = feedback_score
                self._recompute_adoption_rate()
                self._save_session()
                return

    def find_similar_cases(self, query: str, max_cases: int = 5) -> List[ThinkingCase]:
        """Hybrid 检索相似案例（关键词 + tag 相似度）

        Springdrift 的 hybrid 检索：
        先用 query hash 精确匹配，再用关键词 + tag 模糊匹配。
        """
        ql = query.lower()
        qhash = self._hash_query(query)

        # 精确匹配
        exact = [c for c in self.cases if c.query_hash == qhash]
        if exact:
            return exact[:max_cases]

        # 模糊匹配：共同关键词数
        query_tokens = set(re.findall(r'[\w\u4e00-\u9fff]+', ql))
        scored = []
        for c in self.cases:
            ctokens = set(re.findall(r'[\w\u4e00-\u9fff]+', c.query.lower()))
            overlap = len(query_tokens & ctokens)
            if overlap > 0:
                # 时序衰减：越近的案例权重越高
                age_hours = (time.time() - c.timestamp) / 3600
                time_decay = max(0.1, 1.0 - age_hours / 720)  # 30天衰减到0.1
                # 采纳分
                adopt_bonus = 0.3 if c.user_adopted else 0.0
                # 综合分
                score = overlap / max(len(query_tokens), len(ctokens)) * 0.6 \
                        + time_decay * 0.25 + adopt_bonus * 0.15
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:max_cases]]

    def get_history_weights(self) -> Dict[str, float]:
        """计算历史反馈权重字典 {skill_key: weight}

        正反馈 > 0.5，负反馈 < 0.5，无历史 = 中性
        用于 skill_scorer 的 history_score。
        """
        if not self.cases:
            return {}

        skill_scores: Dict[str, List[float]] = {}
        for c in self.cases:
            if c.user_adopted is not None:
                for s in c.recommended_skills:
                    skill_scores.setdefault(s, []).append(c.user_feedback)

        return {
            skill: sum(scores) / len(scores)
            for skill, scores in skill_scores.items()
        }

    # ── Sensorium ──

    def update_sensorium(self, query: str,
                         complexity: float, confusion: float,
                         correction_rate: float = 0.0):
        """更新 sensorium 状态"""
        self.sensorium.current_complexity = complexity
        self.sensorium.current_confusion = confusion
        self.sensorium.correction_rate = correction_rate
        self.sensorium.session_age += 1

        # 重复查询检测
        qhash = self._hash_query(query)
        recent = [c for c in self.cases[-10:] if c.query_hash[:8] == qhash[:8]]
        self.sensorium.repeated_query_count = len(recent)

        # 计算采纳率
        self._recompute_adoption_rate()

        # 时段
        h = time.localtime().tm_hour
        if h < 12:
            self.sensorium.time_of_day = "morning"
        elif h < 18:
            self.sensorium.time_of_day = "afternoon"
        else:
            self.sensorium.time_of_day = "night"

    def get_cognitive_stage(self) -> str:
        """获取当前认知阶段（A-ToM 对齐）"""
        return self.sensorium.infer_cognitive_stage()

    def get_sensorium_state(self) -> Dict:
        """获取 sensorium 状态 dict"""
        return self.sensorium.to_dict()

    # ── 持久化 ──

    def _hash_query(self, query: str) -> str:
        """查询哈希"""
        import hashlib
        return hashlib.md5(query.encode('utf-8')).hexdigest()

    def _load_cases(self):
        """加载历史案例"""
        path = os.path.join(self.storage_dir, f"{self.session_id}.json")
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.cases = [ThinkingCase.from_dict(c) for c in data.get("cases", [])]
                if "sensorium" in data:
                    self.sensorium = Sensorium.from_dict(data["sensorium"])
                    self.sensorium.session_id = self.session_id
            except (json.JSONDecodeError, IOError):
                pass

    def _save_session(self):
        """保存会话案例"""
        path = os.path.join(self.storage_dir, f"{self.session_id}.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    "session_id": self.session_id,
                    "updated_at": time.time(),
                    "sensorium": self.sensorium.to_dict(),
                    "cases": [c.to_dict() for c in self.cases],
                }, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def _recompute_adoption_rate(self):
        """重新计算采纳率"""
        adopted = [c for c in self.cases if c.user_adopted is not None]
        if adopted:
            self.sensorium.skill_adoption_rate = sum(
                1 for c in adopted if c.user_adopted
            ) / len(adopted)


# ── Sensorium 便捷工厂 ──

def sensorium_from_query(query: str, complexity: float = 0.0,
                         confusion: float = 0.0,
                         correction_rate: float = 0.0,
                         session_id: str = "unknown",
                         session_age: int = 0) -> Sensorium:
    """从查询参数构建 Sensorium"""
    s = Sensorium(session_id=session_id, session_age=session_age)
    s.current_complexity = complexity
    s.current_confusion = confusion
    s.correction_rate = correction_rate

    h = time.localtime().tm_hour
    if h < 12:
        s.time_of_day = "morning"
    elif h < 18:
        s.time_of_day = "afternoon"
    else:
        s.time_of_day = "night"

    return s
