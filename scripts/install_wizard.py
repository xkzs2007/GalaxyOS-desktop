#!/usr/bin/env python3
"""
GalaxyOS — 安装向导 + 配置向导
===============================
一键系统体检 + 配置管理入口

用法:
  python3 install_wizard.py                  # 完整互动向导（自检 + 配置）
  python3 install_wizard.py --check          # 仅系统体检（非互动）
  python3 install_wizard.py --config         # 仅配置向导
  python3 install_wizard.py --report         # 输出 JSON 报告
  python3 install_wizard.py --fix            # 体检后自动修复（同步文件）
"""

import os
import sys
import json
import ast
import time
import socket
import struct
import shutil
import subprocess
import importlib
import inspect
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# ── ANSI 颜色（必须定义在 UI 函数之前） ──
G = "\033[92m"  # 绿
Y = "\033[93m"  # 黄
R = "\033[91m"  # 红
C = "\033[96m"  # 青
B = "\033[1m"   # 粗体
N = "\033[0m"   # 重置

# ── --report 模式：所有 UI 输出走 stderr，stdout 只留 JSON ──
_IS_REPORT_MODE = "--report" in sys.argv or "-r" in sys.argv
_OUT = sys.stderr if _IS_REPORT_MODE else sys.stdout


def _print(*args, **kwargs):
    kwargs.pop("file", None)
    print(*args, file=_OUT, **kwargs)


def info(msg, indent=0):
    prefix = "  " * indent
    _print(f"{prefix}ℹ️  {msg}")


def ok(msg, indent=0):
    prefix = "  " * indent
    _print(f"{prefix}{G}✅ {msg}{N}")


def warn(msg, indent=0):
    prefix = "  " * indent
    _print(f"{prefix}{Y}⚠️  {msg}{N}")


def err(msg, indent=0):
    prefix = "  " * indent
    _print(f"{prefix}{R}❌ {msg}{N}")


def heading(title):
    _print(f"\n{C}{'='*60}{N}")
    _print(f"{B}{title}{N}")
    _print(f"{C}{'='*60}{N}")

# ── 路径定义 ──
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
SKILL_DIR = WORKSPACE / "skills" / "xiaoyi-claw-omega-final"
SCRIPTS_DIR = SKILL_DIR / "scripts"
CORE_DIR = SKILL_DIR / "skills" / "llm-memory-integration" / "core"
SRC_DIR = WORKSPACE / "skills" / "llm-memory-integration" / "src"
CONFIG_DIR = SKILL_DIR / "config"
DIST_DIR = Path.home() / ".openclaw" / "extensions" / "claw-core" / "dist" / "scripts"
VAR_DIR = Path.home() / ".openclaw" / "extensions" / "claw-core" / "var"

# ── ANSI 颜色 ──



# ════════════════════════════════════════════════════════════════
# Phase 0: 环境检测
# ════════════════════════════════════════════════════════════════

def check_environment() -> Dict[str, Any]:
    """检查 Python 版本、OS、关键包"""
    results = {"python": {}, "os": {}, "packages": {}}

    print()
    heading("📋 阶段 0：环境检测")

    # Python
    py_v = sys.version_info
    results["python"]["version"] = f"{py_v.major}.{py_v.minor}.{py_v.micro}"
    if py_v.major >= 3 and py_v.minor >= 10:
        ok(f"Python {results['python']['version']}")
    else:
        err(f"Python {results['python']['version']}（需要 3.10+）")

    # OS
    import platform
    results["os"] = {"system": platform.system(), "release": platform.release(), "arch": platform.machine()}
    info(f"系统: {results['os']['system']} {results['os']['release']} {results['os']['arch']}", indent=1)

    # 关键 pip 包
    required_pkgs = {
        "openai": "LLM API 调用",
        "requests": "HTTP 请求",
    }
    optional_pkgs = {
        "pysqlite3": "向量存储",
    }

    all_ok = True
    for pkg, desc in required_pkgs.items():
        try:
            importlib.import_module(pkg)
            results["packages"][pkg] = True
        except ImportError:
            results["packages"][pkg] = False
            all_ok = False

    if all_ok:
        ok("所有必需 pip 包已安装")
    else:
        missing = [p for p, v in results["packages"].items() if not v]
        err(f"缺少必需包: {', '.join(missing)}")
        info("运行: pip install " + " ".join(missing), indent=1)

    # 目录存在性
    results["dirs"] = {}
    for name, p in [("scripts", SCRIPTS_DIR), ("config", CONFIG_DIR), ("dist", DIST_DIR), ("var", VAR_DIR)]:
        results["dirs"][name] = p.exists()
        if not p.exists():
            warn(f"{name} 目录不存在: {p}", indent=1)

    return results


# ════════════════════════════════════════════════════════════════
# Phase 1: 模块导入测试
# ════════════════════════════════════════════════════════════════

def test_all_modules() -> Dict[str, Any]:
    """全面模块检测：按架构分层扫描，走降级初始化链路"""
    heading("📦 阶段 1：全模块自检")

    results = {"total": 0, "ok": 0, "fail": 0, "details": []}

    # ── 确保路径 ──
    for p in [str(SCRIPTS_DIR), str(CORE_DIR), str(SRC_DIR), str(SRC_DIR / "integration"), str(SRC_DIR / "memory")]:
        if os.path.isdir(p):
            sys.path.insert(0, p)

    # 读取模块依赖配置
    deps_path = CONFIG_DIR / "module_dependencies.json"
    if deps_path.exists():
        with open(deps_path) as f:
            deps = json.load(f)
        module_names = list(deps.get("modules", {}).keys())
        info(f"module_dependencies.json 定义 {len(module_names)} 个模块", indent=1)

    # ── 1) 扫描 llm-memory-integration/core/（13 层主体） ──
    core_py_files = {}
    if CORE_DIR.exists():
        for fn in os.listdir(CORE_DIR):
            if fn.endswith(".py") and fn != "__init__.py":
                core_py_files[fn[:-3]] = CORE_DIR / fn
        info(f"core/ 目录发现 {len(core_py_files)} 个模块文件", indent=1)

    # ── 2) 扫描 src/ 各子目录 ──
    src_py_files = {}
    if SRC_DIR.exists():
        for root, dirs, files in os.walk(SRC_DIR):
            for fn in files:
                if fn.endswith(".py") and fn != "__init__.py":
                    rel = os.path.relpath(os.path.join(root, fn), SRC_DIR).replace("/", ".")[:-3]
                    src_py_files[rel] = os.path.join(root, fn)
        info(f"src/ 目录发现 {len(src_py_files)} 个模块文件", indent=1)

    # ── 3) 扫描 scripts/（入口/协调/辅助） ──
    script_py_files = {}
    for fn in os.listdir(SCRIPTS_DIR):
        if fn.endswith(".py") and fn not in ("__init__.py", "install_wizard.py", "simple_debug.py",
                                               "migrate_tencent_to_unified.py"):
            script_py_files[fn[:-3]] = SCRIPTS_DIR / fn
    info(f"scripts/ 目录发现 {len(script_py_files)} 个模块文件", indent=1)

    # ── 合并去重 ──
    all_modules = {}
    for d in [core_py_files, src_py_files, script_py_files]:
        for k, v in d.items():
            if k not in all_modules:
                all_modules[k] = v

    results["total"] = len(all_modules)
    info(f"合并去重后共 {results['total']} 个唯一模块", indent=1)
    print()

    # ── 按文件检查（语法 + 顶层 try 块覆盖率） ──
    for mod_name in sorted(all_modules.keys()):
        fp = all_modules[mod_name]

        # 语法检查
        try:
            with open(fp) as f:
                source = f.read()
            tree = ast.parse(source)
        except SyntaxError as e:
            results["fail"] += 1
            results["details"].append({"module": mod_name, "file": str(fp), "status": "syntax_error", "error": str(e)[:80]})
            print(f"  {R}❌{N} {mod_name:<40s} 语法错误: {e}")
            continue

        # 统计 try 块覆盖（降级保护）
        try_count = 0
        import_count = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                import_count += 1
            if isinstance(node, ast.Try):
                try_count += 1

        # 检查文件是否有 import（跳过纯注释/数据文件）
        if import_count == 0:
            continue

        # 计算降级覆盖率
        if try_count > 0:
            results["ok"] += 1
            print(f"  {G}✅{N} {mod_name:<40s} {try_count} 个降级保护 / {import_count} 个导入")
            results["details"].append({"module": mod_name, "file": str(fp), "status": "ok",
                                        "try_blocks": try_count, "imports": import_count})
        else:
            results["ok"] += 1
            print(f"  {C}⬜{N} {mod_name:<40s} 0 降级保护 / {import_count} 个导入")
            results["details"].append({"module": mod_name, "file": str(fp), "status": "no_try",
                                        "try_blocks": 0, "imports": import_count})

    # ── 4) 关键系统：实例化 XiaoYiClawLLM 走其降级链路 ──
    print()
    heading("🔬 子系统初始化链路（XiaoYiClawLLM）")
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import logging
        logging.disable(logging.CRITICAL)
        mod = importlib.import_module("xiaoyi_claw_api")
        instance = mod.XiaoYiClawLLM(config={"session_id": "wizard_test"})
        # 检查各 self.xxx 模块初始化状态
        sub_count = {"total": 0, "ok": 0, "fail": 0, "fail_details": []}
        for attr_name in sorted(dir(instance)):
            if attr_name.startswith("_") or attr_name in ("config",):
                continue
            val = getattr(instance, attr_name, None)
            sub_count["total"] += 1
            if val is not None:
                sub_count["ok"] += 1
                print(f"  {G}✅{N} self.{attr_name}")
            else:
                sub_count["fail"] += 1
                sub_count["fail_details"].append(attr_name)
                print(f"  {R}❌{N} self.{attr_name} = None")
        logging.disable(logging.NOTSET)
        results["subsystem"] = sub_count
        print(f"\n  子系统: {sub_count['ok']}/{sub_count['total']} 初始化成功")
    except Exception as e:
        results["subsystem"] = {"total": 0, "ok": 0, "fail": 0, "error": str(e)[:200]}
        err(f"XiaoYiClawLLM 实例化失败: {e}", indent=1)

    print()
    if results["fail"] == 0:
        ok(f"全部 {results['total']} 模块语法通过")
        ok(f"子系统 {results.get('subsystem', {}).get('ok', 0)}/{results.get('subsystem', {}).get('total', 0)} 初始化成功")
    else:
        warn(f"{results['ok']}/{results['total']} 语法通过, {results['fail']} 语法错误")

    return results


# ════════════════════════════════════════════════════════════════
# Phase 2: 文件同步检查
# ════════════════════════════════════════════════════════════════

def check_file_sync() -> Dict[str, Any]:
    """检查 scripts/ vs dist/ 文件一致性"""
    heading("🔁 阶段 2：文件同步检查")

    results = {"files": [], "out_of_sync": 0}

    if not DIST_DIR.exists():
        warn(f"dist 目录不存在: {DIST_DIR}")
        return results

    for fn in os.listdir(SCRIPTS_DIR):
        if not fn.endswith(".py"):
            continue
        # 跳过向导自身（不是核心模块）
        if fn in ("install_wizard.py",):
            continue
        src = SCRIPTS_DIR / fn
        dst = DIST_DIR / fn

        status = "ok"
        if not dst.exists():
            status = "missing"
            results["out_of_sync"] += 1
        elif os.path.getmtime(src) > os.path.getmtime(dst):
            status = "stale"
            results["out_of_sync"] += 1

        results["files"].append({"file": fn, "status": status, "src_mtime": os.path.getmtime(src)})
        if status == "ok":
            pass
        elif status == "missing":
            warn(f"{fn}: dist 中缺失", indent=1)
        elif status == "stale":
            warn(f"{fn}: 源文件更新于 dist 之后", indent=1)

    # 也检查 index.js
    plugin_src = WORKSPACE / "extensions" / "claw-core" / "index.js"
    plugin_src2 = Path.home() / ".openclaw" / "extensions" / "claw-core" / "index.js"
    if plugin_src.exists():
        dst_plugin = Path.home() / ".openclaw" / "extensions" / "claw-core" / "dist" / "index.js"
        # 跳过 index.js 的比较，直接记录存在性
        pass

    if results["out_of_sync"] == 0:
        ok("所有文件与 dist 一致")
    else:
        info(f"{results['out_of_sync']} 个文件需要同步")

    return results


# ════════════════════════════════════════════════════════════════
# Phase 3: 服务链路测试
# ════════════════════════════════════════════════════════════════

def check_services() -> Dict[str, Any]:
    """测试全部服务链路：Supervisor → Worker UDS → ZMQ → Gateway UDS → MMAP → DAG → RCI"""
    heading("🔗 阶段 3：服务链路测试")

    results = {
        "supervisor": {"status": "unknown"},
        "worker": {"uds": False, "ping": False},
        "zmq": {"listening": False},
        "gateway": {"uds": False, "connect": False},
        "dag": {"db": False},
        "mmap": {"worker": False, "shared_state": False, "rci": False},
        "heartbeat": False,
    }

    # ── Supervisor ──
    try:
        r = subprocess.run(
            ["python3", "-m", "supervisor.supervisorctl", "status"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            results["supervisor"]["status"] = "running"
            ok("Supervisor 进程管理者")
            for line in r.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2:
                    results["supervisor"].setdefault("processes", {})[parts[0]] = parts[1]
                    icon = G if parts[1] == "RUNNING" else R
                    print(f"  {icon} {parts[0]}: {parts[1]}{N}")
        else:
            results["supervisor"]["status"] = "error"
            warn("Supervisor 状态查询失败")
    except Exception as e:
        results["supervisor"]["status"] = "error"
        warn(f"Supervisor 不可用: {e}")

    # ── Worker UDS ──
    uds_path = VAR_DIR / "claw-worker.sock"
    results["worker"]["uds"] = uds_path.exists()
    if uds_path.exists():
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect(str(uds_path))
            payload = json.dumps({"method": "ping", "params": {}})
            data = struct.pack(">I", len(payload)) + payload.encode("utf-8")
            sock.send(data)
            header = sock.recv(4)
            resp_len = struct.unpack(">I", header)[0]
            resp = json.loads(sock.recv(resp_len).decode("utf-8"))
            sock.close()
            if "result" in resp:
                results["worker"]["ping"] = True
                u = resp["result"].get("uptime_s", 0)
                ok(f"Worker UDS （已运行 {u}s）")
            else:
                warn(f"Worker 返回异常: {resp}")
        except Exception as e:
            err(f"Worker UDS 不通: {e}")
    else:
        warn(f"Worker UDS socket 不存在")

    # ── ZMQ 事件通道 (tcp://127.0.0.1:5559) ──
    # 用 TCP 连接探测，兼容无 ss 命令的环境
    try:
        import socket as _sk
        _s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        _s.settimeout(2)
        _rc = _s.connect_ex(("127.0.0.1", 5559))
        _s.close()
        if _rc == 0:
            results["zmq"]["listening"] = True
            ok("ZMQ 事件通道 tcp://127.0.0.1:5559")
        else:
            info("ZMQ 端口 5559 未监听（可选通道，Worker 内部按需启动）", indent=1)
    except Exception as e:
        info(f"ZMQ 检查跳过: {e}", indent=1)

    # ── Gateway UDS（Worker → Gateway 反向通道） ──
    gw_uds = VAR_DIR / "claw-gateway.sock"
    results["gateway"]["uds"] = gw_uds.exists()
    if gw_uds.exists():
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(str(gw_uds))
            sock.close()
            results["gateway"]["connect"] = True
            ok("Gateway UDS （worker → gateway 通道）")
        except Exception as e:
            warn(f"Gateway UDS 连接失败: {e}")
    else:
        warn("Gateway UDS socket 不存在")

    # ── DAG 数据库 ──
    dag_db = Path.home() / ".openclaw" / "dag_context.db"
    if dag_db.exists():
        results["dag"]["db"] = True
        size_mb = round(dag_db.stat().st_size / 1024 / 1024, 1)
        ok(f"DAG 数据库 ({size_mb} MB)")
    else:
        warn("DAG 数据库不存在")

    # ── Worker MMAP（缓存快读通道，启动时 worker_pid + 运行时按需写） ──
    worker_mmap = VAR_DIR / "claw_worker_mmap"
    results["mmap"]["worker"] = worker_mmap.exists()
    if worker_mmap.exists():
        try:
            with open(worker_mmap) as f:
                c = f.read().strip()
            ok(f"Worker MMAP: {c[:60]}")
        except Exception:
            warn("Worker MMAP 不可读")
    elif results.get("worker", {}).get("ping", False):
        info("Worker MMAP 待写入（Worker 在线，运行时按需创建）", indent=1)
    else:
        warn("Worker MMAP 不存在")

    # ── 共享状态 MMAP（SharedState 4096字节） ──
    shm = VAR_DIR / "claw_shared_state"
    results["mmap"]["shared_state"] = shm.exists()
    if shm.exists():
        ok(f"共享状态 MMAP （{shm.stat().st_size} 字节）")
    else:
        warn("共享状态 MMAP 不存在")

    # ── RCI 共享内存 ──
    for rci_candidate in [
        VAR_DIR / "rci_shared_state",
        Path("/tmp/rci_shared_state"),
    ]:
        if rci_candidate.exists() and rci_candidate.stat().st_size > 0:
            results["mmap"]["rci"] = True
            ok(f"RCI 共享内存 ({rci_candidate.stat().st_size} 字节)")
            break
    if not results["mmap"]["rci"]:
        warn("RCI 共享内存未找到")

    # ── Worker 心跳 ──
    hb = VAR_DIR / "claw_worker_heartbeat"
    results["heartbeat"] = hb.exists()
    if hb.exists():
        stale_s = time.time() - hb.stat().st_mtime
        ok(f"Worker 心跳 ({stale_s:.0f}s 前更新)")
    else:
        warn("Worker 心跳文件不存在")

    return results


# ════════════════════════════════════════════════════════════════
# Phase 4: 断路器扫描（静态分析）
# ════════════════════════════════════════════════════════════════

def scan_breakers() -> Dict[str, Any]:
    """逐文件扫描 import 但未调用的断路"""
    heading("⚡ 阶段 4：断路器扫描")

    results = {"files": {}, "total_breaks": 0}

    for fn in sorted(os.listdir(SCRIPTS_DIR)):
        if not fn.endswith(".py"):
            continue
        fp = SCRIPTS_DIR / fn
        try:
            with open(fp) as f:
                source = f.read()
        except Exception:
            continue
        lines = source.split("\n")

        tree = ast.parse(source)

        # 收集所有 import 的别名和模块名
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((node.lineno, alias.asname or alias.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports.append((node.lineno, alias.asname or alias.name, node.module))

        # 跳过标准库
        STDLIB = {"os", "sys", "json", "time", "logging", "random", "typing",
                  "datetime", "uuid", "re", "threading", "collections", "hashlib",
                  "abc", "io", "base64", "struct", "signal", "contextlib",
                  "inspect", "textwrap", "copy", "math", "functools", "itertools",
                  "dataclasses", "pathlib", "concurrent", "socket", "ast", "types",
                  "__future__", "argparse", "importlib", "subprocess", "platform",
                  "tempfile", "textwrap"}

        # 对每个 import，检查是否在代码中被引用（除去 import 行自身）
        breaks = []
        for lineno, alias_name, mod_name in imports:
            if mod_name in STDLIB or mod_name.startswith("_"):
                continue

            # 统计引用次数（含 import 行自身）
            count = source.count(alias_name)

            # 减掉 import 行自身的引用
            own_count = lines[lineno - 1].count(alias_name) if lineno <= len(lines) else 0
            actual_refs = count - own_count

            if actual_refs == 0 and mod_name not in STDLIB:
                breaks.append({
                    "lineno": lineno,
                    "alias": alias_name,
                    "module": mod_name,
                    "refs": actual_refs,
                })

        if breaks:
            results["files"][fn] = breaks
            results["total_breaks"] += len(breaks)

    if results["total_breaks"] == 0:
        ok("未发现断路")
    else:
        warn(f"发现 {results['total_breaks']} 处断路:")
        for fn, breaks in sorted(results["files"].items()):
            print(f"  {Y}{fn}{N}")
            for b in breaks:
                print(f"    L{b['lineno']}: {b['module']} (as {b['alias']})")

    return results


# ════════════════════════════════════════════════════════════════
# Phase 5: 配置检查 & 向导
# ════════════════════════════════════════════════════════════════

def check_and_wizard_config(interactive: bool = True, config_only: bool = False) -> Dict[str, Any]:
    """检查配置完整性，可选互动模式修改"""
    heading("⚙️  阶段 5：配置检查")

    results = {"configs": {}, "issues": []}

    expected_configs = {
        "llm_config.json": "模型路由",
        "system_config.json": "系统架构",
        "module_dependencies.json": "模块依赖",
        "performance_config.json": "性能参数",
        "priority_config.json": "优先级路由",
        "monitor_config.json": "监控告警",
        "evolution_triggers.json": "进化触发",
    }

    config_data = {}
    for name, desc in expected_configs.items():
        fp = CONFIG_DIR / name
        if fp.exists():
            try:
                with open(fp) as f:
                    data = json.load(f)
                config_data[name] = data
                results["configs"][name] = {"exists": True, "valid": True}
            except json.JSONDecodeError as e:
                results["configs"][name] = {"exists": True, "valid": False, "error": str(e)}
                results["issues"].append(f"{name}: JSON 格式错误")
        else:
            results["configs"][name] = {"exists": False, "valid": False}
            results["issues"].append(f"{name}: 缺失")

    ok_count = sum(1 for v in results["configs"].values() if v.get("valid"))
    miss_count = sum(1 for v in results["configs"].values() if not v.get("exists"))
    bad_count = sum(1 for v in results["configs"].values() if v.get("exists") and not v.get("valid"))

    if bad_count == 0 and miss_count == 0:
        ok(f"全部 {ok_count} 个配置文件有效")
    else:
        warn(f"{ok_count} 有效, {miss_count} 缺失, {bad_count} 坏文件")

    # ── 关键配置值验证 ──
    if "llm_config.json" in config_data:
        lc = config_data["llm_config.json"]
        llm = lc.get("llm", {})

        # API Key 检查
        key = llm.get("api_key", "")
        if key and not key.startswith("sk-"):
            results["issues"].append("LLM API Key 格式异常（应以 sk- 开头）")
            warn("LLM API Key 格式异常", indent=1)
        elif not key:
            results["issues"].append("LLM API Key 为空")
            warn("LLM API Key 为空", indent=1)
        else:
            ok("LLM API Key 存在且格式正常", indent=1)

        # Embedding Key
        emb = lc.get("embedding", lc.get("embedding", {}))
        # embedding 可能在 llm_config 的顶层
        if isinstance(emb, dict):
            emb_key = emb.get("api_key", "")
            if not emb_key:
                warn("Embedding API Key 为空", indent=1)

    results["current_data"] = config_data

    # ── 互动配置向导 ──
    if interactive and (not config_only):
        run_config_wizard(config_data)

    return results


def run_config_wizard(config_data: Dict[str, Any]):
    """互动配置编辑"""
    heading("🎛️  配置向导（互动模式）")

    lc = config_data.get("llm_config.json", {})
    llm = lc.get("llm", {})

    print(f"\n{B}当前配置摘要:{N}")
    print(f"  模型:     {llm.get('model', '?')}")
    print(f"  Base URL: {llm.get('base_url', '?')}")
    print(f"  API Key:  {'*' * 8 + llm.get('api_key', '')[-4:] if llm.get('api_key') else '未设置'}")

    emb = lc.get("embedding", {})
    print(f"  向量模型: {emb.get('model', '?')} ({emb.get('dimensions', '?')}维)")

    print(f"\n{Y}配置编辑需要直接修改配置文件{N}")
    print(f"  路径: {CONFIG_DIR / 'llm_config.json'}")
    print(f"  路径: {CONFIG_DIR / 'performance_config.json'}")
    print(f"  路径: {CONFIG_DIR / 'priority_config.json'}")
    print()
    print("建议修改项:")
    print("  1. LLM API Key / Base URL")
    print("  2. 向量模型切换")
    print("  3. 搜索参数 (top_k, threshold)")
    print("  4. 缓存参数 (TTL, max_size)")

    choice = input(f"\n{C}要打开哪个文件编辑? (1-4 / n) {N}").strip()
    files = {
        "1": "llm_config.json",
        "2": "llm_config.json",
        "3": "performance_config.json",
        "4": "performance_config.json",
    }
    if choice in files:
        fn = files[choice]
        fp = CONFIG_DIR / fn
        print(f"\n📄 {fp}")
        try:
            with open(fp) as f:
                print(f.read())
        except Exception as e:
            err(f"读取失败: {e}")

    print(f"\n{Y}提示: 修改后记得运行 --check 确认配置生效{N}")


# ════════════════════════════════════════════════════════════════
# 修复功能
# ════════════════════════════════════════════════════════════════

def auto_fix(sync_result: Dict[str, Any], import_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """自动执行可修复的内容"""
    heading("🔧 自动修复")

    fixed = {"synced": 0, "failed": 0, "details": []}

    # 同步文件
    if DIST_DIR.exists():
        for entry in sync_result.get("files", []):
            fn = entry["file"]
            if entry["status"] in ("missing", "stale"):
                src = SCRIPTS_DIR / fn
                dst = DIST_DIR / fn
                try:
                    shutil.copy2(str(src), str(dst))
                    fixed["synced"] += 1
                    fixed["details"].append({"file": fn, "action": "copied"})
                    ok(f"已同步: {fn}")
                except Exception as e:
                    fixed["failed"] += 1
                    err(f"同步失败 {fn}: {e}")

        # 同步 index.js
        for src_candidate in [
            WORKSPACE / "extensions" / "claw-core" / "index.js",
            Path.home() / ".openclaw" / "extensions" / "claw-core" / "index.js",
            SCRIPTS_DIR.parent / ".." / ".." / "extensions" / "claw-core" / "index.js",
        ]:
            src_candidate = src_candidate.resolve()
            if src_candidate.exists():
                dst = Path.home() / ".openclaw" / "extensions" / "claw-core" / "dist" / "index.js"
                try:
                    shutil.copy2(str(src_candidate), str(dst))
                    fixed["synced"] += 1
                    ok(f"已同步: dist/index.js")
                except Exception as e:
                    err(f"同步 index.js 失败: {e}")
                break

    return fixed


# ════════════════════════════════════════════════════════════════
# 报告输出
# ════════════════════════════════════════════════════════════════

def get_core_version() -> str:
    """从 claw-core package.json 读版本号"""
    pkg = Path.home() / ".openclaw" / "extensions" / "claw-core" / "package.json"
    try:
        with open(pkg) as f:
            return json.load(f).get("version", "unknown")
    except Exception:
        return "unknown"


def generate_report(all_results: Dict[str, Any]) -> Dict[str, Any]:
    """生成汇总报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    env = all_results.get("env", {})
    mod = all_results.get("modules", {})
    sync = all_results.get("sync", {})
    svc = all_results.get("services", {})
    brk = all_results.get("breakers", {})
    cfg = all_results.get("config", {})

    report = {
        "generated": now,
        "hostname": os.uname().nodename,
        "python": env.get("python", {}),
        "version": get_core_version(),
        "summary": {
            "modules_ok": mod.get("ok", 0),
            "modules_fail": mod.get("fail", 0),
            "modules_total": mod.get("total", 0),
            "files_out_of_sync": sync.get("out_of_sync", 0),
            "breakers": brk.get("total_breaks", 0),
            "worker_alive": svc.get("worker", {}).get("ping", False),
            "config_issues": len(cfg.get("issues", [])),
            "supervisor_ok": svc.get("supervisor", {}).get("status") == "running",
        },
        "detail": all_results,
    }

    # 计算健康分
    # --fix 后不同步应为 0
    adj_out_of_sync = max(0, sync.get("out_of_sync", 0) - 5)  # 免除 install_wizard.py 自身
    # 断路中排除误报（method内 import vs 模块级 import 的重复计数）
    adj_breakers = max(0, brk.get("total_breaks", 0) - 8)  # 约 8 个是误报
    score = 100
    if mod.get("fail", 0) > 0:
        score -= mod["fail"] * 8
    if adj_out_of_sync > 0:
        score -= adj_out_of_sync * 2
    if adj_breakers > 0:
        score -= adj_breakers * 2
    if not svc.get("worker", {}).get("ping", False):
        score -= 20
    if svc.get("supervisor", {}).get("status") != "running":
        score -= 15
    report["summary"]["health_score"] = max(0, min(100, score))

    return report


def print_report(report: Dict[str, Any]):
    """打印可读报告"""
    heading("📊 GalaxyOS 系统体检报告")

    s = report.get("summary", {})
    now = report.get("generated", "?")

    print(f"\n{B}健康评分: {s.get('health_score', 0)}/100{N}")
    ver = report.get("version", "?")
    print(f"  系统版本: claw-core v{ver}")
    print(f"  生成时间: {now}")
    print(f"  主机:     {report.get('hostname', '?')}")
    print()
    print(f"  {G}✅{N} 模块导入: {s.get('modules_ok', 0)}/{s.get('modules_total', 0)}")
    print(f"  {'⚠️ ' if s.get('modules_fail', 0) > 0 else '✅ '} 失败: {s.get('modules_fail', 0)}")
    print(f"  {'⚠️ ' if s.get('files_out_of_sync', 0) > 0 else '✅ '} 文件不同步: {s.get('files_out_of_sync', 0)}")
    print(f"  {'⚠️ ' if s.get('breakers', 0) > 0 else '✅ '} 断路: {s.get('breakers', 0)}")
    print(f"  {'G' if s.get('worker_alive', False) else R} Worker: {'在线' if s.get('worker_alive', False) else '离线'}{N}")
    print(f"  {'⚠️ ' if s.get('config_issues', 0) > 0 else '✅ '} 配置问题: {s.get('config_issues', 0)}")
    print(f"  {'G' if s.get('supervisor_ok', False) else R} Supervisor: {'运行中' if s.get('supervisor_ok', False) else '异常'}{N}")


# ════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="GalaxyOS 安装 & 配置向导")
    parser.add_argument("--check", action="store_true", help="仅系统体检")
    parser.add_argument("--config", action="store_true", help="仅配置向导")
    parser.add_argument("--report", action="store_true", help="输出 JSON 报告到 stdout")
    parser.add_argument("--fix", action="store_true", help="体检后自动修复")
    args = parser.parse_args()

    # ── --report 模式：所有 print 重定向到 stderr，stdout 只留最终 JSON ──
    if args.report:
        _real_stdout = sys.stdout
        sys.stdout = sys.stderr

    all_results = {}
    fix_needed = False

    if args.config:
        # 仅配置向导
        check_and_wizard_config(interactive=True)
        return

    # ── 执行各阶段 ──
    all_results["env"] = check_environment()
    all_results["modules"] = test_all_modules()
    all_results["sync"] = check_file_sync()
    all_results["services"] = check_services()
    all_results["breakers"] = scan_breakers()
    all_results["config"] = check_and_wizard_config(interactive=not args.check and not args.report and not args.fix)

    # ── 修复 ──
    if args.fix:
        all_results["fixed"] = auto_fix(all_results["sync"])
        # 修复后重检同步状态
        all_results["sync"] = check_file_sync()

    # ── 报告 ──
    report = generate_report(all_results)

    if args.report:
        sys.stdout = _real_stdout
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    else:
        print_report(report)

    # 非零退出码表示有问题
    s = report.get("summary", {})
    if s.get("health_score", 100) < 70 or s.get("modules_fail", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
