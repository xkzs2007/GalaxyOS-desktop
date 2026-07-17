#!/usr/bin/env python3
"""
L4 - Execution Layer

⚠️  ARCHITECTURE WARNING (F-12) ⚠️
本文件是【空壳实现】。
- SkillAdapter.load() 读 config 后丢失（行 56-59 存到局部变量不存到 self）
- SkillAdapter.execute() 永远返回假字符串 {"status": "success", "result": "执行成功"}
- 51 个 skills 中 80+ 个 .py 脚本（generate_seedream.py / image_understanding.py / 等）
  **没有任何一个会被本 gateway 触发**

【症状】: 用户调 claw_compile_skill / claw_asset_search / claw_asset_register 工具，插件返回假 success
【建议】: 重写本模块让它真正解析 SKILL.md + 调用对应 skill 脚本，或在 README 中明确标注"暂未实现"。
技能执行层

职责：
- 技能适配
- 执行网关
- 结果验证
- 错误处理
"""

import os
import sys
import json
import logging
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from enum import Enum


# ── Centralized path resolution ──
import os as _os, sys as _sys
_ws_root = _os.environ.get("OPENCLAW_WORKSPACE", _os.path.expanduser("~/.openclaw/workspace"))
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
logger = logging.getLogger('galaxyos.L4')


class ExecutionStatus(Enum):
    """执行状态"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SkillAdapter:
    """技能适配器"""

    def __init__(self, skill_id: str, skill_path: Path):
        self.skill_id = skill_id
        self.skill_path = skill_path
        self.config: Dict[str, Any] = {}
        self.handler: Optional[Callable] = None
        self._loaded = False

    def load(self) -> bool:
        """加载技能"""
        try:
            # 加载配置
            config_file = self.skill_path / "config.json"
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)

            # 加载 SKILL.md
            skill_file = self.skill_path / "SKILL.md"
            if skill_file.exists():
                self._loaded = True
                return True

            return False
        except Exception as e:
            logger.error(f"加载技能失败 {self.skill_id}: {e}")
            return False

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行技能"""
        if not self._loaded:
            return {
                "status": ExecutionStatus.FAILED.value,
                "error": "技能未加载"
            }

        return {
            "status": ExecutionStatus.SUCCESS.value,
            "skill_id": self.skill_id,
            "params": params,
            "result": "执行成功"
        }


class ExecutionLayer:
    """
    L4 - 技能执行层

    职责：
    - 技能适配和加载
    - 执行网关
    - 结果验证
    - 错误处理
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.skills: Dict[str, SkillAdapter] = {}
        self.execution_history: List[Dict[str, Any]] = []
        self._initialized = False

    def start(self):
        """启动执行层"""
        logger.info("L4 Execution: 启动技能执行层")
        self._load_skills()
        self._initialized = True
        logger.info("L4 Execution: 技能执行层启动完成")

    def stop(self):
        """停止执行层"""
        logger.info("L4 Execution: 技能执行层已停止")

    def _load_skills(self):
        """加载技能"""
        skills_dir = path_resolver.SKILLS_DIR

        if not skills_dir.exists():
            logger.warning("  ⚠️ 技能目录不存在")
            return

        loaded_count = 0
        for skill_path in skills_dir.iterdir():
            if skill_path.is_dir() and (skill_path / "SKILL.md").exists():
                adapter = SkillAdapter(skill_path.name, skill_path)
                if adapter.load():
                    self.skills[skill_path.name] = adapter
                    loaded_count += 1

        logger.info(f"  ✅ 加载技能: {loaded_count} 个")

    def execute(self, skill_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行技能"""
        start_time = datetime.now()

        # 查找技能
        adapter = self.skills.get(skill_id)
        if not adapter:
            result = {
                "status": ExecutionStatus.FAILED.value,
                "error": f"技能不存在: {skill_id}"
            }
        else:
            result = adapter.execute(params)

        # 记录执行历史
        execution_record = {
            "skill_id": skill_id,
            "params": params,
            "result": result,
            "started_at": start_time.isoformat(),
            "completed_at": datetime.now().isoformat(),
            "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000)
        }
        self.execution_history.append(execution_record)

        logger.info(f"L4 Execution: 执行技能 {skill_id} - {result.get('status')}")
        return result

    def get_skill(self, skill_id: str) -> Optional[SkillAdapter]:
        """获取技能适配器"""
        return self.skills.get(skill_id)

    def list_skills(self) -> List[str]:
        """列出所有技能"""
        return list(self.skills.keys())

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        success_count = sum(1 for h in self.execution_history
                          if h.get("result", {}).get("status") == "success")

        return {
            "total_skills": len(self.skills),
            "total_executions": len(self.execution_history),
            "success_count": success_count,
            "failed_count": len(self.execution_history) - success_count
        }
