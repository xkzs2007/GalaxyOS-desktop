"""
GalaxyOS Desktop Agent v0.1.4 — 集成测试套件

端到端验证：
  1. 认知增强层注入
  2. 记忆系统
  3. R-CCAM + TokUI 融合
  4. MCP 通信
  5. 性能验收
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List


class TestResult:
    def __init__(self, suite: str, name: str, passed: bool, latency_ms: float = 0, detail: str = ""):
        self.suite = suite
        self.name = name
        self.passed = passed
        self.latency_ms = latency_ms
        self.detail = detail

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"  [{status}] {self.name} ({self.latency_ms:.0f}ms) {self.detail}"


async def test_cognitive_injection() -> List[TestResult]:
    results = []
    suite = "认知增强层注入"

    start = time.time()
    try:
        from galaxyos.kernel.agent_core_bridge import AgentCoreBridge, COGNITIVE_TOOL_NAMES
        bridge = AgentCoreBridge()
        await bridge.initialize()
        result = await bridge.inject_cognitive_tools()
        results.append(TestResult(suite, "认知工具注入", result.get("status") == "injected", (time.time() - start) * 1000, f"{len(COGNITIVE_TOOL_NAMES)} 工具"))
    except Exception as e:
        results.append(TestResult(suite, "认知工具注入", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.agent_core_bridge import AgentCoreBridge
        bridge = AgentCoreBridge()
        assert bridge.select_agent_type("grill-me").value == "react"
        assert bridge.select_agent_type("implement").value == "workflow"
        results.append(TestResult(suite, "Agent 类型选择", True, (time.time() - start) * 1000))
    except Exception as e:
        results.append(TestResult(suite, "Agent 类型选择", False, (time.time() - start) * 1000, str(e)))

    return results


async def test_memory_system() -> List[TestResult]:
    results = []
    suite = "记忆系统"

    start = time.time()
    try:
        from galaxyos.kernel.memory_sync_bridge import MemorySyncBridge
        from galaxyos.kernel.liquid_memory_adapter import LiquidMemoryAdapter
        from galaxyos.kernel.dag_context_fusion import DAGContextFusion

        adapter = LiquidMemoryAdapter()
        dag = DAGContextFusion()
        bridge = MemorySyncBridge(liquid_memory_adapter=adapter, dag_context_fusion=dag)

        await bridge.dual_write("test_ws", "集成测试记忆内容", source="test", memory_type="engram")
        result = await bridge.recall("test_ws", query="集成测试")
        results.append(TestResult(suite, "记忆双写+检索", result.total > 0, (time.time() - start) * 1000, f"{result.total} 条结果"))
    except Exception as e:
        results.append(TestResult(suite, "记忆双写+检索", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.memory_sync_bridge import MemorySyncBridge
        from galaxyos.kernel.liquid_memory_adapter import LiquidMemoryAdapter
        from galaxyos.kernel.dag_context_fusion import DAGContextFusion

        adapter = LiquidMemoryAdapter()
        dag = DAGContextFusion()
        bridge = MemorySyncBridge(liquid_memory_adapter=adapter, dag_context_fusion=dag)

        await bridge.dual_write("ws_a", "A data", source="test")
        await bridge.dual_write("ws_b", "B data", source="test")
        isolated = bridge.verify_workspace_isolation("ws_a", "ws_b")
        results.append(TestResult(suite, "WorkSpace 隔离", isolated, (time.time() - start) * 1000))
    except Exception as e:
        results.append(TestResult(suite, "WorkSpace 隔离", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.memory_sync_bridge import MemorySyncBridge
        from galaxyos.kernel.liquid_memory_adapter import LiquidMemoryAdapter
        from galaxyos.kernel.dag_context_fusion import DAGContextFusion

        adapter = LiquidMemoryAdapter()
        dag = DAGContextFusion()
        bridge = MemorySyncBridge(liquid_memory_adapter=adapter, dag_context_fusion=dag)

        result = await bridge.dream_mode_sync("test_ws")
        results.append(TestResult(suite, "Dream Mode 协同", "consolidation" in result, (time.time() - start) * 1000))
    except Exception as e:
        results.append(TestResult(suite, "Dream Mode 协同", False, (time.time() - start) * 1000, str(e)))

    return results


async def test_rccam_tokui() -> List[TestResult]:
    results = []
    suite = "R-CCAM + TokUI"

    start = time.time()
    try:
        from galaxyos.kernel.rccam_injector import RCCAMInjector
        from galaxyos.kernel.liquid_memory_adapter import LiquidMemoryAdapter
        from galaxyos.kernel.dag_context_fusion import DAGContextFusion

        adapter = LiquidMemoryAdapter()
        dag = DAGContextFusion()
        injector = RCCAMInjector(memory_adapter=adapter, dag_fusion=dag)

        state = await injector.on_pre_agent_reply("test_session", "测试查询")
        results.append(TestResult(suite, "R-CCAM Retrieval", not state.degraded, (time.time() - start) * 1000, f"phase={state.current_phase.value}"))
    except Exception as e:
        results.append(TestResult(suite, "R-CCAM Retrieval", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.tokui_builder import PyTokUIBuilder

        builder = PyTokUIBuilder()
        dsl = builder.card(tt="测试卡片").p("内容").end().build()
        valid = "[card" in dsl and "[/card]" in dsl
        results.append(TestResult(suite, "TokUI DSL 生成", valid, (time.time() - start) * 1000, f"{len(dsl)} chars"))
    except Exception as e:
        results.append(TestResult(suite, "TokUI DSL 生成", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.tokui_builder import PyTokUIBuilder

        builder = PyTokUIBuilder()
        dsl = builder.memory_panel(engram_count=10, neural_count=5, synapse_count=3).build()
        valid = "memory-panel" in dsl
        results.append(TestResult(suite, "自定义组件 DSL", valid, (time.time() - start) * 1000, f"{len(dsl)} chars"))
    except Exception as e:
        results.append(TestResult(suite, "自定义组件 DSL", False, (time.time() - start) * 1000, str(e)))

    return results


async def test_mcp_communication() -> List[TestResult]:
    results = []
    suite = "MCP 通信"

    start = time.time()
    try:
        from galaxyos.kernel.mcp_client import GalaxyOSMCPClient

        client = GalaxyOSMCPClient(transport="streamable_http", host="127.0.0.1", port=8765)
        tools = await client.list_tools()
        results.append(TestResult(suite, "MCP Client 工具发现", len(tools) >= 24, (time.time() - start) * 1000, f"{len(tools)} 工具"))
    except Exception as e:
        results.append(TestResult(suite, "MCP Client 工具发现", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.mcp_client import GalaxyOSMCPClient

        client = GalaxyOSMCPClient(transport="streamable_http")
        stats = client.get_stats()
        results.append(TestResult(suite, "MCP Client 状态", "transport" in stats, (time.time() - start) * 1000))
    except Exception as e:
        results.append(TestResult(suite, "MCP Client 状态", False, (time.time() - start) * 1000, str(e)))

    return results


async def test_performance() -> List[TestResult]:
    results = []
    suite = "性能验收"

    start = time.time()
    try:
        from galaxyos.kernel.tokui_builder import PyTokUIBuilder

        builder = PyTokUIBuilder()
        for i in range(30):
            builder.card(tt=f"卡片{i}").p(f"内容{i}").end()
        dsl = builder.build()
        elapsed = (time.time() - start) * 1000
        passed = elapsed <= 50
        results.append(TestResult(suite, "DSL 生成 ≤50ms", passed, elapsed, f"{len(dsl)} chars"))
    except Exception as e:
        results.append(TestResult(suite, "DSL 生成 ≤50ms", False, (time.time() - start) * 1000, str(e)))

    start = time.time()
    try:
        from galaxyos.kernel.liquid_memory_adapter import LiquidMemoryAdapter

        adapter = LiquidMemoryAdapter()
        for i in range(100):
            await adapter.write(content=f"性能测试记忆 {i}", source="perf")
        result = await adapter.recall(query="性能测试", top_k=10)
        elapsed = (time.time() - start) * 1000
        passed = elapsed <= 200
        results.append(TestResult(suite, "记忆检索 ≤200ms", passed, elapsed, f"{result.get('total', 0)} 结果"))
    except Exception as e:
        results.append(TestResult(suite, "记忆检索 ≤200ms", False, (time.time() - start) * 1000, str(e)))

    return results


async def main():
    print("=" * 60)
    print("GalaxyOS Desktop Agent v0.1.4 — 集成测试")
    print("=" * 60)

    all_results = []

    test_suites = [
        test_cognitive_injection,
        test_memory_system,
        test_rccam_tokui,
        test_mcp_communication,
        test_performance,
    ]

    for suite_fn in test_suites:
        results = await suite_fn()
        if results:
            print(f"\n[{results[0].suite}]")
            for r in results:
                print(r)
            all_results.extend(results)

    passed = sum(1 for r in all_results if r.passed)
    total = len(all_results)

    print("\n" + "=" * 60)
    print(f"集成测试结果: {passed}/{total} 通过")
    print("=" * 60)

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())