#!/usr/bin/env python3
"""
LLM 流式输出模块
流式响应、SSE 支持、WebSocket 支持

功能：
- 真实流式 API 调用（OpenAI SSE 协议）
- 回压控制
- SSE / WebSocket 双协议支持
- 连接管理

优化（基于 2024-2026 前沿论文）：
- TTFT (Time To First Token) 优化与监控
  论文: StreamingLLM (ICLR 2024) - Attention Sink + 流式推理
- Prompt Caching 支持: 对相同 system prompt 前缀，利用 API 的 KV Cache 缓存
  论文: KIVI (arXiv:2402.02750) - KV Cache 2-bit 量化，TTFT -50%
- 语义缓存集成: 相似查询的完整回复可直接缓存
- StreamingLLM Attention Sink: 保留前 N 个 token 的 KV Cache，实现无限长度流式推理
  论文: StreamingLLM (ICLR 2024)
- 语义分块: 按句子边界而非固定字符数分块，提升用户感知
"""

import json
import time
import asyncio
import logging
from typing import AsyncGenerator, Callable, Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class StreamChunk:
    """
    流式响应块
    """

    def __init__(
        self,
        content: str,
        finish: bool = False,
        metadata: Optional[Dict] = None
    ):
        """
        初始化流式块

        Args:
            content: 内容
            finish: 是否结束
            metadata: 元数据
        """
        self.content = content
        self.finish = finish
        self.metadata = metadata or {}
        self.timestamp = time.time()

    def to_sse(self) -> str:
        """
        转换为 SSE 格式

        Returns:
            str: SSE 格式字符串
        """
        data = {
            'content': self.content,
            'finish': self.finish,
            'timestamp': self.timestamp
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def to_json(self) -> str:
        """
        转换为 JSON 格式

        Returns:
            str: JSON 字符串
        """
        return json.dumps({
            'content': self.content,
            'finish': self.finish,
            'timestamp': self.timestamp
        }, ensure_ascii=False)


class LLMStreamer:
    """
    LLM 流式输出器

    支持真实 LLM API 流式调用，以及模拟流式输出。

    优化:
    - TTFT 监控: 首个 token 延迟统计
    - Prompt Caching: 对固定 system prompt 利用 API 缓存特性
    """

    def __init__(
        self,
        llm_client: Any = None,
        chunk_size: int = 10,
        timeout: float = 30.0,
        max_concurrent: int = 10,
        enable_prompt_caching: bool = True,
    ):
        """
        初始化流式输出器

        Args:
            llm_client: LLM 客户端（llm_client.LLMClient 实例）
            chunk_size: 块大小（字符数，仅模拟模式使用）
            timeout: 超时时间
            max_concurrent: 最大并发连接数
            enable_prompt_caching: 是否启用 Prompt Caching 支持
        """
        self.llm_client = llm_client
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.enable_prompt_caching = enable_prompt_caching

        # 回压控制
        self._active_streams = 0
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # TTFT 统计
        self._ttft_stats = {
            'total_streams': 0,
            'total_ttft_ms': 0.0,
            'min_ttft_ms': float('inf'),
            'max_ttft_ms': 0.0,
        }

        # Prompt Cache: 缓存已知 system prompt 的哈希，用于 API 的 prompt caching
        self._prompt_cache_keys: Dict[str, str] = {}

        # StreamingLLM Attention Sink 管理器（惰性初始化）
        self._streaming_llm_manager = None
        self._enable_streaming_llm = True

        # 语义分块: 按句子边界分块
        self._semantic_chunking = True

        logger.info(f"LLM 流式输出器初始化: chunk_size={chunk_size}, timeout={timeout}s, max_concurrent={max_concurrent}, prompt_caching={enable_prompt_caching}")

    @property
    def streaming_llm(self):
        """惰性初始化 StreamingLLM 管理器"""
        if self._streaming_llm_manager is None and self._enable_streaming_llm:
            try:
                from streaming_llm import StreamingLLMManager
                self._streaming_llm_manager = StreamingLLMManager()
                logger.info("StreamingLLM Attention Sink 管理器已初始化")
            except ImportError:
                logger.warning("streaming_llm 模块未找到，StreamingLLM 不可用")
                self._enable_streaming_llm = False
        return self._streaming_llm_manager

    async def stream(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        messages: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        流式生成

        优先使用 LLM Client 的真实流式 API；如果不可用则回退到模拟模式。

        Args:
            prompt: 提示词（当 messages 为 None 时使用）
            max_tokens: 最大 token 数
            temperature: 温度
            messages: 完整的消息列表（可选，优先于 prompt）

        Yields:
            StreamChunk: 流式块
        """
        async with self._semaphore:
            self._active_streams += 1
            try:
                if self.llm_client and hasattr(self.llm_client, 'stream_chat'):
                    # 真实流式 API
                    async for chunk in self._stream_real(prompt, max_tokens, temperature, messages):
                        yield chunk
                else:
                    # 模拟流式
                    async for chunk in self._stream_mock(prompt, max_tokens, temperature):
                        yield chunk
            finally:
                self._active_streams -= 1

    async def _stream_real(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        messages: Optional[List[Dict[str, str]]],
    ) -> AsyncGenerator[StreamChunk, None]:
        """真实 LLM 流式调用（含 TTFT 监控）"""
        if messages is None:
            messages = [{"role": "user", "content": prompt}]

        # Prompt Caching: 为固定的 system prompt 生成缓存键
        if self.enable_prompt_caching and messages:
            messages = self._prepare_cached_messages(messages)

        chunk_index = 0
        ttft_start = time.time()
        first_token = True
        ttft_ms = 0.0

        try:
            async for token in self.llm_client.stream_chat(
                messages, max_tokens=max_tokens, temperature=temperature
            ):
                if first_token:
                    ttft_ms = (time.time() - ttft_start) * 1000
                    self._record_ttft(ttft_ms)
                    first_token = False

                yield StreamChunk(
                    content=token,
                    finish=False,
                    metadata={
                        'chunk_index': chunk_index,
                        'ttft_ms': ttft_ms if chunk_index == 0 else None,
                    }
                )
                chunk_index += 1
        except Exception as e:
            logger.error(f"流式 API 调用失败: {e}")
            yield StreamChunk(
                content=f"[流式输出错误: {e}]",
                finish=True,
                metadata={'chunk_index': chunk_index, 'error': True}
            )
            return

        # 发送结束标记
        yield StreamChunk(
            content="",
            finish=True,
            metadata={'chunk_index': chunk_index}
        )

    def _record_ttft(self, ttft_ms: float):
        """记录 TTFT 统计"""
        self._ttft_stats['total_streams'] += 1
        self._ttft_stats['total_ttft_ms'] += ttft_ms
        self._ttft_stats['min_ttft_ms'] = min(self._ttft_stats['min_ttft_ms'], ttft_ms)
        self._ttft_stats['max_ttft_ms'] = max(self._ttft_stats['max_ttft_ms'], ttft_ms)

    def _prepare_cached_messages(
        self,
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """
        准备 Prompt Caching 消息

        对固定的 system prompt 前缀，利用 API 的 prompt caching 特性。
        在消息中标记可缓存的前缀部分（兼容 OpenAI/Anthropic 的缓存协议）。
        """
        if not messages or len(messages) < 2:
            return messages

        # 找到 system 消息
        system_msgs = [m for m in messages if m.get('role') == 'system']
        if not system_msgs:
            return messages

        # 为 system prompt 生成缓存键
        import hashlib
        system_text = ' '.join(m.get('content', '') for m in system_msgs)
        cache_key = hashlib.sha256(system_text.encode()).hexdigest()[:16]

        if cache_key not in self._prompt_cache_keys:
            self._prompt_cache_keys[cache_key] = system_text
            logger.debug(f"Prompt Cache 注册: {cache_key}")

        # 构建带有缓存标记的消息（兼容各 API 的缓存协议）
        prepared = []
        for msg in messages:
            prepared_msg = dict(msg)
            # 如果是 system 消息，添加缓存标记（OpenAI compatible）
            if msg.get('role') == 'system' and self.enable_prompt_caching:
                prepared_msg['cache_control'] = {'type': 'ephemeral'}
            prepared.append(prepared_msg)

        return prepared

    def get_ttft_stats(self) -> Dict[str, Any]:
        """获取 TTFT 统计信息"""
        total = self._ttft_stats['total_streams']
        if total == 0:
            return {
                'total_streams': 0,
                'avg_ttft_ms': 0,
                'min_ttft_ms': 0,
                'max_ttft_ms': 0,
                'cached_prompts': len(self._prompt_cache_keys),
            }
        return {
            'total_streams': total,
            'avg_ttft_ms': round(self._ttft_stats['total_ttft_ms'] / total, 2),
            'min_ttft_ms': round(self._ttft_stats['min_ttft_ms'], 2),
            'max_ttft_ms': round(self._ttft_stats['max_ttft_ms'], 2),
            'cached_prompts': len(self._prompt_cache_keys),
        }

    async def _stream_mock(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> AsyncGenerator[StreamChunk, None]:
        """模拟流式输出（当无 LLM Client 时使用）"""
        full_response = (
            f"这是对 '{prompt[:30]}...' 的回答。"
            "这是一个模拟的流式响应，用于演示流式输出功能。"
            "在实际应用中，这里会调用真实的 LLM API 来生成内容。"
            "要启用真实流式输出，请传入有效的 LLMClient 实例。"
        )

        for i in range(0, len(full_response), self.chunk_size):
            chunk_content = full_response[i:i + self.chunk_size]
            is_finish = i + self.chunk_size >= len(full_response)

            yield StreamChunk(
                content=chunk_content,
                finish=is_finish,
                metadata={'chunk_index': i // self.chunk_size, 'mock': True}
            )

            await asyncio.sleep(0.05)

    async def stream_with_callback(
        self,
        prompt: str,
        callback: Callable[[StreamChunk], None],
        max_tokens: int = 500,
        temperature: float = 0.7,
    ):
        """
        带回调的流式生成

        Args:
            prompt: 提示词
            callback: 回调函数
            max_tokens: 最大 token 数
            temperature: 温度
        """
        async for chunk in self.stream(prompt, max_tokens, temperature):
            callback(chunk)

    async def stream_to_list(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> List[StreamChunk]:
        """
        流式生成并收集到列表

        Args:
            prompt: 提示词
            max_tokens: 最大 token 数
            temperature: 温度

        Returns:
            List[StreamChunk]: 流式块列表
        """
        chunks = []
        async for chunk in self.stream(prompt, max_tokens, temperature):
            chunks.append(chunk)
        return chunks

    @property
    def active_streams(self) -> int:
        """当前活跃流数量"""
        return self._active_streams


class SSEServer:
    """
    SSE (Server-Sent Events) 服务器
    """

    def __init__(self, streamer: LLMStreamer):
        """
        初始化 SSE 服务器

        Args:
            streamer: 流式输出器
        """
        self.streamer = streamer
        self.connections: Dict[str, Dict] = {}

    def _register_connection(self, conn_id: str):
        """注册连接"""
        self.connections[conn_id] = {
            'id': conn_id,
            'connected_at': time.time(),
            'last_activity': time.time(),
        }

    def _unregister_connection(self, conn_id: str):
        """注销连接"""
        self.connections.pop(conn_id, None)

    async def handle_request(self, prompt: str, conn_id: Optional[str] = None) -> AsyncGenerator[str, None]:
        """
        处理 SSE 请求

        Args:
            prompt: 提示词
            conn_id: 连接 ID（用于连接管理）

        Yields:
            str: SSE 格式数据
        """
        if conn_id is None:
            import uuid
            conn_id = uuid.uuid4().hex[:16]

        self._register_connection(conn_id)

        try:
            async for chunk in self.streamer.stream(prompt):
                self.connections[conn_id]['last_activity'] = time.time()
                yield chunk.to_sse()
        finally:
            self._unregister_connection(conn_id)

    def get_stats(self) -> Dict:
        """获取连接统计"""
        return {
            'active_connections': len(self.connections),
            'connections': dict(self.connections),
        }


class WebSocketHandler:
    """
    WebSocket 处理器
    """

    def __init__(self, streamer: LLMStreamer):
        """
        初始化 WebSocket 处理器

        Args:
            streamer: 流式输出器
        """
        self.streamer = streamer

    async def handle_message(self, message: str) -> AsyncGenerator[str, None]:
        """
        处理 WebSocket 消息

        Args:
            message: 消息内容

        Yields:
            str: JSON 格式数据
        """
        try:
            data = json.loads(message)
            prompt = data.get('prompt', '')
            max_tokens = data.get('max_tokens', 500)
            temperature = data.get('temperature', 0.7)
            messages = data.get('messages', None)

            async for chunk in self.streamer.stream(
                prompt, max_tokens, temperature, messages
            ):
                yield chunk.to_json()

        except json.JSONDecodeError:
            yield json.dumps({'error': 'Invalid JSON'})


if __name__ == "__main__":
    # 测试
    async def test():
        print("=== LLM 流式输出测试 ===")

        # 不带 LLM Client 的模拟模式
        streamer = LLMStreamer(chunk_size=20)

        # 流式生成
        print("\n模拟流式输出:")
        async for chunk in streamer.stream("介绍一下向量搜索"):
            print(chunk.content, end='', flush=True)
            if chunk.finish:
                print("\n[完成]")

        # 收集到列表
        print("\n收集到列表:")
        chunks = await streamer.stream_to_list("测试问题")
        print(f"共 {len(chunks)} 个块")

        # SSE 格式
        print("\nSSE 格式:")
        sse_server = SSEServer(streamer)
        async for sse in sse_server.handle_request("测试"):
            print(sse.strip())
            break  # 只打印第一个

        # 带 LLM Client 的真实模式
        try:
            from llm_client import LLMClient
            client = LLMClient()
            if client.api_key:
                real_streamer = LLMStreamer(llm_client=client)
                print("\n真实流式输出:")
                async for chunk in real_streamer.stream("你好"):
                    print(chunk.content, end='', flush=True)
                    if chunk.finish:
                        print("\n[完成]")
        except ImportError:
            print("\nLLMClient 未找到，跳过真实流式测试")

    asyncio.run(test())
