"""GalaxyOS Integration Test Suite"""
import asyncio
import json
from pathlib import Path


def test_skill_md_parser():
    print("\n[1] SKILLMDParser")
    from galaxyos.skill_infra.skill_md_parser import SKILLMDParser
    parser = SKILLMDParser()
    skill_md = Path("_eval/skills/skills/engineering/implement/SKILL.md")
    parsed = parser.parse_file(skill_md)
    print(f"  name={parsed.name}, disable_model_invocation={parsed.disable_model_invocation}")
    print(f"  steps={len(parsed.steps)}, refs={len(parsed.references)}")
    assert parsed.name == "implement"
    assert parsed.disable_model_invocation is True
    print("  [PASS]")


def test_dependency_resolver():
    print("\n[2] SkillDependencyResolver")
    from galaxyos.skill_infra.skill_md_parser import SKILLMDParser
    from galaxyos.skill_infra.skill_dependency_resolver import SkillDependencyResolver
    parser = SKILLMDParser()
    resolver = SkillDependencyResolver(parser=parser)
    skills_dir = Path("_eval/skills/skills/engineering")
    for sd in skills_dir.iterdir():
        sm = sd / "SKILL.md"
        if sm.exists():
            p = parser.parse_file(sm)
            resolver.register_skill(p)
    resolver.resolve_all()
    order = resolver.install_order()
    cycles = resolver.detect_cycles()
    print(f"  install_order length={len(order)}, cycles={len(cycles)}")
    assert len(cycles) == 0
    print("  [PASS]")


def test_installer_and_discovery():
    print("\n[3] SkillInstaller + SkillDiscovery")
    from galaxyos.skill_infra.skill_installer import SkillInstaller
    from galaxyos.skill_infra.skill_discovery import SkillDiscovery
    installer = SkillInstaller()
    report = installer.install_from_github(target_dir="_eval/skills", scope="user")
    discovery = SkillDiscovery()
    all_skills = discovery.discover(invocation_type="all")
    user_skills = discovery.discover(invocation_type="user-invoked")
    model_skills = discovery.discover(invocation_type="model-invoked")
    print(f"  installed={report.installed_skills}, total_discovered={len(all_skills)}")
    print(f"  user_invoked={len(user_skills)}, model_invoked={len(model_skills)}")
    assert len(all_skills) > 0
    print("  [PASS]")


def test_agent_core_bridge():
    print("\n[4] AgentCoreBridge")
    from galaxyos.kernel.agent_core_bridge import AgentCoreBridge, AgentType
    bridge = AgentCoreBridge()
    at = bridge.select_agent_type("grill-me")
    assert at == AgentType.REACT
    at2 = bridge.select_agent_type("implement")
    assert at2 == AgentType.WORKFLOW
    print(f"  grill-me -> {at.value}, implement -> {at2.value}")
    print("  [PASS]")


def test_memory_sync_bridge():
    print("\n[5] MemorySyncBridge")
    from galaxyos.kernel.memory_sync_bridge import MemorySyncBridge

    async def _test():
        mem = MemorySyncBridge()
        entry = await mem.dual_write("ws-1", "test content", source="test", skill_name="grill-me")
        result = await mem.recall("ws-1", query="test")
        isolation = mem.verify_workspace_isolation("ws-1", "ws-2")
        print(f"  write_id={entry.id}, recall_count={result.total}, isolation={isolation}")
        assert result.total >= 1
        assert isolation is True
        print("  [PASS]")

    asyncio.run(_test())


def test_llm_router_proxy():
    print("\n[6] LLMRouterProxy")
    from galaxyos.kernel.llm_router_proxy import LLMRouterProxy

    async def _test():
        proxy = LLMRouterProxy(default_model="balanced")
        result = await proxy.call("test prompt", skill_name="grill-me", workspace_id="ws-1")
        print(f"  model={result['model']}, tokens={result['total_tokens']}")
        assert result["model"] == "flagship"
        cost = proxy.get_cost_summary(workspace_id="ws-1")
        print(f"  cost_summary: total_tokens={cost['total_tokens']}")
        print("  [PASS]")

    asyncio.run(_test())


def test_hook_adapter():
    print("\n[7] HookAdapter")
    from galaxyos.mcp.hook_adapter import HookAdapter
    adapter = HookAdapter()
    mapping = adapter.get_mapping_table()
    print(f"  mapping_count={len(mapping)}")
    assert len(mapping) == 9
    print("  [PASS]")


def test_skill_executor():
    print("\n[8] SkillExecutor")
    from galaxyos.kernel.skill_executor import SkillExecutor

    async def _test():
        executor = SkillExecutor()
        content = Path("_eval/skills/skills/engineering/implement/SKILL.md").read_text(encoding="utf-8")
        result = await executor.execute("implement", content)
        print(f"  status={result.status.value}, steps_completed={result.steps_completed}/{result.steps_total}")
        print("  [PASS]")

    asyncio.run(_test())


def test_dual_runtime_manager():
    print("\n[9] DualRuntimeManager")
    from galaxyos.kernel.dual_runtime_manager import DualRuntimeManager
    mgr = DualRuntimeManager()
    hc = mgr.health_check()
    print(f"  health={hc.status} (expected: stopped)")
    assert hc.status == "stopped"
    print("  [PASS]")


def test_brand_config():
    print("\n[10] Brand Config")
    brand = json.loads(Path("galaxyos/frontend/brand.config.json").read_text())
    print(f"  product={brand['productName']}, version={brand['about']['version']}")
    assert brand["productName"] == "GalaxyOS"
    print("  [PASS]")


if __name__ == "__main__":
    print("=" * 60)
    print("GalaxyOS Integration Test")
    print("=" * 60)

    tests = [
        test_skill_md_parser,
        test_dependency_resolver,
        test_installer_and_discovery,
        test_agent_core_bridge,
        test_memory_sync_bridge,
        test_llm_router_proxy,
        test_hook_adapter,
        test_skill_executor,
        test_dual_runtime_manager,
        test_brand_config,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)