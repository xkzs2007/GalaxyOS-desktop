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
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
import hashlib

import torch

# ═══ NLP 模块（可选导入） ═══
_NLP_AVAILABLE = False
try:
    import sys as _nlp_sys
    import os as _nlp_os
    _nlp_dir = _nlp_os.path.dirname(_nlp_os.path.abspath(__file__))
    if _nlp_dir not in _nlp_sys.path:
        _nlp_sys.path.insert(0, _nlp_dir)
    import nlp_integration
    _NLP_AVAILABLE = hasattr(nlp_integration, 'get_nlp_integration')
except ImportError:
    pass

# ncps LTC 引擎（延迟 import，安装检查）
_NCPS_AVAILABLE = False
try:
    from ncps.torch import LTCCell
    from ncps.wirings import FullyConnected
    _NCPS_AVAILABLE = True
except ImportError:
    pass

# ==================== LTC 突触集成 ====================

from ltc_synapse import LTCConfig, PRESETS, LTCBatchOptimizer


def _days_since(ts: str) -> float:
    """计算ISO时间戳距今天数"""
    if not ts:
        return 0.0
    try:
        t = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        return (now - t).total_seconds() / 86400.0
    except Exception:
        return 0.0


# ==================== 神经元 LTC 引擎 ====================

_LTC_TEMPLATE: Optional[LTCCell] = None


def _get_ltc_template() -> Optional[LTCCell]:
    """获取共享 LTCCell 模板（input=2, hidden=1）"""
    global _LTC_TEMPLATE
    if not _NCPS_AVAILABLE:
        return None
    if _LTC_TEMPLATE is None:
        _LTC_TEMPLATE = LTCCell(FullyConnected(2, 1), 2, 1)
    return _LTC_TEMPLATE


def _build_ltc_cell_from_params(params_dict: dict) -> Optional[LTCCell]:
    """从序列化参数重建 LTCCell"""
    if not _NCPS_AVAILABLE or not params_dict:
        return None
    template = _get_ltc_template()
    if template is None:
        return None
    cell = LTCCell(FullyConnected(2, 1), 2, 1)
    state_dict = {}
    for name, param in template.named_parameters():
        if name in params_dict:
            state_dict[name] = torch.tensor(params_dict[name]).reshape(param.shape)
    for name, buf in template.named_buffers():
        if name in params_dict:
            state_dict[name] = torch.tensor(params_dict[name]).reshape(buf.shape)
    if state_dict:
        cell.load_state_dict(state_dict, strict=False)
    return cell


def _params_to_dict(cell: LTCCell) -> dict:
    """将 LTCCell 权重展平为序列化词典"""
    d = {}
    for name, p in cell.named_parameters():
        d[name] = p.detach().flatten().tolist()
    for name, b in cell.named_buffers():
        d[name] = b.detach().flatten().tolist()
    return d


def _init_ltc_params() -> str:
    """创建新神经元的 LTC 初始参数（JSON 字符串）"""
    if not _NCPS_AVAILABLE:
        return ""
    cell = LTCCell(FullyConnected(2, 1), 2, 1)
    return json.dumps(_params_to_dict(cell))


# ==================== 数据结构 ====================

class SynapseType(Enum):
    """突触类型"""
    EXCITATORY = "excitatory"  # 兴奋性（增强目标）
    INHIBITORY = "inhibitory"  # 抑制性（抑制目标）


@dataclass
class MemoryNeuron:
    """记忆神经元 — 支持 LTCCell 连续时间状态演化"""
    id: str
    content: str
    embedding: List[float] = field(default_factory=list)
    created_at: str = ""
    last_activated: str = ""
    activation_count: int = 0

    # 神经元状态
    potential: float = 0.0  # 膜电位（fallback）
    refractory_until: str = ""  # 不应期

    # LTC 神经元状态演化
    ltc_cell_params: str = ""  # JSON: 48 个 LTC 权重展平值
    ltc_hidden: float = 0.0  # 当前 ODE 隐藏状态 h_t ∈ [0,1]

    # ────────── NLP 语义标签 ──────────
    nlp_keywords: str = ""  # JSON: [关键词列表]
    nlp_entities: str = ""  # JSON: {实体类型: [实体文本列表]}
    nlp_sentiment: str = ""  # JSON: {label, score, confidence}
    nlp_importance: float = 0.5  # NLP 重要度 [0,1]

    # ────────── 内存缓存（不序列化） ──────────
    _cell_cache: Optional[LTCCell] = None

    def _get_cell(self) -> Optional[LTCCell]:
        """获取缓存的 LTCCell，按需重建一次"""
        if not self.ltc_cell_params:
            return None
        if self._cell_cache is not None:
            return self._cell_cache
        try:
            self._cell_cache = _build_ltc_cell_from_params(json.loads(self.ltc_cell_params))
        except Exception:
            return None
        return self._cell_cache

    def evaluate_state(self) -> float:
        """
        计算当前兴奋度 [0,1]

        LTCCell 输入 = [0.0, time_encoding]
        - time_encoding = 1/(1+exp(days/30-5))  // 软阈值编码
        - 长期不用 → ODE 自然衰减
        """
        if not self.ltc_cell_params:
            return self.potential

        days = _days_since(self.last_activated)
        if days < 0:
            return self.ltc_hidden

        cell = self._get_cell()
        if cell is None:
            return self.potential

        # 时间编码：软 sigmoid，30 天中点
        time_enc = 1.0 / (1.0 + math.exp(days / 30.0 - 5.0))
        inp = torch.tensor([[0.0, time_enc]], dtype=torch.float32)
        hx = torch.tensor([[self.ltc_hidden]], dtype=torch.float32)
        _, h_new = cell(inp, hx)
        self.ltc_hidden = float(torch.clamp(h_new[0, 0].detach(), 0.0, 1.0))
        return self.ltc_hidden

    def apply_activation_signal(self, strength: float = 1.0):
        """
        激活信号 → LTP 效果

        LTCCell 输入 = [strength, 0.0]
        使 h_t 上升，模拟长时程增强
        """
        if not self.ltc_cell_params:
            self.potential = min(1.0, self.potential + strength * 0.1)
            self.ltc_hidden = self.potential
            return

        cell = self._get_cell()
        if cell is None:
            self.potential = min(1.0, self.potential + strength * 0.1)
            self.ltc_hidden = self.potential
            return

        inp = torch.tensor([[strength, 0.0]], dtype=torch.float32)
        hx = torch.tensor([[self.ltc_hidden]], dtype=torch.float32)
        _, h_new = cell(inp, hx)
        self.ltc_hidden = float(torch.clamp(h_new[0, 0].detach(), 0.0, 1.0))

    def _build_cell(self) -> Optional[LTCCell]:
        """从已存储的序列化参数重建 LTCCell"""
        if not self.ltc_cell_params:
            return None
        try:
            return _build_ltc_cell_from_params(json.loads(self.ltc_cell_params))
        except Exception:
            return None

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'MemoryNeuron':
        # 向后兼容：旧文件不含 ltc / nlp 字段
        data.setdefault('ltc_cell_params', '')
        data.setdefault('ltc_hidden', 0.0)
        data.setdefault('nlp_keywords', '')
        data.setdefault('nlp_entities', '')
        data.setdefault('nlp_sentiment', '')
        data.setdefault('nlp_importance', 0.5)
        return cls(**data)


@dataclass
class Synapse:
    """突触连接（支持 LTC 液态时间常数）"""
    id: str
    source_id: str  # 源神经元 ID
    target_id: str  # 目标神经元 ID
    weight: float = 0.5  # 突触权重 (0.0 - 1.0)
    type: SynapseType = SynapseType.EXCITATORY
    
    created_at: str = ""
    last_reinforced: str = ""
    reinforcement_count: int = 0
    
    # LTC 参数（序列化为 JSON 字符串）
    ltc_params: str = ""  # LTCConfig.to_json()
    
    def _get_ltc(self) -> Optional[LTCConfig]:
        if not self.ltc_params:
            return None
        try:
            return LTCConfig.from_dict(json.loads(self.ltc_params))
        except Exception:
            return None
    
    def _set_ltc(self, cfg: LTCConfig):
        self.ltc_params = json.dumps(cfg.to_dict())
    
    def compute_ltc_weight(self, src_hidden: float = 0.5,
                           dst_hidden: float = 0.5) -> float:
        """
        用 LTC 动态计算当前权重

        考虑前/后突触神经元的 LTC 兴奋度：
          base = LTCConfig 时间衰减
          modulation = (src_hidden + dst_hidden) / 2.0
          final = base × (0.5 + 0.5 × modulation)

        Args:
            src_hidden: 源神经元 LTC h_t（0-1）
            dst_hidden: 目标神经元 LTC h_t（0-1）
        """
        ltc = self._get_ltc()
        if ltc is None:
            return self.weight
        days = _days_since(self.last_reinforced)
        base = ltc.compute_weight(days)
        # 神经元活跃度调制
        modulation = (src_hidden + dst_hidden) / 2.0
        return base * (0.5 + 0.5 * modulation)
    
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
        self.ltc_params_path = self.network_path / "ltc_params.jsonl"
        
        # 确保目录存在
        self.network_path.mkdir(parents=True, exist_ok=True)
        
        # 初始化文件
        for path in [self.neurons_path, self.synapses_path, self.ltc_params_path]:
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
    
    # ==================== NLP 特征提取 ====================

    @staticmethod
    def _nlp_extract(content: str) -> Tuple[list, dict, dict, float]:
        """使用 NLP 模块提取文本语义特征"""
        if not _NLP_AVAILABLE or not content:
            return [], {}, {}, 0.5
        try:
            nlp = nlp_integration.get_nlp_integration()
            # 关键词
            keywords = nlp.extract_memory_keywords(content, top_k=10)
            # 实体
            entities = nlp.extract_memory_entities(content)
            # 情感
            sentiment = nlp.check_claim_sentiment(content)
            # 重要度
            importance = nlp.calculate_memory_importance(content)
            return keywords, entities, sentiment, importance
        except Exception:
            pass
        return [], {}, {}, 0.5

    @staticmethod
    def _nlp_semantic_similarity(a_kw: list, a_ent: dict,
                                  b_kw: list, b_ent: dict) -> float:
        """基于关键词和实体计算语义相似度 [0,1]"""
        score = 0.0
        # 关键词 Jaccard
        if a_kw or b_kw:
            set_a, set_b = set(a_kw), set(b_kw)
            union = set_a | set_b
            if union:
                score += 0.6 * len(set_a & set_b) / len(union)
        # 实体重叠
        a_ent_flat = set()
        for vals in a_ent.values():
            a_ent_flat.update(vals)
        b_ent_flat = set()
        for vals in b_ent.values():
            b_ent_flat.update(vals)
        if a_ent_flat or b_ent_flat:
            union = a_ent_flat | b_ent_flat
            if union:
                score += 0.4 * len(a_ent_flat & b_ent_flat) / len(union)
        return min(1.0, score)

    def create_neuron(
        self,
        content: str,
        embedding: List[float] = None,
        neuron_id: str = None
    ) -> MemoryNeuron:
        """创建神经元（NLP 特征提取 + LTC 初始化）"""
        self.network._load()

        ltc_params = _init_ltc_params()

        # NLP 特征提取
        keywords, entities, sentiment, importance = self._nlp_extract(content)

        neuron = MemoryNeuron(
            id=neuron_id or self.network._generate_id("NRN"),
            content=content,
            embedding=embedding or [],
            created_at=self.network._get_timestamp(),
            last_activated=self.network._get_timestamp(),
            activation_count=1,
            ltc_cell_params=ltc_params,
            ltc_hidden=0.5 + (importance - 0.5) * 0.3,  # 高重要度→初始兴奋度略高
            # NLP metadata
            nlp_keywords=json.dumps(keywords, ensure_ascii=False),
            nlp_entities=json.dumps(entities, ensure_ascii=False),
            nlp_sentiment=json.dumps(sentiment, ensure_ascii=False),
            nlp_importance=round(importance, 3),
        )

        self.network._neurons_cache[neuron.id] = neuron
        self.network._save_neuron(neuron)

        return neuron
    
    def get_neuron(self, neuron_id: str) -> Optional[MemoryNeuron]:
        """获取神经元"""
        self.network._load()
        return self.network._neurons_cache.get(neuron_id)
    
    def activate_neuron(self, neuron_id: str) -> Optional[MemoryNeuron]:
        """激活神经元（更新 LTC 状态）"""
        neuron = self.get_neuron(neuron_id)
        if not neuron:
            return None

        neuron.activation_count += 1
        neuron.last_activated = self.network._get_timestamp()

        # LTC：应用激活信号提升兴奋度
        neuron.apply_activation_signal(strength=1.0)

        return neuron
    
    def find_neuron_by_content(self, content: str) -> Optional[MemoryNeuron]:
        """根据内容查找神经元（NLP 语义级匹配兜底）"""
        self.network._load()

        # 1. 精确匹配（最快路径）
        for neuron in self.network._neurons_cache.values():
            if neuron.content == content:
                return neuron

        # 2. NLP 语义匹配（关键词/实体重叠 ≥ 0.5）
        if _NLP_AVAILABLE:
            kw_b, ent_b, _, _ = self._nlp_extract(content)
            for neuron in self.network._neurons_cache.values():
                a_kw = json.loads(neuron.nlp_keywords) if neuron.nlp_keywords else []
                if not a_kw:
                    continue
                a_ent = json.loads(neuron.nlp_entities) if neuron.nlp_entities else {}
                sim = self._nlp_semantic_similarity(a_kw, a_ent, kw_b, ent_b)
                if sim >= 0.5:
                    return neuron

        return None
    
    def get_all_neurons(self) -> List[MemoryNeuron]:
        """获取所有神经元"""
        self.network._load()
        return list(self.network._neurons_cache.values())


# ==================== 突触操作 ====================

class SynapseManager:
    """突触管理器"""
    
    # LTP/LTD 参数（fallback 模式，LTC 优先）
    LTP_STRENGTH = 0.1
    LTD_RATE = 0.01
    MIN_WEIGHT = 0.0
    MAX_WEIGHT = 1.0
    DECAY_THRESHOLD_DAYS = 7
    
    def __init__(self, network: SynapseNetwork, use_ltc: bool = True):
        self.network = network
        self.use_ltc = use_ltc
    
    def create_synapse(
        self,
        source_id: str,
        target_id: str,
        weight: float = 0.5,
        type: SynapseType = SynapseType.EXCITATORY,
        ltc_preset: str = None,  # 可选：预设名
        src_content: str = None,  # 源神经元文本（用于NLP算初始权）
        dst_content: str = None,  # 目标神经元文本
    ) -> Synapse:
        """创建突触（NLP 语义相似度→初始权重）"""
        self.network._load()
        
        # 检查是否已存在
        existing = self.get_synapse(source_id, target_id)
        if existing:
            return existing

        # NLP 语义相似度→初始权重（如有内容）
        if weight == 0.5 and src_content and dst_content and _NLP_AVAILABLE:
            try:
                nlp = nlp_integration.get_nlp_integration()
                src_res = nlp.process(src_content, ['tokenize', 'ner', 'keyword'])
                dst_res = nlp.process(dst_content, ['tokenize', 'ner', 'keyword'])
                # 关键词 Jaccard
                src_kw_set = set(kw for kw, _ in src_res.keywords)
                dst_kw_set = set(kw for kw, _ in dst_res.keywords)
                kw_overlap = 0.0
                if src_kw_set or dst_kw_set:
                    union = src_kw_set | dst_kw_set
                    kw_overlap = len(src_kw_set & dst_kw_set) / len(union)
                # 实体重叠
                src_ent_set = set(e.text for e in src_res.entities)
                dst_ent_set = set(e.text for e in dst_res.entities)
                ent_overlap = 0.0
                if src_ent_set or dst_ent_set:
                    union_e = src_ent_set | dst_ent_set
                    ent_overlap = len(src_ent_set & dst_ent_set) / len(union_e)
                # 综合权重：0.3 基线 + 0.4 关键词 + 0.3 实体
                weight = round(0.3 + 0.4 * kw_overlap + 0.3 * ent_overlap, 3)
                weight = min(1.0, max(0.1, weight))
            except Exception:
                pass
        
        ltc_str = ""
        if ltc_preset and ltc_preset in PRESETS:
            ltc_str = json.dumps(PRESETS[ltc_preset].to_dict())
        elif self.use_ltc:
            ltc_str = json.dumps(PRESETS["classic"].to_dict())
        
        synapse = Synapse(
            id=self.network._generate_id("SYN"),
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            type=type,
            created_at=self.network._get_timestamp(),
            last_reinforced=self.network._get_timestamp(),
            reinforcement_count=1,
            ltc_params=ltc_str
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
        更新 last_reinforced + 源神经元 LTC 兴奋度
        """
        synapse.last_reinforced = self.network._get_timestamp()
        synapse.reinforcement_count += 1

        if synapse.ltc_params:
            # LTC 模式：增强源神经元的 LTC 兴奋度
            ltc = synapse._get_ltc()
            if ltc:
                ltc.ff1 = min(1.0, ltc.ff1 + 0.05)
                synapse._set_ltc(ltc)
            # 同样激活源神经元（LTP 也提升神经元的兴奋度）
            src = self.network._neurons_cache.get(synapse.source_id)
            if src:
                src.apply_activation_signal(strength=0.3)  # 轻度增强
        else:
            # 传统模式：直接加减权重
            strength = strength or self.LTP_STRENGTH
            synapse.weight = min(self.MAX_WEIGHT, synapse.weight + strength)

        return synapse
    
    def ltd(self, synapse: Synapse, decay_rate: float = None) -> Synapse:
        """
        长时程抑制 (Long-Term Depression)
        LTC 模式由 ODE 控制衰减，不需要手动减
        """
        if synapse.ltc_params:
            # LTC 模式：不需要操作，compute_ltc_weight() 会实时反映衰减
            pass
        else:
            decay_rate = decay_rate or self.LTD_RATE
            last_reinforced = datetime.fromisoformat(synapse.last_reinforced)
            days_unused = (datetime.now(timezone.utc) - last_reinforced).days
            if days_unused > self.DECAY_THRESHOLD_DAYS:
                decay_amount = decay_rate * (days_unused - self.DECAY_THRESHOLD_DAYS)
                synapse.weight = max(self.MIN_WEIGHT, synapse.weight - decay_amount)
        
        return synapse
    
    def apply_decay_to_all(self):
        """对所有突触应用衰减"""
        self.network._load()
        for synapse in self.network._synapses_cache.values():
            self.ltd(synapse)
    
    def get_synapse_with_ltc_weight(self, syn_id: str,
                                       src_hidden: float = 0.5,
                                       dst_hidden: float = 0.5) -> Tuple[Optional[Synapse], float]:
        """获取突触及其 LTC 实时计算的权重（考虑神经元状态）"""
        self.network._load()
        syn = self.network._synapses_cache.get(syn_id)
        if syn is None:
            return None, 0.0
        return syn, syn.compute_ltc_weight(src_hidden, dst_hidden) if syn.ltc_params else syn.weight
    
    def batch_optimize_ltc(self, epochs: int = 100, verbose: bool = False) -> int:
        """对全量 LTC 突触进行批量参数优化"""
        self.network._load()
        
        ltc_synapses = [s for s in self.network._synapses_cache.values() if s.ltc_params]
        if not ltc_synapses:
            return 0
        
        # 收集训练数据
        training_data = []
        for s in ltc_synapses:
            days = _days_since(s.last_reinforced)
            training_data.append({
                "days": days,
                "recent_uses": s.reinforcement_count,
                "total_uses": s.reinforcement_count,
                "current_weight": s.compute_ltc_weight(),
            })
        
        trainer = LTCBatchOptimizer(lr=0.01, epochs=epochs, verbose=verbose)
        results = trainer.fit(training_data)
        
        # 写回优化后的参数
        for syn, cfg in zip(ltc_synapses, results):
            syn._set_ltc(cfg)
        
        if verbose:
            print(f"[LTC] 批量优化完成: {len(ltc_synapses)} 条突触")
        return len(ltc_synapses)


# ==================== 激活传播 ====================

class ActivationSpreader:
    """激活传播器"""
    
    DEFAULT_THRESHOLD = 0.3
    MAX_DEPTH = 3  # 最大传播深度
    
    def __init__(self, network: SynapseNetwork, use_ltc: bool = True):
        self.network = network
        self.synapse_manager = SynapseManager(network, use_ltc)
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
        使用 LTC 状态实时计算权重

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

        # LTC：先评估所有神经元当前状态
        network = self.network
        network._load()
        for n in network._neurons_cache.values():
            n.evaluate_state()

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
                # 使用突触实时权重（含源/目标神经元状态）
                src_state = neuron.ltc_hidden if neuron else 0.5
                dst = network._neurons_cache.get(synapse.target_id)
                dst_state = dst.ltc_hidden if dst else 0.5
                live_weight = synapse.compute_ltc_weight(src_state, dst_state) if synapse.ltc_params else synapse.weight

                if live_weight >= threshold:
                    propagated_strength = strength * live_weight

                    # LTP
                    self.synapse_manager.ltp(synapse)

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
    
    def __init__(self, workspace_path: str = None, use_ltc: bool = True):
        self.network = SynapseNetwork(workspace_path)
        self.neuron_manager = NeuronManager(self.network)
        self.synapse_manager = SynapseManager(self.network, use_ltc)
        self.activation_spreader = ActivationSpreader(self.network)
        self.use_ltc = use_ltc
    
    def create_neuron(self, content: str, embedding: List[float] = None) -> MemoryNeuron:
        """创建记忆神经元"""
        return self.neuron_manager.create_neuron(content, embedding)
    
    def create_synapse(
        self,
        source_id: str,
        target_id: str,
        weight: float = 0.5,
        ltc_preset: str = None,
        src_content: str = None,
        dst_content: str = None,
    ) -> Synapse:
        """创建突触连接（支持 LTC 预设 + NLP 语义权重）"""
        return self.synapse_manager.create_synapse(
            source_id, target_id, weight,
            ltc_preset=ltc_preset,
            src_content=src_content,
            dst_content=dst_content,
        )
    
    def activate(self, neuron_id: str) -> List[Tuple[MemoryNeuron, float]]:
        """激活神经元并传播"""
        return self.activation_spreader.spread_activation(neuron_id)
    
    def find_associated(self, neuron_id: str, top_k: int = 5) -> List[Tuple[MemoryNeuron, float]]:
        """查找关联记忆"""
        return self.activation_spreader.find_associated_memories(neuron_id, top_k)
    
    def apply_decay(self):
        """应用突触衰减"""
        self.synapse_manager.apply_decay_to_all()
    
    def batch_optimize(self, epochs: int = 100):
        """批量优化 LTC 参数"""
        return self.synapse_manager.batch_optimize_ltc(epochs=epochs)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取网络统计"""
        self.network._load()
        
        neurons = self.network._neurons_cache
        synapses = self.network._synapses_cache
        
        avg_activation = sum(n.activation_count for n in neurons.values()) / len(neurons) if neurons else 0
        
        # 统计 LTC vs 传统
        ltc_count = sum(1 for s in synapses.values() if s.ltc_params)
        
        # LTC 模式：用当前时间实时计算的权重
        if self.use_ltc:
            live_weights = [s.compute_ltc_weight() for s in synapses.values() if s.ltc_params]
        else:
            live_weights = [s.weight for s in synapses.values()]
        
        avg_weight = sum(live_weights) / len(live_weights) if live_weights else 0
        
        return {
            "total_neurons": len(neurons),
            "total_synapses": len(synapses),
            "ltc_synapses": ltc_count,
            "classic_synapses": len(synapses) - ltc_count,
            "avg_activation_count": round(avg_activation, 2),
            "avg_synapse_weight": round(avg_weight, 3),
            "strong_synapses": len([w for w in live_weights if w > 0.7]),
            "weak_synapses": len([w for w in live_weights if w < 0.3]),
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
