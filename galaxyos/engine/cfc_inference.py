#!/usr/bin/env python3
"""
CfC Inference Engine — 结合 NCP Wiring 的动态突触权重推理层

将 Hasani 团队的 ncps（Neural Circuit Policies）库嵌入记忆突触网络：
  1. 用 CfCCell 替代固定的 LTCConfig 标量参数
     - 每条突触的 (t_a, t_b, ff1, ff2) 由神经元状态动态算出
     - 输入 = cat(源神经元 embedding, 目标神经元 embedding, days)
  2. 用 NCP Wiring 的四层拓扑（sensory→inter→command→motor）
     替代现有的无层级 BFS 传播

不破坏现有 JSONL 存储层。推理层可按需开关。

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-05
"""

import os
import logging
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, asdict

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

logger = logging.getLogger("cfc_inference")

try:
    from ncps.torch import CfCCell, WiredCfCCell
    from ncps.wirings import NCP, AutoNCP, Wiring
    NCP_AVAILABLE = True
except ImportError:
    logger.warning("ncps 未安装，CfC 推理引擎不可用")
    NCP_AVAILABLE = False


# ==================== 四层拓扑 ====================

NEURON_ROLE_SENSORY = "sensory"
NEURON_ROLE_INTER = "inter"
NEURON_ROLE_COMMAND = "command"
NEURON_ROLE_MOTOR = "motor"

ALL_ROLES = [NEURON_ROLE_SENSORY, NEURON_ROLE_INTER,
             NEURON_ROLE_COMMAND, NEURON_ROLE_MOTOR]

# 层级顺序（低→高）
ROLE_ORDER = {r: i for i, r in enumerate(ALL_ROLES)}


@dataclass
class NeuronRole:
    """神经元的 NCP 角色标签"""
    neuron_id: str
    role: str = NEURON_ROLE_INTER  # 默认 inter 神经元
    layer: int = 1  # 0=sensory, 1=inter, 2=command, 3=motor


class NCPTopology:
    """
    NCP 四层拓扑分配器

    将现有记忆神经元映射到：
      sensory (0) — 输入层（检索 query、触发信号）
      inter   (1) — 中间层（大部分记忆神经元）
      command (2) — 指挥层（高频激活的 hub 节点）
      motor   (3) — 输出层（检索结果、关联记忆）
    """

    def __init__(self, neurons_data: Optional[List[dict]] = None):
        self.roles: Dict[str, NeuronRole] = {}
        self._role_counts = {r: 0 for r in ALL_ROLES}

        if neurons_data:
            self.assign_roles(neurons_data)

    def assign_roles(self, neurons_data: List[dict], synapses_data: Optional[List[dict]] = None):
        """
        基于图出度/入度自动分配 NCP 角色

        策略（使用图结构而非激活次数）：
          - sensory: 高 out_degree / 低 in_degree（种子节点）
          - motor:   低 out_degree / 高 in_degree（终点节点）
          - command: 高总度数（hub 节点）
          - inter:   其余

        Args:
            neurons_data: [{"id", ...}, ...]
            synapses_data: [{"source_id", "target_id", ...}, ...] — 可选，用于计算度数
        """
        n = len(neurons_data)
        if n == 0:
            return

        # 计算出度 / 入度
        out_deg: Dict[str, int] = {}
        in_deg: Dict[str, int] = {}
        for nd in neurons_data:
            nid = nd["id"]
            out_deg[nid] = 0
            in_deg[nid] = 0

        if synapses_data:
            for s in synapses_data:
                src = s.get("source_id", "")
                dst = s.get("target_id", "")
                if src in out_deg:
                    out_deg[src] = out_deg.get(src, 0) + 1
                if dst in in_deg:
                    in_deg[dst] = in_deg.get(dst, 0) + 1

        # 计算每个神经元的"种子性"得分和"终点性"得分
        max_out = max(out_deg.values()) or 1
        max_in = max(in_deg.values()) or 1
        max_total = max(max_out, max_in)

        scored = []
        for nd in neurons_data:
            nid = nd["id"]
            o = out_deg.get(nid, 0)
            i = in_deg.get(nid, 0)
            total = o + i
            # seed_score: 出度占比高为 sensory
            seed_score = o / max_out - i / max_in if max_in > 0 else o / max_out
            # sink_score: 入度占比高为 motor
            sink_score = i / max_in - o / max_out if max_out > 0 else i / max_in
            scored.append((nid, total, seed_score, sink_score, o, i))

        # 分配比例
        n_sensory = max(1, int(n * 0.10))
        n_motor = max(1, int(n * 0.15))
        n_command = max(1, int(n * 0.30))

        # 按 seed_score 排序 → 前几名做 sensory
        scored_by_seed = sorted(scored, key=lambda x: x[2], reverse=True)
        for i in range(n_sensory):
            nid = scored_by_seed[i][0]
            self._set_role(nid, NEURON_ROLE_SENSORY, 0)

        # 按 sink_score 排序 → 前几名做 motor
        scored_by_sink = sorted(scored, key=lambda x: x[3], reverse=True)
        motor_assigned = 0
        for nid, _, _, _, _, _ in scored_by_sink:
            if nid not in self.roles and motor_assigned < n_motor:
                self._set_role(nid, NEURON_ROLE_MOTOR, 3)
                motor_assigned += 1

        # 按 total degree 排序 → 前几名做 command
        scored_by_total = sorted(scored, key=lambda x: x[1], reverse=True)
        cmd_assigned = 0
        for nid, _, _, _, _, _ in scored_by_total:
            if nid not in self.roles and cmd_assigned < n_command:
                self._set_role(nid, NEURON_ROLE_COMMAND, 2)
                cmd_assigned += 1

        # 剩余全做 inter
        for nd in neurons_data:
            nid = nd["id"]
            if nid not in self.roles:
                self._set_role(nid, NEURON_ROLE_INTER, 1)

        logger.info(
            f"NCP 拓扑分配: sensory={n_sensory}, inter={n - n_sensory - n_motor - n_command}, "
            f"command={n_command}, motor={n_motor}"
        )

    def _set_role(self, neuron_id: str, role: str, layer: int):
        self.roles[neuron_id] = NeuronRole(neuron_id, role, layer)
        self._role_counts[role] += 1

    def get_role(self, neuron_id: str) -> str:
        return self.roles.get(neuron_id, NeuronRole(neuron_id)).role

    def get_layer(self, neuron_id: str) -> int:
        return self.roles.get(neuron_id, NeuronRole(neuron_id)).layer

    def is_valid_connection(self, src_id: str, dst_id: str) -> bool:
        """
        检查连接是否合法

        NCP 拓扑规则（宽松版）：
          - sensory (0) → 可以发到任何非 motor 层
          - inter   (1) → 可以发到 inter, command, motor
          - command (2) → 可以发到 command, motor（包括 recurrent）
          - motor   (3) → 只能发到 motor 自身（自环）
          - 反向（motor→command 等）不允许
        """
        src_layer = self.get_layer(src_id)
        dst_layer = self.get_layer(dst_id)

        # sensory 不能接收来自上层的
        if dst_layer == 0 and src_layer > 0:
            return False

        # motor 只能自环不能外发
        if src_layer == 3 and dst_layer != 3:
            return False

        # 允许平层和上行
        if dst_layer >= src_layer:
            return True

        return False

    def get_sensory_neurons(self) -> List[str]:
        return [nid for nid, r in self.roles.items() if r.role == NEURON_ROLE_SENSORY]

    def get_motor_neurons(self) -> List[str]:
        return [nid for nid, r in self.roles.items() if r.role == NEURON_ROLE_MOTOR]

    def get_layer_neurons(self, layer: int) -> List[str]:
        return [nid for nid, r in self.roles.items() if r.layer == layer]

    def to_dict(self) -> dict:
        return {
            nid: asdict(r)
            for nid, r in self.roles.items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NCPTopology":
        topo = cls()
        for nid, rdata in data.items():
            topo.roles[nid] = NeuronRole(**rdata)
            topo._role_counts[rdata["role"]] += 1
        return topo


# ==================== 神经元状态管理器 ====================

class NeuronStateManager:
    """
    管理每个神经元的隐藏状态向量

    每个神经元持有一个状态向量（类似 RNN 的 h_t）：
    - 激活时更新状态
    - 状态作为 CfCCell 的输入来计算突触权重
    """

    def __init__(self, state_dim: int = 64, device: str = "cpu"):
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for NeuronStateManager")
        self.state_dim = state_dim
        self.device = torch.device(device)
        self.states: Dict[str, torch.Tensor] = {}

    def get_or_init(self, neuron_id: str) -> torch.Tensor:
        """获取或初始化神经元状态"""
        if neuron_id not in self.states:
            # 用小随机数初始化（打破对称性）
            self.states[neuron_id] = torch.randn(
                self.state_dim, device=self.device
            ) * 0.01
        return self.states[neuron_id]

    def update(self, neuron_id: str, new_state: torch.Tensor):
        """更新神经元状态"""
        self.states[neuron_id] = new_state.detach().clone()

    def batch_get(self, neuron_ids: List[str]) -> torch.Tensor:
        """批量获取状态，返回 (B, state_dim)"""
        states = [self.get_or_init(nid) for nid in neuron_ids]
        return torch.stack(states, dim=0)

    def reset(self, neuron_ids: Optional[List[str]] = None):
        """重置状态（用于新会话）"""
        if neuron_ids:
            for nid in neuron_ids:
                self.states.pop(nid, None)
        else:
            self.states.clear()

    def to_dict(self) -> dict:
        return {
            nid: state.tolist()
            for nid, state in self.states.items()
        }

    def from_dict(self, data: dict):
        self.states = {
            nid: torch.tensor(vec, device=self.device)
            for nid, vec in data.items()
        }

    def save_to_jsonl(self, path: str):
        """持久化状态到 JSONL（覆盖写入）"""
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        import json
        with open(path, 'w') as f:
            for nid, state in self.states.items():
                line = json.dumps({"neuron_id": nid, "state": state.tolist()}, ensure_ascii=False)
                f.write(line + '\n')

    def load_from_jsonl(self, path: str) -> int:
        """从 JSONL 加载持久化状态，返回加载的神经元数"""
        import json
        if not os.path.exists(path):
            return 0
        count = 0
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    self.states[data["neuron_id"]] = torch.tensor(
                        data["state"], device=self.device
                    )
                    count += 1
                except (json.JSONDecodeError, KeyError):
                    continue
        return count


# ==================== CfC 推理引擎 ====================

class CfCSynapseEngine(nn.Module):
    """
    CfC 突触权重推理引擎

    核心思想：用一个共享的 CfCCell 计算所有突触的权重。
    每条突触的输入 = [源神经元状态, 目标神经元状态, days_since_reinforced]

    这样：
    - t_a/t_b/ff1/ff2 不是固定的，而是由两边神经元的当前状态动态算出
    - 多次激活会使神经元的隐藏状态变化 → 突触权重也随之变化
    - 体现了"突触可塑性"的精髓：权重取决于两端的活性

    支持两种模式：
      - use_wired_cfc=False（默认）：用独立的 CfCCell 计算标量权重，
        NCP 拓扑约束在 activate_and_propagate 中通过 is_valid_connection 过滤
      - use_wired_cfc=True：用 ncps 原生的 WiredCfCCell + NCP wiring，
        拓扑约束内建在 wiring 中，不需要手动过滤
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        hidden_size: int = 64,
        mode: str = "default",
        backbone_units: int = 0,  # 0 = 跳过 backbone，直接算
        use_embedding_proj: bool = True,
        use_wired_cfc: bool = False,
        ncp_sparsity: float = 0.5,
        ncp_seed: int = 42,
    ):
        super().__init__()

        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for CfCSynapseEngine")

        if not NCP_AVAILABLE:
            raise RuntimeError("ncps 未安装，无法创建 CfC 推理引擎")

        self.embedding_dim = embedding_dim
        self.state_dim = hidden_size
        self.use_wired_cfc = use_wired_cfc
        self._ncp_sparsity = ncp_sparsity
        self._ncp_seed = ncp_seed
        self._mode = mode

        # WiredCfCCell（如果启用）在 set_topology 时延迟构建
        self._wired_cfc: Optional[WiredCfCCell] = None
        # 神经元 ID → WiredCfCCell 内部索引的映射
        self._sensory_ids: List[str] = []
        self._motor_ids: List[str] = []
        self._neuron_to_hx_idx: Dict[str, int] = {}

        if use_embedding_proj:
            self.embed_proj = nn.Linear(embedding_dim, hidden_size)
        else:
            self.embed_proj = nn.Identity()

        if not use_wired_cfc:
            # 输入维度：源状态 + 目标状态 + days(1) = hidden_size * 2 + 1
            input_size = hidden_size * 2 + 1

            # 核心：CfCCell
            # backbone_units=0 表示跳过 backbone MLP，直接算 ff1/ff2/t_a/t_b
            # 这样每条突触的权重只由两边的状态决定，没有额外非线性层
            self.cfc_cell = CfCCell(
                input_size=input_size,
                hidden_size=1,  # 输出一个标量 = 突触权重的 gate 值
                mode=mode,
                backbone_activation="lecun_tanh",
                backbone_units=0,
                backbone_layers=0,
                backbone_dropout=0.0,
            )
        else:
            self.cfc_cell = None

        self.state_manager = NeuronStateManager(hidden_size)
        self.topo: Optional[NCPTopology] = None
        self._synapse_cache: Dict[str, dict] = {}

    def set_topology(self, topo: NCPTopology):
        """设置 NCP 拓扑，并（如果启用 use_wired_cfc）构建 WiredCfCCell"""
        self.topo = topo

        if self.use_wired_cfc and topo is not None:
            self._build_wired_cfc(topo)

    def _build_wired_cfc(self, topo: NCPTopology):
        """从 NCPTopology 的角色分配构造 WiredCfCCell"""
        num_sensory = topo._role_counts.get(NEURON_ROLE_SENSORY, 1)
        num_inter = topo._role_counts.get(NEURON_ROLE_INTER, 1)
        num_command = topo._role_counts.get(NEURON_ROLE_COMMAND, 1)
        num_motor = topo._role_counts.get(NEURON_ROLE_MOTOR, 1)

        # 确保每种神经元至少 1 个
        num_sensory = max(1, num_sensory)
        num_inter = max(1, num_inter)
        num_command = max(1, num_command)
        num_motor = max(1, num_motor)

        # 记录 sensory 和 motor 的神经元 ID 列表（按拓扑中遍历顺序）
        self._sensory_ids = []
        self._motor_ids = []
        sensory_idx = 0
        motor_idx = 0
        for nid, role in topo.roles.items():
            if role.role == NEURON_ROLE_SENSORY:
                self._sensory_ids.append(nid)
            elif role.role == NEURON_ROLE_MOTOR:
                self._motor_ids.append(nid)

        # 截断以保证不超过 wiring 构造数量
        from random import Random
        _rng = Random(self._ncp_seed)
        if len(self._sensory_ids) > num_sensory:
            _rng.shuffle(self._sensory_ids)
            self._sensory_ids = self._sensory_ids[:num_sensory]
        if len(self._motor_ids) > num_motor:
            _rng.shuffle(self._motor_ids)
            self._motor_ids = self._motor_ids[:num_motor]

        # 其余非 sensory/motor 的神经元映射到 inter+command 隐藏状态
        hidden_ids = [nid for nid in topo.roles if nid not in self._sensory_ids and nid not in self._motor_ids]
        # 前 num_inter 给 inter, 剩余给 command（截断到 num_inter+num_command）
        inter_ids = hidden_ids[:num_inter]
        command_ids = hidden_ids[num_inter:num_inter + num_command]

        total_hidden = num_inter + num_command + num_motor

        # 构建 hx 索引映射: 所有非 sensory 的神经元
        # WiredCfCCell 的 hx 是 (batch, total_hidden) 按 [motor, command, inter] 排列
        self._neuron_to_hx_idx = {}
        # WiredCfCCell 内部神经元顺序: motor (0..num_motor-1), command, inter
        for i, nid in enumerate(self._motor_ids):
            self._neuron_to_hx_idx[nid] = i
        for i, nid in enumerate(command_ids):
            self._neuron_to_hx_idx[nid] = num_motor + i
        for i, nid in enumerate(inter_ids):
            self._neuron_to_hx_idx[nid] = num_motor + num_command + i

        # 构造 NCP wiring
        wiring = NCP(
            inter_neurons=num_inter,
            command_neurons=num_command,
            motor_neurons=num_motor,
            sensory_fanout=max(1, int(num_inter * (1 - self._ncp_sparsity))),
            inter_fanout=max(1, int(num_command * (1 - self._ncp_sparsity))),
            recurrent_command_synapses=max(1, int(num_command * (1 - self._ncp_sparsity) * 2)),
            motor_fanin=max(1, int(num_command * (1 - self._ncp_sparsity))),
            seed=self._ncp_seed,
        )

        # 构建 WiredCfCCell
        self._wired_cfc = WiredCfCCell(
            input_size=num_sensory,
            wiring=wiring,
            mode=self._mode,
        )

        logger.info(
            f"WiredCfCCell 构建完成: sensory={num_sensory}, inter={num_inter}, "
            f"command={num_command}, motor={num_motor}, "
            f"总隐藏={total_hidden}"
        )

    def load_synapses(self, synapses: List[dict]):
        """加载突触列表到缓存"""
        self._synapse_cache = {
            s["id"]: s for s in synapses
        }

    def compute_synaptic_weight(
        self,
        src_state: torch.Tensor,
        dst_state: torch.Tensor,
        days: float,
    ) -> torch.Tensor:
        """
        计算一条突触的 CfC 权重

        CfCCell 前向：
            x = cat(src_state, dst_state, days)
            ff1, ff2, t_a, t_b = Linear(x)  (因 backbone_units=0)
            gate = sigmoid(t_a * days + t_b)
            weight = ff1 * (1 - gate) + gate * ff2

        返回: [1] 标量
        """
        days_t = torch.tensor([days], device=src_state.device, dtype=torch.float32)
        x = torch.cat([src_state, dst_state, days_t], dim=0).unsqueeze(0)
        hx = torch.zeros(1, 1, device=src_state.device)

        h_out, _ = self.cfc_cell.forward(x, hx, ts=days_t)
        weight = torch.sigmoid(h_out.squeeze())
        return weight

    @torch.no_grad()
    def batch_compute_weights(
        self,
        synapse_pairs: List[Tuple[str, str, float]],
        src_states: torch.Tensor,
        dst_states: torch.Tensor,
    ) -> List[float]:
        """
        批量计算突触权重（比逐条调用快很多）

        Args:
            synapse_pairs: [(src_id, dst_id, days), ...]
            src_states: (N, state_dim) 源神经元状态
            dst_states: (N, state_dim) 目标神经元状态

        Returns:
            [weight, ...] 每条突触的权重
        """
        if not self.use_wired_cfc:
            return self._batch_compute_weights_cfc(synapse_pairs, src_states, dst_states)
        else:
            return self._batch_compute_weights_wired(synapse_pairs, src_states, dst_states)

    def _batch_compute_weights_cfc(
        self,
        synapse_pairs: List[Tuple[str, str, float]],
        src_states: torch.Tensor,
        dst_states: torch.Tensor,
    ) -> List[float]:
        """原始 CfCCell 模式的批量权重计算"""
        N = len(synapse_pairs)
        days = torch.tensor([p[2] for p in synapse_pairs],
                            device=self.state_manager.device, dtype=torch.float32).unsqueeze(1)

        # 构造 CfC 输入: (N, state_dim * 2 + 1)
        inputs = torch.cat([src_states, dst_states, days], dim=1)

        # hx = zeros (N, 1)
        hx = torch.zeros(N, 1, device=self.state_manager.device)

        # CfCCell 前向（batch 模式）
        h_out, _ = self.cfc_cell.forward(inputs, hx, ts=days)

        # CfCCell 输出形状: (N, 1)  squeeze 到 (N,)
        weights = torch.sigmoid(h_out.squeeze(-1))

        return weights.tolist()

    def _batch_compute_weights_wired(
        self,
        synapse_pairs: List[Tuple[str, str, float]],
        src_states: torch.Tensor,
        dst_states: torch.Tensor,
    ) -> List[float]:
        """
        WiredCfCCell 模式的批量权重计算。

        用 WiredCfCCell 的 wiring 拓扑结构代替独立的 CfCCell，
        连接权重由 wiring 的邻接矩阵决定：
          - wiring 中存在的连接 → 权重 = 1.0
          - wiring 中不存在的连接 → 权重 = 0.0

        因为 WiredCfCCell 的 wiring 已经内建了 NCP 拓扑约束，
        `activate_and_propagate` 无需再调用 `is_valid_connection`。
        """
        if self._wired_cfc is None:
            # 未构建 WiredCfCCell，回退到 0.5
            return [0.5] * len(synapse_pairs)

        wiring = self._wired_cfc._wiring
        weights = []

        for src_id, dst_id, _ in synapse_pairs:
            # 在 WiredCfCCell 内部查找连接
            src_layer = self.topo.get_layer(src_id) if self.topo else 1
            dst_layer = self.topo.get_layer(dst_id) if self.topo else 1

            if dst_layer == 0:
                # sensory 层不能作为目标（sensory 只接收外部输入）
                weights.append(0.0)
                continue
            if src_layer == 3 and dst_layer != 3:
                # motor 只能自环
                weights.append(0.0)
                continue

            # 检查源/目标是否在 neuron_to_hx_idx 中
            src_in_wiring = src_id in self._neuron_to_hx_idx
            dst_in_wiring = dst_id in self._neuron_to_hx_idx

            if src_id in self._sensory_ids:
                # 源是 sensory — 检查 sensory_adjacency_matrix
                sensory_idx = self._sensory_ids.index(src_id)
                if dst_in_wiring:
                    dst_hx_idx = self._neuron_to_hx_idx[dst_id]
                    # sensory_adjacency_matrix: (sensory_size, total_units)
                    entry = wiring.sensory_adjacency_matrix[sensory_idx, dst_hx_idx]
                    weights.append(1.0 if entry != 0 else 0.0)
                else:
                    weights.append(0.0)
            elif src_in_wiring and dst_in_wiring:
                # 两者都在隐藏层中 — 检查 adjacency_matrix
                src_hx_idx = self._neuron_to_hx_idx[src_id]
                dst_hx_idx = self._neuron_to_hx_idx[dst_id]
                # adjacency_matrix: (total_units, total_units)
                entry = wiring.adjacency_matrix[src_hx_idx, dst_hx_idx]
                weights.append(1.0 if entry != 0 else 0.0)
            else:
                weights.append(0.0)

        return weights

    @torch.no_grad()
    def activate_and_propagate(
        self,
        seed_neuron_id: str,
        synapses: List[dict],
        top_k: int = 5,
        max_depth: int = 3,
        activation_strength: float = 0.3,
    ) -> List[Tuple[str, float]]:
        """
        CfC 驱动的激活传播

        使用 NCP 拓扑约束 + CfC 动态权重，替代原来的 BFS。

        Args:
            seed_neuron_id: 起始神经元 ID
            synapses: 所有突触的 dict 列表
            top_k: 返回 top K 个关联
            max_depth: 最大传播深度
            activation_strength: 激活强度阈值

        Returns:
            [(neuron_id, strength), ...]
        """
        if self.topo is None:
            logger.warning("未设置 NCP 拓扑，回退到无拓扑传播")
            return self._propagate_no_topo(seed_neuron_id, synapses, top_k, max_depth)

        # 过滤拓扑合法的连接
        # 注意：use_wired_cfc=True 时，wiring 已内建拓扑约束，
        # batch_compute_weights 返回的权重中非法连接已经为 0.0，无需 is_valid_connection 过滤
        valid_synapses = []
        pair_data = []
        for s in synapses:
            src = s["source_id"]
            dst = s["target_id"]
            days = self._calc_days(s.get("last_reinforced", ""))
            if self.use_wired_cfc or self.topo.is_valid_connection(src, dst):
                valid_synapses.append(s)
                pair_data.append((src, dst, days))

        if not valid_synapses:
            return []

        # 构造状态矩阵
        src_ids = [p[0] for p in pair_data]
        dst_ids = [p[1] for p in pair_data]
        all_ids = list(set(src_ids + dst_ids))

        # 获取/初始化所有相关神经元的状态
        _ = [self.state_manager.get_or_init(nid) for nid in all_ids]

        # 构造批处理输入
        # 对于每个突触，源和目标状态
        src_states = torch.stack([
            self.state_manager.states[sid] for sid in src_ids
        ], dim=0)
        dst_states = torch.stack([
            self.state_manager.states[did] for did in dst_ids
        ], dim=0)

        # 批量计算权重
        weights = self.batch_compute_weights(pair_data, src_states, dst_states)

        # 构建邻接图（带权重）
        graph: Dict[str, List[Tuple[str, float]]] = {}
        for s, w in zip(valid_synapses, weights):
            src = s["source_id"]
            dst = s["target_id"]
            if src not in graph:
                graph[src] = []
            graph[src].append((dst, w))

        # 层级传播（按 NCP 层级顺序，而非 BFS）
        visited: Set[str] = set()
        results: Dict[str, float] = {}

        # 获取起始神经元的层级
        seed_layer = self.topo.get_layer(seed_neuron_id)

        # 从 seed 层传播到更高层
        queue = [(seed_neuron_id, 1.0)]  # (neuron_id, 累积强度)
        visited.add(seed_neuron_id)

        while queue:
            current_id, current_strength = queue.pop(0)

            # 记录结果（排除种子本身）
            if current_id != seed_neuron_id:
                results[current_id] = current_strength

            # 获取当前神经元的输出突触
            neighbors = graph.get(current_id, [])

            # 按 CfC 权重排序，只传播最强的
            neighbors.sort(key=lambda x: x[1], reverse=True)

            for dst_id, weight in neighbors:
                if dst_id in visited:
                    continue

                # 检查层级：只能向更高层传播
                dst_layer = self.topo.get_layer(dst_id)
                if dst_layer < self.topo.get_layer(current_id):
                    continue

                # 传播强度 = 当前强度 × CfC 权重
                propagated = current_strength * weight

                if propagated < activation_strength:
                    continue  # 低于阈值，剪枝

                visited.add(dst_id)
                queue.append((dst_id, propagated))

                # 更新目标神经元状态（LTP 效应）
                self._update_state_on_activation(dst_id)

        # 按强度排序
        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]

    def _propagate_no_topo(
        self,
        seed_id: str,
        synapses: List[dict],
        top_k: int,
        max_depth: int,
    ) -> List[Tuple[str, float]]:
        """无拓扑时的 BFS 传播（回退方案）"""
        syn_map: Dict[str, List[Tuple[str, float]]] = {}
        pair_data = []

        for s in synapses:
            src = s["source_id"]
            dst = s["target_id"]
            days = self._calc_days(s.get("last_reinforced", ""))
            if src not in syn_map:
                syn_map[src] = []
            pair_data.append((src, dst, days))

        # 批量计算
        src_ids = [p[0] for p in pair_data]
        dst_ids = [p[1] for p in pair_data]
        _ = [self.state_manager.get_or_init(nid)
             for nid in set(src_ids + dst_ids)]
        src_states = torch.stack([self.state_manager.states[sid] for sid in src_ids], dim=0)
        dst_states = torch.stack([self.state_manager.states[did] for did in dst_ids], dim=0)
        weights = self.batch_compute_weights(pair_data, src_states, dst_states)

        for s, w in zip(synapses, weights):
            syn_map[s["source_id"]].append((s["target_id"], w))

        visited: Set[str] = set()
        results: Dict[str, float] = {}
        queue = [(seed_id, 1.0, 0)]

        while queue:
            cid, strength, depth = queue.pop(0)
            if cid in visited or depth > max_depth:
                continue
            visited.add(cid)
            if cid != seed_id:
                results[cid] = strength
            for dst, w in syn_map.get(cid, []):
                p = strength * w
                if p >= 0.1:
                    queue.append((dst, p, depth + 1))
                    self._update_state_on_activation(dst)

        return sorted(results.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _update_state_on_activation(self, neuron_id: str):
        """激活时更新神经元状态（LTP 效应）"""
        if neuron_id not in self.state_manager.states:
            return
        state = self.state_manager.states[neuron_id]
        # 激活使状态向量略微增强（学习效应）
        updated = state * 1.01 + torch.randn_like(state) * 0.001
        self.state_manager.states[neuron_id] = updated

    @staticmethod
    def _calc_days(ts: str) -> float:
        if not ts:
            return 0.0
        try:
            from datetime import datetime, timezone
            t = datetime.fromisoformat(ts)
            return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
        except Exception:
            return 0.0

    def get_weight_for_synapse(
        self, synapse: dict
    ) -> float:
        """对外接口：为单条突触计算 CfC 权重"""
        src_id = synapse["source_id"]
        dst_id = synapse["target_id"]
        days = self._calc_days(synapse.get("last_reinforced", ""))

        src_state = self.state_manager.get_or_init(src_id)
        dst_state = self.state_manager.get_or_init(dst_id)

        with torch.no_grad():
            w = self.compute_synaptic_weight(src_state, dst_state, days)
        return w.item()


# ==================== NCP Wiring 构建器 ====================

def build_ncp_wiring(
    num_sensory: int,
    num_inter: int,
    num_command: int,
    num_motor: int,
    sparsity_level: float = 0.5,
    seed: int = 42,
) -> NCP:
    """
    构建 NCP Wiring 对象

    这是 ncps 原生的 NCP 布线，用于 WiredCfCCell。
    我们用它做两件事：
    1. 生成稀疏连接拓扑（作为突触网络的参考结构）
    2. 直接驱动 WiredCfCCell 前向传播

    Args:
        num_sensory: 输入节点数
        num_inter: 中间节点数
        num_command: 指挥节点数
        num_motor: 输出节点数（= 隐藏状态维度）
        sparsity_level: 稀疏度（0.1=密集, 0.9=极稀疏）
        seed: 随机种子

    Returns:
        NCP Wiring 对象
    """
    total_units = num_inter + num_command + num_motor
    wiring = NCP(
        inter_neurons=num_inter,
        command_neurons=num_command,
        motor_neurons=num_motor,
        sensory_fanout=max(1, int(num_inter * (1 - sparsity_level))),
        inter_fanout=max(1, int(num_command * (1 - sparsity_level))),
        recurrent_command_synapses=max(1, int(num_command * (1 - sparsity_level) * 2)),
        motor_fanin=max(1, int(num_command * (1 - sparsity_level))),
        seed=seed,
    )
    # build：指定输入维度
    wiring.build(num_sensory)
    return wiring


def build_autoncp_wiring(
    total_units: int,
    output_size: int,
    sparsity_level: float = 0.5,
    seed: int = 42,
) -> AutoNCP:
    """简化版：AutoNCP 自动分配拓扑"""
    wiring = AutoNCP(total_units, output_size, sparsity_level, seed)
    return wiring


# ==================== 自测试 ====================

if __name__ == "__main__":
    print("=" * 55)
    print("CfC 推理引擎自测试")
    print("=" * 55)

    if not NCP_AVAILABLE:
        print("❌ ncps 未安装，跳过测试")
        exit(1)

    print("ncps 可用 ✓")
    print()

    # 测试 1: NCP 拓扑分配
    print("1. NCP 拓扑分配")
    mock_neurons = [
        {"id": f"n{i}", "activation_count": 100 - i * 5}
        for i in range(20)
    ]
    topo = NCPTopology(mock_neurons)
    for nid in [f"n{i}" for i in range(20)]:
        r = topo.get_role(nid)
        l = topo.get_layer(nid)
        print(f"  {nid}: role={r}, layer={l}")

    print()

    # 测试 2: NCP Wiring 构建
    print("2. NCP Wiring 构建")
    wiring = build_ncp_wiring(
        num_sensory=2, num_inter=4, num_command=2, num_motor=1
    )
    print("  NCP Wiring created ✓")
    print(f"  神经元数: {wiring.units}")
    print(f"  突触数(内部): {wiring.synapse_count}")
    print(f"  突触数(感官): {wiring.sensory_synapse_count}")

    print()

    # 测试 3: CfC 引擎基本功能
    print("3. CfC 引擎")
    engine = CfCSynapseEngine(embedding_dim=128, hidden_size=16, backbone_units=0)
    engine.set_topology(topo)

    # 模拟突触
    mock_synapses = []
    for i in range(5):
        mock_synapses.append({
            "id": f"s{i}",
            "source_id": f"n{i}",
            "target_id": f"n{i+1}",
            "last_reinforced": "",
            "weight": 0.5,
        })

    engine.load_synapses(mock_synapses)

    # 计算权重
    results = engine.activate_and_propagate("n0", mock_synapses, top_k=3)
    print("  激活传播结果:")
    for nid, strength in results:
        print(f"    {nid}: strength={strength:.4f}")

    print()

    # 测试 4: 与预设对比
    print("4. 单条突触权重 vs 预设")
    from galaxyos.engine.ltc_synapse import PRESETS  # type: ignore[attr-defined]
    test_syn = {"source_id": "n0", "target_id": "n1",
                "last_reinforced": "", "weight": 0.5}
    w_cfc = engine.get_weight_for_synapse(test_syn)
    w_classic = PRESETS["classic"].compute_weight(0)
    print(f"  CfC: {w_cfc:.4f}  |  classic 预设: {w_classic:.4f}")

    print()
    print("✅ CfC 推理引擎测试通过")
