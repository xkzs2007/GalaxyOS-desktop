"""
GalaxyOS Harness — GalaxyAgent 基础类

轻量级封装 R-CCAM 认知循环调用，核心逻辑在 galaxyos.engine 中。
不复制引擎代码，仅通过 from galaxyos.engine import ... 调用引擎能力。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from galaxyos.harness.workspace import Workspace

logger = logging.getLogger("galaxyos.harness.agent")


class GalaxyAgent:
    """桌面端独立 Agent

    封装 R-CCAM 认知循环调用，通过 engine 实例委托所有核心逻辑。

    Args:
        engine: AgentCoreBridge 或兼容引擎实例
        workspace: Workspace 工作空间实例
        worker_pool_size: Worker 池大小
    """

    def __init__(
        self,
        engine: Any,
        workspace: Workspace,
        worker_pool_size: int = 2,
    ) -> None:
        self._engine = engine
        self._workspace = workspace
        self._worker_pool_size = worker_pool_size
        self._running = False

    @property
    def engine(self) -> Any:
        return self._engine

    @property
    def workspace(self) -> Workspace:
        return self._workspace

    @property
    def worker_pool_size(self) -> int:
        return self._worker_pool_size

    @property
    def is_running(self) -> bool:
        return self._running

    async def run(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        """执行 R-CCAM 认知循环

        通过 engine.process() 调用引擎的 R-CCAM 五阶段管线:
        Retrieval → Cognition → Control → Action → Memory

        Args:
            prompt: 用户输入文本
            **kwargs: 传递给 engine.process() 的额外参数:
                - max_cycles: 最大循环轮次（默认 1）
                - store_memory: 是否持久化记忆（默认 True）
                - session_key: 会话 Key

        Returns:
            引擎处理结果字典，包含:
                - generated_answer: 生成的回答
                - answer_confidence: 回答置信度
                - strategy: 使用的策略
                - knowledge_type: 知识类型
                - 其他引擎返回的元数据
        """
        self._running = True
        start = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                self._sync_run,
                prompt,
                kwargs,
            )
            elapsed = time.monotonic() - start
            result["_harness_meta"] = {
                "elapsed_ms": int(elapsed * 1000),
                "worker_pool_size": self._worker_pool_size,
                "workspace": self._workspace.root,
            }
            return result
        except Exception as e:
            logger.error("GalaxyAgent.run 失败: %s", e, exc_info=True)
            return {
                "error": str(e),
                "generated_answer": "",
                "answer_confidence": 0.0,
            }
        finally:
            self._running = False

    def _sync_run(self, prompt: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """同步执行引擎 process 调用（在线程池中运行）"""
        max_cycles = kwargs.get("max_cycles", 1)
        store_memory = kwargs.get("store_memory", True)
        session_key = kwargs.get("session_key", "")

        return self._engine.process(
            user_input=prompt,
            max_cycles=max_cycles,
            store_memory=store_memory,
            session_key=session_key,
        )

    async def recall(self, query: str, top_k: int = 10) -> Dict[str, Any]:
        """检索记忆

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            检索结果字典
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._engine.recall,
            query,
            top_k,
        )

    async def store(self, content: str, source: str = "user") -> Dict[str, Any]:
        """存储记忆

        Args:
            content: 记忆内容
            source: 来源标识

        Returns:
            存储结果字典
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._engine.remember,
            content,
            source,
        )

    def shutdown(self) -> None:
        """关闭 Agent，释放资源"""
        self._running = False
        logger.info("GalaxyAgent 已关闭")
