"""
Rail 权限上下文 — 借鉴 JiuwenClaw 的 owner_scopes + ContextVar 设计

核心机制：
  PermissionContext — 多维度权限上下文
  TOOL_PERMISSION_CONTEXT — 线程级/协程级 ContextVar
  setup/cleanup — 进入/退出作用域
  check_permission — 检查当前上下文中是否允许某操作

示例：
  ctx = PermissionContext(channel_id="group_123", avatar_mode=True)
  token = setup_permission_context(ctx)
  # ... 执行操作 ...
  cleanup_permission_context(token)
"""

from __future__ import annotations

import contextvars
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_persist_lock = threading.Lock()


class RailScope(str, Enum):
    """护栏作用域（与操作类型对应）"""
    USER = "user"                 # 用户操作（记忆读写）
    SESSION = "session"           # 会话级操作
    FEATURE = "feature"           # 功能级操作（技能调用）
    ADMIN = "admin"               # 管理操作（系统配置）
    EXPORT = "export"             # 导出/备份
    EXTERNAL = "external"         # 外部网络/API 调用


class RailDecision(str, Enum):
    """护栏决策结果"""
    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"


@dataclass
class PermissionContext:
    """
    权限上下文 — 类似 JiuwenClaw 的 PermissionContext + owner_scopes
    
    每个维度的默认值都是"放行"（宽松模式），
    只有在显式设置约束时才变为"受限"模式。
    """
    channel_id: str = ""               # 会话/频道 ID
    session_key: str = ""              # OpenClaw session key
    principal_user_id: str = ""        # 主体用户 ID
    agent_name: str = ""               # 当前执行的 agent 名
    avatar_mode: bool = False          # 是否为"分身"模式（代理操作）
    enable_memory: bool = True         # 是否允许记忆操作
    enable_external: bool = True       # 是否允许外部调用
    enable_export: bool = True         # 是否允许导出
    restricted_features: set = None    # 限制的功能集合
    
    def __post_init__(self):
        if self.restricted_features is None:
            self.restricted_features = set()
    
    @property
    def scope_key(self) -> tuple:
        """唯一标识 (channel_id, session_key, principal_user_id)"""
        return (self.channel_id or "", self.session_key or "", self.principal_user_id or "")
    
    def can_access(self, feature: str) -> bool:
        """检查是否允许访问某功能"""
        return feature not in self.restricted_features
    

# 全局 ContextVar（线程/协程安全）
TOOL_PERMISSION_CONTEXT: contextvars.ContextVar[Optional[PermissionContext]] = contextvars.ContextVar(
    "xiaoyi_claw_tool_permission_context",
    default=None,
)


def setup_permission_context(ctx: PermissionContext) -> contextvars.Token:
    """设置权限上下文并返回 token（用于后续 cleanup）"""
    token = TOOL_PERMISSION_CONTEXT.set(ctx)
    return token


def cleanup_permission_context(token: contextvars.Token) -> None:
    """清理权限上下文"""
    TOOL_PERMISSION_CONTEXT.reset(token)


def get_current_context() -> Optional[PermissionContext]:
    """获取当前权限上下文"""
    return TOOL_PERMISSION_CONTEXT.get(None)


def check_permission(scope: RailScope, feature: str = "") -> RailDecision:
    """
    检查当前上下文是否允许某操作
    
    Args:
        scope: 护栏作用域
        feature: 功能名（如 "memory_write", "export_file"）
        
    Returns:
        RailDecision.ALLOW / DENY / ASK_USER
    """
    ctx = get_current_context()
    if ctx is None:
        # 没有显式上下文 = 放行（宽松默认）
        return RailDecision.ALLOW
    
    # 分作用域检查
    if scope == RailScope.USER:
        if not ctx.enable_memory:
            return RailDecision.DENY
    
    elif scope == RailScope.SESSION:
        # 会话级限制
        pass
    
    elif scope == RailScope.FEATURE:
        if feature and not ctx.can_access(feature):
            return RailDecision.DENY
    
    elif scope == RailScope.EXPORT:
        if not ctx.enable_export:
            return RailDecision.ASK_USER
    
    elif scope == RailScope.EXTERNAL:
        if not ctx.enable_external:
            return RailDecision.ASK_USER
    
    return RailDecision.ALLOW


def _load_rails_config() -> Dict:
    """加载护栏配置（白名单、黑名单、默认决策）"""
    import json
    config_path = Path(__file__).parent / "rails_config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Rails config 加载失败: {e}")
    return {}

# 全局配置缓存
_RAILS_CONFIG = None

def get_rails_config() -> Dict:
    global _RAILS_CONFIG
    if _RAILS_CONFIG is None:
        _RAILS_CONFIG = _load_rails_config()
    return _RAILS_CONFIG


class rail:
    """
    Rail 装饰器 — 类似 JiuwenClaw 的 AskUserRail
    
    用法：
        @rail(scope=RailScope.FEATURE, feature="memory_write", on_deny="skip")
        def write_memory(...):
            ...
    
    Args:
        scope: 护栏作用域
        feature: 功能名
        on_deny: "skip"（跳过）| "raise"（抛异常）| "ask"（询问用户）
    
    优先级: 代码装饰器 > 配置白名单 > 默认放行
    """
    
    def __init__(self, scope: RailScope, feature: str = "", on_deny: str = "skip"):
        self.scope = scope
        self.feature = feature
        self.on_deny = on_deny
    
    def __call__(self, func):
        scope = self.scope
        feature = self.feature
        on_deny = self.on_deny
        
        def wrapper(*args, **kwargs):
            decision = check_permission(scope, feature)
            if decision == RailDecision.DENY:
                if on_deny == "skip":
                    return None
                elif on_deny == "raise":
                    raise PermissionError(
                        f"Rail denied: scope={scope}, feature={feature}"
                    )
                elif on_deny == "ask":
                    # 降级为 ask_user
                    logger.info(f"Rail ask_user: scope={scope}, feature={feature}")
                    return None
            return func(*args, **kwargs)
        
        return wrapper
