#!/usr/bin/env python3
"""
多轮对话模块
对话历史管理、上下文窗口、会话管理

功能：
- 多会话管理
- 真实 Token 计数（tiktoken）
- LLM 语义摘要压缩
- SQLite 持久化
- 线程安全
"""

import json
import logging
import sqlite3
import threading
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path
import time

logger = logging.getLogger(__name__)

# Token 计数器：优先使用 tiktoken，不可用时回退到估算
_token_encoder = None
_tokenizer_available = False

try:
    import tiktoken
    _token_encoder = tiktoken.get_encoding("cl100k_base")
    _tokenizer_available = True
except ImportError:
    _tokenizer_available = False


def count_tokens(text: str) -> int:
    """
    计算 token 数量

    优先使用 tiktoken 精确计算，不可用时回退到字符估算。

    Args:
        text: 文本内容

    Returns:
        int: token 数量
    """
    if _tokenizer_available and _token_encoder is not None:
        try:
            return len(_token_encoder.encode(text))
        except Exception:
            pass

    # 回退：中文约 1.5 字符/token，英文约 4 字符/token
    # 混合估算：取 2.5 字符/token
    return max(1, len(text) // 2)


class Message:
    """
    消息定义
    """

    def __init__(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ):
        """
        初始化消息

        Args:
            role: 角色（user/assistant/system）
            content: 内容
            metadata: 元数据
        """
        self.role = role
        self.content = content
        self.metadata = metadata or {}
        self.timestamp = time.time()
        self.message_id = uuid.uuid4().hex[:16]

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'role': self.role,
            'content': self.content,
            'timestamp': self.timestamp,
            'message_id': self.message_id,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Message':
        """从字典创建"""
        msg = cls(data['role'], data['content'], data.get('metadata'))
        msg.timestamp = data.get('timestamp', time.time())
        msg.message_id = data.get('message_id', uuid.uuid4().hex[:16])
        return msg


class Conversation:
    """
    对话定义
    """

    def __init__(
        self,
        conversation_id: Optional[str] = None,
        max_history: int = 50,
        context_window: int = 4096
    ):
        """
        初始化对话

        Args:
            conversation_id: 对话 ID
            max_history: 最大历史消息数
            context_window: 上下文窗口大小（token）
        """
        self.conversation_id = conversation_id or uuid.uuid4().hex[:16]
        self.max_history = max_history
        self.context_window = context_window

        # 消息历史
        self.messages: List[Message] = []

        # 对话元数据
        self.metadata = {
            'created_at': time.time(),
            'updated_at': time.time(),
            'message_count': 0
        }

        # 线程锁
        self._lock = threading.Lock()

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None) -> Message:
        """
        添加消息

        Args:
            role: 角色
            content: 内容
            metadata: 元数据

        Returns:
            Message: 消息对象
        """
        message = Message(role, content, metadata)

        with self._lock:
            self.messages.append(message)

            # 限制历史长度
            if len(self.messages) > self.max_history:
                self.messages = self.messages[-self.max_history:]

            # 更新元数据
            self.metadata['updated_at'] = time.time()
            self.metadata['message_count'] += 1

        return message

    def get_history(self, limit: Optional[int] = None) -> List[Dict]:
        """
        获取历史消息

        Args:
            limit: 返回数量

        Returns:
            List[Dict]: 消息列表
        """
        with self._lock:
            messages = self.messages[-limit:] if limit else self.messages
            return [m.to_dict() for m in messages]

    def get_context(self, max_tokens: Optional[int] = None) -> str:
        """
        获取上下文（基于真实 token 计数）

        Args:
            max_tokens: 最大 token 数

        Returns:
            str: 上下文文本
        """
        max_tokens = max_tokens if max_tokens is not None else self.context_window

        with self._lock:
            context_parts = []
            current_tokens = 0

            for message in reversed(self.messages):
                tokens = count_tokens(message.content)

                if current_tokens + tokens > max_tokens:
                    break

                context_parts.insert(0, f"{message.role}: {message.content}")
                current_tokens += tokens

        return "\n".join(context_parts)

    def clear(self):
        """清空对话"""
        with self._lock:
            self.messages = []
            self.metadata['updated_at'] = time.time()
            self.metadata['message_count'] = 0


class ConversationManager:
    """
    对话管理器

    支持内存和 SQLite 持久化存储。
    """

    def __init__(
        self,
        max_conversations: int = 100,
        max_history_per_conversation: int = 50,
        persist_path: Optional[str] = None,
    ):
        """
        初始化对话管理器

        Args:
            max_conversations: 最大对话数
            max_history_per_conversation: 每个对话的最大历史数
            persist_path: SQLite 持久化路径（None 则仅内存）
        """
        self.max_conversations = max_conversations
        self.max_history = max_history_per_conversation
        self.persist_path = persist_path

        # 对话存储
        self.conversations: Dict[str, Conversation] = {}

        # 用户-对话映射
        self.user_conversations: Dict[str, str] = {}

        # 线程锁
        self._lock = threading.Lock()

        # 初始化持久化
        if persist_path:
            self._init_db()
            self._load_from_db()

        logger.info(f"对话管理器初始化: max_conversations={max_conversations}, persist={persist_path is not None}")

    def _init_db(self):
        """初始化 SQLite 数据库"""
        try:
            db_path = Path(self.persist_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    created_at REAL,
                    updated_at REAL,
                    metadata TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    role TEXT,
                    content TEXT,
                    timestamp REAL,
                    metadata TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                )
            """)

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")

    def _load_from_db(self):
        """从数据库加载对话"""
        try:
            conn = sqlite3.connect(str(self.persist_path))
            cursor = conn.cursor()

            cursor.execute("SELECT conversation_id, user_id, metadata FROM conversations ORDER BY updated_at DESC")
            rows = cursor.fetchall()

            for conv_id, user_id, metadata_str in rows:
                conv = Conversation(
                    conversation_id=conv_id,
                    max_history=self.max_history,
                )

                # 加载消息
                cursor.execute(
                    "SELECT role, content, timestamp, message_id, metadata "
                    "FROM messages WHERE conversation_id = ? "
                    "ORDER BY timestamp",

                    (conv_id,)
                )
                msg_rows = cursor.fetchall()
                for role, content, timestamp, msg_id, msg_metadata in msg_rows:
                    msg = Message(role, content)
                    msg.timestamp = timestamp
                    msg.message_id = msg_id
                    msg.metadata = json.loads(msg_metadata) if msg_metadata else {}
                    conv.messages.append(msg)

                conv.metadata['message_count'] = len(msg_rows)
                self.conversations[conv_id] = conv

                if user_id:
                    self.user_conversations[user_id] = conv_id

            conn.close()
            logger.info(f"从数据库加载了 {len(self.conversations)} 个对话")

        except Exception as e:
            logger.error(f"从数据库加载对话失败: {e}")

    def _persist_message(self, conversation_id: str, message: Message, user_id: Optional[str] = None):
        """持久化单条消息"""
        if not self.persist_path:
            return

        try:
            conn = sqlite3.connect(str(self.persist_path))
            cursor = conn.cursor()

            # 确保对话记录存在
            cursor.execute(
                "INSERT OR IGNORE INTO conversations "
                "(conversation_id, user_id, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, user_id or '', time.time(), time.time(), '{}')
            )

            # 插入消息
            cursor.execute(
                "INSERT INTO messages "
                "(message_id, conversation_id, role, content, timestamp, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message.message_id,
                 conversation_id,
                 message.role,
                 message.content,
                 message.timestamp,
                 json.dumps(
                     message.metadata)))

            # 更新对话时间
            cursor.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (time.time(), conversation_id)
            )

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"消息持久化失败: {e}")

    def create_conversation(
        self,
        user_id: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Conversation:
        """
        创建新对话

        Args:
            user_id: 用户 ID
            system_prompt: 系统提示

        Returns:
            Conversation: 对话对象
        """
        with self._lock:
            conversation = Conversation(max_history=self.max_history)

            # 添加系统提示
            if system_prompt:
                msg = conversation.add_message('system', system_prompt)
                self._persist_message(conversation.conversation_id, msg, user_id)

            # 存储
            self.conversations[conversation.conversation_id] = conversation

            # 用户映射
            if user_id:
                self.user_conversations[user_id] = conversation.conversation_id

            # 清理旧对话
            if len(self.conversations) > self.max_conversations:
                self._cleanup_old_conversations()

        return conversation

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """
        获取对话

        Args:
            conversation_id: 对话 ID

        Returns:
            Optional[Conversation]: 对话对象
        """
        return self.conversations.get(conversation_id)

    def get_user_conversation(self, user_id: str) -> Optional[Conversation]:
        """
        获取用户对话

        Args:
            user_id: 用户 ID

        Returns:
            Optional[Conversation]: 对话对象
        """
        conversation_id = self.user_conversations.get(user_id)
        if conversation_id:
            return self.conversations.get(conversation_id)
        return None

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除对话

        Args:
            conversation_id: 对话 ID

        Returns:
            bool: 是否成功
        """
        with self._lock:
            if conversation_id in self.conversations:
                del self.conversations[conversation_id]

                # 清理用户映射
                for user_id, conv_id in list(self.user_conversations.items()):
                    if conv_id == conversation_id:
                        del self.user_conversations[user_id]

                # 从数据库删除
                if self.persist_path:
                    try:
                        conn = sqlite3.connect(str(self.persist_path))
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
                        cursor.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        logger.error(f"对话删除失败: {e}")

                return True
        return False

    def _cleanup_old_conversations(self):
        """清理旧对话（调用方已持有 self._lock）"""
        sorted_convs = sorted(
            self.conversations.items(),
            key=lambda x: x[1].metadata['updated_at']
        )

        to_remove = sorted_convs[:len(self.conversations) - self.max_conversations]
        for conv_id, _ in to_remove:
            # 直接删除，不再调 delete_conversation（避免死锁）
            if conv_id in self.conversations:
                del self.conversations[conv_id]
            for user_id, cid in list(self.user_conversations.items()):
                if cid == conv_id:
                    del self.user_conversations[user_id]
            # 从数据库删除
            if self.persist_path:
                try:
                    conn = sqlite3.connect(str(self.persist_path))
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
                    cursor.execute("DELETE FROM conversations WHERE conversation_id = ?", (conv_id,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"对话删除失败: {e}")

    def get_stats(self) -> Dict:
        """
        获取统计信息

        Returns:
            Dict: 统计信息
        """
        total_messages = sum(
            conv.metadata['message_count']
            for conv in self.conversations.values()
        )

        return {
            'total_conversations': len(self.conversations),
            'total_messages': total_messages,
            'active_users': len(self.user_conversations),
            'persist_enabled': self.persist_path is not None,
            'tokenizer': 'tiktoken' if _tokenizer_available else 'estimated',
        }


class MemoryCompressor:
    """
    记忆压缩器

    优先使用 LLM 生成语义摘要，不可用时回退到关键词提取。
    """

    def __init__(
        self,
        compression_ratio: float = 0.5,
        llm_client: Optional[Any] = None,
    ):
        """
        初始化记忆压缩器

        Args:
            compression_ratio: 压缩比例
            llm_client: LLM 客户端
        """
        self.compression_ratio = compression_ratio
        self.llm_client = llm_client

    def compress(self, messages: List[Message]) -> str:
        """
        压缩消息

        优先使用 LLM 生成语义摘要，不可用时回退到关键词提取。

        Args:
            messages: 消息列表

        Returns:
            str: 压缩后的摘要
        """
        if not messages:
            return ""

        # 尝试 LLM 压缩
        if self.llm_client is not None:
            try:
                llm_summary = self._compress_with_llm(messages)
                if llm_summary:
                    return llm_summary
            except Exception as e:
                logger.warning(f"LLM 压缩失败，回退到关键词提取: {e}")

        # 回退：关键词提取
        return self._compress_with_keywords(messages)

    def _compress_with_llm(self, messages: List[Message]) -> Optional[str]:
        """使用 LLM 生成语义摘要"""
        if not hasattr(self.llm_client, 'chat'):
            return None

        # 构建对话文本
        conversation_text = "\n".join(
            f"{m.role}: {m.content}" for m in messages
        )

        prompt = (
            f"请总结以下对话的关键信息，保留重要细节：\n\n"
            f"{conversation_text}\n\n"
            "要求：\n"
            "- 保留用户的核心需求和问题\n"
            "- 保留助力的关键回答和结论\n"
            "- 省略寒暄和重复内容\n"
            "- 控制在 200 字以内"
        )

        response = self.llm_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )

        return response.strip() if response else None

    def _compress_with_keywords(self, messages: List[Message]) -> str:
        """关键词提取压缩"""
        key_points = []

        for msg in messages:
            if msg.role == 'user':
                key_points.append(f"用户问: {msg.content[:100]}...")
            elif msg.role == 'assistant':
                key_points.append(f"回答: {msg.content[:100]}...")

        return "\n".join(key_points)


if __name__ == "__main__":
    # 测试
    print("=== 多轮对话测试 ===")

    manager = ConversationManager()

    # 创建对话
    conv = manager.create_conversation(user_id="user_001", system_prompt="你是一个助手")

    # 添加消息
    conv.add_message("user", "你好")
    conv.add_message("assistant", "你好！有什么可以帮助你的？")
    conv.add_message("user", "介绍一下向量搜索")
    conv.add_message("assistant", "向量搜索是一种基于语义相似度的搜索方法...")

    # 获取历史
    history = conv.get_history()
    print(f"历史消息: {len(history)} 条")

    # 获取上下文
    context = conv.get_context(max_tokens=500)
    print(f"上下文长度: {len(context)} 字符")

    # Token 计数测试
    test_text = "这是一段测试文本，用于验证 token 计数功能。"
    tokens = count_tokens(test_text)
    print(
        f"Token 计数: '{test_text}' -> {tokens} tokens "
        f"(tokenizer: {'tiktoken' if _tokenizer_available else 'estimated'})")

    # 统计
    stats = manager.get_stats()
    print(f"统计: {stats}")

    # 记忆压缩测试
    compressor = MemoryCompressor()
    summary = compressor.compress(conv.messages)
    print(f"\n压缩摘要:\n{summary}")
