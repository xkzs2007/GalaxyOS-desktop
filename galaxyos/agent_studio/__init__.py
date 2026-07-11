"""
GalaxyOS Agent Studio Integration

Agent Studio 作为底层框架，GalaxyOS 通过 MCP 协议作为认知增强层注入。

核心模块：
  - adapter: AgentStudioAdapter — OpenClaw 插件声明 -> Agent Studio 插件声明自动转换
  - plugin: GalaxyOSPlugin — Agent Studio 插件入口
  - lifecycle: 生命周期钩子映射（9 钩子 -> Agent Studio 事件）
"""

__version__ = "0.1.0"