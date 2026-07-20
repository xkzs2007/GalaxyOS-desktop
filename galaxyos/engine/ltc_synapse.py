#!/usr/bin/env python3
"""
LTC Synapse — 液态时间常数突触

将 LTC/CfC 的连续时间动态机制嵌入记忆突触网络：
  每条突触学一对 (t_a, t_b, ff1, ff2)
  权重由 sigmoid( t_a * days_unused + t_b ) 动态插值

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-05
"""

import math
import logging
from typing import Dict, List

logger = logging.getLogger("ltc_synapse")


# ==================== 纯 Python 计算 ====================

def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid"""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    else:
        z = math.exp(x)
        return z / (1.0 + z)


def _days_since(timestamp_str: str) -> float:
    """计算距离今天的天数（支持小数）"""
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(timestamp_str)
        now = datetime.now(timezone.utc)
        return (now - t).total_seconds() / 86400.0
    except Exception:
        return 0.0


# ==================== LTC 突触参数 ====================

class LTCConfig:
    """
    一条突触的 LTC 参数组合

    公式:
        time_gate = sigmoid(t_a * days + t_b)
        weight = ff1 * (1 - time_gate) + time_gate * ff2
    """
    __slots__ = ("t_a", "t_b", "ff1", "ff2")

    def __init__(self, t_a: float = 0.0, t_b: float = 0.0,
                 ff1: float = 0.5, ff2: float = 0.5):
        self.t_a = t_a   # 时间常数斜率（正值=随时间衰减，负值=随时间增强）
        self.t_b = t_b   # 时间常数偏置
        self.ff1 = ff1   # 「新信息」通路 — 最近用过时的强度
        self.ff2 = ff2   # 「稳定状态」通路 — 长期不用时的强度

    def compute_weight(self, days: float) -> float:
        """根据距上次使用天数计算权重"""
        gate = _sigmoid(self.t_a * days + self.t_b)
        w = self.ff1 * (1.0 - gate) + gate * self.ff2
        return max(0.0, min(1.0, w))

    def get_decay_profile(self, max_days: int = 30) -> List[float]:
        """生成衰减曲线（用于可视化）"""
        return [self.compute_weight(d) for d in range(max_days + 1)]

    def half_life_days(self) -> float:
        """
        估计半衰期（权重降到 ff1 和 ff2 中点所需天数）
        """
        if abs(self.t_a) < 1e-8:
            return float("inf")
        # sigmoid(t_a * d + t_b) = 0.5 → t_a * d + t_b = 0
        return -self.t_b / self.t_a if self.t_a != 0 else float("inf")

    def to_dict(self) -> Dict[str, float]:
        return {"t_a": self.t_a, "t_b": self.t_b,
                "ff1": self.ff1, "ff2": self.ff2}

    @classmethod
    def from_dict(cls, d: dict) -> "LTCConfig":
        return cls(
            t_a=float(d.get("t_a", 0.0)),
            t_b=float(d.get("t_b", 0.0)),
            ff1=float(d.get("ff1", 0.5)),
            ff2=float(d.get("ff2", 0.5)),
        )

    def clone(self) -> "LTCConfig":
        return LTCConfig(self.t_a, self.t_b, self.ff1, self.ff2)

    def __repr__(self) -> str:
        return (f"LTCConfig(t_a={self.t_a:.3f}, t_b={self.t_b:.3f}, "
                f"ff1={self.ff1:.3f}, ff2={self.ff2:.3f})")


# ==================== 预设衰减曲线 ====================

PRESETS = {
    # 经典 LTP/LTD 式：用就增强，7 天后衰减
    "classic": LTCConfig(t_a=0.3, t_b=-1.5, ff1=0.8, ff2=0.2),
    # 快速遗忘：几天不用就迅速弱化
    "fast_decay": LTCConfig(t_a=0.8, t_b=-1.0, ff1=0.9, ff2=0.05),
    # 慢遗忘：长期保持强度
    "slow_decay": LTCConfig(t_a=0.05, t_b=-0.5, ff1=0.7, ff2=0.4),
    # 稳定递增：越不用反而越强（用于某些长期模式）
    "inverse": LTCConfig(t_a=-0.1, t_b=2.0, ff1=0.3, ff2=0.8),
    # 持续高权重：基本不衰减
    "persistent": LTCConfig(t_a=0.01, t_b=-3.0, ff1=0.9, ff2=0.8),
    # 中性
    "neutral": LTCConfig(t_a=0.0, t_b=0.0, ff1=0.5, ff2=0.5),
}


# ==================== LTC 训练器 ====================

class LTCBatchOptimizer:
    """
    PyTorch 批量优化器

    对一批突触的使用记录进行训练，优化每条突触的 (t_a, t_b, ff1, ff2)。

    训练目标:
        1. 高使用频率的突触 → ff1 高（最近用强度高），t_a 适中
        2. 长期不用的突触 → ff2 低（稳定状态弱）
        3. 所有突触的权重分布在 [0, 1]
    """

    def __init__(self, lr: float = 0.01, epochs: int = 200, verbose: bool = False):
        self.lr = lr
        self.epochs = epochs
        self.verbose = verbose
        self._model = None

    def fit(self, synapses_data: List[dict]) -> List[LTCConfig]:
        """
        训练一批突触

        Args:
            synapses_data: 每条突触的 dict，包含
                - days: float, 距上次使用天数
                - recent_uses: int, 近期使用次数
                - total_uses: int, 总使用次数
                - current_weight: float, 当前权重

        Returns:
            [LTCConfig, ...] 训练后的参数
        """
        n = len(synapses_data)
        if n == 0:
            return []

        if not _TORCH_AVAILABLE:
            # 无 PyTorch 时用规则分配
            if self.verbose:
                print(f"[LTC] PyTorch 不可用，使用规则分配: {n} 条突触")
            results = []
            for s in synapses_data:
                days = s.get("days", 0)
                uses = s.get("total_uses", 0)
                if days < 1 and uses > 5:
                    results.append(PRESETS["classic"].clone())
                elif days > 14:
                    results.append(PRESETS["fast_decay"].clone())
                elif uses > 20:
                    results.append(PRESETS["persistent"].clone())
                else:
                    results.append(LTCConfig(
                        t_a=0.3, t_b=-1.0,
                        ff1=min(0.9, 0.3 + 0.05 * uses),
                        ff2=max(0.1, 0.5 - 0.02 * days),
                    ))
            return results

        import torch as _torch_local
        import torch.nn as _nn_local

        # 准备特征
        days = _torch_local.tensor([s["days"] for s in synapses_data], dtype=_torch_local.float32)
        recent = _torch_local.tensor([s.get("recent_uses", 0) for s in synapses_data], dtype=_torch_local.float32)
        total = _torch_local.tensor([s.get("total_uses", 0) for s in synapses_data], dtype=_torch_local.float32)
        target_w = _torch_local.tensor([s.get("current_weight", 0.5) for s in synapses_data], dtype=_torch_local.float32)

        # 归一化特征
        days_norm = days / (days.max() + 1.0)
        recent_norm = recent / (recent.max() + 1.0)
        total_norm = total / (total.max() + 1.0)

        # 构建可训练模型
        self._model = _LTCTrainable(n)

        optimizer = _torch_local.optim.Adam(self._model.parameters(), lr=self.lr)

        for epoch in range(self.epochs):
            optimizer.zero_grad()

            pred_w = self._model(days_norm, recent_norm, total_norm)

            loss_weight = _nn_local.MSELoss()(pred_w, target_w)
            loss_reg = _torch_local.mean(_torch_local.relu(self._model.ff2 - self._model.ff1 + 0.05))
            loss_ta = 0.01 * _torch_local.mean(_torch_local.relu(-self._model.t_a))

            loss = loss_weight + 0.1 * loss_reg + 0.01 * loss_ta
            loss.backward()
            optimizer.step()

        # 提取参数
        with _torch_local.no_grad():
            t_a = self._model.t_a.numpy()
            t_b = self._model.t_b.numpy()
            ff1 = self._model.ff1.numpy()
            ff2 = self._model.ff2.numpy()

        results = []
        for i in range(n):
            results.append(LTCConfig(
                t_a=float(t_a[i]),
                t_b=float(t_b[i]),
                ff1=max(0.0, min(1.0, float(ff1[i]))),
                ff2=max(0.0, min(1.0, float(ff2[i]))),
            ))

        if self.verbose:
            print(f"[LTC] 训练完成: {n} 条突触, {self.epochs} epochs, "
                  f"loss={loss.item():.4f}")

        return results

    def save_model(self, path: str):
        """保存训练好的模型参数"""
        if self._model is None:
            logger.warning("没有训练过的模型可保存")
            return
        import torch as _torch_local
        _torch_local.save(self._model.state_dict(), path)

    def load_model(self, path: str, num_synapses: int):
        """加载模型参数"""
        if not _TORCH_AVAILABLE:
            logger.warning("PyTorch 不可用，无法加载模型")
            return
        import torch as _torch_local
        self._model = _LTCTrainable(num_synapses)
        self._model.load_state_dict(_torch_local.load(path))
        self._model.eval()


try:
    import torch as _torch
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


if _TORCH_AVAILABLE:
    class _LTCTrainable(_torch.nn.Module):
        """内部可训练的 LTC 参数模块"""

        def __init__(self, n: int):
            super().__init__()
            self.t_a = _torch.nn.Parameter(_torch.zeros(n))
            self.t_b = _torch.nn.Parameter(_torch.zeros(n))
            self.ff1 = _torch.nn.Parameter(_torch.full((n,), 0.5))
            self.ff2 = _torch.nn.Parameter(_torch.full((n,), 0.5))

        def forward(self, days_norm, recent_norm, total_norm):
            """前向：基于特征预测权重"""
            ff1_mod = self.ff1 + 0.2 * recent_norm + 0.1 * total_norm
            ff1_mod = _torch.clamp(ff1_mod, 0.0, 1.0)

            time_gate = _torch.sigmoid(self.t_a * days_norm + self.t_b)
            return ff1_mod * (1.0 - time_gate) + self.ff2 * time_gate


# ==================== 推理评估 ====================

def evaluate_preset(preset_name: str = "classic", max_days: int = 60):
    """评估预设衰减曲线"""
    if preset_name not in PRESETS:
        print(f"未知预设: {preset_name}，可选: {list(PRESETS.keys())}")
        return

    cfg = PRESETS[preset_name]
    profile = cfg.get_decay_profile(max_days)

    print(f"📊 预设: {preset_name}")
    print(f"   参数: {cfg}")
    print(f"   半衰期: {cfg.half_life_days():.1f} 天")

    # 打印关键点
    for d in [0, 1, 3, 7, 14, 30, 60]:
        if d <= max_days:
            print(f"   第 {d:>2d} 天: 权重 {cfg.compute_weight(d):.3f}")
    print()


# ==================== 自测试 ====================

if __name__ == "__main__":
    print("=" * 50)
    print("LTC 突触参数自测试")
    print("=" * 50)

    # 测试预设
    for name in PRESETS:
        evaluate_preset(name)

    # 测试半衰期
    print("=" * 50)
    print("半衰期对比:")
    for name, cfg in PRESETS.items():
        hl = cfg.half_life_days()
        w0 = cfg.compute_weight(0)
        w30 = cfg.compute_weight(30)
        print(f"  {name:>15s}: 半衰期 {hl:>6.1f}d, 第0天={w0:.3f}, 第30天={w30:.3f}")

    # 测试训练器（如果有 PyTorch）
    try:
        import torch
        print("\n" + "=" * 50)
        print("测试 LTC 训练器")
        print("=" * 50)

        # 造一批模拟数据
        mock_data = []
        for i in range(5):
            mock_data.append({
                "days": float(i * 3),          # 0, 3, 6, 9, 12 天
                "recent_uses": max(0, 5 - i),   # 5, 4, 3, 2, 1
                "total_uses": 10 - i * 2,
                "current_weight": max(0.1, 0.9 - i * 0.15),
            })

        trainer = LTCBatchOptimizer(lr=0.02, epochs=100, verbose=True)
        results = trainer.fit(mock_data)

        print("\n训练结果:")
        for i, cfg in enumerate(results):
            print(f"  突触 {i}: {cfg}")
            print(f"    第0天={cfg.compute_weight(0):.3f}, "
                  f"第7天={cfg.compute_weight(7):.3f}, "
                  f"第30天={cfg.compute_weight(30):.3f}")

    except ImportError:
        print("\n[跳过] PyTorch 未安装，跳过训练器测试")

    print("\n✅ LTC 突触模块加载成功")
