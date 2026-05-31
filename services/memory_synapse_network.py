#!/usr/bin/env python3
"""
记忆突触网络 (Memory Synapse Network)

模拟神经元连接和突触可塑性：
- 记忆条目 = 神经元
- 记忆关联 = 突触
- 使用频率 → LTP（长时程增强）
- 长期不用 → LTD（长时程抑制）

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-19
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
import hashlib


# ==================== 数据结构 ====================

class SynapseType(Enum):
    """突触类型"""
    EXCITATORY = "excitatory"  # 兴奋性（增强目标）
    INHIBITORY = "inhibitory"  # 抑制性（抑制目标）


@dataclass
class MemoryNeuron:
    """记忆神经元"""
    id: str
    content: str
    embedding: List[float] = field(default_factory=list)
    created_at: str = ""
    last_activated: str = ""
    activation_count: int = 0
    
    # 神经元状态
    potential: float = 0.0  # 膜电位
    refractory_until: str = ""  # 不应期
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'MemoryNeuron':
        return cls(**data)


@dataclass
class Synapse:
    """突触连接"""
    id: str
    source_id: str  # 源神经元 ID
    target_id: str  # 目标神经元 ID
    weight: float = 0.5  # 突触权重 (0.0 - 1.0)
    type: SynapseType = SynapseType.EXCITATORY
    
    created_at: str = ""
    last_reinforced: str = ""
    reinforcement_count: int = 0
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        result["type"] = self.type.value
        return result
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Synapse':
        data["type"] = SynapseType(data["type"])
        return cls(**data)


# ==================== 突触网络 ====================

class SynapseNetwork:
    """突触网络管理器"""
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or os.path.expanduser("~/.openclaw/workspace"))
        self.network_path = self.workspace_path / ".learnings" / "synapse_network"
        
        # 数据文件
        self.neurons_path = self.network_path / "neurons.jsonl"
        self.synapses_path = self.network_path / "synapses.jsonl"
        
        # 确保目录存在
        self.network_path.mkdir(parents=True, exist_ok=True)
        
        # 初始化文件
        for path in [self.neurons_path, self.synapses_path]:
            if not path.exists():
                path.touch()
        
        # 缓存
        self._neurons_cache: Dict[str, MemoryNeuron] = {}
        self._synapses_cache: Dict[str, Synapse] = {}
        self._loaded = False
    
    def _load(self):
        """加载数据到缓存"""
        if self._loaded:
            return
        
        # 加载神经元
        with open(self.neurons_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    neuron = MemoryNeuron.from_dict(json.loads(line))
                    self._neurons_cache[neuron.id] = neuron
        
        # 加载突触
        with open(self.synapses_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    synapse = Synapse.from_dict(json.loads(line))
                    self._synapses_cache[synapse.id] = synapse
        
        self._loaded = True
    
    def _save_neuron(self, neuron: MemoryNeuron):
        """保存神经元"""
        with open(self.neurons_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(neuron.to_dict(), ensure_ascii=False) + "\n")
    
    def _save_synapse(self, synapse: Synapse):
        """保存突触"""
        with open(self.synapses_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(synapse.to_dict(), ensure_ascii=False) + "\n")
    
    def _get_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def _generate_id(self, prefix: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = hashlib.md5(str(datetime.now().timestamp()).encode()).hexdigest()[:8]
        return f"{prefix}-{timestamp}-{random_suffix}"


# ==================== 神经元操作 ====================

class NeuronManager:
    """神经元管理器"""
    
    def __init__(self, network: SynapseNetwork):
        self.network = network
    
    def create_neuron(
        self,
        content: str,
        embedding: List[float] = None,
        neuron_id: str = None
    ) -> MemoryNeuron:
        """创建神经元"""
        self.network._load()
        
        neuron = MemoryNeuron(
            id=neuron_id or self.network._generate_id("NRN"),
            content=content,
            embedding=embedding or [],
            created_at=self.network._get_timestamp(),
            last_activated=self.network._get_timestamp(),
            activation_count=1
        )
        
        self.network._neurons_cache[neuron.id] = neuron
        self.network._save_neuron(neuron)
        
        return neuron
    
    def get_neuron(self, neuron_id: str) -> Optional[MemoryNeuron]:
        """获取神经元"""
        self.network._load()
        return self.network._neurons_cache.get(neuron_id)
    
    def activate_neuron(self, neuron_id: str) -> Optional[MemoryNeuron]:
        """激活神经元"""
        neuron = self.get_neuron(neuron_id)
        if not neuron:
            return None
        
        neuron.activation_count += 1
        neuron.last_activated = self.network._get_timestamp()
        
        return neuron
    
    def find_neuron_by_content(self, content: str) -> Optional[MemoryNeuron]:
        """根据内容查找神经元"""
        self.network._load()
        
        for neuron in self.network._neurons_cache.values():
            if neuron.content == content:
                return neuron
        
        return None
    
    def get_all_neurons(self) -> List[MemoryNeuron]:
        """获取所有神经元"""
        self.network._load()
        return list(self.network._neurons_cache.values())


# ==================== 突触操作 ====================

class SynapseManager:
    """突触管理器"""
    
    # LTP/LTD 参数
    LTP_STRENGTH = 0.1  # LTP 增强强度
    LTD_RATE = 0.01     # LTD 抑制率
    MIN_WEIGHT = 0.0
    MAX_WEIGHT = 1.0
    DECAY_THRESHOLD_DAYS = 7  # 超过 7 天未用开始衰减
    
    def __init__(self, network: SynapseNetwork):
        self.network = network
    
    def create_synapse(
        self,
        source_id: str,
        target_id: str,
        weight: float = 0.5,
        type: SynapseType = SynapseType.EXCITATORY
    ) -> Synapse:
        """创建突触"""
        self.network._load()
        
        # 检查是否已存在
        existing = self.get_synapse(source_id, target_id)
        if existing:
            return existing
        
        synapse = Synapse(
            id=self.network._generate_id("SYN"),
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            type=type,
            created_at=self.network._get_timestamp(),
            last_reinforced=self.network._get_timestamp(),
            reinforcement_count=1
        )
        
        self.network._synapses_cache[synapse.id] = synapse
        self.network._save_synapse(synapse)
        
        return synapse
    
    def get_synapse(self, source_id: str, target_id: str) -> Optional[Synapse]:
        """获取突触"""
        self.network._load()
        
        for synapse in self.network._synapses_cache.values():
            if synapse.source_id == source_id and synapse.target_id == target_id:
                return synapse
        
        return None
    
    def get_outgoing_synapses(self, neuron_id: str) -> List[Synapse]:
        """获取神经元的所有输出突触"""
        self.network._load()
        
        return [
            s for s in self.network._synapses_cache.values()
            if s.source_id == neuron_id
        ]
    
    def get_incoming_synapses(self, neuron_id: str) -> List[Synapse]:
        """获取神经元的所有输入突触"""
        self.network._load()
        
        return [
            s for s in self.network._synapses_cache.values()
            if s.target_id == neuron_id
        ]
    
    def ltp(self, synapse: Synapse, strength: float = None) -> Synapse:
        """
        长时程增强 (Long-Term Potentiation)
        
        每次使用时增加突触权重
        """
        strength = strength or self.LTP_STRENGTH
        
        synapse.weight = min(
            self.MAX_WEIGHT,
            synapse.weight + strength
        )
        synapse.last_reinforced = self.network._get_timestamp()
        synapse.reinforcement_count += 1
        
        return synapse
    
    def ltd(self, synapse: Synapse, decay_rate: float = None) -> Synapse:
        """
        长时程抑制 (Long-Term Depression)
        
        长期不用时衰减突触权重
        """
        decay_rate = decay_rate or self.LTD_RATE
        
        # 计算未使用天数
        last_reinforced = datetime.fromisoformat(synapse.last_reinforced)
        days_unused = (datetime.now(timezone.utc) - last_reinforced).days
        
        if days_unused > self.DECAY_THRESHOLD_DAYS:
            decay_amount = decay_rate * (days_unused - self.DECAY_THRESHOLD_DAYS)
            synapse.weight = max(
                self.MIN_WEIGHT,
                synapse.weight - decay_amount
            )
        
        return synapse
    
    def apply_decay_to_all(self):
        """对所有突触应用衰减"""
        self.network._load()
        
        for synapse in self.network._synapses_cache.values():
            self.ltd(synapse)


# ==================== 激活传播 ====================

class ActivationSpreader:
    """激活传播器"""
    
    DEFAULT_THRESHOLD = 0.3
    MAX_DEPTH = 3  # 最大传播深度
    
    def __init__(self, network: SynapseNetwork):
        self.network = network
        self.synapse_manager = SynapseManager(network)
        self.neuron_manager = NeuronManager(network)
    
    def spread_activation(
        self,
        neuron_id: str,
        threshold: float = None,
        max_depth: int = None
    ) -> List[Tuple[MemoryNeuron, float]]:
        """
        激活传播
        
        从指定神经元开始，激活相关联的神经元
        
        Args:
            neuron_id: 起始神经元 ID
            threshold: 突触权重阈值
            max_depth: 最大传播深度
        
        Returns:
            [(神经元, 激活强度), ...]
        """
        threshold = threshold or self.DEFAULT_THRESHOLD
        max_depth = max_depth or self.MAX_DEPTH
        
        activated = []
        visited = set()
        
        # BFS 传播
        queue = [(neuron_id, 1.0, 0)]  # (neuron_id, strength, depth)
        
        while queue:
            current_id, strength, depth = queue.pop(0)
            
            if current_id in visited or depth > max_depth:
                continue
            
            visited.add(current_id)
            
            # 激活当前神经元
            neuron = self.neuron_manager.activate_neuron(current_id)
            if neuron:
                activated.append((neuron, strength))
            
            # 获取输出突触
            synapses = self.synapse_manager.get_outgoing_synapses(current_id)
            
            for synapse in synapses:
                if synapse.weight >= threshold:
                    # 计算传播强度
                    propagated_strength = strength * synapse.weight
                    
                    # 增强 synapse (LTP)
                    self.synapse_manager.ltp(synapse)
                    
                    # 加入队列
                    queue.append((synapse.target_id, propagated_strength, depth + 1))
        
        return activated
    
    def find_associated_memories(
        self,
        neuron_id: str,
        top_k: int = 5
    ) -> List[Tuple[MemoryNeuron, float]]:
        """
        查找关联记忆
        
        Args:
            neuron_id: 起始神经元 ID
            top_k: 返回前 K 个
        
        Returns:
            [(神经元, 关联强度), ...]
        """
        activated = self.spread_activation(neuron_id)
        
        # 排除起始神经元
        activated = [(n, s) for n, s in activated if n.id != neuron_id]
        
        # 按强度排序
        activated.sort(key=lambda x: x[1], reverse=True)
        
        return activated[:top_k]


# ==================== 主类：记忆突触网络 ====================

class MemorySynapseNetwork:
    """
    记忆突触网络
    
    使用示例:
        network = MemorySynapseNetwork()
        
        # 创建神经元
        n1 = network.create_neuron("Python 项目")
        n2 = network.create_neuron("修复了一个 bug")
        
        # 创建突触连接
        network.create_synapse(n1.id, n2.id)
        
        # 激活传播
        associated = network.find_associated_memories(n1.id)
        for neuron, strength in associated:
            print(f"{neuron.content}: {strength}")
    """
    
    def __init__(self, workspace_path: str = None):
        self.network = SynapseNetwork(workspace_path)
        self.neuron_manager = NeuronManager(self.network)
        self.synapse_manager = SynapseManager(self.network)
        self.activation_spreader = ActivationSpreader(self.network)
    
    def create_neuron(self, content: str, embedding: List[float] = None) -> MemoryNeuron:
        """创建记忆神经元"""
        return self.neuron_manager.create_neuron(content, embedding)
    
    def create_synapse(
        self,
        source_id: str,
        target_id: str,
        weight: float = 0.5
    ) -> Synapse:
        """创建突触连接"""
        return self.synapse_manager.create_synapse(source_id, target_id, weight)
    
    def activate(self, neuron_id: str) -> List[Tuple[MemoryNeuron, float]]:
        """激活神经元并传播"""
        return self.activation_spreader.spread_activation(neuron_id)
    
    def find_associated(self, neuron_id: str, top_k: int = 5) -> List[Tuple[MemoryNeuron, float]]:
        """查找关联记忆"""
        return self.activation_spreader.find_associated_memories(neuron_id, top_k)
    
    def apply_decay(self):
        """应用突触衰减"""
        self.synapse_manager.apply_decay_to_all()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取网络统计"""
        self.network._load()
        
        neurons = self.network._neurons_cache
        synapses = self.network._synapses_cache
        
        # 计算平均激活次数
        avg_activation = sum(n.activation_count for n in neurons.values()) / len(neurons) if neurons else 0
        
        # 计算平均突触权重
        avg_weight = sum(s.weight for s in synapses.values()) / len(synapses) if synapses else 0
        
        return {
            "total_neurons": len(neurons),
            "total_synapses": len(synapses),
            "avg_activation_count": round(avg_activation, 2),
            "avg_synapse_weight": round(avg_weight, 3),
            "strong_synapses": len([s for s in synapses.values() if s.weight > 0.7]),
            "weak_synapses": len([s for s in synapses.values() if s.weight < 0.3]),
        }


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="记忆突触网络")
    parser.add_argument("command", choices=["create-neuron", "create-synapse", "activate", "stats"])
    parser.add_argument("--content", help="神经元内容")
    parser.add_argument("--source", help="源神经元 ID")
    parser.add_argument("--target", help="目标神经元 ID")
    parser.add_argument("--neuron", help="神经元 ID")
    parser.add_argument("--weight", type=float, default=0.5, help="突触权重")
    
    args = parser.parse_args()
    
    network = MemorySynapseNetwork()
    
    if args.command == "create-neuron":
        if not args.content:
            print("错误: 需要提供 --content")
            return
        neuron = network.create_neuron(args.content)
        print(f"创建神经元: {neuron.id}")
    
    elif args.command == "create-synapse":
        if not args.source or not args.target:
            print("错误: 需要提供 --source 和 --target")
            return
        synapse = network.create_synapse(args.source, args.target, args.weight)
        print(f"创建突触: {synapse.id} (权重: {synapse.weight})")
    
    elif args.command == "activate":
        if not args.neuron:
            print("错误: 需要提供 --neuron")
            return
        associated = network.find_associated(args.neuron)
        print(f"关联记忆 ({len(associated)} 个):")
        for neuron, strength in associated:
            print(f"  - {neuron.content[:50]}... (强度: {strength:.3f})")
    
    elif args.command == "stats":
        stats = network.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
