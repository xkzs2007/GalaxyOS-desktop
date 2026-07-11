#!/usr/bin/env python3
"""
多模态记忆模块

实现图像理解结果的持久化存储和检索。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import base64
import hashlib
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)


@dataclass
class ImageMemory:
    """图像记忆"""
    id: str
    image_path: str
    description: str
    tags: List[str]
    entities: List[str]  # 关联实体
    embedding: Optional[List[float]]
    metadata: Dict[str, Any]
    created_at: str


class MultimodalMemoryStore:
    """
    多模态记忆存储
    
    功能:
    1. 图像理解结果存储
    2. 图像嵌入存储
    3. 图像记忆检索
    4. 图像与实体关联
    """
    
    def __init__(self, storage_path: Optional[str] = None):
        if storage_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path(workspace()))
            storage_path = str(Path(workspace) / 'memory' / 'multimodal')
        
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        self.index_path = self.storage_path / 'image_index.json'
        self.images_path = self.storage_path / 'images'
        self.images_path.mkdir(exist_ok=True)
        
        self.index: Dict[str, ImageMemory] = {}
        self._load_index()
    
    def _load_index(self):
        """加载索引"""
        if not self.index_path.exists():
            return
        
        with open(self.index_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for item in data.get('images', []):
            memory = ImageMemory(
                id=item.get('id', ''),
                image_path=item.get('image_path', ''),
                description=item.get('description', ''),
                tags=item.get('tags', []),
                entities=item.get('entities', []),
                embedding=item.get('embedding'),
                metadata=item.get('metadata', {}),
                created_at=item.get('created_at', '')
            )
            self.index[memory.id] = memory
        
        logger.info(f"加载图像记忆索引: {len(self.index)} 条")
    
    def _save_index(self):
        """保存索引"""
        data = {
            'version': '1.0',
            'updated_at': datetime.now().isoformat(),
            'images': [
                {
                    'id': m.id,
                    'image_path': m.image_path,
                    'description': m.description,
                    'tags': m.tags,
                    'entities': m.entities,
                    'embedding': m.embedding,
                    'metadata': m.metadata,
                    'created_at': m.created_at
                }
                for m in self.index.values()
            ]
        }
        
        with open(self.index_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _generate_id(self, image_data: bytes) -> str:
        """生成图像 ID"""
        hash_val = hashlib.md5(image_data).hexdigest()[:12]
        return f"img_{hash_val}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    def store_image(self,
                    image_data: bytes,
                    description: str,
                    tags: Optional[List[str]] = None,
                    entities: Optional[List[str]] = None,
                    embedding: Optional[List[float]] = None,
                    metadata: Optional[Dict] = None) -> str:
        """
        存储图像记忆
        
        Args:
            image_data: 图像二进制数据
            description: 图像描述
            tags: 标签列表
            entities: 关联实体列表
            embedding: 图像嵌入向量
            metadata: 元数据
        
        Returns:
            图像记忆 ID
        """
        # 生成 ID
        image_id = self._generate_id(image_data)
        
        # 保存图像文件
        filename = f"{image_id}.jpg"
        image_path = self.images_path / filename
        
        with open(image_path, 'wb') as f:
            f.write(image_data)
        
        # 创建记忆记录
        memory = ImageMemory(
            id=image_id,
            image_path=str(image_path),
            description=description,
            tags=tags or [],
            entities=entities or [],
            embedding=embedding,
            metadata=metadata or {},
            created_at=datetime.now().isoformat()
        )
        
        self.index[image_id] = memory
        self._save_index()
        
        logger.info(f"存储图像记忆: {image_id}")
        return image_id
    
    def store_from_url(self,
                       image_url: str,
                       description: str,
                       tags: Optional[List[str]] = None,
                       entities: Optional[List[str]] = None,
                       embedding: Optional[List[float]] = None,
                       metadata: Optional[Dict] = None) -> str:
        """
        从 URL 存储图像记忆
        
        Args:
            image_url: 图像 URL
            description: 图像描述
            tags: 标签列表
            entities: 关联实体列表
            embedding: 图像嵌入向量
            metadata: 元数据
        
        Returns:
            图像记忆 ID
        """
        import urllib.request
        
        try:
            with urllib.request.urlopen(image_url, timeout=30) as response:
                image_data = response.read()
        except Exception as e:
            logger.error(f"下载图像失败: {image_url}, {e}")
            # 创建无图像的记忆
            image_id = f"img_url_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            memory = ImageMemory(
                id=image_id,
                image_path=image_url,
                description=description,
                tags=tags or [],
                entities=entities or [],
                embedding=embedding,
                metadata=metadata or {'url': image_url, 'download_failed': True},
                created_at=datetime.now().isoformat()
            )
            self.index[image_id] = memory
            self._save_index()
            return image_id
        
        return self.store_image(image_data, description, tags, entities, embedding, metadata)
    
    def store_from_understanding(self,
                                 understanding_result: Dict,
                                 source_path: Optional[str] = None) -> str:
        """
        从图像理解结果存储记忆
        
        Args:
            understanding_result: xiaoyi-image-understanding 返回结果
            source_path: 原始图像路径
        
        Returns:
            图像记忆 ID
        """
        description = understanding_result.get('description', '')
        tags = understanding_result.get('tags', [])
        entities = understanding_result.get('entities', [])
        embedding = understanding_result.get('embedding')
        
        metadata = {
            'source': 'xiaoyi-image-understanding',
            'source_path': source_path,
            'raw_result': understanding_result
        }
        
        if source_path and Path(source_path).exists():
            with open(source_path, 'rb') as f:
                image_data = f.read()
            return self.store_image(image_data, description, tags, entities, embedding, metadata)
        else:
            # 无图像数据，仅存储描述
            image_id = f"img_desc_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            memory = ImageMemory(
                id=image_id,
                image_path=source_path or '',
                description=description,
                tags=tags,
                entities=entities,
                embedding=embedding,
                metadata=metadata,
                created_at=datetime.now().isoformat()
            )
            self.index[image_id] = memory
            self._save_index()
            return image_id
    
    def search(self, 
               query: str,
               top_k: int = 10,
               tag_filter: Optional[str] = None) -> List[Dict]:
        """
        搜索图像记忆
        
        Args:
            query: 查询文本
            top_k: 返回数量
            tag_filter: 标签过滤
        
        Returns:
            搜索结果列表
        """
        results = []
        query_lower = query.lower()
        
        for memory in self.index.values():
            # 标签过滤
            if tag_filter and tag_filter not in memory.tags:
                continue
            
            # 计算匹配分数
            score = 0.0
            
            # 描述匹配
            if query_lower in memory.description.lower():
                score += 0.5
            
            # 标签匹配
            for tag in memory.tags:
                if query_lower in tag.lower():
                    score += 0.3
            
            # 实体匹配
            for entity in memory.entities:
                if query_lower in entity.lower():
                    score += 0.2
            
            if score > 0:
                results.append({
                    'id': memory.id,
                    'image_path': memory.image_path,
                    'description': memory.description,
                    'tags': memory.tags,
                    'entities': memory.entities,
                    'score': score,
                    'created_at': memory.created_at
                })
        
        # 排序
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]
    
    def search_by_embedding(self,
                            query_embedding: List[float],
                            top_k: int = 10) -> List[Dict]:
        """
        通过嵌入向量搜索
        
        Args:
            query_embedding: 查询嵌入
            top_k: 返回数量
        
        Returns:
            搜索结果列表
        """
        import numpy as np
        
        results = []
        query_vec = np.array(query_embedding)
        query_norm = np.linalg.norm(query_vec)
        
        for memory in self.index.values():
            if not memory.embedding:
                continue
            
            mem_vec = np.array(memory.embedding)
            mem_norm = np.linalg.norm(mem_vec)
            
            if query_norm > 0 and mem_norm > 0:
                similarity = np.dot(query_vec, mem_vec) / (query_norm * mem_norm)
            else:
                similarity = 0.0
            
            if similarity > 0.1:  # 阈值
                results.append({
                    'id': memory.id,
                    'image_path': memory.image_path,
                    'description': memory.description,
                    'tags': memory.tags,
                    'entities': memory.entities,
                    'score': float(similarity),
                    'created_at': memory.created_at
                })
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]
    
    def get(self, image_id: str) -> Optional[Dict]:
        """获取图像记忆"""
        memory = self.index.get(image_id)
        if not memory:
            return None
        
        return {
            'id': memory.id,
            'image_path': memory.image_path,
            'description': memory.description,
            'tags': memory.tags,
            'entities': memory.entities,
            'metadata': memory.metadata,
            'created_at': memory.created_at
        }
    
    def delete(self, image_id: str) -> bool:
        """删除图像记忆"""
        memory = self.index.get(image_id)
        if not memory:
            return False
        
        # 删除图像文件
        if memory.image_path and Path(memory.image_path).exists():
            Path(memory.image_path).unlink()
        
        # 从索引删除
        del self.index[image_id]
        self._save_index()
        
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_tags = set()
        total_entities = set()
        
        for memory in self.index.values():
            total_tags.update(memory.tags)
            total_entities.update(memory.entities)
        
        return {
            'total_images': len(self.index),
            'total_tags': len(total_tags),
            'total_entities': len(total_entities),
            'with_embedding': sum(1 for m in self.index.values() if m.embedding)
        }


# 便捷函数
_store = None

def get_multimodal_store() -> MultimodalMemoryStore:
    """获取多模态存储实例"""
    global _store
    if _store is None:
        _store = MultimodalMemoryStore()
    return _store


def store_image_memory(description: str, 
                       image_data: Optional[bytes] = None,
                       tags: Optional[List[str]] = None,
                       entities: Optional[List[str]] = None) -> str:
    """存储图像记忆"""
    if image_data:
        return get_multimodal_store().store_image(image_data, description, tags, entities)
    else:
        # 仅存储描述
        store = get_multimodal_store()
        image_id = f"img_desc_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        memory = ImageMemory(
            id=image_id,
            image_path='',
            description=description,
            tags=tags or [],
            entities=entities or [],
            embedding=None,
            metadata={},
            created_at=datetime.now().isoformat()
        )
        store.index[image_id] = memory
        store._save_index()
        return image_id


def search_images(query: str, top_k: int = 10) -> List[Dict]:
    """搜索图像"""
    return get_multimodal_store().search(query, top_k)
