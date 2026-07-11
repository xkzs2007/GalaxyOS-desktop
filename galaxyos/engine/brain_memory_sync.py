#!/usr/bin/env python3
"""
个人知识库与记忆系统同步模块

实现 2nd-brain 与 memory-tencentdb 的双向同步。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)


@dataclass
class BrainEntry:
    """知识库条目"""
    id: str
    category: str  # people, places, games, tech, events, media, ideas, orgs
    name: str
    content: str
    frontmatter: Dict[str, Any]
    file_path: str
    updated_at: str


@dataclass
class SyncRecord:
    """同步记录"""
    brain_entry_id: str
    memory_id: str
    sync_time: str
    sync_type: str  # 'brain_to_memory', 'memory_to_brain', 'bidirectional'
    checksum: str


class BrainReader:
    """知识库读取器"""

    CATEGORIES = ['people', 'places', 'games', 'tech', 'events', 'media', 'ideas', 'orgs']

    def __init__(self, brain_path: Optional[str] = None):
        if brain_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path(workspace()))
            brain_path = str(Path(workspace) / 'brain')

        self.brain_path = brain_path
        self.entries: Dict[str, BrainEntry] = {}
        self._load_entries()

    def _load_entries(self):
        """加载知识库条目"""
        if not Path(self.brain_path).exists():
            logger.warning(f"知识库目录不存在: {self.brain_path}")
            return

        for category in self.CATEGORIES:
            category_path = Path(self.brain_path) / category
            if not category_path.exists():
                continue

            for file_path in category_path.glob('**/*.md'):
                try:
                    entry = self._parse_entry(file_path, category)
                    if entry:
                        self.entries[entry.id] = entry
                except Exception as e:
                    logger.error(f"解析知识库条目失败: {file_path}, {e}")

        logger.info(f"加载知识库: {len(self.entries)} 个条目")

    def _parse_entry(self, file_path: Path, category: str) -> Optional[BrainEntry]:
        """解析知识库条目"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 解析 frontmatter
        frontmatter = {}
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                fm_text = parts[1].strip()
                for line in fm_text.split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        frontmatter[key.strip()] = value.strip().strip('"\'')
                content = parts[2].strip()

        # 生成 ID
        entry_id = self._generate_id(category, file_path.stem)

        # 获取名称
        name = frontmatter.get('name', file_path.stem)

        # 获取更新时间
        stat = file_path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()

        return BrainEntry(
            id=entry_id,
            category=category,
            name=name,
            content=content,
            frontmatter=frontmatter,
            file_path=str(file_path),
            updated_at=updated_at
        )

    def _generate_id(self, category: str, name: str) -> str:
        """生成条目 ID"""
        return f"brain_{category}_{name}"

    def get_entry(self, entry_id: str) -> Optional[BrainEntry]:
        """获取条目"""
        return self.entries.get(entry_id)

    def search_entries(self, query: str, category: Optional[str] = None) -> List[BrainEntry]:
        """搜索条目"""
        results = []
        query_lower = query.lower()

        for entry in self.entries.values():
            if category and entry.category != category:
                continue

            # 匹配名称
            if query_lower in entry.name.lower():
                results.append(entry)
                continue

            # 匹配内容
            if query_lower in entry.content.lower():
                results.append(entry)
                continue

            # 匹配 frontmatter
            for key, value in entry.frontmatter.items():
                if isinstance(value, str) and query_lower in value.lower():
                    results.append(entry)
                    break

        return results

    def get_entries_by_category(self, category: str) -> List[BrainEntry]:
        """获取分类下的所有条目"""
        return [e for e in self.entries.values() if e.category == category]


class BrainMemorySync:
    """
    知识库与记忆系统同步器
    
    实现:
    1. 知识库条目同步到记忆系统
    2. 记忆提取结果同步到知识库
    3. 双向增量同步
    """

    def __init__(self,
                 brain_path: Optional[str] = None,
                 sync_records_path: Optional[str] = None):
        self.brain = BrainReader(brain_path)

        if sync_records_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path(workspace()))
            sync_records_path = str(Path(workspace) / 'memory' / 'brain_sync_records.jsonl')

        self.sync_records_path = sync_records_path
        self.sync_records: Dict[str, SyncRecord] = {}
        self._load_sync_records()

    def _load_sync_records(self):
        """加载同步记录"""
        if not Path(self.sync_records_path).exists():
            return

        with open(self.sync_records_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    record = SyncRecord(
                        brain_entry_id=data.get('brain_entry_id', ''),
                        memory_id=data.get('memory_id', ''),
                        sync_time=data.get('sync_time', ''),
                        sync_type=data.get('sync_type', ''),
                        checksum=data.get('checksum', '')
                    )
                    self.sync_records[record.brain_entry_id] = record
                except json.JSONDecodeError:
                    continue

        logger.info(f"加载同步记录: {len(self.sync_records)} 条")

    def _save_sync_records(self):
        """保存同步记录"""
        Path(self.sync_records_path).parent.mkdir(parents=True, exist_ok=True)

        with open(self.sync_records_path, 'w', encoding='utf-8') as f:
            for record in self.sync_records.values():
                f.write(json.dumps({
                    'brain_entry_id': record.brain_entry_id,
                    'memory_id': record.memory_id,
                    'sync_time': record.sync_time,
                    'sync_type': record.sync_type,
                    'checksum': record.checksum
                }, ensure_ascii=False) + '\n')

    def _compute_checksum(self, content: str) -> str:
        """计算内容校验和"""
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def sync_brain_to_memory(self,
                              entry_id: str,
                              memory_add_func) -> Optional[str]:
        """
        同步知识库条目到记忆系统
        
        Args:
            entry_id: 知识库条目 ID
            memory_add_func: 记忆添加函数 (content, metadata) -> memory_id
        
        Returns:
            记忆 ID 或 None
        """
        entry = self.brain.get_entry(entry_id)
        if not entry:
            logger.warning(f"知识库条目不存在: {entry_id}")
            return None

        # 检查是否需要同步
        current_checksum = self._compute_checksum(entry.content)
        existing_record = self.sync_records.get(entry_id)

        if existing_record and existing_record.checksum == current_checksum:
            logger.debug(f"知识库条目未变更，跳过同步: {entry_id}")
            return existing_record.memory_id

        # 构建记忆内容
        memory_content = f"[{entry.category}] {entry.name}\n\n{entry.content}"

        # 构建元数据
        metadata = {
            'source': 'brain',
            'category': entry.category,
            'brain_entry_id': entry_id,
            'brain_file_path': entry.file_path,
            'name': entry.name,
            **entry.frontmatter
        }

        # 添加到记忆系统
        memory_id = memory_add_func(memory_content, metadata)

        # 记录同步
        record = SyncRecord(
            brain_entry_id=entry_id,
            memory_id=memory_id,
            sync_time=datetime.now().isoformat(),
            sync_type='brain_to_memory',
            checksum=current_checksum
        )
        self.sync_records[entry_id] = record
        self._save_sync_records()

        logger.info(f"同步知识库条目到记忆: {entry_id} -> {memory_id}")
        return memory_id

    def sync_memory_to_brain(self,
                              memory_id: str,
                              memory_content: str,
                              memory_metadata: Dict[str, Any]) -> Optional[str]:
        """
        同步记忆到知识库
        
        Args:
            memory_id: 记忆 ID
            memory_content: 记忆内容
            memory_metadata: 记忆元数据
        
        Returns:
            知识库条目 ID 或 None
        """
        # 检查是否来自知识库（避免循环同步）
        if memory_metadata.get('source') == 'brain':
            return None

        # 提取实体信息
        entity_name = memory_metadata.get('entity_name') or memory_metadata.get('name')
        entity_type = memory_metadata.get('entity_type') or memory_metadata.get('category')

        if not entity_name:
            # 尝试从内容中提取
            lines = memory_content.strip().split('\n')
            if lines:
                first_line = lines[0]
                # 移除可能的标记
                entity_name = re.sub(r'^[\[\]【】\s]+|[\[\]【】\s]+$', '', first_line)

        if not entity_name:
            return None

        # 确定分类
        category = self._infer_category(entity_type, memory_content)

        # 生成条目 ID
        entry_id = f"brain_{category}_{entity_name.lower().replace(' ', '-')}"

        # 检查是否已存在
        existing_entry = self.brain.get_entry(entry_id)

        if existing_entry:
            # 更新现有条目
            self._update_brain_entry(existing_entry, memory_content, memory_metadata)
        else:
            # 创建新条目
            self._create_brain_entry(entry_id, category, entity_name,
                                     memory_content, memory_metadata)

        # 记录同步
        record = SyncRecord(
            brain_entry_id=entry_id,
            memory_id=memory_id,
            sync_time=datetime.now().isoformat(),
            sync_type='memory_to_brain',
            checksum=self._compute_checksum(memory_content)
        )
        self.sync_records[entry_id] = record
        self._save_sync_records()

        logger.info(f"同步记忆到知识库: {memory_id} -> {entry_id}")
        return entry_id

    def _infer_category(self, entity_type: Optional[str], content: str) -> str:
        """推断分类"""
        if entity_type:
            type_map = {
                'Person': 'people',
                'people': 'people',
                'Place': 'places',
                'place': 'places',
                'Restaurant': 'places',
                'Game': 'games',
                'game': 'games',
                'Device': 'tech',
                'tech': 'tech',
                'Event': 'events',
                'event': 'events',
                'Book': 'media',
                'Movie': 'media',
                'Show': 'media',
                'media': 'media',
                'Idea': 'ideas',
                'idea': 'ideas',
                'Organization': 'orgs',
                'Company': 'orgs',
                'org': 'orgs',
            }
            return type_map.get(entity_type, 'ideas')

        # 基于内容推断
        content_lower = content.lower()

        if any(kw in content_lower for kw in ['餐厅', '餐厅', '地点', '地址', 'restaurant', 'place']):
            return 'places'
        elif any(kw in content_lower for kw in ['游戏', 'game', 'played', '玩']):
            return 'games'
        elif any(kw in content_lower for kw in ['会议', '活动', 'event', 'meeting']):
            return 'events'
        elif any(kw in content_lower for kw in ['公司', '组织', 'company', 'org']):
            return 'orgs'
        elif any(kw in content_lower for kw in ['书', '电影', '剧', 'book', 'movie', 'show']):
            return 'media'
        elif any(kw in content_lower for kw in ['设备', '手机', '电脑', 'device', 'phone', 'tech']):
            return 'tech'
        else:
            return 'people'  # 默认为人

    def _create_brain_entry(self,
                            entry_id: str,
                            category: str,
                            name: str,
                            content: str,
                            metadata: Dict[str, Any]):
        """创建知识库条目"""
        workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                   Path(workspace()))
        brain_path = Path(workspace) / 'brain' / category
        brain_path.mkdir(parents=True, exist_ok=True)

        # 生成文件名
        file_name = name.lower().replace(' ', '-').replace('/', '-')
        file_path = brain_path / f"{file_name}.md"

        # 构建内容
        frontmatter = {
            'name': name,
            'created': datetime.now().strftime('%Y-%m-%d'),
            'last_updated': datetime.now().strftime('%Y-%m-%d'),
            'source': 'memory-sync',
            'memory_id': metadata.get('memory_id', '')
        }

        fm_str = '---\n'
        for key, value in frontmatter.items():
            fm_str += f"{key}: {value}\n"
        fm_str += '---\n\n'

        full_content = fm_str + content

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(full_content)

        # 添加到内存索引
        entry = BrainEntry(
            id=entry_id,
            category=category,
            name=name,
            content=content,
            frontmatter=frontmatter,
            file_path=str(file_path),
            updated_at=datetime.now().isoformat()
        )
        self.brain.entries[entry_id] = entry

    def _update_brain_entry(self,
                            entry: BrainEntry,
                            new_content: str,
                            metadata: Dict[str, Any]):
        """更新知识库条目"""
        # 追加新内容
        updated_content = entry.content + f"\n\n---\n\n**更新 ({datetime.now().strftime('%Y-%m-%d')}):**\n\n{new_content}"

        # 更新 frontmatter
        entry.frontmatter['last_updated'] = datetime.now().strftime('%Y-%m-%d')

        # 写入文件
        fm_str = '---\n'
        for key, value in entry.frontmatter.items():
            fm_str += f"{key}: {value}\n"
        fm_str += '---\n\n'

        full_content = fm_str + updated_content

        with open(entry.file_path, 'w', encoding='utf-8') as f:
            f.write(full_content)

        # 更新内存索引
        entry.content = updated_content
        entry.updated_at = datetime.now().isoformat()

    def sync_all_brain_to_memory(self, memory_add_func) -> Dict[str, str]:
        """
        同步所有知识库条目到记忆系统
        
        Returns:
            Dict[entry_id, memory_id]
        """
        results = {}

        for entry_id in self.brain.entries:
            memory_id = self.sync_brain_to_memory(entry_id, memory_add_func)
            if memory_id:
                results[entry_id] = memory_id

        logger.info(f"同步知识库到记忆完成: {len(results)} 条")
        return results

    def get_sync_status(self) -> Dict[str, Any]:
        """获取同步状态"""
        return {
            'brain_entries': len(self.brain.entries),
            'sync_records': len(self.sync_records),
            'categories': {
                cat: len(self.brain.get_entries_by_category(cat))
                for cat in BrainReader.CATEGORIES
            }
        }


# 便捷函数
_sync = None

def get_sync() -> BrainMemorySync:
    """获取默认同步器实例"""
    global _sync
    if _sync is None:
        _sync = BrainMemorySync()
    return _sync


def sync_entry_to_memory(entry_id: str, memory_add_func) -> Optional[str]:
    """同步知识库条目到记忆"""
    return get_sync().sync_brain_to_memory(entry_id, memory_add_func)


def sync_memory_to_entry(memory_id: str, content: str, metadata: Dict) -> Optional[str]:
    """同步记忆到知识库"""
    return get_sync().sync_memory_to_brain(memory_id, content, metadata)
