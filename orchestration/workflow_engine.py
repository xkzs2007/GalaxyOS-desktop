#!/usr/bin/env python3
"""
工作流引擎 - 执行预定义的工作流

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-23
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
import importlib.util

logger = logging.getLogger(__name__)

# 路径配置
SKILL_ROOT = Path(__file__).parent.parent
CORE_DIR = SKILL_ROOT / "skills/llm-memory-integration/core"
CONFIG_DIR = SKILL_ROOT / "config"


class WorkflowStatus(Enum):
    """工作流状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WorkflowStep:
    """工作流步骤"""
    module: str
    action: str
    description: str = ""
    required: bool = True
    timeout: int = 30
    retry: int = 0
    parallel_group: Optional[int] = None  # 同组并行执行，None=串行
    depends_on: Optional[List[int]] = None  # 依赖的步骤索引（确保在这些步骤完成后才执行）


@dataclass
class WorkflowResult:
    """工作流执行结果"""
    workflow_name: str
    status: WorkflowStatus
    steps_executed: int
    steps_total: int
    results: List[Dict[str, Any]]
    errors: List[str]
    duration_ms: float


class WorkflowEngine:
    """工作流引擎"""
    
    def __init__(self, coordinator=None):
        self.coordinator = coordinator
        self.workflows: Dict[str, List[WorkflowStep]] = {}
        self.module_cache: Dict[str, Any] = {}
        self._load_workflows()
        self._load_module_dependencies()
    
    def _load_workflows(self):
        """加载工作流定义"""
        # 内置工作流
        self.workflows = self._build_builtin_workflows()
        
        # 尝试从配置文件加载
        workflow_config = CONFIG_DIR / "workflows.json"
        if workflow_config.exists():
            try:
                data = json.loads(workflow_config.read_text())
                for name, steps in data.get("workflows", {}).items():
                    self.workflows[name] = [
                        WorkflowStep(**step) if isinstance(step, dict) else WorkflowStep(step[0], step[1])
                        for step in steps
                    ]
                logger.info(f"从配置文件加载了 {len(data.get('workflows', {}))} 个工作流")
            except Exception as e:
                logger.warning(f"加载工作流配置失败: {e}")
    
    def _load_module_dependencies(self):
        """加载模块依赖配置"""
        dep_config = CONFIG_DIR / "module_dependencies.json"
        if dep_config.exists():
            try:
                self.dependencies = json.loads(dep_config.read_text())
                logger.info(f"加载了 {len(self.dependencies.get('modules', {}))} 个模块依赖")
            except Exception as e:
                logger.warning(f"加载模块依赖失败: {e}")
                self.dependencies = {}
        else:
            self.dependencies = {}
    
    def _build_builtin_workflows(self) -> Dict[str, List[WorkflowStep]]:
        """构建内置工作流"""
        return {
            # ==================== 记忆核心工作流 ====================
            "learn_from_mistake": [
                WorkflowStep("memory_reflector", "get_reflection_summary", "获取反思摘要"),
                WorkflowStep("emotion_memory", "get_high_priority_memories", "情感记忆"),
                WorkflowStep("adaptive_memory", "run_optimization_cycle", "优化参数"),
            ],
            "neural_plasticity": [
                WorkflowStep("adaptive_ltp_ltd", "get_adjustment_stats", "LTP/LTD统计"),
                WorkflowStep("adaptive_memory", "run_optimization_cycle", "参数自适应"),
            ],
            
            # ==================== MemGPT 风格工作流 ====================
            "memgpt_recall": [
                WorkflowStep("memgpt_memory", "search", "核心记忆检索"),
                WorkflowStep("memory_stream", "get", "记忆流查询"),
                WorkflowStep("importance_scorer", "score", "重要性评分"),
                WorkflowStep("context_compressor", "compress", "上下文压缩"),
            ],

            
            # ==================== Generative Agents 工作流 ====================

            
            # ==================== Self-RAG 工作流 ====================
            "self_rag_query": [
                WorkflowStep("isrel_predictor", "should_retrieve", "预测检索必要性"),
                WorkflowStep("knowledge_refiner", "refine", "精炼知识"),
            ],

            
            # ==================== 知识图谱工作流 ====================
            "kg_build": [
                WorkflowStep("graph_constructor", "get_adjacency_matrix", "获取图谱邻接矩阵"),
                WorkflowStep("graph_constructor", "get_statistics", "获取图谱统计"),
            ],
            "kg_query": [
                WorkflowStep("memory_ontology_bridge", "get_entity", "查询实体"),
                WorkflowStep("graph_constructor", "get_statistics", "获取图谱统计"),
            ],
            
            # ==================== 检索增强工作流 ====================
            "recall": [
                WorkflowStep("emotion_memory", "get_high_priority_memories", "情感权重排序"),
                WorkflowStep("hallucination_guard", "verify_statement", "验证记忆准确性"),
                WorkflowStep("crag", "create_crag", "CRAG纠错"),
            ],
            "enhanced_recall": [
                WorkflowStep("hybrid_search", "search", "混合检索"),
                WorkflowStep("crag_pipeline", "run", "CRAG纠错"),
                WorkflowStep("adaptive_rrf", "get_adjustment_stats", "RRF统计"),
                WorkflowStep("rag_cache", "search", "缓存结果"),
            ],
            "safe_generation": [
                WorkflowStep("hallucination_guard", "verify_statement", "生成前验证"),
                WorkflowStep("adaptive_hallucination_params", "get_adjustment_stats", "动态阈值"),
            ],
            "cached_recall": [
                WorkflowStep("rag_cache", "search", "缓存命中检查"),
                WorkflowStep("semantic_cache", "get_stats", "语义缓存统计"),
                WorkflowStep("crag_pipeline", "run", "CRAG检索"),
            ],
            "proposition_recall": [
                WorkflowStep("proposition_retriever", "get_stats", "命题提取统计"),
                WorkflowStep("hybrid_search", "search", "混合检索"),
                WorkflowStep("adaptive_rrf", "get_adjustment_stats", "结果融合"),
            ],
            "multimodal_recall": [
                WorkflowStep("multimodal_memory", "get", "多模态记忆"),
                WorkflowStep("visual_generation", "visualize_memory", "视觉呈现"),
                WorkflowStep("emotion_memory", "get_high_priority_memories", "情感排序"),
            ],
            "cross_lingual_recall": [
                WorkflowStep("cross_lingual", "detect", "语言检测"),
                WorkflowStep("hybrid_search", "search", "混合检索"),
                WorkflowStep("adaptive_rrf", "get_adjustment_stats", "结果融合"),
            ],
            
            # ==================== 向量优化工作流 ====================
            "vector_index": [
                WorkflowStep("vector_api", "get_capabilities", "向量能力检测"),
                WorkflowStep("vector_api", "print_info", "向量信息"),
            ],
            "vector_search": [
                WorkflowStep("vector_api", "get_capabilities", "向量能力检测"),
                WorkflowStep("approximate_cache", "get_stats", "近似缓存统计"),
            ],
            
            # ==================== LLM 优化工作流 ====================

            "llm_optimize": [
                WorkflowStep("llm_optimizer", "get_stats", "LLM统计"),
                WorkflowStep("speculative_hybrid", "generate", "投机解码"),
            ],
            "smart_llm_call": [
                WorkflowStep("llm_optimizer", "generate", "调用LLM"),
                WorkflowStep("semantic_cache", "get_stats", "缓存统计"),
            ],
            
            # ==================== 缓存工作流 ====================
            "cache_warmup": [
                WorkflowStep("rag_cache", "search", "预热RAG缓存"),
                WorkflowStep("semantic_cache", "get_stats", "语义缓存统计"),
            ],

            
            # ==================== 硬件优化工作流 ====================
            "hardware_detect": [
                WorkflowStep("hardware_optimize", "get_info", "硬件检测"),
                WorkflowStep("numa_optimizer", "get_info", "NUMA拓扑"),
            ],
            "hardware_tune": [
                WorkflowStep("numa_optimizer", "get_info", "NUMA调优"),
                WorkflowStep("hugepage_manager", "get_stats", "大页内存"),
                WorkflowStep("hardware_optimize", "get_info", "硬件信息"),
            ],
            "realtime_tune": [
                WorkflowStep("cache_allocator", "get_info", "缓存分配"),
                WorkflowStep("hardware_optimize", "get_info", "硬件检测"),
            ],
            
            # ==================== 系统可靠性工作流 ====================
            "health_check": [
                WorkflowStep("failover", "check_all_health", "故障检测"),
                WorkflowStep("resilience_system", "check_all", "恢复检查"),
                WorkflowStep("auto_tuner", "get_results", "自动调优状态"),
                WorkflowStep("adaptive_memory", "get_current_params", "参数状态"),
            ],
            "failover_recover": [
                WorkflowStep("failover", "failover", "故障转移"),
                WorkflowStep("resilience_system", "check_all", "完整恢复"),
            ],
            
            # ==================== 会话管理工作流 ====================
            "long_conversation": [
                WorkflowStep("conversation", "get_conversation", "加载对话历史"),
                WorkflowStep("context_compressor", "compress", "压缩上下文"),
                WorkflowStep("memory_reflector", "get_reflection_summary", "召回相关记忆"),
                WorkflowStep("adaptive_memory", "run_optimization_cycle", "优化参数"),
            ],
            "session_manage": [
                WorkflowStep("conversation", "get_conversation", "会话管理"),
                WorkflowStep("context_compressor", "compress", "上下文压缩"),
            ],
            
            # ==================== Persona 工作流 ====================
            "persona_update": [
                WorkflowStep("auto_update_persona", "get_stats", "获取统计"),
                WorkflowStep("smart_memory_update", "summarize_with_llm", "智能摘要"),
            ],
            "preference_learn": [
                WorkflowStep("auto_learner", "get_recent_learnings", "获取最近学习"),
                WorkflowStep("importance_scorer", "score", "重要性评分"),
            ],
            
            # ==================== NLP 工作流 ====================
            "nlp_process": [
                WorkflowStep("nlp_processor", "process_text", "NLP处理"),
                WorkflowStep("nlp_integration", "extract_memory_keywords", "NLP整合"),
                WorkflowStep("importance_scorer", "score", "重要性评分"),
            ],
            "text_analyze": [
                WorkflowStep("nlp_processor", "process_text", "分词/实体/关键词"),
                WorkflowStep("emotion_memory", "get_high_priority_memories", "情感分析"),
                WorkflowStep("importance_scorer", "score", "重要性评分"),
            ],
            
            # ==================== 集成工作流 ====================
            "knowledge_sync": [
                WorkflowStep("brain_memory_sync", "search_entries", "同步知识库"),
                WorkflowStep("memory_ontology_bridge", "get_entity", "更新知识图谱"),
            ],
            ],
            "full_recall": [
                WorkflowStep("hybrid_search", "search", "混合检索"),
                WorkflowStep("crag_pipeline", "run", "CRAG纠错"),
                WorkflowStep("adaptive_rrf", "get_adjustment_stats", "结果融合"),
            ],
            
            # ==================== 优化工作流 ====================
            "optimization_run": [
                WorkflowStep("adaptive_hallucination_params", "get_adjustment_stats", "防幻觉参数"),
                WorkflowStep("adaptive_ltp_ltd", "get_adjustment_stats", "LTP/LTD"),
                WorkflowStep("adaptive_rrf", "get_adjustment_stats", "RRF权重"),
            ],
            "heartbeat_execute": [
                WorkflowStep("autonomous_integrator", "get_autonomous_status", "自主任务"),
                WorkflowStep("rules_manager", "get_rule_summary", "规则管理"),
                WorkflowStep("enhanced_hallucination_guard", "verify_with_cross_validation", "增强防幻觉"),
            ],
            
            # ==================== 多模态工作流 ====================
            "multi_modal_recall": [
                WorkflowStep("multimodal_memory", "get", "多模态记忆"),
                WorkflowStep("visual_generation", "visualize_memory", "视觉呈现"),
                WorkflowStep("emotion_memory", "get_high_priority_memories", "情感排序"),
            ],

            
            # ==================== 分布式工作流 ====================
            "distributed_recall": [
                WorkflowStep("distributed_search", "search", "分布式检索"),
                WorkflowStep("adaptive_rrf", "get_adjustment_stats", "结果融合"),
            ],
            
            # ==================== 工具注册工作流 ====================
            "tool_register": [
                WorkflowStep("auto_tuner", "get_results", "自动调优"),
            ],
            "resource_orchestrate": [
                WorkflowStep("resource_orchestrator", "generate_deployment_plan", "资源编排"),
                WorkflowStep("cache_allocator", "get_status", "缓存分配"),
            ],
        }
    
    def _get_default_input(self, workflow_name: str) -> Dict[str, Any]:
        """为每个工作流提供合适的默认参数"""
        defaults = {
            # 记忆相关
            'query': '测试查询内容',
            'text': '这是一段测试文本，用于验证工作流功能。',
            'content': '测试内容',
            'document': '这是一篇测试文档，包含多个段落和重要信息。',
            'doc_id': 'test_doc_001',
            
            # 向量相关
            'memory_id': 'test_memory_001',
            'memory_metadata': {'type': 'episodic', 'importance': 0.8},
            'created_at': '2026-04-24',
            'entity_count': 5,
            'content_length': 100,
            
            # 检索相关
            'dense_results': [{'id': '1', 'score': 0.9, 'text': '结果1'}],
            'sparse_results': [{'id': '2', 'score': 0.8, 'text': '结果2'}],
            'k': 10,
            
            # 缓存相关
            'query_embedding': [0.1] * 4096,  # 4096维向量
            'response': '这是缓存的响应内容',
            'metadata': {'source': 'test'},
            
            # 知识图谱相关
            'head_entity': '测试实体A',
            'relation': '相关',
            'tail_entity': '测试实体B',
            
            # NLP相关
            'statement': '这是一个需要验证的陈述',
            'claim': '这是一个需要验证的声明',
            
            # 多模态相关
            'image_path': '/tmp/test_image.jpg',
            'image_data': b'fake_image_data',
        }
        
        # 工作流特定的默认值
        workflow_defaults = {
            'adaptive_retrieval': {
                'query': '什么是人工智能？',
                'context': '人工智能是计算机科学的一个分支',
            },
            'agent_reflect': {
                'memories': [{'id': '1', 'content': '记忆1'}, {'id': '2', 'content': '记忆2'}],
            },
            'memgpt_recall': {
                'query': '用户偏好是什么？',
            },
            'memgpt_archive': {
                'content': '需要归档的记忆内容',
                'importance': 0.9,
            },
            'safe_generation': {
                'statement': '地球是圆的',
                'context': '科学常识',
            },
            'self_rag_query': {
                'query': '什么是机器学习？',
            },
            'kg_build': {
                'documents': [{'id': '1', 'text': '文档内容'}],
            },
            'kg_query': {
                'entity': '人工智能',
            },
            'image_understand': {
                'image_path': '/tmp/test.jpg',
                'query': '图片里有什么？',
            },
            'heartbeat_execute': {
                'claims': ['声明1', '声明2'],
                'sources': ['记忆', '知识图谱'],
            },
            'nlp_process': {
                'text': '这是一段需要进行NLP处理的中文文本。',
            },
            'text_analyze': {
                'text': '今天天气真好，心情很愉快。',
            },
            'proposition_recall': {
                'document': '人工智能是计算机科学的一个分支，旨在创建能够执行通常需要人类智能的任务的系统。',
                'doc_id': 'ai_doc_001',
            },
            'vector_index': {
                'vectors': [[0.1] * 128] * 10,  # 10个128维向量
                'ids': ['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9', 'v10'],
            },
            'vector_search': {
                'query': '搜索测试',
                'k': 5,
            },
        }
        
        # 合并默认值
        result = defaults.copy()
        if workflow_name in workflow_defaults:
            result.update(workflow_defaults[workflow_name])
        
        return result
    
    def get_workflow(self, name: str) -> Optional[List[WorkflowStep]]:
        """获取工作流"""
        return self.workflows.get(name)
    
    def list_workflows(self) -> List[str]:
        """列出所有工作流"""
        return list(self.workflows.keys())
    
    def get_workflow_info(self, name: str) -> Dict[str, Any]:
        """获取工作流信息"""
        workflow = self.workflows.get(name)
        if not workflow:
            return {"error": f"工作流 '{name}' 不存在"}
        
        return {
            "name": name,
            "steps": len(workflow),
            "modules": [step.module for step in workflow],
            "actions": [step.action for step in workflow],
            "descriptions": [step.description for step in workflow],
        }
    
    def _load_module(self, module_name: str) -> Optional[Any]:
        """加载模块"""
        if module_name in self.module_cache:
            return self.module_cache[module_name]
        
        # 尝试从核心目录加载
        module_path = CORE_DIR / f"{module_name}.py"
        if not module_path.exists():
            logger.warning(f"模块文件不存在: {module_path}")
            return None
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.module_cache[module_name] = module
                return module
        except Exception as e:
            logger.error(f"加载模块 {module_name} 失败: {e}")
        
        return None
    
    def _get_action_func(self, module: Any, action: str, module_name: str) -> Optional[Tuple[Any, bool]]:
        """
        获取动作函数，支持类和模块级函数
        
        返回: (func, is_instance) 或 None
        """
        # 1. 先尝试模块级函数
        action_func = getattr(module, action, None)
        if callable(action_func) and not isinstance(action_func, type):
            return (action_func, False)
        
        # 2. 尝试查找主类并实例化
        # 常见的类名模式: ModuleName, ModuleNameSearcher, ModuleNameManager 等
        potential_class_names = [
            module_name.split('_')[-1].title() + ''.join([w.title() for w in module_name.split('_')[1:]]),
            ''.join([w.title() for w in module_name.split('_')]),  # CamelCase
            module_name.title().replace('_', ''),  # PascalCase
        ]
        
        # 也尝试从模块中找第一个类
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and not attr_name.startswith('_'):
                # 找到了类，尝试实例化
                try:
                    instance = attr()
                    if hasattr(instance, action):
                        return (getattr(instance, action), True)
                except TypeError:
                    # 类需要参数，跳过
                    continue
        
        # 3. 尝试模块的主函数
        for fallback in ['process', 'run', 'execute', 'main']:
            fallback_func = getattr(module, fallback, None)
            if callable(fallback_func) and not isinstance(fallback_func, type):
                return (fallback_func, False)
        
        return None
    
    def _prepare_call_arg(
        self,
        current_data: Any,
        input_data: Any,
        module_name: str,
        action: str,
        action_func: Any = None
    ) -> Any:
        """
        智能准备调用参数
        
        根据模块和动作的特性，从 current_data 或 input_data 中提取合适的参数
        支持参数签名检测，自动匹配参数名
        """
        import inspect
        
        # 获取方法的参数签名
        param_names = []
        param_defaults = {}
        if action_func:
            try:
                sig = inspect.signature(action_func)
                for pname, pparam in sig.parameters.items():
                    if pname in ('self', 'cls'):
                        continue
                    param_names.append(pname)
                    if pparam.default != inspect.Parameter.empty:
                        param_defaults[pname] = pparam.default
            except Exception:
                pass
        
        # 收集可用的数据源
        available_data = {}
        
        # 从 input_data 收集
        if isinstance(input_data, dict):
            available_data.update(input_data)
        elif input_data is not None:
            available_data['_input'] = input_data
            
        # 从 current_data 收集
        if isinstance(current_data, dict):
            available_data.update(current_data)
        elif current_data is not None:
            available_data['_current'] = current_data
            # 尝试提取常见字段
            if isinstance(current_data, tuple) and len(current_data) > 0:
                first = current_data[0]
                if isinstance(first, str):
                    available_data['text'] = first
                    available_data['query'] = first
                elif isinstance(first, dict):
                    available_data.update(first)
                if first is None and len(current_data) > 1:
                    available_data['_current'] = current_data[1]
        
        # 如果方法需要特定参数名，尝试匹配
        if param_names:
            kwargs = {}
            for pname in param_names:
                # 直接匹配
                if pname in available_data:
                    val = available_data[pname]
                    # 如果值是字典但参数名暗示需要字符串，提取字符串
                    if isinstance(val, dict) and pname in ('text', 'query', 'content', 'statement', 'input_text'):
                        val = val.get('text') or val.get('query') or val.get('content') or str(val)
                    kwargs[pname] = val
                # 常见别名
                elif pname in ('query', 'q', 'question') and ('query' in available_data or 'text' in available_data):
                    kwargs[pname] = available_data.get('query') or available_data.get('text')
                elif pname in ('text', 'content', 'input_text', 'statement') and ('text' in available_data or 'content' in available_data):
                    kwargs[pname] = available_data.get('text') or available_data.get('content')
                elif pname in ('documents', 'docs', 'results') and '_current' in available_data:
                    kwargs[pname] = available_data['_current']
                # 使用默认值
                elif pname in param_defaults:
                    kwargs[pname] = param_defaults[pname]
            
            # 如果收集到了参数，返回 kwargs
            if kwargs:
                return kwargs
        
        # 回退逻辑：返回单个值
        # 如果是第一步，直接用 input_data
        if current_data is None:
            if isinstance(input_data, dict):
                for key in ['query', 'text', 'content']:
                    if key in input_data:
                        return input_data[key]
            return input_data
        
        # 如果 current_data 是元组，提取第一个元素
        if isinstance(current_data, tuple) and len(current_data) > 0:
            first = current_data[0]
            if isinstance(first, str):
                return first
            if first is None and len(current_data) > 1:
                return current_data[1]
        
        # 如果 current_data 是字典，尝试提取关键字段
        if isinstance(current_data, dict):
            for key in ['query', 'text', 'content', 'result', 'documents', 'data', 'output', 'generated_text']:
                if key in current_data:
                    return current_data[key]
            if len(current_data) == 1:
                return list(current_data.values())[0]
        
        # 如果 current_data 是列表
        if isinstance(current_data, list):
            if isinstance(input_data, dict) and 'query' in input_data:
                return input_data['query']
        
        return current_data
    
    def _execute_step(self, i: int, step: WorkflowStep, input_data: Any, context_results: Dict[int, Any]) -> Tuple[bool, Dict]:
        """执行单步工作流（可被并行调度）"""
        try:
            module = self._load_module(step.module)
            if not module:
                if step.required:
                    return False, {"step": i+1, "module": step.module, "action": step.action, "success": False, "error": "模块加载失败"}
                return True, {"step": i+1, "module": step.module, "action": step.action, "success": True, "skipped": True}

            action_result = self._get_action_func(module, step.action, step.module)
            if not action_result:
                if step.required:
                    return False, {"step": i+1, "module": step.module, "action": step.action, "success": False, "error": "没有可执行的函数"}
                return True, {"step": i+1, "module": step.module, "action": step.action, "success": True, "skipped": True}

            action_func, is_instance = action_result

            # 从上下文中找前序步骤的 output，step 0 直接用 input_data
            call_data = context_results.get(i - 1, None) if i > 0 else input_data
            # 如果前序步骤在并行组中，优先用主输入
            if step.parallel_group is not None:
                # 检查依赖是否完成
                if step.depends_on:
                    call_data = context_results.get(step.depends_on[0], input_data)
                else:
                    call_data = input_data

            call_arg = self._prepare_call_arg(call_data, input_data, step.module, step.action, action_func)

            import asyncio
            import inspect

            # 检测函数是否不接受任何参数（如 get_stats() 无参方法）
            _sig_params = list(inspect.signature(action_func).parameters.keys())
            _accepts_args = len([p for p in _sig_params if p not in ('self', 'cls')]) > 0

            if inspect.iscoroutinefunction(action_func):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    if not _accepts_args:
                        result = loop.run_until_complete(action_func())
                    elif isinstance(call_arg, dict):
                        result = loop.run_until_complete(action_func(**call_arg))
                    elif call_arg is not None:
                        result = loop.run_until_complete(action_func(call_arg))
                    else:
                        result = loop.run_until_complete(action_func())
                finally:
                    loop.close()
            else:
                if not _accepts_args:
                    result = action_func()
                elif isinstance(call_arg, dict):
                    result = action_func(**call_arg)
                elif call_arg is not None:
                    result = action_func(call_arg)
                else:
                    result = action_func()

            return True, {
                "step": i + 1,
                "module": step.module,
                "action": step.action,
                "success": True,
                "result": str(result)[:200] if result else None
            }
        except Exception as e:
            if step.required:
                return False, {"step": i+1, "module": step.module, "action": step.action, "success": False, "error": str(e)}
            return True, {"step": i+1, "module": step.module, "action": step.action, "success": True, "skipped": True, "error": str(e)}

    def execute_workflow(
        self,
        name: str,
        input_data: Any = None,
        context: Dict[str, Any] = None
    ) -> WorkflowResult:
        """执行工作流（支持并行组）"""
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_EXCEPTION
        start_time = time.time()

        workflow = self.workflows.get(name)
        if not workflow:
            return WorkflowResult(
                workflow_name=name,
                status=WorkflowStatus.FAILED,
                steps_executed=0,
                steps_total=0,
                results=[],
                errors=[f"工作流 '{name}' 不存在"],
                duration_ms=0
            )

        if not input_data:
            input_data = self._get_default_input(name)

        total_steps = len(workflow)
        results: List[Dict] = []
        errors: List[str] = []

        # 将步骤分组（串行组 vs 并行组）
        # 按 parallel_group 分组，None=串行，同组号并行
        groups: List[Tuple[str, List[int]]] = []  # [(group_type, [indices]), ...]
        current_serial = []  # 当前串行组
        parallel_buckets: Dict[int, List[int]] = {}  # group_no → [indices]

        for idx, step in enumerate(workflow):
            if step.parallel_group is None:
                # 收尾之前的并行组
                if parallel_buckets:
                    for g, indices in parallel_buckets.items():
                        groups.append(("parallel", indices))
                    parallel_buckets.clear()
                current_serial.append(idx)
            else:
                # 收尾之前的串行组
                if current_serial:
                    groups.append(("serial", current_serial))
                    current_serial = []
                if step.parallel_group not in parallel_buckets:
                    parallel_buckets[step.parallel_group] = []
                parallel_buckets[step.parallel_group].append(idx)

        # 收尾剩余
        if current_serial:
            groups.append(("serial", current_serial))
        if parallel_buckets:
            for g, indices in parallel_buckets.items():
                groups.append(("parallel", indices))

        # 逐组执行
        context_results: Dict[int, Any] = {}  # 步骤索引 → 输出结果
        all_results: Dict[int, Dict] = {}
        all_errors: List[str] = []
        steps_executed = 0

        for group_type, indices in groups:
            if group_type == "serial":
                # 串行执行组内各步
                for idx in indices:
                    step = workflow[idx]
                    ok, r = self._execute_step(idx, step, input_data, context_results)
                    all_results[idx] = r
                    results.append(r)
                    if r.get("success"):
                        steps_executed += 1
                        context_results[idx] = r.get("result", "")
                    if not ok:
                        errors.append(r.get("error", ""))
                        break  # 必要的步骤失败，中断工作流
            elif group_type == "parallel":
                # 并行执行同组内各步
                with ThreadPoolExecutor(max_workers=len(indices)) as executor:
                    fut_to_idx = {}
                    for idx in indices:
                        step = workflow[idx]
                        fut = executor.submit(self._execute_step, idx, step, input_data, context_results)
                        fut_to_idx[fut] = idx
                    for fut in as_completed(fut_to_idx, timeout=60):
                        idx = fut_to_idx[fut]
                        try:
                            ok, r = fut.result()
                            all_results[idx] = r
                            results.append(r)
                            if r.get("success"):
                                steps_executed += 1
                                context_results[idx] = r.get("result", "")
                            else:
                                # 检查是否是 required 步骤
                                step = workflow[idx]
                                if step.required:
                                    errors.append(r.get("error", ""))
                        except Exception as e:
                            errors.append(f"步骤 {idx+1} 异常: {e}")

        duration_ms = (time.time() - start_time) * 1000

        status = WorkflowStatus.COMPLETED if not errors else WorkflowStatus.FAILED

        return WorkflowResult(
            workflow_name=name,
            status=status,
            steps_executed=steps_executed,
            steps_total=total_steps,
            results=results,
            errors=errors[:10],
            duration_ms=duration_ms
        )
    
    def add_workflow(self, name: str, steps: List[WorkflowStep]) -> bool:
        """添加工作流"""
        if name in self.workflows:
            logger.warning(f"工作流 '{name}' 已存在，将被覆盖")
        self.workflows[name] = steps
        return True
    
    def remove_workflow(self, name: str) -> bool:
        """移除工作流"""
        if name in self.workflows:
            del self.workflows[name]
            return True
        return False
    
    def save_workflows(self) -> bool:
        """保存工作流到配置文件"""
        workflow_config = CONFIG_DIR / "workflows.json"
        try:
            data = {
                "version": "1.0.0",
                "workflows": {
                    name: [
                        {
                            "module": step.module,
                            "action": step.action,
                            "description": step.description,
                            "required": step.required,
                            "timeout": step.timeout,
                            "retry": step.retry,
                        }
                        for step in steps
                    ]
                    for name, steps in self.workflows.items()
                }
            }
            workflow_config.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            logger.info(f"工作流已保存到 {workflow_config}")
            return True
        except Exception as e:
            logger.error(f"保存工作流失败: {e}")
            return False


# CLI 接口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="工作流引擎")
    parser.add_argument("command", choices=["list", "info", "execute", "save"])
    parser.add_argument("--name", help="工作流名称")
    parser.add_argument("--input", help="输入数据")
    
    args = parser.parse_args()
    
    engine = WorkflowEngine()
    
    if args.command == "list":
        print(f"可用工作流 ({len(engine.list_workflows())} 个):")
        for name in sorted(engine.list_workflows()):
            info = engine.get_workflow_info(name)
            print(f"  {name}: {info['steps']} 步")
    
    elif args.command == "info":
        if not args.name:
            print("请指定 --name")
            sys.exit(1)
        info = engine.get_workflow_info(args.name)
        print(json.dumps(info, indent=2, ensure_ascii=False))
    
    elif args.command == "execute":
        if not args.name:
            print("请指定 --name")
            sys.exit(1)
        result = engine.execute_workflow(args.name, args.input)
        print(f"工作流: {result.workflow_name}")
        print(f"状态: {result.status.value}")
        print(f"步骤: {result.steps_executed}/{result.steps_total}")
        print(f"耗时: {result.duration_ms:.2f}ms")
        if result.errors:
            print("错误:")
            for err in result.errors:
                print(f"  - {err}")
    
    elif args.command == "save":
        if engine.save_workflows():
            print("工作流已保存")
        else:
            print("保存失败")
