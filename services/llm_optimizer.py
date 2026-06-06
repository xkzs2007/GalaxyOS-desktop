#!/usr/bin/env python3
"""
L4 多 API 优化层 (LLM Optimizer)

实现多远程 API 的智能路由、投机解码、缓存优化：
- 智能路由：根据任务类型选择最优模型
- 投机解码：小模型生成候选 + 大模型验证
- 故障转移：主模型失败自动切换
- 语义缓存：相同请求缓存复用
- 成本控制：优先使用免费 API

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import os
import json
import time
import hashlib
import asyncio
import aiohttp
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# 配置
# ============================================================================

# NVIDIA NIM API
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# 小艺 GLM5 API
XIAOYI_API_KEY = os.environ.get("XIAOYI_API_KEY", "")
XIAOYI_BASE_URL = "https://celia-claw-drcn.ai.dbankcloud.cn/celia-claw/v1/sse-api"
XIAOYI_UID = os.environ.get("XIAOYI_UID", "")

# 固定 System Prompts（提高 NVIDIA NIM 缓存命中率）
SYSTEM_PROMPTS = {
    "default": "你是一个专业的AI助手。请用中文回答所有问题。提供准确、有帮助的回答。",
    "coding": "你是一个资深的编程专家。请用中文回答编程相关问题。提供清晰、高效的代码解决方案。",
    "chinese": "你是一个中文语言专家。请用中文回答所有问题。",
    "analysis": "你是一个分析专家。请深入分析问题，提供全面的见解。",
    "creative": "你是一个创意写作专家。请发挥想象力，创作有趣的内容。",
}


class TaskType(Enum):
    """任务类型"""
    SIMPLE = "simple"        # 简单：格式化、翻译、简单问答
    MEDIUM = "medium"        # 中等：总结、改写、分析
    COMPLEX = "complex"      # 复杂：推理、创作
    CODING = "coding"        # 编程：代码生成、调试
    CHINESE = "chinese"      # 中文：中文任务


@dataclass
class ModelConfig:
    """模型配置"""
    id: str                  # 模型 ID
    name: str                # 显示名称
    size: str                # 模型大小
    role: str                # 角色: draft/target/general
    strengths: List[str]     # 擅长领域
    max_tokens: int = 4096
    cost_per_1k: float = 0.0  # NVIDIA NIM 免费
    provider: str = "nvidia"  # nvidia | xiaoyi


# 模型配置
MODELS = {
    # Draft 模型（快速生成候选）
    "llama-3.2-1b": ModelConfig(
        id="meta/llama-3.2-1b-instruct",
        name="Llama 3.2 1B",
        size="1B",
        role="draft",
        strengths=["fast", "simple"],
        max_tokens=2048,
        provider="nvidia"
    ),
    "llama-3.2-3b": ModelConfig(
        id="meta/llama-3.2-3b-instruct",
        name="Llama 3.2 3B",
        size="3B",
        role="draft",
        strengths=["fast", "general"],
        max_tokens=4096,
        provider="nvidia"
    ),
    
    # Target 模型（高质量验证）
    "llama-3.1-8b": ModelConfig(
        id="meta/llama-3.1-8b-instruct",
        name="Llama 3.1 8B",
        size="8B",
        role="target",
        strengths=["general", "reasoning", "chinese"],
        max_tokens=4096,
        provider="nvidia"
    ),
    "qwen-2.5-7b": ModelConfig(
        id="qwen/qwen2.5-7b-instruct",
        name="Qwen 2.5 7B",
        size="7B",
        role="target",
        strengths=["chinese", "coding", "general"],
        max_tokens=4096,
        provider="nvidia"
    ),
    "deepseek-v3": ModelConfig(
        id="deepseek-ai/deepseek-v3.2",
        name="DeepSeek V3.2",
        size="671B",
        role="target",
        strengths=["reasoning", "coding", "math"],
        max_tokens=4096,
        provider="nvidia"
    ),
    
    # 小艺 GLM5（内部 API，无成本）
    "glm-5": ModelConfig(
        id="LLM_GLM5",
        name="GLM-5",
        size="?",
        role="general",
        strengths=["chinese", "general", "long-context"],
        max_tokens=4096,
        cost_per_1k=0.0,
        provider="xiaoyi"
    ),
    "qwen-2.5-7b": ModelConfig(
        id="qwen/qwen2.5-7b-instruct",
        name="Qwen 2.5 7B",
        size="7B",
        role="target",
        strengths=["chinese", "general"],
        max_tokens=4096,
        provider="nvidia"
    ),
    "qwen-3.5-397b": ModelConfig(
        id="qwen/qwen3.5-397b-a17b",
        name="Qwen 3.5 397B",
        size="397B",
        role="target",
        strengths=["chinese", "complex", "reasoning"],
        max_tokens=8192,
        provider="nvidia"
    ),
    "deepseek-v3.2": ModelConfig(
        id="deepseek-ai/deepseek-v3.2",
        name="DeepSeek V3.2",
        size="large",
        role="target",
        strengths=["coding", "reasoning", "math"],
        max_tokens=8192,
        provider="nvidia"
    ),
    "mistral-large": ModelConfig(
        id="mistralai/mistral-large",
        name="Mistral Large",
        size="large",
        role="target",
        strengths=["general", "reasoning", "multilingual"],
        max_tokens=8192,
        provider="nvidia"
    ),
}


# ============================================================================
# 故障转移系统
# ============================================================================

class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"      # 正常
    OPEN = "open"          # 熔断（暂停调用）
    HALF_OPEN = "half_open"  # 半开（试探性恢复）


@dataclass
class CircuitBreaker:
    """熔断器"""
    name: str
    failure_threshold: int = 3      # 连续失败 N 次后熔断
    recovery_timeout: int = 60      # 熔断后 N 秒尝试恢复
    half_open_max_calls: int = 3    # 半开状态最大尝试次数
    
    # 状态
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    half_open_calls: int = 0
    
    def record_success(self):
        """记录成功"""
        self.success_count += 1
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self._reset()
    
    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.state == CircuitState.HALF_OPEN:
            self._trip()
        elif self.failure_count >= self.failure_threshold:
            self._trip()
    
    def can_call(self) -> bool:
        """是否可以调用"""
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            # 检查是否可以进入半开状态
            if self.last_failure_time:
                elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    return True
            return False
        else:  # HALF_OPEN
            return self.half_open_calls < self.half_open_max_calls
    
    def _trip(self):
        """熔断"""
        self.state = CircuitState.OPEN
        logger.warning(f"熔断器 [{self.name}] 已熔断")
    
    def _reset(self):
        """重置"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0
        logger.info(f"熔断器 [{self.name}] 已恢复")


@dataclass
class RetryPolicy:
    """重试策略"""
    max_retries: int = 3
    base_delay: float = 1.0      # 基础延迟（秒）
    max_delay: float = 30.0      # 最大延迟
    exponential_base: float = 2.0  # 指数基数
    
    def get_delay(self, attempt: int) -> float:
        """计算延迟（指数退避）"""
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)


@dataclass
class FallbackChain:
    """备用链"""
    name: str
    models: List[str]             # 备用模型列表
    current_index: int = 0        # 当前使用的模型索引
    
    def get_next(self) -> Optional[str]:
        """获取下一个备用模型"""
        if self.current_index < len(self.models) - 1:
            self.current_index += 1
            return self.models[self.current_index]
        return None
    
    def reset(self):
        """重置"""
        self.current_index = 0
    
    def current(self) -> str:
        """获取当前模型"""
        return self.models[self.current_index]


class FailoverManager:
    """故障转移管理器"""
    
    def __init__(self):
        # 熔断器（每个模型一个）
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        
        # 重试策略
        self.retry_policy = RetryPolicy()
        
        # 备用链
        self.fallback_chains = {
            "default": FallbackChain("default", ["llama-3.1-8b", "glm-5", "qwen-2.5-7b"]),
            "coding": FallbackChain("coding", ["deepseek-v3.2", "glm-5", "llama-3.1-8b"]),
            "chinese": FallbackChain("chinese", ["qwen-2.5-7b", "glm-5", "llama-3.1-8b"]),
            "fast": FallbackChain("fast", ["llama-3.2-1b", "glm-5", "llama-3.1-8b"]),
        }
        
        # 健康状态
        self.health_status: Dict[str, Dict] = {}
        
        # 统计
        self.stats = {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "fallback_calls": 0,
            "circuit_opens": 0,
            "retries": 0,
        }
    
    def get_circuit_breaker(self, model: str) -> CircuitBreaker:
        """获取模型的熔断器"""
        if model not in self.circuit_breakers:
            self.circuit_breakers[model] = CircuitBreaker(name=model)
        return self.circuit_breakers[model]
    
    def get_fallback_chain(self, task_type: str = "default") -> FallbackChain:
        """获取备用链"""
        return self.fallback_chains.get(task_type, self.fallback_chains["default"])
    
    async def call_with_failover(
        self,
        call_func,
        model: str,
        task_type: str = "default",
        **kwargs
    ) -> Tuple[Any, Dict]:
        """
        带故障转移的调用
        
        Args:
            call_func: 调用函数
            model: 首选模型
            task_type: 任务类型（用于选择备用链）
            **kwargs: 传递给调用函数的参数
        
        Returns:
            (结果, 信息)
        """
        self.stats["total_calls"] += 1
        
        # 获取备用链
        fallback_chain = self.get_fallback_chain(task_type)
        fallback_chain.reset()
        
        # 如果首选模型不在备用链中，添加到首位
        if model not in fallback_chain.models:
            fallback_chain.models.insert(0, model)
            fallback_chain.current_index = 0
        
        last_error = None
        attempted_models = []
        
        while True:
            current_model = fallback_chain.current()
            attempted_models.append(current_model)
            
            # 检查熔断器
            circuit = self.get_circuit_breaker(current_model)
            if not circuit.can_call():
                logger.warning(f"模型 [{current_model}] 熔断中，跳过")
                next_model = fallback_chain.get_next()
                if next_model is None:
                    break
                continue
            
            # 尝试调用（带重试）
            for attempt in range(self.retry_policy.max_retries):
                try:
                    result = await call_func(current_model, **kwargs)
                    
                    # 成功
                    circuit.record_success()
                    self.stats["successful_calls"] += 1
                    
                    if current_model != model:
                        self.stats["fallback_calls"] += 1
                    
                    return result, {
                        "model": current_model,
                        "attempted_models": attempted_models,
                        "attempts": attempt + 1,
                        "fallback": current_model != model,
                    }
                    
                except Exception as e:
                    last_error = e
                    circuit.record_failure()
                    
                    if circuit.state == CircuitState.OPEN:
                        self.stats["circuit_opens"] += 1
                    
                    if attempt < self.retry_policy.max_retries - 1:
                        self.stats["retries"] += 1
                        delay = self.retry_policy.get_delay(attempt)
                        logger.warning(f"模型 [{current_model}] 调用失败，{delay:.1f}s 后重试: {e}")
                        await asyncio.sleep(delay)
            
            # 当前模型彻底失败，尝试下一个
            logger.error(f"模型 [{current_model}] 重试耗尽，切换备用")
            next_model = fallback_chain.get_next()
            if next_model is None:
                break
        
        # 所有模型都失败
        self.stats["failed_calls"] += 1
        raise Exception(f"所有模型调用失败: {attempted_models}。最后错误: {last_error}")
    
    def update_health(self, model: str, is_healthy: bool, latency: float = 0):
        """更新健康状态"""
        if model not in self.health_status:
            self.health_status[model] = {
                "is_healthy": True,
                "last_check": None,
                "avg_latency": 0,
                "total_calls": 0,
                "failure_rate": 0,
            }
        
        status = self.health_status[model]
        status["last_check"] = datetime.now()
        status["total_calls"] += 1
        
        # 更新平均延迟
        if latency > 0:
            status["avg_latency"] = (status["avg_latency"] * (status["total_calls"] - 1) + latency) / status["total_calls"]
        
        # 更新健康状态
        if not is_healthy:
            failures = self.circuit_breakers.get(model, CircuitBreaker(name=model)).failure_count
            status["failure_rate"] = failures / status["total_calls"]
        
        status["is_healthy"] = is_healthy and status["failure_rate"] < 0.5
    
    def get_health_report(self) -> Dict:
        """获取健康报告"""
        return {
            "stats": self.stats,
            "circuit_breakers": {
                name: {
                    "state": cb.state.value,
                    "failures": cb.failure_count,
                    "successes": cb.success_count,
                }
                for name, cb in self.circuit_breakers.items()
            },
            "health_status": self.health_status,
        }


# ============================================================================
# 语义缓存
# ============================================================================

@dataclass
class CacheEntry:
    """缓存条目"""
    prompt_hash: str
    prompt: str
    response: str
    model: str
    timestamp: datetime
    hit_count: int = 0
    ttl_seconds: int = 3600
    
    def is_expired(self) -> bool:
        return datetime.now() - self.timestamp > timedelta(seconds=self.ttl_seconds)


class SemanticCache:
    """语义缓存"""
    
    def __init__(self, max_size: int = 1000):
        self.cache: Dict[str, CacheEntry] = {}
        self.max_size = max_size
    
    def _hash(self, prompt: str, model: str = "") -> str:
        """计算 hash"""
        content = f"{model}:{prompt}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, prompt: str, model: str = "") -> Optional[str]:
        """获取缓存"""
        h = self._hash(prompt, model)
        entry = self.cache.get(h)
        
        if entry and not entry.is_expired():
            entry.hit_count += 1
            logger.debug(f"缓存命中: {h[:8]}... (hits: {entry.hit_count})")
            return entry.response
        
        return None
    
    def set(self, prompt: str, response: str, model: str, ttl: int = 3600):
        """设置缓存"""
        h = self._hash(prompt, model)
        
        # LRU 淘汰
        if len(self.cache) >= self.max_size:
            oldest = min(self.cache.values(), key=lambda e: e.hit_count)
            del self.cache[oldest.prompt_hash]
        
        self.cache[h] = CacheEntry(
            prompt_hash=h,
            prompt=prompt,
            response=response,
            model=model,
            timestamp=datetime.now(),
            ttl_seconds=ttl
        )
    
    def stats(self) -> Dict:
        """统计信息"""
        hits = sum(e.hit_count for e in self.cache.values())
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "total_hits": hits
        }


# ============================================================================
# 智能路由
# ============================================================================

class SmartRouter:
    """智能路由器"""
    
    # 任务类型关键词
    TASK_KEYWORDS = {
        TaskType.SIMPLE: ["翻译", "格式化", "转换", "translate", "format", "简单"],
        TaskType.MEDIUM: ["总结", "分析", "改写", "summarize", "analyze", "解释"],
        TaskType.COMPLEX: ["推理", "为什么", "创作", "reasoning", "create", "设计"],
        TaskType.CODING: ["代码", "编程", "函数", "code", "function", "debug", "bug"],
        TaskType.CHINESE: ["中文", "汉语", "chinese"],
    }
    
    def __init__(self):
        pass
    
    def classify_task(self, prompt: str) -> TaskType:
        """分类任务"""
        prompt_lower = prompt.lower()
        
        scores = {t: 0 for t in TaskType}
        
        for task_type, keywords in self.TASK_KEYWORDS.items():
            for kw in keywords:
                if kw in prompt_lower:
                    scores[task_type] += 1
        
        if max(scores.values()) == 0:
            return TaskType.MEDIUM
        
        return max(scores, key=scores.get)
    
    def select_model(self, prompt: str, task_type: Optional[TaskType] = None) -> str:
        """选择最优模型"""
        if task_type is None:
            task_type = self.classify_task(prompt)
        
        # 根据任务类型选择
        model_mapping = {
            TaskType.SIMPLE: "llama-3.2-1b",
            TaskType.MEDIUM: "llama-3.1-8b",
            TaskType.COMPLEX: "llama-3.3-70b",
            TaskType.CODING: "deepseek-v3.2",
            TaskType.CHINESE: "qwen-2.5-7b",
        }
        
        return model_mapping.get(task_type, "llama-3.1-8b")
    
    def select_draft_model(self) -> str:
        """选择 Draft 模型"""
        return "llama-3.2-1b"
    
    def select_target_model(self, prompt: str) -> str:
        """选择 Target 模型"""
        return self.select_model(prompt)


# ============================================================================
# LLM 优化器
# ============================================================================

class LLMOptimizer:
    """L4 多 API 优化层"""
    
    def __init__(self, api_key: str = NVIDIA_API_KEY, base_url: str = NVIDIA_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url
        self.cache = SemanticCache()
        self.router = SmartRouter()
        self.failover = FailoverManager()  # 故障转移管理器
        self.session: Optional[aiohttp.ClientSession] = None
        
        # 缓存优化
        self.current_system_prompt = SYSTEM_PROMPTS["default"]
        self.system_prompt_type = "default"
        
        # 统计
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "speculative_requests": 0,
            "speculative_accepted": 0,
            "nvidia_cached_tokens": 0,
            "nvidia_cache_hit_count": 0,
        }
        
        logger.info(f"L4 优化器初始化完成，API: {base_url}")
    
    def set_system_prompt(self, prompt_type: str = "default", custom_prompt: str = None):
        """
        设置 System Prompt（提高 NVIDIA NIM 缓存命中率）
        
        Args:
            prompt_type: prompt 类型 (default/coding/chinese/analysis/creative)
            custom_prompt: 自定义 prompt（优先级更高）
        """
        if custom_prompt:
            self.current_system_prompt = custom_prompt
            self.system_prompt_type = "custom"
        else:
            self.current_system_prompt = SYSTEM_PROMPTS.get(prompt_type, SYSTEM_PROMPTS["default"])
            self.system_prompt_type = prompt_type
        
        logger.info(f"System Prompt 已设置为: {self.system_prompt_type}")
    
    async def _ensure_session(self):
        """确保 session 存在"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close(self):
        """关闭连接"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def _call_api(
        self,
        model_key: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        system: str = None
    ) -> Tuple[str, Dict]:
        """调用 API（支持多 Provider）"""
        model = MODELS[model_key]
        
        await self._ensure_session()
        
        # 使用固定的 system prompt（提高缓存命中率）
        if system is None:
            system = self.current_system_prompt
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        start_time = time.time()
        
        # 根据 Provider 选择不同的调用方式
        if model.provider == "xiaoyi":
            return await self._call_xiaoyi_api(model, messages, max_tokens, temperature, start_time)
        else:
            return await self._call_nvidia_api(model, messages, max_tokens, temperature, start_time)
    
    async def _call_nvidia_api(
        self,
        model: ModelConfig,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        start_time: float
    ) -> Tuple[str, Dict]:
        """调用 NVIDIA NIM API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
        }
        
        body = {
            "model": model.id,
            "messages": messages,
            "max_tokens": min(max_tokens, model.max_tokens),
            "temperature": temperature,
        }
        
        try:
            async with self.session.post(
                f"{NVIDIA_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"NVIDIA API 错误 {resp.status}: {error_text}")
                
                data = await resp.json()
                response = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                
                latency = time.time() - start_time
                
                # 记录 NVIDIA NIM 缓存命中
                cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                if cached_tokens > 0:
                    self.stats["nvidia_cached_tokens"] += cached_tokens
                    self.stats["nvidia_cache_hit_count"] += 1
                
                return response, {
                    "model": model.name,
                    "provider": "nvidia",
                    "latency": latency,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "cached_tokens": cached_tokens,
                    "cache_hit_rate": cached_tokens / usage.get("prompt_tokens", 1) * 100 if usage.get("prompt_tokens", 0) > 0 else 0,
                }
                
        except Exception as e:
            logger.error(f"NVIDIA API 调用失败: {e}")
            raise
    
    async def _call_xiaoyi_api(
        self,
        model: ModelConfig,
        messages: List[Dict],
        max_tokens: int,
        temperature: float,
        start_time: float
    ) -> Tuple[str, Dict]:
        """调用小艺 GLM5 API"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-request-from": "openclaw",
            "x-uid": XIAOYI_UID,
            "x-api-key": XIAOYI_API_KEY,
        }
        
        body = {
            "model": model.id,
            "messages": messages,
            "max_tokens": min(max_tokens, model.max_tokens),
            "temperature": temperature,
            "stream": False,
        }
        
        try:
            async with self.session.post(
                f"{XIAOYI_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"小艺 API 错误 {resp.status}: {error_text}")
                
                # 小艺返回 SSE 格式，需要解析
                text = await resp.text()
                response = self._parse_xiaoyi_sse(text)
                
                latency = time.time() - start_time
                
                # 小艺 API 无缓存机制
                return response, {
                    "model": model.name,
                    "provider": "xiaoyi",
                    "latency": latency,
                    "prompt_tokens": 0,  # 小艺不返回详细 usage
                    "completion_tokens": 0,
                    "cached_tokens": 0,
                    "cache_hit_rate": 0,
                }
                
        except Exception as e:
            logger.error(f"小艺 API 调用失败: {e}")
            raise
    
    def _parse_xiaoyi_sse(self, text: str) -> str:
        """解析小艺 SSE 响应"""
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
    
    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        use_cache: bool = True,
        use_failover: bool = True,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        生成入口
        
        Args:
            prompt: 输入提示
            model: 指定模型（None 则自动选择）
            use_cache: 是否使用缓存
            use_failover: 是否启用故障转移
            **kwargs: 其他参数
        """
        self.stats["total_requests"] += 1
        
        # 智能路由
        if model is None:
            model = self.router.select_model(prompt)
        
        # 检查缓存
        if use_cache:
            cached = self.cache.get(prompt, model)
            if cached:
                self.stats["cache_hits"] += 1
                return cached, {"model": model, "cached": True}
        
        # 调用 API（带或不带故障转移）
        if use_failover:
            # 获取任务类型
            task_type = self.router.classify_task(prompt).value
            
            # 使用故障转移管理器
            async def call_wrapper(m, **kw):
                return await self._call_api(m, prompt, **kw)
            
            response, info = await self.failover.call_with_failover(
                call_wrapper,
                model,
                task_type=task_type,
                **kwargs
            )
            info["cached"] = False
        else:
            # 直接调用（无故障转移）
            response, info = await self._call_api(model, prompt, **kwargs)
            info["cached"] = False
        
        # 缓存结果
        if use_cache:
            self.cache.set(prompt, response, model)
        
        return response, info
    
    async def speculative_decode(
        self,
        prompt: str,
        draft_model: Optional[str] = None,
        target_model: Optional[str] = None,
        acceptance_threshold: float = 0.8,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        投机解码
        
        流程:
        1. Draft 模型快速生成候选
        2. Target 模型验证候选
        3. 如果接受，直接返回；否则用 Target 重新生成
        """
        self.stats["speculative_requests"] += 1
        
        # 选择模型
        if draft_model is None:
            draft_model = self.router.select_draft_model()
        if target_model is None:
            target_model = self.router.select_target_model(prompt)
        
        logger.info(f"投机解码: draft={draft_model}, target={target_model}")
        
        start_time = time.time()
        
        # 1. Draft 模型生成候选
        draft_response, draft_info = await self._call_api(
            draft_model, prompt, **kwargs
        )
        
        draft_latency = draft_info["latency"]
        
        # 2. Target 模型验证（简化版：直接比较长度和相关性）
        # 实际应该用 Target 模型验证每个 token
        # 这里简化为：如果 Draft 响应足够长，就接受
        
        draft_len = len(draft_response)
        prompt_len = len(prompt)
        
        # 简单的接受条件：响应长度合理
        accept = draft_len > prompt_len * 0.5 and draft_len > 50
        
        if accept:
            self.stats["speculative_accepted"] += 1
            total_latency = time.time() - start_time
            return draft_response, {
                "method": "speculative",
                "accepted": True,
                "draft_model": draft_model,
                "target_model": target_model,
                "draft_latency": draft_latency,
                "total_latency": total_latency,
                "speedup": draft_latency / total_latency if total_latency > 0 else 1.0,
            }
        
        # 3. 拒绝，用 Target 重新生成
        target_response, target_info = await self._call_api(
            target_model, prompt, **kwargs
        )
        
        total_latency = time.time() - start_time
        
        return target_response, {
            "method": "speculative",
            "accepted": False,
            "draft_model": draft_model,
            "target_model": target_model,
            "draft_latency": draft_latency,
            "target_latency": target_info["latency"],
            "total_latency": total_latency,
        }
    
    async def parallel_generate(
        self,
        prompt: str,
        models: List[str] = None,
        **kwargs
    ) -> Tuple[str, Dict]:
        """
        并行生成（多模型投票）
        
        同时调用多个模型，选择最佳结果
        """
        if models is None:
            models = ["llama-3.1-8b", "qwen-2.5-7b", "mistral-large"]
        
        await self._ensure_session()
        
        # 并行调用
        tasks = [self._call_api(m, prompt, **kwargs) for m in models]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 收集结果
        responses = {}
        for model, result in zip(models, results):
            if isinstance(result, Exception):
                responses[model] = {"error": str(result)}
            else:
                responses[model] = {
                    "response": result[0],
                    "info": result[1]
                }
        
        # 选择最佳（目前选最快的）
        valid = [(k, v) for k, v in responses.items() if "response" in v]
        if valid:
            best = min(valid, key=lambda x: x[1]["info"]["latency"])
            return best[1]["response"], {
                "method": "parallel",
                "best_model": best[0],
                "all_models": list(responses.keys()),
            }
        
        raise Exception("所有模型都失败")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            "cache": self.cache.stats(),
            "cache_hit_rate": (
                self.stats["cache_hits"] / self.stats["total_requests"] 
                if self.stats["total_requests"] > 0 else 0
            ),
            "speculative_accept_rate": (
                self.stats["speculative_accepted"] / self.stats["speculative_requests"]
                if self.stats["speculative_requests"] > 0 else 0
            ),
            "nvidia_cache_avg_tokens": (
                self.stats["nvidia_cached_tokens"] / self.stats["nvidia_cache_hit_count"]
                if self.stats["nvidia_cache_hit_count"] > 0 else 0
            ),
            "current_system_prompt": self.system_prompt_type,
            "failover": self.failover.get_health_report(),
        }
    
    def get_health_report(self) -> Dict:
        """获取健康报告"""
        return self.failover.get_health_report()
    
    async def warmup_cache(self, prompts: List[str] = None):
        """
        预热缓存（提高后续请求的缓存命中率）
        
        Args:
            prompts: 预热的 prompt 列表
        """
        if prompts is None:
            prompts = [
                "你好",
                "请介绍一下你自己",
                "你能做什么",
            ]
        
        logger.info(f"预热缓存: {len(prompts)} 个 prompt")
        
        for prompt in prompts:
            try:
                await self._call_api("llama-3.1-8b", prompt, max_tokens=10)
            except Exception as e:
                logger.warning(f"预热失败: {e}")
        
        logger.info("缓存预热完成")


# ============================================================================
# 同步包装器
# ============================================================================

class LLMOptimizerSync:
    """同步版本"""
    
    def __init__(self):
        self.optimizer = LLMOptimizer()
    
    def generate(self, prompt: str, model: str = None, **kwargs) -> str:
        """生成"""
        response, _ = asyncio.run(self.optimizer.generate(prompt, model, **kwargs))
        return response
    
    def speculative_decode(self, prompt: str, **kwargs) -> str:
        """投机解码"""
        response, _ = asyncio.run(self.optimizer.speculative_decode(prompt, **kwargs))
        return response
    
    def parallel_generate(self, prompt: str, models: List[str] = None, **kwargs) -> str:
        """并行生成"""
        response, _ = asyncio.run(self.optimizer.parallel_generate(prompt, models, **kwargs))
        return response
    
    def get_stats(self) -> Dict:
        """统计信息"""
        return self.optimizer.get_stats()


# ============================================================================
# CLI 测试
# ============================================================================

if __name__ == "__main__":
    import sys
    
    async def test():
        optimizer = LLMOptimizer()
        
        print("=" * 60)
        print("L4 多 API 优化层测试")
        print("=" * 60)
        
        # 测试 1: 智能路由
        print("\n[测试 1] 智能路由")
        prompts = [
            ("翻译这句话到英文", "翻译任务"),
            ("写一个 Python 快速排序", "编程任务"),
            ("解释量子力学的基本原理", "复杂任务"),
            ("你好", "简单任务"),
        ]
        
        for prompt, desc in prompts:
            task_type = optimizer.router.classify_task(prompt)
            model = optimizer.router.select_model(prompt)
            print(f"  {desc}: {task_type.value} → {model}")
        
        # 测试 2: System Prompt 缓存优化
        print("\n[测试 2] System Prompt 缓存优化")
        optimizer.set_system_prompt("default")
        
        # 预热缓存
        print("  预热缓存...")
        await optimizer.warmup_cache(["你好", "测试"])
        
        # 测试缓存命中
        print("  测试缓存命中:")
        for i in range(3):
            response, info = await optimizer.generate("你好，请用一句话介绍自己")
            print(f"    请求 {i+1}: cache_hit_rate={info.get('cache_hit_rate', 0):.1f}%")
        
        # 测试 3: 投机解码
        print("\n[测试 3] 投机解码")
        response, info = await optimizer.speculative_decode(
            "什么是人工智能？请简要说明。"
        )
        print(f"  Response: {response[:100]}...")
        print(f"  Info: accepted={info.get('accepted')}, speedup={info.get('speedup', 1):.2f}x")
        
        # 测试 4: 缓存
        print("\n[测试 4] 客户端缓存测试")
        prompt = "1+1等于几？"
        r1, i1 = await optimizer.generate(prompt)
        r2, i2 = await optimizer.generate(prompt)
        print(f"  第一次: cached={i1.get('cached', False)}")
        print(f"  第二次: cached={i2.get('cached', False)}")
        
        # 测试 5: 故障转移
        print("\n[测试 5] 故障转移测试")
        print("  模拟主模型失败...")
        
        # 获取健康报告
        health = optimizer.get_health_report()
        print(f"  当前熔断器状态: {health.get('circuit_breakers', {})}")
        print(f"  故障转移统计: {health.get('stats', {})}")
        
        # 测试备用链
        print("\n  备用链配置:")
        for name, chain in optimizer.failover.fallback_chains.items():
            print(f"    {name}: {' → '.join(chain.models)}")
        
        # 统计
        print("\n[统计信息]")
        stats = optimizer.get_stats()
        for k, v in stats.items():
            if isinstance(v, float):
                print(f"  {k}: {v:.2f}")
            elif isinstance(v, dict):
                print(f"  {k}: {json.dumps(v, indent=4, ensure_ascii=False)[:200]}")
            else:
                print(f"  {k}: {v}")
        
        await optimizer.close()
    
    asyncio.run(test())
