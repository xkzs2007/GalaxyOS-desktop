#!/usr/bin/env python3
"""
投机解码模块 (Speculative Decoding)

论文参考:
- Leviathan et al. (2023): Fast Inference from Transformers via Speculative Decoding (ICML 2023)
- Medusa: Simple LLM Inference Acceleration Framework with Multiple Decoding Heads (2024)
- Eagle: Speculative Sampling Requires Rethinking Feature Uncertainty (2024)

核心思想:
1. 用小模型 (draft model) 快速生成 K 个候选 token
2. 用大模型 (target model) 并行验证这些 token
3. 接受正确的 token，拒绝错误的 token，从正确位置重新生成
4. 理论上不改变输出分布，但实际解码速度提升 2-3x

实现模式:
- 本模块提供投机解码框架，可与任何 LLM API 集成
- 支持 top-k 采样、温度调节
- 支持 draft token tree 验证（Medusa 风格）
"""

import logging
import time
import json
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SpecDecodingStrategy(Enum):
    """投机解码策略"""
    GREEDY = "greedy"              # 贪心: draft 取 argmax
    SAMPLING = "sampling"          # 采样: draft 按分布采样
    TOP_K = "top_k"               # Top-K 采样
    MEDUSA = "medusa"             # Medusa: 多头并行


@dataclass
class SpecDecodingConfig:
    """投机解码配置"""
    strategy: SpecDecodingStrategy = SpecDecodingStrategy.SAMPLING
    max_draft_tokens: int = 5      # 最大 draft token 数
    temperature: float = 0.7       # 采样温度
    top_k: int = 10               # Top-K 采样参数
    acceptance_threshold: float = 0.1  # 接受阈值（用于 Medusa 多头）
    max_retries: int = 3          # 最大重试次数


@dataclass
class DraftResult:
    """Draft 生成结果"""
    tokens: List[str]             # Draft token 列表
    token_ids: List[int]          # Draft token ID 列表
    logprobs: List[float]         # 每个 token 的 log 概率
    draft_model: str              # Draft 模型名
    latency_ms: float = 0.0       # Draft 生成延迟


@dataclass
class VerificationResult:
    """验证结果"""
    accepted_tokens: List[str]    # 被接受的 token
    accepted_count: int           # 接受数量
    rejected_at: int              # 拒绝位置 (-1 表示全部接受)
    acceptance_rate: float        # 接受率
    target_model: str             # Target 模型名
    latency_ms: float = 0.0       # 验证延迟


@dataclass
class SpecDecodingResult:
    """投机解码最终结果"""
    text: str                     # 生成的文本
    tokens: List[str]             # 所有 token
    draft_results: List[DraftResult]       # Draft 结果列表
    verification_results: List[VerificationResult]  # 验证结果列表
    total_draft_tokens: int       # 总 draft token 数
    total_accepted_tokens: int    # 总接受 token 数
    overall_acceptance_rate: float  # 总体接受率
    total_latency_ms: float      # 总延迟
    speedup: float = 1.0         # 加速比
    metadata: Dict = field(default_factory=dict)


class DraftModel:
    """
    Draft 模型包装器

    用小模型快速生成候选 token。
    实际部署时，这里应该连接到真正的小模型 API（如 Qwen3-4B）。
    """

    def __init__(
        self,
        llm_client: Any = None,
        model_name: str = "draft-model",
        max_tokens_per_draft: int = 5,
    ):
        self.llm_client = llm_client
        self.model_name = model_name
        self.max_tokens_per_draft = max_tokens_per_draft
        self.stats = {
            'draft_calls': 0,
            'total_draft_tokens': 0,
            'total_latency_ms': 0.0,
        }

    def generate_draft(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
    ) -> DraftResult:
        """
        生成 draft token

        Args:
            messages: 对话消息列表
            max_tokens: 最大 draft token 数
            temperature: 采样温度

        Returns:
            DraftResult
        """
        start = time.time()
        max_tokens = max_tokens or self.max_tokens_per_draft

        if self.llm_client is None:
            # 无 draft 模型时，返回空结果
            return DraftResult(
                tokens=[],
                token_ids=[],
                logprobs=[],
                draft_model=self.model_name,
                latency_ms=0.0,
            )

        try:
            # 调用小模型生成 draft
            prompt = messages[-1].get("content", "") if messages else ""
            result = self.llm_client.chat(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                use_cache=False,
            )

            if result is None:
                return DraftResult(
                    tokens=[],
                    token_ids=[],
                    logprobs=[],
                    draft_model=self.model_name,
                    latency_ms=(time.time() - start) * 1000,
                )

            # 将结果按 token 拆分
            # 简化: 按空格和标点拆分
            tokens = self._split_to_tokens(result)
            logprobs = [0.0] * len(tokens)  # 简化: 无实际 logprob

            latency = (time.time() - start) * 1000
            self.stats['draft_calls'] += 1
            self.stats['total_draft_tokens'] += len(tokens)
            self.stats['total_latency_ms'] += latency

            return DraftResult(
                tokens=tokens,
                token_ids=list(range(len(tokens))),
                logprobs=logprobs,
                draft_model=self.model_name,
                latency_ms=latency,
            )

        except Exception as e:
            logger.error(f"Draft 生成失败: {e}")
            return DraftResult(
                tokens=[],
                token_ids=[],
                logprobs=[],
                draft_model=self.model_name,
                latency_ms=(time.time() - start) * 1000,
            )

    @staticmethod
    def _split_to_tokens(text: str) -> List[str]:
        """将文本拆分为 token 级别（简化版）"""
        tokens = []
        current = ""
        for ch in text:
            if ch in (' ', '\n', '\t', '.', ',', '!', '?', ';', ':', '"', "'", '。', '，', '！', '？'):
                if current:
                    tokens.append(current)
                tokens.append(ch)
                current = ""
            else:
                current += ch
        if current:
            tokens.append(current)
        return tokens

    def get_stats(self) -> Dict:
        return dict(self.stats)


class TargetModel:
    """
    Target 模型包装器

    用大模型验证 draft token。
    实际部署时，这里应该连接到真正的大模型 API（如 Qwen3-72B）。
    """

    def __init__(
        self,
        llm_client: Any = None,
        model_name: str = "target-model",
    ):
        self.llm_client = llm_client
        self.model_name = model_name
        self.stats = {
            'verify_calls': 0,
            'total_verified_tokens': 0,
            'total_latency_ms': 0.0,
        }

    def verify_draft(
        self,
        messages: List[Dict[str, str]],
        draft_tokens: List[str],
    ) -> VerificationResult:
        """
        验证 draft token

        通过让大模型生成完整回复，然后与 draft 对比来验证。
        实际部署时，应使用大模型的 logprob API 进行精确验证。

        Args:
            messages: 对话消息列表
            draft_tokens: Draft token 列表

        Returns:
            VerificationResult
        """
        start = time.time()

        if self.llm_client is None or not draft_tokens:
            return VerificationResult(
                accepted_tokens=[],
                accepted_count=0,
                rejected_at=-1,
                acceptance_rate=0.0,
                target_model=self.model_name,
                latency_ms=0.0,
            )

        try:
            # 让大模型生成回复
            draft_text = "".join(draft_tokens)
            verify_prompt = list(messages)
            # 在 prompt 中包含 draft，让大模型判断是否一致
            verify_prompt.append({
                "role": "assistant",
                "content": draft_text,
            })
            verify_prompt.append({
                "role": "user",
                "content": "请继续完成上述回答。如果上述回答开头合理，请从上述内容继续；否则从头开始。",
            })

            result = self.llm_client.chat(
                verify_prompt,
                max_tokens=max(len(draft_tokens) * 3, 100),
                temperature=0.1,
                use_cache=False,
            )

            latency = (time.time() - start) * 1000
            self.stats['verify_calls'] += 1
            self.stats['total_latency_ms'] += latency

            if result is None:
                return VerificationResult(
                    accepted_tokens=[],
                    accepted_count=0,
                    rejected_at=0,
                    acceptance_rate=0.0,
                    target_model=self.model_name,
                    latency_ms=latency,
                )

            # 比较大模型输出与 draft 的前缀匹配
            accepted_tokens = []
            rejected_at = -1
            result_tokens = self._split_to_tokens(result)

            for i, draft_tok in enumerate(draft_tokens):
                if i < len(result_tokens) and self._token_match(draft_tok, result_tokens[i]):
                    accepted_tokens.append(draft_tok)
                else:
                    rejected_at = i
                    break

            accepted_count = len(accepted_tokens)
            acceptance_rate = accepted_count / len(draft_tokens) if draft_tokens else 0.0
            self.stats['total_verified_tokens'] += len(draft_tokens)

            return VerificationResult(
                accepted_tokens=accepted_tokens,
                accepted_count=accepted_count,
                rejected_at=rejected_at,
                acceptance_rate=acceptance_rate,
                target_model=self.model_name,
                latency_ms=latency,
            )

        except Exception as e:
            logger.error(f"验证失败: {e}")
            return VerificationResult(
                accepted_tokens=[],
                accepted_count=0,
                rejected_at=0,
                acceptance_rate=0.0,
                target_model=self.model_name,
                latency_ms=(time.time() - start) * 1000,
            )

    @staticmethod
    def _split_to_tokens(text: str) -> List[str]:
        """将文本拆分为 token"""
        return DraftModel._split_to_tokens(text)

    @staticmethod
    def _token_match(draft: str, target: str) -> bool:
        """判断两个 token 是否匹配"""
        return draft.strip().lower() == target.strip().lower()

    def get_stats(self) -> Dict:
        return dict(self.stats)


class SpeculativeDecoder:
    """
    投机解码器

    使用小模型生成候选 token，大模型验证。

    使用示例:
    >>> draft_model = DraftModel(llm_client=small_client, model_name="qwen3-4b")
    >>> target_model = TargetModel(llm_client=large_client, model_name="qwen3-72b")
    >>> decoder = SpeculativeDecoder(draft_model=draft_model, target_model=target_model)
    >>> result = decoder.decode([{"role": "user", "content": "你好"}])
    >>> print(result.text)
    >>> print(f"加速比: {result.speedup:.2f}x")
    """

    def __init__(
        self,
        draft_model: Optional[DraftModel] = None,
        target_model: Optional[TargetModel] = None,
        config: Optional[SpecDecodingConfig] = None,
    ):
        self.draft_model = draft_model or DraftModel()
        self.target_model = target_model or TargetModel()
        self.config = config or SpecDecodingConfig()

        self.stats = {
            'total_decodes': 0,
            'total_draft_tokens': 0,
            'total_accepted_tokens': 0,
            'total_draft_latency_ms': 0.0,
            'total_verify_latency_ms': 0.0,
            'total_latency_ms': 0.0,
        }

    def decode(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> SpecDecodingResult:
        """
        投机解码

        Args:
            messages: 对话消息列表
            max_tokens: 最大生成 token 数
            temperature: 采样温度

        Returns:
            SpecDecodingResult
        """
        start_time = time.time()
        all_tokens = []
        draft_results = []
        verification_results = []
        total_draft = 0
        total_accepted = 0

        remaining_tokens = max_tokens
        max_iterations = max_tokens + 10  # 安全上限，防止无限循环

        # 避免修改传入的 messages
        working_messages = list(messages)

        while remaining_tokens > 0 and max_iterations > 0:
            max_iterations -= 1

            # Step 1: Draft 模型生成候选 token
            draft = self.draft_model.generate_draft(
                messages=working_messages,
                max_tokens=min(self.config.max_draft_tokens, remaining_tokens),
                temperature=temperature,
            )
            draft_results.append(draft)

            if not draft.tokens:
                # Draft 失败，直接用 target 生成
                break

            # Step 2: Target 模型验证
            verification = self.target_model.verify_draft(
                messages=working_messages,
                draft_tokens=draft.tokens,
            )
            verification_results.append(verification)

            # Step 3: 收集接受的 token
            accepted = verification.accepted_tokens
            if accepted:
                all_tokens.extend(accepted)
                total_draft += len(draft.tokens)
                total_accepted += len(accepted)
                remaining_tokens -= len(accepted)

            if verification.rejected_at == -1:
                # 全部接受，继续生成
                continue
            else:
                # 有拒绝，从拒绝点重新开始
                break

        # 如果没有生成任何 token，用 target 直接生成
        if not all_tokens and self.target_model.llm_client:
            direct_result = self.target_model.llm_client.chat(
                messages,
                max_tokens=remaining_tokens,
                temperature=temperature,
                use_cache=False,
            )
            if direct_result:
                all_tokens = self.target_model._split_to_tokens(direct_result)

        total_latency = (time.time() - start_time) * 1000
        text = "".join(all_tokens)

        # 计算加速比
        # 理论加速比 = (K+1) / (K * acceptance_rate + 1)
        # 其中 K 是 draft token 数量
        acceptance_rate = total_accepted / total_draft if total_draft > 0 else 0.0
        k = self.config.max_draft_tokens
        if acceptance_rate > 0:
            speedup = (k + 1) / (k * (1 - acceptance_rate) + 1)
        else:
            speedup = 1.0

        # 更新统计
        self.stats['total_decodes'] += 1
        self.stats['total_draft_tokens'] += total_draft
        self.stats['total_accepted_tokens'] += total_accepted
        self.stats['total_latency_ms'] += total_latency

        return SpecDecodingResult(
            text=text,
            tokens=all_tokens,
            draft_results=draft_results,
            verification_results=verification_results,
            total_draft_tokens=total_draft,
            total_accepted_tokens=total_accepted,
            overall_acceptance_rate=acceptance_rate,
            total_latency_ms=total_latency,
            speedup=round(speedup, 2),
            metadata={
                'config': {
                    'strategy': self.config.strategy.value,
                    'max_draft_tokens': self.config.max_draft_tokens,
                },
            },
        )

    def get_stats(self) -> Dict:
        """获取统计信息"""
        stats = dict(self.stats)
        if stats['total_draft_tokens'] > 0:
            stats['overall_acceptance_rate'] = (
                stats['total_accepted_tokens'] / stats['total_draft_tokens']
            )
        else:
            stats['overall_acceptance_rate'] = 0.0
        if stats['total_decodes'] > 0:
            stats['avg_latency_ms'] = stats['total_latency_ms'] / stats['total_decodes']
        return stats


# ==================== DSpark-Style Confidence-Scheduled Decoder ====================
# Inspired by: deepseek-ai/DeepSpec (DSpark: block-wise draft + confidence early-stop)
# Source: https://github.com/deepseek-ai/DeepSpec
# Reference: DSpark_paper.pdf bundled in DeepSpec repo
#
# 与经典 Speculative Decoding 的区别 (DSpark-style):
# 1. Block-wise verify: draft 一次性产出一个 block (默认 K=7) 的 token 链，target
#    并行验证整个 block 段，定位首个 rejection 位置。
# 2. Confidence-Scheduled K: 不是固定 K，而是根据历史 acceptance_rate 自适应调整
#    draft 长度 (类似 DFlash 的动态 block size，但走置信度路径)：
#    - 高接受率 (>0.8) → 扩展 K，搏长接受
#    - 中接受率 (0.5-0.8) → 保持当前 K
#    - 低接受率 (<0.5) → 收缩 K，减少 wasted compute
# 3. Confidence early-stop: 客户端支持返回 draft 置信度（avg logprob / max prob），
#    当置信度低于 threshold 时立即停止本轮 draft（DSpark eval.py 行为）。
# 4. 不改变输出分布 (与 Leviathan 2023 一致：拒绝后 target 重新生成保证分布等价)。
#
# 设计权衡：本项目里 draft/target 都用同一个 llm_client.chat()，没有真实 drafter
# 训练流程；因此该调度器只解决"推理时调度"层，训练 drafter 请用 DeepSpec 仓库
# (SpecForge + Eagle3/DFlash/DSpark) 在外部完成。


@dataclass
class ConfidenceScheduledConfig:
    """DSpark 风格调度配置"""
    initial_block_size: int = 7              # 初始 draft block 长度 (对齐 DeepSpec block7)
    min_block_size: int = 2                  # 自适应下限
    max_block_size: int = 16                 # 自适应上限
    confidence_threshold: float = 0.0        # draft 置信度早停阈值 (0.0=不早停, 仅采集)
    high_accept_threshold: float = 0.8       # 接受率高于此值 → 扩张 K
    low_accept_threshold: float = 0.5        # 接受率低于此值 → 收缩 K
    expansion_factor: float = 1.5            # K 扩张乘子
    shrink_factor: float = 0.5               # K 收缩乘子
    max_total_blocks: int = 200              # 安全上限


@dataclass
class BlockVerificationResult:
    """Block-wise 验证结果 (DSpark 风格)"""
    block_size: int                          # 本轮 block 长度
    accepted_count: int                      # block 内被接受的 token 数
    rejected_at: int                         # 首个拒绝位置 (-1 = 全部接受)
    acceptance_rate: float                   # 本轮 block 接受率
    stopped_early: bool = False              # 是否因 confidence 早停
    avg_confidence: float = 1.0              # 本轮 draft 平均置信度
    latency_ms: float = 0.0


class _ConfidenceDraftModel(DraftModel):
    """DSpark 风格 draft wrapper: 在生成 draft 之外，额外返回 confidence。

    因为当前基础设施没有 logprobs API，confidence 用一个 0.0-1.0 的占位值。
    真实部署时应该用目标模型的 logprob API 或 drafter head 的 sigmoid 输出。
    """

    def __init__(self, *args, confidence_provider=None, **kwargs):
        super().__init__(*args, **kwargs)
        # confidence_provider: 可调用对象 (messages, token_ids) -> float
        # 如果为 None，使用 1.0 (不早停)
        self._confidence_provider = confidence_provider

    def estimate_confidence(self, messages, token_ids) -> float:
        """估算 draft 的置信度 (0.0-1.0)"""
        if not token_ids or self._confidence_provider is None:
            return 1.0
        try:
            conf = float(self._confidence_provider(messages, token_ids))
            return max(0.0, min(1.0, conf))
        except Exception:
            return 1.0


class ConfidenceScheduledDecoder:
    """
    DSpark 风格置信度调度投机解码器

    工作循环 (与 DeepSpec 仓库 README 一致)：
        1. Draft 生成 block_size 个 token (含 confidence)
        2. 若 confidence < threshold 早停 → 截短 block
        3. Target 一次性验证整个 block，定位首个 rejection
        4. 更新累计 acceptance_rate，按调度规则调整下一轮 block_size
        5. 接受区间的 token 进入输出，rejection 位置由 target 续生成

    使用示例:
        >>> draft = _ConfidenceDraftModel(llm_client=small, model_name="qwen3-4b-draft")
        >>> target = TargetModel(llm_client=large, model_name="qwen3-72b")
        >>> decoder = ConfidenceScheduledDecoder(draft_model=draft, target_model=target)
        >>> result = decoder.decode([{"role":"user","content":"hi"}])
        >>> print(result.text, result.speedup)
    """

    def __init__(
        self,
        draft_model: Optional[_ConfidenceDraftModel] = None,
        target_model: Optional[TargetModel] = None,
        config: Optional[ConfidenceScheduledConfig] = None,
    ):
        self.draft_model = draft_model or _ConfidenceDraftModel()
        self.target_model = target_model or TargetModel()
        self.config = config or ConfidenceScheduledConfig()

        # 调度状态
        self.current_block_size = self.config.initial_block_size
        # 统计
        self.stats = {
            'total_decodes': 0,
            'total_blocks': 0,
            'total_draft_tokens': 0,
            'total_accepted_tokens': 0,
            'total_latency_ms': 0.0,
            'early_stop_count': 0,
            'acceptance_window': [],  # 近 N 轮 acceptance 用于调度
        }

    def _update_block_size(self, acceptance_rate: float) -> int:
        """根据本轮接受率自适应调整下一轮 block_size (Confidence-Scheduled K)"""
        if acceptance_rate >= self.config.high_accept_threshold:
            new_size = min(
                self.config.max_block_size,
                max(self.current_block_size + 1,
                    int(self.current_block_size * self.config.expansion_factor)),
            )
        elif acceptance_rate <= self.config.low_accept_threshold:
            new_size = max(
                self.config.min_block_size,
                int(self.current_block_size * self.config.shrink_factor),
            )
        else:
            new_size = self.current_block_size  # 保持
        return new_size

    def decode(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 500,
        temperature: float = 0.7,
    ) -> SpecDecodingResult:
        """DSpark 风格 block-wise 置信度调度解码"""
        start_time = time.time()
        all_tokens: List[str] = []
        draft_results: List[DraftResult] = []
        verifications: List[BlockVerificationResult] = []
        total_draft = 0
        total_accepted = 0

        remaining = max_tokens
        block_count = 0
        self.current_block_size = self.config.initial_block_size
        # 重置 acceptance 窗口，确保每次 decode 独立
        self.stats['acceptance_window'] = []

        while remaining > 0 and block_count < self.config.max_total_blocks:
            block_count += 1
            k = min(self.current_block_size, remaining)

            # Step 1: draft 一个 block
            draft = self.draft_model.generate_draft(
                messages=messages,
                max_tokens=k,
                temperature=temperature,
            )
            draft_results.append(draft)

            if not draft.tokens:
                break

            # Step 2: confidence 早停
            avg_conf = self.draft_model.estimate_confidence(
                messages, draft.token_ids,
            )
            stopped_early = False
            if (self.config.confidence_threshold > 0.0
                    and avg_conf < self.config.confidence_threshold):
                stopped_early = True
                # DSpark 行为：截短 block，仅提交前 N 个 token (N = floor(k*conf))
                keep = max(1, int(len(draft.tokens) * avg_conf))
                draft = DraftResult(
                    tokens=draft.tokens[:keep],
                    token_ids=draft.token_ids[:keep],
                    logprobs=draft.logprobs[:keep],
                    draft_model=draft.draft_model,
                    latency_ms=draft.latency_ms,
                )
                self.stats['early_stop_count'] += 1

            # Step 3: target 验证整 block
            verification = self.target_model.verify_draft(
                messages=messages,
                draft_tokens=draft.tokens,
            )
            block_v = BlockVerificationResult(
                block_size=len(draft.tokens),
                accepted_count=verification.accepted_count,
                rejected_at=verification.rejected_at,
                acceptance_rate=verification.acceptance_rate,
                stopped_early=stopped_early,
                avg_confidence=avg_conf,
                latency_ms=verification.latency_ms,
            )
            verifications.append(block_v)

            accepted = verification.accepted_tokens
            if accepted:
                all_tokens.extend(accepted)
                total_draft += len(draft.tokens)
                total_accepted += len(accepted)
                remaining -= len(accepted)

            # Step 4: 自适应 K (滑动窗口均值)
            self.stats['acceptance_window'].append(verification.acceptance_rate)
            if len(self.stats['acceptance_window']) > 5:
                self.stats['acceptance_window'].pop(0)
            recent_rate = (
                sum(self.stats['acceptance_window'])
                / len(self.stats['acceptance_window'])
            )
            self.current_block_size = self._update_block_size(recent_rate)

            # 全部接受 → 继续 block；否则跳出走 fallback
            if verification.rejected_at == -1 and not stopped_early:
                continue
            break

        # Fallback: 无 token 时直接 target 生成
        if not all_tokens and self.target_model.llm_client:
            direct = self.target_model.llm_client.chat(
                messages,
                max_tokens=remaining,
                temperature=temperature,
                use_cache=False,
            )
            if direct:
                all_tokens = TargetModel._split_to_tokens(direct)

        total_latency = (time.time() - start_time) * 1000
        text = "".join(all_tokens)
        acceptance_rate = total_accepted / total_draft if total_draft > 0 else 0.0
        # 加速比公式同 SpeculativeDecoder: (K+1) / (K*(1-accept)+1)
        k_avg = (total_draft / block_count) if block_count else self.config.initial_block_size
        if acceptance_rate > 0:
            speedup = (k_avg + 1) / (k_avg * (1 - acceptance_rate) + 1)
        else:
            speedup = 1.0

        self.stats['total_decodes'] += 1
        self.stats['total_blocks'] += block_count
        self.stats['total_draft_tokens'] += total_draft
        self.stats['total_accepted_tokens'] += total_accepted
        self.stats['total_latency_ms'] += total_latency

        return SpecDecodingResult(
            text=text,
            tokens=all_tokens,
            draft_results=draft_results,
            verification_results=[],  # 兼容字段：实际 block 验证在 verifications
            total_draft_tokens=total_draft,
            total_accepted_tokens=total_accepted,
            overall_acceptance_rate=round(acceptance_rate, 4),
            total_latency_ms=round(total_latency, 2),
            speedup=round(speedup, 2),
            metadata={
                'algorithm': 'DSpark-style',
                'block_count': block_count,
                'avg_block_size': round(k_avg, 2),
                'early_stop_count': self.stats['early_stop_count'],
                'final_block_size': self.current_block_size,
                'block_verifications': [
                    {
                        'block_size': v.block_size,
                        'accepted': v.accepted_count,
                        'rejected_at': v.rejected_at,
                        'acceptance_rate': round(v.acceptance_rate, 4),
                        'stopped_early': v.stopped_early,
                        'avg_confidence': round(v.avg_confidence, 4),
                    } for v in verifications
                ],
            },
        )

    def get_stats(self) -> Dict:
        stats = dict(self.stats)
        if stats['total_draft_tokens'] > 0:
            stats['overall_acceptance_rate'] = (
                stats['total_accepted_tokens'] / stats['total_draft_tokens']
            )
        else:
            stats['overall_acceptance_rate'] = 0.0
        if stats['total_decodes'] > 0:
            stats['avg_latency_ms'] = (
                stats['total_latency_ms'] / stats['total_decodes']
            )
            stats['avg_blocks_per_decode'] = (
                stats['total_blocks'] / stats['total_decodes']
            )
        return stats


# 导出
__all__ = [
    'SpeculativeDecoder',
    'DraftModel',
    'TargetModel',
    'SpecDecodingConfig',
    'SpecDecodingStrategy',
    'DraftResult',
    'VerificationResult',
    'SpecDecodingResult',
    # DSpark-style
    'ConfidenceScheduledDecoder',
    'ConfidenceScheduledConfig',
    'BlockVerificationResult',
    '_ConfidenceDraftModel',
]


if __name__ == "__main__":
    print("=== 投机解码测试 ===\n")

    # 不依赖 API 的结构测试
    draft = DraftModel(model_name="qwen3-4b")
    target = TargetModel(model_name="qwen3-72b")

    decoder = SpeculativeDecoder(
        draft_model=draft,
        target_model=target,
        config=SpecDecodingConfig(max_draft_tokens=5),
    )

    # 测试 decode（无 API，draft 会返回空）
    result = decoder.decode([{"role": "user", "content": "你好"}])
    print(f"生成文本: '{result.text}'")
    print(f"加速比: {result.speedup:.2f}x")
    print(f"统计: {decoder.get_stats()}")

    # 测试 token 拆分
    tokens = DraftModel._split_to_tokens("你好，世界！这是一个测试。")
    print(f"\nToken 拆分: {tokens}")
