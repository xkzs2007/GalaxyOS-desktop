"""
Unit Tests for Knowledge Graph GNN Implementation
"""

import unittest
import torch
import numpy as np
import tempfile
import os
from typing import List, Dict

# Import modules
from graph_constructor import GraphConstructor, Entity, Relation
from graphsage_layer import GraphSAGELayer, GraphSAGE, MeanAggregator
from gat_layer import GraphAttentionLayer, MultiHeadGraphAttentionLayer, GAT
from knowledge_graph_gnn import KnowledgeGraphGNN, KnowledgeGraphEncoder
from relation_predictor import RelationPredictor, KnowledgeGraphCompletion


class TestGraphConstructor(unittest.TestCase):
    """测试图构建器"""
    
    def setUp(self):
        """设置测试环境"""
        self.graph = GraphConstructor(embedding_dim=64)
    
    def test_add_entity(self):
        """测试添加实体"""
        entity_id = self.graph.add_entity("Python", "language", {"year": 1991})
        self.assertIsNotNone(entity_id)
        self.assertEqual(len(self.graph.entities), 1)
        
        # 测试重复添加
        entity_id2 = self.graph.add_entity("Python", "language", {"version": "3.9"})
        self.assertEqual(entity_id, entity_id2)
        self.assertEqual(len(self.graph.entities), 1)
    
    def test_add_relation(self):
        """测试添加关系"""
        self.graph.add_entity("Python", "language")
        self.graph.add_entity("机器学习", "topic")
        self.graph.add_relation("Python", "机器学习", "used_for")
        
        self.assertEqual(len(self.graph.relations), 1)
        # 检查邻接表中是否包含目标实体ID
        python_id = self.graph.entity_name_index["Python"]
        ml_id = self.graph.entity_name_index["机器学习"]
        self.assertIn(ml_id, self.graph.adjacency[python_id])
    
    def test_get_neighbors(self):
        """测试获取邻居"""
        self.graph.add_entity("A", "test")
        self.graph.add_entity("B", "test")
        self.graph.add_entity("C", "test")
        self.graph.add_relation("A", "B", "connects")
        self.graph.add_relation("B", "C", "connects")
        
        neighbors = self.graph.get_neighbors(self.graph.entity_name_index["A"])
        self.assertEqual(len(neighbors), 1)
    
    def test_query_graph(self):
        """测试图谱查询"""
        self.graph.add_entity("Python", "language")
        self.graph.add_entity("Java", "language")
        self.graph.add_entity("机器学习", "topic")
        
        results = self.graph.query_graph("Python")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Python")
    
    def test_serialization(self):
        """测试序列化和反序列化"""
        self.graph.add_entity("Python", "language", {"year": 1991})
        self.graph.add_entity("机器学习", "topic")
        self.graph.add_relation("Python", "机器学习", "used_for")
        
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            filepath = f.name
        
        try:
            self.graph.save(filepath)
            loaded_graph = GraphConstructor.load(filepath)
            
            self.assertEqual(len(loaded_graph.entities), len(self.graph.entities))
            self.assertEqual(len(loaded_graph.relations), len(self.graph.relations))
        finally:
            os.unlink(filepath)
    
    def test_get_adjacency_matrix(self):
        """测试邻接矩阵生成"""
        self.graph.add_entity("A", "test")
        self.graph.add_entity("B", "test")
        self.graph.add_relation("A", "B", "connects")
        
        adj_matrix, id_to_idx = self.graph.get_adjacency_matrix()
        
        self.assertEqual(adj_matrix.shape[0], 2)
        self.assertEqual(adj_matrix.shape[1], 2)
    
    def test_build_from_memories(self):
        """测试从记忆构建图谱"""
        memories = [
            {
                'id': 'mem1',
                'content': 'Python is used for machine learning',
                'type': 'fact',
                'topics': ['Python', '机器学习'],
                'persons': [],
                'locations': []
            }
        ]
        
        self.graph.build_from_memories(memories)
        
        self.assertGreater(len(self.graph.entities), 0)


class TestGraphSAGELayer(unittest.TestCase):
    """测试 GraphSAGE 层"""
    
    def setUp(self):
        """设置测试环境"""
        torch.manual_seed(42)
        self.num_nodes = 50
        self.input_dim = 32
        self.output_dim = 64
        
        # 创建测试数据
        self.features = torch.randn(self.num_nodes, self.input_dim)
        self.adj_list = [
            list(np.random.choice(self.num_nodes, size=np.random.randint(1, 8), replace=False))
            for _ in range(self.num_nodes)
        ]
    
    def test_mean_aggregator(self):
        """测试均值聚合器"""
        aggregator = MeanAggregator(self.input_dim, self.output_dim)
        
        neighbors = torch.randint(0, self.num_nodes, (self.num_nodes, 10))
        num_neighbors = torch.randint(1, 10, (self.num_nodes,))
        
        output = aggregator(self.features, neighbors, num_neighbors)
        
        self.assertEqual(output.shape, (self.num_nodes, self.output_dim))
    
    def test_graphsage_layer_forward(self):
        """测试 GraphSAGE 层前向传播"""
        layer = GraphSAGELayer(
            input_dim=self.input_dim,
            output_dim=self.output_dim,
            aggregator_type='mean'
        )
        
        output = layer(self.features, self.adj_list, num_samples=5)
        
        self.assertEqual(output.shape, (self.num_nodes, self.output_dim))
    
    def test_graphsage_model(self):
        """测试完整 GraphSAGE 模型"""
        model = GraphSAGE(
            input_dim=self.input_dim,
            hidden_dims=[64, 32],
            output_dim=self.output_dim,
            aggregator_type='mean'
        )
        
        output = model(self.features, self.adj_list)
        
        self.assertEqual(output.shape, (self.num_nodes, self.output_dim))
    
    def test_different_aggregators(self):
        """测试不同聚合器"""
        for agg_type in ['mean', 'pool']:
            layer = GraphSAGELayer(
                input_dim=self.input_dim,
                output_dim=self.output_dim,
                aggregator_type=agg_type
            )
            output = layer(self.features, self.adj_list)
            self.assertEqual(output.shape, (self.num_nodes, self.output_dim))


class TestGATLayer(unittest.TestCase):
    """测试 GAT 层"""
    
    def setUp(self):
        """设置测试环境"""
        torch.manual_seed(42)
        self.num_nodes = 50
        self.input_dim = 32
        self.output_dim = 64
        self.num_heads = 4
        
        # 创建测试数据
        self.features = torch.randn(self.num_nodes, self.input_dim)
        
        # 创建邻接矩阵
        adj = torch.rand(self.num_nodes, self.num_nodes)
        self.adj = (adj > 0.8).float() + torch.eye(self.num_nodes)
    
    def test_single_head_attention(self):
        """测试单头注意力"""
        layer = GraphAttentionLayer(self.input_dim, self.output_dim // self.num_heads)
        
        output = layer(self.features, self.adj)
        
        expected_dim = self.output_dim // self.num_heads
        self.assertEqual(output.shape, (self.num_nodes, expected_dim))
    
    def test_multi_head_attention(self):
        """测试多头注意力"""
        layer = MultiHeadGraphAttentionLayer(
            self.input_dim, 
            self.output_dim // self.num_heads,
            num_heads=self.num_heads,
            concat=True
        )
        
        output = layer(self.features, self.adj)
        
        self.assertEqual(output.shape, (self.num_nodes, self.output_dim))
    
    def test_gat_model(self):
        """测试完整 GAT 模型"""
        model = GAT(
            input_dim=self.input_dim,
            hidden_dim=self.output_dim // self.num_heads,
            output_dim=self.output_dim,
            num_heads=self.num_heads,
            num_layers=2
        )
        
        output = model(self.features, self.adj)
        
        self.assertEqual(output.shape, (self.num_nodes, self.output_dim))
    
    def test_attention_weights(self):
        """测试注意力权重获取"""
        model = GAT(
            input_dim=self.input_dim,
            hidden_dim=self.output_dim // self.num_heads,
            output_dim=self.output_dim,
            num_heads=self.num_heads,
            num_layers=2
        )
        
        attn_weights = model.get_attention_weights(self.features, self.adj, layer_idx=0)
        
        if attn_weights is not None:
            self.assertEqual(attn_weights.shape, (self.num_nodes, self.num_nodes))


class TestKnowledgeGraphGNN(unittest.TestCase):
    """测试知识图谱 GNN"""
    
    def setUp(self):
        """设置测试环境"""
        torch.manual_seed(42)
        self.kg = KnowledgeGraphGNN(
            embedding_dim=32,
            hidden_dim=64,
            num_layers=2,
            num_heads=4,
            gnn_type='gat'
        )
        
        # 添加测试数据
        self.entities = ["Python", "Java", "机器学习", "深度学习", "TensorFlow"]
        for e in self.entities:
            self.kg.add_entity(e, "concept")
        
        self.relations = [
            ("Python", "机器学习", "used_for"),
            ("机器学习", "深度学习", "includes"),
            ("深度学习", "TensorFlow", "implemented_by")
        ]
        
        for h, r, t in self.relations:
            self.kg.add_relation(h, t, r)
        
        self.kg.build_graph()
    
    def test_forward(self):
        """测试前向传播"""
        embeddings = self.kg.forward()
        
        self.assertEqual(embeddings.shape[0], len(self.entities))
        self.assertEqual(embeddings.shape[1], self.kg.embedding_dim)
    
    def test_get_entity_embedding(self):
        """测试获取实体嵌入"""
        emb = self.kg.get_entity_embedding("Python")
        
        self.assertIsNotNone(emb)
        self.assertEqual(emb.shape[0], self.kg.embedding_dim)
    
    def test_query_graph(self):
        """测试图谱查询"""
        results = self.kg.query_graph("Python", top_k=3)
        
        self.assertGreater(len(results), 0)
        self.assertTrue(any(r[0].name == "Python" for r in results))
    
    def test_find_similar_entities(self):
        """测试查找相似实体"""
        similar = self.kg.find_similar_entities("Python", top_k=3)
        
        self.assertIsInstance(similar, list)
        # 不应该包含自己
        self.assertTrue(all(s[0].name != "Python" for s in similar))
    
    def test_get_neighbors_info(self):
        """测试获取邻居信息"""
        info = self.kg.get_neighbors_info("Python", hop=1)
        
        self.assertIn('entity', info)
        self.assertIn('neighbors', info)
        self.assertIn('relations', info)
    
    def test_different_gnn_types(self):
        """测试不同 GNN 类型"""
        for gnn_type in ['gat', 'graphsage']:
            kg = KnowledgeGraphGNN(
                embedding_dim=32,
                hidden_dim=64,
                num_layers=2,
                gnn_type=gnn_type
            )
            
            kg.add_entity("Test", "test")
            kg.build_graph()
            
            embeddings = kg.forward()
            self.assertEqual(embeddings.shape[1], 32)
    
    def test_save_load(self):
        """测试保存和加载"""
        with tempfile.NamedTemporaryFile(suffix='.pt', delete=False) as f:
            filepath = f.name
        
        try:
            self.kg.save(filepath)
            loaded_kg = KnowledgeGraphGNN.load(filepath)
            
            self.assertEqual(
                len(loaded_kg.graph_constructor.entities),
                len(self.kg.graph_constructor.entities)
            )
        finally:
            os.unlink(filepath)
            graph_path = filepath.replace('.pt', '_graph.json')
            if os.path.exists(graph_path):
                os.unlink(graph_path)


class TestRelationPredictor(unittest.TestCase):
    """测试关系预测器"""
    
    def setUp(self):
        """设置测试环境"""
        torch.manual_seed(42)
        
        self.embedding_dim = 32
        self.num_relations = 5
        
        self.predictor = RelationPredictor(
            embedding_dim=self.embedding_dim,
            hidden_dim=64,
            num_relations=self.num_relations,
            prediction_type='mlp'
        )
        
        # 注册关系
        self.relations = ['used_for', 'includes', 'implemented_by', 'related_to', 'is_a']
        for r in self.relations:
            self.predictor.register_relation(r)
    
    def test_forward(self):
        """测试前向传播"""
        batch_size = 10
        head_emb = torch.randn(batch_size, self.embedding_dim)
        tail_emb = torch.randn(batch_size, self.embedding_dim)
        
        log_probs = self.predictor(head_emb, tail_emb)
        
        self.assertEqual(log_probs.shape, (batch_size, self.num_relations))
    
    def test_predict_relation(self):
        """测试关系预测"""
        head_emb = torch.randn(self.embedding_dim)
        tail_emb = torch.randn(self.embedding_dim)
        
        predictions = self.predictor.predict_relation(head_emb, tail_emb, top_k=3)
        
        self.assertEqual(len(predictions), 3)
        for rel_id, prob in predictions:
            self.assertIsInstance(rel_id, int)
            self.assertIsInstance(prob, float)
    
    def test_different_prediction_types(self):
        """测试不同预测类型"""
        for pred_type in ['mlp', 'bilinear', 'distmult', 'transe']:
            predictor = RelationPredictor(
                embedding_dim=self.embedding_dim,
                hidden_dim=64,
                num_relations=self.num_relations,
                prediction_type=pred_type
            )
            
            head_emb = torch.randn(self.embedding_dim)
            tail_emb = torch.randn(self.embedding_dim)
            
            predictions = predictor.predict_relation(head_emb, tail_emb)
            
            self.assertGreater(len(predictions), 0)
    
    def test_compute_loss(self):
        """测试损失计算"""
        batch_size = 10
        head_emb = torch.randn(batch_size, self.embedding_dim)
        tail_emb = torch.randn(batch_size, self.embedding_dim)
        labels = torch.randint(0, self.num_relations, (batch_size,))
        
        loss = self.predictor.compute_loss(head_emb, tail_emb, labels)
        
        self.assertIsInstance(loss.item(), float)


class TestKnowledgeGraphCompletion(unittest.TestCase):
    """测试知识图谱补全"""
    
    def setUp(self):
        """设置测试环境"""
        torch.manual_seed(42)
        
        # 创建知识图谱
        self.kg = KnowledgeGraphGNN(
            embedding_dim=32,
            hidden_dim=64,
            num_layers=2,
            gnn_type='gat'
        )
        
        # 添加实体
        self.entities = ["A", "B", "C", "D", "E"]
        for e in self.entities:
            self.kg.add_entity(e, "test")
        
        # 添加关系
        self.triples = [
            ("A", "connects", "B"),
            ("B", "connects", "C"),
            ("C", "connects", "D")
        ]
        
        for h, r, t in self.triples:
            self.kg.add_relation(h, t, r)
        
        self.kg.build_graph()
        
        # 创建补全模型
        self.kg_completion = KnowledgeGraphCompletion(
            self.kg, num_relations=5, prediction_type='mlp'
        )
        
        for h, r, t in self.triples:
            self.kg_completion.register_known_relation(h, r, t)
    
    def test_link_prediction_tail(self):
        """测试尾实体预测"""
        results = self.kg_completion.link_prediction(
            head="A", relation="connects", top_k=3
        )
        
        self.assertIsInstance(results, list)
    
    def test_link_prediction_relation(self):
        """测试关系预测"""
        results = self.kg_completion.link_prediction(
            head="A", tail="C", top_k=3
        )
        
        self.assertIsInstance(results, list)
    
    def test_predict_missing_relations(self):
        """测试缺失关系预测"""
        predictions = self.kg_completion.predict_missing_relations(
            threshold=0.1, top_k=2
        )
        
        self.assertIsInstance(predictions, list)
    
    def test_train_step(self):
        """测试训练步骤"""
        optimizer = torch.optim.Adam(
            list(self.kg.parameters()) + list(self.kg_completion.predictor.parameters()),
            lr=0.001
        )
        
        loss = self.kg_completion.train_step(self.triples, optimizer)
        
        self.assertIsInstance(loss, float)
    
    def test_evaluate(self):
        """测试评估"""
        metrics = self.kg_completion.evaluate(self.triples, k_values=[1, 3])
        
        self.assertIn('mrr', metrics)
        self.assertIn('hit@1', metrics)
        self.assertIn('hit@3', metrics)


class TestIntegration(unittest.TestCase):
    """集成测试"""
    
    def test_full_pipeline(self):
        """测试完整流程"""
        torch.manual_seed(42)
        
        # 1. 创建知识图谱
        kg = KnowledgeGraphGNN(
            embedding_dim=32,
            hidden_dim=64,
            num_layers=2,
            gnn_type='gat'
        )
        
        # 2. 添加实体和关系
        entities = ["Python", "机器学习", "深度学习", "PyTorch", "TensorFlow"]
        for e in entities:
            kg.add_entity(e, "concept")
        
        relations = [
            ("Python", "机器学习", "used_for"),
            ("机器学习", "深度学习", "includes"),
            ("深度学习", "PyTorch", "implemented_by")
        ]
        
        for h, r, t in relations:
            kg.add_relation(h, t, r)
        
        # 3. 构建图
        kg.build_graph()
        
        # 4. 获取嵌入
        embeddings = kg.forward()
        self.assertEqual(embeddings.shape[0], len(entities))
        
        # 5. 创建关系预测器
        kg_completion = KnowledgeGraphCompletion(kg, num_relations=5)
        
        for h, r, t in relations:
            kg_completion.register_known_relation(h, r, t)
        
        # 6. 预测缺失关系
        predictions = kg_completion.predict_missing_relations(threshold=0.1)
        self.assertIsInstance(predictions, list)
        
        # 7. 链接预测
        link_results = kg_completion.link_prediction(
            head="Python", relation="used_for", top_k=3
        )
        self.assertIsInstance(link_results, list)
        
        # 8. 查询
        query_results = kg.query_graph("Python")
        self.assertGreater(len(query_results), 0)
        
        # 9. 相似实体
        similar = kg.find_similar_entities("Python")
        self.assertIsInstance(similar, list)


def run_tests():
    """运行所有测试"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestGraphConstructor))
    suite.addTests(loader.loadTestsFromTestCase(TestGraphSAGELayer))
    suite.addTests(loader.loadTestsFromTestCase(TestGATLayer))
    suite.addTests(loader.loadTestsFromTestCase(TestKnowledgeGraphGNN))
    suite.addTests(loader.loadTestsFromTestCase(TestRelationPredictor))
    suite.addTests(loader.loadTestsFromTestCase(TestKnowledgeGraphCompletion))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


if __name__ == '__main__':
    run_tests()
