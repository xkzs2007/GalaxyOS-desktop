#!/usr/bin/env python3
"""
L5 - Governance Layer
治理审计层

职责：
- 安全验证
- 权限管理
- 审计日志
- 合规检查
"""

import os
import sys
import json
import logging
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum


# ── Centralized path resolution ──
import os as _os, sys as _sys
_ws_root = _os.environ.get("OPENCLAW_WORKSPACE", _os.path.expanduser("~/.openclaw/workspace"))
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
logger = logging.getLogger('xiaoyi-claw-omega.L5')


class Permission(Enum):
    """权限枚举"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


class AuditLevel(Enum):
    """审计级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class GovernanceLayer:
    """
    L5 - 治理审计层

    职责：
    - 安全验证
    - 权限管理
    - 审计日志
    - 合规检查
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.permissions: Dict[str, List[Permission]] = {}
        self.audit_logs: List[Dict[str, Any]] = []
        self.security_policies: Dict[str, Any] = {}
        self._initialized = False

    def start(self):
        """启动治理层"""
        logger.info("L5 Governance: 启动治理审计层")
        self._init_security_policies()
        self._init_default_permissions()
        self._initialized = True
        logger.info("L5 Governance: 治理审计层启动完成")

    def stop(self):
        """停止治理层"""
        self._save_audit_logs()
        logger.info("L5 Governance: 治理审计层已停止")

    def _init_security_policies(self):
        """初始化安全策略"""
        self.security_policies = {
            "max_execution_time": 300,  # 最大执行时间（秒）
            "max_memory_usage": 1024,   # 最大内存使用（MB）
            "allowed_operations": ["read", "write", "execute"],
            "blocked_paths": ["/etc/passwd", "/root/.ssh"],
            "require_confirmation": ["delete", "system_modify"]
        }
        logger.info("  ✅ 安全策略加载完成")

    def _init_default_permissions(self):
        """初始化默认权限"""
        self.permissions = {
            "default": [Permission.READ, Permission.EXECUTE],
            "admin": list(Permission),
            "readonly": [Permission.READ]
        }
        logger.info("  ✅ 默认权限加载完成")

    def _save_audit_logs(self):
        """保存审计日志"""
        if not self.audit_logs:
            return

        log_dir = path_resolver.OPENCLAW_HOME / "logs" / "audit"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.json"
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(self.audit_logs, f, indent=2, ensure_ascii=False)

    def check_permission(self, user: str, operation: str, resource: str) -> bool:
        """检查权限"""
        user_perms = self.permissions.get(user, self.permissions.get("default", []))

        # 简单权限检查
        if operation == "read" and Permission.READ in user_perms:
            return True
        if operation == "write" and Permission.WRITE in user_perms:
            return True
        if operation == "execute" and Permission.EXECUTE in user_perms:
            return True
        if operation == "admin" and Permission.ADMIN in user_perms:
            return True

        self.audit(
            action="permission_denied",
            user=user,
            resource=resource,
            level=AuditLevel.WARNING,
            details={"operation": operation}
        )
        return False

    def audit(self, action: str, user: str = "system", resource: str = "",
              level: AuditLevel = AuditLevel.INFO, details: Optional[Dict] = None):
        """记录审计日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "user": user,
            "resource": resource,
            "level": level.value,
            "details": details or {}
        }

        self.audit_logs.append(log_entry)
        logger.info(f"L5 Governance: 审计 [{level.value}] {action} - {resource}")

    def validate_operation(self, operation: str, params: Dict[str, Any]) -> bool:
        """验证操作"""
        # 检查是否在允许的操作列表中
        if operation not in self.security_policies.get("allowed_operations", []):
            self.audit(
                action="operation_blocked",
                level=AuditLevel.WARNING,
                details={"operation": operation, "reason": "not_allowed"}
            )
            return False

        # 检查是否需要确认
        if operation in self.security_policies.get("require_confirmation", []):
            self.audit(
                action="confirmation_required",
                level=AuditLevel.INFO,
                details={"operation": operation}
            )
            # 实际实现中需要用户确认

        return True

    def get_audit_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取审计日志"""
        return self.audit_logs[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        level_counts = {}
        for log in self.audit_logs:
            level = log.get("level", "unknown")
            level_counts[level] = level_counts.get(level, 0) + 1

        return {
            "total_logs": len(self.audit_logs),
            "level_counts": level_counts,
            "policies_count": len(self.security_policies)
        }
