#!/usr/bin/env python3
"""
超网络路由 — Hypernetwork for Adaptive Routing (论文级升级)

Ha et al. (ICLR 2017) arXiv:1609.09106 — HyperNetworks (被引 3000+)

核心增强 (2026-05-27):
  A. LinearMetaLearner — 8维特征 → 5维策略分数的线性映射 + SGD 训练
  B. select_strategy 增强 — 已收敛时用 meta 预测，否则 Q-table 冷启动
  C. save_model() / load_model() — W 矩阵持久化到 .learnings/hyper_router_model.json

用法:
    hr = HyperRouter()
    strategy = hr.select_strategy(query_features)
    hr.feedback(strategy_name, success=True, latency_ms=200)
"""

import json
import os
import math
import random
import logging
from typing import List, Dict, Optional, Any, Tuple
from collections import defaultdict

import numpy as np

logger = logging.getLogger(__name__)

# 默认路由策略池 (R-CCAM Control 阶段可选)
DEFAULT_STRATEGIES = {
    "direct_answer": {
        "description": "纯模型回答，不检索",
        "suits": ["faq", "greeting", "opinion", "simple_fact"],
        "default_q": 0.5,
    },
    "quick_recall": {
        "description": "单轮向量检索",
        "suits": ["factoid", "info", "recommend"],
        "default_q": 0.6,
    },
    "deep_search": {
        "description": "检索→评估→再检索循环",
        "suits": ["complex", "tech", "analysis"],
        "default_q": 0.4,
    },
    "multi_path": {
        "description": "分支推理→评估→选择最优",
        "suits": ["creative", "planning", "debate"],
        "default_q": 0.3,
    },
    "tool_call": {
        "description": "优先走工具链",
        "suits": ["operation", "query_data", "web_search"],
        "default_q": 0.5,
    },
}

STRATEGY_NAMES = list(DEFAULT_STRATEGIES.keys())

# 特征维度说明
FEATURE_KEYS = [
    "query_len",         # query 长度 (归一化 0~1)
    "complexity",        # 复杂度 (简单/中等/复杂)
    "has_question",      # 是否疑问句
    "has_technical",     # 含技术词
    "has_opinion",       # 含观点词
    "is_followup",       # 是否是追问
    "confidence_low",    # 模型置信度低 (语义熵高)
    "emotion_negative",  # 用户情绪负面
]

N_FEATURES = len(FEATURE_KEYS)       # 8
N_STRATEGIES = len(STRATEGY_NAMES)   # 5


class LinearMetaLearner:
    """
    线性元学习器 — 8维特征 → 5维策略分数的线性映射

    用 SGD (纯 numpy) 在线更新权重，不依赖任何框架。
    核心思想: 特征向量 x (8,) → W (8x5) 点乘 + bias (5,) → softmax → 策略分数

    收敛判据: 滑动窗口平均 loss < 0.05
    """

    def __init__(self, lr: float = 0.01, momentum: float = 0.9):
        self.lr = lr
        self.momentum = momentum

        # Xavier 初始化
        scale = np.sqrt(2.0 / (N_FEATURES + N_STRATEGIES))
        self.W = np.random.randn(N_FEATURES, N_STRATEGIES) * scale
        self.bias = np.zeros(N_STRATEGIES)

        # Momentum
        self.vW = np.zeros_like(self.W)
        self.vb = np.zeros_like(self.bias)

        # 训练统计
        self._train_count = 0
        self._loss_history = []
        self._window_size = 20

    def predict(self, features: np.ndarray) -> np.ndarray:
        """前向传播: features (N_FEATURES,) → scores (N_STRATEGIES,)"""
        logits = features @ self.W + self.bias  # (N_STRATEGIES,)
        # softmax
        exp = np.exp(logits - np.max(logits))
        return exp / (np.sum(exp) + 1e-10)

    def train_step(self, features: np.ndarray,
                   target_scores: np.ndarray) -> float:
        """
        一步 SGD 训练

        Args:
            features: 输入特征 (N_FEATURES,)
            target_scores: 目标分数 (N_STRATEGIES,) — 来自 Q-table 或 feedback

        Returns:
            loss: float 当前 step 的 loss
        """
        # 前向
        logits = features @ self.W + self.bias
        pred = np.exp(logits - np.max(logits))
        pred = pred / (np.sum(pred) + 1e-10)

        # MSE loss
        loss = np.mean((pred - target_scores) ** 2)

        # 梯度: dL/dlogits = 2 * (pred - target) * pred * (1 - pred) [近似]
        # 更准确的 softmax-MSE 梯度
        grad_logits = 2 * (pred - target_scores) * pred * (1 - pred)

        # 参数梯度
        grad_W = np.outer(features, grad_logits)  # (N_FEATURES, N_STRATEGIES)
        grad_b = grad_logits

        # Momentum SGD
        self.vW = self.momentum * self.vW - self.lr * grad_W
        self.vb = self.momentum * self.vb - self.lr * grad_b
        self.W += self.vW
        self.bias += self.vb

        # 统计
        self._train_count += 1
        self._loss_history.append(loss)
        if len(self._loss_history) > self._window_size * 2:
            self._loss_history = self._loss_history[-self._window_size * 2:]

        return float(loss)

    def is_converged(self, threshold: float = 0.05) -> bool:
        """检查元学习器是否已收敛 (滑动窗口平均 loss < threshold)"""
        if len(self._loss_history) < self._window_size:
            return False
        recent = self._loss_history[-self._window_size:]
        return float(np.mean(recent)) < threshold

    def get_convergence_status(self) -> Dict[str, Any]:
        """返回收敛状态"""
        recent = self._loss_history[-self._window_size:] if len(
            self._loss_history) >= self._window_size else self._loss_history
        avg_loss = float(np.mean(recent)) if recent else 1.0
        return {
            "avg_loss": round(avg_loss, 4),
            "is_converged": avg_loss < 0.05,
            "train_steps": self._train_count,
            "W_shape": list(self.W.shape),
        }

    def to_dict(self) -> Dict:
        """序列化为字典"""
        return {
            "W": self.W.tolist(),
            "bias": self.bias.tolist(),
            "vW": self.vW.tolist(),
            "vb": self.vb.tolist(),
            "lr": self.lr,
            "momentum": self.momentum,
            "train_count": self._train_count,
            "loss_history": self._loss_history[-100:],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "LinearMetaLearner":
        ml = cls(lr=data.get("lr", 0.01), momentum=data.get("momentum", 0.9))
        ml.W = np.array(data["W"])
        ml.bias = np.array(data["bias"])
        ml.vW = np.array(data.get("vW", np.zeros_like(ml.vW)))
        ml.vb = np.array(data.get("vb", np.zeros_like(ml.vb)))
        ml._train_count = data.get("train_count", 0)
        ml._loss_history = data.get("loss_history", [])
        return ml


class HyperRouter:
    """
    超网络路由 — 在线自适应策略选择器

    升级版: Q-table (冷启动) + LinearMetaLearner (元学习预测)
    已收敛时用元学习器，冷启动用 Q-table。
    """

    def __init__(self, storage_path: str = "", epsilon: float = 0.15,
                 alpha: float = 0.1, gamma: float = 0.9,
                 meta_lr: float = 0.01):
        """
        Args:
            epsilon: e-greedy 探索率 (0.05~0.3)
            alpha: Q-learning 学习率
            gamma: 折扣因子
            meta_lr: 元学习者学习率
        """
        base = os.environ.get("WORKSPACE",
                              os.path.expanduser("~/.openclaw/workspace"))
        self.q_path = storage_path or os.path.join(
            base, ".learnings", "hyper_router.json"
        )
        self.model_path = os.path.join(
            base, ".learnings", "hyper_router_model.json"
        )
        self.epsilon = epsilon
        self.alpha = alpha
        self.gamma = gamma

        # Q-table (冷启动)
        self.q_table = self._load_q()
        # 策略定义
        self.strategies = dict(DEFAULT_STRATEGIES)

        # 线性元学习器
        self.meta_learner = self._load_model() or LinearMetaLearner(lr=meta_lr)

        # 本轮回溯
        self._last_choice = None
        # 上一步特征缓存 (用于 feedback 中的 meta 训练)
        self._last_features = None

    # ──────────── 对外接口 ────────────

    def select_strategy(self, features: Dict[str, float],
                        force_strategy: str = "") -> Dict[str, Any]:
        """
        根据特征选择路由策略

        Meta 优先: 如果元学习器已收敛，直接用它预测
        冷启动: 用 Q-table + e-greedy

        Args:
            features: query 特征字典 (包含 FEATURE_KEYS 中的键)
            force_strategy: 强制指定策略

        Returns:
            name: str 策略名
            description: str 策略描述
            q_value: float Q值/预测分数
            is_explore: bool 是否探索
            source: str "meta" | "qtable" | "force"
        """
        if force_strategy and force_strategy in self.strategies:
            s = self.strategies[force_strategy]
            return {"name": force_strategy,
                    "description": s["description"],
                    "q_value": s.get("default_q", 0.5),
                    "is_explore": False,
                    "source": "force"}

        f_hash = self._feature_hash(features)
        q_row = self.q_table.get(f_hash, {})
        feat_vec = self._features_to_vector(features)

        # 缓存特征用于 feedback
        self._last_features = feat_vec

        # 决策逻辑: meta 优先
        meta = self.meta_learner
        meta_converged = meta.is_converged()

        if meta_converged:
            # 元学习器已收敛 → 直接用它预测
            scores = meta.predict(feat_vec)
            idx = int(np.argmax(scores))
            choice = STRATEGY_NAMES[idx]
            q_val = float(scores[idx])
            is_explore = False
            source = "meta"
        else:
            # 冷启动: Q-table + e-greedy
            if random.random() < self.epsilon:
                choice = random.choice(list(self.strategies.keys()))
                q_val = q_row.get(choice,
                                  self.strategies[choice]["default_q"])
                is_explore = True
                source = "explore"
            else:
                if q_row:
                    choice = max(q_row, key=lambda k: q_row.get(k, 0))
                    q_val = q_row[choice]
                else:
                    choice = self._default_match(features)
                    q_val = self.strategies[choice]["default_q"]
                is_explore = False
                source = "qtable"

            # 同时用当前结果训练 meta learner（监督信号来自 Q-table）
            if q_row:
                target = np.array([
                    q_row.get(s, self.strategies[s]["default_q"])
                    for s in STRATEGY_NAMES
                ], dtype=np.float32)
                meta.train_step(feat_vec, target)

        self._last_choice = {
            "features_hash": f_hash,
            "strategy": choice,
            "q_value": round(q_val, 4),
            "source": source,
        }

        return {"name": choice,
                "description": self.strategies[choice]["description"],
                "q_value": round(q_val, 4),
                "is_explore": is_explore,
                "source": source}

    def feedback(self, strategy: str, success: bool = True,
                 latency_ms: float = 0, error: bool = False) -> Dict:
        """
        反馈学习: 更新 Q-table + 训练 meta learner

        Args:
            strategy: 执行的策略名
            success: 是否成功
            latency_ms: 延迟 (ms)
            error: 是否出错

        Returns:
            updated_q: float 更新后的 Q 值
            meta_loss: float 元学习 loss
        """
        if not self._last_choice:
            return {"updated_q": 0, "meta_loss": 0}

        f_hash = self._last_choice["features_hash"]
        if f_hash not in self.q_table:
            self.q_table[f_hash] = {}

        # 奖励函数
        reward = 1.0 if success else -0.5
        if error:
            reward -= 0.5
        if latency_ms > 5000:
            reward -= 0.3
        elif latency_ms > 3000:
            reward -= 0.1

        old_q = self.q_table[f_hash].get(
            strategy, self.strategies[strategy]["default_q"])
        max_next = max(self.q_table[f_hash].values()
                       ) if self.q_table[f_hash] else 0
        new_q = old_q + self.alpha * (
            reward + self.gamma * max_next - old_q)
        self.q_table[f_hash][strategy] = round(new_q, 4)

        # 用 feedback 训练 meta learner
        meta_loss = 0.0
        if self._last_features is not None:
            target = np.zeros(N_STRATEGIES, dtype=np.float32)
            idx = STRATEGY_NAMES.index(strategy)
            # 给实际执行的策略更高分
            target[idx] = max(0, reward)
            # 补充其他策略的 Q 值
            for i, s in enumerate(STRATEGY_NAMES):
                if target[i] == 0:
                    target[i] = self.q_table.get(f_hash, {}).get(
                        s, self.strategies[s]["default_q"])
            # 归一化
            target = target / (np.sum(target) + 1e-10)
            meta_loss = self.meta_learner.train_step(
                self._last_features, target)

        self._save_q()
        self._save_model()
        return {"updated_q": round(new_q, 4),
                "meta_loss": round(meta_loss, 4)}

    def get_stats(self) -> Dict[str, Any]:
        """获取路由统计"""
        total_states = len(self.q_table)
        total_entries = sum(len(v) for v in self.q_table.values())
        strategy_usage = defaultdict(int)
        for row in self.q_table.values():
            for s in row:
                strategy_usage[s] += 1
        meta_status = self.meta_learner.get_convergence_status()
        return {
            "states_learned": total_states,
            "total_q_entries": total_entries,
            "strategy_coverage": dict(strategy_usage),
            "epsilon": self.epsilon,
            "meta_learner": meta_status,
        }

    def save_model(self, path: str = "") -> bool:
        """持久化 W 矩阵和 meta learner 参数"""
        p = path or self.model_path
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            data = self.meta_learner.to_dict()
            data["epsilon"] = self.epsilon
            data["alpha"] = self.alpha
            with open(p, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            return True
        except Exception as e:
            logger.warning(f"Save model failed: {e}")
            return False

    def load_model(self, path: str = "") -> bool:
        """加载持久化的 meta learner 参数"""
        p = path or self.model_path
        try:
            if os.path.exists(p):
                with open(p) as f:
                    data = json.load(f)
                self.meta_learner = LinearMetaLearner.from_dict(data)
                self.epsilon = data.get("epsilon", self.epsilon)
                self.alpha = data.get("alpha", self.alpha)
                return True
        except Exception as e:
            logger.warning(f"Load model failed: {e}")
        return False

    # ──────────── 内部 ────────────

    def _feature_hash(self, features: Dict[str, float]) -> str:
        buckets = []
        for k in FEATURE_KEYS:
            val = features.get(k, 0.5)
            if val < 0.33:
                b = "L"
            elif val < 0.66:
                b = "M"
            else:
                b = "H"
            buckets.append(f"{k[:2]}{b}")
        return "|".join(buckets)

    @staticmethod
    def _features_to_vector(features: Dict[str, float]) -> np.ndarray:
        """特征字典 → numpy 向量 (N_FEATURES,)"""
        vec = np.zeros(N_FEATURES, dtype=np.float32)
        for i, k in enumerate(FEATURE_KEYS):
            vec[i] = features.get(k, 0.5)
        return vec

    def _default_match(self, features: Dict[str, float]) -> str:
        if features.get("is_followup", 0) > 0.5:
            return "quick_recall"
        if features.get("has_technical", 0) > 0.5:
            return "deep_search"
        if features.get("has_opinion", 0) > 0.5:
            return "direct_answer"
        if features.get("complexity", 0) > 0.5:
            return "deep_search"
        if features.get("confidence_low", 0) > 0.5:
            return "multi_path"
        return "quick_recall"

    def _save_q(self):
        """保存 Q-table"""
        try:
            os.makedirs(os.path.dirname(self.q_path), exist_ok=True)
            trimmed = dict(list(self.q_table.items())[-200:])
            with open(self.q_path, "w") as f:
                json.dump({
                    "q_table": trimmed,
                    "epsilon": self.epsilon,
                    "alpha": self.alpha,
                }, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Save Q-table failed: {e}")

    def _load_q(self) -> Dict:
        try:
            if os.path.exists(self.q_path):
                with open(self.q_path) as f:
                    data = json.load(f)
                    return data.get("q_table", {})
        except Exception:
            pass
        return {}

    def _save_model(self):
        self.save_model()

    def _load_model(self) -> Optional[LinearMetaLearner]:
        try:
            if os.path.exists(self.model_path):
                with open(self.model_path) as f:
                    data = json.load(f)
                return LinearMetaLearner.from_dict(data)
        except Exception:
            pass
        return None


def extract_features(query: str, semantic_entropy: float = 0.5,
                     is_followup: bool = False) -> Dict[str, float]:
    """从 query 提取路由特征"""
    import re
    q_len = len(query)

    tech_words = {"代码", "bug", "api", "python", "架构", "配置", "部署",
                  "命令", "文件", "脚本", "协议", "接口", "错误", "报错",
                  "依赖", "版本", "升级", "日志", "缓存", "线程", "进程"}
    opinion_words = {"觉得", "认为", "建议", "推荐", "哪个好", "比较",
                     "更好", "选择", "犹豫"}
    q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
    has_tech = len(q_words & tech_words) > 0
    has_opinion = len(q_words & opinion_words) > 0
    has_q = "?" in query or "？" in query

    complexity = 0.1
    if has_tech:
        complexity += 0.3
    if has_q and len(query) > 20:
        complexity += 0.3
    if is_followup:
        complexity += 0.2
    complexity = min(complexity, 1.0)

    return {
        "query_len": min(q_len / 200, 1.0),
        "complexity": complexity,
        "has_question": 1.0 if has_q else 0.0,
        "has_technical": 1.0 if has_tech else 0.0,
        "has_opinion": 1.0 if has_opinion else 0.0,
        "is_followup": 1.0 if is_followup else 0.0,
        "confidence_low": 1.0 if semantic_entropy > 0.6 else 0.0,
        "emotion_negative": 0.0,
    }


# 全局单例
_instance = None


def get_hyper_router(storage_path: str = "") -> HyperRouter:
    global _instance
    if _instance is None:
        _instance = HyperRouter(storage_path)
    return _instance


if __name__ == "__main__":
    hr = HyperRouter()
    feat = extract_features("帮我用python写个爬虫?", 0.3)
    s = hr.select_strategy(feat)
    print(f"Selected: {s}")
    r = hr.feedback(s["name"], success=True, latency_ms=2000)
    print(f"Feedback: {r}")
    print(f"Stats: {hr.get_stats()}")
    print(f"Meta converged: {hr.meta_learner.is_converged()}")
