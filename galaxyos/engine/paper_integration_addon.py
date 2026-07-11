#!/usr/bin/env python3
"""
三论文集成桥接器 — RLM + SKILL0 + MemoryOS

注册到 Worker UDS 方法表，挂到编排器 pipeline。

用法:
  from paper_integration_addon import PaperIntegrationAddon
  addon = PaperIntegrationAddon(worker)
  addon.register_all()  # 注册 UDS 方法
"""

import os
import json
import sys
import logging
from pathlib import Path
from typing import Dict, Any, Optional

# ── 三方新模块 ──
from galaxyos.engine.rlm_env import RLMProcessor, FastRLMProcessor, RLMEnvironment
from galaxyos.engine.skill_curriculum import (
    SkillCurriculum, SkillValidationBridge, build_default_skill_catalog
)
from galaxyos.engine.memory_os import (
    hybrid_score, HeatTracker, SegmentedPageOrganizer
)

logger = logging.getLogger("paper_integration_addon")


class PaperIntegrationAddon:
    """
    三论文集成插件
    
    提供：
    - RLM: rlm_process / rlm_fast_process UDS 方法
    - SKILL0: skill_curriculum_step / skill_curriculum_status UDS 方法
    - MemoryOS: memory_os_* UDS 方法（热度和分段）
    """

    def __init__(self, worker=None, methods_map=None):
        self.worker = worker
        self._methods_map = methods_map  # 可选的 UDS 方法表

        # RLM
        self.rlm_processor = None
        self.fast_rlm = FastRLMProcessor()

        # SKILL0
        self.skill_curriculum = None
        self.validation_bridge = SkillValidationBridge()

        # MemoryOS
        self.heat_tracker = HeatTracker()
        self.page_organizer = SegmentedPageOrganizer()

        # 是否已注册
        self._registered = False

    def register_all(self, methods_map=None):
        """注册所有 UDS 方法和 hooks"""
        if self._registered:
            return

        if not self.worker:
            logger.warning("Worker 未提供，跳过 UDS 注册")
            return

        # 方法表来源优先级: 参数 > self._methods_map > getattr(worker, '_METHODS') > 模块级 _METHODS
        methods = methods_map or self._methods_map
        if methods is None:
            methods = getattr(self.worker, '_METHODS', None)
        if methods is None:
            import sys as _sys
            _mod = _sys.modules.get(self.worker.__class__.__module__, None)
            if _mod:
                methods = getattr(_mod, '_METHODS', None)
            if methods is None:
                # 最后兜底: 全局命名空间（测试场景）
                import builtins
                try:
                    from claw_worker import _METHODS
                except ImportError:
                    pass

        if methods is None:
            logger.warning("找不到 _METHODS 表，UDS 注册跳过")
            return

        # 获取 LLM 引用
        llm_flash = getattr(self.worker, 'llm_flash', None)
        llm_pro = getattr(self.worker, 'llm_pro', None)

        # 初始化 RLM
        self.rlm_processor = RLMProcessor(llm_flash=llm_flash, llm_pro=llm_pro)

        # 初始化 SKILL0 课程（自动加载历史状态）
        self.skill_curriculum = SkillCurriculum()
        catalog = build_default_skill_catalog()
        if not self.skill_curriculum._is_internalizing:
            self.skill_curriculum.initialize(catalog)

        # 注册 UDS 方法
        methods["rlm_process"] = self._uds_rlm_process
        methods["rlm_fast_process"] = self._uds_rlm_fast_process
        methods["skill_curriculum_step"] = self._uds_skill_step
        methods["skill_curriculum_status"] = self._uds_skill_status
        methods["memory_os_heat_status"] = self._uds_heat_status
        methods["memory_os_search"] = self._uds_memory_os_search
        methods["memory_os_hybrid_score"] = self._uds_hybrid_score
        logger.info("三论文集成: 7 个 UDS 方法已注册")

        self._registered = True
        logger.info("✅ 论文集成完成: RLM + SKILL0 + MemoryOS")

    # ── RLM UDS 方法 ──

    def _uds_rlm_process(self, p: dict) -> dict:
        """RLM 递归处理超长 prompt"""
        prompt = p.get("prompt", "")
        if not prompt:
            return {"ok": False, "error": "prompt required"}

        try:
            result = self.rlm_processor.process(prompt)
            return {"ok": True, "result": result, "len": len(result)}
        except Exception as e:
            logger.error(f"RLM 处理失败: {e}")
            return {"ok": False, "error": str(e)}

    def _uds_rlm_fast_process(self, p: dict) -> dict:
        """快速 RLM（无 LLM 调用，仅切片分段）"""
        text = p.get("text", "")
        chunk_size = p.get("chunk_size", 6000)
        if not text:
            return {"ok": False, "error": "text required"}

        try:
            self.fast_rlm.chunk_size = chunk_size
            chunks = self.fast_rlm.process(text)
            return {
                "ok": True,
                "chunks": chunks,
                "count": len(chunks),
                "total_chars": sum(len(c) for c in chunks),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── SKILL0 UDS 方法 ──

    def _uds_skill_step(self, p: dict) -> dict:
        """SKILL0 课程步进"""
        try:
            status = self.skill_curriculum.step(
                validation_fn=self.validation_bridge.validate
            )
            return {"ok": True, **status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _uds_skill_status(self, p: dict) -> dict:
        """SKILL0 课程状态"""
        try:
            status = self.skill_curriculum.get_status()
            return {"ok": True, **status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── MemoryOS UDS 方法 ──

    def _uds_heat_status(self, p: dict) -> dict:
        """热度跟踪器状态"""
        try:
            status = self.heat_tracker.get_status()
            return {"ok": True, **status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _uds_memory_os_search(self, p: dict) -> dict:
        """MemoryOS 三段搜索"""
        query = p.get("query", "")
        top_k = p.get("top_k", 5)
        if not query:
            return {"ok": False, "error": "query required"}

        try:
            results = self.page_organizer.search(query, top_k=top_k)
            return {"ok": True, "results": results, "count": len(results)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _uds_hybrid_score(self, p: dict) -> dict:
        """MemoryOS 混合评分"""
        text_a = p.get("text_a", "")
        text_b = p.get("text_b", "")
        if not text_a or not text_b:
            return {"ok": False, "error": "text_a and text_b required"}

        try:
            score = hybrid_score(text_a, text_b)
            return {"ok": True, "score": score}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# 快速集成工具函数
# ═══════════════════════════════════════════════════


def integrate_into_worker(worker, methods_map: dict = None) -> PaperIntegrationAddon:
    """
    便捷函数: 将三论文集成注入 Worker
    
    用法:
      from paper_integration_addon import integrate_into_worker
      integrate_into_worker(self, _METHODS)
    """
    addon = PaperIntegrationAddon(worker, methods_map=methods_map)
    addon.register_all()
    return addon


def patch_worker_init(worker_cls):
    """
    Monkey-patch Worker 的 __init__ 以自动注入三论文集成
    
    用于 _init_methods 之前自动注册。
    """
    orig_init = worker_cls.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        # 自动注册
        from galaxyos.engine.paper_integration_addon import integrate_into_worker
        self._paper_addon = integrate_into_worker(self)

    worker_cls.__init__ = patched_init
    return worker_cls
