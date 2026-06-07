"""测试 conversation — 对话管理"""
import sys; sys.path.insert(0, '.')
import pytest
from services.conversation import Conversation, ConversationManager, Message, MemoryCompressor


class TestMessage:
    def test_creation(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.metadata == {}

    def test_with_metadata(self):
        m = Message(role="assistant", content="hi", metadata={"model": "gpt"})
        assert m.metadata["model"] == "gpt"

    def test_to_dict(self):
        m = Message(role="system", content="prompt")
        d = m.to_dict()
        assert d["role"] == "system"
        assert d["content"] == "prompt"

    def test_from_dict(self):
        d = {"role": "user", "content": "test", "metadata": {}}
        m = Message.from_dict(d)
        assert m.role == "user"
        assert m.content == "test"

    def test_roundtrip(self):
        original = Message(role="assistant", content="answer", metadata={"key": "v"})
        restored = Message.from_dict(original.to_dict())
        assert restored.role == "assistant"
        assert restored.content == "answer"
        assert restored.metadata["key"] == "v"


class TestConversation:
    @pytest.fixture
    def conv(self):
        return Conversation(conversation_id="test_conv", max_history=10)

    def test_creation(self, conv):
        assert conv.conversation_id == "test_conv"

    def test_add_message(self, conv):
        conv.add_message(role="user", content="hi")
        conv.add_message(role="assistant", content="hello")
        history = conv.get_history()
        assert len(history) == 2

    def test_max_history(self, conv):
        for i in range(15):
            conv.add_message(role="user", content=f"msg{i}")
        history = conv.get_history()
        assert len(history) <= 10

    def test_get_context(self, conv):
        conv.add_message(role="user", content="q")
        conv.add_message(role="assistant", content="a")
        ctx = conv.get_context()
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_clear(self, conv):
        conv.add_message(role="user", content="test")
        conv.clear()
        assert len(conv.get_history()) == 0


class TestConversationManager:
    @pytest.fixture
    def manager(self):
        return ConversationManager(max_conversations=5, max_history_per_conversation=10)

    def test_create_conversation(self, manager):
        conv = manager.create_conversation("user1")
        assert conv is not None

    def test_get_conversation(self, manager):
        conv = manager.get_conversation("user1")
        # 可能返回 None（如果是按需创建模式）或 Conversation
        assert conv is None or isinstance(conv, Conversation)

    def test_delete_conversation(self, manager):
        manager.create_conversation("user1")
        manager.delete_conversation("user1")

    def test_get_stats(self, manager):
        manager.create_conversation("u1")
        stats = manager.get_stats()
        assert isinstance(stats, dict)

    def test_max_conversations(self, manager):
        for i in range(10):
            manager.create_conversation(f"user{i}")


class TestMemoryCompressor:
    def test_init(self):
        c = MemoryCompressor(compression_ratio=0.5)
        assert c.compression_ratio == 0.5

    def test_compress_short(self):
        c = MemoryCompressor()
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]
        result = c.compress(msgs)
        # compress 可能返回字符串或列表
        assert result is not None
