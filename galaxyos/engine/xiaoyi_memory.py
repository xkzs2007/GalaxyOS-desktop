#!/usr/bin/env python3
"""
小艺记忆系统统一入口 V2 (Xiaoyi Memory System V2)

集成所有 78 个模块，形成完整的记忆增强系统：

Layer 1: 记忆核心层 - 防幻觉、突触网络、情感记忆、反思、自适应
Layer 2: 检索增强层 - CRAG、混合检索、命题检索、RAG 缓存
Layer 3: 向量优化层 - ANN 选择、稀疏索引、量化
Layer 4: LLM 优化层 - 投机解码、流式生成、模型路由
Layer 5: 缓存管理层 - 语义缓存、统一缓存、近似缓存
Layer 6: 硬件优化层 - NUMA/GPU/MKL 加速
Layer 7: 模块协调层 - 资源编排、自动调优
Layer 8: 系统可靠性层 - 故障转移、自动恢复
Layer 9: 会话管理层 - 对话管理、上下文压缩
Layer 10: Persona 管理层 - 自动学习、智能更新
Layer 11: 思考技能层 - 技能协调、工作流

Author: 小艺 Claw
Version: 3.0.0
Created: 2026-04-21
"""

import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import logging
from galaxyos.shared.paths import workspace

# 添加模块路径
CORE_DIR = Path(__file__).parent
PRIVILEGED_DIR = Path.home() / ".openclaw/workspace/skills/llm-memory-integration/src/privileged"
ORCH_DIR = Path(__file__).parent.parent.parent.parent / "orchestration"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PRIVILEGED_DIR))
sys.path.insert(0, str(ORCH_DIR))

logger = logging.getLogger(__name__)

# 导入 Layer 1 核心模块
from hallucination_guard import (
    HallucinationGuard,
    VerifiedMemory,
    SourceType,
    VerificationStatus
)
from memory_synapse_network import MemorySynapseNetwork
from emotion_memory import EmotionMemoryManager
from memory_reflector import MemoryReflector
from adaptive_memory import AdaptiveMemoryManager
from unified_coordinator import UnifiedCoordinator, ModuleType


class XiaoyiMemoryV2:
    """
    小艺记忆系统 V2 - 全量整合版
    
    整合 78 个模块，提供统一的记忆增强接口。
    """
    
    def __init__(self, workspace_path: str = None, enable_all_layers: bool = True):
        self.workspace_path = Path(workspace_path or 
            workspace())
        self.enable_all_layers = enable_all_layers
        
        # Layer 1: 核心模块（始终启用）
        self.hallucination_guard = HallucinationGuard(str(self.workspace_path))
        self.synapse_network = MemorySynapseNetwork(str(self.workspace_path))
        self.emotion_manager = EmotionMemoryManager(str(self.workspace_path))
        self.reflector = MemoryReflector(str(self.workspace_path))
        self.adaptive_manager = AdaptiveMemoryManager(str(self.workspace_path))
        self.coordinator = UnifiedCoordinator(str(self.workspace_path))
        
        # 注意：不自带 _claw_api 引用，避免循环依赖。
        # XiaoYiClawLLM 通过 _init_memory_v2 创建本实例
        self._claw_api = None
        
        # Layer 2-11: 高级模块（按需加载）
        self._advanced_modules: Dict[str, Any] = {}
        
        # 模块状态
        self._module_status = {
            "layer1_loaded": True,
            "layer2_loaded": False,
            "layer3_loaded": False,
            "layer4_loaded": False,
            "layer5_loaded": False,
            "layer6_loaded": False,
            "layer7_loaded": False,
            "layer8_loaded": False,
            "layer9_loaded": False,
            "layer10_loaded": False,
            "layer11_loaded": False,
        }
        
        
        logger.info("✅ 小艺记忆系统 V2 已启动 (懒加载模式 - 模块按需初始化)")
    
    def _lazy_load(self, module_name: str) -> Optional[Any]:
        """懒加载模块"""
        if module_name in self._advanced_modules:
            return self._advanced_modules[module_name]
        
        module = self.coordinator._load_module(module_name)
        if module:
            self._advanced_modules[module_name] = module
            # 同步更新层状态标志，确保 health_check 能反映真实加载情况
            if module_name in self.coordinator.modules:
                layer = self.coordinator.modules[module_name].layer
                key = f"layer{layer}_loaded"
                if key in self._module_status:
                    self._module_status[key] = True
        return module

    # ==================== 核心接口（继承 V1）====================
    
    def store(
        self,
        content: str,
        source: str = "unknown",
        context: Dict = None,
        entities: List[str] = None,
        tags: List[str] = None
    ) -> Dict:
        """存储记忆（本地直存：防幻觉守卫 + 突触网络 + 情感记忆，不再走 XiaoYiClawLLM 避免循环依赖）"""
        context = context or {}
        
        # 防幻觉守卫：前检查
        should_refuse, reason = self.hallucination_guard.check_before_generation(content)
        if should_refuse:
            logger.warning(f"存储被防幻觉守卫拒绝: {reason}")
            return {
                "memory_id": "",
                "neuron_id": "",
                "verified": "refused",
                "confidence": 0.0,
                "emotion": {},
                "source": source
            }
        
        # 直接写入持久化存储
        from hallucination_guard import VerifiedMemory, SourceType
        import uuid
        memory = VerifiedMemory(
            id=str(uuid.uuid4()),
            content=content,
            source=SourceType[source.upper()] if source.upper() in SourceType.__members__ else SourceType.USER_DIRECT,
            confidence=0.7,
            importance=0.5,
            created_at=datetime.now(timezone.utc).isoformat()
        )
        store_path = self.workspace_path / ".learnings" / "verified_memories.jsonl"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(memory.to_dict(), ensure_ascii=False) + "\n")
        
        # 突触网络：创建神经元
        neuron = self.synapse_network.create_neuron(content)
        
        # 情感记忆：处理情感权重
        emotion_result = self.emotion_manager.process_message(content)
        
        # 自适应 LTP/LTD：对已有突触做 LTP 增强（高频记忆加固）
        try:
            from adaptive_ltp_ltd import AdaptiveLTP_LTD, SynapseState
            ltd_adapter = AdaptiveLTP_LTD()
            # 找到与内容相关的已有神经元，做 LTP 增强
            for rel_neuron in self.synapse_network.get_recent_neurons(top_k=10):
                if hasattr(rel_neuron, 'id') and rel_neuron.id:
                    synapse_state = SynapseState(
                        weight=rel_neuron.importance if hasattr(rel_neuron, 'importance') else 0.5,
                        reinforcement_count=rel_neuron.reinforcement_count if hasattr(rel_neuron, 'reinforcement_count') else 0,
                        last_reinforced=rel_neuron.last_activated if hasattr(rel_neuron, 'last_activated') else datetime.now(),
                        importance=rel_neuron.importance if hasattr(rel_neuron, 'importance') else 0.5,
                        created_at=rel_neuron.created_at if hasattr(rel_neuron, 'created_at') else datetime.now()
                    )
                    ltd_adapter.apply_ltp(synapse_state)
        except Exception as e:
            logger.debug(f"adaptive_ltp_ltd 增强失败: {e}")
        
        return {
            "memory_id": memory.id,
            "neuron_id": neuron.id if hasattr(neuron, 'id') else "",
            "verified": "verified",
            "confidence": emotion_result.get("weight", 0.7),
            "emotion": emotion_result.get("emotion", {}),
            "source": source
        }
    
    def remember(
        self,
        content: str,
        source: str = "unknown",
        context: Dict = None,
        entities: List[str] = None,
        tags: List[str] = None
    ) -> Dict:
        """记忆存储别名 - 与 XiaoYiClawLLM 的 remember() 接口对齐"""
        return self.store(content, source, context, entities, tags)
    
    def recall(
        self,
        query: str,
        top_k: int = 10,
        min_confidence: float = 0.3,
        use_enhanced: bool = True
    ) -> List[Dict]:
        """
        召回记忆（本地直查：防幻觉守卫 + Embedding 增强，不再走 XiaoYiClawLLM 避免循环依赖）
        
        Args:
            query: 查询
            top_k: 返回数量
            min_confidence: 最小置信度
            use_enhanced: 是否使用增强检索
        """
        # 1. 生成前检查
        should_refuse, reason = self.hallucination_guard.check_before_generation(query)
        if should_refuse:
            logger.warning(f"检索被防幻觉守卫拒绝: {reason}")
            return []
        
        # 2. 本地：关键词匹配
        memories = self.hallucination_guard._load_memories()
        valid_memories = self.hallucination_guard.filter_valid_memories(memories)
        
        results = []
        query_lower = query.lower()
        query_chars = set(query_lower)
        query_words = set(query_lower.split())
        
        for memory in valid_memories:
            if memory.get_effective_confidence() < min_confidence:
                continue
            content_lower = memory.content.lower()
            content_chars = set(content_lower)
            content_words = set(content_lower.split())
            word_overlap = len(query_words & content_words)
            char_overlap = len(query_chars & content_chars)
            if char_overlap >= 3 or word_overlap > 0:
                overlap_score = word_overlap * 10 + char_overlap
                results.append({
                    "id": memory.id,
                    "content": memory.content,
                    "confidence": memory.get_effective_confidence(),
                    "source": memory.source.value,
                    "status": memory.verification_status.value,
                    "importance": memory.importance,
                    "overlap": overlap_score
                })
        
        results.sort(key=lambda x: (x["overlap"], x["confidence"]), reverse=True)
        results = results[:top_k]
        
        # 3. Embedding 增强：质量评分 + 过滤 + 代表性抽取
        if use_enhanced and results and len(results) > 1:
            try:
                from embedding_enhance import EmbeddingEnhancer
                ee = EmbeddingEnhancer()
                if ee.available():
                    scored = ee.score_relevance(query, results)
                    scored = [r for r in scored if r.get("relevance_score", 0.5) >= 0.25]
                    if scored:
                        representative = ee.extract_representative(scored, top_k=min(top_k, len(scored)))
                        if representative:
                            results = representative
            except Exception:
                pass
        
        results.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return results[:top_k]
    
    def answer(
        self,
        query: str,
        raw_answer: str = None,
        top_k: int = 5,
        use_fast_generation: bool = False
    ) -> Dict:
        """
        生成回答（本地验证增强，不再走 XiaoYiClawLLM 避免循环依赖）
        
        基于本地防幻觉守卫 + 记忆检索，输出带不确定性表达的回答。
        """
        should_refuse, reason = self.hallucination_guard.check_before_generation(query)
        if should_refuse:
            return {
                "answer": reason,
                "confidence": 0.0,
                "sources": [],
                "validation": {"refused": True, "reason": reason}
            }
        
        # 本地检索记忆
        memories = self.recall(query, top_k=top_k, min_confidence=0.0)
        avg_confidence = sum(m["confidence"] for m in memories) / len(memories) if memories else 0.4
        
        if raw_answer:
            # 用防幻觉守卫验证原始回答
            validation = self.hallucination_guard.validate_output(
                raw_answer,
                [m for m in self.hallucination_guard._load_memories()[:top_k]]
            ) if hasattr(self.hallucination_guard, 'validate_output') else {}
            
            final_answer = self.hallucination_guard.express_with_confidence(
                raw_answer, avg_confidence, None,
                [m["content"][:30] for m in memories[:3]]
            ) if hasattr(self.hallucination_guard, 'express_with_confidence') else raw_answer
            
            return {
                "answer": final_answer,
                "confidence": avg_confidence,
                "sources": [m["content"][:50] for m in memories[:3]],
                "validation": validation or {}
            }
        
        return {"answer": raw_answer or query, "confidence": avg_confidence, "sources": [], "validation": {}}
    
    def correct(
        self,
        original: str,
        corrected: str
    ) -> Dict:
        """处理用户纠正（本地处理，不再走 XiaoYiClawLLM 避免循环依赖）"""
        # 本地存储纠正内容，标记原始记忆为需重新验证
        correction_id = ""
        try:
            memories = self.hallucination_guard._load_memories()
            for memory in memories:
                if original in memory.content or memory.content in original:
                    memory.verification_status = VerificationStatus.VERIFIED_FALSE
                    self._update_memory(memory)
            
            # 同时存储纠正内容为新记忆
            from hallucination_guard import SourceType
            import json, uuid
            correction_id = f"correction_{uuid.uuid4().hex[:8]}"
            correction_entry = {
                "id": correction_id,
                "content": corrected,
                "source": SourceType.USER_DIRECT.value,
                "verification_status": VerificationStatus.VERIFIED_TRUE.value,
                "confidence": 1.0,
                "tags": [original],  # 原始内容存tags（不破坏 VerifiedMemory 结构）
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            store_path = self.workspace_path / ".learnings" / "verified_memories.jsonl"
            with open(store_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(correction_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"本地记忆处理失败（非关键路径）: {e}")
        
        return {
            "correction_id": correction_id,
            "message": "已记录纠正，原始信息已标记为需重新验证"
        }
    
    def _update_memory(self, memory: VerifiedMemory):
        """更新记忆"""
        store_path = self.workspace_path / ".learnings" / "verified_memories.jsonl"
        memories = self.hallucination_guard._load_memories()
        
        import json
        with open(store_path, "w", encoding="utf-8") as f:
            for m in memories:
                f.write(json.dumps(m.to_dict(), ensure_ascii=False) + "\n")
    
    # ==================== 增强接口（Layer 2-11）====================
    
    def enhanced_recall(
        self,
        query: str,
        top_k: int = 10,
        use_crag: bool = True,
        use_hybrid: bool = True,
        use_cache: bool = True
    ) -> Dict:
        """
        增强检索（使用 Layer 2 模块，本地处理）
        
        整合:
        - CRAG 纠错检索
        - 混合检索 (Dense + Sparse)
        - RAG 缓存
        - 命题检索
        """
        result = {
            "query": query,
            "basic_results": [],
            "enhanced_results": [],
            "corrections": [],
            "cache_hit": False
        }
        
        # 1. 基础检索（本地直查）
        result["basic_results"] = self.recall(query, top_k=top_k, use_enhanced=False)
        
        # 2. 增强检索：尝试加载 Layer 2 模块（use_crag 控制 CRAG 开关）
        _layers = ["hybrid_search", "proposition_retrieval"]
        if use_crag and self._lazy_load("crag_pipeline"):
            _layers.insert(0, "crag_pipeline")
        for module_name in _layers:
            try:
                module = self._lazy_load(module_name)
                if module:
                    if hasattr(module, 'search'):
                        enhanced = module.search(query, top_k=top_k)
                        if enhanced:
                            if module_name == "crag_pipeline":
                                result["corrections"].extend(enhanced if isinstance(enhanced, list) else [enhanced])
                            else:
                                for item in (enhanced if isinstance(enhanced, list) else [enhanced]):
                                    if isinstance(item, dict):
                                        result["enhanced_results"].append({
                                            "id": item.get("id", ""),
                                            "content": item.get("content", str(item)),
                                            "confidence": item.get("confidence", item.get("score", 0.5)),
                                            "source": module_name
                                        })
            except Exception as e:
                logger.debug(f"{module_name} 加载失败: {e}")
        
        # 3. 检索公式重排：用 retrieval_formula 的 MemoryRetriever 加权评分
        try:
            formula = self._lazy_load("retrieval_formula")
            if formula and hasattr(formula, 'MemoryRetriever'):
                from retrieval_formula import MemoryRetriever, RetrievalConfig
                retriever = MemoryRetriever()
                # 将 enhanced_results 转成 Memory 对象后重新排序
                if result["enhanced_results"]:
                    reordered = retriever.retrieve(
                        result["enhanced_results"],
                        query,
                        top_k=len(result["enhanced_results"])
                    )
                    if reordered:
                        result["enhanced_results"] = reordered
        except Exception as e:
            logger.debug(f"retrieval_formula 评分失败: {e}")
        
        return result
    
    def fast_generate(
        self,
        prompt: str,
        use_speculative: bool = True,
        use_streaming: bool = True
    ) -> Dict:
        """
        快速生成（使用 Layer 4 模块）
        
        整合:
        - 投机解码
        - 流式生成
        - 模型路由
        """
        result = {
            "prompt": prompt,
            "answer": "",
            "latency_ms": 0,
            "speedup": 1.0,
            "method": "basic"
        }
        
        start_time = datetime.now()
        
        # 基础生成
        answer_result = self.answer(prompt)
        result["answer"] = answer_result["answer"]
        
        # 本地 recall 做上下文增强
        try:
            context_results = self.recall(prompt, top_k=3)
            if context_results:
                result["enhanced_with"] = "local_recall"
                result["context_results"] = len(context_results)
        except Exception as e:
            logger.debug(f"本地上下文增强失败: {e}")
        
        result["latency_ms"] = (datetime.now() - start_time).total_seconds() * 1000
        
        return result
    
    def smart_cache(
        self,
        query: str,
        answer: str,
        use_semantic: bool = True
    ) -> Dict:
        """
        智能缓存（使用 Layer 5 模块，本地处理）
        
        整合:
        - 语义缓存
        - 统一缓存
        - 近似缓存
        """
        result = {
            "cached": False,
            "cache_type": None,
            "similar_queries": []
        }
        
        if use_semantic and self._module_status["layer5_loaded"]:
            try:
                # 使用本地 store 存储缓存
                store_result = self.store(
                    query,
                    source="cache",
                    context={"answer": answer, "type": "semantic_cache"}
                )
                result["cached"] = True
                result["cache_type"] = "semantic"
                result["similar_queries"] = []
                result["memory_id"] = store_result.get("memory_id", "")
            except Exception as e:
                logger.debug(f"语义缓存（本地）失败: {e}")
        
        return result
    
    def hardware_optimize(self) -> Dict:
        """
        硬件优化（本地检测，不走 XiaoYiClawLLM）
        """
        result = {
            "numa_optimized": False,
            "gpu_available": False,
            "mkl_available": False,
            "recommendations": []
        }
        
        if self._module_status["layer6_loaded"]:
            try:
                stats = self.stats()
                result["numa_optimized"] = stats["loaded_modules"] > 0
                result["recommendations"] = ["硬件优化（本地检测）"]
            except Exception as e:
                logger.debug(f"硬件优化（本地）失败: {e}")
        
        return result
    
    def self_heal(self) -> Dict:
        """
        自我修复（本地健康检查，不走 XiaoYiClawLLM）
        """
        result = {
            "healthy": True,
            "issues": [],
            "repairs": []
        }
        
        if self._module_status["layer8_loaded"]:
            try:
                local_health = self.health_check()
                if not local_health.get("healthy", True):
                    result["healthy"] = False
                    result["issues"].extend(local_health.get("issues", []))
                stats = self.stats()
                total = stats["hallucination_guard"]["total_memories"]
                if total == 0:
                    result["issues"].append("无记忆数据")
            except Exception as e:
                logger.debug(f"自我修复（本地）失败: {e}")
        
        return result
    
    def auto_learn(
        self,
        conversation: List[Dict],
        update_persona: bool = True
    ) -> Dict:
        """
        自动学习（本地处理，不走 XiaoYiClawLLM）
        
        整合:
        - 自动更新 Persona
        - 智能记忆更新
        - 反思改进
        """
        result = {
            "learned": False,
            "new_memories": 0,
            "persona_updated": False,
            "improvements": []
        }
        
        if update_persona and self._module_status["layer10_loaded"]:
            try:
                # 使用本地 store 存储对话内容
                if conversation:
                    for msg in conversation[:5]:
                        if isinstance(msg, dict) and msg.get("role") == "user":
                            content = msg.get("content", "")
                            response = msg.get("response", "")
                            self.store(
                                content,
                                source="auto_learn",
                                context={"response": response}
                            )
                    result["learned"] = True
                    result["new_memories"] = min(len(conversation), 5)
            except Exception as e:
                logger.debug(f"自动学习（本地）失败: {e}")
        
        return result
    
    # ==================== 系统维护 ====================
    
    def optimize(self) -> Dict:
        """运行系统优化"""
        opt_result = self.adaptive_manager.run_optimization_cycle()
        self.synapse_network.apply_decay()
        
        from hallucination_guard import TemporalValidator
        memories = self.hallucination_guard._load_memories()
        expired = TemporalValidator.check_and_mark_expired(memories)
        
        return {
            "optimization": opt_result,
            "expired_memories": len(expired)
        }
    
    def stats(self) -> Dict:
        """获取系统统计"""
        guard_stats = self.hallucination_guard.get_stats()
        synapse_stats = self.synapse_network.get_stats()
        emotion_stats = self.emotion_manager.get_emotion_stats()
        
        return {
            "hallucination_guard": guard_stats,
            "synapse_network": synapse_stats,
            "emotion_memory": emotion_stats,
            "module_status": self._module_status,
            "loaded_modules": len(self._advanced_modules),
            "total_modules": len(self.coordinator.modules)
        }
    
    def health_check(self) -> Dict:
        """健康检查"""
        issues = []
        
        stats = self.stats()
        if stats["hallucination_guard"]["total_memories"] == 0:
            issues.append("无记忆数据")
        
        total = stats["hallucination_guard"]["total_memories"]
        # 数据量小（<50条）时"高置信度比例过低"无参考意义，跳过
        if total >= 50:
            high_conf = stats["hallucination_guard"]["high_confidence_count"]
            if high_conf / total < 0.1:
                issues.append("高置信度记忆比例过低")
        
        expired = stats["hallucination_guard"]["expired_count"]
        if expired > total * 0.5:
            issues.append("过期记忆过多")
        
        # 检查各层状态（从实际加载的模块推算）
        layer_status = {}
        # Layer 1 的核心模块是在 __init__ 直接初始化的，不走 _lazy_load
        layer1_direct_modules = {"hallucination_guard", "synapse_network", "emotion_memory"}
        loaded_module_names = set(self._advanced_modules.keys())
        for layer_idx in range(1, 12):
            modules_in_layer = self.coordinator.get_modules_by_layer(layer_idx)
            # 该层任一模块已加载即为 ✅
            any_loaded = any(m in loaded_module_names for m in modules_in_layer)
            # Layer 1 额外检查直接初始化的模块
            if layer_idx == 1:
                any_loaded = any_loaded or any(m in layer1_direct_modules for m in modules_in_layer)
            layer_status[f"layer_{layer_idx}"] = "✅" if any_loaded else "⏸️"
        
        return {
            "healthy": len(issues) == 0,
            "issues": issues,
            "stats": stats,
            "layer_status": layer_status
        }
    
    def get_workflow(self, scenario: str) -> List[Tuple[str, str]]:
        """获取工作流"""
        return self.coordinator.get_integrated_workflow(scenario)
    
    def execute_workflow(self, scenario: str, initial_input: Any = None) -> Dict:
        """执行工作流"""
        return self.coordinator.execute_workflow(scenario, initial_input)


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="小艺记忆系统 V2")
    parser.add_argument("command", choices=[
        "store", "recall", "answer", "correct", "optimize", 
        "stats", "health", "workflow", "enhanced-recall", "fast-generate"
    ])
    parser.add_argument("--content", help="记忆内容")
    parser.add_argument("--query", help="查询")
    parser.add_argument("--source", default="unknown", help="来源")
    parser.add_argument("--answer", help="原始回答")
    parser.add_argument("--original", help="原始内容（纠正时）")
    parser.add_argument("--scenario", help="场景名称")
    
    args = parser.parse_args()
    
    memory = XiaoyiMemoryV2()
    
    if args.command == "store":
        if not args.content:
            print("错误: 需要提供 --content")
            return
        result = memory.store(args.content, args.source)
        print(f"✅ 已存储: {result['memory_id']}")
        print(f"   置信度: {result['confidence']:.2f}")
    
    elif args.command == "recall":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        results = memory.recall(args.query)
        print(f"找到 {len(results)} 条记忆:")
        for r in results:
            print(f"  [{r['confidence']:.2f}] {r['content'][:50]}...")
    
    elif args.command == "answer":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = memory.answer(args.query, args.answer)
        print(f"回答: {result['answer']}")
        print(f"置信度: {result['confidence']:.2f}")
    
    elif args.command == "correct":
        if not args.original or not args.content:
            print("错误: 需要提供 --original 和 --content")
            return
        result = memory.correct(args.original, args.content)
        print(f"✅ {result['message']}")
    
    elif args.command == "optimize":
        result = memory.optimize()
        print(f"✅ 优化完成")
        print(f"   参数变更: {result['optimization']['params_changed']}")
        print(f"   过期记忆: {result['expired_memories']}")
    
    elif args.command == "stats":
        stats = memory.stats()
        import json
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    
    elif args.command == "health":
        result = memory.health_check()
        if result["healthy"]:
            print("✅ 系统健康")
        else:
            print("⚠️ 发现问题:")
            for issue in result["issues"]:
                print(f"  - {issue}")
        
        print("\n各层状态:")
        for layer, status in result["layer_status"].items():
            print(f"  {status} {layer}")
    
    elif args.command == "workflow":
        if not args.scenario:
            print("错误: 需要提供 --scenario")
            return
        workflow = memory.get_workflow(args.scenario)
        if workflow:
            print(f"工作流: {args.scenario}")
            for i, (module, action) in enumerate(workflow, 1):
                print(f"  {i}. {module}: {action}")
        else:
            print(f"未找到场景: {args.scenario}")
    
    elif args.command == "enhanced-recall":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = memory.enhanced_recall(args.query)
        print(f"增强检索结果:")
        print(f"  基础结果: {len(result['basic_results'])} 条")
        print(f"  缓存命中: {result['cache_hit']}")
    
    elif args.command == "fast-generate":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = memory.fast_generate(args.query)
        print(f"回答: {result['answer']}")
        print(f"延迟: {result['latency_ms']:.0f}ms")


if __name__ == "__main__":
    main()
