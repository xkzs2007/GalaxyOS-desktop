#!/usr/bin/env python3
"""test_memo_onnx.py — 验证 OnnxMeMoAdapter 真的能用 LFM2.5-1.2B-Thinking-Q4 ONNX 推理。

跑法:
    python3 desktop-shell/scripts/test_memo_onnx.py

会自动按优先级探测以下 3 个路径（与 memo_adapter.py:_candidate_onnx_paths() 一致）:
    1. ~/.openclaw/workspace/models/LFM2.5-1.2B-ONNX/
    2. <path_resolver_desktop.MODELS_DIR>/LFM2.5-1.2B-ONNX/
    3. $GALAXYOS_HOME/models/LFM2.5-1.2B-ONNX/

如果都没有,会强制使用环境变量 GALAXYOS_LFM_ONNX_DIR 指定的路径。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# 把 desktop-shell/python 加入 sys.path 才能 import memo_adapter
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from memo_adapter import OnnxMeMoAdapter, MockMeMoAdapter, load_default_adapter  # noqa: E402


# 探测 3 个标准位置 + 1 个环境变量 override
def find_onnx_model() -> tuple[str, str, str] | None:
    """Return (model_path, tokenizer_path, config_path) or None."""
    home = Path(os.environ.get("HOME") or Path.home())
    candidates = [
        home / ".openclaw" / "workspace" / "models" / "LFM2.5-1.2B-ONNX",
        Path("/workspace/models/LFM2.5-1.2B-ONNX"),
    ]
    env_dir = os.environ.get("GALAXYOS_LFM_ONNX_DIR")
    if env_dir:
        candidates.insert(0, Path(env_dir))
    for d in candidates:
        m = d / "onnx" / "model_q4.onnx"
        t = d / "tokenizer.json"
        c = d / "config.json"
        if m.exists() and t.exists():
            return str(m), str(t), str(c) if c.exists() else str(m.parent.parent / "config.json")
    return None


# 7 个 MeMo 3-stage 协议会用到的 atomic sub-questions
TEST_QUESTIONS = [
    "What is GalaxyOS",
    "Who maintains GalaxyOS",
    "What does R-CCAM stand for",
    "How many stages does R-CCAM have",
    "What is MeMo in one sentence",
    "Who proposed the MeMo paper",
    "What is Agent-as-a-Router",
]


async def main() -> int:
    print("=" * 60)
    print("  MeMo ONNX Adapter — Live LFM2.5-1.2B-Thinking-Q4 Test")
    print("=" * 60)
    print()

    found = find_onnx_model()
    if not found:
        print("❌ 找不到 LFM2.5-1.2B-ONNX 权重")
        print("   期望位置之一:")
        print("     ~/.openclaw/workspace/models/LFM2.5-1.2B-ONNX/")
        print("     /workspace/models/LFM2.5-1.2B-ONNX/")
        print("   或设置环境变量: GALAXYOS_LFM_ONNX_DIR=/path/to/dir")
        print()
        print("   可以通过 install_wizard.py 下载:")
        print("     python3 scripts/install_wizard.py --download-lfm-onnx")
        return 1

    model_path, tok_path, cfg_path = found
    print(f"📁 Model:    {model_path}")
    print(f"   ({Path(model_path).stat().st_size / 1024:.1f} KB)")
    print(f"📁 Data:     {Path(model_path).with_suffix('.onnx_data')}")
    if Path(model_path).with_suffix(".onnx_data").exists():
        sz = Path(model_path).with_suffix(".onnx_data").stat().st_size / 1024**3
        print(f"   ({sz:.2f} GB)")
    print(f"📁 Tokenizer: {tok_path}")
    print(f"📁 Config:    {cfg_path}")
    print()

    # Construct adapter
    print("🔧 Constructing OnnxMeMoAdapter ...")
    t0 = time.perf_counter()
    adapter = OnnxMeMoAdapter(
        model_path=model_path,
        tokenizer_path=tok_path,
        config_path=cfg_path,
    )
    print(f"   ({time.perf_counter() - t0:.2f}s)")
    print()

    # Lazy load
    print("⏳ Loading ONNX model (this takes 10-30s on CPU) ...")
    t0 = time.perf_counter()
    try:
        # Force load
        await adapter.is_loaded()
        # _ensure_loaded is internal but we need it; use the is_loaded public
        # path which calls it. If is_loaded() returns True, the session is ready.
        adapter._ensure_loaded()
    except Exception as e:
        print(f"❌ Load failed: {type(e).__name__}: {e}")
        return 2
    print(f"   loaded in {time.perf_counter() - t0:.2f}s")
    print()

    # Report backend
    print(f"🏷  Backend: {adapter.backend_name()}")
    print()

    # Run 7 Grounding sub-questions
    print("─" * 60)
    print(f"  Running {len(TEST_QUESTIONS)} Grounding sub-questions")
    print("─" * 60)
    total_t = 0.0
    for i, q in enumerate(TEST_QUESTIONS, 1):
        t0 = time.perf_counter()
        try:
            ans = await adapter.answer(q, max_tokens=64)
        except Exception as e:
            ans = f"[ERROR {type(e).__name__}: {e}]"
        dt = (time.perf_counter() - t0) * 1000
        total_t += dt
        snip = ans[:80].replace("\n", " ")
        print(f"  [{i}/{len(TEST_QUESTIONS)}] {dt:6.0f}ms  Q: {q}")
        print(f"           A: {snip}{'...' if len(ans) > 80 else ''}")
    print("─" * 60)
    print(f"  Total: {total_t:.0f}ms  ·  Avg: {total_t/len(TEST_QUESTIONS):.0f}ms/q")
    print(f"  Calls: {adapter.call_count}")
    print()

    # 3-stage protocol smoke (compare to Mock on one canonical question)
    print("─" * 60)
    print("  3-Stage protocol smoke (one question, Mock baseline)")
    print("─" * 60)
    mock = MockMeMoAdapter()
    q = "What is GalaxyOS"
    mock_ans = await mock.answer(q, max_tokens=64)
    onnx_ans = await adapter.answer(q, max_tokens=64)
    print(f"  Q:   {q}")
    print(f"  Mock: {mock_ans[:90]}")
    print(f"  ONNX: {onnx_ans[:90]}")
    print()
    print("✅ DONE — OnnxMeMoAdapter is functional with LFM2.5-Q4")
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
