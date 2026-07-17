#!/usr/bin/env python3
"""
防幻觉守护系统 (Hallucination Guard System)

集成多种防幻觉机制：
1. SELF-FAMILIARITY - 自熟悉度检测（生成前）
2. SOURCE_TRACING - 来源溯源
3. CONFLICT_DETECTION - 冲突检测
4. TEMPORAL_VALIDITY - 时效性标记
5. CONFIDENCE_DECAY - 置信度衰减
6. OUTPUT_VALIDATION - 输出验证
7. UNCERTAINTY_EXPRESSION - 不确定性表达
8. MULTI_AGENT_VERIFY - 多智能体验证
9. KG_VERIFICATION - 知识图谱验证
10. ADAPTIVE_RETRIEVAL - 自适应检索

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-20
"""

import json
import os
import re
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple, Set
from dataclasses import dataclass, asdict, field
from enum import Enum
import math
from galaxyos.shared.paths import workspace


# ==================== 数据结构 ====================

class SourceType(Enum):
    """记忆来源类型"""
    USER_DIRECT = "user_direct"           # 用户直接陈述
    USER_CORRECTION = "user_correction"   # 用户纠正
    AI_INFERENCE = "ai_inference"         # AI 推断
    EXTERNAL_DOC = "external_doc"         # 外部文档
    WEB_SEARCH = "web_search"             # 网络搜索
    SYSTEM_RULE = "system_rule"           # 系统规则
    AI_JUDGE = "ai_judge"                 # AI 评价（旧数据兼容）
    DC_JUDGE = "dc_judge"                 # DC 评价（旧数据兼容）
    UNKNOWN = "unknown"                   # 未知来源


class ConfidenceLevel(Enum):
    """置信度等级"""
    VERY_HIGH = "very_high"   # >= 0.9
    HIGH = "high"             # >= 0.7
    MEDIUM = "medium"         # >= 0.5
    LOW = "low"               # >= 0.3
    VERY_LOW = "very_low"     # < 0.3


class VerificationStatus(Enum):
    """验证状态"""
    UNVERIFIED = "unverified"       # 未验证
    VERIFIED_TRUE = "verified_true" # 验证为真
    VERIFIED_FALSE = "verified_false"  # 验证为假
    CONFLICTING = "conflicting"     # 存在冲突
    EXPIRED = "expired"             # 已过期


@dataclass
class VerifiedMemory:
    """带验证的记忆"""
    id: str
    content: str
    source: SourceType
    confidence: float  # 0.0 - 1.0

    # 时间信息
    created_at: str
    valid_from: str = ""
    valid_until: str = ""  # 空 = 永久有效

    # 验证信息
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verified_at: str = ""
    verified_by: str = ""  # "user" | "kg" | "multi_agent" | "external"

    # 关联信息
    related_entities: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)  # 支撑证据
    conflict_ids: List[str] = field(default_factory=list)  # 冲突记忆

    # 元数据
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5  # 0.0 - 1.0

    def to_dict(self) -> Dict:
        result = asdict(self)
        result["source"] = self.source.value
        result["verification_status"] = self.verification_status.value
        return result

    @classmethod
    def from_dict(cls, data: Dict) -> 'VerifiedMemory':
        data["source"] = SourceType(data["source"])
        data["verification_status"] = VerificationStatus(data["verification_status"])
        # 只提取 VerifiedMemory 已知字段，忽略 extra fields（如 original）
        known_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known_fields)

    def is_expired(self) -> bool:
        """检查是否过期"""
        if not self.valid_until:
            return False
        try:
            expire_time = datetime.fromisoformat(self.valid_until.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) > expire_time
        except:
            return False

    def get_effective_confidence(self) -> float:
        """计算有效置信度（考虑衰减）"""
        base = self.confidence

        # 来源可信度调整
        source_weights = {
            SourceType.USER_DIRECT: 0.95,
            SourceType.USER_CORRECTION: 0.98,
            SourceType.SYSTEM_RULE: 0.90,
            SourceType.EXTERNAL_DOC: 0.75,
            SourceType.WEB_SEARCH: 0.70,
            SourceType.AI_INFERENCE: 0.50,
            SourceType.UNKNOWN: 0.30,
        }
        source_factor = source_weights.get(self.source, 0.5)

        # 时间衰减
        try:
            created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created).days
            age_penalty = min(age_days * 0.005, 0.3)  # 每天衰减 0.5%，最多 30%
        except:
            age_penalty = 0

        # 验证加成
        verification_bonus = 0.0
        if self.verification_status == VerificationStatus.VERIFIED_TRUE:
            verification_bonus = 0.15
        elif self.verification_status == VerificationStatus.VERIFIED_FALSE:
            verification_bonus = -0.5  # 验证为假，大幅降权
        elif self.verification_status == VerificationStatus.CONFLICTING:
            verification_bonus = -0.2

        # 过期惩罚
        expired_penalty = 0.4 if self.is_expired() else 0

        effective = base * source_factor - age_penalty + verification_bonus - expired_penalty
        return max(0.0, min(1.0, effective))


# ==================== 1. 自熟悉度检测 ====================

class SelfFamiliarityChecker:
    """
    SELF-FAMILIARITY 检测器

    在生成前评估模型对概念的熟悉程度
    """

    # 熟悉度阈值
    FAMILIARITY_THRESHOLD = 0.15  # 低于此值拒绝回答（降低阈值，减少误拒）

    def __init__(self, memory_store_path: str = None):
        self.memory_store_path = Path(memory_store_path or
            os.path.expanduser("~/.openclaw/workspace/.learnings/verified_memories.jsonl"))

        # 概念频率缓存
        self._concept_freq: Dict[str, int] = {}
        self._total_memories = 0
        self._loaded = False

    def _load(self):
        """加载记忆统计概念频率"""
        if self._loaded:
            return

        if not self.memory_store_path.exists():
            self._loaded = True
            return

        with open(self.memory_store_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    self._total_memories += 1
                    memory = json.loads(line)
                    # 提取概念（简化：分词）
                    content = memory.get("content", "")
                    words = re.findall(r'[\w\u4e00-\u9fff]+', content.lower())
                    for word in words:
                        if len(word) >= 2:  # 忽略单字
                            self._concept_freq[word] = self._concept_freq.get(word, 0) + 1

        self._loaded = True

    def check_familiarity(self, concept: str) -> float:
        """
        检查对概念的熟悉程度

        Args:
            concept: 概念词

        Returns:
            熟悉度 0.0 - 1.0
        """
        self._load()

        if self._total_memories == 0:
            return 0.3  # 无记忆时返回中等偏低

        freq = self._concept_freq.get(concept.lower(), 0)

        # 归一化：出现次数越多越熟悉
        # 使用对数平滑
        if freq == 0:
            return 0.35  # 从未见过的概念，给中等偏低分数

        max_freq = max(self._concept_freq.values()) if self._concept_freq else 1
        familiarity = 0.3 + 0.7 * math.log(freq + 1) / math.log(max_freq + 1)

        return min(1.0, familiarity)

    def check_query_familiarity(self, query: str) -> Tuple[float, List[str]]:
        """
        检查查询的整体熟悉度

        Args:
            query: 用户查询

        Returns:
            (整体熟悉度, 不熟悉的概念列表)
        """
        words = re.findall(r'[\w\u4e00-\u9fff]+', query.lower())
        concepts = [w for w in words if len(w) >= 2]

        if not concepts:
            return 0.5, []

        familiarities = []
        unfamiliar = []

        for concept in concepts:
            fam = self.check_familiarity(concept)
            familiarities.append(fam)
            if fam < self.FAMILIARITY_THRESHOLD:
                unfamiliar.append(concept)

        # 整体熟悉度 = 平均值（而非最小值，避免单个不熟悉概念导致拒绝）
        overall = sum(familiarities) / len(familiarities) if familiarities else 0.5

        return overall, unfamiliar

    def should_refuse(self, query: str) -> Tuple[bool, str]:
        """
        判断是否应该拒绝回答

        Args:
            query: 用户查询

        Returns:
            (是否拒绝, 原因)
        """
        overall, unfamiliar = self.check_query_familiarity(query)

        # 只有当整体熟悉度低于阈值 且 有不熟悉的概念时才拒绝
        # 过滤掉过长的"概念"（通常是整个查询被当作一个概念）
        real_unfamiliar = [c for c in unfamiliar if len(c) <= 6]

        if overall < self.FAMILIARITY_THRESHOLD and real_unfamiliar:
            return True, f"我对以下概念不够熟悉：{', '.join(real_unfamiliar)}，无法确定回答的准确性"

        return False, ""


# ==================== 2. 来源溯源 ====================

class SourceTracer:
    """来源溯源器"""

    @staticmethod
    def determine_source(context: Dict) -> SourceType:
        """
        根据上下文判断来源类型

        Args:
            context: 包含来源信息的上下文

        Returns:
            SourceType
        """
        # 显式标记
        if context.get("source"):
            source_str = context["source"].lower()
            if "user" in source_str and "correct" in source_str:
                return SourceType.USER_CORRECTION
            elif "user" in source_str:
                return SourceType.USER_DIRECT
            elif "inference" in source_str or "推断" in source_str:
                return SourceType.AI_INFERENCE
            elif "doc" in source_str or "文档" in source_str:
                return SourceType.EXTERNAL_DOC
            elif "web" in source_str or "搜索" in source_str:
                return SourceType.WEB_SEARCH
            elif "rule" in source_str or "规则" in source_str:
                return SourceType.SYSTEM_RULE

        # 隐式推断
        if context.get("is_user_statement"):
            return SourceType.USER_DIRECT
        elif context.get("is_correction"):
            return SourceType.USER_CORRECTION
        elif context.get("is_inferred"):
            return SourceType.AI_INFERENCE
        elif context.get("from_document"):
            return SourceType.EXTERNAL_DOC

        return SourceType.UNKNOWN

    @staticmethod
    def get_source_trust(source: SourceType) -> float:
        """获取来源可信度"""
        trust_map = {
            SourceType.USER_DIRECT: 0.95,
            SourceType.USER_CORRECTION: 0.98,
            SourceType.SYSTEM_RULE: 0.90,
            SourceType.EXTERNAL_DOC: 0.75,
            SourceType.WEB_SEARCH: 0.70,
            SourceType.AI_INFERENCE: 0.50,
            SourceType.UNKNOWN: 0.30,
        }
        return trust_map.get(source, 0.5)


# ==================== 3. 冲突检测 ====================

class ConflictDetector:
    """冲突检测器"""

    # 冲突关键词对（简化版，不做分组匹配）
    CONFLICT_PATTERNS = [
        ("是", "不是"),
        ("应该", "不应该"),
        ("正确", "错误"),
        ("对", "错"),
        ("可以", "不可以"),
        ("支持", "不支持"),
        ("存在", "不存在"),
    ]

    def __init__(self, memory_store_path: str = None):
        self.memory_store_path = Path(memory_store_path or
            os.path.expanduser("~/.openclaw/workspace/.learnings/verified_memories.jsonl"))

    def detect_conflicts(self, new_memory: str, existing_memories: List[VerifiedMemory]) -> List[VerifiedMemory]:
        """
        检测新记忆是否与已有记忆冲突

        Args:
            new_memory: 新记忆内容
            existing_memories: 已有记忆列表

        Returns:
            冲突的记忆列表
        """
        conflicts = []
        new_lower = new_memory.lower()

        for memory in existing_memories:
            if memory.verification_status == VerificationStatus.VERIFIED_FALSE:
                continue  # 已验证为假的记忆不参与冲突检测

            old_lower = memory.content.lower()

            # 检查冲突模式
            for pattern1, pattern2 in self.CONFLICT_PATTERNS:
                if pattern1 in new_lower and pattern2 in old_lower:
                    conflicts.append(memory)
                    break
                elif pattern2 in new_lower and pattern1 in old_lower:
                    conflicts.append(memory)
                    break

        return conflicts

    def resolve_conflict(self, new_memory: VerifiedMemory, conflicting: List[VerifiedMemory]) -> VerifiedMemory:
        """
        解决冲突

        策略：高置信度优先，用户纠正最高
        """
        # 用户纠正优先级最高
        if new_memory.source == SourceType.USER_CORRECTION:
            # 标记冲突记忆为假
            for mem in conflicting:
                mem.verification_status = VerificationStatus.VERIFIED_FALSE
                mem.conflict_ids.append(new_memory.id)

            new_memory.verification_status = VerificationStatus.VERIFIED_TRUE
            for mem in conflicting:
                new_memory.conflict_ids.append(mem.id)

            return new_memory

        # 否则比较置信度
        max_old_confidence = max(m.get_effective_confidence() for m in conflicting) if conflicting else 0

        if new_memory.get_effective_confidence() > max_old_confidence:
            # 新记忆胜出
            for mem in conflicting:
                mem.verification_status = VerificationStatus.CONFLICTING
                mem.conflict_ids.append(new_memory.id)

            new_memory.verification_status = VerificationStatus.VERIFIED_TRUE
        else:
            # 旧记忆胜出
            new_memory.verification_status = VerificationStatus.CONFLICTING

        return new_memory


# ==================== 4. 时效性管理 ====================

class TemporalValidator:
    """时效性验证器"""

    # 不同类型记忆的默认有效期（天）
    DEFAULT_VALIDITY = {
        SourceType.USER_DIRECT: 365,      # 用户陈述：1 年
        SourceType.USER_CORRECTION: 365,  # 用户纠正：1 年
        SourceType.SYSTEM_RULE: 9999,     # 系统规则：永久
        SourceType.EXTERNAL_DOC: 180,     # 外部文档：半年
        SourceType.WEB_SEARCH: 30,        # 网络搜索：1 月
        SourceType.AI_INFERENCE: 90,      # AI 推断：3 月
        SourceType.UNKNOWN: 30,           # 未知：1 月
    }

    @staticmethod
    def set_validity(memory: VerifiedMemory, custom_days: int = None):
        """设置记忆有效期"""
        if custom_days:
            valid_until = datetime.now(timezone.utc) + timedelta(days=custom_days)
        else:
            days = TemporalValidator.DEFAULT_VALIDITY.get(memory.source, 30)
            valid_until = datetime.now(timezone.utc) + timedelta(days=days)

        memory.valid_until = valid_until.isoformat()

    @staticmethod
    def check_and_mark_expired(memories: List[VerifiedMemory]) -> List[VerifiedMemory]:
        """检查并标记过期记忆"""
        expired = []

        for memory in memories:
            if memory.is_expired() and memory.verification_status != VerificationStatus.EXPIRED:
                memory.verification_status = VerificationStatus.EXPIRED
                expired.append(memory)

        return expired


# ==================== 5. 置信度计算 ====================

class ConfidenceCalculator:
    """置信度计算器"""

    @staticmethod
    def calculate_initial_confidence(source: SourceType, has_evidence: bool = False) -> float:
        """计算初始置信度"""
        base = SourceTracer.get_source_trust(source)

        if has_evidence:
            base = min(1.0, base + 0.1)

        return base

    @staticmethod
    def get_confidence_level(confidence: float) -> ConfidenceLevel:
        """获取置信度等级"""
        if confidence >= 0.9:
            return ConfidenceLevel.VERY_HIGH
        elif confidence >= 0.7:
            return ConfidenceLevel.HIGH
        elif confidence >= 0.5:
            return ConfidenceLevel.MEDIUM
        elif confidence >= 0.3:
            return ConfidenceLevel.LOW
        else:
            return ConfidenceLevel.VERY_LOW


# ==================== 6. 输出验证 ====================

class OutputValidator:
    """输出验证器"""

    def __init__(self, memory_store_path: str = None):
        self.memory_store_path = Path(memory_store_path or
            os.path.expanduser("~/.openclaw/workspace/.learnings/verified_memories.jsonl"))
        self._memories: List[VerifiedMemory] = []
        self._loaded = False

    def _load(self):
        """加载记忆"""
        if self._loaded:
            return

        if not self.memory_store_path.exists():
            self._loaded = True
            return

        with open(self.memory_store_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    self._memories.append(VerifiedMemory.from_dict(json.loads(line)))

        self._loaded = True

    def validate_output(self, output: str, recalled_memories: List[VerifiedMemory] = None) -> Dict:
        """
        验证输出

        Args:
            output: AI 输出内容
            recalled_memories: 召回的记忆列表

        Returns:
            {
                "is_valid": bool,
                "issues": [...],
                "unsupported_claims": [...],
                "low_confidence_parts": [...]
            }
        """
        self._load()

        result = {
            "is_valid": True,
            "issues": [],
            "unsupported_claims": [],
            "low_confidence_parts": []
        }

        # 检查召回记忆的置信度
        if recalled_memories:
            for memory in recalled_memories:
                conf = memory.get_effective_confidence()

                if conf < 0.5:
                    result["low_confidence_parts"].append({
                        "content": memory.content[:50],
                        "confidence": conf,
                        "reason": "低置信度记忆"
                    })

                if memory.verification_status == VerificationStatus.VERIFIED_FALSE:
                    result["issues"].append({
                        "type": "used_false_memory",
                        "content": memory.content[:50],
                        "severity": "high"
                    })
                    result["is_valid"] = False

                if memory.verification_status == VerificationStatus.EXPIRED:
                    result["issues"].append({
                        "type": "used_expired_memory",
                        "content": memory.content[:50],
                        "severity": "medium"
                    })

        return result


# ==================== 7. 不确定性表达 ====================

class UncertaintyExpresser:
    """不确定性表达器"""

    @staticmethod
    def express_with_uncertainty(content: str, confidence: float,
                                  unfamiliar_concepts: List[str] = None) -> str:
        """
        根据置信度添加不确定性表达

        Args:
            content: 原始内容
            confidence: 置信度
            unfamiliar_concepts: 不熟悉的概念

        Returns:
            带不确定性标记的内容
        """
        level = ConfidenceCalculator.get_confidence_level(confidence)

        if level == ConfidenceLevel.VERY_HIGH:
            return content

        elif level == ConfidenceLevel.HIGH:
            # 高置信度，轻微提示
            prefix = "根据我的了解，"
            return f"{prefix}{content}"

        elif level == ConfidenceLevel.MEDIUM:
            # 中等置信度，明确表达不确定
            prefix = "我记得好像是"
            suffix = "，但建议你确认一下"
            return f"{prefix}{content}{suffix}"

        elif level == ConfidenceLevel.LOW:
            # 低置信度，强烈建议验证
            prefix = "我不太确定，可能是"
            suffix = "。这个信息可能不准确，建议你查证"
            return f"{prefix}{content}{suffix}"

        else:  # VERY_LOW
            # 极低置信度，建议不回答
            if unfamiliar_concepts:
                return f"我对「{'、'.join(unfamiliar_concepts)}」这些概念不够了解，无法给出可靠回答。建议你查阅权威资料。"
            else:
                return "我对这个问题的了解很有限，无法给出可靠回答。建议你查阅权威资料。"

    @staticmethod
    def add_evidence_markers(content: str, evidence_sources: List[str]) -> str:
        """添加证据来源标记"""
        if not evidence_sources:
            return content

        sources_str = "、".join(evidence_sources[:3])  # 最多显示 3 个
        return f"{content}\n\n📚 参考：{sources_str}"


# ==================== 8. 多智能体验证 ====================

class MultiAgentVerifier:
    """
    多智能体验证器（简化版）

    通过不同视角的自我质询来验证陈述
    """

    VERIFY_PROMPTS = [
        "这个说法有证据支撑吗？",
        "有没有反例或例外情况？",
        "这个信息是否可能已经过时？",
        "是否存在歧义或多重理解？",
    ]

    @staticmethod
    def verify_statement(statement: str, context: Dict = None) -> Dict:
        """
        验证陈述（简化实现）

        Args:
            statement: 待验证的陈述
            context: 上下文

        Returns:
            {
                "is_reliable": bool,
                "confidence": float,
                "issues": [...],
                "suggestions": [...]
            }
        """
        # 简化实现：基于启发式规则
        issues = []
        suggestions = []

        # 检查绝对化表述
        absolute_patterns = [
            (r"一定", "过于绝对"),
            (r"肯定", "过于绝对"),
            (r"绝对", "过于绝对"),
            (r"所有.*都", "可能存在例外"),
            (r"没有任何", "可能存在例外"),
        ]

        for pattern, issue in absolute_patterns:
            if re.search(pattern, statement):
                issues.append(issue)
                suggestions.append("建议改为更谨慎的表述")

        # 检查是否有数据支撑
        if re.search(r'\d+', statement) and not re.search(r'来源|根据|数据显示', statement):
            issues.append("包含数据但未注明来源")
            suggestions.append("建议添加数据来源")

        # 检查时效性表述
        if not re.search(r'目前|现在|截至|当前', statement):
            if re.search(r'是|为|有', statement):
                issues.append("可能缺乏时效性说明")

        confidence = 1.0 - len(issues) * 0.15
        confidence = max(0.3, confidence)

        return {
            "is_reliable": len(issues) == 0,
            "confidence": confidence,
            "issues": issues,
            "suggestions": suggestions
        }


# ==================== 9. 知识图谱验证 ====================

class KnowledgeGraphVerifier:
    """
    知识图谱验证器

    利用 ontology 模块验证实体和关系
    """

    def __init__(self, ontology_path: str = None):
        self.ontology_path = Path(ontology_path or
            os.path.expanduser("~/.openclaw/workspace/.learnings/ontology.json"))
        self._entities: Dict[str, Dict] = {}
        self._relations: List[Dict] = []
        self._loaded = False

    def _load(self):
        """加载知识图谱"""
        if self._loaded:
            return

        if not self.ontology_path.exists():
            self._loaded = True
            return

        try:
            with open(self.ontology_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._entities = data.get("entities", {})
                self._relations = data.get("relations", [])
        except:
            pass

        self._loaded = True

    def verify_entity(self, entity_name: str) -> Tuple[bool, Optional[Dict]]:
        """
        验证实体是否存在

        Args:
            entity_name: 实体名称

        Returns:
            (是否存在, 实体信息)
        """
        self._load()

        # 精确匹配
        if entity_name in self._entities:
            return True, self._entities[entity_name]

        # 模糊匹配
        for name, info in self._entities.items():
            if entity_name.lower() in name.lower() or name.lower() in entity_name.lower():
                return True, info

        return False, None

    def verify_relation(self, subject: str, relation: str, obj: str) -> Tuple[bool, float]:
        """
        验证关系是否成立

        Args:
            subject: 主体
            relation: 关系
            obj: 客体

        Returns:
            (是否成立, 置信度)
        """
        self._load()

        for rel in self._relations:
            if (rel.get("subject", "").lower() == subject.lower() and
                rel.get("relation", "").lower() == relation.lower() and
                rel.get("object", "").lower() == obj.lower()):
                return True, 0.9

        return False, 0.0

    def extract_and_verify(self, text: str) -> Dict:
        """
        提取文本中的实体和关系并验证

        Args:
            text: 待验证文本

        Returns:
            {
                "entities_found": [...],
                "entities_missing": [...],
                "relations_verified": [...],
                "relations_unverified": [...]
            }
        """
        self._load()

        result = {
            "entities_found": [],
            "entities_missing": [],
            "relations_verified": [],
            "relations_unverified": []
        }

        # 简化实现：检查已知实体
        for entity_name in self._entities.keys():
            if entity_name in text:
                result["entities_found"].append(entity_name)

        return result


# ==================== 10. 自适应检索 ====================

class AdaptiveRetriever:
    """
    自适应检索器

    根据置信度决定是否需要外部检索
    """

    RETRIEVAL_THRESHOLD = 0.6  # 低于此值触发检索

    def __init__(self, familiarity_checker: SelfFamiliarityChecker = None):
        self.familiarity_checker = familiarity_checker or SelfFamiliarityChecker()

    def should_retrieve(self, query: str, internal_confidence: float = None) -> Tuple[bool, str]:
        """
        判断是否需要外部检索

        Args:
            query: 用户查询
            internal_confidence: 内部置信度（可选）

        Returns:
            (是否需要检索, 原因)
        """
        # 检查熟悉度
        familiarity, unfamiliar = self.familiarity_checker.check_query_familiarity(query)

        if familiarity < self.RETRIEVAL_THRESHOLD:
            return True, f"对概念「{'、'.join(unfamiliar)}」不够熟悉"

        # 检查内部置信度
        if internal_confidence is not None and internal_confidence < self.RETRIEVAL_THRESHOLD:
            return True, f"内部置信度较低 ({internal_confidence:.2f})"

        # 检查是否需要最新信息
        temporal_keywords = ["最新", "现在", "当前", "今年", "最近", "latest", "current"]
        for keyword in temporal_keywords:
            if keyword in query.lower():
                return True, "需要最新信息"

        return False, "内部知识足够"


# ==================== 主类：防幻觉守护系统 ====================

class HallucinationGuard:
    """
    防幻觉守护系统 - 统一接口

    使用示例:
        guard = HallucinationGuard()

        # 1. 生成前检查
        should_refuse, reason = guard.check_before_generation(query)
        if should_refuse:
            return reason

        # 2. 存储记忆时验证
        memory = guard.create_verified_memory(content, source="user")

        # 3. 召回时过滤
        valid_memories = guard.filter_valid_memories(memories)

        # 4. 输出前验证
        validation = guard.validate_output(output, recalled_memories)

        # 5. 添加不确定性表达
        final_output = guard.express_with_confidence(output, confidence)
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or
            workspace())
        self.store_path = self.workspace_path / ".learnings" / "verified_memories.jsonl"

        # 确保目录存在
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self.store_path.touch()

        # 初始化各模块
        self.familiarity_checker = SelfFamiliarityChecker(str(self.store_path))
        self.source_tracer = SourceTracer()
        self.conflict_detector = ConflictDetector(str(self.store_path))
        self.temporal_validator = TemporalValidator()
        self.confidence_calculator = ConfidenceCalculator()
        self.output_validator = OutputValidator(str(self.store_path))
        self.uncertainty_expresser = UncertaintyExpresser()
        self.multi_agent_verifier = MultiAgentVerifier()
        self.kg_verifier = KnowledgeGraphVerifier()
        self.adaptive_retriever = AdaptiveRetriever(self.familiarity_checker)

    def _generate_id(self) -> str:
        """生成唯一 ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = hashlib.md5(str(datetime.now().timestamp()).encode()).hexdigest()[:8]
        return f"VM-{timestamp}-{random_suffix}"

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        return datetime.now(timezone.utc).isoformat()

    # ==================== 生成前检查 ====================

    def check_before_generation(self, query: str) -> Tuple[bool, str]:
        """
        生成前检查

        Args:
            query: 用户查询

        Returns:
            (是否应该拒绝, 原因)
        """
        # 1. 自熟悉度检查
        should_refuse, reason = self.familiarity_checker.should_refuse(query)
        if should_refuse:
            return True, reason

        # 2. 判断是否需要检索
        need_retrieve, reason = self.adaptive_retriever.should_retrieve(query)
        if need_retrieve:
            # 这里不拒绝，而是标记需要检索
            return False, f"[建议检索] {reason}"

        return False, ""

    # ==================== 记忆创建与验证 ====================

    def create_verified_memory(
        self,
        content: str,
        source: str = "unknown",
        context: Dict = None,
        custom_validity_days: int = None
    ) -> VerifiedMemory:
        """
        创建带验证的记忆

        Args:
            content: 记忆内容
            source: 来源类型
            context: 上下文
            custom_validity_days: 自定义有效期（天）

        Returns:
            VerifiedMemory
        """
        context = context or {}

        # 确定来源类型
        source_type = self.source_tracer.determine_source({"source": source, **context})

        # 计算初始置信度
        confidence = self.confidence_calculator.calculate_initial_confidence(
            source_type,
            has_evidence=context.get("has_evidence", False)
        )

        # 创建记忆
        memory = VerifiedMemory(
            id=self._generate_id(),
            content=content,
            source=source_type,
            confidence=confidence,
            created_at=self._get_timestamp(),
            valid_from=self._get_timestamp()
        )

        # 设置有效期
        self.temporal_validator.set_validity(memory, custom_validity_days)

        # 检测冲突
        existing = self._load_memories()
        conflicts = self.conflict_detector.detect_conflicts(content, existing)

        if conflicts:
            memory = self.conflict_detector.resolve_conflict(memory, conflicts)

        # 保存
        self._save_memory(memory)

        return memory

    def _load_memories(self) -> List[VerifiedMemory]:
        """加载所有记忆（跳过不兼容行）"""
        memories = []

        with open(self.store_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    memories.append(VerifiedMemory.from_dict(data))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue  # 跳过格式不兼容或枚举值无效的旧数据

        return memories

    def _save_memory(self, memory: VerifiedMemory):
        """保存记忆"""
        with open(self.store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(memory.to_dict(), ensure_ascii=False) + "\n")

    # ==================== 召回过滤 ====================

    def filter_valid_memories(self, memories: List[VerifiedMemory]) -> List[VerifiedMemory]:
        """
        过滤有效记忆

        排除：已验证为假、已过期、置信度过低
        """
        valid = []

        for memory in memories:
            # 排除验证为假的
            if memory.verification_status == VerificationStatus.VERIFIED_FALSE:
                continue

            # 排除过期的
            if memory.is_expired():
                continue

            # 排除置信度过低的
            if memory.get_effective_confidence() < 0.3:
                continue

            valid.append(memory)

        # 按有效置信度排序
        valid.sort(key=lambda m: m.get_effective_confidence(), reverse=True)

        return valid

    # ==================== 输出验证 ====================

    def validate_output(self, output: str, recalled_memories: List[VerifiedMemory] = None) -> Dict:
        """
        验证输出

        Args:
            output: AI 输出
            recalled_memories: 召回的记忆

        Returns:
            验证结果
        """
        return self.output_validator.validate_output(output, recalled_memories)

    # ==================== 不确定性表达 ====================

    def express_with_confidence(
        self,
        content: str,
        confidence: float,
        unfamiliar_concepts: List[str] = None,
        evidence_sources: List[str] = None
    ) -> str:
        """
        根据置信度表达内容

        Args:
            content: 原始内容
            confidence: 置信度
            unfamiliar_concepts: 不熟悉的概念
            evidence_sources: 证据来源

        Returns:
            带不确定性标记的内容
        """
        result = self.uncertainty_expresser.express_with_uncertainty(
            content, confidence, unfamiliar_concepts
        )

        if evidence_sources:
            result = self.uncertainty_expresser.add_evidence_markers(result, evidence_sources)

        return result

    # ==================== 多智能体验证 ====================

    def verify_statement(self, statement: str, context: Dict = None) -> Dict:
        """验证陈述"""
        return MultiAgentVerifier.verify_statement(statement, context)

    # ==================== 知识图谱验证 ====================

    def verify_with_kg(self, text: str) -> Dict:
        """用知识图谱验证文本"""
        return self.kg_verifier.extract_and_verify(text)

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict:
        """获取系统统计"""
        memories = self._load_memories()

        # 按来源统计
        source_counts = {}
        for memory in memories:
            source = memory.source.value
            source_counts[source] = source_counts.get(source, 0) + 1

        # 按验证状态统计
        status_counts = {}
        for memory in memories:
            status = memory.verification_status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        # 平均置信度
        avg_confidence = sum(m.get_effective_confidence() for m in memories) / len(memories) if memories else 0

        return {
            "total_memories": len(memories),
            "source_distribution": source_counts,
            "verification_distribution": status_counts,
            "average_confidence": round(avg_confidence, 3),
            "expired_count": len([m for m in memories if m.is_expired()]),
            "high_confidence_count": len([m for m in memories if m.get_effective_confidence() >= 0.7]),
        }


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="防幻觉守护系统")
    parser.add_argument("command", choices=[
        "check", "create", "filter", "validate", "stats", "verify"
    ])
    parser.add_argument("--query", help="用户查询")
    parser.add_argument("--content", help="记忆内容")
    parser.add_argument("--source", default="unknown", help="来源类型")
    parser.add_argument("--output", help="待验证的输出")

    args = parser.parse_args()

    guard = HallucinationGuard()

    if args.command == "check":
        if not args.query:
            print("错误: 需要提供 --query")
            return

        should_refuse, reason = guard.check_before_generation(args.query)
        if should_refuse:
            print(f"❌ 建议拒绝回答: {reason}")
        else:
            print(f"✅ 可以回答: {reason}")

    elif args.command == "create":
        if not args.content:
            print("错误: 需要提供 --content")
            return

        memory = guard.create_verified_memory(args.content, args.source)
        print(f"✅ 创建记忆: {memory.id}")
        print(f"   来源: {memory.source.value}")
        print(f"   置信度: {memory.confidence}")
        print(f"   有效期至: {memory.valid_until}")

    elif args.command == "filter":
        memories = guard._load_memories()
        valid = guard.filter_valid_memories(memories)
        print(f"总记忆: {len(memories)}, 有效: {len(valid)}")
        for m in valid[:5]:
            print(f"  - [{m.get_effective_confidence():.2f}] {m.content[:50]}...")

    elif args.command == "validate":
        if not args.output:
            print("错误: 需要提供 --output")
            return

        result = guard.validate_output(args.output)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "stats":
        stats = guard.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif args.command == "verify":
        if not args.content:
            print("错误: 需要提供 --content")
            return

        result = guard.verify_statement(args.content)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
