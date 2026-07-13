#!/usr/bin/env python3
"""
规则整合管理器 (Rules Integration Manager)

整合所有规则配置：
- AGENTS.md 行为规则
- MEMORY.md 核心规则
- TOOLS.md 工具规则
- 配置文件规则
- 学习记录规则

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)


class RulesManager:
    """
    规则整合管理器
    
    统一管理所有规则配置和行为约束。
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or
            workspace())

        # 规则文件路径
        self.agents_md = self.workspace_path / "AGENTS.md"
        self.memory_md = self.workspace_path / "MEMORY.md"
        self.tools_md = self.workspace_path / "TOOLS.md"
        self.soul_md = self.workspace_path / "SOUL.md"
        self.user_md = self.workspace_path / "USER.md"
        self.heartbeat_md = self.workspace_path / "HEARTBEAT.md"

        # 配置文件路径
        self.config_dir = self.workspace_path / "skills/galaxyos-engine/config"
        self.learnings_dir = self.workspace_path / ".learnings"
        self.memory_dir = self.workspace_path / "memory"

        # 腾讯云配置
        self.tencentdb_config = Path.home() / ".openclaw/memory-tdai/config/extension_config.json"

        # 求是技能路径
        self.qiushi_skill_dir = self.workspace_path / "skills/qiushi-skill"

        logger.info("规则管理器初始化完成")

    def get_all_rules(self) -> Dict:
        """获取所有规则"""
        rules = {
            "core_rules": self._extract_core_rules(),
            "behavior_rules": self._extract_behavior_rules(),
            "tool_rules": self._extract_tool_rules(),
            "config_rules": self._extract_config_rules(),
            "learning_rules": self._extract_learning_rules(),
            "qiushi_rules": self._extract_qiushi_rules(),
            "session_state": self._get_session_state(),
        }

        return rules

    def _extract_core_rules(self) -> Dict:
        """从 MEMORY.md 提取核心规则"""
        rules = {}

        if not self.memory_md.exists():
            return rules

        content = self.memory_md.read_text(encoding="utf-8")

        # 直接检查关键词
        rules["version_control"] = "本地源码修改不同步远程仓库" in content
        rules["data_warehouse"] = "gitee.com/xkzs2007/xkzs" in content
        rules["architecture_x64"] = "x64" in content
        rules["architecture_no_arm64"] = "拒绝 ARM64" in content or "拒绝ARM64" in content
        rules["backup_huawei"] = "华为云盘" in content
        rules["auto_recall"] = "混合模式" in content or "auto_recall" in content
        rules["push_trigger"] = "智能触发模式" in content

        return rules

    def _extract_behavior_rules(self) -> Dict:
        """从 AGENTS.md 和 SOUL.md 提取行为规则"""
        rules = {}

        # AGENTS.md
        if self.agents_md.exists():
            content = self.agents_md.read_text(encoding="utf-8")

            rules["memory_priority"] = "MEMORY.md" in content
            rules["tdai_search_required"] = "tdai_memory_search" in content
            rules["reflection_enabled"] = "reflection" in content.lower()
            rules["skill_coordinator"] = "skill_coordinator" in content
            rules["execution_validator"] = "execution-validator" in content
            rules["secret_guardian"] = "secret-guardian" in content
            rules["heartbeat_enabled"] = "HEARTBEAT.md" in content

        # SOUL.md
        if self.soul_md.exists():
            content = self.soul_md.read_text(encoding="utf-8")

            rules["qiqing_liuyu"] = "qiqing-liuyu" in content
            rules["no_dash"] = "破折号零容忍" in content
            rules["chinese_style"] = "中国化表达" in content
            rules["emotion_triggers"] = "吐槽触发" in content or "共情触发" in content

        return rules

    def _extract_tool_rules(self) -> Dict:
        """从 TOOLS.md 提取工具规则"""
        rules = {}

        if not self.tools_md.exists():
            return rules

        content = self.tools_md.read_text(encoding="utf-8")

        rules["web_search_default"] = "xiaoyi-web-search" in content
        rules["gui_agent_rules"] = "xiaoyi-gui-agent" in content
        rules["find_skills_default"] = "find-skills" in content
        rules["doc_convert_default"] = "xiaoyi-doc-convert" in content
        rules["image_understanding_default"] = "xiaoyi-image-understanding" in content
        rules["file_return_default"] = "send_file_to_user" in content
        rules["cron_channel_required"] = "cron" in content and "channel" in content
        rules["git_download_dir"] = "OPENCLAW_GIT_DIR" in content

        return rules

    def _extract_config_rules(self) -> Dict:
        """从配置文件提取规则"""
        rules = {}

        # 系统配置
        system_config = self.config_dir / "system_config.json"
        if system_config.exists():
            try:
                config = json.loads(system_config.read_text())
                rules["layers"] = list(config.get("layers", {}).keys())
                rules["features"] = config.get("features", {})
            except:
                pass

        # 腾讯云配置
        if self.tencentdb_config.exists():
            try:
                config = json.loads(self.tencentdb_config.read_text())
                rules["search_engines"] = list(config.get("extensions", {}).keys())
                rules["default_engine"] = config.get("search", {}).get("default_engine")
                rules["fallback_order"] = config.get("search", {}).get("fallback_order", [])
            except:
                pass

        # 记忆参数
        params_file = self.learnings_dir / "memory_params.json"
        if params_file.exists():
            try:
                params = json.loads(params_file.read_text())
                rules["memory_params"] = params
            except:
                pass

        return rules

    def _extract_learning_rules(self) -> Dict:
        """从学习记录提取规则"""
        rules = {}

        # 错误模式
        patterns_file = self.learnings_dir / "PATTERNS.jsonl"
        if patterns_file.exists():
            patterns = []
            for line in patterns_file.read_text().strip().split("\n"):
                if line:
                    try:
                        patterns.append(json.loads(line))
                    except:
                        pass
            rules["error_patterns"] = patterns

        # 反思记录
        reflections_file = self.learnings_dir / "REFLECTIONS.jsonl"
        if reflections_file.exists():
            count = len(reflections_file.read_text().strip().split("\n"))
            rules["reflections_count"] = count

        return rules

    def _extract_qiushi_rules(self) -> Dict:
        """从求是技能提取方法论规则"""
        rules = {}

        if not self.qiushi_skill_dir.exists():
            return rules

        # 检查九大思想武器
        skills_dir = self.qiushi_skill_dir / "skills"
        if skills_dir.exists():
            weapons = {
                "contradiction-analysis": "矛盾分析法",
                "practice-cognition": "实践认识论",
                "investigation-first": "调查研究",
                "mass-line": "群众路线",
                "criticism-self-criticism": "批评与自我批评",
                "protracted-strategy": "持久战略",
                "concentrate-forces": "集中兵力",
                "spark-prairie-fire": "星火燎原",
                "overall-planning": "统筹兼顾",
            }

            installed_weapons = []
            for weapon_dir, weapon_name in weapons.items():
                skill_file = skills_dir / weapon_dir / "SKILL.md"
                if skill_file.exists():
                    installed_weapons.append(weapon_name)

            rules["installed_weapons"] = installed_weapons
            rules["weapons_count"] = len(installed_weapons)
            rules["total_weapons"] = 9

        # 检查武装思想入口
        arming_thought = skills_dir / "arming-thought" / "SKILL.md"
        if arming_thought.exists():
            rules["arming_thought_enabled"] = True

        # 检查工作流
        workflows = skills_dir / "workflows" / "SKILL.md"
        if workflows.exists():
            rules["workflows_enabled"] = True

        # 总原则
        rules["core_principle"] = "实事求是"
        rules["methodology_source"] = "毛选（第一至五卷）"

        return rules

    def _get_session_state(self) -> Dict:
        """获取会话状态"""
        state = {}

        session_file = self.memory_dir / "SESSION-STATE.md"
        if session_file.exists():
            content = session_file.read_text(encoding="utf-8")

            # 解析当前任务
            if "**ID:**" in content:
                start = content.find("**ID:**") + 7
                end = content.find("\n", start)
                state["current_task_id"] = content[start:end].strip()

            if "**Status:**" in content:
                start = content.find("**Status:**") + 11
                end = content.find("\n", start)
                state["current_task_status"] = content[start:end].strip()

            if "**Progress:**" in content:
                start = content.find("**Progress:**") + 13
                end = content.find("\n", start)
                state["goal_progress"] = content[start:end].strip()

        return state

    def get_rule_summary(self) -> str:
        """获取规则摘要"""
        rules = self.get_all_rules()

        summary = []
        summary.append("=" * 60)
        summary.append("规则整合摘要")
        summary.append("=" * 60)

        # 核心规则
        summary.append("\n【核心规则】")
        core = rules.get("core_rules", {})
        if core.get("version_control"):
            summary.append("  ✅ 版本控制: 本地修改不同步远程")
        if core.get("data_warehouse"):
            summary.append("  ✅ 数据仓库: gitee.com/xkzs2007/xkzs")
        if core.get("architecture"):
            summary.append("  ✅ 架构偏好: x64，拒绝 ARM64")
        if core.get("backup"):
            summary.append("  ✅ 数据备份: 华为云盘")

        # 行为规则
        summary.append("\n【行为规则】")
        behavior = rules.get("behavior_rules", {})
        if behavior.get("tdai_search_required"):
            summary.append("  ✅ 记忆检索: 必须使用 tdai_memory_search")
        if behavior.get("execution_validator"):
            summary.append("  ✅ 执行验证: 必须使用 execution-validator")
        if behavior.get("secret_guardian"):
            summary.append("  ✅ 密钥守护: secret-guardian 已启用")
        if behavior.get("qiqing_liuyu"):
            summary.append("  ✅ 表达风格: 七情六欲规则")

        # 工具规则
        summary.append("\n【工具规则】")
        tools = rules.get("tool_rules", {})
        if tools.get("web_search_default"):
            summary.append("  ✅ 联网搜索: xiaoyi-web-search")
        if tools.get("gui_agent_rules"):
            summary.append("  ✅ 手机操控: xiaoyi-gui-agent")
        if tools.get("find_skills_default"):
            summary.append("  ✅ 技能发现: find-skills")

        # 配置规则
        summary.append("\n【配置规则】")
        config = rules.get("config_rules", {})
        if config.get("layers"):
            summary.append(f"  ✅ 六层架构: {len(config['layers'])} 层")
        if config.get("search_engines"):
            summary.append(f"  ✅ 搜索引擎: {', '.join(config['search_engines'])}")
        if config.get("memory_params"):
            params = config["memory_params"]
            summary.append(f"  ✅ 记忆参数: 召回阈值={params.get('recall_threshold')}")

        # 求是方法论规则
        summary.append("\n【求是方法论】")
        qiushi = rules.get("qiushi_rules", {})
        if qiushi.get("core_principle"):
            summary.append(f"  ☀️ 总原则: {qiushi['core_principle']}")
        if qiushi.get("weapons_count"):
            summary.append(f"  ⚔️ 思想武器: {qiushi['weapons_count']}/9 已安装")
        if qiushi.get("installed_weapons"):
            weapons_str = "、".join(qiushi["installed_weapons"][:5])
            if len(qiushi["installed_weapons"]) > 5:
                weapons_str += f" 等{len(qiushi['installed_weapons'])}个"
            summary.append(f"  📜 已安装: {weapons_str}")
        if qiushi.get("arming_thought_enabled"):
            summary.append("  🧠 武装思想: 已启用")
        if qiushi.get("workflows_enabled"):
            summary.append("  🔗 工作流: 已启用")

        # 会话状态
        summary.append("\n【会话状态】")
        state = rules.get("session_state", {})
        if state.get("current_task_id"):
            summary.append(f"  📋 当前任务: {state['current_task_id']}")
            summary.append(f"     状态: {state.get('current_task_status', 'unknown')}")
        if state.get("goal_progress"):
            summary.append(f"  🎯 目标进度: {state['goal_progress']}")

        return "\n".join(summary)

    def validate_rules(self) -> Dict:
        """验证规则完整性"""
        issues = []

        # 检查必要文件
        required_files = [
            ("AGENTS.md", self.agents_md),
            ("MEMORY.md", self.memory_md),
            ("TOOLS.md", self.tools_md),
            ("SOUL.md", self.soul_md),
        ]

        for name, path in required_files:
            if not path.exists():
                issues.append(f"缺少必要文件: {name}")

        # 检查核心规则
        rules = self.get_all_rules()
        core = rules.get("core_rules", {})

        if not core.get("version_control"):
            issues.append("核心规则缺失: 版本控制策略")
        if not core.get("data_warehouse"):
            issues.append("核心规则缺失: 数据仓库配置")

        # 检查行为规则
        behavior = rules.get("behavior_rules", {})

        if not behavior.get("execution_validator"):
            issues.append("安全规则缺失: execution-validator")
        if not behavior.get("secret_guardian"):
            issues.append("安全规则缺失: secret-guardian")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "checked_at": datetime.now().isoformat()
        }


# CLI 接口
def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="规则整合管理器")
    parser.add_argument("command", choices=["summary", "validate", "export"])
    parser.add_argument("--output", help="输出文件路径")

    args = parser.parse_args()

    manager = RulesManager()

    if args.command == "summary":
        print(manager.get_rule_summary())

    elif args.command == "validate":
        result = manager.validate_rules()
        if result["valid"]:
            print("✅ 规则验证通过")
        else:
            print("❌ 发现问题:")
            for issue in result["issues"]:
                print(f"  - {issue}")

    elif args.command == "export":
        rules = manager.get_all_rules()
        output_path = args.output or "rules_export.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)

        print(f"✅ 规则已导出到: {output_path}")


if __name__ == "__main__":
    main()
