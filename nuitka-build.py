#!/usr/bin/env python3
"""GalaxyOS Nuitka build script (vendor mode).

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


def check_prerequisites():
    errors = []
    if sys.version_info < (3, 12):
        errors.append(f"Python 3.12+ required, got {sys.version}")
    try:
        subprocess.run(
            ["python", "-m", "nuitka", "--version"],
            capture_output=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        errors.append("Nuitka not installed: pip install nuitka ordered-set zstandard")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _find_package_dir(pkg_name):
    """Return (package_dir, package_basename) for a installed package, or None."""
    try:
        spec = importlib.util.find_spec(pkg_name)
    except (ImportError, ValueError):
        return None
    if not spec:
        return None
    # Package is a directory (has submodule_search_locations)
    if spec.submodule_search_locations:
        pkg_dir = spec.submodule_search_locations[0]
        return pkg_dir, os.path.basename(pkg_dir)
    # Single-file module (.pyd / .so)
    if spec.origin and os.path.isfile(spec.origin):
        return spec.origin, os.path.basename(spec.origin)
    return None


def build_nuitka():
    torch_variant = os.environ.get("TORCH_VARIANT", "cpu").lower()
    cache_dir = os.environ.get("NUITKA_CACHE_DIR", "nuitka-cache")
    output_filename = f"galaxyos-mcp-{torch_variant}"

    cpu_count = os.cpu_count() or 1
    nuitka_jobs = os.environ.get("NUITKA_JOBS", str(min(cpu_count, max(cpu_count - 2, 1))))

    # ---- Light packages: compile with Nuitka ----
    compile_packages = [
        "fastmcp",
        "mcp",
        "starlette",
        "openjiuwen",
        "uvicorn",
        "onnxruntime",
        "sklearn",
        "scipy",
        "numpy",
        "pydantic",
        "httpx",
        "aiohttp",
        "orjson",
        "openai",
        "pdfminer",
        "pypdfium2",
        "urllib3",
        "certifi",
        "charset_normalizer",
        "idna",
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
        "mpmath",
        "sympy",
        "networkx",
        "multidict",
        "yarl",
        "frozenlist",
        "aiosignal",
        "attrs",
        "annotated_types",
        "typing_extensions",
        "packaging",
        "sse_starlette",
        "zmq",  # pyzmq on PyPI, imports as zmq
        "requests",
        "PIL",
        "polars",
        "duckdb",
        "jieba",
        "snownlp",
        "tiktoken",
        "psutil",
        "ncps",
        "tokenizers",
    ]

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

    for pkg in compile_packages:
        cmd.append(f"--include-package={pkg}")

    # Conditional packages (platform-specific)
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

    # ---- Heavy C-extension packages: vendor as data (no compilation) ----
    # Copy the entire site-packages directory into dist so the standalone
    # executable can import them at runtime.  --nofollow-import-to prevents
    # Nuitka from trying to compile them (which would be extremely slow / OOM).
    heavy_vendor_packages = [
        "torch",
        "faiss",
        "transformers",
        "pandas",
        "hnswlib",
    ]

    for pkg in heavy_vendor_packages:
        info = _find_package_dir(pkg)
        if info is None:
            print(f"[Nuitka] {pkg} not installed, skipping vendor")
            continue
        src_path, dest_name = info
        if os.path.isdir(src_path):
            cmd.append(f"--include-data-dir={src_path}={dest_name}")
            cmd.append(f"--nofollow-import-to={pkg}")
            print(f"[Nuitka] Vendoring {pkg}: {src_path}")
        else:
            # Single .pyd/.so file
            cmd.append(f"--include-data-file={src_path}={dest_name}")
            cmd.append(f"--nofollow-import-to={pkg}")
            print(f"[Nuitka] Vendoring {pkg} (single file): {src_path}")

    # ---- Modules to skip entirely (no compile, no include) ----
    skip_modules = [
        "tkinter",
        "matplotlib",
        "unittest",
        "openjiuwen_studio",
        # Windows-only modules that Nuitka should not follow
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
    for mod in skip_modules:
        cmd.append(f"--nofollow-import-to={mod}")

    print(f"[Nuitka] Building {torch_variant} variant...")
    print(f"[Nuitka] Command: {' '.join(cmd)}")
    env = os.environ.copy()
    env["NUITKA_CACHE_DIR"] = cache_dir

    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print(f"[Nuitka] Build failed with return code {result.returncode}", file=sys.stderr)
        sys.exit(1)

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
    print("[PyInstaller] Fallback build...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "galaxyos-mcp.spec", "--noconfirm"],
    )
    if result.returncode != 0:
        print(f"[PyInstaller] Build failed with return code {result.returncode}", file=sys.stderr)
        sys.exit(1)

    pyinstaller_dist = os.path.join("dist", "galaxyos-mcp")
    target_dist = "galaxyos-mcp-dist"
    if os.path.exists(target_dist):
        shutil.rmtree(target_dist)
    if os.path.exists(pyinstaller_dist):
        shutil.copytree(pyinstaller_dist, target_dist)
        print(f"[PyInstaller] Copied {pyinstaller_dist} -> {target_dist}")

    print(f"[PyInstaller] Build complete: {target_dist}/")
    return 0


def main():
    packaging_tool = os.environ.get("PACKAGING_TOOL", "nuitka").lower()

    if packaging_tool == "pyinstaller":
        return build_pyinstaller()

    check_prerequisites()

    try:
        return build_nuitka()
    except Exception as e:
        print(f"[Nuitka] Build error: {e}", file=sys.stderr)
        print("[Nuitka] Consider setting PACKAGING_TOOL=pyinstaller as fallback", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
