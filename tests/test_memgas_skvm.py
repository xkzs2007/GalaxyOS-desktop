#!/usr/bin/env python3
"""
test_memgas_skvm.py — Test stubs for MemGAS-SkVM integrated system

测试覆盖：
1. knowledge_asset: KnowledgeAsset CRUD, AssetRegistry, to_blob/from_blob
2. multi_granularity: MultiGranularityExtractor, GMMAssociator, GranularityPipeline
3. capability_registry: CapabilityProfile, HarnessProfile, ProfileMatcher, SkillClassifier, RegistryManager
4. skill_compiler: SkillCompiler.compile, profile_check, env_bind, skill_prune, optimize_text
5. cde_solidifier: CodeSolidifier.scan, solidify, detect_templates
6. concurrency_extractor: ConcurrencyExtractor.analyze, extract_steps, build_dag
"""

import os
import sys
import json
import time
import unittest
import tempfile
import logging
from typing import Dict, List, Optional

# 确保能 import
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICES_DIR = os.path.normpath(os.path.join(TEST_DIR, "..", "services"))
if SERVICES_DIR not in sys.path:
    sys.path.insert(0, SERVICES_DIR)

logging.basicConfig(level=logging.WARNING)


# ═══════════════════════════════════════════════
# 1. knowledge_asset 测试
# ═══════════════════════════════════════════════

class TestKnowledgeAsset(unittest.TestCase):
    """test KnowledgeAsset model + AssetRegistry"""

    def setUp(self):
        from knowledge_asset import KnowledgeAsset, AssetType, AssetRegistry, AssociationEdge
        self.KnowledgeAsset = KnowledgeAsset
        self.AssetType = AssetType
        self.AssetRegistry = AssetRegistry
        self.AssociationEdge = AssociationEdge

    def test_asset_creation(self):
        asset = self.KnowledgeAsset(
            asset_id="test_001",
            asset_type=self.AssetType.SKILL,
            raw_content="Test skill for web search",
            capability_profile={"web_access": 0.9, "search": 0.8},
            tags=["web", "search"],
            category="retrieval",
        )
        self.assertEqual(asset.asset_id, "test_001")
        self.assertEqual(asset.asset_type, self.AssetType.SKILL)
        self.assertEqual(asset.category, "retrieval")
        self.assertIn("web", asset.tags)

    def test_to_dict_from_dict(self):
        asset = self.KnowledgeAsset(
            asset_id="test_002",
            asset_type=self.AssetType.MEMORY,
            raw_content="User prefers Chinese expressions",
            tags=["preference"],
        )
        d = asset.to_dict(include_raw=True)
        self.assertEqual(d["asset_id"], "test_002")
        self.assertEqual(d["asset_type"], "memory")
        restored = self.KnowledgeAsset.from_dict(d)
        self.assertEqual(restored.asset_id, asset.asset_id)
        self.assertEqual(restored.raw_content, asset.raw_content)

    def test_to_blob_from_blob(self):
        asset = self.KnowledgeAsset(
            asset_id="test_003",
            asset_type=self.AssetType.SKILL,
            raw_content="Some skill content",
        )
        blob = asset.to_blob()
        self.assertIsInstance(blob, bytes)
        restored = self.KnowledgeAsset.from_blob(blob)
        self.assertEqual(restored.asset_id, asset.asset_id)
        self.assertEqual(restored.raw_content, asset.raw_content)

    def test_asset_registry_crud(self):
        reg = self.AssetRegistry()
        asset = self.KnowledgeAsset(
            asset_id="test_crud_001",
            asset_type=self.AssetType.SKILL,
            raw_content="CRUD test skill",
            category="test",
        )
        reg.register(asset)
        self.assertIsNotNone(reg.get("test_crud_001"))
        self.assertEqual(reg.count(), 1)

        deleted = reg.delete("test_crud_001")
        self.assertTrue(deleted)
        self.assertIsNone(reg.get("test_crud_001"))
        self.assertEqual(reg.count(), 0)

    def test_asset_registry_query_by_capability(self):
        reg = self.AssetRegistry()
        a1 = self.KnowledgeAsset(
            asset_id="q_web", asset_type=self.AssetType.SKILL,
            raw_content="web skill",
            capability_profile={"web_access": 0.9, "search": 0.8},
            importance=0.8,
        )
        a2 = self.KnowledgeAsset(
            asset_id="q_mem", asset_type=self.AssetType.MEMORY,
            raw_content="memory skill",
            capability_profile={"memory": 0.7, "search": 0.5},
            importance=0.6,
        )
        reg.register(a1)
        reg.register(a2)

        web_results = reg.query_by_capability("web_access", 0.5)
        self.assertEqual(len(web_results), 1)
        self.assertEqual(web_results[0].asset_id, "q_web")

        search_results = reg.query_by_capability("search", 0.5)
        self.assertGreaterEqual(len(search_results), 1)

    def test_asset_registry_edge(self):
        reg = self.AssetRegistry()
        a1 = self.KnowledgeAsset(asset_id="src", asset_type=self.AssetType.SKILL, raw_content="source")
        a2 = self.KnowledgeAsset(asset_id="dst", asset_type=self.AssetType.SKILL, raw_content="target")
        reg.register(a1)
        reg.register(a2)

        edge = self.AssociationEdge(target_asset_id="dst", relation="similar_to", weight=0.8)
        added = reg.add_edge("src", edge)
        self.assertTrue(added)

        neighbors = reg.get_neighbors("src")
        self.assertEqual(len(neighbors), 1)
        nb, rel, w = neighbors[0]
        self.assertEqual(nb.asset_id, "dst")
        self.assertEqual(rel, "similar_to")
        self.assertEqual(w, 0.8)

    def test_asset_registry_stats(self):
        reg = self.AssetRegistry()
        a1 = self.KnowledgeAsset(asset_id="s1", asset_type=self.AssetType.SKILL, raw_content="s1")
        a2 = self.KnowledgeAsset(asset_id="m1", asset_type=self.AssetType.MEMORY, raw_content="m1")
        reg.register(a1)
        reg.register(a2)
        stats = reg.get_stats()
        self.assertEqual(stats["total_assets"], 2)
        self.assertEqual(stats["type_counts"].get("skill", 0), 1)
        self.assertEqual(stats["type_counts"].get("memory", 0), 1)


# ═══════════════════════════════════════════════
# 2. multi_granularity 测试
# ═══════════════════════════════════════════════

class TestMultiGranularity(unittest.TestCase):
    """test MultiGranularityExtractor + GMMAssociator"""

    def setUp(self):
        from multi_granularity import MultiGranularityExtractor, GMMAssociator, GranularityPipeline
        self.MultiGranularityExtractor = MultiGranularityExtractor
        self.GMMAssociator = GMMAssociator
        self.GranularityPipeline = GranularityPipeline

    def test_extract_session_level(self):
        extractor = self.MultiGranularityExtractor()
        text = "这是一个测试文本。包含多个句子。用于验证多粒度提取。"
        result = extractor.extract(text)
        self.assertIn("session_level", result)
        self.assertIn("turn_level", result)
        self.assertIn("summary_level", result)
        self.assertIn("keyword_level", result)
        self.assertIn("这是一个测试文本", result["session_level"][:50])

    def test_extract_turn_level(self):
        extractor = self.MultiGranularityExtractor()
        text = "第一句。第二句！第三句？"
        result = extractor.extract(text)
        self.assertGreaterEqual(len(result["turn_level"]), 1)

    def test_extract_keywords(self):
        extractor = self.MultiGranularityExtractor()
        text = "人工智能深度学习自然语言处理计算机视觉算法"
        result = extractor.extract(text)
        self.assertGreaterEqual(len(result["keyword_level"]), 1)

    def test_gmm_associator_fit(self):
        associator = self.GMMAssociator(n_components=2)
        texts = [
            "用户偏好中文表达风格",
            "AI 应该使用七情六欲规则",
            "记忆系统支持五路并行检索",
            "系统架构共16层",
        ]
        ok = associator.fit(texts)
        self.assertTrue(ok)
        info = associator.cluster_info()
        self.assertTrue(info["fitted"])
        self.assertGreater(info["n_samples"], 0)

    def test_gmm_associator_associate(self):
        associator = self.GMMAssociator(n_components=2)
        texts = [
            "用户喜欢中文表达",
            "记忆系统支持混合检索",
            "AI 需要情感能力",
        ]
        associator.fit(texts)
        results = associator.associate("用户要求中国化表达方式", "new_001")
        self.assertIsInstance(results, list)

    def test_granularity_pipeline(self):
        pipe = self.GranularityPipeline()
        texts = [
            "用户偏好中文表达和七情六欲风格",
            "记忆系统支持混合检索五路并行",
            "系统架构共16层包含4个自研插件",
        ]
        result = pipe.process(texts)
        self.assertIn("granularities", result)
        self.assertIn("clusters", result)
        self.assertIn("edges", result)
        self.assertGreaterEqual(len(result["granularities"]), 3)


# ═══════════════════════════════════════════════
# 3. capability_registry 测试
# ═══════════════════════════════════════════════

class TestCapabilityRegistry(unittest.TestCase):
    """test CapabilityProfile, HarnessProfile, ProfileMatcher, SkillClassifier, RegistryManager"""

    def setUp(self):
        from capability_registry import CapabilityProfile, HarnessProfile, ProfileMatcher
        from capability_registry import SkillClassifier, RegistryManager
        self.CapabilityProfile = CapabilityProfile
        self.HarnessProfile = HarnessProfile
        self.ProfileMatcher = ProfileMatcher
        self.SkillClassifier = SkillClassifier
        self.RegistryManager = RegistryManager

    def test_capability_profile_defaults(self):
        profile = self.CapabilityProfile()
        self.assertEqual(profile.reasoning, 1)
        self.assertEqual(profile.token_budget, "medium")
        self.assertFalse(profile.memory)

    def test_capability_profile_summary(self):
        profile = self.CapabilityProfile(
            web_access={"enabled": True, "level": "search"},
            reasoning=3,
            memory=True,
        )
        summary = profile.summary()
        self.assertIn("web(search)", summary)
        self.assertIn("reasoning", summary)

    def test_harness_profile_auto_detect(self):
        hp = self.HarnessProfile.auto_detect()
        self.assertIsInstance(hp.model, str)
        self.assertGreater(len(hp.tools), 0)
        self.assertIsInstance(hp.environment, dict)
        # 自动检测后的 profile
        profile = hp.detect_profile()
        self.assertIsNotNone(profile)

    def test_profile_matcher_perfect_match(self):
        matcher = self.ProfileMatcher()
        skill = self.CapabilityProfile(
            web_access={"enabled": True, "level": "search"},
            reasoning=2,
            memory=True,
        )
        harness = self.CapabilityProfile(
            web_access={"enabled": True, "level": "search"},
            reasoning=2,
            memory=True,
        )
        score = matcher.compute_adaptation_score(skill, harness)
        self.assertGreater(score, 0.5)

    def test_profile_matcher_critical_gap(self):
        matcher = self.ProfileMatcher()
        skill = self.CapabilityProfile(
            web_access={"enabled": True, "level": "search"},
            memory=True,
        )
        harness = self.CapabilityProfile(web_access={"enabled": False, "level": "none"})
        score = matcher.compute_adaptation_score(skill, harness)
        self.assertEqual(score, 0.0)

    def test_skill_classifier(self):
        classifier = self.SkillClassifier()
        skill_text = """
# web-search
Search the web using galaxy-web-search API.
Fetches results from multiple search engines.
Requires: node, fetch API
Supports: search, scrape, query expansion
"""
        profile = classifier.classify(skill_text, "web-search")
        self.assertTrue(profile.web_access.get("enabled"))
        self.assertTrue(profile.search)

    def test_registry_manager(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rm = self.RegistryManager(data_dir=tmpdir)
            profile = self.CapabilityProfile(
                web_access={"enabled": True, "level": "search"},
                memory=True,
            )
            rm.register("skill_test", profile)
            loaded = rm.get("skill_test")
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.memory)

            results = rm.query_by_capability("memory")
            self.assertEqual(len(results), 1)

            stats = rm.get_stats()
            self.assertEqual(stats["total_profiles"], 1)

            deleted = rm.delete("skill_test")
            self.assertTrue(deleted)
            self.assertIsNone(rm.get("skill_test"))


# ═══════════════════════════════════════════════
# 4. skill_compiler 测试
# ═══════════════════════════════════════════════

class TestSkillCompiler(unittest.TestCase):
    """test SkillCompiler"""

    def setUp(self):
        from skill_compiler import SkillCompiler, CompiledArtifact, compile_skill
        self.SkillCompiler = SkillCompiler
        self.CompiledArtifact = CompiledArtifact
        self.compile_skill = compile_skill

    def test_compile_returns_artifact(self):
        compiler = self.SkillCompiler()
        skill_text = """
# test
## Step 1
Simple step.
"""
        artifact = compiler.compile(skill_text, "test")
        self.assertIsInstance(artifact, self.CompiledArtifact)
        self.assertEqual(artifact.skill_name, "test")

    def test_profile_check(self):
        compiler = self.SkillCompiler()
        profile = {"memory": True, "web_access": {"enabled": True, "level": "search"}}
        harness = {"memory": False, "web_access": {"enabled": False, "level": "none"}}
        gap = compiler.profile_check(profile, harness)
        self.assertGreater(len(gap["critical_gaps"]), 0)

    def test_env_bind(self):
        compiler = self.SkillCompiler()
        text = """
import jieba
import numpy
pip install sklearn
"""
        scripts = compiler.env_bind(text, {})
        self.assertGreater(len(scripts), 0)

    def test_skill_prune(self):
        compiler = self.SkillCompiler()
        steps = [
            {"text": "Do web search", "description": "web", "skill_name": "t", "code_blocks": []},
            {"text": "Recall memory", "description": "mem", "skill_name": "t", "code_blocks": []},
        ]
        gap = {
            "critical_gaps": ["web_access"],
            "optional_gaps": [],
            "capability_footprint": {},
        }
        pruned = compiler.skill_prune(steps, gap)
        self.assertEqual(len(pruned), 2)  # web step is pruned but still in list as note
        self.assertTrue(any("PRUNED" in s.get("text", "") for s in pruned))

    def test_compile_skill_e2e(self):
        text = """
# test-skill
## Step 1: Analyze
Simple analysis.

```python
import jieba
```

## Step 2: Search
Web search for results.
"""
        artifact = self.compile_skill(text, "test-skill")
        self.assertEqual(artifact.skill_name, "test-skill")
        self.assertIsInstance(artifact.compilation_time_ms, float)


# ═══════════════════════════════════════════════
# 5. cde_solidifier 测试
# ═══════════════════════════════════════════════

class TestCodeSolidifier(unittest.TestCase):
    """test CodeSolidifier"""

    def setUp(self):
        from cde_solidifier import CodeSolidifier, SolidifiedTemplate, solidify_from_skill
        self.CodeSolidifier = CodeSolidifier
        self.SolidifiedTemplate = SolidifiedTemplate
        self.solidify_from_skill = solidify_from_skill

    def test_scan_curl(self):
        solidifier = self.CodeSolidifier()
        text = """
```bash
curl -X GET "https://api.example.com/search?q={query}&limit={limit}"
```
"""
        templates = solidifier.scan(text)
        self.assertGreaterEqual(len(templates), 1)
        for t in templates:
            self.assertIn("query", t.param_names)

    def test_solidify(self):
        solidifier = self.CodeSolidifier()
        text = """
```bash
curl -X GET "https://api.example.com/search?q={query}&limit={limit}"
```
"""
        templates = solidifier.scan(text)
        if templates:
            t_dict = templates[0].to_dict()
            script = solidifier.solidify(t_dict, {"query": "hello", "limit": "10"})
            self.assertIn("hello", script)
            self.assertIn("10", script)

    def test_solidify_missing_param_raises(self):
        solidifier = self.CodeSolidifier()
        t = self.SolidifiedTemplate(
            description="test",
            language="bash",
            template_text="curl {query}",
            param_names=["query"],
            required_params=["query"],
        )
        with self.assertRaises(ValueError):
            solidifier.solidify(t.to_dict(), {})

    def test_detect_templates(self):
        solidifier = self.CodeSolidifier()
        text = """
```python
response = requests.get("https://api.example.com/item/{item_id}")
```
"""
        templates = solidifier.detect_templates(text)
        self.assertIsInstance(templates, list)

    def test_solidify_from_skill(self):
        text = """
```bash
curl "https://api.example.com/search?q={query}"
```
"""
        scripts = self.solidify_from_skill(text, {"query": "test"})
        self.assertGreaterEqual(len(scripts), 1)


# ═══════════════════════════════════════════════
# 6. concurrency_extractor 测试
# ═══════════════════════════════════════════════

class TestConcurrencyExtractor(unittest.TestCase):
    """test ConcurrencyExtractor"""

    def setUp(self):
        from concurrency_extractor import ConcurrencyExtractor, ConcurrencyDAG, ConcurrencyNode
        self.ConcurrencyExtractor = ConcurrencyExtractor
        self.ConcurrencyDAG = ConcurrencyDAG
        self.ConcurrencyNode = ConcurrencyNode

    def test_extract_steps(self):
        extractor = self.ConcurrencyExtractor()
        text = """
## Step 1: Fetch Data
Fetch data from API.

## Step 2: Process Results
Process the fetched data.
"""
        steps = extractor.extract_steps(text)
        self.assertGreaterEqual(len(steps), 2)

    def test_analyze_returns_dag(self):
        extractor = self.ConcurrencyExtractor()
        text = """
## Step 1: Search
Search query.

## Step 2: Index
Build index.

## Step 3: Respond
Generate response.
"""
        dag = extractor.analyze(text)
        self.assertIsInstance(dag, self.ConcurrencyDAG)
        self.assertGreaterEqual(len(dag.nodes), 1)

    def test_detect_dependencies(self):
        extractor = self.ConcurrencyExtractor()
        s1 = self.ConcurrencyNode(step_id="step_0", description="Search", text="Search the web")
        s2 = self.ConcurrencyNode(step_id="step_1", description="Process", text="Process the search results from previous step")
        deps = extractor.detect_dependencies([s1, s2])
        self.assertIn("step_1", deps)

    def test_critical_path(self):
        extractor = self.ConcurrencyExtractor()
        text = """
## Step 1: A
Step A
## Step 2: B
Depends on A
## Step 3: C
Depends on B
"""
        dag = extractor.analyze(text)
        self.assertGreater(len(dag.critical_path), 0)

    def test_parallel_groups(self):
        extractor = self.ConcurrencyExtractor()
        text = """
## Step 1: Fetch A
Fetch A data.

## Step 2: Fetch B
Fetch B data.
Independent from step 1.

## Step 3: Merge
Merge A and B results.
This depends on both Step 1 and Step 2.
"""
        dag = extractor.analyze(text)
        self.assertGreaterEqual(len(dag.parallel_groups), 1)
        self.assertGreaterEqual(dag.estimated_parallel_speedup, 1.0)


# ═══════════════════════════════════════════════
# Test Runner
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    suite = unittest.TestSuite()

    # 添加所有测试类
    test_classes = [
        TestKnowledgeAsset,
        TestMultiGranularity,
        TestCapabilityRegistry,
        TestSkillCompiler,
        TestCodeSolidifier,
        TestConcurrencyExtractor,
    ]

    for tc in test_classes:
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n{'='*60}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"{'='*60}")

    sys.exit(0 if result.wasSuccessful() else 1)
