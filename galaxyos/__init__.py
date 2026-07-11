"""
GalaxyOS — OpenClaw 核心认知增强引擎

统一的 Python 包，整合：
  - engine/       核心引擎 (Worker, 检索, 记忆, DAG, R-CCAM)
  - privileged/   跨平台系统模块 (Vector API, Platform Adapter, GPU/NUMA 优化)
  - orchestration/ 工作流调度 (Workflow Engine, Task Engine)
  - config/       引擎配置

Usage:
    from galaxyos.engine import ClawWorker
    from galaxyos.privileged import VectorAPI, PlatformAdapter
    from galaxyos.orchestration import WorkflowEngine
"""

__version__ = "0.1.4"
__author__ = "xiaoyi-claw"
