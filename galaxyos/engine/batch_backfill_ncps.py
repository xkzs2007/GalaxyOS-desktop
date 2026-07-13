#!/usr/bin/env python3
"""
batch_backfill_ncps.py — 从 DAG 历史数据批量回接 NCPS 神经突触网络

从已有的 1677+5311 条历史 DAG/R-CCAM 节点创建神经元和突触，
让 ncps 神经网络立刻拥有历史记忆数据。

流程:
  1. 读 DAG 库的 dag_nodes + rccam_nodes
  2. 每个节点→一个神经元（去重，内容指纹）
  3. 同会话相邻节点→突触（时间邻近）
  4. R-CCAM 阶段链→突触（phase→phase 父子链）

用法:
  python3 batch_backfill_ncps.py [--dry-run] [--batch-size 500]
"""

import sys
import os
import json
import re
import time
import hashlib
import logging
import math
import random
from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from galaxyos.shared.paths import workspace

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger("backfill")

WORKSPACE = workspace()
DAG_DB = os.path.expanduser("~/.openclaw/dag_context.db")

# 要跳过的会话前缀（测试数据）
SKIP_SESSION_PREFIXES = ("test_compact", "test_spec", "test_check", "test_dag", "test_flow",
                       "test_speculative", "_cog_subtree")

# 内容指纹
FP_LEN = 200
def _fingerprint(text: str) -> str:
    """内容指纹（忽略空白差异）"""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.strip())[:FP_LEN]

def _is_placeholder(content: str) -> bool:
    """判断是否为填充/测试内容"""
    if not content:
        return True
    # 全是 x/y/填充字符
    stripped = content.strip()
    if not stripped:
        return True
    # xxxx/yyyy 模式
    for ch in set(stripped):
        if ch not in ('x', 'y', ' ', '\t', '\n', '\r', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
                      'X', 'Y', 'z', 'Z', '.', ',', '!', '?', ':', ';', '-', '_', '@', '#', '$', '%',
                      '^', '&', '*', '(', ')', '+', '=', '[', ']', '{', '}', '|', '\\', '/', '<', '>',
                      '~', '`', '"', "'"):
            return False
    return len(stripped) > 20  # 全是空白/填充字符


def load_dag_nodes() -> Tuple[List[Dict], List[Dict]]:
    """从 DAG 数据库加载历史节点"""
    import sqlite3

    if not os.path.exists(DAG_DB):
        logger.warning(f"DAG 数据库不存在: {DAG_DB}")
        return [], []

    conn = sqlite3.connect(DAG_DB)
    c = conn.cursor()

    # dag_nodes
    dag_nodes = []
    for row in c.execute(
        "SELECT node_id, session_key, content, timestamp, parent_ids, importance_score, keywords, entities, node_type "
        "FROM dag_nodes"
    ):
        node_id, session_key, content, ts, parent_ids, imp, keywords, entities, node_type = row
        if not content:
            continue
        if any(session_key.startswith(p) for p in SKIP_SESSION_PREFIXES):
            continue
        if _is_placeholder(content):
            continue
        dag_nodes.append({
            "node_id": node_id,
            "session_key": session_key,
            "content": content,
            "timestamp": ts or 0,
            "parent_ids": json.loads(parent_ids) if isinstance(parent_ids, str) and parent_ids != '[]' else [],
            "importance": imp or 0.5,
            "keywords": keywords,
            "entities": entities,
            "node_type": node_type or "message",
        })

    # rccam_nodes
    rccam_nodes = []
    for row in c.execute(
        "SELECT node_id, session_key, content, timestamp, parent_ids, cycle_id, phase_name, cycle_index, "
        "importance_score, confidence "
        "FROM rccam_nodes"
    ):
        node_id, session_key, content, ts, parent_ids, cycle_id, phase_name, cycle_idx, imp, conf = row
        if not content:
            continue
        if any(session_key.startswith(p) for p in SKIP_SESSION_PREFIXES):
            continue
        if _is_placeholder(content):
            continue
        rccam_nodes.append({
            "node_id": node_id,
            "session_key": session_key,
            "content": content,
            "timestamp": ts or 0,
            "parent_ids": json.loads(parent_ids) if isinstance(parent_ids, str) and parent_ids != '[]' else [],
            "cycle_id": cycle_id or "",
            "phase_name": phase_name or "",
            "cycle_index": cycle_idx or 0,
            "importance": imp or 0.5,
            "confidence": conf or 0.5,
        })

    conn.close()
    logger.info(f"加载 dag_nodes: {len(dag_nodes)} 条, rccam_nodes: {len(rccam_nodes)} 条")
    return dag_nodes, rccam_nodes


def create_neurons_from_nodes(nodes: List[Dict], mn) -> Dict[str, str]:
    """
    从节点列表创建神经元
    返回: {node_id → neuron_id} 映射
    """
    _ws = WORKSPACE
    sys.path.insert(0, os.path.join(_ws, "GalaxyOS/skills/llm-memory-integration/core"))
    sys.path.insert(0, os.path.join(_ws, "skills/galaxyos-engine/skills/llm-memory-integration/core"))
    from memory_synapse_network import MemoryNeuron

    # 先加载现有神经元 → 去重
    existing = mn.neuron_manager.get_all_neurons()
    existing_fps: Dict[str, str] = {}
    for n in existing:
        fp = _fingerprint(n.content or "")
        if fp:
            existing_fps[fp] = n.id

    node_to_neuron: Dict[str, str] = {}
    created = 0
    skipped = 0

    for node in nodes:
        content = node["content"]
        fp = _fingerprint(content)
        if not fp:
            skipped += 1
            continue

        # 去重
        if fp in existing_fps:
            node_to_neuron[node["node_id"]] = existing_fps[fp]
            skipped += 1
            continue

        # 截断超长内容（保存精华部分）
        content_clean = content[:4000] if len(content) > 4000 else content

        # 创建神经元（通过 NeuronManager 直接调用，支持 neuron_id）
        try:
            n = mn.neuron_manager.create_neuron(
                content_clean,
                embedding=[],
                neuron_id=f"NRN-BF-{node['node_id'][:24]}" if node['node_id'] else None,
            )

            # 设置初始 LTC h_t（基于重要度）
            imp = node.get('importance', 0.5)
            if hasattr(n, 'ltc_hidden'):
                n.ltc_hidden = 0.3 + 0.5 * min(1.0, imp)
            if hasattr(n, 'activation_count'):
                n.activation_count = max(1, int(imp * 10))

            node_to_neuron[node["node_id"]] = n.id
            existing_fps[fp] = n.id
            created += 1
        except Exception as e:
            logger.debug(f"创建神经元失败: {e}")
            skipped += 1

        if created % 200 == 0 and created > 0:
            logger.info(f"  已创建 {created} 个神经元...")

    logger.info(f"创建神经元: {created} 新, {skipped} 跳过/去重")
    return node_to_neuron


def create_synapses_from_proximity(nodes: List[Dict], node_to_neuron: Dict[str, str], mn):
    """
    基于会话内的时序邻近创建突触
    
    规则:
    - 同会话内相邻节点（时间排序）→ 权重 0.5
    - 同会话内间隔 1 个节点 → 权重 0.3
    - 同一 cycle 的 phase 链（R-CCAM）→ 权重 0.7
    - 父子节点 → 权重 0.6
    """
    _ws = WORKSPACE
    sys.path.insert(0, os.path.join(_ws, "GalaxyOS/skills/llm-memory-integration/core"))
    sys.path.insert(0, os.path.join(_ws, "skills/galaxyos-engine/skills/llm-memory-integration/core"))

    syn_count = 0
    skip_count = 0

    # 按 session_key 分组
    by_session: Dict[str, List[Dict]] = defaultdict(list)
    for node in nodes:
        if node["node_id"] in node_to_neuron:
            by_session[node["session_key"]].append(node)

    for session_key, session_nodes in by_session.items():
        # 时间排序
        session_nodes.sort(key=lambda x: x["timestamp"])
        neuron_ids = [node_to_neuron[n["node_id"]] for n in session_nodes if n["node_id"] in node_to_neuron]

        # 跳过太短的会话
        if len(neuron_ids) < 2:
            continue

        # 相邻突触
        for i in range(len(neuron_ids) - 1):
            src, dst = neuron_ids[i], neuron_ids[i + 1]
            if src == dst:
                continue
            try:
                mn.create_synapse(src, dst, weight=0.5)
                syn_count += 1
            except Exception:
                skip_count += 1

        # 间隔 1 的弱连接
        for i in range(len(neuron_ids) - 2):
            src, dst = neuron_ids[i], neuron_ids[i + 2]
            if src == dst:
                continue
            try:
                mn.create_synapse(src, dst, weight=0.3)
                syn_count += 1
            except Exception:
                pass

        # 父子突触（parent_ids）
        for node in session_nodes:
            nid = node["node_id"]
            if nid not in node_to_neuron:
                continue
            for pid in node.get("parent_ids", []):
                if pid in node_to_neuron:
                    src, dst = node_to_neuron[pid], node_to_neuron[nid]
                    if src == dst:
                        continue
                    try:
                        mn.create_synapse(src, dst, weight=0.6)
                        syn_count += 1
                    except Exception:
                        skip_count += 1

    # R-CCAM 同 cycle 的 phase 链
    rccam_by_cycle: Dict[str, List[Dict]] = defaultdict(list)
    for node in nodes:
        # 只有 rccam_nodes 有 cycle_id
        if "cycle_id" in node and node["cycle_id"] and node["node_id"] in node_to_neuron:
            rccam_by_cycle[node["cycle_id"]].append(node)

    for cycle_id, cycle_nodes in rccam_by_cycle.items():
        cycle_nodes.sort(key=lambda x: {
            "retrieval": 0, "cognition": 1, "control": 2, "action": 3, "memory": 4
        }.get(x.get("phase_name", ""), 5))

        phase_nids = [node_to_neuron[n["node_id"]] for n in cycle_nodes if n["node_id"] in node_to_neuron]
        for i in range(len(phase_nids) - 1):
            src, dst = phase_nids[i], phase_nids[i + 1]
            if src == dst:
                continue
            try:
                mn.create_synapse(src, dst, weight=0.7)
                syn_count += 1
            except Exception:
                skip_count += 1

    logger.info(f"创建突触: {syn_count} 条, 跳过: {skip_count}")
    return syn_count


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量回接 NCPS 神经突触网络")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不创建")
    parser.add_argument("--batch-size", type=int, default=500, help="每批处理大小")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("NCPS 历史数据回接启动")
    logger.info(f"Dry-run: {args.dry_run}")
    logger.info(f"Workspace: {WORKSPACE}")
    logger.info("=" * 60)

    t0 = time.time()

    # Step 1: 加载 DAG 数据
    dag_nodes, rccam_nodes = load_dag_nodes()
    all_nodes = dag_nodes + rccam_nodes
    logger.info(f"总节点数: {len(all_nodes)} (dag={len(dag_nodes)}, rccam={len(rccam_nodes)})")

    if args.dry_run:
        # 干跑统计
        fps = set()
        sessions = set()
        for n in all_nodes:
            fp = _fingerprint(n["content"])
            if fp:
                fps.add(fp)
            sessions.add(n["session_key"])
        logger.info("干跑统计:")
        logger.info(f"  唯一内容指纹: {len(fps)}")
        logger.info(f"  唯一会话: {len(sessions)}")
        logger.info(f"  预计神经元: ~{len(fps)}")

        # 预估突触数量
        by_session = defaultdict(list)
        for n in all_nodes:
            by_session[n["session_key"]].append(n)
        est_synapses = 0
        for s, nodes in by_session.items():
            if len(nodes) >= 2:
                est_synapses += (len(nodes) - 1) * 2  # 相邻 + 间隔1
        logger.info(f"  预计突触: ~{est_synapses}")
        logger.info(f"  耗时: {time.time()-t0:.1f}s")
        return

    # Step 2: 初始化神经突触网络
    sys.path.insert(0, os.path.join(WORKSPACE, "GalaxyOS/skills/llm-memory-integration/core"))
    sys.path.insert(0, os.path.join(WORKSPACE, "skills/galaxyos-engine/skills/llm-memory-integration/core"))
    from memory_synapse_network import MemorySynapseNetwork
    from memory_consolidation import ConsolidationEngine

    # 用 ConsolidationEngine 初始化（保证路径一致）
    ce = ConsolidationEngine(WORKSPACE)
    mn = ce._get_synapse_network()

    before = len(mn.neuron_manager.get_all_neurons())
    logger.info(f"回接前神经网络: {before} 个神经元")

    # Step 3: 分批创建神经元
    logger.info("创建神经元中...")
    node_to_neuron = {}
    for i in range(0, len(all_nodes), args.batch_size):
        batch = all_nodes[i:i+args.batch_size]
        batch_map = create_neurons_from_nodes(batch, mn)
        node_to_neuron.update(batch_map)
        if (i + args.batch_size) % (args.batch_size * 4) == 0:
            mn.network._load()  # 刷新缓存(数据已由各create方法单条持久化)
            after = len(mn.neuron_manager.get_all_neurons())
            logger.info(f"[进度] {i+args.batch_size}/{len(all_nodes)} → {after} 神经元")

    after = len(mn.neuron_manager.get_all_neurons())
    logger.info(f"神经元创建完成: {before} → {after} (新增 {after-before})")

    # Step 4: 创建突触
    logger.info("创建突触中...")
    syn_count = create_synapses_from_proximity(all_nodes, node_to_neuron, mn)

    # Step 5: 持久化
    mn.network._load()  # 刷新缓存(数据已由各create方法单条持久化)

    elapsed = time.time() - t0
    stats = mn.get_stats()
    logger.info("=" * 60)
    logger.info("回接完成!")
    logger.info(f"  耗时: {elapsed:.1f}s")
    logger.info(f"  神经元: {stats['total_neurons']}")
    logger.info(f"  突触: {stats['total_synapses']}")
    logger.info(f"  LTC 突触: {stats['ltc_synapses']}")
    logger.info(f"  平均权重: {stats['avg_synapse_weight']}")
    logger.info(f"  强连接: {stats['strong_synapses']}")
    logger.info(f"  弱连接: {stats['weak_synapses']}")
    logger.info(f"  平均激活次数: {stats['avg_activation_count']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
