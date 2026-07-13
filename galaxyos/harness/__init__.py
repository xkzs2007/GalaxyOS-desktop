"""
GalaxyOS Harness — 桌面端独立 Agent 入口

提供不依赖 OpenClaw SDK 的轻量级 Agent 创建接口。
核心逻辑委托给 galaxyos.engine，harness 仅做薄封装。

Usage:
    from galaxyos.harness import create_galaxy_agent
    agent = create_galaxy_agent(config={})
    result = await agent.run("你好")
"""

from __future__ import annotations

import importlib
import logging
import warnings
from typing import Any, Dict, Optional

from galaxyos.harness.agent import GalaxyAgent
from galaxyos.harness.workspace import Workspace

logger = logging.getLogger("galaxyos.harness")

__all__ = ["create_galaxy_agent", "GalaxyAgent", "Workspace"]


def _check_openclaw_sdk() -> None:
    """启动时检测 OpenClaw SDK 是否存在，若存在则发出告警。

    检测不阻断启动流程，仅做日志告警，建议用户移除 OpenClaw 依赖。
    """
    try:
        importlib.import_module("openclaw")
        warnings.warn(
            "检测到 openclaw 模块已安装。GalaxyOS harness 层不依赖 OpenClaw SDK，"
            "建议移除 openclaw 依赖以获得更轻量的运行环境。",
            UserWarning,
            stacklevel=3,
        )
        logger.warning(
            "检测到 openclaw 模块已安装，harness 层不依赖 OpenClaw SDK，建议移除"
        )
    except ImportError:
        pass


_check_openclaw_sdk()


def create_galaxy_agent(
    config: Optional[Dict[str, Any]] = None,
) -> GalaxyAgent:
    """创建 GalaxyAgent 实例（桌面端独立入口）

    流程:
        1. 初始化引擎（懒加载 galaxyos.engine）
        2. 配置 Worker Pool
        3. 返回 GalaxyAgent 实例

    Args:
        config: 可选配置字典，支持以下键:
            - worker_pool_size: Worker 池大小（默认 2）
            - session_id: 会话 ID
            - workspace: 工作空间路径（默认使用 galaxyos.shared.paths.workspace()）
            - engine_config: 传递给引擎的额外配置

    Returns:
        GalaxyAgent 实例
    """
    from galaxyos.shared.constants import DEFAULT_WORKER_POOL_SIZE

    cfg = config or {}
    worker_pool_size = cfg.get("worker_pool_size", DEFAULT_WORKER_POOL_SIZE)
    session_id = cfg.get("session_id")
    workspace_path = cfg.get("workspace")
    engine_config = cfg.get("engine_config", {})

    ws = Workspace(root=workspace_path)

    from galaxyos.kernel.agent_core_bridge import AgentCoreBridge

    merged_engine_config = dict(engine_config)
    if session_id:
        merged_engine_config["session_id"] = session_id

    engine = AgentCoreBridge(config=merged_engine_config)

    agent = GalaxyAgent(
        engine=engine,
        workspace=ws,
        worker_pool_size=worker_pool_size,
    )

    logger.info(
        "GalaxyAgent 已创建 (worker_pool_size=%d, workspace=%s)",
        worker_pool_size,
        ws.root,
    )
    return agent