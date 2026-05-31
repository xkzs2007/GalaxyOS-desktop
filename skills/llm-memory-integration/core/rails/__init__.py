"""
小艺 Claw Rails 护栏系统 — 借鉴 JiuwenClaw 的权限 RPC + 作用域设计

核心机制：
- RailContextVar: 线程级/协程级权限上下文
- rail decorator: 给模块/函数挂护栏检查点
- owner_scopes: 多维度权限作用域（chat_id/session/feature）
- ask_user_rail: 结构化问题 + 选项交互

设计目标：
- 轻量：不引入新的 RPC 框架，用 ContextVar + 装饰器
- 兼容：不破坏现有 16 层架构
- 渐进：各模块独立开启护栏
"""
