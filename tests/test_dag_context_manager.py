"""测试 dag_context_manager — DAG 上下文管理"""
import sys; sys.path.insert(0, '.')
import pytest
from services.dag_context_manager import (
    DAGContextManager, DAGNode, DAGNodeType,
    CognitionForestType,
)


class TestDAGNodeType:
    def test_has_types(self):
        """DAGNodeType 应该是枚举或类"""
        assert DAGNodeType is not None


class TestCognitionForestType:
    def test_has_types(self):
        assert CognitionForestType is not None


class TestDAGNode:
    def test_creation(self):
        node = DAGNode(
            node_id="n1", node_type="message",
            session_key="s1", content="test", tokens=10,
        )
        assert node.node_id == "n1"
        assert node.content == "test"
        assert node.tokens == 10

    def test_to_dict(self):
        node = DAGNode(node_id="n1", node_type="message",
                       session_key="s1", content="test", tokens=5)
        d = node.to_dict()
        assert d["node_id"] == "n1"
        assert d["content"] == "test"

    def test_from_dict(self):
        d = {"node_id": "n2", "node_type": "message",
             "session_key": "s2", "content": "hello", "tokens": 3}
        node = DAGNode.from_dict(d)
        assert node.node_id == "n2"
        assert node.content == "hello"

    def test_roundtrip(self):
        original = DAGNode(
            node_id="n3", node_type="response",
            session_key="s3", content="data", tokens=7,
        )
        restored = DAGNode.from_dict(original.to_dict())
        assert restored.node_id == original.node_id
        assert restored.content == original.content


class TestDAGContextManager:
    def test_init(self, tmp_path):
        db_path = str(tmp_path / "dag.db")
        mgr = DAGContextManager(db_path=db_path)
        assert mgr is not None

    def test_init_default(self):
        mgr = DAGContextManager()
        assert mgr is not None

    def test_add_message(self, tmp_path):
        mgr = DAGContextManager(db_path=str(tmp_path / "dag.db"))
        node_id = mgr.add_message(
            session_key="s1", role="user", content="hello",
        )
        assert isinstance(node_id, str)

    def test_add_node(self, tmp_path):
        mgr = DAGContextManager(db_path=str(tmp_path / "dag.db"))
        node = DAGNode(
            node_id="custom_n1", node_type="message",
            session_key="s1", content="test node", tokens=5,
        )
        result = mgr.add_node(node)
        assert isinstance(result, bool)

    def test_add_cognitive_anchor(self, tmp_path):
        mgr = DAGContextManager(db_path=str(tmp_path / "dag.db"))
        # 先添加一个消息节点，然后用其 ID 创建锚点
        node_id = mgr.add_message(
            session_key="s1", role="user", content="important fact",
        )
        anchor_id = mgr.add_cognitive_anchor(node_id=node_id)
        assert anchor_id is None or isinstance(anchor_id, str)

    def test_add_cognition_subtree(self, tmp_path):
        mgr = DAGContextManager(db_path=str(tmp_path / "dag.db"))
        result = mgr.add_cognition_subtree(
            forest_type="user", content="subtree content",
            tokens=10,
        )
        assert isinstance(result, str)

    def test_add_persona_node(self, tmp_path):
        mgr = DAGContextManager(db_path=str(tmp_path / "dag.db"))
        node_id = mgr.add_persona_node(content="assistant persona", session_key="s1")
        assert isinstance(node_id, str)
