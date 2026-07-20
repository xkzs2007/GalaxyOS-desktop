"""
Relation Predictor - 关系预测器
基于 GNN 嵌入预测实体间的隐含关系
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple, Set

from knowledge_graph_gnn import KnowledgeGraphGNN


class RelationPredictor(nn.Module):
    """
    关系预测器

    功能：
    - 预测实体间的潜在关系
    - 关系分类
    - 知识图谱补全
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        hidden_dim: int = 256,
        num_relations: int = 10,
        dropout: float = 0.3,
        prediction_type: str = 'mlp'  # 'mlp', 'bilinear', 'distmult', 'transe'
    ):
        """
        Args:
            embedding_dim: 实体嵌入维度
            hidden_dim: 隐藏层维度
            num_relations: 关系类型数量
            dropout: Dropout 概率
            prediction_type: 预测器类型
        """
        super().__init__()
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        self.num_relations = num_relations
        self.prediction_type = prediction_type

        if prediction_type == 'mlp':
            # MLP 预测器
            self.predictor = nn.Sequential(
                nn.Linear(embedding_dim * 2, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, num_relations)
            )

        elif prediction_type == 'bilinear':
            # 双线性预测器
            self.bilinear = nn.Bilinear(embedding_dim, embedding_dim, hidden_dim)
            self.output = nn.Linear(hidden_dim, num_relations)

        elif prediction_type == 'distmult':
            # DistMult 风格
            self.relation_embeddings = nn.Embedding(num_relations, embedding_dim)

        elif prediction_type == 'transe':
            # TransE 风格
            self.relation_embeddings = nn.Embedding(num_relations, embedding_dim)
            self.margin = 1.0

        else:
            raise ValueError(f"Unknown prediction type: {prediction_type}")

        # 关系类型映射
        self._relation_to_id: Dict[str, int] = {}
        self._id_to_relation: Dict[int, str] = {}

    def register_relation(self, relation_name: str) -> int:
        """注册关系类型"""
        if relation_name not in self._relation_to_id:
            rel_id = len(self._relation_to_id)
            self._relation_to_id[relation_name] = rel_id
            self._id_to_relation[rel_id] = relation_name
        return self._relation_to_id[relation_name]

    def forward(
        self,
        head_emb: torch.Tensor,
        tail_emb: torch.Tensor
    ) -> torch.Tensor:
        """
        预测关系

        Args:
            head_emb: 头实体嵌入 (batch_size, embedding_dim)
            tail_emb: 尾实体嵌入 (batch_size, embedding_dim)

        Returns:
            关系概率分布 (batch_size, num_relations)
        """
        if self.prediction_type == 'mlp':
            # 拼接头尾实体嵌入
            combined = torch.cat([head_emb, tail_emb], dim=-1)
            logits = self.predictor(combined)
            return F.log_softmax(logits, dim=-1)

        elif self.prediction_type == 'bilinear':
            bilinear_out = self.bilinear(head_emb, tail_emb)
            logits = self.output(F.relu(bilinear_out))
            return F.log_softmax(logits, dim=-1)

        elif self.prediction_type == 'distmult':
            # DistMult: score(h, r, t) = h^T diag(r) t
            batch_size = head_emb.size(0)
            scores = []
            for rel_id in range(self.num_relations):
                rel_emb = self.relation_embeddings.weight[rel_id]
                score = (head_emb * rel_emb * tail_emb).sum(dim=-1)
                scores.append(score)
            scores = torch.stack(scores, dim=-1)
            return F.log_softmax(scores, dim=-1)

        elif self.prediction_type == 'transe':
            # TransE: score(h, r, t) = -||h + r - t||
            batch_size = head_emb.size(0)
            scores = []
            for rel_id in range(self.num_relations):
                rel_emb = self.relation_embeddings.weight[rel_id]
                dist = torch.norm(head_emb + rel_emb - tail_emb, p=2, dim=-1)
                scores.append(-dist)
            scores = torch.stack(scores, dim=-1)
            return F.log_softmax(scores, dim=-1)

    def predict_relation(
        self,
        head_emb: torch.Tensor,
        tail_emb: torch.Tensor,
        top_k: int = 5
    ) -> List[Tuple[int, float]]:
        """
        预测最可能的关系

        Args:
            head_emb: 头实体嵌入
            tail_emb: 尾实体嵌入

        Returns:
            (关系ID, 概率) 列表
        """
        with torch.no_grad():
            log_probs = self.forward(head_emb.unsqueeze(0), tail_emb.unsqueeze(0))
            probs = torch.exp(log_probs.squeeze(0))

            values, indices = torch.topk(probs, k=min(top_k, self.num_relations))

            return list(zip(indices.tolist(), values.tolist()))

    def predict_tail(
        self,
        head_emb: torch.Tensor,
        relation_id: int,
        candidate_embs: torch.Tensor,
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        给定头实体和关系，预测尾实体

        Args:
            head_emb: 头实体嵌入
            relation_id: 关系ID
            candidate_embs: 候选实体嵌入 (num_candidates, embedding_dim)
            top_k: 返回数量

        Returns:
            (候选索引, 得分) 列表
        """
        with torch.no_grad():
            if self.prediction_type == 'distmult':
                rel_emb = self.relation_embeddings.weight[relation_id]
                scores = (head_emb * rel_emb * candidate_embs).sum(dim=-1)

            elif self.prediction_type == 'transe':
                rel_emb = self.relation_embeddings.weight[relation_id]
                dist = torch.norm(
                    head_emb.unsqueeze(0) + rel_emb - candidate_embs,
                    p=2, dim=-1
                )
                scores = -dist

            else:
                # 使用通用方法
                head_expanded = head_emb.unsqueeze(0).expand(candidate_embs.size(0), -1)
                log_probs = self.forward(head_expanded, candidate_embs)
                scores = log_probs[:, relation_id]

            values, indices = torch.topk(scores, k=min(top_k, len(scores)))

            return list(zip(indices.tolist(), values.tolist()))

    def compute_loss(
        self,
        head_emb: torch.Tensor,
        tail_emb: torch.Tensor,
        relation_labels: torch.Tensor
    ) -> torch.Tensor:
        """
        计算损失

        Args:
            head_emb: 头实体嵌入
            tail_emb: 尾实体嵌入
            relation_labels: 关系标签

        Returns:
            损失值
        """
        log_probs = self.forward(head_emb, tail_emb)
        loss = F.nll_loss(log_probs, relation_labels)
        return loss


class KnowledgeGraphCompletion:
    """
    知识图谱补全

    整合 GNN 和关系预测器进行知识图谱补全
    """

    def __init__(
        self,
        kg_gnn: KnowledgeGraphGNN,
        num_relations: int = 20,
        hidden_dim: int = 256,
        prediction_type: str = 'mlp'
    ):
        """
        Args:
            kg_gnn: 知识图谱 GNN 模型
            num_relations: 关系类型数量
            hidden_dim: 隐藏层维度
            prediction_type: 预测器类型
        """
        self.kg_gnn = kg_gnn
        self.predictor = RelationPredictor(
            embedding_dim=kg_gnn.embedding_dim,
            hidden_dim=hidden_dim,
            num_relations=num_relations,
            prediction_type=prediction_type
        )

        # 已知关系缓存
        self._known_relations: Set[Tuple[str, str, str]] = set()

    def register_known_relation(
        self,
        head: str,
        relation: str,
        tail: str
    ) -> None:
        """注册已知关系"""
        self._known_relations.add((head, relation, tail))
        self.predictor.register_relation(relation)

    def is_known_relation(
        self,
        head: str,
        relation: str,
        tail: str
    ) -> bool:
        """检查关系是否已知"""
        return (head, relation, tail) in self._known_relations

    def predict_missing_relations(
        self,
        entity_pairs: Optional[List[Tuple[str, str]]] = None,
        threshold: float = 0.5,
        top_k: int = 3
    ) -> List[Tuple[str, str, str, float]]:
        """
        预测缺失的关系

        Args:
            entity_pairs: 待预测的实体对列表，如果为 None 则自动生成
            threshold: 概率阈值
            top_k: 每对实体返回的 top-k 关系

        Returns:
            (头实体, 关系, 尾实体, 概率) 列表
        """
        self.kg_gnn.eval()
        self.predictor.eval()

        # 如果没有提供实体对，自动生成
        if entity_pairs is None:
            entity_pairs = self._generate_candidate_pairs()

        predictions = []

        with torch.no_grad():
            # 获取所有实体嵌入
            all_embeddings = self.kg_gnn.forward()

            for head_name, tail_name in entity_pairs:
                # 获取实体嵌入
                head_emb = self.kg_gnn.get_entity_embedding(head_name)
                tail_emb = self.kg_gnn.get_entity_embedding(tail_name)

                if head_emb is None or tail_emb is None:
                    continue

                # 预测关系
                rel_predictions = self.predictor.predict_relation(
                    head_emb, tail_emb, top_k=top_k
                )

                for rel_id, prob in rel_predictions:
                    if prob >= threshold:
                        rel_name = self.predictor._id_to_relation.get(
                            rel_id, f"relation_{rel_id}"
                        )

                        # 检查是否为已知关系
                        if not self.is_known_relation(head_name, rel_name, tail_name):
                            predictions.append((head_name, rel_name, tail_name, prob))

        # 按概率排序
        predictions.sort(key=lambda x: x[3], reverse=True)

        return predictions

    def _generate_candidate_pairs(self) -> List[Tuple[str, str]]:
        """生成候选实体对"""
        entities = list(self.kg_gnn.graph_constructor.entities.values())
        pairs = []

        # 生成所有可能的实体对
        for i, e1 in enumerate(entities):
            for e2 in entities[i+1:]:
                # 检查是否已有直接关系
                neighbors = self.kg_gnn.graph_constructor.get_neighbors(e1.id, hop=1)
                if e2.id not in neighbors:
                    pairs.append((e1.name, e2.name))
                    pairs.append((e2.name, e1.name))

        return pairs

    def link_prediction(
        self,
        head: Optional[str] = None,
        relation: Optional[str] = None,
        tail: Optional[str] = None,
        top_k: int = 10
    ) -> List[Tuple[str, float]]:
        """
        链接预测

        给定 (h, r, ?)、(?, r, t) 或 (h, ?, t)，预测缺失的部分

        Args:
            head: 头实体（可选）
            relation: 关系（可选）
            tail: 尾实体（可选）
            top_k: 返回数量

        Returns:
            (预测结果, 得分) 列表
        """
        self.kg_gnn.eval()
        self.predictor.eval()

        with torch.no_grad():
            all_embeddings = self.kg_gnn.forward()

            # (h, r, ?) - 预测尾实体
            if head and relation and not tail:
                head_emb = self.kg_gnn.get_entity_embedding(head)
                if head_emb is None:
                    return []

                rel_id = self.predictor._relation_to_id.get(relation)
                if rel_id is None:
                    return []

                predictions = self.predictor.predict_tail(
                    head_emb, rel_id, all_embeddings, top_k
                )

                results = []
                for idx, score in predictions:
                    entity_id = self.kg_gnn._idx_to_id.get(idx)
                    if entity_id:
                        entity = self.kg_gnn.graph_constructor.get_entity(entity_id)
                        if entity:
                            results.append((entity.name, score))

                return results

            # (?, r, t) - 预测头实体
            elif not head and relation and tail:
                tail_emb = self.kg_gnn.get_entity_embedding(tail)
                if tail_emb is None:
                    return []

                rel_id = self.predictor._relation_to_id.get(relation)
                if rel_id is None:
                    return []

                # 反向预测
                # 对于对称关系，可以直接使用
                # 对于非对称关系，需要训练反向关系
                predictions = self.predictor.predict_tail(
                    tail_emb, rel_id, all_embeddings, top_k
                )

                results = []
                for idx, score in predictions:
                    entity_id = self.kg_gnn._idx_to_id.get(idx)
                    if entity_id:
                        entity = self.kg_gnn.graph_constructor.get_entity(entity_id)
                        if entity:
                            results.append((entity.name, score))

                return results

            # (h, ?, t) - 预测关系
            elif head and not relation and tail:
                head_emb = self.kg_gnn.get_entity_embedding(head)
                tail_emb = self.kg_gnn.get_entity_embedding(tail)

                if head_emb is None or tail_emb is None:
                    return []

                predictions = self.predictor.predict_relation(
                    head_emb, tail_emb, top_k
                )

                results = []
                for rel_id, prob in predictions:
                    rel_name = self.predictor._id_to_relation.get(
                        rel_id, f"relation_{rel_id}"
                    )
                    results.append((rel_name, prob))

                return results

        return []

    def train_step(
        self,
        triples: List[Tuple[str, str, str]],
        optimizer: torch.optim.Optimizer
    ) -> float:
        """
        训练步骤

        Args:
            triples: 三元组列表 (head, relation, tail)
            optimizer: 优化器

        Returns:
            损失值
        """
        self.kg_gnn.train()
        self.predictor.train()

        # 获取实体嵌入
        all_embeddings = self.kg_gnn.forward()

        # 准备批次数据
        head_embs = []
        tail_embs = []
        labels = []

        for head, relation, tail in triples:
            head_entity = self.kg_gnn.graph_constructor.get_entity_by_name(head)
            tail_entity = self.kg_gnn.graph_constructor.get_entity_by_name(tail)

            if head_entity is None or tail_entity is None:
                continue

            head_idx = self.kg_gnn._id_to_idx.get(head_entity.id)
            tail_idx = self.kg_gnn._id_to_idx.get(tail_entity.id)

            if head_idx is None or tail_idx is None:
                continue

            head_embs.append(all_embeddings[head_idx])
            tail_embs.append(all_embeddings[tail_idx])

            # 注册并获取关系ID
            rel_id = self.predictor.register_relation(relation)
            labels.append(rel_id)

        if not head_embs:
            return 0.0

        head_embs = torch.stack(head_embs)
        tail_embs = torch.stack(tail_embs)
        labels = torch.tensor(labels, dtype=torch.long)

        # 计算损失
        loss = self.predictor.compute_loss(head_embs, tail_embs, labels)

        # 反向传播
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        return loss.item()

    def evaluate(
        self,
        test_triples: List[Tuple[str, str, str]],
        k_values: List[int] = [1, 3, 10]
    ) -> Dict[str, float]:
        """
        评估模型

        Args:
            test_triples: 测试三元组
            k_values: Hit@k 的 k 值列表

        Returns:
            评估指标字典
        """
        self.kg_gnn.eval()
        self.predictor.eval()

        hits = {f'hit@{k}': 0 for k in k_values}
        mrr_sum = 0.0
        total = 0

        with torch.no_grad():
            for head, relation, tail in test_triples:
                # 预测尾实体
                predictions = self.link_prediction(
                    head=head, relation=relation, top_k=max(k_values)
                )

                # 计算排名
                rank = None
                for i, (pred_entity, _) in enumerate(predictions):
                    if pred_entity == tail:
                        rank = i + 1
                        break

                if rank is None:
                    rank = len(predictions) + 1

                # 计算 MRR
                mrr_sum += 1.0 / rank

                # 计算 Hits
                for k in k_values:
                    if rank <= k:
                        hits[f'hit@{k}'] += 1

                total += 1

        if total == 0:
            return {'mrr': 0.0, **{f'hit@{k}': 0.0 for k in k_values}}

        return {
            'mrr': mrr_sum / total,
            **{f'hit@{k}': hits[f'hit@{k}'] / total for k in k_values}
        }


class RelationExtractor:
    """
    关系抽取器

    从文本中抽取实体关系
    """

    def __init__(
        self,
        kg_completion: KnowledgeGraphCompletion,
        confidence_threshold: float = 0.7
    ):
        """
        Args:
            kg_completion: 知识图谱补全模型
            confidence_threshold: 置信度阈值
        """
        self.kg_completion = kg_completion
        self.confidence_threshold = confidence_threshold

        # 关系模式
        self.relation_patterns = {
            'is_a': ['是', '属于', '是一种'],
            'has_part': ['包含', '有', '拥有'],
            'related_to': ['相关', '有关', '涉及'],
            'used_for': ['用于', '用来', '目的是'],
            'created_by': ['由', '创造', '发明'],
            'located_at': ['位于', '在', '处于']
        }

    def extract_from_text(
        self,
        text: str,
        entities: List[str]
    ) -> List[Tuple[str, str, str, float]]:
        """
        从文本中抽取关系

        Args:
            text: 输入文本
            entities: 已识别的实体列表

        Returns:
            (头实体, 关系, 尾实体, 置信度) 列表
        """
        extracted = []

        # 基于模式的抽取
        for i, e1 in enumerate(entities):
            for e2 in entities[i+1:]:
                # 检查文本中是否同时包含两个实体
                if e1 in text and e2 in text:
                    # 尝试匹配关系模式
                    for rel_type, patterns in self.relation_patterns.items():
                        for pattern in patterns:
                            # 简单的模式匹配
                            if f"{e1}{pattern}{e2}" in text:
                                extracted.append((e1, rel_type, e2, 0.8))
                            elif f"{e2}{pattern}{e1}" in text:
                                extracted.append((e2, rel_type, e1, 0.8))

        # 基于 GNN 的预测
        for e1 in entities:
            for e2 in entities:
                if e1 != e2:
                    predictions = self.kg_completion.link_prediction(
                        head=e1, tail=e2, top_k=3
                    )
                    for rel, score in predictions:
                        if score >= self.confidence_threshold:
                            extracted.append((e1, rel, e2, score))

        # 去重
        seen = set()
        unique_extracted = []
        for item in extracted:
            key = (item[0], item[1], item[2])
            if key not in seen:
                seen.add(key)
                unique_extracted.append(item)

        return unique_extracted


if __name__ == '__main__':
    # 测试
    torch.manual_seed(42)

    # 创建知识图谱
    from knowledge_graph_gnn import KnowledgeGraphGNN

    kg = KnowledgeGraphGNN(
        embedding_dim=64,
        hidden_dim=128,
        num_layers=2,
        gnn_type='gat'
    )

    # 添加实体
    entities = ["Python", "机器学习", "深度学习", "TensorFlow", "PyTorch", "神经网络"]
    for e in entities:
        kg.add_entity(e, "concept")

    # 添加关系
    relations = [
        ("Python", "used_for", "机器学习"),
        ("机器学习", "includes", "深度学习"),
        ("深度学习", "uses", "神经网络"),
        ("深度学习", "implemented_by", "TensorFlow"),
        ("深度学习", "implemented_by", "PyTorch")
    ]

    for h, r, t in relations:
        kg.add_relation(h, t, r)

    kg.build_graph()

    # 创建关系预测器
    predictor = RelationPredictor(
        embedding_dim=64,
        hidden_dim=128,
        num_relations=10,
        prediction_type='mlp'
    )

    # 注册关系
    for h, r, t in relations:
        predictor.register_relation(r)

    # 测试预测
    head_emb = kg.get_entity_embedding("Python")
    tail_emb = kg.get_entity_embedding("深度学习")

    if head_emb is not None and tail_emb is not None:
        predictions = predictor.predict_relation(head_emb, tail_emb)
        print(f"预测关系: {predictions}")

    # 创建知识图谱补全
    kg_completion = KnowledgeGraphCompletion(kg, num_relations=10)

    # 注册已知关系
    for h, r, t in relations:
        kg_completion.register_known_relation(h, r, t)

    # 预测缺失关系
    missing = kg_completion.predict_missing_relations(threshold=0.3)
    print(f"\n预测的缺失关系: {missing[:5]}")

    # 链接预测
    link_pred = kg_completion.link_prediction(head="Python", relation="used_for", top_k=5)
    print(f"\n链接预测 (Python, used_for, ?): {link_pred}")
