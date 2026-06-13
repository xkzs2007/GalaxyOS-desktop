#!/usr/bin/env python3
"""
任务-记忆桥接模块

实现 proactive-tasks 与 memory 系统的双向关联。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TaskMemoryLink:
    """任务-记忆关联"""
    task_id: str
    memory_id: str
    link_type: str  # 'created_from', 'related_to', 'completed_with'
    created_at: str


class TaskMemoryBridge:
    """
    任务-记忆桥接器
    
    功能:
    1. 任务创建时关联相关记忆
    2. 任务完成时记录到记忆
    3. 记忆检索时关联任务上下文
    """
    
    def __init__(self, links_path: Optional[str] = None):
        if links_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path.home() / '.openclaw' / 'workspace')
            links_path = str(Path(workspace) / 'memory' / 'task_memory_links.jsonl')
        
        self.links_path = links_path
        self.links: List[TaskMemoryLink] = []
        self._load_links()
    
    def _load_links(self):
        """加载关联"""
        if not Path(self.links_path).exists():
            return
        
        with open(self.links_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    link = TaskMemoryLink(
                        task_id=data.get('task_id', ''),
                        memory_id=data.get('memory_id', ''),
                        link_type=data.get('link_type', ''),
                        created_at=data.get('created_at', '')
                    )
                    self.links.append(link)
                except json.JSONDecodeError:
                    continue
        
        logger.info(f"加载任务-记忆关联: {len(self.links)} 条")
    
    def _save_links(self):
        """保存关联"""
        Path(self.links_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.links_path, 'w', encoding='utf-8') as f:
            for link in self.links:
                f.write(json.dumps({
                    'task_id': link.task_id,
                    'memory_id': link.memory_id,
                    'link_type': link.link_type,
                    'created_at': link.created_at
                }, ensure_ascii=False) + '\n')
    
    def link_task_to_memory(self,
                            task_id: str,
                            memory_id: str,
                            link_type: str = 'related_to'):
        """
        关联任务到记忆
        
        Args:
            task_id: 任务 ID
            memory_id: 记忆 ID
            link_type: 关联类型
        """
        link = TaskMemoryLink(
            task_id=task_id,
            memory_id=memory_id,
            link_type=link_type,
            created_at=datetime.now().isoformat()
        )
        self.links.append(link)
        self._save_links()
        
        logger.info(f"关联任务到记忆: {task_id} -> {memory_id}")
    
    def get_memories_for_task(self, task_id: str) -> List[str]:
        """获取任务关联的记忆"""
        return [l.memory_id for l in self.links if l.task_id == task_id]
    
    def get_tasks_for_memory(self, memory_id: str) -> List[str]:
        """获取记忆关联的任务"""
        return [l.task_id for l in self.links if l.memory_id == memory_id]
    
    def record_task_completion(self,
                                task_id: str,
                                task_title: str,
                                task_notes: str,
                                memory_add_func) -> str:
        """
        记录任务完成到记忆
        
        Args:
            task_id: 任务 ID
            task_title: 任务标题
            task_notes: 任务备注
            memory_add_func: 记忆添加函数
        
        Returns:
            记忆 ID
        """
        content = f"[任务完成] {task_title}\n\n{task_notes}"
        metadata = {
            'source': 'proactive-tasks',
            'task_id': task_id,
            'type': 'task_completion'
        }
        
        memory_id = memory_add_func(content, metadata)
        
        # 创建关联
        self.link_task_to_memory(task_id, memory_id, 'completed_with')
        
        return memory_id
    
    def get_task_context(self, 
                          task_title: str,
                          memory_search_func) -> List[Dict]:
        """
        获取任务相关的记忆上下文
        
        Args:
            task_title: 任务标题
            memory_search_func: 记忆搜索函数
        
        Returns:
            相关记忆列表
        """
        # 搜索相关记忆
        results = memory_search_func(task_title, top_k=5)
        
        return results


# 便捷函数
_bridge = None

def get_task_bridge() -> TaskMemoryBridge:
    """获取任务桥接器"""
    global _bridge
    if _bridge is None:
        _bridge = TaskMemoryBridge()
    return _bridge


def link_task_memory(task_id: str, memory_id: str, link_type: str = 'related_to'):
    """关联任务和记忆"""
    get_task_bridge().link_task_to_memory(task_id, memory_id, link_type)
