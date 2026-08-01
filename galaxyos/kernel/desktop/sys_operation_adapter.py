from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SysOperationMcpAdapter:
    def __init__(self) -> None:
        self._sys_op: Optional[Any] = None
        self._available = False

    async def initialize(self, workspace_root: Optional[str] = None) -> bool:
        try:
            from openjiuwen.core.foundation.sys_operation import SysOperation, SysOperationCard
            from openjiuwen.core.foundation.sys_operation.models import OperationMode, LocalWorkConfig

            card = SysOperationCard(
                id="galaxyos_desktop",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(
                    sandbox_root=[workspace_root or "."],
                    restrict_to_sandbox=False,
                ),
            )
            self._sys_op = SysOperation(card)
            self._available = True
            logger.info("SysOperation initialized in LOCAL mode")
            return True
        except ImportError as e:
            logger.warning(f"openjiuwen SysOperation not available ({e})")
            self._available = False
            return False
        except Exception as e:
            logger.error(f"SysOperation initialization failed: {e}")
            self._available = False
            return False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def sys_op(self) -> Optional[Any]:
        return self._sys_op

    def adapt_tools(self) -> List[Tuple[str, Callable]]:
        if not self._available or not self._sys_op:
            return []

        tools: List[Tuple[str, Callable]] = []
        tools.extend(self._adapt_fs_tools())
        tools.extend(self._adapt_shell_tools())
        tools.extend(self._adapt_code_tools())
        return tools

    def _adapt_fs_tools(self) -> List[Tuple[str, Callable]]:
        op = self._sys_op

        async def desktop_fs_read(path: str, mode: str = "text", head: int = 0, tail: int = 0, encoding: str = "utf-8") -> str:
            t0 = time.time()
            try:
                result = await op.fs().read_file(path=path, mode=mode, head=head, tail=tail, encoding=encoding)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_fs_read", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_fs_read", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_fs_write(path: str, content: str, mode: str = "write", encoding: str = "utf-8") -> str:
            t0 = time.time()
            try:
                result = await op.fs().write_file(path=path, content=content, mode=mode, encoding=encoding)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_fs_write", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_fs_write", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_fs_list(path: str = ".", recursive: bool = False, pattern: str = "") -> str:
            t0 = time.time()
            try:
                result = await op.fs().list_dir(path=path, recursive=recursive, pattern=pattern)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_fs_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_fs_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_fs_search(path: str = ".", pattern: str = "*") -> str:
            t0 = time.time()
            try:
                result = await op.fs().search(path=path, pattern=pattern)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_fs_search", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_fs_search", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_fs_move(source: str, destination: str) -> str:
            t0 = time.time()
            try:
                result = await op.fs().move(source=source, destination=destination)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_fs_move", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_fs_move", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_fs_delete(path: str, confirm: bool = False) -> str:
            t0 = time.time()
            try:
                if not confirm:
                    return json.dumps({"success": False, "error": "Delete requires explicit confirmation (confirm=True)", "tool_name": "desktop_fs_delete"}, ensure_ascii=False)
                result = await op.fs().delete(path=path)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_fs_delete", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_fs_delete", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        return [
            ("desktop_fs_read", desktop_fs_read),
            ("desktop_fs_write", desktop_fs_write),
            ("desktop_fs_list", desktop_fs_list),
            ("desktop_fs_search", desktop_fs_search),
            ("desktop_fs_move", desktop_fs_move),
            ("desktop_fs_delete", desktop_fs_delete),
        ]

    def _adapt_shell_tools(self) -> List[Tuple[str, Callable]]:
        op = self._sys_op

        async def desktop_shell_exec(command: str, cwd: str = "", timeout: int = 30, shell_type: str = "auto") -> str:
            t0 = time.time()
            try:
                result = await op.shell().execute_cmd(command=command, cwd=cwd or None, timeout=timeout)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_shell_exec", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_shell_exec", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_shell_exec_stream(command: str, cwd: str = "", timeout: int = 60, shell_type: str = "auto") -> str:
            t0 = time.time()
            try:
                result = await op.shell().execute_cmd(command=command, cwd=cwd or None, timeout=timeout)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_shell_exec_stream", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_shell_exec_stream", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        async def desktop_shell_background(command: str, cwd: str = "") -> str:
            t0 = time.time()
            try:
                result = await op.shell().execute_cmd(command=command, cwd=cwd or None, timeout=0)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_shell_background", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_shell_background", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        return [
            ("desktop_shell_exec", desktop_shell_exec),
            ("desktop_shell_exec_stream", desktop_shell_exec_stream),
            ("desktop_shell_background", desktop_shell_background),
        ]

    def _adapt_code_tools(self) -> List[Tuple[str, Callable]]:
        op = self._sys_op

        async def desktop_code_exec(code: str, language: str = "python", timeout: int = 30) -> str:
            t0 = time.time()
            try:
                result = await op.code().execute_code(code=code, language=language, timeout=timeout)
                return json.dumps({"success": True, "data": result, "tool_name": "desktop_code_exec", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_code_exec", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        return [
            ("desktop_code_exec", desktop_code_exec),
        ]
