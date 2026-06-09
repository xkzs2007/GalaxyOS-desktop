#!/usr/bin/env python3
"""emotion_tracker — 轻量情感追踪，paper_integration 依赖"""
import json, os, time, logging
from collections import defaultdict, deque

logger = logging.getLogger('emotion_tracker')

class EmotionTracker:
    """追踪对话情感状态，支持持久化"""

    EMOTION_MAP = {
        '正面': ['开心', '满意', '兴奋', '感动', '感激', '骄傲', '轻松', '好奇', '惊喜', '安心'],
        '中性': ['平静', '专注', '思考', '疑惑', '无奈', '随意', '期待'],
        '负面': ['生气', '沮丧', '失望', '焦虑', '烦躁', '疲惫', '难过', '厌烦', '担心', '压力'],
    }

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.expanduser(
            '~/.openclaw/workspace/.learnings/emotion_track.json')
        self.history = deque(maxlen=200)
        self.current_state = {
            'primary': '中性',
            'intensity': 0.5,
            'trend': 'stable',
            'last_update': time.time()
        }
        self._load()

    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path) as f:
                    data = json.load(f)
                if 'history' in data:
                    self.history = deque(data['history'][-200:], maxlen=200)
                if 'current_state' in data:
                    self.current_state.update(data['current_state'])
            except Exception as e:
                logger.warning(f'EmotionTracker load failed: {e}')

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            with open(self.db_path, 'w') as f:
                json.dump({
                    'history': list(self.history),
                    'current_state': self.current_state,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f'EmotionTracker save failed: {e}')

    def update(self, text: str, session: str = ''):
        """更新情感跟踪"""
        now = time.time()
        # 简化的情感分析
        primary, intensity = self._classify(text)

        # 更新趋势
        trend = 'stable'
        if self.history:
            recent = [e['primary'] for e in list(self.history)[-10:]]
            pos = recent.count('正面')
            neg = recent.count('负面')
            if pos > neg * 2:
                trend = 'improving'
            elif neg > pos * 2:
                trend = 'declining'

        self.current_state = {
            'primary': primary,
            'intensity': round(intensity, 2),
            'trend': trend,
            'last_update': now,
        }

        self.history.append({
            'ts': now,
            'primary': primary,
            'intensity': round(intensity, 2),
            'session': session[:20],
            'text_preview': text[:60],
        })

        self._save()

    def _classify(self, text: str):
        """简单基于关键词的情感分类"""
        if not text:
            return '中性', 0.5
        text_lower = text.lower()

        pos_score = 0
        neg_score = 0
        for word in self.EMOTION_MAP['正面']:
            if word in text: pos_score += 1
        for word in self.EMOTION_MAP['负面']:
            if word in text: neg_score += 1

        if pos_score > neg_score:
            return '正面', min(0.5 + 0.3 * (pos_score / max(neg_score, 1)), 1.0)
        elif neg_score > pos_score:
            return '负面', min(0.5 + 0.3 * (neg_score / max(pos_score, 1)), 1.0)
        else:
            return '中性', 0.5

    def get_state(self):
        """返回当前情感状态"""
        return dict(self.current_state)

    def get_trend(self, minutes: int = 60):
        """返回最近N分钟的情感趋势"""
        cutoff = time.time() - minutes * 60
        recent = [e for e in self.history if e['ts'] > cutoff]
        if not recent:
            return {'period_min': minutes, 'entries': 0, 'primary': '中性', 'trend': 'stable'}
        primary = max(set(e['primary'] for e in recent), key=lambda p: sum(1 for e in recent if e['primary'] == p))
        return {
            'period_min': minutes,
            'entries': len(recent),
            'primary': primary,
            'trend': self.current_state.get('trend', 'stable'),
        }

    def inject_to_context(self):
        """生成情感上下文注入文本（paper_integration 依赖）"""
        s = self.current_state
        lines = [
            f'[用户情感状态] 主要情绪: {s["primary"]} (强度: {s["intensity"]:.2f})',
            f'[情感趋势] 最近3天: {s["trend"]}',
        ]
        recent = list(self.history)[-20:]
        if recent:
            primaries = [e['primary'] for e in recent]
            most_common = max(set(primaries), key=primaries.count)
            lines.append(f'[近期主导情绪] {most_common}')
            if s['intensity'] < 0.3:
                lines.append('[波动模式] 情绪保持稳定，波动较小，情绪单一')
            else:
                lines.append(f'[波动模式] 情绪波动明显，{s["trend"]}')
        return '\n'.join(lines)

    def get_current_state(self):
        """返回当前情感状态（paper_integration 依赖）"""
        return dict(self.current_state)

    def get_trajectory(self, days: int = 7):
        """返回最近N天的情感轨迹（paper_integration 依赖）"""
        cutoff = time.time() - days * 86400
        recent = [e for e in self.history if e['ts'] > cutoff]
        trajectory = {}
        for e in recent:
            day = time.strftime('%Y-%m-%d', time.gmtime(e['ts']))
            if day not in trajectory:
                trajectory[day] = []
            trajectory[day].append({'primary': e['primary'], 'intensity': e['intensity']})
        summary = {}
        for day, entries in trajectory.items():
            primaries = [e['primary'] for e in entries]
            summary[day] = {
                'dominant': max(set(primaries), key=primaries.count),
                'count': len(entries),
                'avg_intensity': round(sum(e['intensity'] for e in entries) / len(entries), 2),
            }
        return {'days': days, 'trajectory': summary, 'total_entries': len(recent)}

    def emotion_weighted_search(self, query_results, current_emotion=None):
        """情感权重重排序（paper_integration 依赖）"""
        if not query_results:
            return query_results
        if current_emotion is None:
            current_emotion = self.current_state
        # 按 emotion_keyword 加权 + 原始得分
        primary = current_emotion.get('primary', '中性')
        scored = []
        for r in query_results:
            score = r.get('score', 0.5) if isinstance(r, dict) else 0.5
            content = r.get('content', '') if isinstance(r, dict) else str(r)
            weight = 1.0
            if primary == '正面' and any(w in content for w in self.EMOTION_MAP['正面']):
                weight = 1.2
            elif primary == '负面' and any(w in content for w in self.EMOTION_MAP['负面']):
                weight = 1.2
            scored.append((score * weight, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    def analyze(self, text: str, session: str = ''):
        """兼容 paper_integration 调用方式"""
        self.update(text, session)
        return self.current_state
