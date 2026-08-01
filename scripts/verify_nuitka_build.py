#!/usr/bin/env python3
"""Verify Nuitka build output.

Checks C extension imports, MCP Server startup, ONNX model integrity.
Usage: python scripts/verify_nuitka_build.py [dist_dir]
"""

import hashlib
import os
import subprocess
import sys
import time


def verify_imports():
    modules = [
        ("torch", True),
        ("faiss", True),
        ("hnswlib", False),
        ("onnxruntime", True),
        ("transformers", True),
        ("pandas", True),
        ("numpy", True),
        ("scipy", True),
        ("pydantic", True),
        ("httpx", True),
        ("aiohttp", True),
        ("orjson", True),
        ("openai", True),
        ("fastmcp", False),
        ("mcp", False),
        ("starlette", False),
        ("uvicorn", False),
    ]
    galaxyos_modules = [
        "galaxyos.kernel.mcp_server",
        "galaxyos.kernel.agent_core_bridge",
        "galaxyos.kernel.dsl_bridge",
        "galaxyos.kernel.liquid_memory_adapter",
        "galaxyos.kernel.dag_context_fusion",
        "galaxyos.kernel.memory_sync_bridge",
        "galaxyos.kernel.rccam_injector",
        "galaxyos.kernel.tokui_builder",
        "galaxyos.kernel.tokui_streamer",
        "galaxyos.kernel.skill_executor",
        "galaxyos.kernel.dual_runtime_manager",
        "galaxyos.kernel.i18n_manager",
        "galaxyos.kernel.rccam_rail",
        "galaxyos.shared.constants",
        "galaxyos.shared.paths",
        "galaxyos.shared.audit",
        "galaxyos.shared.fusion_guard",
    ]
    openjiuwen_modules = [
        "openjiuwen.harness.factory",
        "openjiuwen.harness.rails",
        "openjiuwen.core.foundation.llm.model",
    ]

    passed = 0
    failed = 0

    for mod, has_version in modules:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "N/A") if has_version else "N/A"
            print(f"  PASS  import {mod} (version: {ver})")
            passed += 1
        except ImportError as e:
            print(f"  FAIL  import {mod}: {e}")
            failed += 1

    for mod in galaxyos_modules:
        try:
            __import__(mod)
            print(f"  PASS  import {mod}")
            passed += 1
        except ImportError as e:
            print(f"  FAIL  import {mod}: {e}")
            failed += 1

    for mod in openjiuwen_modules:
        try:
            __import__(mod)
            print(f"  PASS  import {mod}")
            passed += 1
        except ImportError as e:
            print(f"  FAIL  import {mod}: {e}")
            failed += 1

    return passed, failed


def verify_onnx_models(dist_dir):
    model_files = [
        os.path.join("models", "embeddings", "model.onnx"),
        os.path.join("models", "embeddings", "model.onnx_data"),
        os.path.join("models", "embeddings", "tokenizer.json"),
    ]
    pretrained = [
        os.path.join("models", "cfc_gat.pt"),
        os.path.join("models", "cfc_gat_v2.pt"),
        os.path.join("models", "liquid_gat.pt"),
        os.path.join("models", "liquid_sage.pt"),
        os.path.join("models", "synapse_pretrain.pth"),
    ]
    passed = 0
    failed = 0

    for rel in model_files + pretrained:
        src = os.path.join(".", rel)
        dst = os.path.join(dist_dir, rel)
        if not os.path.exists(dst):
            print(f"  FAIL  {rel}: not found in {dist_dir}")
            failed += 1
            continue
        if os.path.exists(src):
            with open(src, "rb") as f:
                src_md5 = hashlib.md5(f.read()).hexdigest()
            with open(dst, "rb") as f:
                dst_md5 = hashlib.md5(f.read()).hexdigest()
            if src_md5 == dst_md5:
                print(f"  PASS  {rel}: MD5 match")
                passed += 1
            else:
                print(f"  FAIL  {rel}: MD5 mismatch (src={src_md5}, dst={dst_md5})")
                failed += 1
        else:
            print(f"  PASS  {rel}: exists (no source to compare)")
            passed += 1

    return passed, failed


def verify_mcp_server(exe_path):
    print(f"\n[MCP Server] Starting {exe_path}...")
    proc = subprocess.Popen(
        [exe_path, "--transport", "sse", "--host", "127.0.0.1", "--port", "8765"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(10)
        import urllib.request
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=5)
            print(f"  PASS  MCP Server health check: {resp.status}")
            return 1, 0
        except Exception as e:
            print(f"  FAIL  MCP Server health check: {e}")
            return 0, 1
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main():
    dist_dir = sys.argv[1] if len(sys.argv) > 1 else "galaxyos-mcp-dist"
    total_passed = 0
    total_failed = 0

    print("=" * 60)
    print("GalaxyOS Nuitka Build Verification")
    print("=" * 60)

    print("\n[1/3] C Extension & Module Imports")
    p, f = verify_imports()
    total_passed += p
    total_failed += f

    print(f"\n[2/3] ONNX Model & Pretrained Weight Integrity")
    p, f = verify_onnx_models(dist_dir)
    total_passed += p
    total_failed += f

    torch_variant = os.environ.get("TORCH_VARIANT", "cpu").lower()
    exe_name = f"galaxyos-mcp-{torch_variant}" if sys.platform == "linux" else f"galaxyos-mcp-{torch_variant}.exe"
    exe_path = os.path.join(dist_dir, exe_name)
    if os.path.exists(exe_path):
        print(f"\n[3/3] MCP Server Startup")
        p, f = verify_mcp_server(exe_path)
        total_passed += p
        total_failed += f
    else:
        print(f"\n[3/3] MCP Server Startup - SKIPPED (exe not found: {exe_path})")

    print("\n" + "=" * 60)
    print(f"Results: {total_passed} PASSED, {total_failed} FAILED")
    print("=" * 60)

    sys.exit(1 if total_failed > 0 else 0)


if __name__ == "__main__":
    main()
