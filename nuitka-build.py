#!/usr/bin/env python3
"""GalaxyOS Nuitka build script (vendor mode optimized).

Vendor mode:
  - galaxyos/ itself is compiled to native code.
  - Light pure-Python packages are compiled via --include-package.
  - Heavy C-extension packages (torch, faiss, transformers, pandas, hnswlib)
    are vendored as pre-built data (--include-data-dir + --nofollow-import-to):
    the entire site-packages directory is copied into dist without compilation,
    and at runtime the Python interpreter loads them dynamically.

Environment variables:
  TORCH_VARIANT    - "cpu" (default) or "cuda"
  PACKAGING_TOOL   - "nuitka" (default) or "pyinstaller" (fallback)
  NUITKA_CACHE_DIR - Nuitka compilation cache dir (default: nuitka-cache)
  NUITKA_JOBS      - Parallel C compiler jobs (default: cpu_count-2)
"""

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys


# ═══════════════════════════════════════════════════════════════════
# 第 1 类：编译（纯 Python 小包，< 5MB，.py 文件数 < 200）
# Nuitka 编译 .py → C++ → 原生代码，启动快、代码加密
# ═══════════════════════════════════════════════════════════════════
COMPILE_PACKAGES = [
    "galaxyos",
    "fastmcp",
    "mcp",
    "starlette",
    "uvicorn",
    "sse_starlette",
    "openjiuwen",
    "pydantic",
    "annotated_types",
    "httpx",
    "aiohttp",
    "requests",
    "urllib3",
    "orjson",
    "openai",
    "anyio",
    "sniffio",
    "h11",
    "dotenv",
    "click",
    "typer",
    "rich",
    "markdown_it",
    "mdurl",
    "pygments",
    "multidict",
    "yarl",
    "frozenlist",
    "aiosignal",
    "attrs",
    "typing_extensions",
    "packaging",
    "pynacl",
    "certifi",
    "charset_normalizer",
    "idna",
]

# ═══════════════════════════════════════════════════════════════════
# 第 2 类：Vendor 拷贝（C 扩展大包或体积过大的纯 Python 包）
# --include-data-dir + --nofollow-import-to 组合：不编译但拷贝
# ═══════════════════════════════════════════════════════════════════
VENDOR_PACKAGES = [
    "torch",
    "transformers",
    "faiss",
    "hnswlib",
    "pandas",
    "polars",
    "duckdb",
    "scipy",
    "numpy",
    "sklearn",
    "sympy",
    "networkx",
    "onnxruntime",
    "tokenizers",
    "tiktoken",
    "jieba",
    "snownlp",
    "PIL",
    "zmq",
    "psutil",
    "ncps",
    "pdfminer",
    "pypdfium2",
]

# ═══════════════════════════════════════════════════════════════════
# 第 3 类：完全排除（确定不需要的包）
# ═══════════════════════════════════════════════════════════════════
EXCLUDE_PACKAGES = [
    "matplotlib",
    "tkinter",
    "unittest",
    "test",
    "tests",
    "openjiuwen_studio",
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32api",
    "win32con",
    "win32event",
    "win32file",
    "win32pipe",
    "win32process",
    "win32security",
    "winerror",
]


def _find_package_dir(pkg_name):
    """Return (package_dir, package_basename) for a installed package, or None."""
    try:
        spec = importlib.util.find_spec(pkg_name)
    except (ImportError, ValueError):
        return None
    if not spec:
        return None
    if spec.submodule_search_locations:
        pkg_dir = spec.submodule_search_locations[0]
        return pkg_dir, os.path.basename(pkg_dir)
    if spec.origin and os.path.isfile(spec.origin):
        return spec.origin, os.path.basename(spec.origin)
    return None


def build_nuitka():
    torch_variant = os.environ.get("TORCH_VARIANT", "cpu").lower()
    cache_dir = os.environ.get("NUITKA_CACHE_DIR", "nuitka-cache")
    output_filename = f"galaxyos-mcp-{torch_variant}"

    cpu_count = os.cpu_count() or 1
    nuitka_jobs = os.environ.get("NUITKA_JOBS", str(min(cpu_count, max(cpu_count - 2, 1))))

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        f"--jobs={nuitka_jobs}",
        f"--output-filename={output_filename}",
        "--output-dir=dist",
        "--assume-yes-for-downloads",
        "--include-data-dir=skills=skills",
        "--include-data-dir=models=models",
        "--include-data-dir=galaxyos/translations=native_translations",
        "galaxyos/kernel/mcp_server_entry.py",
    ]

    # ── 第 1 类：编译 ──
    for pkg in COMPILE_PACKAGES:
        cmd.append(f"--include-package={pkg}")

    # ── 第 2 类：Vendor 拷贝（关键：include-data-dir + nofollow 组合）──
    for pkg in VENDOR_PACKAGES:
        info = _find_package_dir(pkg)
        if info is None:
            print(f"[Vendor] {pkg}: not installed, skipping")
            continue
        src_path, dest_name = info
        if os.path.isdir(src_path):
            cmd.append(f"--include-data-dir={src_path}={dest_name}")
            cmd.append(f"--nofollow-import-to={pkg}")
            print(f"[Vendor] {pkg}: {src_path}")
        else:
            cmd.append(f"--include-data-file={src_path}={dest_name}")
            cmd.append(f"--nofollow-import-to={pkg}")
            print(f"[Vendor] {pkg} (single file): {src_path}")

    # ── 第 3 类：排除 ──
    for pkg in EXCLUDE_PACKAGES:
        cmd.append(f"--exclude-module={pkg}")

    # ── 条件包（平台相关）──
    conditional_packages = [
        "httptools",
        "uvloop",
        "winloop",
        "async_timeout",
    ]
    for pkg in conditional_packages:
        try:
            importlib.import_module(pkg)
            cmd.append(f"--include-package={pkg}")
        except ImportError:
            pass

    print(f"[Nuitka] Building {torch_variant} variant...")
    print(f"[Nuitka] Command: {' '.join(cmd)}")
    env = os.environ.copy()
    env["NUITKA_CACHE_DIR"] = cache_dir

    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"Nuitka build failed with return code {result.returncode}"
        )

    nuitka_dist = os.path.join("dist", f"{output_filename}.dist")
    target_dist = "galaxyos-mcp-dist"
    if os.path.exists(target_dist):
        shutil.rmtree(target_dist)
    if os.path.exists(nuitka_dist):
        shutil.copytree(nuitka_dist, target_dist)
        print(f"[Nuitka] Copied {nuitka_dist} -> {target_dist}")
    else:
        print(f"[Nuitka] WARNING: {nuitka_dist} not found", file=sys.stderr)

    print(f"[Nuitka] Build complete: {target_dist}/")
    return 0


def build_pyinstaller():
    torch_variant = os.environ.get("TORCH_VARIANT", "cpu").lower()
    print("[PyInstaller] Fallback build...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "galaxyos-mcp.spec", "--noconfirm"],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PyInstaller failed with return code {result.returncode}"
        )

    pyinstaller_dist = os.path.join("dist", "galaxyos-mcp")
    target_dist = "galaxyos-mcp-dist"
    if os.path.exists(target_dist):
        shutil.rmtree(target_dist)
    if os.path.exists(pyinstaller_dist):
        shutil.copytree(pyinstaller_dist, target_dist)
        print(f"[PyInstaller] Copied {pyinstaller_dist} -> {target_dist}")

    # Rename exe to match Nuitka naming convention (galaxyos-mcp-{variant}.exe)
    src_exe = os.path.join(target_dist, "galaxyos-mcp.exe")
    dst_exe = os.path.join(target_dist, f"galaxyos-mcp-{torch_variant}.exe")
    if os.path.exists(src_exe) and not os.path.exists(dst_exe):
        os.rename(src_exe, dst_exe)
        print(f"[PyInstaller] Renamed {src_exe} -> {dst_exe}")

    print(f"[PyInstaller] Build complete: {target_dist}/")
    return 0


def main():
    packaging_tool = os.environ.get("PACKAGING_TOOL", "nuitka").lower()

    if packaging_tool == "pyinstaller":
        return build_pyinstaller()

    # 检查前置条件
    if sys.version_info < (3, 12):
        print(f"ERROR: Python 3.12+ required, got {sys.version}", file=sys.stderr)
        return 1
    try:
        subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: Nuitka not installed: pip install nuitka ordered-set zstandard", file=sys.stderr)
        return 1

    # 真正的 fallback 逻辑：build_nuitka 失败时 raise 而非 sys.exit
    try:
        return build_nuitka()
    except Exception as e:
        print(f"[Nuitka] Build error: {e}", file=sys.stderr)
        print("[Nuitka] Auto-falling back to PyInstaller...", file=sys.stderr)
        try:
            return build_pyinstaller()
        except Exception as e2:
            print(f"[PyInstaller] Also failed: {e2}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
