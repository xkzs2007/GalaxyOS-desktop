#!/usr/bin/env python3
"""
Galaxy Engine Integration Layer

将 Engram / DAGLiquid / LFM / SSM / ODE-RNN 等新模块插入 R-CCAM 管道。

当前状态：
  ✅ unified_coordinator.py 已注册，测试能跑
  ❌ 没有任何运行中代码调它们

本模块提供 pluggable 接口，侵入式 hook 到 xiaoyi_claw_api.py 四个阶段：

  1. Retrieval Phase  →  Engram 条件记忆 + DAGLiquid 压缩建议
  2. Cognition Phase  →  SSM 状态追踪 + LFM 自适应推理
  3. Action Phase     →  LFM 推理替代通道
  4. Memory Phase     →  SSM 热度更新 + ODE-RNN 持续学习标记

  5. Heartbeat →  ODE-RNN 持续学习管线（run_heartbeat.py 调）

核心原则：
  - lazyload：首次调用才导入，不影响系统启动
  - 降级友好：模块不可用时静默跳过

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-14
"""

import os
import sys
import math
import time
import json
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable
from pathlib import Path
from galaxyos.shared.paths import workspace

logger = logging.getLogger("galaxy_engine_integration")

# ── 模块路径 ──
_ENGINE_DIR = Path(__file__).parent.resolve()
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

# ── 懒加载缓存 ──
_M: Dict[str, Any] = {}


def _lazy(mod_name: str):
    """Lazy import 单例"""
    if mod_name not in _M:
        _M[mod_name] = __import__(mod_name)
    return _M[mod_name]


# ===========================================================================
# 1. Engram + DAGLiquid → 记忆检索环节
# ===========================================================================

class EngramRetrievalAugmenter:
    """
    Engram 条件记忆 → 检索阶段增强
    
    在 retrieval_phase 中：
      1. 检索前：用 Engram O(1) 快速查找常见模式，预填充结果
      2. 检索后：用 Engram hit_rate 调节 retrieval_confidence
    """
    
    def __init__(self):
        self._engram_mem = None
        self._engram_initialized = False
    
    def _ensure_engram(self):
        if self._engram_initialized:
            return self._engram_mem is not None
        try:
            from engram_memory import EngramMemory, EngramConfig
            persist = os.path.join(
                os.path.expanduser("~/.openclaw/extensions/galaxyos/var"),
                "engram_retrieval.json"
            )
            os.makedirs(os.path.dirname(persist), exist_ok=True)
            config = EngramConfig(
                num_slots=4096,      # 轻量，适合对话场景
                embed_dim=32,
                ngram_n=2,
                persist_path=persist,
            )
            self._engram_mem = EngramMemory(config)
            self._engram_initialized = True
            logger.info(f"EngramRetrievalAugmenter 初始化: slots={config.num_slots}")
            return True
        except Exception as e:
            logger.warning(f"Engram 初始化失败: {e}")
            self._engram_initialized = True  # 标记为已尝试
            return False
    
    def pre_retrieval_lookup(self, query: str) -> Dict:
        """
        检索前快速条件记忆查找
        
        如果 Engram 命中率高，说明是常见问题模式，
        可以直接提高 retrieval_confidence 或提供预填充答案。
        
        Returns:
            {"hit": bool, "hit_rate": float, "embedding": ...}
        """
        if not self._ensure_engram():
            return {"hit": False, "hit_rate": 0.0}
        try:
            emb, info = self._engram_mem.lookup(query)
            return {
                "hit": info["hit"],
                "hit_rate": info["hit_rate"],
                "embedding_norm": info.get("embedding_norm", 0.0),
            }
        except Exception as e:
            logger.debug(f"Engram pre_retrieval 跳过: {e}")
            return {"hit": False, "hit_rate": 0.0}
    
    def post_retrieval_bias(self, query: str, 
                            base_confidence: float,
                            retrieved_count: int) -> float:
        """
        检索后：用 Engram hit_rate 微调 confidence
        
        如果 Engram 命中率高 + 检索结果少 → 降低 confidence（常见问题但没找到）
        如果 Engram 命中率高 + 检索结果多 → 提升 confidence（模式确认）
        """
        if not self._ensure_engram():
            return base_confidence
        try:
            _, info = self._engram_mem.lookup(query)
            hr = info["hit_rate"]
            if hr > 0.3 and retrieved_count >= 2:
                return min(1.0, base_confidence + 0.05 * hr)
            elif hr > 0.3 and retrieved_count < 2:
                return max(0.1, base_confidence - 0.05 * hr)
            return base_confidence
        except Exception:
            return base_confidence
    
    def remember_query(self, query: str, answer: str = ""):
        """将查询存入条件记忆"""
        if not self._ensure_engram():
            return
        try:
            self._engram_mem.remember(query + " " + answer[:100])
        except Exception:
            pass
    
    def get_status(self) -> Dict:
        if not self._ensure_engram():
            return {"available": False}
        try:
            return {
                "available": True,
                "status": self._engram_mem.get_status(),
            }
        except Exception:
            return {"available": False}


class DAGLiquidAdviceProvider:
    """
    DAG + Liquid → 给 DAG 上下文管理器提供压缩建议
    
    在 retrieval_phase 中，DAG 上下文组装时调用：
      - should_compact() 判断
      - select_nodes_to_compact() 选择
    """
    
    def __init__(self):
        self._fusion = None
        self._initialized = False
    
    def _ensure(self):
        if self._initialized:
            return self._fusion is not None
        try:
            from dag_liquid_fusion import DAGLiquidFusion, DAGLiquidFusionConfig
            config = DAGLiquidFusionConfig(
                tau_min=0.5,
                tau_max=12.0,
                soft_compact_threshold=0.75,
                recency_weight=0.3,
                heat_weight=0.3,
                liquid_weight=0.4,
            )
            self._fusion = DAGLiquidFusion(config)
            self._initialized = True
            return True
        except Exception as e:
            logger.warning(f"DAGLiquid 初始化失败: {e}")
            self._initialized = True
            return False
    
    def get_compact_advice(self, raw_tokens: int, max_tokens: int,
                           dag_nodes: List[Dict]) -> Dict:
        """
        获取压缩建议（不操作 DAG，只给建议）
        
        Returns:
            {"should_compact": bool, "readiness": float, 
             "candidates": [...], "retain": [...]}
        """
        if not self._ensure():
            return {"should_compact": False, "reason": "unavailable"}
        try:
            return self._fusion.get_compact_recommendation(
                raw_tokens, max_tokens, dag_nodes
            )
        except Exception as e:
            logger.debug(f"DAGLiquid advice 跳过: {e}")
            return {"should_compact": False, "reason": str(e)}
    
    def rank_nodes(self, nodes: List[Dict], top_k: int = None) -> List:
        """液态时间感知排序"""
        if not self._ensure():
            return []
        try:
            scores = self._fusion.rank_by_liquid_importance(nodes, top_k=top_k)
            return [{"node_id": s.node_id, "score": s.score, "tau": s.tau_value}
                    for s in scores]
        except Exception:
            return []
    
    def compute_summary_tau(self, source_nodes: List[Dict]) -> float:
        """压缩后摘要节点的时间常数"""
        if not self._ensure():
            return 5.0
        try:
            return self._fusion.compact_strategy.compute_compact_tau(source_nodes)
        except Exception:
            return 5.0
    
    def get_info(self) -> Dict:
        if not self._ensure():
            return {"available": False}
        try:
            return {
                "available": True,
                "info": self._fusion.get_info(),
            }
        except Exception:
            return {"available": False}


# ===========================================================================
# 2. LFM 系列 → 推理替代通道
# ===========================================================================

# ── LFM 全局共享实例（与 V81IntegrationAddon 共用同一个模型加载）──
_LFM_REAL_NET = None

def _get_lfm_real() -> Optional[Any]:
    """获取/创建全局共享的 RealLFMNetwork 单例"""
    global _LFM_REAL_NET
    if _LFM_REAL_NET is not None:
        return _LFM_REAL_NET
    try:
        from lfm_adaptive_operator import RealLFMNetwork
        _LFM_REAL_NET = RealLFMNetwork()
        if _LFM_REAL_NET._ensure():
            logger.info("LFMReasoningChannel 共享 RealLFMNetwork (LFM2.5-1.2B) ✅")
            return _LFM_REAL_NET
        _LFM_REAL_NET = False
    except Exception as e:
        logger.warning(f"RealLFMNetwork 加载失败: {e}")
        _LFM_REAL_NET = False
    return None


class LFMReasoningChannel:
    """
    LFM 推理通道 — 委托到全局 RealLFMNetwork（与 v8.1 管线共享模型实例）
    
    提供 analyze_query / lfm_reason 接口，
    底层使用 LFM2.5-1.2B-Thinking 真实权重。
    """
    
    def __init__(self):
        self._real = None
        self._initialized = False
    
    def _ensure(self):
        if self._initialized:
            return self._real is not None
        self._real = _get_lfm_real()
        self._initialized = True
        return self._real is not None
    
    def _forward_text(self, text: str) -> Dict:
        """委托给 RealLFMNetwork 的隐状态分析"""
        if not self._ensure():
            return {"reasoning_available": False}
        return self._real._forward_text(text)
    
    def generate(self, prompt: str, max_new_tokens: int = 128,
                 temperature: float = 0.7) -> str:
        """委托给 RealLFMNetwork 的文本生成"""
        if not self._ensure():
            return ""
        return self._real.generate(prompt, max_new_tokens, temperature)
    
    def analyze_query(self, query: str) -> Dict:
        """语义分析 — 隐状态 norm + 关键字意图"""
        ft = self._forward_text(query)
        if not ft.get('reasoning_available'):
            return {"reasoning_available": False}
        try:
            import torch
            inputs = self._real._tokenizer(query, return_tensors='pt')
            with torch.no_grad():
                outputs = self._real._model(**inputs, output_hidden_states=True)
                hidden = outputs.hidden_states[-1]
                norm = float(hidden.norm().item())
            
            token_cnt = len(self._real._tokenizer.encode(query))
            intent_patterns = {
                "query": ["什么", "怎么", "为什么", "如何", "谁是", "哪里", "多少", "何时"],
                "action": ["帮我", "请", "做", "创建", "生成", "写", "画", "调", "设置", "打开"],
                "memory": ["记住", "回忆", "忘了", "之前", "保存", "笔记", "记录"],
                "command": ["执行", "运行", "启动", "停止", "配置", "查看状态", "检查"],
            }
            intent = "unknown"
            for it, kws in intent_patterns.items():
                if any(kw in query for kw in kws):
                    intent = it
                    break
            
            return {
                "reasoning_available": True,
                "embedding_norm": round(norm, 2),
                "complexity": round(min(1.0, token_cnt / 128.0), 4),
                "intent_analysis": intent,
                "token_count": token_cnt,
            }
        except Exception as e:
            logger.debug(f"LFM analyze 跳过: {e}")
            return ft
    
    def lfm_reason(self, query: str, context: str = "") -> Dict:
        """完整推理"""
        if not self._ensure():
            return {"reasoning_available": False}
        try:
            if context:
                prompt = f"""<|im_start|>system
分析上下文并回答用户问题。
<|im_end|>
<|im_start|>user
Context: {context}

Query: {query}
<|im_end|>
<|im_start|>assistant
"""
            else:
                prompt = f"""<|im_start|>system
你是一个有用的助手。
<|im_end|>
<|im_start|>user
{query}
<|im_end|>
<|im_start|>assistant
"""
            reason = self._real.generate(prompt, max_new_tokens=128)
            if not reason:
                return {"reasoning_available": False}
            return {
                "reasoning_available": True,
                "reason": reason,
                "confidence": 0.85,
            }
        except Exception as e:
            logger.debug(f"LFM reason 失败: {e}")
            return {"reasoning_available": False}


# ===========================================================================
# 3. SSM 系列 → 状态追踪
# ===========================================================================

class SSMStateTracker:
    """
    SSM 状态预测器 → 跟踪对话状态、记忆热度模式
    
    在 cognition_phase 和 memory_phase 中：
      - 预测用户的"注意力状态"（话题切换/延续）
      - 预测哪些记忆应该被激活
    """
    
    def __init__(self):
        self._predictor = None
        self._liquid_ssm = None
        self._initialized = False
        self._state: Dict[str, Any] = {
            "topic": "",
            "engagement": 0.5,
            "switch_probability": 0.0,
            "turns": 0,
            "last_update": time.time(),
        }
    
    def _ensure(self):
        if self._initialized:
            return self._predictor is not None
        try:
            from ssm_state_predictor import SSMStatePredictor
            # SSMStatePredictor 的真实签名：
            #   (tau=3600.0, decay_scale=1.0, base_rate=0.001, alpha=0.5, activation_window=86400.0)
            self._predictor = SSMStatePredictor(
                tau=1800.0,   # 30 分钟衰减，适合对话
                decay_scale=0.5,
                base_rate=0.01,
                alpha=0.3,
                activation_window=43200.0,  # 12 小时窗口
            )
            self._initialized = True
            logger.info("SSMStateTracker 初始化")
            return True
        except Exception as e:
            logger.warning(f"SSMStateTracker 初始化失败: {e}")
            self._initialized = True
            return False
    
    def on_turn(self, query: str, topic: str = "") -> Dict:
        """
        每轮对话更新状态追踪
        
        Returns:
            {"switch_probability": float, "engagement": float,
             "predicted_next_hour_recalls": int, ...}
        """
        self._state["turns"] += 1
        self._state["last_update"] = time.time()
        
        if not self._ensure():
            return {"status": "fallback", "turns": self._state["turns"]}
        
        try:
            # 话题切换检测
            if topic and self._state["topic"] and topic != self._state["topic"]:
                self._state["switch_probability"] = 0.7
            else:
                self._state["switch_probability"] = max(
                    0.0, self._state.get("switch_probability", 0) - 0.1
                )
            self._state["topic"] = topic or self._state["topic"]
            
            # 用 SSM 预测器分析时间模式
            now = time.time()
            # SSMStatePredictor 的真实方法：predict(memory_id, now=None)
            _mem_id = f"topic_{self._state['topic']}" if self._state['topic'] else "general"
            prediction = self._predictor.predict(
                memory_id=_mem_id,
                now=now,
            )
            
            self._state["engagement"] = min(
                1.0, self._state["turns"] * 0.1 + 0.3
            )
            
            return {
                "status": "ok",
                "switch_probability": self._state["switch_probability"],
                "engagement": self._state["engagement"],
                "predicted_next_hour_recalls": prediction.get("predicted_intensity", 0),
                "should_refresh": prediction.get("trend", "stable") == "rising",
            }
        except Exception as e:
            logger.debug(f"SSM on_turn 跳过: {e}")
            return {"status": "fallback", "turns": self._state["turns"]}
    
    def record_memory_access(self, memory_id: str, now: float = None):
        """记录记忆访问（供 SSM 学习时间模式）
        
        SSMStatePredictor.record_recall 的真实签名：
          record_recall(self, memory_ids: List[str])
        """
        if not self._ensure():
            return
        try:
            self._predictor.record_recall([memory_id])
        except Exception:
            pass
    
    def get_state(self) -> Dict:
        return dict(self._state)


# ===========================================================================
# 4. 持续学习管线（心跳执行）
# ===========================================================================

class ContinualLearningPipeline:
    """
    ODE-RNN 持续学习管线 → 后台心跳
    
    在心跳周期中：
      1. 收集近期交互数据
      2. 用 EWC 正则项微调 ODE-RNN 参数
      3. 更新记忆嵌入
      4. 生成学习报告
    """
    
    def __init__(self):
        self._continual = None
        self._initialized = False
        self._last_learn_time = time.time()
        self._session_data: List[Dict] = []
        self._total_learn_steps = 0
    
    def _ensure(self):
        if self._initialized:
            return self._continual is not None
        try:
            from ode_rnn_continual import ODERNNContinual
            # ODERNNContinual 真实签名：
            #   (input_dim, hidden_dim=64, output_dim=1, memory_size=100,
            #    memory_dim=64, num_ode_layers=2, ode_solver='rk4',
            #    ewc_lambda=100.0, learning_rate=0.01)
            # 轻量对话配置
            self._continual = ODERNNContinual(
                input_dim=64,
                hidden_dim=32,
                output_dim=16,
                memory_size=50,
                memory_dim=16,
                num_ode_layers=1,
                ewc_lambda=50.0,
                learning_rate=0.001,
            )
            self._initialized = True
            logger.info("ContinualLearningPipeline 初始化: hidden_dim=32, memory_slots=50")
            return True
        except Exception as e:
            logger.warning(f"持续学习管线初始化失败: {e}")
            self._initialized = True
            return False
    
    def record_interaction(self, query: str, answer: str, 
                           metadata: Dict = None):
        """记录一次交互（供后续学习）"""
        self._session_data.append({
            "query": query,
            "answer": answer,
            "metadata": metadata or {},
            "timestamp": time.time(),
        })
        # 只保留最近 200 条
        if len(self._session_data) > 200:
            self._session_data = self._session_data[-200:]
    
    def execute_learning_step(self, max_steps: int = 10) -> Dict:
        """
        执行一次持续学习步骤（心跳调用）
        
        Returns:
            {"learned": int, "ewc_loss": float, "total_steps": int, ...}
        """
        if not self._ensure():
            return {"learned": 0, "error": "unavailable"}
        if not self._session_data:
            return {"learned": 0, "reason": "no_data"}
        
        try:
            import numpy as np
            recent = self._session_data[-max_steps:]
            total_loss = 0.0
            for item in recent:
                # train_step 真实签名：x_seq [T, input_dim], y_true [T, output_dim]
                # input_dim=64, output_dim=16
                text = (item["query"] + " " + item["answer"])[:256]
                T = min(len(text), 32)
                x_seq = np.zeros((T, 64), dtype=np.float32)
                for i, c in enumerate(text[:T]):
                    x_seq[i, ord(c) % 64] = 1.0
                # 下一个字符预测
                y_true = np.zeros((T, 16), dtype=np.float32)
                for i in range(T - 1):
                    y_true[i, ord(text[i+1]) % 16] = 1.0
                
                loss = self._continual.continual_learning_step(
                    x_seq, y_true, task_id=0
                )
                total_loss += loss
                self._total_learn_steps += 1
            
            self._last_learn_time = time.time()
            
            return {
                "learned": len(recent),
                "ewc_loss": total_loss / max(len(recent), 1),
                "total_steps": self._total_learn_steps,
                "session_data_cached": len(self._session_data),
                "last_learn_ago_s": time.time() - self._last_learn_time,
            }
        except Exception as e:
            logger.warning(f"持续学习步进失败: {e}")
            return {"learned": 0, "error": str(e)}
    
    def summarize_patterns(self) -> Dict:
        """
        总结学习到的模式
        
        Returns:
            {"patterns_found": List, "ewc_importance": Dict, ...}
        """
        if not self._ensure():
            return {"available": False}
        try:
            summary = self._continual.summarize()
            return {"available": True, "summary": summary}
        except Exception:
            return {"available": False, "error": "summarize failed"}
    
    def get_status(self) -> Dict:
        self._ensure()  # 触发懒加载
        return {
            "available": self._initialized and self._continual is not None,
            "total_learn_steps": self._total_learn_steps,
            "session_data_cached": len(self._session_data),
            "last_learn_time": self._last_learn_time,
            "seconds_since_last_learn": time.time() - self._last_learn_time,
        }


# ===========================================================================
# 5. 顶层入口
# ===========================================================================

class GalaxyEngineIntegration:
    """
    Galaxy Engine 顶层集成入口
    
    统一管理所有子模块的初始化、生命周期、状态报告。
    """
    
    def __init__(self, workspace: str = ""):
        self.ws = workspace or os.environ.get(
            "OPENCLAW_WORKSPACE", 
            workspace()
        )
        
        # 子模块（均为 lazyload）
        self.engram = EngramRetrievalAugmenter()
        self.dag_liquid = DAGLiquidAdviceProvider()
        self.lfm = LFMReasoningChannel()
        self.ssm = SSMStateTracker()
        self.continual = ContinualLearningPipeline()
        
        logger.info("GalaxyEngineIntegration 已创建 (5 个子模块)")
    
    # ────────── Retrieval Phase Hooks ──────────
    
    def pre_retrieval(self, query: str) -> Dict:
        """检索前：Engram 条件记忆快速查找"""
        return self.engram.pre_retrieval_lookup(query)
    
    def post_retrieval(self, query: str, base_confidence: float,
                       retrieved_count: int) -> float:
        """检索后：confidence 微调"""
        return self.engram.post_retrieval_bias(
            query, base_confidence, retrieved_count
        )
    
    def get_compact_advice(self, raw_tokens: int, max_tokens: int,
                           dag_nodes: List[Dict]) -> Dict:
        """DAG 压缩建议"""
        return self.dag_liquid.get_compact_advice(
            raw_tokens, max_tokens, dag_nodes
        )
    
    def remember_retrieved(self, query: str, answer: str = ""):
        """记录查询到 Engram"""
        self.engram.remember_query(query, answer)
    
    # ────────── Cognition Phase Hooks ──────────
    
    def analyze_with_lfm(self, query: str) -> Dict:
        """LFM 推理分析"""
        return self.lfm.analyze_query(query)
    
    def track_ssm_state(self, query: str, topic: str = "") -> Dict:
        """SSM 状态追踪"""
        return self.ssm.on_turn(query, topic)
    
    # ────────── Memory Phase Hooks ──────────
    
    def record_memory_access_ssm(self, memory_id: str):
        """SSM 记录记忆访问"""
        self.ssm.record_memory_access(memory_id)
    
    def record_interaction_continual(self, query: str, answer: str,
                                      metadata: Dict = None):
        """持续学习记录交互"""
        self.continual.record_interaction(query, answer, metadata)
    
    # ────────── Heartbeat ──────────
    
    def execute_continual_learning(self, max_steps: int = 10) -> Dict:
        """心跳：持续学习步进"""
        return self.continual.execute_learning_step(max_steps=max_steps)
    
    # ────────── Status ──────────
    
    def get_full_status(self) -> Dict:
        return {
            "engram": self.engram.get_status(),
            "dag_liquid": self.dag_liquid.get_info(),
            "lfm": {"available": self.lfm._ensure()},
            "ssm": {"state": self.ssm.get_state()},
            "continual": self.continual.get_status(),
        }
    
    def get_enabled_count(self) -> int:
        return sum([
            1 if self.engram.get_status().get("available") else 0,
            1 if self.dag_liquid.get_info().get("available") else 0,
            1 if self.lfm._ensure() else 0,
            1 if self.ssm._ensure() else 0,
            1 if self.continual.get_status().get("available") else 0,
        ])


# ── 全局单例 ──
_galaxy_int: Optional[GalaxyEngineIntegration] = None


def get_galaxy_engine(workspace: str = "") -> GalaxyEngineIntegration:
    """获取/创建全局实例"""
    global _galaxy_int
    if _galaxy_int is None:
        _galaxy_int = GalaxyEngineIntegration(workspace)
    return _galaxy_int
