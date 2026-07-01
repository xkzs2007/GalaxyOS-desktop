"""
GalaxyOS Engine — 核心引擎层

包含:
  - ClawWorker  常驻 Python Worker (UDS JSON-RPC, selectors 串行)
  - XiaoYiClawLLM  引擎主类 (13层/44工作流)
  - UnifiedEntry  CLI 入口
  - RetrievalHub  7通道检索
  - DAG Context Manager  上下文压缩
  - FastPIL  独立子进程图像处理
  - CircuitBreaker/SessionContext  弹性基础设施
"""

# Engine 模块延迟导入（避免初始化时的循环依赖）
# 运行时通过 claw_worker.main() 启动完整引擎
