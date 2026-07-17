#!/usr/bin/env python3
"""
自适应记忆架构 (Adaptive Memory Architecture)

系统自动优化记忆参数：
- 监控记忆系统性能
- 分析效率问题
- 自动调整参数
- 持续优化

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-19
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict, field
from galaxyos.shared.paths import workspace


# ==================== 记忆参数 ====================

@dataclass
class MemoryParameters:
    """记忆系统参数"""

    # 召回参数
    recall_threshold: float = 0.25      # 召回阈值
    max_recall_results: int = 10        # 最大召回数

    # 遗忘参数
    forget_threshold: float = 0.1       # 遗忘阈值
    decay_rate: float = 0.01            # 衰减率

    # 突触参数
    ltp_strength: float = 0.1           # LTP 增强强度
    ltd_rate: float = 0.01              # LTD 抑制率
    synapse_threshold: float = 0.3      # 突触激活阈值

    # 情感参数
    emotion_weight_factor: float = 0.5  # 情感权重因子

    # 反思参数
    reflection_frequency: int = 3       # 反思频率（每 N 次对话）
    auto_apply_threshold: float = 0.8   # 自动应用阈值

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'MemoryParameters':
        return cls(**data)


# ==================== 性能指标 ====================

@dataclass
class PerformanceMetrics:
    """性能指标"""

    # 召回性能
    recall_precision: float = 0.0  # 召回精度
    recall_recall: float = 0.0     # 召回率
    recall_f1: float = 0.0         # F1 分数

    # 遗忘性能
    forget_accuracy: float = 0.0   # 遗忘准确率
    false_forget_rate: float = 0.0 # 误遗忘率

    # 用户满意度（基于反馈）
    user_satisfaction: float = 0.0

    # 系统效率
    avg_recall_time_ms: float = 0.0  # 平均召回时间
    memory_usage_mb: float = 0.0     # 内存使用

    # 时间戳
    measured_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ==================== 性能分析器 ====================

class PerformanceAnalyzer:
    """性能分析器"""

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.metrics_path = self.workspace_path / ".learnings" / "performance_metrics.jsonl"

        # 确保目录存在
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.metrics_path.exists():
            self.metrics_path.touch()

    def _get_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def measure_recall_performance(self) -> tuple:
        """
        测量召回性能

        Returns:
            (precision, recall, f1)
        """
        # 简化实现：基于反思记录估算
        # 实际应该基于用户反馈

        reflections_path = self.workspace_path / ".learnings" / "REFLECTIONS.jsonl"

        if not reflections_path.exists():
            return 0.8, 0.7, 0.75  # 默认值

        # 统计用户纠正次数
        total_recalls = 0
        user_corrections = 0

        with open(reflections_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if record["type"] == "error":
                        total_recalls += 1
                        if record["data"]["type"] == "user_correction":
                            user_corrections += 1

        if total_recalls == 0:
            return 0.8, 0.7, 0.75

        # 用户纠正越多，精度越低
        precision = max(0.5, 1.0 - user_corrections / total_recalls * 0.5)

        # 召回率假设较高
        recall = 0.75

        # F1
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        return precision, recall, f1

    def measure_forget_performance(self) -> tuple:
        """
        测量遗忘性能

        Returns:
            (accuracy, false_forget_rate)
        """
        # 简化实现：基于记忆数量估算

        memory_path = self.workspace_path / ".learnings" / "emotion_memories.jsonl"

        if not memory_path.exists():
            return 0.85, 0.1  # 默认值

        # 统计高优先级记忆比例
        total = 0
        high_priority = 0

        with open(memory_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    memory = json.loads(line)
                    total += 1
                    if memory.get("priority") == "high":
                        high_priority += 1

        if total == 0:
            return 0.85, 0.1

        # 高优先级记忆比例越高，遗忘准确率越高
        accuracy = 0.7 + 0.3 * (high_priority / total) if total > 0 else 0.85

        # 误遗忘率
        false_forget_rate = 1.0 - accuracy

        return accuracy, false_forget_rate

    def measure_system_efficiency(self) -> tuple:
        """
        测量系统效率

        Returns:
            (avg_recall_time_ms, memory_usage_mb)
        """
        import psutil

        # 内存使用
        process = psutil.Process(os.getpid())
        memory_usage_mb = process.memory_info().rss / 1024 / 1024

        # 召回时间（假设值）
        avg_recall_time_ms = 50.0  # 50ms

        return avg_recall_time_ms, memory_usage_mb

    def analyze(self) -> PerformanceMetrics:
        """
        全面分析性能

        Returns:
            PerformanceMetrics
        """
        # 召回性能
        precision, recall, f1 = self.measure_recall_performance()

        # 遗忘性能
        forget_accuracy, false_forget_rate = self.measure_forget_performance()

        # 系统效率
        avg_recall_time_ms, memory_usage_mb = self.measure_system_efficiency()

        metrics = PerformanceMetrics(
            recall_precision=precision,
            recall_recall=recall,
            recall_f1=f1,
            forget_accuracy=forget_accuracy,
            false_forget_rate=false_forget_rate,
            user_satisfaction=(precision + forget_accuracy) / 2,
            avg_recall_time_ms=avg_recall_time_ms,
            memory_usage_mb=memory_usage_mb,
            measured_at=self._get_timestamp()
        )

        # 保存指标
        self._save_metrics(metrics)

        return metrics

    def _save_metrics(self, metrics: PerformanceMetrics):
        """保存性能指标"""
        with open(self.metrics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics.to_dict(), ensure_ascii=False) + "\n")

    def get_recent_metrics(self, limit: int = 10) -> List[PerformanceMetrics]:
        """获取最近的性能指标"""
        metrics = []

        with open(self.metrics_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    metrics.append(PerformanceMetrics.from_dict(json.loads(line)))

        return metrics[-limit:]


# ==================== 参数优化器 ====================

class ParameterOptimizer:
    """参数优化器"""

    # 优化规则
    OPTIMIZATION_RULES = [
        {
            "condition": lambda m: m.recall_recall < 0.7,
            "action": "decrease_recall_threshold",
            "param": "recall_threshold",
            "adjustment": 0.9,  # 乘以 0.9
            "reason": "召回率过低，降低阈值"
        },
        {
            "condition": lambda m: m.recall_precision < 0.7,
            "action": "increase_recall_threshold",
            "param": "recall_threshold",
            "adjustment": 1.1,  # 乘以 1.1
            "reason": "召回精度过低，提高阈值"
        },
        {
            "condition": lambda m: m.forget_accuracy < 0.8,
            "action": "decrease_decay_rate",
            "param": "decay_rate",
            "adjustment": 0.9,
            "reason": "遗忘准确率过低，降低衰减率"
        },
        {
            "condition": lambda m: m.false_forget_rate > 0.2,
            "action": "decrease_decay_rate",
            "param": "decay_rate",
            "adjustment": 0.8,
            "reason": "误遗忘率过高，大幅降低衰减率"
        },
        {
            "condition": lambda m: m.avg_recall_time_ms > 100,
            "action": "decrease_max_recall",
            "param": "max_recall_results",
            "adjustment": 0.8,
            "reason": "召回时间过长，减少召回数量"
        },
    ]

    def __init__(self, params: MemoryParameters = None):
        self.params = params or MemoryParameters()

    def optimize(self, metrics: PerformanceMetrics) -> Dict:
        """
        根据性能指标优化参数

        Args:
            metrics: 性能指标

        Returns:
            {
                "adjustments": [...],
                "new_params": MemoryParameters
            }
        """
        adjustments = []

        for rule in self.OPTIMIZATION_RULES:
            if rule["condition"](metrics):
                param_name = rule["param"]
                current_value = getattr(self.params, param_name)
                new_value = current_value * rule["adjustment"]

                # 限制范围
                if param_name in ["recall_threshold", "forget_threshold", "synapse_threshold"]:
                    new_value = max(0.1, min(0.9, new_value))
                elif param_name in ["decay_rate", "ltd_rate", "ltp_strength"]:
                    new_value = max(0.001, min(0.5, new_value))
                elif param_name == "max_recall_results":
                    new_value = int(max(3, min(50, new_value)))

                setattr(self.params, param_name, new_value)

                adjustments.append({
                    "param": param_name,
                    "old_value": current_value,
                    "new_value": new_value,
                    "reason": rule["reason"]
                })

        return {
            "adjustments": adjustments,
            "new_params": self.params
        }


# ==================== 自适应管理器 ====================

class AdaptiveMemoryManager:
    """
    自适应记忆管理器

    集成性能分析和参数优化
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.params_path = self.workspace_path / ".learnings" / "memory_params.json"

        # 加载或初始化参数
        self.params = self._load_params()

        self.analyzer = PerformanceAnalyzer(workspace_path)
        self.optimizer = ParameterOptimizer(self.params)

    def _load_params(self) -> MemoryParameters:
        """加载参数"""
        if self.params_path.exists():
            with open(self.params_path, "r", encoding="utf-8") as f:
                return MemoryParameters.from_dict(json.load(f))
        return MemoryParameters()

    def _save_params(self):
        """保存参数"""
        with open(self.params_path, "w", encoding="utf-8") as f:
            json.dump(self.params.to_dict(), f, indent=2, ensure_ascii=False)

    def run_optimization_cycle(self) -> Dict:
        """
        运行优化周期

        Returns:
            {
                "metrics": PerformanceMetrics,
                "optimization": {...},
                "params_changed": bool
            }
        """
        # 1. 分析性能
        metrics = self.analyzer.analyze()

        # 2. 优化参数
        optimization = self.optimizer.optimize(metrics)

        # 3. 更新参数
        self.params = optimization["new_params"]

        # 4. 保存参数
        if optimization["adjustments"]:
            self._save_params()

        return {
            "metrics": metrics.to_dict(),
            "optimization": optimization,
            "params_changed": len(optimization["adjustments"]) > 0
        }

    def get_current_params(self) -> MemoryParameters:
        """获取当前参数"""
        return self.params

    def get_optimization_history(self, limit: int = 10) -> List[Dict]:
        """获取优化历史"""
        metrics = self.analyzer.get_recent_metrics(limit)
        return [m.to_dict() for m in metrics]


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="自适应记忆架构")
    parser.add_argument("command", choices=["analyze", "optimize", "params", "history"])
    parser.add_argument("--limit", type=int, default=10, help="返回数量限制")

    args = parser.parse_args()

    manager = AdaptiveMemoryManager()

    if args.command == "analyze":
        metrics = manager.analyzer.analyze()
        print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))

    elif args.command == "optimize":
        result = manager.run_optimization_cycle()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "params":
        params = manager.get_current_params()
        print(json.dumps(params.to_dict(), indent=2, ensure_ascii=False))

    elif args.command == "history":
        history = manager.get_optimization_history(args.limit)
        for record in history:
            print(f"[{record['measured_at']}] F1: {record['recall_f1']:.3f}, 遗忘准确率: {record['forget_accuracy']:.3f}")


if __name__ == "__main__":
    main()
