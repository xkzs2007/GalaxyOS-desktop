#!/usr/bin/env python3
"""
自主学习模块

从用户交互中持续学习和改进。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime
import re
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)


@dataclass
class LearningEvent:
    """学习事件"""
    id: str
    event_type: str  # 'preference', 'correction', 'feedback', 'pattern'
    content: str
    context: Dict[str, Any]
    learned_at: str
    applied: bool


class AutoLearner:
    """
    自主学习器

    功能:
    1. 从用户反馈中学习偏好
    2. 从纠正中学习正确行为
    3. 从交互模式中学习习惯
    4. 自动更新用户画像
    """

    def __init__(self, learning_path: Optional[str] = None):
        if learning_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path(workspace()))
            learning_path = str(Path(workspace) / 'memory' / 'learning_events.jsonl')

        self.learning_path = learning_path
        self.events: List[LearningEvent] = []
        self.preferences: Dict[str, Any] = {}
        self.patterns: Dict[str, int] = {}
        self._load_events()

    def _load_events(self):
        """加载学习事件"""
        if not Path(self.learning_path).exists():
            return

        with open(self.learning_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    event = LearningEvent(
                        id=data.get('id', ''),
                        event_type=data.get('event_type', ''),
                        content=data.get('content', ''),
                        context=data.get('context', {}),
                        learned_at=data.get('learned_at', ''),
                        applied=data.get('applied', False)
                    )
                    self.events.append(event)
                except json.JSONDecodeError:
                    continue

        # 重建偏好和模式
        self._rebuild_state()
        logger.info(f"加载学习事件: {len(self.events)} 条")

    def _save_events(self):
        """保存学习事件"""
        Path(self.learning_path).parent.mkdir(parents=True, exist_ok=True)

        with open(self.learning_path, 'w', encoding='utf-8') as f:
            for event in self.events:
                f.write(json.dumps({
                    'id': event.id,
                    'event_type': event.event_type,
                    'content': event.content,
                    'context': event.context,
                    'learned_at': event.learned_at,
                    'applied': event.applied
                }, ensure_ascii=False) + '\n')

    def _rebuild_state(self):
        """重建状态"""
        self.preferences = {}
        self.patterns = {}

        for event in self.events:
            if event.event_type == 'preference':
                key = event.context.get('key', '')
                value = event.context.get('value')
                if key:
                    self.preferences[key] = value

            elif event.event_type == 'pattern':
                pattern = event.content
                self.patterns[pattern] = self.patterns.get(pattern, 0) + 1

    def _generate_id(self) -> str:
        """生成事件 ID"""
        import uuid
        return f"learn_{uuid.uuid4().hex[:8]}"

    def learn_preference(self, key: str, value: Any, context: Optional[Dict] = None):
        """
        学习偏好

        Args:
            key: 偏好键
            value: 偏好值
            context: 上下文
        """
        event = LearningEvent(
            id=self._generate_id(),
            event_type='preference',
            content=f"{key} = {value}",
            context={'key': key, 'value': value, **(context or {})},
            learned_at=datetime.now().isoformat(),
            applied=False
        )

        self.events.append(event)
        self.preferences[key] = value
        self._save_events()

        logger.info(f"学习偏好: {key} = {value}")

    def learn_correction(self,
                          original: str,
                          corrected: str,
                          context: Optional[Dict] = None):
        """
        学习纠正

        Args:
            original: 原始内容
            corrected: 纠正内容
            context: 上下文
        """
        event = LearningEvent(
            id=self._generate_id(),
            event_type='correction',
            content=f"{original} -> {corrected}",
            context={'original': original, 'corrected': corrected, **(context or {})},
            learned_at=datetime.now().isoformat(),
            applied=False
        )

        self.events.append(event)
        self._save_events()

        logger.info(f"学习纠正: {original} -> {corrected}")

    def learn_feedback(self,
                        feedback_type: str,
                        content: str,
                        context: Optional[Dict] = None):
        """
        学习反馈

        Args:
            feedback_type: 反馈类型 ('positive', 'negative', 'neutral')
            content: 反馈内容
            context: 上下文
        """
        event = LearningEvent(
            id=self._generate_id(),
            event_type='feedback',
            content=content,
            context={'feedback_type': feedback_type, **(context or {})},
            learned_at=datetime.now().isoformat(),
            applied=False
        )

        self.events.append(event)
        self._save_events()

        logger.info(f"学习反馈: {feedback_type} - {content}")

    def learn_pattern(self, pattern: str, context: Optional[Dict] = None):
        """
        学习模式

        Args:
            pattern: 模式描述
            context: 上下文
        """
        event = LearningEvent(
            id=self._generate_id(),
            event_type='pattern',
            content=pattern,
            context=context or {},
            learned_at=datetime.now().isoformat(),
            applied=False
        )

        self.events.append(event)
        self.patterns[pattern] = self.patterns.get(pattern, 0) + 1
        self._save_events()

        logger.info(f"学习模式: {pattern}")

    def extract_preferences_from_text(self, text: str) -> List[Dict]:
        """
        从文本中提取偏好

        Args:
            text: 输入文本

        Returns:
            提取的偏好列表
        """
        preferences = []

        # 偏好模式
        patterns = [
            (r'我喜欢(.+)', 'like'),
            (r'我不喜欢(.+)', 'dislike'),
            (r'我偏好(.+)', 'prefer'),
            (r'我习惯(.+)', 'habit'),
            (r'我通常(.+)', 'usually'),
            (r'我总是(.+)', 'always'),
            (r'我从不(.+)', 'never'),
        ]

        for pattern, pref_type in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                preferences.append({
                    'type': pref_type,
                    'value': match.strip(),
                    'source': text[:100]
                })

        return preferences

    def auto_learn_from_interaction(self,
                                     user_input: str,
                                     assistant_response: str,
                                     user_feedback: Optional[str] = None):
        """
        从交互中自动学习

        Args:
            user_input: 用户输入
            assistant_response: 助手回复
            user_feedback: 用户反馈
        """
        # 提取偏好
        prefs = self.extract_preferences_from_text(user_input)
        for pref in prefs:
            self.learn_preference(
                key=f"{pref['type']}_{len(self.preferences)}",
                value=pref['value'],
                context={'source': pref['source']}
            )

        # 学习反馈
        if user_feedback:
            feedback_type = 'neutral'
            if any(kw in user_feedback for kw in ['好', '对', '谢谢', '正确', 'good', 'thanks']):
                feedback_type = 'positive'
            elif any(kw in user_feedback for kw in ['不对', '错了', '不好', 'wrong', 'bad']):
                feedback_type = 'negative'

            self.learn_feedback(feedback_type, user_feedback)

        # 学习交互模式
        if len(user_input) > 10:
            # 简单模式：用户输入长度分布
            length_category = 'short' if len(user_input) < 50 else 'medium' if len(user_input) < 200 else 'long'
            self.learn_pattern(f"input_length:{length_category}")

    def get_preference(self, key: str, default: Any = None) -> Any:
        """获取偏好"""
        return self.preferences.get(key, default)

    def get_all_preferences(self) -> Dict[str, Any]:
        """获取所有偏好"""
        return self.preferences.copy()

    def get_patterns(self, min_count: int = 2) -> Dict[str, int]:
        """获取模式"""
        return {k: v for k, v in self.patterns.items() if v >= min_count}

    def get_recent_learnings(self, limit: int = 10) -> List[Dict]:
        """获取最近的学习"""
        recent = self.events[-limit:]
        return [
            {
                'id': e.id,
                'type': e.event_type,
                'content': e.content,
                'learned_at': e.learned_at
            }
            for e in reversed(recent)
        ]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_events': len(self.events),
            'preferences_count': len(self.preferences),
            'patterns_count': len(self.patterns),
            'by_type': {
                'preference': sum(1 for e in self.events if e.event_type == 'preference'),
                'correction': sum(1 for e in self.events if e.event_type == 'correction'),
                'feedback': sum(1 for e in self.events if e.event_type == 'feedback'),
                'pattern': sum(1 for e in self.events if e.event_type == 'pattern'),
            }
        }


# 便捷函数
_learner = None

def get_learner() -> AutoLearner:
    """获取学习器实例"""
    global _learner
    if _learner is None:
        _learner = AutoLearner()
    return _learner


def learn_preference(key: str, value: Any):
    """学习偏好"""
    get_learner().learn_preference(key, value)


def learn_correction(original: str, corrected: str):
    """学习纠正"""
    get_learner().learn_correction(original, corrected)


def get_preference(key: str, default: Any = None) -> Any:
    """获取偏好"""
    return get_learner().get_preference(key, default)
