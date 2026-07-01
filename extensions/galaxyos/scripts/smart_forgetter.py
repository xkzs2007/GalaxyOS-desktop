#!/usr/bin/env python3
"""
智能遗忘管理器

基于重要性评分自动清理低价值记忆。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import shutil

logger = logging.getLogger(__name__)


@dataclass
class ForgetAction:
    """遗忘动作"""
    memory_id: str
    action: str  # 'archive', 'delete', 'keep'
    reason: str
    score: float
    timestamp: str


class SmartForgetter:
    """
    智能遗忘管理器
    
    功能:
    1. 基于重要性评分决定遗忘策略
    2. 归档低价值记忆
    3. 删除过期记忆
    4. 合并重复记忆
    """
    
    # 遗忘策略配置
    DEFAULT_CONFIG = {
        'archive_threshold': 0.3,  # 低于此分数归档
        'delete_threshold': 0.15,  # 低于此分数删除
        'max_age_days': 180,  # 最大保留天数
        'archive_path': 'archive',
        'dry_run': False,  # 试运行模式
    }
    
    def __init__(self, 
                 config: Optional[Dict] = None,
                 storage_path: Optional[str] = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        
        if storage_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path.home() / '.openclaw' / 'workspace')
            storage_path = str(Path(workspace) / 'memory')
        
        self.storage_path = Path(storage_path)
        self.archive_path = self.storage_path / self.config['archive_path']
        self.archive_path.mkdir(parents=True, exist_ok=True)
        
        self.history_path = self.storage_path / 'forget_history.json'
        self.history: List[ForgetAction] = []
        self._load_history()
        
        # 导入评分器
        try:
            from .importance_scorer import ImportanceScorer
            self.scorer = ImportanceScorer()
        except ImportError:
            from importance_scorer import ImportanceScorer
            self.scorer = ImportanceScorer()
    
    def _load_history(self):
        """加载历史记录"""
        if not self.history_path.exists():
            return
        
        with open(self.history_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for item in data.get('actions', []):
            action = ForgetAction(
                memory_id=item.get('memory_id', ''),
                action=item.get('action', ''),
                reason=item.get('reason', ''),
                score=item.get('score', 0.0),
                timestamp=item.get('timestamp', '')
            )
            self.history.append(action)
        
        logger.info(f"加载遗忘历史: {len(self.history)} 条")
    
    def _save_history(self):
        """保存历史记录"""
        with open(self.history_path, 'w', encoding='utf-8') as f:
            json.dump({
                'actions': [
                    {
                        'memory_id': a.memory_id,
                        'action': a.action,
                        'reason': a.reason,
                        'score': a.score,
                        'timestamp': a.timestamp
                    }
                    for a in self.history
                ],
                'updated_at': datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def analyze(self, memories: List[Dict]) -> Dict[str, List[ForgetAction]]:
        """
        分析记忆，生成遗忘建议
        
        Args:
            memories: 记忆列表
        
        Returns:
            分类后的遗忘动作
        """
        actions = {
            'keep': [],
            'archive': [],
            'delete': []
        }
        
        for mem in memories:
            memory_id = mem.get('id', '')
            metadata = mem.get('metadata', {})
            created_at = metadata.get('created_at') or mem.get('created_at')
            entity_count = len(metadata.get('entities', []))
            content_length = len(mem.get('content', ''))
            
            # 计算重要性分数
            score = self.scorer.score(
                memory_id, metadata, created_at, entity_count, content_length
            )
            
            # 检查年龄
            age_days = 0
            if created_at:
                try:
                    created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    age_days = (datetime.now(created.tzinfo) - created).days
                except:
                    pass
            
            # 决定动作
            if score.total_score < self.config['delete_threshold']:
                action_type = 'delete'
                reason = f"重要性过低 ({score.total_score:.2f})"
            elif score.total_score < self.config['archive_threshold']:
                action_type = 'archive'
                reason = f"重要性较低 ({score.total_score:.2f})"
            elif age_days > self.config['max_age_days']:
                action_type = 'archive'
                reason = f"超过保留期限 ({age_days} 天)"
            else:
                action_type = 'keep'
                reason = f"重要性正常 ({score.total_score:.2f})"
            
            action = ForgetAction(
                memory_id=memory_id,
                action=action_type,
                reason=reason,
                score=score.total_score,
                timestamp=datetime.now().isoformat()
            )
            
            actions[action_type].append(action)
        
        return actions
    
    def execute(self, 
                actions: List[ForgetAction],
                delete_func: Optional[Callable] = None,
                archive_func: Optional[Callable] = None) -> Dict[str, int]:
        """
        执行遗忘动作
        
        Args:
            actions: 遗忘动作列表
            delete_func: 删除函数
            archive_func: 归档函数
        
        Returns:
            执行结果统计
        """
        stats = {
            'archived': 0,
            'deleted': 0,
            'kept': 0,
            'failed': 0
        }
        
        for action in actions:
            try:
                if action.action == 'delete':
                    if delete_func:
                        delete_func(action.memory_id)
                    stats['deleted'] += 1
                    
                elif action.action == 'archive':
                    if archive_func:
                        archive_func(action.memory_id)
                    stats['archived'] += 1
                    
                else:
                    stats['kept'] += 1
                
                # 记录历史
                self.history.append(action)
                
            except Exception as e:
                logger.error(f"执行遗忘动作失败: {action.memory_id}, {e}")
                stats['failed'] += 1
        
        self._save_history()
        return stats
    
    def run_cleanup(self,
                    memories: List[Dict],
                    delete_func: Optional[Callable] = None,
                    archive_func: Optional[Callable] = None,
                    dry_run: bool = False) -> Dict[str, Any]:
        """
        运行清理
        
        Args:
            memories: 记忆列表
            delete_func: 删除函数
            archive_func: 归档函数
            dry_run: 试运行模式
        
        Returns:
            清理结果
        """
        # 分析
        actions = self.analyze(memories)
        
        result = {
            'dry_run': dry_run or self.config['dry_run'],
            'analysis': {
                'keep': len(actions['keep']),
                'archive': len(actions['archive']),
                'delete': len(actions['delete'])
            },
            'executed': None
        }
        
        # 执行
        if not result['dry_run']:
            all_actions = actions['archive'] + actions['delete']
            result['executed'] = self.execute(all_actions, delete_func, archive_func)
        
        return result
    
    def archive_memory(self, memory_data: Dict) -> str:
        """
        归档记忆
        
        Args:
            memory_data: 记忆数据
        
        Returns:
            归档文件路径
        """
        memory_id = memory_data.get('id', '')
        archive_file = self.archive_path / f"{memory_id}.json"
        
        with open(archive_file, 'w', encoding='utf-8') as f:
            json.dump({
                **memory_data,
                'archived_at': datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f"归档记忆: {memory_id}")
        return str(archive_file)
    
    def restore_memory(self, memory_id: str) -> Optional[Dict]:
        """
        恢复归档记忆
        
        Args:
            memory_id: 记忆 ID
        
        Returns:
            记忆数据
        """
        archive_file = self.archive_path / f"{memory_id}.json"
        
        if not archive_file.exists():
            return None
        
        with open(archive_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 移除归档标记
        data.pop('archived_at', None)
        
        # 删除归档文件
        archive_file.unlink()
        
        logger.info(f"恢复记忆: {memory_id}")
        return data
    
    def get_archive_list(self) -> List[Dict]:
        """获取归档列表"""
        archives = []
        
        for file in self.archive_path.glob('*.json'):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                archives.append({
                    'id': data.get('id', ''),
                    'content_preview': data.get('content', '')[:100],
                    'archived_at': data.get('archived_at', ''),
                    'file': str(file)
                })
            except:
                continue
        
        return archives
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_actions': len(self.history),
            'archive_count': len(list(self.archive_path.glob('*.json'))),
            'recent_actions': [
                {
                    'memory_id': a.memory_id,
                    'action': a.action,
                    'reason': a.reason,
                    'timestamp': a.timestamp
                }
                for a in self.history[-10:]
            ]
        }


# 便捷函数
_forgetter = None

def get_forgetter() -> SmartForgetter:
    """获取遗忘管理器实例"""
    global _forgetter
    if _forgetter is None:
        _forgetter = SmartForgetter()
    return _forgetter


def analyze_memories(memories: List[Dict]) -> Dict[str, List]:
    """分析记忆"""
    return get_forgetter().analyze(memories)


def run_cleanup(memories: List[Dict], **kwargs) -> Dict[str, Any]:
    """运行清理"""
    return get_forgetter().run_cleanup(memories, **kwargs)
