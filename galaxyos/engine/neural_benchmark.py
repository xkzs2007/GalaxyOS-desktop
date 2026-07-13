#!/usr/bin/env python3
"""
全链路基准测�?�?GNN + CfC + LiquidWeight + NCP vs 传统 BFS

用真实记�?JSONL 构建测试图，对比四种模式�?
  1. GAT + CfC + NCP
  2. GraphSAGE + LSTM + CfC + NCP
  3. GraphSAGE + Mean + CfC + NCP
  4. 传统 BFS（无 GNN，无拓扑�?

指标�?
  - embedding 标准差（表示学习区分度，越大越好�?
  - 传播结果数（拓扑约束下能找回多少关联�?
  - 每条突触的权重差异（CfC 实时权重是否 vs 传统固定权重的差值）
  - 传播耗时

Author: 小艺 Claw
Created: 2026-06-05
"""

import json
import os
import sys
import time
import re
import math
import logging
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple
from datetime import datetime, timezone
from galaxyos.shared.paths import workspace

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("benchmark")

import torch
import numpy as np

# 路径
WORKSPACE = workspace()
SRC_DIR = os.path.join(
    WORKSPACE,
    "skills/galaxyos-engine/skills/llm-memory-integration/src",
)
sys.path.insert(0, SRC_DIR)

# ==================== 数据加载 ====================

def load_real_memories() -> Tuple[List[str], List[List[str]]]:
    """
    从实�?JSONL 加载记忆内容，按关键词聚�?

    Returns:
        contents: [内容文本, ...]
        clusters: [[idx, ...], ...] 每个 cluster 的索�?
    """
    sources = [
        Path(WORKSPACE) / ".learnings" / "merged_memories.jsonl",
        Path(WORKSPACE) / ".learnings" / "verified_memories.jsonl",
        Path(WORKSPACE) / ".learnings" / "emotion_memories.jsonl",
        Path(WORKSPACE) / ".learnings" / "reflexions.jsonl",
    ]

    raw_contents = []
    for fp in sources:
        if fp.exists():
            with open(fp) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        c = d.get("content", d.get("user_text", d.get("text", "")))
                        if c and len(c) > 10 and len(c) < 500:
                            raw_contents.append(c.strip())
                    except:
                        pass

    # 去重
    seen = set()
    contents = []
    for c in raw_contents:
        key = c[:100]
        if key not in seen:
            seen.add(key)
            contents.append(c)

    # 关键词聚类（�?TF 风格的标记）
    clusters = defaultdict(list)
    cluster_keywords = {
        "dag": {"dag", "上下�?, "faiss", "rccam", "uds", "预计�?},
        "memory": {"记忆", "memory", "存储", "检�?, "recall"},
        "architecture": {"架构", "结构", "模块", "组件", "system", "layer"},
        "neural_network": {"神经网络", "gnn", "gcn", "gat", "sage", "embed"},
        "test": {"测试", "压测", "验证", "benchmark"},
        "code": {"代码", "python", "api", "接口", "函数"},
        "debug": {"bug", "错误", "失败", "异常", "error", "fail"},
        "communication": {"通信", "通道", "uds", "协议"},
        "config": {"配置", "config", "设置", "参数"},
        "learn": {"学习", "进化", "self", "improve"},
    }

    for i, c in enumerate(contents):
        words = set(re.findall(r'[\w\u4e00-\u9fff]+', c.lower()))
        max_overlap = 0
        best_cluster = "other"
        for k, kw in cluster_keywords.items():
            overlap = len(words & kw)
            if overlap > max_overlap:
                max_overlap = overlap
                best_cluster = k
        clusters[best_cluster].append(i)

    return contents, dict(clusters)


def build_synapse_network(
    contents: List[str],
    clusters: Dict[str, List[int]],
    seed: int = 42,
) -> Tuple[List[dict], List[dict]]:
    """
    从真实记忆构建神经元�?

    策略�?
      - 每个 cluster 内部：全连接（strong, weight 0.5-0.9�?
      - cluster 之间：按关键词重叠连接（weak, weight 0.1-0.4�?
      - 使用频率：根据内容中的数�?时间戳标记（模拟�?
    """
    rng = np.random.RandomState(seed)
    now = datetime.now(timezone.utc)

    neurons = []
    for i, c in enumerate(contents):
        # 模拟使用频率
        has_numbers = len(re.findall(r'\d+', c))
        freq = min(200, max(1, int(has_numbers * 3 + rng.exponential(scale=5))))
        days_since = max(1, int(90 * (1 - freq / 200) * rng.uniform(0.3, 1.5)))
        days_since = min(90, max(1, days_since))

        neurons.append({
            "id": f"n{i:04d}",
            "content": c[:200],
            "activation_count": freq,
            "created_at": (now - __import__('datetime').timedelta(days=days_since + 30)).isoformat(),
            "last_activated": (now - __import__('datetime').timedelta(days=days_since)).isoformat(),
        })

    # 构建 cluster 索引�?neuron id
    cluster_ids = {}
    for k, indices in clusters.items():
        cluster_ids[k] = [f"n{i:04d}" for i in indices]

    synapses = []
    used_pairs = set()

    # Cluster 内连�?
    for k, nids in cluster_ids.items():
        for i in range(len(nids) - 1):
            for j in range(i + 1, min(i + 4, len(nids))):
                src, dst = nids[i], nids[j]
                pair = (src, dst)
                if pair not in used_pairs:
                    used_pairs.add(pair)
                    src_n = neurons[[n["id"] for n in neurons].index(src)]
                    dst_n = neurons[[n["id"] for n in neurons].index(dst)]
                    w = round(rng.uniform(0.5, 0.9), 4)
                    days = _avg_days(src_n, dst_n)
                    synapses.append({
                        "id": f"s{len(synapses):04d}",
                        "source_id": src,
                        "target_id": dst,
                        "weight": w,
                        "type": "excitatory",
                        "created_at": (now - __import__('datetime').timedelta(days=days + 30)).isoformat(),
                        "last_reinforced": (now - __import__('datetime').timedelta(days=days)).isoformat(),
                        "reinforcement_count": max(1, int((src_n["activation_count"] + dst_n["activation_count"]) / 2 * 0.3)),
                    })

    # Cluster 间连接（按关键词重叠度）
    cluster_names = list(cluster_ids.keys())
    for ci in range(len(cluster_names) - 1):
        for cj in range(ci + 1, len(cluster_names)):
            ni = cluster_ids[cluster_names[ci]]
            nj = cluster_ids[cluster_names[cj]]
            if not ni or not nj:
                continue
            for _ in range(min(4, len(ni), len(nj))):
                src = ni[rng.randint(len(ni))]
                dst = nj[rng.randint(len(nj))]
                pair = (src, dst)
                if pair not in used_pairs:
                    used_pairs.add(pair)
                    src_n = neurons[[n["id"] for n in neurons].index(src)]
                    dst_n = neurons[[n["id"] for n in neurons].index(dst)]
                    w = round(rng.uniform(0.1, 0.4), 4)
                    days = _avg_days(src_n, dst_n)
                    synapses.append({
                        "id": f"s{len(synapses):04d}",
                        "source_id": src,
                        "target_id": dst,
                        "weight": w,
                        "type": "excitatory",
                        "created_at": (now - __import__('datetime').timedelta(days=days + 30)).isoformat(),
                        "last_reinforced": (now - __import__('datetime').timedelta(days=days)).isoformat(),
                        "reinforcement_count": 1,
                    })

    # 补充随机连接（增加图密度�?
    while len(synapses) < len(neurons) * 1.5:
        src = neurons[rng.randint(len(neurons))]["id"]
        dst = neurons[rng.randint(len(neurons))]["id"]
        if src == dst or (src, dst) in used_pairs:
            continue
        used_pairs.add((src, dst))
        src_n = neurons[[n["id"] for n in neurons].index(src)]
        dst_n = neurons[[n["id"] for n in neurons].index(dst)]
        days = _avg_days(src_n, dst_n)
        synapses.append({
            "id": f"s{len(synapses):04d}",
            "source_id": src,
            "target_id": dst,
            "weight": round(rng.uniform(0.2, 0.6), 4),
            "type": "excitatory",
            "created_at": (now - __import__('datetime').timedelta(days=days + 30)).isoformat(),
            "last_reinforced": (now - __import__('datetime').timedelta(days=days)).isoformat(),
            "reinforcement_count": 1,
        })

    return neurons, synapses


def _avg_days(n1: dict, n2: dict) -> float:
    d1 = _calc_days(n1.get("last_activated", ""))
    d2 = _calc_days(n2.get("last_activated", ""))
    return (d1 + d2) / 2


def _calc_days(ts: str) -> float:
    if not ts:
        return 90
    try:
        t = datetime.fromisoformat(ts)
        return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    except:
        return 90


# ==================== 基准测试 ====================

def write_jsonl(neurons, synapses):
    np = Path(WORKSPACE) / ".learnings" / "synapse_network"
    np.mkdir(parents=True, exist_ok=True)
    with open(np / "neurons.jsonl", "w") as f:
        for n in neurons:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    with open(np / "synapses.jsonl", "w") as f:
        for s in synapses:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def benchmark_mode(pipeline, mode_name: str, seed_id: str, top_k: int = 8):
    """测试一种模�?""
    t0 = time.time()
    result = pipeline.activate(seed_id, top_k=top_k)
    elapsed = (time.time() - t0) * 1000

    stats = pipeline.get_stats()
    emb_std = stats.get("embedding_stats", {}).get("std", 0)
    emb_mean = stats.get("embedding_stats", {}).get("mean", 0)

    return {
        "mode": mode_name,
        "results": result.activated_neurons,
        "labels": result.neuron_labels,
        "pipeline_time_ms": result.pipeline_time_ms,
        "elapsed_ms": elapsed,
        "emb_std": emb_std,
        "emb_mean": emb_mean,
        "num_neurons": result.num_neurons,
        "num_synapses": result.num_synapses,
    }


def run_traditional_bfs(neurons, synapses, seed_id: str, top_k: int = 8):
    """传统 BFS 传播"""
    from memory_synapse_network import MemorySynapseNetwork
    net = MemorySynapseNetwork()
    n_map = {}
    for n in neurons:
        n_map[n["id"]] = net.create_neuron(n["content"])
    for s in synapses:
        net.create_synapse(s["source_id"], s["target_id"], weight=s["weight"])

    from memory_synapse_network import ActivationSpreader
    spreader = ActivationSpreader(net.network)

    t0 = time.time()
    associated = spreader.find_associated_memories(seed_id, top_k=top_k)
    elapsed = (time.time() - t0) * 1000

    return {
        "mode": "传统 BFS",
        "results": [(n.id, s) for n, s in associated],
        "labels": {n.id: n.content[:30] for n, _ in associated},
        "pipeline_time_ms": elapsed,
        "elapsed_ms": elapsed,
        "emb_std": 0,
        "emb_mean": 0,
        "num_neurons": len(neurons),
        "num_synapses": len(synapses),
    }


def report(results: List[dict]):
    """打印对比报告"""
    print(f"\n{'='*70}")
    print("全链路基准测试报�?)
    print(f"{'='*70}")

    # 数据概览
    if results:
        r0 = results[0]
        print(f"图规�? {r0['num_neurons']} 神经�? {r0['num_synapses']} 突触")

    print(f"\n{'模式':<22s}  {'Embedding Std':>14s}  {'结果�?:>6s}  {'耗时(ms)':>9s}  {'命中神经元内�?(top 5)':<40s}")
    print(f"{'-'*22}  {'-'*14}  {'-'*6}  {'-'*9}  {'-'*40}")

    for r in results:
        hits = []
        for nid, s in r["results"][:5]:
            label = r["labels"].get(nid, nid)[:25]
            hits.append(f"{label}({s:.3f})")
        hits_str = ", ".join(hits) if hits else "(�?"

        emb_str = f"{r['emb_std']:.4f}" if r['emb_std'] > 0 else "(N/A)"
        print(f"{r['mode']:<22s}  {emb_str:>14s}  {len(r['results']):>6d}  {r['elapsed_ms']:>8.1f}  {hits_str:<40s}")


# ==================== 入口 ====================

def main():
    from neural_pipeline import NeuralMemoryPipeline

    print("=" * 70)
    print("全链路基准测�?�?真实记忆数据")
    print("=" * 70)

    # 1. 加载数据
    print("\n1. 加载真实记忆...")
    contents, clusters = load_real_memories()
    print(f"   记忆�? {len(contents)}, 聚类: {len(clusters)}")

    for k, v in sorted(clusters.items(), key=lambda x: -len(x[1])):
        print(f"     {k}: {len(v)} �?)

    # 2. 构建突触网络
    print("\n2. 构建突触网络...")
    neurons, synapses = build_synapse_network(contents, clusters)
    print(f"   神经�? {len(neurons)}, 突触: {len(synapses)}")
    write_jsonl(neurons, synapses)

    # 3. 准备种子神经�?
    seed_map = {}
    for n in neurons:
        c = n["content"].lower()
        for kw in ["记忆", "dag", "测试", "架构", "代码"]:
            if kw in c:
                seed_map.setdefault(kw, n["id"])

    print(f"\n   种子神经�? {list(seed_map.keys())}")

    # 4. 跑各模式
    modes = [
        ("gat", {"gnn_type": "gat"}),
        ("GraphSAGE+LSTM", {"gnn_type": "graphsage", "aggregator_type": "lstm"}),
        ("GraphSAGE+Mean", {"gnn_type": "graphsage", "aggregator_type": "mean"}),
    ]

    all_results = {mode[0]: [] for mode in modes}
    all_results["传统 BFS"] = []

    for query_kw, seed_id in seed_map.items():
        print(f"\n3. 传播测试 - 种子: \"{query_kw}\"")

        for mode_name, kwargs in modes:
            try:
                pipe = NeuralMemoryPipeline(**kwargs)
                r = benchmark_mode(pipe, mode_name, seed_id, top_k=8)
                all_results[mode_name].append(r)
                print(f"   {mode_name:<20s}  {len(r['results']):3d} 结果  emb_std={r['emb_std']:.4f}  {r['elapsed_ms']:.1f}ms")
                del pipe
            except Exception as e:
                print(f"   {mode_name:<20s}  FAILED: {e}")

        try:
            r = run_traditional_bfs(neurons, synapses, seed_id, top_k=8)
            all_results["传统 BFS"].append(r)
            print(f"   {'传统 BFS':<20s}  {len(r['results']):3d} 结果  (N/A)  {r['elapsed_ms']:.1f}ms")
        except Exception as e:
            print(f"   {'传统 BFS':<20s}  FAILED: {e}")

    # 5. 聚合报告
    print(f"\n{'='*70}")
    print(f"聚合报告 (�?{len(seed_map)} 个种�?")
    print(f"{'='*70}")

    print(f"\n{'模式':<22s}  {'平均结果�?:>9s}  {'平均耗时ms':>10s}  {'平均Emb Std':>12s}")
    print(f"{'-'*22}  {'-'*9}  {'-'*10}  {'-'*12}")

    for mode_name in [m[0] for m in modes] + ["传统 BFS"]:
        rs = all_results.get(mode_name, [])
        if not rs:
            continue
        avg_results = np.mean([len(r["results"]) for r in rs])
        avg_time = np.mean([r["elapsed_ms"] for r in rs])
        avg_std = np.mean([r["emb_std"] for r in rs]) if rs[0]["emb_std"] > 0 else 0
        std_str = f"{avg_std:.4f}" if avg_std > 0 else "(N/A)"
        print(f"{mode_name:<22s}  {avg_results:>9.1f}  {avg_time:>10.1f}  {std_str:>12s}")

    # 6. 单种子详细输�?
    first_kw = list(seed_map.keys())[0]
    print(f"\n{'='*70}")
    print(f"详细: 种子 \"{first_kw}\"")
    print(f"{'='*70}")

    for mode_name in [m[0] for m in modes] + ["传统 BFS"]:
        rs = all_results.get(mode_name, [])
        r = next((x for x in rs if seed_map[first_kw] in [
            nid for nid, _ in x.get("results", [])] or any(
                first_kw in v for v in x.get("labels", {}).values())), None)
        if not r and rs:
            r = rs[0]
        if not r:
            continue

        print(f"\n  [{mode_name}]")
        for rank, (nid, s) in enumerate(r["results"][:6], 1):
            label = r["labels"].get(nid, nid)[:40]
            print(f"    {rank}. {s:.4f}  {label}")
        if len(r["results"]) > 6:
            print(f"    ... 还有 {len(r['results'])-6} �?)

    # 7. 清理
    for p in [Path(WORKSPACE) / ".learnings" / "synapse_network" / f
              for f in ["neurons.jsonl", "synapses.jsonl"]]:
        if p.exists():
            p.unlink()

    print()
    print("�?基准测试完成")


if __name__ == "__main__":
    main()
