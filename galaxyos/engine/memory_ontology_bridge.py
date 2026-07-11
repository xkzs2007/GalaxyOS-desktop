#!/usr/bin/env python3
"""
记忆与知识图谱桥接模块

实现 memory-tencentdb 与 ontology 技能的双向关联。
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
import re
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    """知识图谱实体"""
    id: str
    type: str  # Person, Project, Task, Event, Document, etc.
    name: str
    properties: Dict[str, Any]
    relations: List[Dict[str, Any]]


@dataclass  
class MemoryEntityLink:
    """记忆与实体的关联"""
    memory_id: str
    entity_id: str
    entity_type: str
    relation: str  # 'mentions', 'about', 'involves', etc.
    confidence: float


class OntologyReader:
    """知识图谱读取器"""
    
    def __init__(self, graph_path: Optional[str] = None):
        if graph_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE', 
                                       Path(workspace()))
            graph_path = str(Path(workspace) / 'memory' / 'ontology' / 'graph.jsonl')
        
        self.graph_path = graph_path
        self.entities: Dict[str, Entity] = {}
        self._load_graph()
    
    def _load_graph(self):
        """加载知识图谱"""
        if not Path(self.graph_path).exists():
            logger.warning(f"知识图谱文件不存在: {self.graph_path}")
            return
        
        with open(self.graph_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # 处理 op 格式: {"op":"create","entity":{...}}
                    if 'op' in data:
                        op = data.get('op')
                        if op == 'create' and 'entity' in data:
                            entity_data = data['entity']
                            entity = Entity(
                                id=entity_data.get('id', ''),
                                type=entity_data.get('type', 'Unknown'),
                                name=entity_data.get('properties', {}).get('name', ''),
                                properties=entity_data.get('properties', {}),
                                relations=[]
                            )
                            self.entities[entity.id] = entity
                        elif op == 'relate' and 'from' in data:
                            # 记录关系到实体的 relations 列表
                            from_id = data.get('from')
                            rel = data.get('rel')
                            to_id = data.get('to')
                            if from_id in self.entities:
                                self.entities[from_id].relations.append({
                                    'relation': rel,
                                    'target': to_id
                                })
                    # 兼容旧格式: 直接是 entity
                    elif 'id' in data:
                        entity = Entity(
                            id=data.get('id', ''),
                            type=data.get('type', 'Unknown'),
                            name=data.get('properties', {}).get('name', ''),
                            properties=data.get('properties', {}),
                            relations=data.get('relations', [])
                        )
                        self.entities[entity.id] = entity
                except json.JSONDecodeError:
                    continue
        
        logger.info(f"加载知识图谱: {len(self.entities)} 个实体")
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """获取实体"""
        return self.entities.get(entity_id)
    
    def search_entities(self, query: str, entity_type: Optional[str] = None) -> List[Entity]:
        """搜索实体（简单关键词匹配）"""
        results = []
        query_lower = query.lower()
        
        for entity in self.entities.values():
            if entity_type and entity.type != entity_type:
                continue
            
            # 匹配名称
            if query_lower in entity.name.lower():
                results.append(entity)
                continue
            
            # 匹配属性
            for key, value in entity.properties.items():
                if isinstance(value, str) and query_lower in value.lower():
                    results.append(entity)
                    break
        
        return results
    
    def get_related_entities(self, entity_id: str, relation_type: Optional[str] = None) -> List[Tuple[Entity, str]]:
        """获取关联实体"""
        entity = self.entities.get(entity_id)
        if not entity:
            return []
        
        related = []
        for rel in entity.relations:
            if relation_type and rel.get('type') != relation_type:
                continue
            
            target_id = rel.get('target')
            target_entity = self.entities.get(target_id)
            if target_entity:
                related.append((target_entity, rel.get('type', 'related')))
        
        return related


class EntityExtractor:
    """实体提取器"""
    
    # 实体类型关键词
    ENTITY_PATTERNS = {
        'Person': [
            r'(?:用户|我|他|她|他们|她们)',
            r'(?:先生|女士|老师|教授|医生|工程师)',
            r'(?:张|王|李|赵|刘|陈|杨|黄|周|吴)[\u4e00-\u9fa5]{1,2}',
        ],
        'Project': [
            r'(?:项目|工程|计划|任务)',
            r'(?:开发|研究|设计|实现)',
        ],
        'Task': [
            r'(?:任务|待办|TODO|todo)',
            r'(?:需要|要|应该|必须)',
        ],
        'Event': [
            r'(?:会议|活动|聚会|约会)',
            r'(?:时间|日期|地点)',
        ],
        'Document': [
            r'(?:文档|文件|报告|论文)',
            r'(?:\.pdf|\.doc|\.txt|\.md)',
        ],
        'Organization': [
            r'(?:公司|团队|组织|部门)',
            r'(?:华为|腾讯|阿里|百度|字节)',
        ],
    }
    
    def __init__(self):
        self.patterns = {}
        for entity_type, patterns in self.ENTITY_PATTERNS.items():
            self.patterns[entity_type] = [re.compile(p) for p in patterns]
    
    def extract(self, text: str) -> List[Tuple[str, str, float]]:
        """
        从文本中提取实体
        
        Returns:
            List of (entity_name, entity_type, confidence)
        """
        results = []
        
        for entity_type, patterns in self.patterns.items():
            for pattern in patterns:
                matches = pattern.findall(text)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0] if match else ''
                    
                    if match and len(match) > 1:
                        results.append((match, entity_type, 0.7))
        
        # 去重
        seen = set()
        unique_results = []
        for name, etype, conf in results:
            key = (name, etype)
            if key not in seen:
                seen.add(key)
                unique_results.append((name, etype, conf))
        
        return unique_results


class MemoryOntologyBridge:
    """
    记忆与知识图谱桥接器
    
    实现:
    1. 从记忆中提取实体并关联到知识图谱
    2. 检索记忆时注入相关知识图谱信息
    """
    
    def __init__(self, 
                 graph_path: Optional[str] = None,
                 links_path: Optional[str] = None):
        self.ontology = OntologyReader(graph_path)
        self.extractor = EntityExtractor()
        
        if links_path is None:
            workspace = os.environ.get('OPENCLAW_WORKSPACE',
                                       Path(workspace()))
            links_path = str(Path(workspace) / 'memory' / 'ontology' / 'memory_links.jsonl')
        
        self.links_path = links_path
        self.links: List[MemoryEntityLink] = []
        self._load_links()
    
    def _load_links(self):
        """加载记忆-实体关联"""
        if not Path(self.links_path).exists():
            return
        
        with open(self.links_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    link = MemoryEntityLink(
                        memory_id=data.get('memory_id', ''),
                        entity_id=data.get('entity_id', ''),
                        entity_type=data.get('entity_type', ''),
                        relation=data.get('relation', 'mentions'),
                        confidence=data.get('confidence', 0.5)
                    )
                    self.links.append(link)
                except json.JSONDecodeError:
                    continue
        
        logger.info(f"加载记忆-实体关联: {len(self.links)} 条")
    
    def _save_links(self):
        """保存记忆-实体关联"""
        Path(self.links_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.links_path, 'w', encoding='utf-8') as f:
            for link in self.links:
                f.write(json.dumps({
                    'memory_id': link.memory_id,
                    'entity_id': link.entity_id,
                    'entity_type': link.entity_type,
                    'relation': link.relation,
                    'confidence': link.confidence
                }, ensure_ascii=False) + '\n')
    
    def link_memory_to_entities(self, 
                                 memory_id: str,
                                 memory_content: str,
                                 auto_create: bool = True) -> List[MemoryEntityLink]:
        """
        将记忆关联到实体
        
        Args:
            memory_id: 记忆 ID
            memory_content: 记忆内容
            auto_create: 是否自动创建新实体
        
        Returns:
            创建的关联列表
        """
        # 提取实体
        extracted = self.extractor.extract(memory_content)
        
        links = []
        for entity_name, entity_type, confidence in extracted:
            # 查找现有实体
            existing = self.ontology.search_entities(entity_name, entity_type)
            
            if existing:
                entity = existing[0]
            elif auto_create:
                # 创建新实体
                entity_id = f"{entity_type.lower()}_{len(self.ontology.entities) + 1}"
                entity = Entity(
                    id=entity_id,
                    type=entity_type,
                    name=entity_name,
                    properties={'name': entity_name, 'source': 'memory'},
                    relations=[]
                )
                self.ontology.entities[entity_id] = entity
            else:
                continue
            
            # 创建关联
            link = MemoryEntityLink(
                memory_id=memory_id,
                entity_id=entity.id,
                entity_type=entity.type,
                relation='mentions',
                confidence=confidence
            )
            links.append(link)
            self.links.append(link)
        
        self._save_links()
        return links
    
    def get_entities_for_memory(self, memory_id: str) -> List[Tuple[Entity, str, float]]:
        """
        获取记忆关联的实体
        
        Returns:
            List of (entity, relation, confidence)
        """
        results = []
        
        for link in self.links:
            if link.memory_id == memory_id:
                entity = self.ontology.get_entity(link.entity_id)
                if entity:
                    results.append((entity, link.relation, link.confidence))
        
        return results
    
    def get_memories_for_entity(self, entity_id: str) -> List[Tuple[str, str, float]]:
        """
        获取实体关联的记忆
        
        Returns:
            List of (memory_id, relation, confidence)
        """
        results = []
        
        for link in self.links:
            if link.entity_id == entity_id:
                results.append((link.memory_id, link.relation, link.confidence))
        
        return results
    
    def enhance_search_results(self, 
                                results: List[Dict],
                                query: str) -> List[Dict]:
        """
        增强搜索结果，注入知识图谱信息
        
        Args:
            results: 原始搜索结果
            query: 查询文本
        
        Returns:
            增强后的搜索结果
        """
        # 从查询中提取实体
        query_entities = self.extractor.extract(query)
        
        enhanced_results = []
        for result in results:
            memory_id = result.get('id', '')
            
            # 获取记忆关联的实体
            linked_entities = self.get_entities_for_memory(memory_id)
            
            # 计算实体匹配分数
            entity_boost = 0.0
            matched_entities = []
            
            for entity, relation, confidence in linked_entities:
                for qe_name, qe_type, qe_conf in query_entities:
                    if entity.type == qe_type and qe_name.lower() in entity.name.lower():
                        entity_boost += 0.1 * confidence * qe_conf
                        matched_entities.append({
                            'name': entity.name,
                            'type': entity.type,
                            'relation': relation
                        })
            
            # 增强结果
            enhanced = result.copy()
            enhanced['entity_boost'] = entity_boost
            enhanced['matched_entities'] = matched_entities
            enhanced['enhanced_score'] = result.get('score', 0) + entity_boost
            
            enhanced_results.append(enhanced)
        
        # 重新排序
        enhanced_results.sort(key=lambda x: x.get('enhanced_score', 0), reverse=True)
        
        return enhanced_results
    
    def get_entity_context(self, entity_name: str) -> Dict[str, Any]:
        """
        获取实体的上下文信息
        
        Args:
            entity_name: 实体名称
        
        Returns:
            实体上下文信息
        """
        entities = self.ontology.search_entities(entity_name)
        
        if not entities:
            return {'found': False, 'name': entity_name}
        
        entity = entities[0]
        related = self.ontology.get_related_entities(entity.id)
        
        return {
            'found': True,
            'id': entity.id,
            'name': entity.name,
            'type': entity.type,
            'properties': entity.properties,
            'related_entities': [
                {'name': e.name, 'type': e.type, 'relation': r}
                for e, r in related
            ],
            'memory_count': len(self.get_memories_for_entity(entity.id))
        }


# 便捷函数
_bridge = None

def get_bridge() -> MemoryOntologyBridge:
    """获取默认桥接器实例"""
    global _bridge
    if _bridge is None:
        _bridge = MemoryOntologyBridge()
    return _bridge


def link_memory(memory_id: str, content: str) -> List[MemoryEntityLink]:
    """关联记忆到实体"""
    return get_bridge().link_memory_to_entities(memory_id, content)


def get_entity_info(entity_name: str) -> Dict[str, Any]:
    """获取实体信息"""
    return get_bridge().get_entity_context(entity_name)
