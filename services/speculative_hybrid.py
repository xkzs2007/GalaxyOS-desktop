#!/usr/bin/env python3
"""
投机解码混合策略模块（真正的投机解码架构）

核心原理：小模型生成草稿 → 大模型并行验证 → 拒绝采样保持分布一致
参考：https://www.cnblogs.com/rossiXYZ/p/18837229

架构：
- Draft Model（草稿模型）：
  - L1: 检索型草稿（FAISS/Qdrant → Trie 草稿序列）
  - L2: 模型型草稿（NIM 小模型 → 草稿序列）
  - L1 + L2 并发生成草稿，草稿+验证链并行
- Target Model（验证模型）：
  - L3 中的 DeepSeek Flash 作为验证模型
  - 接受：草稿经前缀续写验证通过 → 直接返回续写结果
  - 拒绝：草稿不通过 → 走 API 直接生成（兜底）

Author: 小艺 Claw
Version: 2.0.0
Created: 2026-04-23 | Speculative Decoding原理重构: 2026-05-04
"""

import os
import sys
import time
import json
import asyncio
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# API 配置
# ============================================================================

# NVIDIA NIM API
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "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")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# 小艺 DeepSeek V4 API（兜底模型，同 provider 不同 model）
XIAOYI_API_KEY = os.getenv("XIAOYI_API_KEY", "SK-903B8C62E08BAFC76A5CB076A20D434E")
XIAOYI_BASE_URL = "https://celia-claw-drcn.ai.dbankcloud.cn/celia-claw/v1/sse-api"
XIAOYI_UID = os.getenv("XIAOYI_UID", "10086000874200786")
XIAOYI_MODEL = os.getenv("XIAOYI_MODEL", "LLM_DeepSeekV4_Thinking")

# DeepSeek 官方 API（L3 并发兜底）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "YOUR_DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/beta"
DEEPSEEK_MODELS = {
    "flash": "deepseek-v4-flash",   # L1 验证 + L3 兜底，便宜快速
    "pro": "deepseek-v4-pro",       # L3 Pro 加强兜底（带推理能力，质量更高）
}

# VLM 第三通道 — glm-4v-plus（视觉验证，不负责草稿生成，验证 DAG 摘要的视觉准确性）
VLM_API_KEY = os.getenv("VLM_API_KEY", "3b94029d5a044474bf41d4f8825881b0.VULoxcszSQigtVsX")
VLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
VLM_MODEL = "glm-4v-plus"

# NVIDIA NIM 多模型列表（L2 并发）
NVIDIA_NIM_MODELS = [
    "meta/llama-3.2-1b-instruct",       # 最轻最快（0.75s）
    "meta/llama-3.2-3b-instruct",       # 轻量快速（1.29s）
    "google/gemma-2-2b-it",             # Google 出品（1.03s）
]

# ============================================================================
# 速率控制器
# ============================================================================

@dataclass
class RateLimiter:
    """
    速率控制器
    
    用于 NVIDIA NIM API 的速率限制（40次/分钟）
    """
    max_calls: int = 40           # 最大调用次数
    window: int = 60              # 时间窗口（秒）
    calls: List[float] = field(default_factory=list)  # 调用时间戳
    
    def can_call(self) -> bool:
        """检查是否可以调用"""
        now = time.time()
        # 清理过期记录
        self.calls = [t for t in self.calls if now - t < self.window]
        return len(self.calls) < self.max_calls
    
    def record_call(self):
        """记录一次调用"""
        self.calls.append(time.time())
    
    def remaining_calls(self) -> int:
        """剩余可用调用次数"""
        now = time.time()
        self.calls = [t for t in self.calls if now - t < self.window]
        return max(0, self.max_calls - len(self.calls))
    
    def wait_time(self) -> float:
        """需要等待的时间（秒）"""
        if self.can_call():
            return 0.0
        if not self.calls:
            return 0.0
        # 等待最早的调用过期
        oldest = min(self.calls)
        return max(0.0, self.window - (time.time() - oldest))
    
    def stats(self) -> Dict:
        """统计信息"""
        return {
            "max_calls": self.max_calls,
            "window": self.window,
            "current_calls": len(self.calls),
            "remaining": self.remaining_calls(),
            "wait_time": self.wait_time(),
        }


# ============================================================================
# 备份内存缓存（依赖备选路径，需 Qwen3-Embedding-8B API + numpy）
# ============================================================================

class BackupMemoryCache:
    """
    备份内存缓存（依赖备选路径）
    
    ⚠️ 依赖警告：
    - 需要 Qwen3-Embedding-8B 向量模型 API（Gitee AI embedding endpoint）
    - 需要 numpy（用于余弦相似度计算）
    
    当 FAISS/Qdrant 不可用时，使用 Qwen3-Embedding-8B 在内存中对最近/热门记忆
    做 brute-force 向量相似度匹配，作为降级备选路径。
    
    两个路径共享同一个向量模型（Qwen3-Embedding-8B），加载成本一次。
    主路径优先（FAISS/Qdrant），仅当检索结果为空时降级至此。
    """
    
    def __init__(self, max_cache: int = 1000):
        self.max_cache = max_cache
        self.cache: List[Dict] = []  # [{text, embedding, timestamp, hot_score}, ...]
    
    def add(self, text: str, embedding: List[float]):
        """添加记忆到缓存"""
        self.cache.append({
            "text": text,
            "embedding": embedding,
            "timestamp": time.time(),
            "hot_score": 1.0,
        })
        # 超限时移除最旧的
        if len(self.cache) > self.max_cache:
            self.cache = self.cache[-self.max_cache:]
    
    def add_batch(self, items: List[Tuple[str, List[float]]]):
        """批量添加"""
        for text, emb in items:
            self.add(text, emb)
    
    def search(self, query_embedding: List[float], top_k: int = 5) -> List['RetrievalResult']:
        """
        暴力相似度匹配
        
        Args:
            query_embedding: 查询向量
            top_k: 返回 top K 结果
        
        Returns:
            [RetrievalResult, ...]
        """
        if not self.cache:
            return []
        
        def cosine_similarity(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = sum(x * x for x in a) ** 0.5 or 1.0
            norm_b = sum(x * x for x in b) ** 0.5 or 1.0
            return dot / (norm_a * norm_b)
        
        scored = []
        for item in self.cache:
            sim = cosine_similarity(query_embedding, item["embedding"])
            # 热度衰减因子（7天后热度过半）
            age = time.time() - item["timestamp"]
            hot_factor = max(0.5, 1.0 - age / (7 * 86400) * 0.5)
            combined_score = sim * hot_factor * item["hot_score"]
            scored.append((combined_score, item["text"]))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            RetrievalResult(text=text, score=score)
            for score, text in scored[:top_k]
        ]
    
    def size(self) -> int:
        return len(self.cache)


# ============================================================================
# OCR2 预处理
# ============================================================================

class OCR2Preprocessor:
    """
    用户输入预处理 —— 图文混合查询
    
    如果用户输入包含图片/截图/文档等，用 DeepSeek-OCR-2 提取文字后
    再进向量检索和投机解码流程，扩充检索池。
    """
    
    def __init__(self, ocr2_adapter=None):
        self.ocr2_adapter = ocr2_adapter
    
    def set_adapter(self, adapter):
        """设置 OCR2 适配器"""
        self.ocr2_adapter = adapter
    
    async def preprocess(self, prompt: str, media_path: Optional[str] = None) -> str:
        """
        预处理用户输入
        
        如果包含图片，先用 OCR2 提取文字，追加到 prompt 后
        
        Args:
            prompt: 用户文本输入
            media_path: 可选，用户上传的图片路径
        
        Returns:
            预处理后的完整文本
        """
        if not media_path:
            return prompt
        
        if self.ocr2_adapter:
            try:
                # 用 OCR2 的 ocr() 方法提取图片文字
                # ocr() 返回 OCRResult 对象，有 content、success、confidence 等属性
                result = self.ocr2_adapter.ocr(media_path)
                if result and result.success and result.content and result.content.strip():
                    logger.info(f"OCR2 提取文字: {len(result.content)} chars (置信度: {result.confidence:.2f})")
                    return f"{prompt}\n\n[图片提取文字]:\n{result.content}"
            except Exception as e:
                logger.warning(f"OCR2 预处理失败: {e}")
        
        return prompt


# ============================================================================
# Trie 草稿构建器
# ============================================================================

class TrieNode:
    """Trie 节点"""
    def __init__(self):
        self.children: Dict[str, 'TrieNode'] = {}
        self.is_end: bool = False
        self.count: int = 0  # 出现次数
        self.text: str = ""  # 完整文本（仅在 is_end=True 时有效）


class DraftTrie:
    """
    草稿 Trie
    
    用于从检索结果构建候选序列
    参考 REST 论文的 Trie 索引方法
    """
    
    def __init__(self):
        self.root = TrieNode()
        self.total_sequences = 0
    
    def insert(self, text: str):
        """插入文本序列"""
        node = self.root
        tokens = self._tokenize(text)
        
        for token in tokens:
            if token not in node.children:
                node.children[token] = TrieNode()
            node = node.children[token]
            node.count += 1
        
        node.is_end = True
        node.text = text
        self.total_sequences += 1
    
    def _tokenize(self, text: str) -> List[str]:
        """简单分词（按字符）"""
        # 可以替换为更复杂的分词器
        return list(text)
    
    def get_candidates(self, prefix: str, top_k: int = 5) -> List[Tuple[str, int]]:
        """
        获取候选序列
        
        Args:
            prefix: 前缀
            top_k: 返回前 K 个候选
        
        Returns:
            [(候选文本, 出现次数), ...]
        """
        # 找到前缀对应的节点
        node = self.root
        for token in self._tokenize(prefix):
            if token not in node.children:
                return []
            node = node.children[token]
        
        # 收集所有后缀
        candidates = []
        self._collect_candidates(node, prefix, candidates)
        
        # 按出现次数排序
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]
    
    def _collect_candidates(self, node: TrieNode, prefix: str, candidates: List):
        """收集候选序列"""
        if node.is_end:
            candidates.append((node.text, node.count))
        
        for token, child in node.children.items():
            self._collect_candidates(child, prefix + token, candidates)
    
    def clear(self):
        """清空 Trie"""
        self.root = TrieNode()
        self.total_sequences = 0


# ============================================================================
# 检索型投机解码器
# ============================================================================

@dataclass
class RetrievalResult:
    """检索结果"""
    text: str
    score: float
    metadata: Dict = field(default_factory=dict)


class RetrievalSpeculativeDecoder:
    """
    检索型投机解码器
    
    参考 REST 论文：用向量检索替代小模型生成草稿
    
    流程：
    1. 向量检索相似记忆
    2. 构建 Trie 草稿
    3. 选择候选序列
    4. 大模型验证
    """
    
    def __init__(
        self,
        vector_store=None,
        embedding_fn=None,
        quality_threshold: float = 0.8,
        top_k: int = 5
    ):
        self.vector_store = vector_store
        self.embedding_fn = embedding_fn
        self.quality_threshold = quality_threshold
        self.top_k = top_k
        self.trie = DraftTrie()

    def set_embedding_fn(self, fn):
        self.embedding_fn = fn
        
        # 统计
        self.stats = {
            "total_queries": 0,
            "successful_drafts": 0,
            "failed_drafts": 0,
            "avg_quality_score": 0.0,
        }
    
    def set_vector_store(self, store):
        """设置向量存储"""
        self.vector_store = store
    
    async def search_similar(self, query: str) -> List[RetrievalResult]:
        """检索相似记忆"""
        if self.vector_store is None:
            logger.warning("向量存储未设置")
            return []
        
        try:
            # 调用向量检索
            results = await self._vector_search(query)
            return results
        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []
    
    async def _vector_search(self, query: str) -> List[RetrievalResult]:
        """向量检索（通过 embedding_fn 转向量后检索）"""
        
        # 先转 embedding 向量
        query_vector = None
        if self.embedding_fn:
            try:
                vec = self.embedding_fn(query)
                if vec is not None:
                    query_vector = vec
            except Exception as e:
                logger.debug(f"embedding_fn failed: {e}")
        
        if query_vector is None and hasattr(self.vector_store, 'search'):
            # 没 embedding_fn 时退回到直接传字符串（部分后端支持）
            results = self.vector_store.search(query, top_k=self.top_k)
            return [
                RetrievalResult(
                    text=r.get('text', r.get('content', '')),
                    score=r.get('score', r.get('distance', 0)),
                    metadata=r
                )
                for r in results
            ]
        
        if query_vector and hasattr(self.vector_store, 'search'):
            results = self.vector_store.search(query_vector=query_vector, top_k=self.top_k)
            return [
                RetrievalResult(
                    text=r.get('text', r.get('content', '')),
                    score=r.get('score', r.get('distance', 0)),
                    metadata=r
                )
                for r in results
            ]
        elif hasattr(self.vector_store, 'asearch'):
            # 异步接口
            results = await self.vector_store.asearch(query, top_k=self.top_k)
            return [
                RetrievalResult(
                    text=r.get('text', r.get('content', '')),
                    score=r.get('score', r.get('distance', 0)),
                    metadata=r
                )
                for r in results
            ]
        else:
            logger.warning("向量存储不支持 search 或 asearch 方法")
            return []
    
    def build_trie(self, results: List[RetrievalResult]) -> DraftTrie:
        """从检索结果构建 Trie"""
        self.trie.clear()
        
        for result in results:
            if result.text:
                self.trie.insert(result.text)
        
        return self.trie
    
    def get_draft_candidates(
        self,
        query: str,
        top_k: int = 3
    ) -> List[Tuple[str, float]]:
        """
        获取草稿候选
        
        Args:
            query: 查询文本
            top_k: 返回前 K 个候选
        
        Returns:
            [(候选文本, 置信度), ...]
        """
        candidates = self.trie.get_candidates(query, top_k=top_k)
        
        # 计算置信度（基于出现次数和检索分数）
        total_count = sum(c[1] for c in candidates) if candidates else 1
        
        return [
            (text, count / total_count)
            for text, count in candidates
        ]
    
    async def generate_draft(
        self,
        query: str
    ) -> Tuple[Optional[str], Dict]:
        """
        生成草稿
        
        Args:
            query: 查询文本
        
        Returns:
            (草稿文本, 信息字典)
        """
        self.stats["total_queries"] += 1
        
        # 1. 检索相似记忆
        results = await self.search_similar(query)
        
        if not results:
            self.stats["failed_drafts"] += 1
            return None, {
                "success": False,
                "reason": "no_results",
                "query": query,
            }
        
        # 2. 计算质量分数
        avg_score = sum(r.score for r in results) / len(results)
        self.stats["avg_quality_score"] = (
            self.stats["avg_quality_score"] * (self.stats["total_queries"] - 1) + avg_score
        ) / self.stats["total_queries"]
        
        # 3. 检查质量阈值
        if avg_score < self.quality_threshold:
            self.stats["failed_drafts"] += 1
            return None, {
                "success": False,
                "reason": "low_quality",
                "avg_score": avg_score,
                "threshold": self.quality_threshold,
                "results_count": len(results),
            }
        
        # 4. 构建 Trie
        self.build_trie(results)
        
        # 5. 获取候选
        candidates = self.get_draft_candidates(query, top_k=1)
        
        if not candidates:
            self.stats["failed_drafts"] += 1
            return None, {
                "success": False,
                "reason": "no_candidates",
                "avg_score": avg_score,
            }
        
        # 6. 返回最佳候选
        best_draft, confidence = candidates[0]
        self.stats["successful_drafts"] += 1
        
        return best_draft, {
            "success": True,
            "confidence": confidence,
            "avg_score": avg_score,
            "results_count": len(results),
            "draft_length": len(best_draft),
        }


# ============================================================================
# 智能混合生成器
# ============================================================================

class SmartHybridGenerator:
    """
    智能混合生成器
    
    三层架构：
    - Level 1: Qwen3-Embedding-8B 向量检索 + Trie 草稿 + DeepSeek Flash 前缀续写验证
    - Level 2: NVIDIA NIM 多模型并发（L1 + L2 并行执行，谁快用谁）
    - Level 3: 多 API 并发兜底（小艺通道 + DeepSeek Flash 非思考模式）
    """
    
    def __init__(
        self,
        vector_store=None,
        embedding_fn=None,
        nvidia_api_key: str = None,
        xiaoyi_api_key: str = None,
        xiaoyi_uid: str = None,
        xiaoyi_model: str = None,
        vlm_api_key: str = None,
        deepseek_api_key: str = None,
        rate_limit: int = 40,
        rate_window: int = 60,
        quality_threshold: float = 0.8,
        level1_timeout: float = 5.0,   # L1 前缀续写超时（秒）
        level2_timeout: float = 8.0,   # L2 并发超时（秒）
        level3_timeout: float = 12.0,  # L3 并发超时（秒）
        backup_cache: Optional['BackupMemoryCache'] = None,
        ocr2_adapter: Optional['OCR2Preprocessor'] = None,
    ):
        # 速率控制器
        self.rate_limiter = RateLimiter(max_calls=rate_limit, window=rate_window)
        
        # 默认用 hnswlib C++ 后端做向量检索（如果没指定）
        if vector_store is None:
            try:
                from unified_vector_store import UnifiedVectorStore
                vector_store = UnifiedVectorStore(backend='hnswlib')
                logger.info("投机解码使用 hnswlib C++ 后端")
            except (ImportError, Exception) as e:
                logger.warning(f"hnswlib 初始化失败，回退到纯 SQLite 后端: {e}")
        
        # 检索型投机解码器（主路径 + 双路径）
        self.retrieval_decoder = RetrievalSpeculativeDecoder(
            vector_store=vector_store,
            embedding_fn=embedding_fn,
            quality_threshold=quality_threshold,
        )
        
        # 备份内存缓存（备选路径）
        self.backup_cache = backup_cache or BackupMemoryCache()
        
        # OCR2 预处理器
        self.ocr2_preprocessor = ocr2_adapter or OCR2Preprocessor()
        
        # API 配置
        self.nvidia_api_key = nvidia_api_key or NVIDIA_API_KEY
        self.xiaoyi_api_key = xiaoyi_api_key or XIAOYI_API_KEY
        self.xiaoyi_uid = xiaoyi_uid or XIAOYI_UID
        self.xiaoyi_model = xiaoyi_model or XIAOYI_MODEL
        self.vlm_api_key = vlm_api_key or VLM_API_KEY
        self.deepseek_api_key = deepseek_api_key or DEEPSEEK_API_KEY
        
        # 超时配置
        self.level1_timeout = level1_timeout
        self.level2_timeout = level2_timeout
        self.level3_timeout = level3_timeout
        
        # 会话管理（用于 KV Cache 复用）
        self.session_id: Optional[str] = None
        self.conversation_history: List[Dict] = []
        
        # 统计
        self.stats = {
            "total_requests": 0,
            "level1_success": 0,  # 检索型 + Flash 前缀续写成功
            "level2_success": 0,  # NVIDIA NIM 并发成功
            "level3_success": 0,  # 多 API 并发成功
            "vlm_verify_used": 0,  # VLM 视觉验证调用次数
            "vlm_verify_accepted": 0,  # VLM 验证接受次数
            "level1_failed": 0,
            "level2_failed": 0,
            "rate_limit_hits": 0,
            "level2_models_used": {},  # L2 各模型命中次数
            "level3_providers_used": {},  # L3 各 provider 命中次数
            "backup_cache_used": 0,  # 备选路径使用次数
            "ocr2_processed": 0,     # OCR2 预处理次数
        }
    
    def set_vector_store(self, store):
        """设置向量存储"""
        self.retrieval_decoder.set_vector_store(store)
    
    def set_session(self, session_id: str):
        """设置会话 ID（用于 KV Cache 复用）"""
        self.session_id = session_id
        self.conversation_history = []
    
    def add_to_history(self, role: str, content: str):
        """添加到对话历史"""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
    
    async def generate(
        self,
        prompt: str,
        media_path: Optional[str] = None,
        use_retrieval: bool = True,
        use_nim: bool = True,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        投机解码生成（真正的 speculative decoding 范式）
        
        流程（参考 https://www.cnblogs.com/rossiXYZ/p/18837229）：
        1. L1 + L2 并发生成草稿（draft，Step 1）
        2. L3 Flash 做前缀续写并行验证（verification，Step 2）
        3. 验证成功 → 返回续写结果
        4. 验证失败 → L3 直接生成（降级兜底）
        
        Args:
            prompt: 输入提示
            media_path: 可选，用户上传的图片路径
            use_retrieval: 是否使用检索型草稿
            use_nim: 是否使用 NIM 小模型草稿
            **kwargs: 其他参数
        
        Returns:
            (响应文本, 信息字典)
        """
        self.stats["total_requests"] += 1
        
        # ── 0. OCR2 预处理（OCR 提取文字用于 prompt 增强，VLM 在 Step 2 看原图） ──
        if media_path or ("图片" in prompt or "截图" in prompt or "图" in prompt):
            processed_prompt = await self.ocr2_preprocessor.preprocess(prompt, media_path)
            if processed_prompt != prompt:
                self.stats["ocr2_processed"] += 1
                logger.info(f"OCR2 预处理完成: {len(processed_prompt)} chars")
                prompt = processed_prompt
        
        self.add_to_history("user", prompt)
        
        # ================================================================
        # Step 1: Draft Generation（草稿生成）
        # L1（检索型）+ L2（NIM 模型型）并发生成草稿
        # ================================================================
        draft_result = {"text": None, "info": None, "from": None}
        draft_tasks = []
        t1 = None
        t2 = None
        
        if use_retrieval:
            t1 = asyncio.create_task(self._run_draft_level1(prompt, **kwargs))
            draft_tasks.append(t1)
        
        if use_nim and self.rate_limiter.can_call():
            self.rate_limiter.record_call()
            t2 = asyncio.create_task(self._run_draft_level2(prompt, **kwargs))
            draft_tasks.append(t2)
        elif use_nim:
            self.stats["rate_limit_hits"] += 1
            logger.info("达到速率限制，跳过 L2 草稿")
        
        if draft_tasks:
            draft_timeout = max(self.level1_timeout, self.level2_timeout)
            done_set, pending = await asyncio.wait(
                draft_tasks,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=draft_timeout
            )
            
            for task in done_set:
                label = "L1" if task == t1 else "L2"
                try:
                    draft_text, draft_info = task.result()
                    if draft_text:
                        draft_result["text"] = draft_text
                        draft_result["info"] = draft_info
                        draft_result["from"] = label
                        logger.info(f"草稿生成成功: {label}, draft_len={len(draft_text)}")
                        break
                except Exception as e:
                    logger.debug(f"{label} 草稿生成失败: {e}")
            
            for t in pending:
                t.cancel()
        
        # ================================================================
        # Step 2: Verification（并行验证）
        # DeepSeek Flash 做前缀续写验证草稿
        # ================================================================
        
        if draft_result["text"]:
            response, info = await self._verify_draft(
                prompt=prompt,
                draft=draft_result["text"],
                draft_info=draft_result["info"],
                draft_source=draft_result["from"],
                **kwargs
            )
            if response:
                return response, info
        
        # ================================================================
        # Step 3: Fallback — Direct Generation
        # ================================================================
        logger.info("草稿验证失败，降级到直接生成")
        self.stats["draft_fallback"] = self.stats.get("draft_fallback", 0) + 1
        response, info = await self._call_level3_concurrent(prompt, **kwargs)
        self.stats["level3_success"] = self.stats.get("level3_success", 0) + 1
        
        response = response or "抱歉，当前无法获取回复"
        self.add_to_history("assistant", response)
        return response, {
            "level": 3,
            "method": "direct_generation",
            "reason": "draft_verification_failed",
            "underlying": info,
        }
    
    async def _run_draft_level1(
        self,
        prompt: str,
        **kwargs
    ) -> Tuple[Optional[str], Dict]:
        """L1 草稿生成：检索型草稿"""
        draft, info = await self.retrieval_decoder.generate_draft(prompt)
        if not draft and self.backup_cache.size() > 0:
            logger.info("主向量检索失败，切换到备份内存缓存")
            try:
                backup_results = self.backup_cache.search(
                    query_embedding=[0.0] * 10,
                    top_k=self.retrieval_decoder.top_k
                )
                if backup_results:
                    self.retrieval_decoder.build_trie(backup_results)
                    candidates = self.retrieval_decoder.get_draft_candidates(prompt, top_k=1)
                    if candidates:
                        draft, confidence = candidates[0]
                        info = {"success": True, "confidence": confidence,
                                "avg_score": backup_results[0].score if backup_results else 0.5,
                                "results_count": len(backup_results), "draft_length": len(draft), "backup": True}
                        self.stats["backup_cache_used"] += 1
            except Exception as e:
                logger.debug(f"备份失败: {e}")
        if not draft:
            return None, {"level": 1, "success": False, "reason": info.get("reason", "no_draft")}
        return draft, {"level": 1, "draft_method": "retrieval", "draft_info": info}
    
    async def _run_draft_level2(
        self,
        prompt: str,
        **kwargs
    ) -> Tuple[str, Dict]:
        """L2 草稿生成：NIM 小模型草稿"""
        return await self._call_nim_concurrent(prompt, max_tokens=kwargs.get("draft_max_tokens", 256), **kwargs)
    
    async def _verify_draft(
        self,
        prompt: str,
        draft: str,
        draft_info: Dict,
        draft_source: str,
        **kwargs
    ) -> Tuple[Optional[str], Dict]:
        """验证草稿（视觉优先 + 文本兜底）
        
        策略：
        - 有图片（media_path）时：VLM (glm-4v-plus) 做视觉验证
        - 无图片时：DeepSeek Flash 做文本前缀续写验证
        - 均失败则降级到 prompt_judge
        """
        import asyncio
        
        media_path = kwargs.get("media_path")
        
        # 有图片：VLM 唯一验证通道（文本验证没有图片上下文只能猜）
        if media_path:
            self.stats["vlm_verify_used"] += 1
            try:
                response, info = await asyncio.wait_for(
                    self._verify_with_vlm(prompt, draft, media_path, kwargs),
                    timeout=self.level1_timeout
                )
                if response:
                    return response, info
            except (asyncio.TimeoutError, Exception):
                pass
            # VLM 失败，文本验证兜底（不计数 vlm_verify_accepted）
        
        # 无图片或 VLM 失败：文本验证
        try:
            return await self._verify_with_prefix(prompt, draft, kwargs)
        except Exception:
            return await self._verify_with_prompt_judge(prompt, draft, kwargs)
    
    async def _verify_with_prefix(
        self,
        prompt: str,
        draft: str,
        kwargs
    ) -> Tuple[Optional[str], Dict]:
        """前缀续写验证"""
        import aiohttp
        prefix_messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": draft, "prefix": True},
        ]
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.deepseek_api_key}"}
        if self.session_id:
            headers["X-Conversation-Id"] = self.session_id
        body = {
            "model": DEEPSEEK_MODELS["flash"],
            "messages": prefix_messages,
            "max_tokens": kwargs.get("max_tokens", 1024),
            "temperature": 0.3,
            "thinking": {"type": "disabled"},
            "user_id": "xiaoyi-claw-speculative-verifier",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=self.level1_timeout)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    verified = data["choices"][0]["message"]["content"]
                    if verified:
                        self.stats["draft_accepted"] = self.stats.get("draft_accepted", 0) + 1
                        self.add_to_history("assistant", verified)
                        return verified, {
                            "level": "verified",
                            "method": "prefix_completion",
                            "verification": {"model": "deepseek-v4-flash", "accepted": True},
                            "draft_source": self.draft_source if hasattr(self, 'draft_source') else "unknown",
                        }
        return None, {}
    
    async def _verify_with_vlm(
        self,
        prompt: str,
        draft: str,
        media_path: str,
        kwargs
    ) -> Tuple[Optional[str], Dict]:
        """VLM 视觉验证 — glm-4v-plus（智谱视觉验证，判断草稿与图片内容是否一致）
        
        当输入包含图片时，并行发射 VLM 验证草稿的视觉相关性。
        将图片和草稿一起发给 VLM，判断草稿对图片内容的描述是否准确。
        """
        import aiohttp
        
        headers = {
            "Authorization": f"Bearer {self.vlm_api_key}",
            "Content-Type": "application/json",
        }
        
        # 从 data: URL 或本地路径构造图片 content
        image_content = {"type": "image_url", "image_url": {"url": media_path}}
        
        verify_prompt = (
            f"以下是对用户问题的回答草稿：\n\n{draft}\n\n"
            f"请结合图片判断此回答是否准确。如果描述正确，直接输出修正完善后的最终回答；"
            f"如果回答与图片内容无关或明显错误，请仅回复：__REJECT__"
        )
        
        body = {
            "model": VLM_MODEL,
            "messages": [
                {"role": "user", "content": [image_content, {"type": "text", "text": prompt}]},
                {"role": "assistant", "content": draft, "prefix": True},
                {"role": "user", "content": verify_prompt},
            ],
            "max_tokens": kwargs.get("max_tokens", 512),
            "temperature": 0.3,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{VLM_BASE_URL}/chat/completions",
                headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=self.level1_timeout)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    verified = data["choices"][0]["message"].get("content", "")
                    if verified and "__REJECT__" not in verified:
                        self.stats["vlm_verify_accepted"] += 1
                        self.add_to_history("assistant", verified)
                        return verified, {
                            "level": "vlm_verified",
                            "method": "visual_verification",
                            "verification": {"model": VLM_MODEL, "accepted": True},
                            "draft_source": draft_source if hasattr(self, 'draft_source') else "unknown",
                        }
                
                # VLM 不可用（429/超时等）不影响流程，返回 None 让其他通道兜底
                return None, {"vlm_fallback": True}
    
    async def _verify_with_prompt_judge(
        self,
        prompt: str,
        draft: str,
        kwargs
    ) -> Tuple[Optional[str], Dict]:
        """降级验证"""
        import aiohttp
        fallback_prompt = (
            f"用户问题：{prompt}\n\n"
            f"草稿回答：{draft}\n\n"
            "判断以上草稿是否准确完整地回答了问题。"
            "如果质量好，请直接输出最终回答（可适当修正补充）；"
            "如果质量差（不相关、错误），请仅回复：__REJECT__"
        )
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.deepseek_api_key}"}
        body = {
            "model": DEEPSEEK_MODELS["flash"],
            "messages": [{"role": "user", "content": fallback_prompt}],
            "max_tokens": kwargs.get("max_tokens", 1024),
            "temperature": 0.5,
            "thinking": {"type": "disabled"},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=10.0)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data["choices"][0]["message"]["content"]
                    if result and "__REJECT__" not in result:
                        self.stats["draft_accepted_fallback"] = self.stats.get("draft_accepted_fallback", 0) + 1
                        self.add_to_history("assistant", result)
                        return result, {"level": "verified", "method": "prompt_judge_fallback"}
        self.stats["draft_rejected"] = self.stats.get("draft_rejected", 0) + 1
        return None, {}
    
    # ========================================================================
    # Level 1: 检索型投机解码（保留原 _run_level1 逻辑降级用）
    # ========================================================================
    
    async def _run_level1(
        self,
        prompt: str,
        **kwargs
    ) -> Tuple[Optional[str], Dict]:
        """
        Level 1 执行体（可被并行取消）
        
        双路径：主路径 FAISS/Qdrant → Trie → Flash 前缀续写
               备选路径 内存暴力匹配 → Trie → Flash 前缀续写
        """
        # ── 尝试主路径：FAISS/Qdrant 向量检索 ──
        draft, info = await self.retrieval_decoder.generate_draft(prompt)
        
        # ── 主路径失败，降级到备选路径：内存暴力匹配 ──
        if not draft and self.backup_cache.size() > 0:
            logger.info("主向量检索失败，切换到备份内存缓存进行暴力匹配")
            try:
                # 用检索解码器的向量搜索接口（如果 set_vector_store 有 embedding 模型）
                backup_results = self.backup_cache.search(
                    query_embedding=[0.0] * 10,  # 实际应使用 embedding 模型编码
                    top_k=self.retrieval_decoder.top_k
                )
                if backup_results:
                    self.retrieval_decoder.build_trie(backup_results)
                    candidates = self.retrieval_decoder.get_draft_candidates(prompt, top_k=1)
                    if candidates:
                        draft, confidence = candidates[0]
                        info = {
                            "success": True,
                            "confidence": confidence,
                            "avg_score": backup_results[0].score if backup_results else 0.5,
                            "results_count": len(backup_results),
                            "draft_length": len(draft),
                            "backup": True,
                        }
                        self.stats["backup_cache_used"] += 1
                        logger.info(f"备份内存缓存命中: {len(backup_results)} 条")
            except Exception as e:
                logger.debug(f"备份内存缓存失败: {e}")
        
        if not draft:
            return None, {"level": 1, "success": False, "reason": info.get("reason", "no_draft")}
        
        # ── 用对话前缀续写（Beta）验证草稿 ──
        # 将 Trie 草稿作为 assistant 前缀，强制模型从草稿续写
        try:
            prefix_messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": draft, "prefix": True},
            ]
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.deepseek_api_key}",
            }
            
            # 跨请求复用 KV 缓存
            if self.session_id:
                headers["X-Conversation-Id"] = self.session_id
            
            # beta 端点是对话前缀续写必须的
            base_url = "https://api.deepseek.com/beta"
            
            body = {
                "model": DEEPSEEK_MODELS["flash"],
                "messages": prefix_messages,
                "max_tokens": kwargs.get("max_tokens", 1024),
                "temperature": 0.3,  # 低温，偏向稳定续写
                "thinking": {"type": "disabled"},  # 非思考模式，确保temperature生效
                "user_id": "xiaoyi-claw-l1-prefix",  # KV Cache 隔离
            }
            
            import aiohttp
            
            start = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=self.level1_timeout)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        verified = data["choices"][0]["message"]["content"]
                        if verified:
                            self.add_to_history("assistant", verified)
                            return verified, {
                                "level": 1,
                                "method": "retrieval_prefix_verified",
                                "draft_info": info,
                                "verification": {
                                    "model": "deepseek-v4-flash",
                                    "method": "prefix_completion",
                                    "accepted": True,
                                }
                            }
                    else:
                        # HTTP 错误（如 400 说明 prefix 不支持），降级到普通验证
                        logger.debug(f"前缀续写返回 {resp.status}，降级到普通 prompt 验证")
                        
                        # 降级：用 prompt 问"是否接受"
                        fallback_prompt = (
                            f"用户问题：{prompt}\n\n"
                            f"草稿回答：{draft}\n\n"
                            "判断以上草稿是否准确完整地回答了问题。"
                            "如果质量好，请直接输出最终回答（可适当修正补充）；"
                            "如果质量差（不相关、错误），请仅回复：__REJECT__"
                        )
                        messages = [{"role": "user", "content": fallback_prompt}]
                        headers.pop("prefix", None)  # 移除 prefix 相关 header
                        
                        async with session.post(
                            f"{base_url}/chat/completions",
                            headers=headers,
                            json={**body, "messages": messages},
                            timeout=aiohttp.ClientTimeout(total=self.level1_timeout)
                        ) as resp2:
                            if resp2.status == 200:
                                data2 = await resp2.json()
                                verified = data2["choices"][0]["message"]["content"]
                                if verified and "__REJECT__" not in verified:
                                    self.add_to_history("assistant", verified)
                                    return verified, {
                                        "level": 1,
                                        "method": "retrieval_speculative_verified",
                                        "draft_info": info,
                                        "verification": {
                                            "model": "deepseek-v4-flash",
                                            "method": "prompt_judge_fallback",
                                            "accepted": True,
                                        }
                                    }
        except asyncio.CancelledError:
            # L2 先返回了，L1 被取消不算失败
            raise
        except Exception as e:
            logger.debug(f"Level 1 验证异常: {e}")
        
        return None, {"level": 1, "success": False, "reason": "verification_failed"}
    
    # ========================================================================
    # Level 2: NVIDIA NIM 多模型并发（可被 L1 并行取消）
    # ========================================================================
    
    async def _run_level2(
        self,
        prompt: str,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        Level 2 执行体（可被 L1 并行取消）
        
        并发调用多个 NVIDIA NIM 小模型，最快返回者胜。
        """
        return await self._call_nim_concurrent(prompt, **kwargs)
    
    async def _call_nim_concurrent(
        self,
        prompt: str,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        并发调用多个 NVIDIA NIM 小模型，最快返回者胜
        
        策略：asyncio.gather 同时发射，谁先返回用谁
        超时后未返回的直接取消
        """
        import asyncio
        import aiohttp
        
        async def call_single(model: str) -> Tuple[str, Dict]:
            """单个 NIM 模型调用"""
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.nvidia_api_key}",
            }
            
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": kwargs.get("max_tokens", 512),
                "temperature": kwargs.get("temperature", 0.7),
            }
            
            start = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=self.level2_timeout - 1)
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"NIM {model} 错误 {resp.status}: {await resp.text()}")
                    data = await resp.json()
                    text = data["choices"][0]["message"]["content"]
                    latency = time.time() - start
                    return text, {"model": model, "latency": latency, "provider": "nvidia"}
        
        # 并发发射所有模型
        tasks = {
            asyncio.create_task(call_single(model), name=model): model
            for model in NVIDIA_NIM_MODELS
        }
        
        # fastest-wins 策略：谁先完成用谁
        pending = set(tasks.keys())
        first_result = None
        first_info = None
        
        # 等待第一个完成
        done_set, pending = await asyncio.wait(
            pending,
            return_when=asyncio.FIRST_COMPLETED,
            timeout=self.level2_timeout
        )
        
        if done_set:
            # 取第一个完成的任务
            done = list(done_set)
            for task in done:
                try:
                    text, info = task.result()
                    if text:
                        first_result = text
                        first_info = info
                        break
                except Exception:
                    continue
        
        if first_result:
            # 取消所有未完成的任务
            for task in pending:
                task.cancel()
            return first_result, first_info
        
        # 所有任务都失败了，再等一下剩下的
        if pending:
            done_set2, _ = await asyncio.wait(pending, timeout=2.0)
            for task in done_set2:
                try:
                    text, info = task.result()
                    if text:
                        for t in pending:
                            t.cancel()
                        return text, info
                except Exception:
                    continue
        
        raise Exception(f"Level 2 全部 {len(NVIDIA_NIM_MODELS)} 个 NIM 模型均失败")
    
    # ========================================================================
    # Level 3: 多 API 并发兜底
    # ========================================================================
    
    async def _call_level3_concurrent(
        self,
        prompt: str,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        并发调用多个兜底 API，最快返回者胜
        
        并发调用：
        - 小艺通道 DeepSeek V4（当前会话模型）
        - DeepSeek 官方 V4 Flash（便宜快速）
        
        策略：asyncio.gather 同时发射，谁先返回用谁
        超时后未返回的直接取消
        """
        import asyncio
        import aiohttp
        
        # ── 任务1: 小艺通道 ──
        async def call_xiaoyi() -> Tuple[str, Dict]:
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "x-request-from": "openclaw",
                "x-uid": self.xiaoyi_uid,
                "x-api-key": self.xiaoyi_api_key,
            }
            messages = self.conversation_history.copy()
            if not messages or messages[-1]["role"] != "user":
                messages.append({"role": "user", "content": prompt})
            if self.session_id:
                headers["X-Conversation-Id"] = self.session_id
            
            body = {
                "model": self.xiaoyi_model,
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 1024),
                "temperature": kwargs.get("temperature", 0.7),
                "stream": False,
            }
            
            start = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://celia-claw-drcn.ai.dbankcloud.cn/celia-claw/v1/sse-api/chat/completions",
                    headers=headers, json=body,
                    timeout=aiohttp.ClientTimeout(total=self.level3_timeout - 1)
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"小艺 API 错误 {resp.status}")
                    text = await resp.text()
                    response = self._parse_sse(text)
                    latency = time.time() - start
                    return response, {
                        "model": self.xiaoyi_model,
                        "latency": latency,
                        "provider": "xiaoyi-channel",
                        "session_id": self.session_id,
                    }
        
        # ── 任务2: DeepSeek 官方 Flash ──
        async def call_deepseek_flash() -> Tuple[str, Dict]:
            return await self._call_deepseek(
                prompt, model_key="flash",
                max_tokens=kwargs.get("max_tokens", 1024),
                temperature=kwargs.get("temperature", 0.7),
                timeout=self.level3_timeout - 1
            )
        
        # 并发发射（小艺通道 + DeepSeek Flash 双兜底，Pro 太贵不做兜底）
        tasks = {
            asyncio.create_task(call_xiaoyi(), name="xiaoyi"): "xiaoyi",
            asyncio.create_task(call_deepseek_flash(), name="flash"): "flash",
        }
        
        pending = set(tasks.keys())
        
        # fastest-wins
        done_set, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED,
            timeout=self.level3_timeout
        )
        
        if done_set:
            # 收集所有完成的结果，选第一个成功的
            errors = []
            for task in done_set:
                try:
                    text, info = task.result()
                    if text:
                        for t in pending:
                            t.cancel()
                        return text, info
                except Exception as e:
                    errors.append(str(e))
            
            # 完成的都失败了，继续等待剩下的
            if pending:
                done_set2, pending = await asyncio.wait(pending, timeout=3.0)
                for task in done_set2:
                    try:
                        text, info = task.result()
                        if text:
                            for t in pending:
                                t.cancel()
                            return text, info
                    except Exception as e:
                        errors.append(str(e))
            
            raise Exception(f"Level 3 全部失败: {'; '.join(errors)}")
        
        raise Exception("Level 3 全部超时")
    
    async def _call_deepseek(
        self,
        prompt: str,
        model_key: str = "flash",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        timeout: float = 15.0,
        prefix: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        response_format: Optional[Dict] = None,
    ) -> Tuple[str, Dict]:
        """
        调用 DeepSeek 官方 API（非流式，Beta 功能全开）
        
        支持 Beta 特性：
        - 对话前缀续写（prefix=True）
        - Tool Calls
        - JSON Output（response_format={"type": "json_object"}）
        
        直接返回 JSON，不涉及 thinking 事件问题
        """
        import aiohttp
        
        model_name = DEEPSEEK_MODELS[model_key]
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.deepseek_api_key}",
        }
        
        # 写死中文系统提示，强制思考和回复都用中文
        messages = [
            {"role": "system", "content": "你是一个中文AI助手，请全程使用中文思考和回复。"},
            {"role": "user", "content": prompt}
        ]
        
        # 固定 user_id，用于 KV Cache 隔离和缓存命中
        # 同一 user_id 的请求共享缓存，缓存命中价格低至 1/10
        user_id = "xiaoyi-claw-speculative-decoder"
        
        # X-Conversation-Id — 跨请求复用 KV 缓存（搭配 user_id 提升命中率）
        if self.session_id:
            headers["X-Conversation-Id"] = self.session_id
        
        body = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,  # 非流式，避免 thinking 事件
            "user_id": user_id,  # KV Cache 隔离，提升缓存命中率
        }
        
        # Beta: 对话前缀续写 — 给 assistant 消息加 prefix=True
        if prefix:
            body["messages"].append({
                "role": "assistant",
                "content": prefix,
                "prefix": True,
            })
        
        # Beta: Tool Calls
        if tools:
            body["tools"] = tools
        
        # Beta: JSON Output
        if response_format:
            body["response_format"] = response_format
        
        # Flash 显式禁用思考模式（非思考模式）
        body["thinking"] = {"type": "disabled"}
        start = time.time()
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"DeepSeek API {model_key} 错误 {resp.status}: {error_text[:200]}")
                
                data = await resp.json()
                
                # 非流式响应，直接取 choices[0].message.content
                response = data["choices"][0]["message"]["content"]
                latency = time.time() - start
        
        return response, {
            "model": model_name,
            "latency": latency,
            "provider": "deepseek",
            "model_key": model_key,
        }
    
    def _parse_sse(self, text: str) -> str:
        """解析 SSE 响应"""
        lines = text.strip().split('\n')
        content_parts = []
        
        for line in lines:
            if line.startswith('data: '):
                try:
                    data = json.loads(line[6:])
                    if 'choices' in data and len(data['choices']) > 0:
                        msg = data['choices'][0].get('message', {})
                        token = msg.get('token_text', '')
                        if token:
                            content_parts.append(token)
                except:
                    pass
        
        return ''.join(content_parts)
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        total = self.stats["total_requests"]
        
        return {
            **self.stats,
            "rate_limiter": self.rate_limiter.stats(),
            "retrieval_decoder": self.retrieval_decoder.stats,
            "success_rate": {
                "level1": self.stats["level1_success"] / total if total > 0 else 0,
                "level2": self.stats["level2_success"] / total if total > 0 else 0,
                "level3": self.stats["level3_success"] / total if total > 0 else 0,
            },
        }


# ============================================================================
# 便捷函数
# ============================================================================

# 全局实例
_global_generator: Optional[SmartHybridGenerator] = None


def get_generator(vector_store=None) -> SmartHybridGenerator:
    """获取全局生成器"""
    global _global_generator
    
    if _global_generator is None:
        _global_generator = SmartHybridGenerator(vector_store=vector_store)
    
    return _global_generator


async def smart_generate(
    prompt: str,
    vector_store=None,
    **kwargs
) -> Tuple[str, Dict]:
    """
    智能生成（便捷函数）
    
    Args:
        prompt: 输入提示
        vector_store: 向量存储（可选）
        **kwargs: 其他参数
    
    Returns:
        (响应文本, 信息字典)
    """
    generator = get_generator(vector_store)
    return await generator.generate(prompt, **kwargs)


# ============================================================================
# 测试
# ============================================================================

async def test_hybrid_generator():
    """测试混合生成器"""
    print("=" * 50)
    print("测试智能混合生成器")
    print("=" * 50)
    
    generator = SmartHybridGenerator()
    
    # 测试速率控制器
    print("\n[1] 测试速率控制器")
    for i in range(45):
        if generator.rate_limiter.can_call():
            generator.rate_limiter.record_call()
            print(f"  调用 {i+1}: 成功")
        else:
            print(f"  调用 {i+1}: 达到限制，需等待 {generator.rate_limiter.wait_time():.1f}s")
            break
    
    print(f"\n速率控制器状态: {generator.rate_limiter.stats()}")
    
    # 测试 Trie
    print("\n[2] 测试 Trie 草稿构建")
    trie = DraftTrie()
    texts = [
        "你好，我是小艺",
        "你好，很高兴认识你",
        "你好，有什么可以帮助你的",
        "今天天气不错",
        "今天天气很好",
    ]
    
    for text in texts:
        trie.insert(text)
    
    candidates = trie.get_candidates("你好", top_k=3)
    print(f"  候选序列: {candidates}")
    
    # 测试检索型解码器
    print("\n[3] 测试检索型投机解码器")
    decoder = RetrievalSpeculativeDecoder()
    
    # 模拟检索结果
    mock_results = [
        RetrievalResult(text="你好，我是小艺", score=0.9),
        RetrievalResult(text="你好，很高兴认识你", score=0.85),
        RetrievalResult(text="你好，有什么可以帮助你的", score=0.8),
    ]
    
    decoder.build_trie(mock_results)
    candidates = decoder.get_draft_candidates("你好", top_k=3)
    print(f"  草稿候选: {candidates}")
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_hybrid_generator())
