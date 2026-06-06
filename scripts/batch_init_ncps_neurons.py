#!/usr/bin/env python3
"""
批量初始化 4641 个旧神经元的 LTC 状态

问题：旧神经元 ltc_hidden=0.0，LTC ODE 输入 [0, time_enc] + hx=[0] 输出还是 0
导致 evaluate_state() 全返回 0，_do_synapse() 的 h_t < 0.35 过滤跳过所有神经元

修复：对每个带 ltc_cell_params 但 ltc_hidden=0 的神经元：
  1. apply_activation_signal(0.5) — 给个中等激活信号把 h_t 拉起来
  2. evaluate_state() — 再跑一次 ODE 让时间衰减也起作用

用法：python3 batch_init_ncps_neurons.py [--dry-run]
"""

import os, sys, json, time, logging, argparse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("batch_init_ncps")

HOME = Path.home()
NEURONS_PATH = HOME / ".openclaw" / "workspace" / ".learnings" / "synapse_network" / "neurons.jsonl"
SYNAPSES_PATH = HOME / ".openclaw" / "workspace" / ".learnings" / "synapse_network" / "synapses.jsonl"
BACKUP_DIR = HOME / ".openclaw" / "workspace" / ".learnings" / "synapse_network" / "bak"

# 加载内存突触网络模块
GALAXY_DIR = os.path.join(str(HOME), ".openclaw", "workspace", "GalaxyOS")
sys.path.insert(0, os.path.join(GALAXY_DIR, "services"))
sys.path.insert(0, os.path.join(GALAXY_DIR, "skills", "llm-memory-integration", "core"))
from memory_synapse_network import MemoryNeuron


def main():
    parser = argparse.ArgumentParser(description="批量初始化 LTC 神经元状态")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写文件")
    args = parser.parse_args()

    if not NEURONS_PATH.exists():
        logger.error(f"神经元文件不存在: {NEURONS_PATH}")
        sys.exit(1)

    # 读取全部神经元
    neurons = []
    with open(NEURONS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                neurons.append(json.loads(line))

    logger.info(f"已读取 {len(neurons)} 条神经元")

    # 统计
    total = len(neurons)
    with_params = sum(1 for n in neurons if n.get('ltc_cell_params'))
    h0_with_params = sum(1 for n in neurons if n.get('ltc_cell_params') and n.get('ltc_hidden', -1) == 0)
    already_active = sum(1 for n in neurons if n.get('ltc_hidden', 0) > 0)

    logger.info(f"  带 ltc_cell_params: {with_params}")
    logger.info(f"  其中 ltc_hidden=0（需初始化）: {h0_with_params}")
    logger.info(f"  已有活跃 h_t>0: {already_active}")

    if h0_with_params == 0:
        logger.info("没有需要初始化的神经元，退出")
        return

    # 备份
    if not args.dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak_neurons = BACKUP_DIR / f"neurons_{ts}.bak.jsonl"
        bak_synapses = BACKUP_DIR / f"synapses_{ts}.bak.jsonl"
        import shutil
        shutil.copy2(NEURONS_PATH, bak_neurons)
        if SYNAPSES_PATH.exists():
            shutil.copy2(SYNAPSES_PATH, bak_synapses)
        logger.info(f"备份原始数据 → {bak_neurons}")

    # 批量初始化
    init_count = 0
    changed_count = 0
    h_before = []
    h_after = []

    for n in neurons:
        if not n.get('ltc_cell_params'):
            continue
        if n.get('ltc_hidden', -1) != 0:
            continue  # 跳过已经有 h_t>0 的

        try:
            neuron = MemoryNeuron(**n)
            h_before.append(neuron.ltc_hidden)

            # 步骤1：给个中等激活信号 0.5，让 h_t 脱离 0
            neuron.apply_activation_signal(strength=0.5)

            # 步骤2：跑一次 ODE 演化，让时间编码的衰减也生效
            neuron.evaluate_state()

            h_after.append(neuron.ltc_hidden)
            n['ltc_hidden'] = round(neuron.ltc_hidden, 6)
            changed_count += 1

        except Exception as e:
            logger.warning(f"  初始化失败: neuron row error: {e}")
            continue

        init_count += 1
        if init_count % 500 == 0:
            logger.info(f"  进度: {init_count}/{h0_with_params}")

    logger.info(f"\n初始化结果:")
    logger.info(f"  尝试初始化: {init_count}")
    logger.info(f"  成功变更: {changed_count}")

    if h_before:
        logger.info(f"  初始化前 h_t 范围: {min(h_before):.4f} ~ {max(h_before):.4f}")
    if h_after:
        logger.info(f"  初始化后 h_t 范围: {min(h_after):.4f} ~ {max(h_after):.4f}")
        logger.info(f"  初始化后 h_t 均值: {sum(h_after)/len(h_after):.4f}")
        h_nonzero = [h for h in h_after if h > 0]
        logger.info(f"  >0 数量: {len(h_nonzero)}/{len(h_after)}")
        h35 = [h for h in h_after if h >= 0.35]
        logger.info(f"  >=0.35 数量: {len(h35)}/{len(h_after)}")

    if args.dry_run:
        logger.info("（预览模式，未写入）")
        return

    # 写回
    with open(NEURONS_PATH, 'w') as f:
        for n in neurons:
            f.write(json.dumps(n, ensure_ascii=False) + '\n')

    logger.info(f"\n写入完成: {NEURONS_PATH}")

    # 验证
    verify_count = 0
    verify_active = 0
    with open(NEURONS_PATH) as f:
        for line in f:
            n = json.loads(line)
            if n.get('ltc_cell_params'):
                verify_count += 1
                if n.get('ltc_hidden', 0) > 0:
                    verify_active += 1
    logger.info(f"验证: {verify_active}/{verify_count} 带参数神经元 h_t>0")


if __name__ == "__main__":
    main()
