#!/usr/bin/env python3
"""LFM ONNX 提速实测：对比 ONNX vs Mock backend 的真实延迟。"""
import asyncio
import sys
import time
import os

sys.path.insert(0, '/workspace/desktop-shell/python')
from memo_adapter import load_default_adapter, reset_adapter_cache, MockMeMoAdapter


QUESTIONS = [
    "What is GalaxyOS?",
    "Who maintains GalaxyOS?",
    "When was GalaxyOS released?",
    "What is MeMo?",
    "What does MeMo stand for?",
    "Who proposed MeMo?",
    "What is TokUI used for?",
]


async def bench(adapter, label):
    print(f"\n=== {label} ({adapter.backend_name()}) ===")
    latencies = []
    for i, q in enumerate(QUESTIONS, 1):
        t0 = time.perf_counter()
        out = await adapter.answer(q, max_tokens=96)
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        snippet = out.replace("\n", " ")[:80]
        print(f"  [{i}] {dt:7.1f} ms | {snippet}")
    avg = sum(latencies) / len(latencies)
    print(f"  -- avg: {avg:.1f} ms / query ({sum(latencies):.1f} ms total)")
    return latencies, avg


async def main():
    # Force ONNX by passing explicit path (auto-probe misses /workspace/models)
    from pathlib import Path
    from memo_adapter import OnnxMeMoAdapter
    real_path = Path("/workspace/models/LFM2.5-1.2B-ONNX")
    onnx_adapter = OnnxMeMoAdapter(
        model_path=str(real_path / "onnx" / "model_q4.onnx"),
        tokenizer_path=str(real_path / "tokenizer.json"),
        config_path=str(real_path / "config.json"),
    )
    onnx_lats, onnx_avg = await bench(onnx_adapter, "ONNX")

    reset_adapter_cache()
    mock = MockMeMoAdapter()
    mock_lats, mock_avg = await bench(mock, "Mock")

    if onnx_avg > 0 and mock_avg > 0:
        print(f"\n=== Summary ===")
        print(f"Mock avg: {mock_avg:.1f} ms")
        print(f"ONNX avg: {onnx_avg:.1f} ms")
        print(f"ONNX / Mock = {onnx_avg/mock_avg:.2f}x slower  "
              f"(speedup over Mock = {mock_avg/onnx_avg:.2f}x)")


if __name__ == "__main__":
    asyncio.run(main())
