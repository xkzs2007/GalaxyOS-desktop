"""
SwarmAgentServerBridge — JiuwenSwarm AgentServer 子进程生命周期管理

管理 JiuwenSwarm AgentServer 进程的启动、停止、健康检查和 WebSocket URL 获取。
AgentServer 是 JiuwenSwarm 的核心运行时，运行 Agent + WebSocket Server（端口 19000）。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class ProcessSpawnError(Exception):
    pass


class HealthTimeoutError(Exception):
    pass


@dataclass
class SwarmHealthStatus:
    running: bool
    pid: Optional[int]
    websocket_url: str
    uptime_s: float
    restart_count: int


class SwarmAgentServerBridge:
    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 19000
    MAX_RESTART_ATTEMPTS = 3
    HEALTH_CHECK_TIMEOUT = 60
    SHUTDOWN_TIMEOUT = 5.0
    BACKOFF_BASE = 2.0
    BACKOFF_MAX = 10.0

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        dotenv_path: str = "",
    ):
        self._host = host
        self._port = port
        self._dotenv_path = dotenv_path
        self._process: Optional[subprocess.Popen] = None
        self._restart_count = 0
        self._start_time: float = 0
        self._running = False

    async def start(self) -> None:
        try:
            cmd = [sys.executable, "-m", "jiuwenswarm.server.app_agentserver"]

            env = {
                **dict(__import__("os").environ),
                "GALAXYOS_MODE": "desktop",
                "AGENTSERVER_HOST": self._host,
                "AGENTSERVER_PORT": str(self._port),
            }

            if self._dotenv_path:
                env["DOTENV_PATH"] = self._dotenv_path

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            self._running = True
            self._start_time = time.time()
            self._restart_count = 0
            logger.info(f"JiuwenSwarm AgentServer starting: host={self._host}, port={self._port}")

            healthy = await self.check_health()
            if not healthy:
                raise HealthTimeoutError(
                    f"AgentServer health check timed out after {self.HEALTH_CHECK_TIMEOUT}s"
                )

            logger.info(f"JiuwenSwarm AgentServer ready on port {self._port}")

        except HealthTimeoutError:
            self._running = False
            raise
        except Exception as e:
            self._running = False
            raise ProcessSpawnError(f"Failed to start AgentServer: {e}") from e

    async def stop(self) -> None:
        if not self._process or self._process.poll() is not None:
            self._running = False
            return

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=self.SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._process.kill()
                logger.warning("AgentServer force-killed after timeout")
        except Exception as e:
            logger.warning(f"Error stopping AgentServer: {e}")
        finally:
            self._running = False

    async def check_health(self) -> bool:
        import aiohttp

        url = f"http://{self._host}:{self._port}/health"
        for i in range(self.HEALTH_CHECK_TIMEOUT):
            if not self._process or self._process.poll() is not None:
                return False
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                        if resp.status == 200:
                            logger.info(f"AgentServer health check passed after {i}s")
                            return True
            except Exception:
                pass
            await asyncio.sleep(1)

        return False

    def get_websocket_url(self) -> str:
        return f"ws://{self._host}:{self._port}/ws"

    def get_stats(self) -> dict:
        return {
            "running": self._running and self._process is not None and self._process.poll() is None,
            "host": self._host,
            "port": self._port,
            "websocket_url": self.get_websocket_url(),
            "pid": self._process.pid if self._process else None,
            "restart_count": self._restart_count,
            "uptime_s": round(time.time() - self._start_time, 1) if self._start_time else 0,
        }

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None