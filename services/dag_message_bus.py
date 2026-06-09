#!/usr/bin/env python3
"""
DAG 消息总线 — A2A 异步消息路由

基于 GalaxyOS 现有 DAG 架构: 所有 Agent 间通信通过 DAG 节点传输，
不直接调用。调度器轮询未消费消息，按订阅规则分发。

跟标准 A2A 的区别:
  - Google A2A: request-response, AgentCard 发现
  - GalaxyOS A2A: 消息写入 DAG, 调度器决定谁消费
  - 类似 Erlang Actor Model 但跑在 DAG 上

消息格式:
  {
    "type": "dag_message",
    "subtype": "request" | "response" | "broadcast" | "system",
    "source_agent": "agent_name",
    "target_agent": "agent_name" | "__broadcast__",
    "message_id": "uuid",
    "in_reply_to": "message_id" | None,
    "payload": {...},
    "timestamp": float,
    "ttl_seconds": int,
    "priority": 0 | 1 | 2,
  }

使用 DAG 已有的字段:
  - session_key = "a2a_bus" (统一通信频道)
  - node_type = "a2a_message"
  - blob_id = JSON serialized payload (超出 content 限制时)
  - priority = 消息优先级

Layer: L4 (Agent 通信层)
作者: 小艺 Claw
版本: 1.0.0
创建: 2026-06-09
"""

import json
import os
import time
import uuid
import logging
import threading
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

logger = logging.getLogger("dag_message_bus")

# ============================================================================
# 消息类型
# ============================================================================

class MessageSubtype(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    SYSTEM = "system"

class MessagePriority:
    CRITICAL = 2
    NORMAL = 1
    DEFERRED = 0


# ============================================================================
# 消息容器
# ============================================================================

@dataclass
class DAGMessage:
    """A2A DAG 消息"""
    subtype: str                  # request / response / broadcast / system
    source_agent: str             # 发送者
    target_agent: str             # 接收者 ("__broadcast__" 表示广播)
    payload: Dict[str, Any]       # 消息内容
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    in_reply_to: Optional[str] = None
    ttl_seconds: int = 3600       # 默认 1 小时后自动过期
    priority: int = MessagePriority.NORMAL
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "dag_message",
            "subtype": self.subtype,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "message_id": self.message_id,
            "in_reply_to": self.in_reply_to,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DAGMessage":
        return cls(
            subtype=d.get("subtype", "broadcast"),
            source_agent=d.get("source_agent", "unknown"),
            target_agent=d.get("target_agent", "__broadcast__"),
            payload=d.get("payload", {}),
            message_id=d.get("message_id", str(uuid.uuid4())),
            in_reply_to=d.get("in_reply_to"),
            ttl_seconds=d.get("ttl_seconds", 3600),
            priority=d.get("priority", MessagePriority.NORMAL),
            timestamp=d.get("timestamp", time.time()),
        )

    @property
    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl_seconds

    @property
    def is_broadcast(self) -> bool:
        return self.target_agent == "__broadcast__"

    @property
    def key(self) -> str:
        return f"{self.subtype}:{self.target_agent}:{self.message_id[:8]}"


# ============================================================================
# 订阅管理器
# ============================================================================

class SubscriptionManager:
    """管理 Agent 的订阅关系"""

    def __init__(self):
        # {agent_name: (message_types, filter_fn)}
        self._subscriptions: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def subscribe(
        self,
        agent_name: str,
        message_types: Optional[List[str]] = None,
        filter_fn: Optional[Callable] = None,
    ) -> None:
        """Agent 订阅消息

        Args:
            agent_name: Agent 名字
            message_types: 订阅的消息类型，None 表示所有
            filter_fn: 可选的过滤函数 msg -> bool
        """
        with self._lock:
            self._subscriptions[agent_name] = (message_types, filter_fn)

    def unsubscribe(self, agent_name: str) -> None:
        with self._lock:
            self._subscriptions.pop(agent_name, None)

    def match(self, message: DAGMessage) -> List[str]:
        """返回所有匹配该消息的订阅者"""
        with self._lock:
            matched = []
            for agent, (types, filter_fn) in self._subscriptions.items():
                # 广播消息匹配所有订阅者
                if message.is_broadcast:
                    if types is None or message.subtype in types:
                        if filter_fn is None or filter_fn(message):
                            matched.append(agent)
                # 定向消息只匹配目标
                elif message.target_agent == agent:
                    if types is None or message.subtype in types:
                        if filter_fn is None or filter_fn(message):
                            matched.append(agent)
            return matched

    def list_subscribers(self) -> List[str]:
        with self._lock:
            return list(self._subscriptions.keys())

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_subscribers": len(self._subscriptions),
                "subscribers": list(self._subscriptions.keys()),
            }


# ============================================================================
# DAG 消息总线
# ============================================================================

DEFAULT_A2A_SESSION = "a2a_bus"


class DAGMessageBus:
    """DAG 消息总线 — 基于现有 DAGContextManager 的 A2A 通信

    数据流:
        Agent A 发送消息 → DAG.add_node() → DAG 持久化
        ↓
        调度器轮询未消费消息
        ↓
        DAGMessageBus.match() → 找到订阅者
        ↓
        Agent B 消费 → DAG 标记已消费

    无需独立的消息队列，完全复用 DAG 的存储和压缩机制。

    用法:
        bus = DAGMessageBus(dag_manager)

        # Agent 注册
        bus.register_agent("retriever")
        bus.register_agent("verifier", message_types=["request"])

        # Agent A 发送请求
        msg = bus.send("retriever", "verifier",
            {"query": "记忆检索请求", "top_k": 5})

        # 调度器轮询
        pending = bus.poll("verifier")
        for msg, meta in pending:
            # 处理...
            bus.ack(msg.message_id, "verifier")
    """

    def __init__(self, dag_manager=None):
        self._dag = dag_manager
        self._subscriptions = SubscriptionManager()
        self._consumed: set = set()  # 已消费的 message_id
        self._queues: Dict[str, deque] = defaultdict(deque)  # per-agent 待发送队列
        self._lock = threading.Lock()

    def set_dag_manager(self, dag_manager) -> None:
        self._dag = dag_manager

    # ── Agent 注册 ──────────────────────────────────────────────────

    def register_agent(
        self,
        agent_name: str,
        message_types: Optional[List[str]] = None,
    ) -> None:
        """注册 Agent

        Args:
            agent_name: Agent 唯一名字
            message_types: 订阅的消息类型，None = 全部
        """
        self._subscriptions.subscribe(agent_name, message_types)
        logger.info(f"Agent 注册: {agent_name} (订阅: {message_types or 'all'})")

    def unregister_agent(self, agent_name: str) -> None:
        self._subscriptions.unsubscribe(agent_name)
        with self._lock:
            self._queues.pop(agent_name, None)
        logger.info(f"Agent 注销: {agent_name}")

    # ── 消息发送 ────────────────────────────────────────────────────

    def send(
        self,
        source: str,
        target: str,
        payload: Dict[str, Any],
        *,
        subtype: str = "request",
        in_reply_to: Optional[str] = None,
        ttl: int = 3600,
        priority: int = MessagePriority.NORMAL,
    ) -> DAGMessage:
        """发送消息到 DAG

        Args:
            source: 发送者 Agent 名
            target: 接收者 Agent 名，传 "__broadcast__" 广播
            payload: 消息内容
            subtype: 消息类型
            in_reply_to: 回复的目标 message_id
            ttl: 存活时间(秒)
            priority: 优先级

        Returns:
            DAGMessage（已写入 DAG）
        """
        msg = DAGMessage(
            subtype=subtype,
            source_agent=source,
            target_agent=target,
            payload=payload,
            in_reply_to=in_reply_to,
            ttl_seconds=ttl,
            priority=priority,
        )

        # 写入 DAG
        self._write_to_dag(msg)

        # 广播: 入队到所有订阅者队列
        if msg.is_broadcast:
            with self._lock:
                for agent in self._subscriptions.list_subscribers():
                    if agent != source:
                        self._queues[agent].append(msg)
        # 定向: 入队到目标队列
        else:
            with self._lock:
                self._queues[target].append(msg)

        logger.debug(f"消息发送: {source} → {target} ({subtype})")
        return msg

    def reply(
        self,
        original: DAGMessage,
        source: str,
        payload: Dict[str, Any],
        *,
        subtype: str = "response",
        **kwargs,
    ) -> DAGMessage:
        """回复消息"""
        return self.send(
            source=source,
            target=original.source_agent,
            payload=payload,
            subtype=subtype,
            in_reply_to=original.message_id,
            **kwargs,
        )

    def broadcast(
        self,
        source: str,
        payload: Dict[str, Any],
        *,
        subtype: str = "broadcast",
        **kwargs,
    ) -> DAGMessage:
        """广播消息（所有已注册 Agent 都会收到）"""
        return self.send(
            source=source,
            target="__broadcast__",
            payload=payload,
            subtype=subtype,
            **kwargs,
        )

    # ── 消息消费 ────────────────────────────────────────────────────

    def poll(self, agent_name: str, batch_size: int = 10) -> List[tuple]:
        """轮询 Agent 的未消费消息

        Args:
            agent_name: Agent 名
            batch_size: 最多返回条数

        Returns:
            [(DAGMessage, {"from_dag": bool}), ...]
            优先内存队列（实时），其次 DAG 持久化（容错）
        """
        results = []

        # 1. 内存队列（实时消息）
        with self._lock:
            q = self._queues.get(agent_name)
            while q and len(results) < batch_size:
                msg = q.popleft()
                if not msg.is_expired and msg.message_id not in self._consumed:
                    results.append((msg, {"from_queue": True}))

        # 2. DAG 持久化（Worker 重启后容错）
        if len(results) < batch_size and self._dag is not None:
            try:
                remaining = batch_size - len(results)
                from_dag = self._read_from_dag(agent_name, limit=remaining)
                for msg, meta in from_dag:
                    if msg.message_id not in self._consumed:
                        results.append((msg, meta))
            except Exception as e:
                logger.debug(f"从DAG读取消息失败: {e}")

        return results

    def ack(self, message_id: str, agent_name: str) -> None:
        """确认消费"""
        with self._lock:
            self._consumed.add(message_id)

    def nack(self, message_id: str, agent_name: str) -> None:
        """拒绝消费（重新入队）"""
        # 从 DAG 读到后放回队列
        pass  # 暂不实现，未来可加

    # ── DAG 读写 ─────────────────────────────────────────────────────

    def _write_to_dag(self, msg: DAGMessage) -> None:
        """将消息写入 DAGContextManager"""
        if self._dag is None:
            return  # 无 DAG 时走纯内存模式

        try:
            payload_json = json.dumps(msg.payload, ensure_ascii=False)
            content = json.dumps(msg.to_dict(), ensure_ascii=False)

            # 使用 DAGContextManager.add_node()
            self._dag.add_node(
                node_id=f"a2a_{msg.message_id[:12]}",
                node_type="a2a_message",
                session_key=DEFAULT_A2A_SESSION,
                content=content,
                tokens=len(content) // 4 + 1,
                priority=msg.priority,
                is_summary=False,
                timestamp=msg.timestamp,
                metadata={
                    "subtype": msg.subtype,
                    "source": msg.source_agent,
                    "target": msg.target_agent,
                    "message_id": msg.message_id,
                    "in_reply_to": msg.in_reply_to or "",
                    "consumed": False,
                },
            )
        except Exception as e:
            logger.warning(f"写入 DAG 失败: {e}")

    def _read_from_dag(self, agent_name: str, limit: int = 10) -> List[tuple]:
        """从 DAG 读取未消费的定向消息

        只读取未被 consumed 标记的 request / response 类型消息。
        """
        if self._dag is None:
            return []

        results = []
        try:
            nodes = self._dag.get_session_nodes(DEFAULT_A2A_SESSION)
            a2a_nodes = [
                n for n in nodes
                if n.node_type == "a2a_message"
                and n.metadata is not None
                and not n.metadata.get("consumed", False)
            ]

            for node in a2a_nodes:
                try:
                    data = json.loads(node.content)
                    msg = DAGMessage.from_dict(data)
                    # 只返回给指定 Agent 的消息
                    if msg.target_agent == agent_name or msg.is_broadcast:
                        if msg.message_id not in self._consumed and not msg.is_expired:
                            results.append((msg, {"from_dag": True}))
                            if len(results) >= limit:
                                break
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"从 DAG 读取失败: {e}")

        return results

    # ── 调度器 ──────────────────────────────────────────────────────

    def dispatch_all(self) -> Dict[str, int]:
        """全量调度（供心跳/定时任务调用）

        遍历所有已注册 Agent，检查是否有待消费消息。
        广播消息会投递到每个 Agent 的队列。

        Returns:
            {agent_name: 投递的消息数}
        """
        counts = defaultdict(int)
        with self._lock:
            subscribers = self._subscriptions.list_subscribers()

        for agent in subscribers:
            msgs = self.poll(agent, batch_size=20)
            for msg, _ in msgs:
                counts[agent] += 1

        return dict(counts)

    # ── 统计与调试 ──────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            queue_sizes = {k: len(v) for k, v in self._queues.items()}
            return {
                "subscriptions": self._subscriptions.get_stats(),
                "queue_sizes": queue_sizes,
                "consumed_count": len(self._consumed),
                "total_queued": sum(queue_sizes.values()),
            }


# ============================================================================
# 演示
# ============================================================================

def demo():
    """模拟 A2A 通信流程"""
    bus = DAGMessageBus(dag_manager=None)  # 纯内存模式

    # Agent 注册
    bus.register_agent("user_proxy", message_types=["request", "response"])
    bus.register_agent("retriever", message_types=["request"])
    bus.register_agent("verifier", message_types=["request"])
    bus.register_agent("writer", message_types=["response"])

    print("=== Agent 注册完成 ===")

    # 发送请求
    msg1 = bus.send("user_proxy", "retriever",
        {"query": "Titans 论文核心公式", "top_k": 3},
        subtype="request",
    )
    print(f"user_proxy → retriever: {msg1.message_id[:12]}")

    msg2 = bus.send("retriever", "verifier",
        {"claim": "Titans 使用惊讶度驱动记忆", "sources": ["paper.pdf"]},
        subtype="request",
    )
    print(f"retriever → verifier: {msg2.message_id[:12]}")

    # 广播
    msg3 = bus.broadcast("user_proxy", {"event": "memory_refresh"})
    print(f"广播: user_proxy → __broadcast__: {msg3.message_id[:12]}")

    # 轮询
    print("\n=== 轮询消息 ===")
    for agent in ["retriever", "verifier", "writer"]:
        pending = bus.poll(agent)
        print(f"  {agent}: {len(pending)} 条")
        for msg, meta in pending:
            print(f"    来自 {msg.source_agent}: {msg.subtype} "
                  f"{json.dumps(msg.payload, ensure_ascii=False)[:60]}")
            bus.ack(msg.message_id, agent)

    # 回复
    print("\n=== 回复 ===")
    bus.reply(msg1, "retriever", {"results": ["公式1: surprise = ||M(k)-v||²", "公式2: M_t = (1-α)M_{t-1} + S_t"]})
    pending = bus.poll("user_proxy")
    print(f"user_proxy 收到 {len(pending)} 条回复")

    # 统计
    print("\n=== 统计 ===")
    stats = bus.get_stats()
    print(f"  订阅者: {stats['subscriptions']['total_subscribers']}")
    print(f"  队列: {dict(stats['queue_sizes'])}")
    print(f"  已消费: {stats['consumed_count']}")


def main():
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        demo()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
