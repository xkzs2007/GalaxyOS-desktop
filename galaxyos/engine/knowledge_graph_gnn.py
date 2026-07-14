"""
Knowledge Graph GNN - 图神经网络增强的知识图谱
整合 GraphSAGE 和 GAT 进行知识图谱表示学习
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple, Any, Union
import numpy as np
from collections import defaultdict

try:
    from .graph_constructor import GraphConstructor, Entity, Relation
    from .graphsage_layer import GraphSAGE
    from .gat_layer import GAT
except ImportError:
    from graph_constructor import GraphConstructor, Entity, Relation  # type: ignore[no-redef]
    from graphsage_layer import GraphSAGE  # type: ignore[no-redef]
    from gat_layer import GAT  # type: ignore[no-redef]


class KnowledgeGraphGNN(nn.Module):
    """
    图神经网络增强的知识图谱
    
    功能：
    - 整合 GraphSAGE 和 GAT
    - 实体嵌入学习
    - 关系感知的消息传递
    - 知识图谱补全
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        hidden_dim: int = 256,
        num_layers: int = 2,
        num_heads: int = 8,
        dropout: float = 0.3,
        gnn_type: str = 'gat',  # 'gat', 'graphsage', 'hybrid'
        num_relations: int = 10,
        use_relation_embedding: bool = True
    ):
        """
        Args:
            embedding_dim: 实体嵌入维度
            hidden_dim: 隐藏层维度
            num_layers: GNN 层数
            num_heads: 注意力头数量（GAT）
            dropout: Dropout 概率
            gnn_type: GNN 类型
            num_relations: 关系类型数量
            use_relation_embedding: 是否使用关系嵌入
        """
        super().__init__()
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.num_heads = num_heads
        self.gnn_type = gnn_type
        self.num_relations = num_relations
        self.use_relation_embedding = use_relation_embedding

        # 实体嵌入层
        self.entity_embedding = nn.EmbeddingBag(
            num_embeddings=100000,  # 最大实体数量
            embedding_dim=embedding_dim,
            mode='mean'
        )

        # 关系嵌入
        if use_relation_embedding:
            self.relation_embedding = nn.Embedding(num_relations, embedding_dim)

        # 特征投影
        self.feature_proj = nn.Linear(embedding_dim, hidden_dim)

        # GNN 层
        if gnn_type == 'gat':
            self.gnn = GAT(
                input_dim=hidden_dim,
                hidden_dim=hidden_dim // num_heads,
                output_dim=embedding_dim,
                num_heads=num_heads,
                num_layers=num_layers,
                dropout=dropout
            )
        elif gnn_type == 'graphsage':
            self.gnn = GraphSAGE(
                input_dim=hidden_dim,
                hidden_dims=[hidden_dim] * (num_layers - 1),
                output_dim=embedding_dim,
                aggregator_type='mean',
                dropout=dropout
            )
        elif gnn_type == 'hybrid':
            # 混合使用 GAT 和 GraphSAGE
            self.gnn_gat = GAT(
                input_dim=hidden_dim,
                hidden_dim=hidden_dim // num_heads,
                output_dim=hidden_dim,
                num_heads=num_heads,
                num_layers=1,
                dropout=dropout
            )
            self.gnn_sage = GraphSAGE(
                input_dim=hidden_dim,
                hidden_dims=[],
                output_dim=embedding_dim,
                aggregator_type='mean',
                dropout=dropout
            )
            self.combine = nn.Linear(hidden_dim + embedding_dim, embedding_dim)
        else:
            raise ValueError(f"Unknown GNN type: {gnn_type}")

        # 输出层
        self.output_layer = nn.Sequential(
            nn.Linear(embedding_dim, embedding_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(embedding_dim, embedding_dim)
        )

        # 图构建器
        self.graph_constructor = GraphConstructor(embedding_dim=embedding_dim)

        # 缓存
        self._entity_id_map: Dict[str, int] = {}
        self._relation_id_map: Dict[str, int] = {}
        self._next_entity_id = 0
        self._next_relation_id = 0

    def _get_entity_id(self, entity_name: str) -> int:
        """获取或创建实体ID"""
        if entity_name not in self._entity_id_map:
            self._entity_id_map[entity_name] = self._next_entity_id
            self._next_entity_id += 1
        return self._entity_id_map[entity_name]

    def _get_relation_id(self, relation_type: str) -> int:
        """获取或创建关系ID"""
        if relation_type not in self._relation_id_map:
            self._relation_id_map[relation_type] = self._next_relation_id
            self._next_relation_id += 1
        return self._relation_id_map[relation_type]

    def add_entity(
        self,
        name: str,
        entity_type: str,
        properties: Optional[Dict] = None,
        embedding: Optional[np.ndarray] = None
    ) -> str:
        """添加实体"""
        return self.graph_constructor.add_entity(name, entity_type, properties, embedding)

    def add_relation(
        self,
        source: str,
        target: str,
        relation: str,
        weight: float = 1.0,
        properties: Optional[Dict] = None
    ) -> None:
        """添加关系"""
        self.graph_constructor.add_relation(source, target, relation, weight, properties)
        # 更新关系ID映射
        self._get_relation_id(relation)

    def build_graph(self) -> None:
        """
        构建图数据结构

        双路径存储（避免大图 OOM）：
          - 始终缓存 edge_index (2, E) 稀疏表示
          - 当图小时 (N <= GAT_sparse_threshold) 额外缓存 _adj_matrix 供向后兼容
          - 当图大时只存 edge_index，GAT 前向自动用 SparseGraphAttentionLayer
        """
        from gat_layer import _DEFAULT_SPARSE_THRESHOLD
        edge_index, id_to_idx = self.graph_constructor.get_edge_index()
        feature_matrix, _ = self.graph_constructor.get_feature_matrix()

        # 总是存 feature + edge_index
        self._feature_matrix = torch.tensor(feature_matrix, dtype=torch.float32)
        self._edge_index = torch.tensor(edge_index, dtype=torch.long)
        self._id_to_idx = id_to_idx
        self._idx_to_id = {v: k for k, v in id_to_idx.items()}

        # 小图额外缓存稠密 adj (向后兼容 + 取 attention weights)
        N = feature_matrix.shape[0] if feature_matrix.size > 0 else 0
        if N > 0 and N <= _DEFAULT_SPARSE_THRESHOLD:
            adj_matrix, _ = self.graph_constructor.get_adjacency_matrix()
            self._adj_matrix = torch.tensor(adj_matrix, dtype=torch.float32)
        else:
            self._adj_matrix = None  # 大图不缓存稠密

    def _resolve_graph(self, device: torch.device) -> torch.Tensor:
        """按 N 大小返回稠密 adj 或 edge_index，给 GAT.forward 用。"""
        if self._adj_matrix is not None:
            return self._adj_matrix.to(device)
        return self._edge_index.to(device)

    def forward(
        self,
        entity_ids: Optional[torch.Tensor] = None,
        adj_matrix: Optional[torch.Tensor] = None,
        feature_matrix: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        """
        前向传播

        Args:
            entity_ids: 实体ID张量（可选）
            adj_matrix: 邻接矩阵（可选）— 仅向后兼容
            feature_matrix: 特征矩阵（可选）

        Returns:
            实体嵌入
        """
        # 使用缓存的数据或提供的输入
        # 优先用 edge_index（PyG 友好 + 节省 5.8GB 容器下小图稠密 adj 显存）
        # 如果显式传 adj_matrix 才用稠密（向后兼容）
        if adj_matrix is None:
            # 内部缓存：优先用 edge_index（pyg 后端可吃）
            if getattr(self, '_edge_index', None) is not None:
                adj_matrix = None  # 保持 None，让下方用 edge_index
            else:
                adj_matrix = getattr(self, '_adj_matrix', None)
        if feature_matrix is None:
            feature_matrix = getattr(self, '_feature_matrix', None)

        if feature_matrix is None:
            raise ValueError("Please call build_graph() first or provide adj_matrix and feature_matrix")

        # 移动到正确的设备
        device = next(self.parameters()).device
        feature_matrix = feature_matrix.to(device)
        # 优先 edge_index（PyG 友好）；没有才退回 adj_matrix
        edge_index = getattr(self, '_edge_index', None)
        if adj_matrix is None and edge_index is not None:
            graph = edge_index.to(device)
        elif adj_matrix is not None:
            graph = adj_matrix.to(device)
        else:
            # 没有图数据 — 返回恒等特征
            return self.output_layer(self.feature_proj(feature_matrix))

        # 特征投影
        h = self.feature_proj(feature_matrix)

        # GNN 前向传播
        if self.gnn_type == 'hybrid':
            h_gat = self.gnn_gat(h, graph)
            h_sage = self.gnn_sage(h, self._get_adj_list_from_index(edge_index, device))
            h = torch.cat([h_gat, h_sage], dim=-1)
            h = self.combine(h)
        elif self.gnn_type == 'graphsage':
            h = self.gnn(h, self._get_adj_list_from_index(edge_index, device))
        else:
            h = self.gnn(h, graph)

        # 输出层
        output = self.output_layer(h)

        return output

    def _get_adj_list_from_index(
        self, edge_index: Optional[torch.Tensor], device: torch.device
    ) -> List[List[int]]:
        """从 edge_index 生成邻接表（GraphSAGE 用）。"""
        if edge_index is None or edge_index.numel() == 0:
            n = self._feature_matrix.size(0) if self._feature_matrix is not None else 0
            return [[] for _ in range(n)]
        ei = edge_index.to(device)
        src = ei[0].cpu().numpy()
        dst = ei[1].cpu().numpy()
        n = int(max(src.max(initial=0), dst.max(initial=0))) + 1 if src.size > 0 else 0
        # 实际上节点数从 feature_matrix 取更稳
        if self._feature_matrix is not None:
            n = self._feature_matrix.size(0)
        adj_list: List[List[int]] = [[] for _ in range(n)]
        for s, d in zip(src.tolist(), dst.tolist()):
            if 0 <= s < n:
                adj_list[s].append(d)
        return adj_list

    def get_entity_embedding(
        self,
        entity_name: str
    ) -> Optional[torch.Tensor]:
        """
        获取实体嵌入
        
        Args:
            entity_name: 实体名称
            
        Returns:
            实体嵌入向量
        """
        entity = self.graph_constructor.get_entity_by_name(entity_name)
        if entity is None:
            return None

        entity_id = entity.id
        if entity_id not in self._id_to_idx:
            return None

        idx = self._id_to_idx[entity_id]

        # 获取所有嵌入
        with torch.no_grad():
            embeddings = self.forward()
            return embeddings[idx]

    def query_graph(
        self,
        query: str,
        top_k: int = 10,
        query_embedding: Optional[np.ndarray] = None
    ) -> List[Tuple[Entity, float]]:
        """
        查询知识图谱
        
        Args:
            query: 查询文本
            top_k: 返回数量
            query_embedding: 查询向量
            
        Returns:
            (实体, 分数) 列表
        """
        # 首先使用图构建器的基础查询
        base_results = self.graph_constructor.query_graph(query, top_k * 2, query_embedding)

        # 如果有 GNN 嵌入，使用它们进行重排序
        if hasattr(self, '_adj_matrix') and query_embedding is not None:
            with torch.no_grad():
                entity_embeddings = self.forward()

            scores = []
            query_tensor = torch.tensor(query_embedding, dtype=torch.float32)
            query_tensor = query_tensor.to(entity_embeddings.device)

            for entity in base_results:
                if entity.id in self._id_to_idx:
                    idx = self._id_to_idx[entity.id]
                    entity_emb = entity_embeddings[idx]

                    # 计算相似度
                    similarity = F.cosine_similarity(
                        query_tensor.unsqueeze(0),
                        entity_emb.unsqueeze(0)
                    ).item()
                    scores.append((entity, similarity))

            # 按相似度排序
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:top_k]

        return [(e, 1.0) for e in base_results[:top_k]]

    def find_similar_entities(
        self,
        entity_name: str,
        top_k: int = 10
    ) -> List[Tuple[Entity, float]]:
        """
        查找相似实体
        
        Args:
            entity_name: 实体名称
            top_k: 返回数量
            
        Returns:
            (实体, 相似度) 列表
        """
        target_embedding = self.get_entity_embedding(entity_name)
        if target_embedding is None:
            return []

        with torch.no_grad():
            all_embeddings = self.forward()

        # 计算相似度
        similarities = F.cosine_similarity(
            target_embedding.unsqueeze(0),
            all_embeddings,
            dim=-1
        )

        # 获取 top-k
        num_entities = all_embeddings.size(0)
        values, indices = torch.topk(similarities, k=min(top_k + 1, num_entities))

        results = []
        target_entity = self.graph_constructor.get_entity_by_name(entity_name)

        for val, idx in zip(values.tolist(), indices.tolist()):
            entity_id = self._idx_to_id.get(idx)
            if entity_id:
                entity = self.graph_constructor.get_entity(entity_id)
                if entity and entity != target_entity:
                    results.append((entity, val))

        return results[:top_k]

    def get_neighbors_info(
        self,
        entity_name: str,
        hop: int = 1
    ) -> Dict[str, Any]:
        """
        获取邻居信息
        
        Args:
            entity_name: 实体名称
            hop: 跳数
            
        Returns:
            邻居信息字典
        """
        entity = self.graph_constructor.get_entity_by_name(entity_name)
        if entity is None:
            return {}

        neighbor_ids = self.graph_constructor.get_neighbors(entity.id, hop)
        neighbors = [
            self.graph_constructor.get_entity(nid)
            for nid in neighbor_ids
        ]

        relations = self.graph_constructor.get_relations_for_entity(entity.id)

        return {
            'entity': entity,
            'neighbors': [n for n in neighbors if n is not None],
            'relations': relations,
            'hop': hop
        }

    def save(self, filepath: str) -> None:
        """保存模型和图谱"""
        # 保存图谱
        graph_path = filepath.replace('.pt', '_graph.json')
        self.graph_constructor.save(graph_path)

        # 保存模型
        torch.save({
            'state_dict': self.state_dict(),
            'config': {
                'embedding_dim': self.embedding_dim,
                'hidden_dim': self.hidden_dim,
                'num_layers': self.num_layers,
                'num_heads': self.num_heads,
                'gnn_type': self.gnn_type,
                'num_relations': self.num_relations
            },
            'entity_id_map': self._entity_id_map,
            'relation_id_map': self._relation_id_map
        }, filepath)

    @classmethod
    def load(cls, filepath: str) -> 'KnowledgeGraphGNN':
        """加载模型和图谱"""
        checkpoint = torch.load(filepath)

        model = cls(**checkpoint['config'])
        model.load_state_dict(checkpoint['state_dict'])
        model._entity_id_map = checkpoint['entity_id_map']
        model._relation_id_map = checkpoint['relation_id_map']

        # 加载图谱
        graph_path = filepath.replace('.pt', '_graph.json')
        model.graph_constructor = GraphConstructor.load(graph_path)

        return model

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        stats = self.graph_constructor.get_statistics()
        stats.update({
            'gnn_type': self.gnn_type,
            'embedding_dim': self.embedding_dim,
            'num_layers': self.num_layers
        })
        return stats


class KnowledgeGraphEncoder(KnowledgeGraphGNN):
    """
    知识图谱编码器
    
    用于下游任务的实体和关系编码
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # 关系编码器
        self.relation_encoder = nn.Sequential(
            nn.Linear(self.embedding_dim, self.hidden_dim),
            nn.ReLU(),
            nn.Linear(self.hidden_dim, self.embedding_dim)
        )

    def encode_triple(
        self,
        head: str,
        relation: str,
        tail: str
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        编码三元组
        
        Args:
            head: 头实体名称
            relation: 关系类型
            tail: 尾实体名称
            
        Returns:
            (头实体嵌入, 关系嵌入, 尾实体嵌入)
        """
        head_emb = self.get_entity_embedding(head)
        tail_emb = self.get_entity_embedding(tail)

        if head_emb is None or tail_emb is None:
            raise ValueError(f"Entity not found: {head if head_emb is None else tail}")

        # 关系嵌入
        rel_id = self._get_relation_id(relation)
        rel_emb = self.relation_embedding(torch.tensor([rel_id]))
        rel_emb = self.relation_encoder(rel_emb).squeeze(0)

        return head_emb, rel_emb, tail_emb

    def compute_triple_score(
        self,
        head: str,
        relation: str,
        tail: str,
        scoring_func: str = 'distmult'
    ) -> float:
        """
        计算三元组得分
        
        Args:
            head: 头实体
            relation: 关系
            tail: 尾实体
            scoring_func: 打分函数 ('distmult', 'transe', 'complex')
            
        Returns:
            三元组得分
        """
        head_emb, rel_emb, tail_emb = self.encode_triple(head, relation, tail)

        if scoring_func == 'distmult':
            # DistMult: (h * r * t).sum()
            score = (head_emb * rel_emb * tail_emb).sum().item()
        elif scoring_func == 'transe':
            # TransE: -||h + r - t||
            score = -torch.norm(head_emb + rel_emb - tail_emb, p=2).item()
        elif scoring_func == 'complex':
            # Simplified ComplEx
            score = (head_emb * rel_emb * tail_emb).sum().item()
        else:
            raise ValueError(f"Unknown scoring function: {scoring_func}")

        return score


if __name__ == '__main__':
    # 测试
    torch.manual_seed(42)

    # 创建知识图谱
    kg = KnowledgeGraphGNN(
        embedding_dim=64,
        hidden_dim=128,
        num_layers=2,
        gnn_type='gat'
    )

    # 添加实体
    kg.add_entity("Python", "language", {"year": 1991})
    kg.add_entity("机器学习", "topic", {})
    kg.add_entity("深度学习", "topic", {})
    kg.add_entity("TensorFlow", "framework", {})
    kg.add_entity("PyTorch", "framework", {})

    # 添加关系
    kg.add_relation("Python", "机器学习", "used_for")
    kg.add_relation("机器学习", "深度学习", "includes")
    kg.add_relation("深度学习", "TensorFlow", "implemented_by")
    kg.add_relation("深度学习", "PyTorch", "implemented_by")

    # 构建图
    kg.build_graph()

    # 获取嵌入
    embeddings = kg.forward()
    print(f"实体嵌入形状: {embeddings.shape}")

    # 查询
    results = kg.query_graph("Python")
    print(f"查询结果: {[r[0].name for r in results]}")

    # 查找相似实体
    similar = kg.find_similar_entities("Python")
    print(f"相似实体: {[(s[0].name, s[1]) for s in similar]}")

    print(f"\n统计信息: {kg.get_statistics()}")
