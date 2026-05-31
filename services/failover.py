#!/usr/bin/env python3
"""
故障转移模块 (Failover Module)

提供服务故障转移和自动恢复能力。

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-23
"""

import time
import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading

logger = logging.getLogger(__name__)


class ServiceStatus(Enum):
    """服务状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ServiceEndpoint:
    """服务端点"""
    name: str
    url: str
    status: ServiceStatus = ServiceStatus.UNKNOWN
    last_check: Optional[datetime] = None
    failure_count: int = 0
    success_count: int = 0
    last_error: Optional[str] = None


class FailoverManager:
    """
    故障转移管理器

    功能:
    1. 健康检查
    2. 自动切换
    3. 故障恢复
    4. 负载均衡
    """

    def __init__(self, check_interval: int = 30, max_failures: int = 3):
        """
        初始化故障转移管理器

        Args:
            check_interval: 健康检查间隔（秒）
            max_failures: 最大失败次数
        """
        self.check_interval = check_interval
        self.max_failures = max_failures
        self.endpoints: Dict[str, ServiceEndpoint] = {}
        self._lock = threading.Lock()
        self._running = False
        self._check_thread: Optional[threading.Thread] = None

        logger.info("故障转移管理器初始化完成")

    def register_endpoint(self, name: str, url: str) -> None:
        """注册服务端点"""
        with self._lock:
            self.endpoints[name] = ServiceEndpoint(name=name, url=url)
            logger.info(f"注册服务端点: {name} ({url})")

    def unregister_endpoint(self, name: str) -> None:
        """注销服务端点"""
        with self._lock:
            if name in self.endpoints:
                del self.endpoints[name]
                logger.info(f"注销服务端点: {name}")

    def check_health(self, name: str) -> ServiceStatus:
        """检查单个服务健康状态"""
        endpoint = self.endpoints.get(name)
        if not endpoint:
            return ServiceStatus.UNKNOWN

        try:
            # 这里可以实现实际的健康检查逻辑
            # 目前简化为返回健康状态
            endpoint.status = ServiceStatus.HEALTHY
            endpoint.last_check = datetime.now()
            endpoint.success_count += 1
            endpoint.failure_count = 0
            return ServiceStatus.HEALTHY
        except Exception as e:
            endpoint.status = ServiceStatus.UNHEALTHY
            endpoint.last_check = datetime.now()
            endpoint.failure_count += 1
            endpoint.last_error = str(e)
            logger.warning(f"服务 {name} 健康检查失败: {e}")
            return ServiceStatus.UNHEALTHY

    def check_all_health(self) -> Dict[str, ServiceStatus]:
        """检查所有服务健康状态"""
        results = {}
        with self._lock:
            for name in list(self.endpoints.keys()):
                results[name] = self.check_health(name)
        return results

    def get_healthy_endpoint(self) -> Optional[ServiceEndpoint]:
        """获取一个健康的端点"""
        with self._lock:
            for endpoint in self.endpoints.values():
                if endpoint.status == ServiceStatus.HEALTHY:
                    return endpoint
        return None

    def failover(self, failed_name: str) -> Optional[ServiceEndpoint]:
        """故障转移到备用端点"""
        with self._lock:
            if failed_name in self.endpoints:
                self.endpoints[failed_name].status = ServiceStatus.UNHEALTHY

            # 查找健康的备用端点
            for name, endpoint in self.endpoints.items():
                if name != failed_name and endpoint.status == ServiceStatus.HEALTHY:
                    logger.info(f"故障转移: {failed_name} -> {name}")
                    return endpoint

        logger.warning(f"无可用的备用端点: {failed_name}")
        return None

    def start_health_check(self) -> None:
        """启动健康检查线程"""
        if self._running:
            return

        self._running = True
        self._check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self._check_thread.start()
        logger.info("健康检查线程已启动")

    def stop_health_check(self) -> None:
        """停止健康检查线程"""
        self._running = False
        if self._check_thread:
            self._check_thread.join(timeout=5)
        logger.info("健康检查线程已停止")

    def _health_check_loop(self) -> None:
        """健康检查循环"""
        while self._running:
            self.check_all_health()
            time.sleep(self.check_interval)

    def get_status(self) -> Dict[str, Any]:
        """获取故障转移状态"""
        return {
            "running": self._running,
            "check_interval": self.check_interval,
            "max_failures": self.max_failures,
            "endpoints": {
                name: {
                    "url": ep.url,
                    "status": ep.status.value,
                    "last_check": ep.last_check.isoformat() if ep.last_check else None,
                    "failure_count": ep.failure_count,
                    "success_count": ep.success_count,
                    "last_error": ep.last_error
                }
                for name, ep in self.endpoints.items()
            }
        }


# 全局实例
_failover_manager: Optional[FailoverManager] = None


def get_failover_manager() -> FailoverManager:
    """获取全局故障转移管理器"""
    global _failover_manager
    if _failover_manager is None:
        _failover_manager = FailoverManager()
    return _failover_manager


if __name__ == "__main__":
    # 测试
    manager = get_failover_manager()
    manager.register_endpoint("primary", "http://localhost:8080")
    manager.register_endpoint("backup", "http://localhost:8081")

    print("健康检查:", manager.check_all_health())
    print("状态:", manager.get_status())
