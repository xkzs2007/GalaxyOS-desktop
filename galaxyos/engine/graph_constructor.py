"""
Graph Constructor - 知识图谱构建器
从记忆数据中自动构建知识图谱
"""

import json
import hashlib
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict
import numpy as np


@dataclass
class Entity:
    """实体节点"""
    id: str
    name: str
    entity_type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[np.ndarray] = None

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'name': self.name,
            'entity_type': self.entity_type,
            'properties': self.properties
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Entity':
        return cls(
            id=data['id'],
            name=data['name'],
            entity_type=data['entity_type'],
            properties=data.get('properties', {})
        )


@dataclass
class Relation:
    """关系边"""
    source_id: str
    target_id: str
    relation_type: str
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'source_id': self.source_id,
            'target_id': self.target_id,
            'relation_type': self.relation_type,
            'weight': self.weight,
            'properties': self.properties
        }


class GraphConstructor:
    """
    知识图谱构建器
    
    功能：
    - 从记忆数据自动构建图谱
    - 实体抽取与合并
    - 关系推断
    - 图谱序列化/反序列化
    """

    def __init__(self, embedding_dim: int = 128):
        self.embedding_dim = embedding_dim
        self.entities: Dict[str, Entity] = {}
        self.relations: List[Relation] = []
        self.adjacency: Dict[str, Set[str]] = defaultdict(set)
        self.reverse_adjacency: Dict[str, Set[str]] = defaultdict(set)
        self.entity_name_index: Dict[str, str] = {}  # name -> id

        # 实体类型统计
        self.type_counts: Dict[str, int] = defaultdict(int)

        # 关系类型统计
        self.relation_counts: Dict[str, int] = defaultdict(int)

    def _generate_id(self, name: str, entity_type: str) -> str:
        """生成唯一实体ID"""
        base = f"{entity_type}:{name}"
        return hashlib.md5(base.encode()).hexdigest()[:16]

    def add_entity(
        self,
        name: str,
        entity_type: str,
        properties: Optional[Dict] = None,
        embedding: Optional[np.ndarray] = None
    ) -> str:
        """
        添加实体
        
        Args:
            name: 实体名称
            entity_type: 实体类型
            properties: 实体属性
            embedding: 实体嵌入向量
            
        Returns:
            实体ID
        """
        # 检查是否已存在同名实体
        if name in self.entity_name_index:
            entity_id = self.entity_name_index[name]
            # 合并属性
            if properties:
                self.entities[entity_id].properties.update(properties)
            if embedding is not None:
                self.entities[entity_id].embedding = embedding
            return entity_id

        entity_id = self._generate_id(name, entity_type)

        entity = Entity(
            id=entity_id,
            name=name,
            entity_type=entity_type,
            properties=properties or {},
            embedding=embedding
        )

        self.entities[entity_id] = entity
        self.entity_name_index[name] = entity_id
        self.type_counts[entity_type] += 1

        return entity_id

    def add_relation(
        self,
        source: str,
        target: str,
        relation: str,
        weight: float = 1.0,
        properties: Optional[Dict] = None
    ) -> None:
        """
        添加关系
        
        Args:
            source: 源实体名称或ID
            target: 目标实体名称或ID
            relation: 关系类型
            weight: 关系权重
            properties: 关系属性
        """
        # 解析实体ID
        source_id = self._resolve_entity_id(source)
        target_id = self._resolve_entity_id(target)

        if source_id is None or target_id is None:
            return

        # 检查关系是否已存在
        for rel in self.relations:
            if (rel.source_id == source_id and
                rel.target_id == target_id and
                rel.relation_type == relation):
                # 更新权重
                rel.weight = max(rel.weight, weight)
                if properties:
                    rel.properties.update(properties)
                return

        relation_obj = Relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation,
            weight=weight,
            properties=properties or {}
        )

        self.relations.append(relation_obj)
        self.adjacency[source_id].add(target_id)
        self.reverse_adjacency[target_id].add(source_id)
        self.relation_counts[relation] += 1

    def _resolve_entity_id(self, identifier: str) -> Optional[str]:
        """解析实体ID"""
        if identifier in self.entities:
            return identifier
        if identifier in self.entity_name_index:
            return self.entity_name_index[identifier]
        return None

    def get_neighbors(self, entity_id: str, hop: int = 1) -> Set[str]:
        """
        获取邻居节点
        
        Args:
            entity_id: 实体ID
            hop: 跳数
            
        Returns:
            邻居实体ID集合
        """
        if hop == 1:
            return self.adjacency.get(entity_id, set())

        visited = {entity_id}
        frontier = {entity_id}

        for _ in range(hop):
            next_frontier = set()
            for node in frontier:
                neighbors = self.adjacency.get(node, set())
                for neighbor in neighbors:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier

        return visited - {entity_id}

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """获取实体"""
        return self.entities.get(entity_id)

    def get_entity_by_name(self, name: str) -> Optional[Entity]:
        """通过名称获取实体"""
        entity_id = self.entity_name_index.get(name)
        if entity_id:
            return self.entities.get(entity_id)
        return None

    def get_relations_for_entity(self, entity_id: str) -> List[Relation]:
        """获取实体的所有关系"""
        return [
            rel for rel in self.relations
            if rel.source_id == entity_id or rel.target_id == entity_id
        ]

    def build_from_memories(
        self,
        memories: List[Dict],
        entity_extractor: Optional[callable] = None
    ) -> None:
        """
        从记忆数据构建图谱
        
        Args:
            memories: 记忆数据列表
            entity_extractor: 可选的实体提取函数
        """
        for memory in memories:
            self._process_memory(memory, entity_extractor)

    def _process_memory(
        self,
        memory: Dict,
        entity_extractor: Optional[callable] = None
    ) -> None:
        """处理单条记忆"""
        # 提取实体
        if entity_extractor:
            entities_data = entity_extractor(memory)
        else:
            entities_data = self._default_entity_extraction(memory)

        # 添加实体
        entity_ids = []
        for entity_data in entities_data:
            entity_id = self.add_entity(
                name=entity_data['name'],
                entity_type=entity_data.get('type', 'unknown'),
                properties=entity_data.get('properties'),
                embedding=entity_data.get('embedding')
            )
            entity_ids.append(entity_id)

        # 推断关系
        relations_data = self._infer_relations(memory, entity_ids)
        for rel_data in relations_data:
            self.add_relation(**rel_data)

    def _default_entity_extraction(self, memory: Dict) -> List[Dict]:
        """默认实体提取逻辑"""
        entities = []

        # 从记忆中提取关键实体
        content = memory.get('content', '')
        memory_type = memory.get('type', 'general')

        # 添加记忆本身作为实体
        entities.append({
            'name': f"memory_{memory.get('id', 'unknown')}",
            'type': 'memory',
            'properties': {
                'content': content[:200],
                'memory_type': memory_type,
                'timestamp': memory.get('timestamp')
            }
        })

        # 提取主题/话题
        topics = memory.get('topics', [])
        for topic in topics:
            entities.append({
                'name': topic,
                'type': 'topic',
                'properties': {}
            })

        # 提取人物
        persons = memory.get('persons', [])
        for person in persons:
            entities.append({
                'name': person,
                'type': 'person',
                'properties': {}
            })

        # 提取地点
        locations = memory.get('locations', [])
        for loc in locations:
            entities.append({
                'name': loc,
                'type': 'location',
                'properties': {}
            })

        return entities

    def _infer_relations(
        self,
        memory: Dict,
        entity_ids: List[str]
    ) -> List[Dict]:
        """推断实体间的关系"""
        relations = []

        if len(entity_ids) < 2:
            return relations

        # 记忆实体与其他实体的关系
        memory_entity_id = entity_ids[0]
        other_entity_ids = entity_ids[1:]

        for entity_id in other_entity_ids:
            entity = self.entities.get(entity_id)
            if entity:
                relation_type = f"mentions_{entity.entity_type}"
                relations.append({
                    'source': memory_entity_id,
                    'target': entity_id,
                    'relation': relation_type,
                    'weight': 1.0
                })

        # 同一记忆中的实体建立关联
        for i, eid1 in enumerate(other_entity_ids):
            for eid2 in other_entity_ids[i+1:]:
                relations.append({
                    'source': eid1,
                    'target': eid2,
                    'relation': 'co_occurs',
                    'weight': 0.5
                })

        return relations

    def get_adjacency_matrix(self) -> Tuple[np.ndarray, Dict[str, int]]:
        """
        获取邻接矩阵

        Returns:
            adjacency_matrix: 邻接矩阵
            id_to_idx: 实体ID到索引的映射
        """
        n = len(self.entities)
        entity_ids = list(self.entities.keys())
        id_to_idx = {eid: i for i, eid in enumerate(entity_ids)}

        adj_matrix = np.zeros((n, n), dtype=np.float32)

        for rel in self.relations:
            i = id_to_idx[rel.source_id]
            j = id_to_idx[rel.target_id]
            adj_matrix[i, j] = rel.weight
            # 对于无向图，也设置反向边
            # adj_matrix[j, i] = rel.weight

        return adj_matrix, id_to_idx

    def get_edge_index(self) -> Tuple[np.ndarray, Dict[str, int]]:
        """
        获取稀疏边索引 (2, E) — 避免 N×N 稠密矩阵带来的 OOM

        Returns:
            edge_index: (2, E) int64 数组，edge_index[0]=src, edge_index[1]=dst
            id_to_idx: 实体ID到索引的映射
        """
        n = len(self.entities)
        entity_ids = list(self.entities.keys())
        id_to_idx = {eid: i for i, eid in enumerate(entity_ids)}

        src_list = []
        dst_list = []
        for rel in self.relations:
            if rel.source_id in id_to_idx and rel.target_id in id_to_idx:
                src_list.append(id_to_idx[rel.source_id])
                dst_list.append(id_to_idx[rel.target_id])

        if src_list:
            edge_index = np.stack([
                np.array(src_list, dtype=np.int64),
                np.array(dst_list, dtype=np.int64),
            ], axis=0)
        else:
            edge_index = np.zeros((2, 0), dtype=np.int64)

        return edge_index, id_to_idx

    def get_feature_matrix(self) -> Tuple[np.ndarray, Dict[str, int]]:
        """
        获取特征矩阵
        
        Returns:
            feature_matrix: 特征矩阵 (n_entities x embedding_dim)
            id_to_idx: 实体ID到索引的映射
        """
        n = len(self.entities)
        entity_ids = list(self.entities.keys())
        id_to_idx = {eid: i for i, eid in enumerate(entity_ids)}

        feature_matrix = np.zeros((n, self.embedding_dim), dtype=np.float32)

        for eid, entity in self.entities.items():
            idx = id_to_idx[eid]
            if entity.embedding is not None:
                feature_matrix[idx] = entity.embedding
            else:
                # 使用随机初始化
                feature_matrix[idx] = np.random.randn(self.embedding_dim).astype(np.float32) * 0.1

        return feature_matrix, id_to_idx

    def to_dict(self) -> Dict:
        """序列化为字典"""
        return {
            'entities': [e.to_dict() for e in self.entities.values()],
            'relations': [r.to_dict() for r in self.relations],
            'embedding_dim': self.embedding_dim,
            'type_counts': dict(self.type_counts),
            'relation_counts': dict(self.relation_counts)
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'GraphConstructor':
        """从字典反序列化"""
        graph = cls(embedding_dim=data.get('embedding_dim', 128))

        for entity_data in data.get('entities', []):
            entity = Entity.from_dict(entity_data)
            graph.entities[entity.id] = entity
            graph.entity_name_index[entity.name] = entity.id

        for rel_data in data.get('relations', []):
            relation = Relation(
                source_id=rel_data['source_id'],
                target_id=rel_data['target_id'],
                relation_type=rel_data['relation_type'],
                weight=rel_data.get('weight', 1.0),
                properties=rel_data.get('properties', {})
            )
            graph.relations.append(relation)
            graph.adjacency[relation.source_id].add(relation.target_id)
            graph.reverse_adjacency[relation.target_id].add(relation.source_id)

        graph.type_counts = defaultdict(int, data.get('type_counts', {}))
        graph.relation_counts = defaultdict(int, data.get('relation_counts', {}))

        return graph

    def save(self, filepath: str) -> None:
        """保存到文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, filepath: str) -> 'GraphConstructor':
        """从文件加载"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    def query_graph(
        self,
        query: str,
        top_k: int = 10,
        query_embedding: Optional[np.ndarray] = None
    ) -> List[Entity]:
        """
        查询知识图谱
        
        Args:
            query: 查询文本
            top_k: 返回数量
            query_embedding: 查询向量
            
        Returns:
            匹配的实体列表
        """
        results = []

        # 名称匹配
        query_lower = query.lower()
        for entity in self.entities.values():
            score = 0.0
            if query_lower in entity.name.lower():
                score = 1.0
            elif entity.entity_type.lower() == query_lower:
                score = 0.8

            if score > 0:
                results.append((entity, score))

        # 向量相似度匹配
        if query_embedding is not None:
            for entity in self.entities.values():
                if entity.embedding is not None:
                    similarity = np.dot(query_embedding, entity.embedding) / (
                        np.linalg.norm(query_embedding) * np.linalg.norm(entity.embedding) + 1e-8
                    )
                    if similarity > 0.5:
                        results.append((entity, float(similarity)))

        # 排序并返回
        results.sort(key=lambda x: x[1], reverse=True)
        return [r[0] for r in results[:top_k]]

    def get_statistics(self) -> Dict:
        """获取图谱统计信息"""
        return {
            'num_entities': len(self.entities),
            'num_relations': len(self.relations),
            'entity_types': dict(self.type_counts),
            'relation_types': dict(self.relation_counts),
            'avg_degree': len(self.relations) * 2 / max(len(self.entities), 1)
        }


if __name__ == '__main__':
    # 测试
    graph = GraphConstructor(embedding_dim=64)

    # 添加实体
    e1 = graph.add_entity("Python", "programming_language", {"year": 1991})
    e2 = graph.add_entity("机器学习", "topic", {})
    e3 = graph.add_entity("深度学习", "topic", {})

    # 添加关系
    graph.add_relation("Python", "机器学习", "used_for")
    graph.add_relation("机器学习", "深度学习", "includes")

    print("图谱统计:", graph.get_statistics())
    print("查询结果:", [e.name for e in graph.query_graph("Python")])
