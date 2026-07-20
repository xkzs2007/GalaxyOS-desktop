#!/usr/bin/env python3
"""
StreamingLLM Attention Sink 模块

论文参考:
- StreamingLLM: Efficient Streaming Language Models with Attention Sinks (ICLR 2024)
  核心发现: LLM 的注意力机制中，前几个 token 会成为 "Attention Sink"，
  吸引大量注意力权重。保留这些 token 的 KV Cache 即可实现无限长度流式推理，
  无需重新计算整个 KV Cache。

核心思想:
1. Attention Sink: 序列前 4 个 token 吸引大量注意力（即使语义不相关）
2. 流式推理: 保留 Sink tokens + 滑动窗口，实现无限长度生成
3. KV Cache 优化: 只保留 Sink + Window，内存占用恒定

效果:
- 无限长度流式推理，无需重新计算
- 推理速度提升 2-3x（避免频繁的 KV Cache 重计算）
- 内存占用恒定（不随序列长度增长）

实现:
- 在流式推理中自动管理 KV Cache
- 保留 Attention Sink tokens
- 滑动窗口管理
"""

import logging
import time
from typing import Dict, List, Any
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class KVCacheEntry:
    """KV Cache 条目"""
    token_id: int
    key: np.ndarray
    value: np.ndarray
    position: int
    is_sink: bool = False              # 是否是 Attention Sink
    timestamp: float = 0.0


class StreamingKVCache:
    """
    StreamingLLM KV Cache 管理器

    实现 Attention Sink + 滑动窗口策略:
    - 保留前 N 个 Sink tokens（它们吸引大量注意力）
    - 保留最近的 W 个 Window tokens
    - 总 KV Cache 大小 = N + W，恒定不变

    使用示例:
    >>> cache = StreamingKVCache(num_sinks=4, window_size=4092)
    >>> # 推理时不断添加 token
    >>> cache.add_token(token_id=1, key=k, value=v)
    >>> # 缓存自动管理: 保留 sinks + 最近窗口
    >>> info = cache.get_info()
    """

    def __init__(
        self,
        num_sinks: int = 4,           # Attention Sink 数量（论文推荐 4）
        window_size: int = 4092,       # 滑动窗口大小
        num_layers: int = 32,         # Transformer 层数
        num_heads: int = 32,          # 注意力头数
        head_dim: int = 128,          # 头维度
    ):
        self.num_sinks = num_sinks
        self.window_size = window_size
        self.num_layers = num_layers
        self.num_heads = num_heads
        self.head_dim = head_dim

        # KV Cache: layer_id -> List[KVCacheEntry]
        # 惰性初始化，避免预分配大量空列表
        self._cache: Dict[int, List[KVCacheEntry]] = {}

        # 当前位置
        self._current_position = 0

        # 统计
        self.stats = {
            'total_tokens_added': 0,
            'total_evictions': 0,
            'sink_preservations': 0,
            'cache_hits': 0,
            'cache_misses': 0,
        }

    def add_token(
        self,
        token_id: int,
        key: np.ndarray,
        value: np.ndarray,
        layer_id: int = 0,
    ) -> Dict[str, Any]:
        """
        添加一个 token 的 KV Cache

        自动管理: 如果超出窗口，驱逐最旧的非 Sink token

        Args:
            token_id: Token ID
            key: Key 张量
            value: Value 张量
            layer_id: 层 ID

        Returns:
            Dict: 添加结果
        """
        self._current_position += 1
        self.stats['total_tokens_added'] += 1

        # 判断是否是 Sink
        is_sink = self._current_position <= self.num_sinks

        entry = KVCacheEntry(
            token_id=token_id,
            key=key,
            value=value,
            position=self._current_position,
            is_sink=is_sink,
            timestamp=time.time(),
        )

        # 添加到缓存
        if layer_id not in self._cache:
            self._cache[layer_id] = []
        self._cache[layer_id].append(entry)

        # 驱逐: 如果超出窗口
        evicted = 0
        max_size = self.num_sinks + self.window_size
        if len(self._cache[layer_id]) > max_size:
            evicted = self._evict_oldest_non_sink(layer_id)
            self.stats['total_evictions'] += evicted

        if is_sink:
            self.stats['sink_preservations'] += 1

        return {
            'position': self._current_position,
            'is_sink': is_sink,
            'evicted': evicted,
            'cache_size': len(self._cache[layer_id]),
        }

    def add_tokens_batch(
        self,
        token_ids: List[int],
        keys: np.ndarray,
        values: np.ndarray,
        layer_id: int = 0,
    ) -> List[Dict]:
        """
        批量添加 tokens

        Args:
            token_ids: Token ID 列表
            keys: Key 张量 (num_tokens, ...)
            values: Value 张量 (num_tokens, ...)
            layer_id: 层 ID

        Returns:
            添加结果列表
        """
        results = []
        for i, token_id in enumerate(token_ids):
            result = self.add_token(
                token_id=token_id,
                key=keys[i] if i < len(keys) else np.array([]),
                value=values[i] if i < len(values) else np.array([]),
                layer_id=layer_id,
            )
            results.append(result)
        return results

    def _evict_oldest_non_sink(self, layer_id: int) -> int:
        """驱逐最旧的非 Sink token（高效版：批量驱逐）"""
        cache = self._cache[layer_id]
        evicted = 0
        max_size = self.num_sinks + self.window_size
        excess = len(cache) - max_size

        if excess <= 0:
            return 0

        # 收集所有非 Sink 的索引
        non_sink_indices = [i for i, entry in enumerate(cache) if not entry.is_sink]

        # 驱逐最旧的（索引最小的）非 Sink token
        to_remove = non_sink_indices[:excess]
        for idx in reversed(to_remove):
            if idx < len(cache):
                cache.pop(idx)
                evicted += 1

        return evicted

    def get_attention_weights_hint(self, layer_id: int = 0) -> Dict[str, Any]:
        """
        获取注意力权重提示

        用于指导注意力计算: Sink tokens 应始终获得注意力

        Args:
            layer_id: 层 ID

        Returns:
            Dict: 注意力权重提示
        """
        cache = self._cache.get(layer_id, [])
        if not cache:
            return {'sink_positions': [], 'window_positions': [], 'total_cached': 0, 'max_position': self._current_position}

        sink_positions = [e.position for e in cache if e.is_sink]
        window_positions = [e.position for e in cache if not e.is_sink]

        return {
            'sink_positions': sink_positions,
            'window_positions': window_positions,
            'total_cached': len(cache),
            'max_position': self._current_position,
        }

    def get_info(self) -> Dict[str, Any]:
        """获取缓存信息"""
        total_entries = sum(len(v) for v in self._cache.values())
        max_size = self.num_sinks + self.window_size
        num_active_layers = len(self._cache)

        # 估算内存占用
        kv_entry_bytes = 2 * self.num_heads * self.head_dim * 4  # 2 (K+V) * heads * dim * 4bytes
        estimated_memory_mb = total_entries * kv_entry_bytes / (1024 ** 2)

        return {
            'num_sinks': self.num_sinks,
            'window_size': self.window_size,
            'max_cache_size': max_size,
            'current_position': self._current_position,
            'total_cached_entries': total_entries,
            'active_layers': num_active_layers,
            'estimated_memory_mb': round(estimated_memory_mb, 2),
            'utilization': round(total_entries / (max_size * num_active_layers) * 100, 2) if num_active_layers > 0 else 0,
        }

    def clear(self):
        """清空缓存"""
        self._cache.clear()
        self._current_position = 0

    def get_stats(self) -> Dict:
        return dict(self.stats)


class StreamingLLMManager:
    """
    StreamingLLM 管理器

    整合 Attention Sink + 滑动窗口的流式推理管理。

    使用示例:
    >>> manager = StreamingLLMManager()
    >>> # 开始新对话
    >>> manager.start_conversation()
    >>> # 推理时使用
    >>> manager.add_token(1, key, value)
    >>> # 获取 KV Cache 状态
    >>> info = manager.get_cache_info()
    """

    def __init__(
        self,
        num_sinks: int = 4,
        window_size: int = 4092,
        num_layers: int = 32,
        num_heads: int = 32,
        head_dim: int = 128,
    ):
        self.kv_cache = StreamingKVCache(
            num_sinks=num_sinks,
            window_size=window_size,
            num_layers=num_layers,
            num_heads=num_heads,
            head_dim=head_dim,
        )

        self.stats = {
            'conversations': 0,
            'total_tokens': 0,
        }

    def start_conversation(self):
        """开始新对话"""
        self.kv_cache.clear()
        self.stats['conversations'] += 1

    def add_token(
        self,
        token_id: int,
        key: np.ndarray,
        value: np.ndarray,
        layer_id: int = 0,
    ) -> Dict:
        """添加 token"""
        self.stats['total_tokens'] += 1
        return self.kv_cache.add_token(token_id, key, value, layer_id)

    def get_cache_info(self) -> Dict:
        """获取缓存信息"""
        return self.kv_cache.get_info()

    def get_attention_hint(self, layer_id: int = 0) -> Dict:
        """获取注意力权重提示"""
        return self.kv_cache.get_attention_weights_hint(layer_id)

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            'kv_cache_stats': self.kv_cache.get_stats(),
        }


# 导出
__all__ = [
    'StreamingLLMManager',
    'StreamingKVCache',
    'KVCacheEntry',
]


if __name__ == "__main__":
    print("=== StreamingLLM Attention Sink 测试 ===\n")

    # 创建管理器
    manager = StreamingLLMManager(
        num_sinks=4,
        window_size=20,  # 小窗口用于测试
        num_layers=2,
        num_heads=4,
        head_dim=8,
    )

    # 模拟流式推理
    print("1. 模拟流式推理:")
    manager.start_conversation()

    for i in range(30):
        key = np.random.randn(4, 8).astype(np.float32)   # (heads, dim)
        value = np.random.randn(4, 8).astype(np.float32)
        result = manager.add_token(
            token_id=i,
            key=key,
            value=value,
            layer_id=0,
        )
        if i < 6 or i >= 25:
            sink_tag = " [SINK]" if result['is_sink'] else ""
            evict_tag = f" (evicted={result['evicted']})" if result['evicted'] else ""
            print(f"   Token {i}: pos={result['position']}, "
                  f"cache_size={result['cache_size']}{sink_tag}{evict_tag}")

    # 缓存信息
    print("\n2. 缓存信息:")
    info = manager.get_cache_info()
    for k, v in info.items():
        print(f"   {k}: {v}")

    # 注意力提示
    print("\n3. 注意力权重提示:")
    hint = manager.get_attention_hint(layer_id=0)
    print(f"   Sink 位置: {hint['sink_positions']}")
    print(f"   窗口位置: {hint['window_positions'][:5]}... (共 {len(hint['window_positions'])} 个)")

    # 统计
    print(f"\n4. 统计: {manager.get_stats()}")
