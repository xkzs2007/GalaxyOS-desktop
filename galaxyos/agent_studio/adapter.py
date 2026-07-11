"""
AgentStudioAdapter — 将 GalaxyOS 的 OpenClaw 插件声明自动转换为 Agent Studio 插件声明格式。

核心职责：
1. 读取 openclaw.plugin.json，转换为 Agent Studio plugin.json 格式
2. 将 OpenClaw contracts.tools 迁移为 MCP Server 声明
3. 将 OpenClaw contracts.contextEngine/memory 迁移为 Agent Studio 生命周期钩子
4. 将 openclaw.plugin.json configSchema 迁移为 Agent Studio settings
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPENCLAW_HOOK_TO_AGENT_STUDIO_EVENT = {
    "gateway_start": "on_plugin_load",
    "gateway_stop": "on_plugin_unload",
    "before_tool_call": "on_pre_tool_use",
    "after_tool_call": "on_post_tool_use",
    "before_compaction": "on_pre_compaction",
    "after_compaction": "on_post_compaction",
    "before_agent_reply": "on_pre_agent_reply",
    "agent_end": "on_post_agent_reply",
    "before_prompt_build": "on_user_prompt_submit",
}

DEGRADED_HOOKS = {"before_compaction", "after_compaction"}

OPENCLAW_TOOL_POLICIES = {
    "galaxy_pool": {"channels": ["dm", "group"], "roles": ["owner", "member"], "rateLimit": "30/min"},
    "claw_rccam_progress": {"channels": ["dm", "group"], "roles": ["owner", "member"], "rateLimit": "60/min"},
    "claw_recall": {"channels": ["dm"], "roles": ["owner", "member"], "rateLimit": "60/min"},
    "claw_lobster": {"channels": ["dm"], "roles": ["owner"], "rateLimit": "20/min"},
    "claw_health": {"channels": ["dm", "group"], "roles": ["owner", "member"], "rateLimit": "30/min"},
    "claw_vector_info": {"channels": ["dm", "group"], "roles": ["owner", "member"], "rateLimit": "30/min"},
    "claw_events": {"channels": ["dm"], "roles": ["owner", "member"], "rateLimit": "60/min"},
    "claw_store": {"channels": ["dm"], "roles": ["owner"], "rateLimit": "30/min"},
    "claw_verify": {"channels": ["dm", "group"], "roles": ["owner", "member"], "rateLimit": "30/min"},
    "claw_rccam": {"channels": ["dm"], "roles": ["owner", "member"], "rateLimit": "20/min"},
    "claw_save_memory": {"channels": ["dm"], "roles": ["owner"], "rateLimit": "30/min"},
    "claw_compile_skill": {"channels": ["dm"], "roles": ["owner"], "rateLimit": "10/min"},
    "claw_asset_search": {"channels": ["dm", "group"], "roles": ["owner", "member"], "rateLimit": "60/min"},
    "claw_asset_register": {"channels": ["dm"], "roles": ["owner"], "rateLimit": "20/min"},
    "claw_node_invoke": {"channels": ["dm"], "roles": ["owner"], "rateLimit": "10/min"},
}


@dataclass
class MCPToolDeclaration:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookMapping:
    openclaw_hook: str
    agent_studio_event: str
    is_degraded: bool = False


@dataclass
class AgentStudioPluginManifest:
    id: str
    name: str
    description: str
    version: str
    hooks: list[HookMapping] = field(default_factory=list)
    mcp_tools: list[MCPToolDeclaration] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    mcp_server: dict[str, Any] = field(default_factory=dict)


class AgentStudioAdapter:
    """
    将 GalaxyOS 的 OpenClaw 插件声明转换为 Agent Studio 插件声明格式。

    用法：
        adapter = AgentStudioAdapter()
        manifest = adapter.convert("extensions/galaxyos/openclaw.plugin.json")
        adapter.save_plugin_json(manifest, "extensions/galaxyos/plugin.json")
    """

    def __init__(self, repo_root: str | Path | None = None):
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()

    def convert(self, openclaw_plugin_path: str | Path) -> AgentStudioPluginManifest:
        openclaw_path = self.repo_root / openclaw_plugin_path
        if not openclaw_path.exists():
            raise FileNotFoundError(f"OpenClaw plugin file not found: {openclaw_path}")

        with open(openclaw_path, "r", encoding="utf-8") as f:
            openclaw_data = json.load(f)

        hooks = self._convert_hooks(openclaw_data)
        mcp_tools = self._convert_tools(openclaw_data)
        skills = self._convert_skills()
        settings = self._convert_settings(openclaw_data)
        mcp_server = self._build_mcp_server_config(openclaw_data)

        return AgentStudioPluginManifest(
            id="galaxyos",
            name="GalaxyOS 认知增强引擎",
            description=openclaw_data.get("description", "GalaxyOS 认知增强引擎 — 记忆/检索/推理/验证/自进化"),
            version=self._read_version(),
            hooks=hooks,
            mcp_tools=mcp_tools,
            skills=skills,
            settings=settings,
            mcp_server=mcp_server,
        )

    def _convert_hooks(self, openclaw_data: dict) -> list[HookMapping]:
        hooks = []
        for openclaw_hook, agent_studio_event in OPENCLAW_HOOK_TO_AGENT_STUDIO_EVENT.items():
            hooks.append(HookMapping(
                openclaw_hook=openclaw_hook,
                agent_studio_event=agent_studio_event,
                is_degraded=openclaw_hook in DEGRADED_HOOKS,
            ))
        return hooks

    def _convert_tools(self, openclaw_data: dict) -> list[MCPToolDeclaration]:
        contracts = openclaw_data.get("contracts", {})
        tool_names = contracts.get("tools", [])
        mcp_tools = []
        for tool_name in tool_names:
            policy = OPENCLAW_TOOL_POLICIES.get(tool_name, {})
            mcp_tools.append(MCPToolDeclaration(
                name=tool_name,
                description=f"GalaxyOS cognitive tool: {tool_name}",
                parameters={},
                policy=policy,
            ))
        return mcp_tools

    def _convert_skills(self) -> list[str]:
        skills_dir = self.repo_root / "skills"
        if not skills_dir.exists():
            return []
        skill_names = []
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                skill_names.append(d.name)
        return skill_names

    def _convert_settings(self, openclaw_data: dict) -> dict[str, Any]:
        config_schema = openclaw_data.get("configSchema", {})
        return {
            "contextEngine": config_schema.get("contextEngine", {}),
            "memorySlots": config_schema.get("memorySlots", {}),
            "communication": config_schema.get("communication", {}),
            "worker": config_schema.get("worker", {}),
        }

    def _build_mcp_server_config(self, openclaw_data: dict) -> dict[str, Any]:
        return {
            "transport": "streamable_http",
            "host": "127.0.0.1",
            "port": 8765,
            "auth": {"type": "bearer", "token_env": "GALAXYOS_MCP_TOKEN"},
        }

    def _read_version(self) -> str:
        version_file = self.repo_root / "VERSION"
        if version_file.exists():
            return version_file.read_text(encoding="utf-8").strip()
        return "0.1.0"

    def save_plugin_json(self, manifest: AgentStudioPluginManifest, output_path: str | Path) -> Path:
        output = self.repo_root / output_path
        output.parent.mkdir(parents=True, exist_ok=True)

        plugin_data = {
            "id": manifest.id,
            "name": manifest.name,
            "description": manifest.description,
            "version": manifest.version,
            "hooks": [
                {
                    "event": h.agent_studio_event,
                    "openclaw_hook": h.openclaw_hook,
                    "degraded": h.is_degraded,
                }
                for h in manifest.hooks
            ],
            "mcpServers": {
                "galaxyos-cognitive": {
                    "transport": manifest.mcp_server.get("transport", "streamable_http"),
                    "host": manifest.mcp_server.get("host", "127.0.0.1"),
                    "port": manifest.mcp_server.get("port", 8765),
                    "auth": manifest.mcp_server.get("auth", {}),
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                            "policy": t.policy,
                        }
                        for t in manifest.mcp_tools
                    ],
                }
            },
            "skills": manifest.skills,
            "settings": manifest.settings,
        }

        with open(output, "w", encoding="utf-8") as f:
            json.dump(plugin_data, f, ensure_ascii=False, indent=2)

        return output

    def generate_agent_studio_plugin_json(self, openclaw_plugin_path: str | Path = "extensions/galaxyos/openclaw.plugin.json", output_path: str | Path = "extensions/galaxyos/plugin.json") -> Path:
        manifest = self.convert(openclaw_plugin_path)
        return self.save_plugin_json(manifest, output_path)