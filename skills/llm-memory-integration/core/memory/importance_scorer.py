#!/usr/bin/env python3
"""
重要性评分器

为记忆计算重要性分数，支持智能遗忘决策。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

logger = logging.getLogger(__name__)


@dataclass
class ImportanceScore:
    """重要性分数"""
    memory_id: str
    total_score: float
    components: Dict[str, float]
    level: str  # 'critical', 'high', 'medium', 'low', 'trivial'
    recommendation: str  # 'keep', 'archive', 'delete'


class ImportanceScorer:
    """
    记忆重要性评分器
    
    评分维度:
    1. 访问频率 (Access Frequency)
    2. 时间衰减 (Time Decay)
    3. 情感权重 (Emotional Weight)
    4. 用户反馈 (User Feedback)
    5. 实体关联 (Entity Connection)
    6. 内容质量 (Content Quality)
    """
    
    # 权重配置
    DEFAULT_WEIGHTS = {
        'access_frequency': 0.25,
        'time_decay': 0.20,
        'emotional_weight': 0.15,
        'user_feedback': 0.20,
        'entity_connection': 0.10,
        'content_quality': 0.10
    }
    
    # 时间衰减参数
    HALF_LIFE_DAYS = 30  # 半衰期：30天
    MIN_DECAY = 0.1  # 最小衰减值
    
    # 重要性阈值
    LEVELS = {
        'critical': 0.8,
        'high': 0.6,
        'medium': 0.4,
        'low': 0.2,
        'trivial': 0.0
    }
    
    def __init__(self, 
                 weights: Optional[Dict[str, float]] = None,
                 stats_path: Optional[str] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        
        if stats_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path.home() / '.openclaw' / 'workspace')
            stats_path = str(Path(workspace) / 'memory' / 'importance_stats.json')
        
        self.stats_path = stats_path
        self.access_stats: Dict[str, Dict] = {}  # memory_id -> access stats
        self.feedback_stats: Dict[str, Dict] = {}  # memory_id -> feedback stats
        self._load_stats()
    
    def _load_stats(self):
        """加载统计数据"""
        if not Path(self.stats_path).exists():
            return
        
        with open(self.stats_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.access_stats = data.get('access_stats', {})
        self.feedback_stats = data.get('feedback_stats', {})
        
        logger.info(f"加载重要性统计: {len(self.access_stats)} 条访问记录")
    
    def _save_stats(self):
        """保存统计数据"""
        Path(self.stats_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.stats_path, 'w', encoding='utf-8') as f:
            json.dump({
                'access_stats': self.access_stats,
                'feedback_stats': self.feedback_stats,
                'updated_at': datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def record_access(self, memory_id: str, access_type: str = 'recall'):
        """
        记录访问
        
        Args:
            memory_id: 记忆 ID
            access_type: 访问类型 ('recall', 'reference', 'update')
        """
        if memory_id not in self.access_stats:
            self.access_stats[memory_id] = {
                'total_accesses': 0,
                'first_access': datetime.now().isoformat(),
                'last_access': datetime.now().isoformat(),
                'access_types': {}
            }
        
        stats = self.access_stats[memory_id]
        stats['total_accesses'] += 1
        stats['last_access'] = datetime.now().isoformat()
        stats['access_types'][access_type] = stats['access_types'].get(access_type, 0) + 1
        
        self._save_stats()
    
    def record_feedback(self, memory_id: str, feedback_type: str, weight: float = 1.0):
        """
        记录用户反馈
        
        Args:
            memory_id: 记忆 ID
            feedback_type: 反馈类型 ('positive', 'negative', 'correct', 'important')
            weight: 反馈权重
        """
        if memory_id not in self.feedback_stats:
            self.feedback_stats[memory_id] = {
                'positive_count': 0,
                'negative_count': 0,
                'correct_count': 0,
                'important_count': 0,
                'total_weight': 0.0
            }
        
        stats = self.feedback_stats[memory_id]
        
        if feedback_type == 'positive':
            stats['positive_count'] += 1
            stats['total_weight'] += weight
        elif feedback_type == 'negative':
            stats['negative_count'] += 1
            stats['total_weight'] -= weight
        elif feedback_type == 'correct':
            stats['correct_count'] += 1
        elif feedback_type == 'important':
            stats['important_count'] += 1
            stats['total_weight'] += weight * 2
        
        self._save_stats()
    
    def score(self, 
              memory_id: str,
              memory_metadata: Optional[Dict] = None,
              created_at: Optional[str] = None,
              entity_count: int = 0,
              content_length: int = 0) -> ImportanceScore:
        """
        计算重要性分数
        
        Args:
            memory_id: 记忆 ID
            memory_metadata: 记忆元数据
            created_at: 创建时间
            entity_count: 关联实体数量
            content_length: 内容长度
        
        Returns:
            ImportanceScore
        """
        components = {}
        
        # 1. 访问频率分数
        components['access_frequency'] = self._score_access_frequency(memory_id)
        
        # 2. 时间衰减分数
        components['time_decay'] = self._score_time_decay(created_at)
        
        # 3. 情感权重分数
        components['emotional_weight'] = self._score_emotional_weight(memory_metadata)
        
        # 4. 用户反馈分数
        components['user_feedback'] = self._score_user_feedback(memory_id)
        
        # 5. 实体关联分数
        components['entity_connection'] = self._score_entity_connection(entity_count)
        
        # 6. 内容质量分数
        components['content_quality'] = self._score_content_quality(content_length)
        
        # 计算总分
        total_score = sum(
            components[k] * self.weights.get(k, 0)
            for k in components
        )
        
        # 确定等级
        level = 'trivial'
        for lvl, threshold in sorted(self.LEVELS.items(), key=lambda x: -x[1]):
            if total_score >= threshold:
                level = lvl
                break
        
        # 确定建议
        if level in ('critical', 'high'):
            recommendation = 'keep'
        elif level == 'medium':
            recommendation = 'keep'
        elif level == 'low':
            recommendation = 'archive'
        else:
            recommendation = 'delete'
        
        return ImportanceScore(
            memory_id=memory_id,
            total_score=round(total_score, 3),
            components={k: round(v, 3) for k, v in components.items()},
            level=level,
            recommendation=recommendation
        )
    
    def _score_access_frequency(self, memory_id: str) -> float:
        """计算访问频率分数"""
        stats = self.access_stats.get(memory_id)
        
        if not stats:
            return 0.0
        
        total_accesses = stats.get('total_accesses', 0)
        
        # 使用对数缩放，避免高频访问过度影响
        if total_accesses == 0:
            return 0.0
        
        # log(1 + accesses) / log(1 + 100) 归一化到 0-1
        max_accesses = 100
        score = math.log(1 + total_accesses) / math.log(1 + max_accesses)
        
        return min(score, 1.0)
    
    def _score_time_decay(self, created_at: Optional[str]) -> float:
        """计算时间衰减分数"""
        if not created_at:
            return 0.5  # 未知时间，给中等分数
        
        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            age_days = (datetime.now(created.tzinfo) - created).days
        except:
            return 0.5
        
        # 指数衰减: score = min_decay + (1 - min_decay) * 0.5^(age / half_life)
        decay_factor = 0.5 ** (age_days / self.HALF_LIFE_DAYS)
        score = self.MIN_DECAY + (1 - self.MIN_DECAY) * decay_factor
        
        return score
    
    def _score_emotional_weight(self, metadata: Optional[Dict]) -> float:
        """计算情感权重分数"""
        if not metadata:
            return 0.5
        
        # 检查情感标记
        emotional_keywords = [
            'important', 'critical', 'favorite', 'love', 'hate',
            '重要', '关键', '喜欢', '讨厌', '紧急'
        ]
        
        score = 0.5
        
        # 检查标签
        tags = metadata.get('tags', [])
        for tag in tags:
            tag_lower = tag.lower()
            for kw in emotional_keywords:
                if kw in tag_lower:
                    score += 0.1
        
        # 检查优先级
        priority = metadata.get('priority', '')
        if priority in ('high', 'critical', '高', '紧急'):
            score += 0.2
        elif priority in ('medium', '中'):
            score += 0.1
        
        return min(score, 1.0)
    
    def _score_user_feedback(self, memory_id: str) -> float:
        """计算用户反馈分数"""
        stats = self.feedback_stats.get(memory_id)
        
        if not stats:
            return 0.5
        
        positive = stats.get('positive_count', 0)
        negative = stats.get('negative_count', 0)
        important = stats.get('important_count', 0)
        
        total = positive + negative + important
        if total == 0:
            return 0.5
        
        # 正向反馈占比
        score = 0.5 + (positive + important * 2 - negative) / (total * 2)
        
        return max(0.0, min(score, 1.0))
    
    def _score_entity_connection(self, entity_count: int) -> float:
        """计算实体关联分数"""
        if entity_count <= 0:
            return 0.3
        
        # 使用对数缩放
        score = math.log(1 + entity_count) / math.log(1 + 10)
        
        return min(score, 1.0)
    
    def _score_content_quality(self, content_length: int) -> float:
        """计算内容质量分数"""
        if content_length <= 0:
            return 0.0
        
        # 内容长度适中为佳
        optimal_length = 500  # 最佳长度
        
        if content_length < 50:
            score = content_length / 50 * 0.5
        elif content_length < optimal_length:
            score = 0.5 + (content_length - 50) / (optimal_length - 50) * 0.5
        else:
            # 过长内容略微降分
            score = 1.0 - min(0.3, (content_length - optimal_length) / 5000)
        
        return score
    
    def batch_score(self, 
                    memories: List[Dict],
                    threshold: str = 'low') -> List[ImportanceScore]:
        """
        批量评分
        
        Args:
            memories: 记忆列表
            threshold: 过滤阈值
        
        Returns:
            评分列表
        """
        results = []
        
        for mem in memories:
            memory_id = mem.get('id', '')
            metadata = mem.get('metadata', {})
            created_at = metadata.get('created_at') or mem.get('created_at')
            entity_count = len(metadata.get('entities', []))
            content_length = len(mem.get('content', ''))
            
            score = self.score(memory_id, metadata, created_at, entity_count, content_length)
            
            # 过滤
            threshold_value = self.LEVELS.get(threshold, 0.0)
            if score.total_score >= threshold_value:
                results.append(score)
        
        # 按分数排序
        results.sort(key=lambda x: x.total_score, reverse=True)
        
        return results
    
    def get_forget_candidates(self, 
                               memories: List[Dict],
                               max_age_days: int = 90) -> List[ImportanceScore]:
        """
        获取遗忘候选
        
        Args:
            memories: 记忆列表
            max_age_days: 最大年龄
        
        Returns:
            遗忘候选列表
        """
        candidates = []
        
        for mem in memories:
            memory_id = mem.get('id', '')
            metadata = mem.get('metadata', {})
            created_at = metadata.get('created_at') or mem.get('created_at')
            
            # 检查年龄
            if created_at:
                try:
                    created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    age_days = (datetime.now(created.tzinfo) - created).days
                    if age_days < max_age_days:
                        continue
                except:
                    pass
            
            # 计算分数
            entity_count = len(metadata.get('entities', []))
            content_length = len(mem.get('content', ''))
            score = self.score(memory_id, metadata, created_at, entity_count, content_length)
            
            # 只返回建议归档或删除的
            if score.recommendation in ('archive', 'delete'):
                candidates.append(score)
        
        return candidates


# 便捷函数
_scorer = None

def get_scorer() -> ImportanceScorer:
    """获取评分器实例"""
    global _scorer
    if _scorer is None:
        _scorer = ImportanceScorer()
    return _scorer


def score_memory(memory_id: str, **kwargs) -> ImportanceScore:
    """计算记忆重要性"""
    return get_scorer().score(memory_id, **kwargs)


def record_access(memory_id: str, access_type: str = 'recall'):
    """记录访问"""
    get_scorer().record_access(memory_id, access_type)


def record_feedback(memory_id: str, feedback_type: str, weight: float = 1.0):
    """记录反馈"""
    get_scorer().record_feedback(memory_id, feedback_type, weight)
