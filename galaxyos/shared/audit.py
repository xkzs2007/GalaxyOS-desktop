"""GalaxyOS 审计日志模块

提供 AuditLogger 类，用于记录关键操作（记忆删除、配置变更等）的审计事件。

特性：
  - JSON Lines 追加写入 $GALAXYOS_HOME/logs/audit/audit-YYYY-MM-DD.jsonl
  - 按时间/操作类型/操作者筛选
  - 磁盘写入失败降级为 sys.stderr
  - AuditEvent 字段：timestamp(ISO 8601)、operator、action、scope、result
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import List, Optional

from galaxyos.shared.paths import audit_log_dir


@dataclass
class AuditEvent:
    timestamp: str = ""
    operator: str = ""
    action: str = ""
    scope: str = ""
    result: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class AuditLogger:
    def __init__(self, log_dir: Optional[str] = None):
        self._log_dir = log_dir or audit_log_dir()
        self._lock = threading.Lock()
        self._fallback_stderr = False

    def _log_path(self) -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return os.path.join(self._log_dir, f"audit-{date_str}.jsonl")

    def log(self, event: AuditEvent) -> None:
        if not event.timestamp:
            event.timestamp = datetime.now(timezone.utc).isoformat()
        line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
        with self._lock:
            try:
                os.makedirs(self._log_dir, exist_ok=True)
                with open(self._log_path(), "a", encoding="utf-8") as f:
                    f.write(line)
                if self._fallback_stderr:
                    self._fallback_stderr = False
            except OSError:
                if not self._fallback_stderr:
                    self._fallback_stderr = True
                try:
                    sys.stderr.write(f"[audit-fallback] {line}")
                    sys.stderr.flush()
                except Exception:
                    pass

    def query(
        self,
        *,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        action: Optional[str] = None,
        operator: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        results: List[AuditEvent] = []
        try:
            files = sorted(
                f
                for f in os.listdir(self._log_dir)
                if f.startswith("audit-") and f.endswith(".jsonl")
            )
        except OSError:
            return results

        for fname in reversed(files):
            fpath = os.path.join(self._log_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue

            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if start_time and obj.get("timestamp", "") < start_time:
                    continue
                if end_time and obj.get("timestamp", "") > end_time:
                    continue
                if action and obj.get("action") != action:
                    continue
                if operator and obj.get("operator") != operator:
                    continue

                results.append(AuditEvent(**obj))
                if len(results) >= limit:
                    return results

        return results


_logger_instance: Optional[AuditLogger] = None
_logger_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    global _logger_instance
    if _logger_instance is None:
        with _logger_lock:
            if _logger_instance is None:
                _logger_instance = AuditLogger()
    return _logger_instance
