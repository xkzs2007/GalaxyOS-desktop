#!/usr/bin/env python3
"""
弹性系统 (Resilience System)

适配当前架构的故障检测、降级、恢复机制：
- 36个防幻觉机制的健康监控
- 仿生神经网络模块的状态管理
- 多层模块的依赖关系处理
- 渐进式降级策略
- 自动恢复与热重载

Author: 小艺 Claw
Version: 2.0.0
Created: 2026-04-23
"""

import os
import sys
import json
import time
import asyncio
import threading
import importlib
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Callable, Set, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict
import logging
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

# ==================== 枚举定义 ====================

class ComponentStatus(Enum):
    """组件状态"""
    HEALTHY = "healthy"           # 健康
    DEGRADED = "degraded"         # 降级
    UNHEALTHY = "unhealthy"       # 不健康
    OFFLINE = "offline"           # 离线
    RECOVERING = "recovering"     # 恢复中


class ComponentTier(Enum):
    """组件层级"""
    CORE = "core"                 # 核心层（必须）
    CRITICAL = "critical"         # 关键层（重要）
    ENHANCED = "enhanced"         # 增强层（可选）
    OPTIONAL = "optional"         # 可选层（锦上添花）


class FailureType(Enum):
    """故障类型"""
    IMPORT_ERROR = "import_error"       # 导入失败
    RUNTIME_ERROR = "runtime_error"     # 运行时错误
    TIMEOUT = "timeout"                 # 超时
    RESOURCE_EXHAUSTED = "resource"     # 资源耗尽
    DEPENDENCY_FAILED = "dependency"    # 依赖失败
    DATA_CORRUPTION = "data_corruption" # 数据损坏


class RecoveryStrategy(Enum):
    """恢复策略"""
    RELOAD = "reload"             # 重新加载
    RESTART = "restart"           # 重启
    FALLBACK = "fallback"         # 降级回退
    REBUILD = "rebuild"           # 重建数据
    SKIP = "skip"                 # 跳过（可选组件）


# ==================== 数据结构 ====================

@dataclass
class ComponentInfo:
    """组件信息"""
    id: str
    name: str
    tier: ComponentTier
    module_path: str
    class_name: str
    description: str = ""

    # 依赖关系
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)

    # 状态
    status: ComponentStatus = ComponentStatus.HEALTHY
    last_check: str = ""
    last_error: str = ""
    error_count: int = 0
    success_count: int = 0

    # 配置
    max_errors: int = 3
    recovery_threshold: int = 2
    auto_reload: bool = True
    fallback_component: str = ""  # 降级替代组件

    # 实例
    instance: Any = None

    def to_dict(self) -> Dict:
        result = asdict(self)
        result['tier'] = self.tier.value
        result['status'] = self.status.value
        result.pop('instance', None)
        return result


@dataclass
class FailureEvent:
    """故障事件"""
    timestamp: str
    component_id: str
    failure_type: FailureType
    error_message: str
    stack_trace: str
    recovery_action: str
    recovered: bool = False

    def to_dict(self) -> Dict:
        result = asdict(self)
        result['failure_type'] = self.failure_type.value
        return result


# ==================== 组件注册表 ====================

# 当前架构的所有组件
COMPONENT_REGISTRY: Dict[str, ComponentInfo] = {
    # === 核心层 (CORE) - 必须正常 ===
    "memory_core": ComponentInfo(
        id="memory_core",
        name="记忆核心",
        tier=ComponentTier.CORE,
        module_path="xiaoyi_memory",
        class_name="XiaoyiMemoryV2",
        description="统一记忆入口",
        dependencies=[],
        max_errors=1,
        auto_reload=False
    ),
    "vector_store": ComponentInfo(
        id="vector_store",
        name="向量存储",
        tier=ComponentTier.CORE,
        module_path="unified_vector_store",
        class_name="UnifiedVectorStore",
        description="统一向量存储接口",
        dependencies=[],
        max_errors=1
    ),

    # === 关键层 (CRITICAL) - 重要功能 ===
    "hallucination_guard": ComponentInfo(
        id="hallucination_guard",
        name="防幻觉守护",
        tier=ComponentTier.CRITICAL,
        module_path="hallucination_guard",
        class_name="HallucinationGuard",
        description="基础防幻觉系统（10机制）",
        dependencies=["memory_core"],
        fallback_component="simple_validation"
    ),
    "enhanced_hallucination": ComponentInfo(
        id="enhanced_hallucination",
        name="增强防幻觉",
        tier=ComponentTier.CRITICAL,
        module_path="enhanced_hallucination_guard",
        class_name="EnhancedHallucinationGuard",
        description="增强版防幻觉（8机制）",
        dependencies=["hallucination_guard"],
        fallback_component="hallucination_guard"
    ),
    "self_rag": ComponentInfo(
        id="self_rag",
        name="Self-RAG框架",
        tier=ComponentTier.CRITICAL,
        module_path="self_rag",
        class_name="SelfRAG",
        description="Self-RAG检索增强（5机制）",
        dependencies=["vector_store"],
        fallback_component="basic_retrieval"
    ),
    "crag": ComponentInfo(
        id="crag",
        name="CRAG框架",
        tier=ComponentTier.CRITICAL,
        module_path="crag",
        class_name="CRAG",
        description="CRAG纠错检索（5机制）",
        dependencies=["vector_store", "self_rag"],
        fallback_component="self_rag"
    ),
    "synapse_network": ComponentInfo(
        id="synapse_network",
        name="突触网络",
        tier=ComponentTier.CRITICAL,
        module_path="memory_synapse_network",
        class_name="MemorySynapseNetwork",
        description="记忆突触网络",
        dependencies=["memory_core"],
        fallback_component="simple_linking"
    ),
    "emotion_memory": ComponentInfo(
        id="emotion_memory",
        name="情感记忆",
        tier=ComponentTier.CRITICAL,
        module_path="emotion_memory",
        class_name="EmotionMemoryManager",
        description="情感驱动记忆",
        dependencies=["memory_core"],
        fallback_component="neutral_weighting"
    ),
    "adaptive_memory": ComponentInfo(
        id="adaptive_memory",
        name="自适应记忆",
        tier=ComponentTier.CRITICAL,
        module_path="adaptive_memory",
        class_name="AdaptiveMemoryManager",
        description="自适应记忆架构",
        dependencies=["synapse_network", "emotion_memory"],
        fallback_component="static_params"
    ),

    # === 增强层 (ENHANCED) - 提升体验 ===
    "adaptive_hallucination_params": ComponentInfo(
        id="adaptive_hallucination_params",
        name="自适应防幻觉参数",
        tier=ComponentTier.ENHANCED,
        module_path="adaptive_hallucination_params",
        class_name="AdaptiveHallucinationParams",
        description="动态防幻觉阈值（5机制）",
        dependencies=["hallucination_guard"],
        fallback_component="static_thresholds"
    ),
    "dynamic_crag_threshold": ComponentInfo(
        id="dynamic_crag_threshold",
        name="动态CRAG阈值",
        tier=ComponentTier.ENHANCED,
        module_path="dynamic_crag_threshold",
        class_name="DynamicCRAGThreshold",
        description="动态CRAG阈值（3机制）",
        dependencies=["crag"],
        fallback_component="fixed_threshold"
    ),
    "adaptive_ltp_ltd": ComponentInfo(
        id="adaptive_ltp_ltd",
        name="自适应LTP/LTD",
        tier=ComponentTier.ENHANCED,
        module_path="adaptive_ltp_ltd",
        class_name="AdaptiveLTP_LTD",
        description="自适应突触可塑性",
        dependencies=["synapse_network"],
        fallback_component="static_ltp_ltd"
    ),
    "adaptive_rrf": ComponentInfo(
        id="adaptive_rrf",
        name="自适应RRF融合",
        tier=ComponentTier.ENHANCED,
        module_path="adaptive_rrf",
        class_name="AdaptiveRRF",
        description="自适应RRF权重",
        dependencies=["vector_store"],
        fallback_component="fixed_rrf"
    ),
    "thinking_skills": ComponentInfo(
        id="thinking_skills",
        name="思考技能协调器",
        tier=ComponentTier.ENHANCED,
        module_path="skill_coordinator",
        class_name="SkillCoordinator",
        description="思考技能智能路由",
        dependencies=[],
        fallback_component="basic_reasoning"
    ),

    "visual_generation": ComponentInfo(
        id="visual_generation",
        name="视觉生成模块",
        tier=ComponentTier.ENHANCED,
        module_path="visual_generation",
        class_name="VisualGenerator",
        description="记忆可视化",
        dependencies=["memory_core"],
        fallback_component="text_only"
    ),
    "crag_pipeline": ComponentInfo(
        id="crag_pipeline",
        name="CRAG流水线",
        tier=ComponentTier.CRITICAL,
        module_path="crag_pipeline",
        class_name="CRAGPipeline",
        description="完整CRAG流水线",
        dependencies=["vector_store"],
        fallback_component="crag"
    ),
    "rag_cache": ComponentInfo(
        id="rag_cache",
        name="RAG缓存",
        tier=ComponentTier.ENHANCED,
        module_path="rag_cache",
        class_name="RAGCache",
        description="RAG结果缓存",
        dependencies=["vector_store"],
        fallback_component="no_cache"
    ),
    "semantic_cache": ComponentInfo(
        id="semantic_cache",
        name="语义缓存",
        tier=ComponentTier.ENHANCED,
        module_path="semantic_cache",
        class_name="SemanticCache",
        description="语义级缓存",
        dependencies=[],
        fallback_component="exact_cache"
    ),
    "conversation": ComponentInfo(
        id="conversation",
        name="对话管理",
        tier=ComponentTier.CRITICAL,
        module_path="conversation",
        class_name="ConversationManager",
        description="对话上下文管理",
        dependencies=["memory_core"],
        fallback_component="simple_context"
    ),
    "context_compressor": ComponentInfo(
        id="context_compressor",
        name="上下文压缩",
        tier=ComponentTier.ENHANCED,
        module_path="context_compressor",
        class_name="ContextCompressor",
        description="长对话压缩",
        dependencies=["conversation"],
        fallback_component="truncate"
    ),
    "memgpt_memory": ComponentInfo(
        id="memgpt_memory",
        name="MemGPT记忆",
        tier=ComponentTier.ENHANCED,
        module_path="memgpt_memory",
        class_name="MemGPTMemory",
        description="MemGPT风格记忆管理",
        dependencies=["memory_core"],
        fallback_component="basic_memory"
    ),
    "brain_sync": ComponentInfo(
        id="brain_sync",
        name="知识库同步",
        tier=ComponentTier.ENHANCED,
        module_path="brain_memory_sync",
        class_name="BrainMemorySync",
        description="知识库与记忆同步",
        dependencies=["memory_core"],
        fallback_component="manual_sync"
    ),

    # === 可选层 (OPTIONAL) - 锦上添花 ===
    "memory_reflector": ComponentInfo(
        id="memory_reflector",
        name="记忆反思模块",
        tier=ComponentTier.OPTIONAL,
        module_path="memory_reflector",
        class_name="MemoryReflector",
        description="记忆反思与改进",
        dependencies=["memory_core"],
        auto_reload=True
    ),
    "proactive_tasks": ComponentInfo(
        id="proactive_tasks",
        name="主动任务系统",
        tier=ComponentTier.OPTIONAL,
        module_path="task_manager",  # 直接文件名
        class_name="",  # 函数式模块，无类
        description="主动任务执行",
        dependencies=[],
        auto_reload=True
    ),
    "heartbeat_executor": ComponentInfo(
        id="heartbeat_executor",
        name="心跳任务执行器",
        tier=ComponentTier.OPTIONAL,
        module_path="heartbeat_task_executor",
        class_name="HeartbeatTaskExecutor",
        description="心跳任务自动化",
        dependencies=["proactive_tasks"],
        auto_reload=True
    ),
}


# ==================== 弹性系统 ====================

class ResilienceSystem:
    """
    弹性系统
    
    功能：
    1. 组件健康监控
    2. 依赖关系管理
    3. 故障检测与隔离
    4. 渐进式降级
    5. 自动恢复
    6. 热重载
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or
            workspace())
        self.core_path = self.workspace_path / "skills" / "xiaoyi-claw-omega-final" / "skills" / "llm-memory-integration" / "core"

        # 添加到 Python 路径
        if str(self.core_path) not in sys.path:
            sys.path.insert(0, str(self.core_path))

        # 组件注册表
        self.components: Dict[str, ComponentInfo] = COMPONENT_REGISTRY.copy()

        # 故障历史
        self.failure_history: List[FailureEvent] = []
        self.max_history = 100

        # 监控状态
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # 回调
        self.on_failure_callbacks: List[Callable] = []
        self.on_recovery_callbacks: List[Callable] = []

        # 统计
        self.stats = {
            'total_checks': 0,
            'total_failures': 0,
            'total_recoveries': 0,
            'uptime_start': datetime.now(timezone.utc).isoformat()
        }

        logger.info("弹性系统初始化完成")

    # ==================== 组件管理 ====================

    def register_component(self, component: ComponentInfo):
        """注册组件"""
        with self._lock:
            self.components[component.id] = component
            logger.info(f"组件已注册: {component.id} ({component.tier.value})")

    def get_component(self, component_id: str) -> Optional[ComponentInfo]:
        """获取组件"""
        return self.components.get(component_id)

    def get_instance(self, component_id: str) -> Optional[Any]:
        """获取组件实例"""
        component = self.get_component(component_id)
        if component and component.instance:
            return component.instance

        # 尝试加载
        return self._load_component(component_id)

    def _load_component(self, component_id: str) -> Optional[Any]:
        """加载组件实例"""
        component = self.get_component(component_id)
        if not component:
            return None

        try:
            # 特殊处理：proactive_tasks 路径
            if component_id == "proactive_tasks":
                proactive_path = str(self.workspace_path / "skills" / "proactive-tasks" / "scripts")
                if proactive_path not in sys.path:
                    sys.path.insert(0, proactive_path)

            # 导入模块
            module = importlib.import_module(component.module_path)

            # 检查是否有类名（函数式模块无类）
            if not component.class_name:
                # 直接使用模块作为实例
                instance = module
            else:
                # 获取类
                cls = getattr(module, component.class_name)

                # 特殊处理：需要参数的类
                special_init = {
                    "reflection_engine": lambda: cls(None),  # MemoryStream=None
                    "planning_engine": lambda: cls(None),   # MemoryStream=None
                    "relation_predictor": lambda: cls(embedding_dim=128, hidden_dim=256),
                    "gat_layer": lambda: cls(in_features=128, out_features=64),
                    "graphsage_layer": lambda: cls(input_dim=128, output_dim=64),
                }

                if component_id in special_init:
                    instance = special_init[component_id]()
                else:
                    # 默认无参构造
                    instance = cls()

            with self._lock:
                component.instance = instance
                component.status = ComponentStatus.HEALTHY
                component.success_count += 1

            logger.info(f"组件加载成功: {component_id}")
            return instance

        except Exception as e:
            self._handle_failure(component_id, FailureType.IMPORT_ERROR, str(e))
            return None

    # ==================== 健康检查 ====================

    def check_component(self, component_id: str) -> bool:
        """检查单个组件健康"""
        component = self.get_component(component_id)
        if not component:
            return False

        try:
            # 检查实例是否存在
            if component.instance is None:
                # 尝试加载
                if self._load_component(component_id) is None:
                    return False

            # 检查依赖
            for dep_id in component.dependencies:
                dep = self.get_component(dep_id)
                if dep and dep.status != ComponentStatus.HEALTHY:
                    self._handle_failure(
                        component_id,
                        FailureType.DEPENDENCY_FAILED,
                        f"依赖组件 {dep_id} 不健康"
                    )
                    return False

            # 简单健康检查（调用实例方法）
            if hasattr(component.instance, 'get_stats'):
                component.instance.get_stats()

            # 更新状态
            with self._lock:
                component.status = ComponentStatus.HEALTHY
                component.last_check = datetime.now(timezone.utc).isoformat()
                component.success_count += 1
                component.error_count = 0

            return True

        except Exception as e:
            self._handle_failure(component_id, FailureType.RUNTIME_ERROR, str(e))
            return False

    def check_all(self) -> Dict[str, bool]:
        """检查所有组件"""
        results = {}

        # 按层级顺序检查（核心优先）
        tiers = [ComponentTier.CORE, ComponentTier.CRITICAL, ComponentTier.ENHANCED, ComponentTier.OPTIONAL]

        for tier in tiers:
            for comp_id, comp in self.components.items():
                if comp.tier == tier:
                    results[comp_id] = self.check_component(comp_id)

        self.stats['total_checks'] += 1
        return results

    # ==================== 故障处理 ====================

    def _handle_failure(self, component_id: str, failure_type: FailureType, error_msg: str):
        """处理故障"""
        component = self.get_component(component_id)
        if not component:
            return

        with self._lock:
            component.error_count += 1
            component.last_error = error_msg
            component.last_check = datetime.now(timezone.utc).isoformat()

            # 更新状态
            if component.error_count >= component.max_errors:
                component.status = ComponentStatus.UNHEALTHY
            else:
                component.status = ComponentStatus.DEGRADED

            # 记录故障事件
            event = FailureEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                component_id=component_id,
                failure_type=failure_type,
                error_message=error_msg,
                stack_trace=traceback.format_exc(),
                recovery_action=self._determine_recovery_action(component)
            )
            self.failure_history.append(event)

            # 限制历史长度
            if len(self.failure_history) > self.max_history:
                self.failure_history = self.failure_history[-self.max_history:]

            self.stats['total_failures'] += 1

        # 触发回调
        for callback in self.on_failure_callbacks:
            try:
                callback(component_id, failure_type, error_msg)
            except Exception as e:
                logger.error(f"故障回调执行失败: {e}")

        logger.warning(f"组件故障: {component_id} - {failure_type.value} - {error_msg}")

        # 尝试恢复
        self._attempt_recovery(component_id)

    def _determine_recovery_action(self, component: ComponentInfo) -> str:
        """确定恢复策略"""
        if component.tier == ComponentTier.CORE:
            return RecoveryStrategy.RESTART.value
        elif component.tier == ComponentTier.CRITICAL:
            if component.fallback_component:
                return RecoveryStrategy.FALLBACK.value
            return RecoveryStrategy.RELOAD.value
        elif component.tier == ComponentTier.ENHANCED:
            if component.fallback_component:
                return RecoveryStrategy.FALLBACK.value
            return RecoveryStrategy.SKIP.value
        else:  # OPTIONAL
            return RecoveryStrategy.SKIP.value

    def _attempt_recovery(self, component_id: str) -> bool:
        """尝试恢复组件"""
        component = self.get_component(component_id)
        if not component:
            return False

        action = self._determine_recovery_action(component)

        try:
            if action == RecoveryStrategy.RELOAD.value:
                # 重新加载模块
                if component.auto_reload:
                    if component.module_path in sys.modules:
                        importlib.reload(sys.modules[component.module_path])
                    return self._load_component(component_id) is not None

            elif action == RecoveryStrategy.FALLBACK.value:
                # 使用降级组件
                if component.fallback_component:
                    fallback = self.get_component(component.fallback_component)
                    if fallback and fallback.status == ComponentStatus.HEALTHY:
                        logger.info(f"组件 {component_id} 降级到 {component.fallback_component}")
                        return True

            elif action == RecoveryStrategy.SKIP.value:
                # 跳过可选组件
                component.status = ComponentStatus.OFFLINE
                logger.info(f"可选组件 {component_id} 已跳过")
                return True

        except Exception as e:
            logger.error(f"恢复失败: {component_id} - {e}")

        return False

    # ==================== 降级策略 ====================

    def get_degradation_level(self) -> Tuple[int, str]:
        """
        获取系统降级级别
        
        Returns:
            Tuple[int, str]: (级别, 描述)
            0 = 完全健康
            1 = 可选组件降级
            2 = 增强组件降级
            3 = 关键组件降级
            4 = 核心组件故障
        """
        unhealthy_counts = defaultdict(int)

        for comp in self.components.values():
            if comp.status != ComponentStatus.HEALTHY:
                unhealthy_counts[comp.tier] += 1

        if unhealthy_counts[ComponentTier.CORE] > 0:
            return (4, "核心组件故障，系统不可用")
        elif unhealthy_counts[ComponentTier.CRITICAL] > 0:
            return (3, f"关键组件降级，{unhealthy_counts[ComponentTier.CRITICAL]}个组件异常")
        elif unhealthy_counts[ComponentTier.ENHANCED] > 0:
            return (2, f"增强组件降级，{unhealthy_counts[ComponentTier.ENHANCED]}个组件异常")
        elif unhealthy_counts[ComponentTier.OPTIONAL] > 0:
            return (1, f"可选组件降级，{unhealthy_counts[ComponentTier.OPTIONAL]}个组件异常")
        else:
            return (0, "系统完全健康")

    def get_available_features(self) -> Dict[str, List[str]]:
        """获取当前可用的功能"""
        available = {
            'core': [],
            'critical': [],
            'enhanced': [],
            'optional': []
        }

        for comp in self.components.values():
            if comp.status == ComponentStatus.HEALTHY:
                available[comp.tier.value].append(comp.name)

        return available

    # ==================== 监控 ====================

    def start_monitoring(self, interval: float = 30.0):
        """启动后台监控"""
        if self._monitoring:
            return

        self._monitoring = True

        def monitor_loop():
            while self._monitoring:
                try:
                    self.check_all()
                except Exception as e:
                    logger.error(f"监控检查失败: {e}")

                time.sleep(interval)

        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"后台监控已启动，间隔 {interval}s")

    def stop_monitoring(self):
        """停止监控"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("后台监控已停止")

    # ==================== 报告 ====================

    def get_health_report(self) -> Dict:
        """获取健康报告"""
        level, desc = self.get_degradation_level()
        features = self.get_available_features()

        component_status = {}
        for comp_id, comp in self.components.items():
            component_status[comp_id] = {
                'name': comp.name,
                'tier': comp.tier.value,
                'status': comp.status.value,
                'error_count': comp.error_count,
                'last_error': comp.last_error[:100] if comp.last_error else None
            }

        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'degradation_level': level,
            'degradation_desc': desc,
            'total_components': len(self.components),
            'healthy_components': sum(1 for c in self.components.values() if c.status == ComponentStatus.HEALTHY),
            'available_features': features,
            'components': component_status,
            'stats': self.stats,
            'recent_failures': [f.to_dict() for f in self.failure_history[-5:]]
        }

    def print_report(self):
        """打印健康报告"""
        report = self.get_health_report()

        print("=" * 60)
        print("弹性系统健康报告")
        print("=" * 60)
        print(f"时间: {report['timestamp']}")
        print(f"降级级别: {report['degradation_level']} - {report['degradation_desc']}")
        print(f"健康组件: {report['healthy_components']}/{report['total_components']}")
        print()

        print("可用功能:")
        for tier, features in report['available_features'].items():
            if features:
                print(f"  [{tier}] {', '.join(features)}")

        print()
        print("组件状态:")
        for comp_id, info in report['components'].items():
            status_icon = "✅" if info['status'] == 'healthy' else "⚠️" if info['status'] == 'degraded' else "❌"
            print(f"  {status_icon} {info['name']} ({info['tier']}): {info['status']}")
            if info['last_error']:
                print(f"      错误: {info['last_error']}")

        if report['recent_failures']:
            print()
            print("最近故障:")
            for f in report['recent_failures']:
                print(f"  - [{f['timestamp'][:19]}] {f['component_id']}: {f['failure_type']}")

        print("=" * 60)


# ==================== 单例 ====================

_resilience_system: Optional[ResilienceSystem] = None

def get_resilience_system() -> ResilienceSystem:
    """获取弹性系统单例"""
    global _resilience_system
    if _resilience_system is None:
        _resilience_system = ResilienceSystem()
    return _resilience_system


# ==================== 测试 ====================

if __name__ == "__main__":
    print("弹性系统测试")
    print("=" * 60)

    system = ResilienceSystem()

    # 检查所有组件
    print("\n检查所有组件...")
    results = system.check_all()

    # 打印报告
    print()
    system.print_report()

    # 获取降级级别
    level, desc = system.get_degradation_level()
    print(f"\n降级级别: {level} - {desc}")


# ==================== P2: 硬件优化模块注册 ====================

HARDWARE_MODULES = {
    "numa_optimizer": ComponentInfo(
        id="numa_optimizer",
        name="NUMA优化器",
        tier=ComponentTier.OPTIONAL,
        module_path="numa_optimizer",
        class_name="NUMAOptimizer",
        description="NUMA架构优化",
        dependencies=[],
        auto_reload=True
    ),
    "gpu_optimizer": ComponentInfo(
        id="gpu_optimizer",
        name="GPU优化器",
        tier=ComponentTier.OPTIONAL,
        module_path="gpu_optimizer",
        class_name="GPUOptimizer",
        description="GPU加速优化",
        dependencies=[],
        auto_reload=True
    ),
    "mkl_accelerator": ComponentInfo(
        id="mkl_accelerator",
        name="MKL加速器",
        tier=ComponentTier.OPTIONAL,
        module_path="mkl_accelerator",
        class_name="MKLAccelerator",
        description="Intel MKL加速",
        dependencies=[],
        auto_reload=True
    ),
    "io_optimizer": ComponentInfo(
        id="io_optimizer",
        name="IO优化器",
        tier=ComponentTier.OPTIONAL,
        module_path="io_optimizer",
        class_name="IOOptimizer",
        description="IO性能优化",
        dependencies=[],
        auto_reload=True
    ),
    "resource_orchestrator": ComponentInfo(
        id="resource_orchestrator",
        name="资源编排器",
        tier=ComponentTier.OPTIONAL,
        module_path="resource_orchestrator",
        class_name="ResourceOrchestrator",
        description="资源统一调度",
        dependencies=[],
        auto_reload=True
    ),
}

# 合并到主注册表
COMPONENT_REGISTRY.update(HARDWARE_MODULES)


# ==================== 剩余高价值模块注册 ====================

REMAINING_MODULES = {
    # 记忆核心
    "memory_bank": ComponentInfo(
        id="memory_bank",
        name="多银行记忆",
        tier=ComponentTier.ENHANCED,
        module_path="memory_bank",
        class_name="MemoryBank",
        description="多银行记忆系统",
        dependencies=["memory_core"],
        fallback_component="single_bank"
    ),
    "memory_functions": ComponentInfo(
        id="memory_functions",
        name="记忆函数库",
        tier=ComponentTier.ENHANCED,
        module_path="memory_functions",
        class_name="MemoryFunctions",
        description="记忆操作函数集合",
        dependencies=["memory_core"],
        fallback_component="basic_ops"
    ),
    "unified_vector_store": ComponentInfo(
        id="unified_vector_store",
        name="统一向量存储",
        tier=ComponentTier.CORE,
        module_path="unified_vector_store",
        class_name="UnifiedVectorStore",
        description="统一向量存储接口",
        dependencies=[],
        max_errors=1
    ),
    "memory_ontology_bridge": ComponentInfo(
        id="memory_ontology_bridge",
        name="记忆-知识图谱桥接",
        tier=ComponentTier.ENHANCED,
        module_path="memory_ontology_bridge",
        class_name="MemoryOntologyBridge",
        description="记忆与知识图谱双向同步",
        dependencies=["memory_core"],
        fallback_component="no_ontology"
    ),
    "xiaoyi_claw_api": ComponentInfo(
        id="xiaoyi_claw_api",
        name="小艺Claw API",
        tier=ComponentTier.CORE,
        module_path="xiaoyi_claw_api",
        class_name="XiaoYiClawLLM",
        description="统一API接口",
        dependencies=["memory_core"],
        max_errors=1
    ),

    # Self-RAG 组件
    "isrel_predictor": ComponentInfo(
        id="isrel_predictor",
        name="IsREL预测器",
        tier=ComponentTier.CRITICAL,
        module_path="isrel_predictor",
        class_name="IsRELPredictor",
        description="检索必要性预测",
        dependencies=[],
        fallback_component="always_retrieve"
    ),
    "issup_predictor": ComponentInfo(
        id="issup_predictor",
        name="IsSUP预测器",
        tier=ComponentTier.CRITICAL,
        module_path="issup_predictor",
        class_name="IsSUPPredictor",
        description="检索结果相关性预测",
        dependencies=[],
        fallback_component="assume_relevant"
    ),
    "isuse_predictor": ComponentInfo(
        id="isuse_predictor",
        name="IsUSE预测器",
        tier=ComponentTier.CRITICAL,
        module_path="isuse_predictor",
        class_name="IsUSEPredictor",
        description="生成内容可靠性预测",
        dependencies=[],
        fallback_component="assume_useful"
    ),

    # CRAG 组件
    "retrieval_evaluator": ComponentInfo(
        id="retrieval_evaluator",
        name="检索评估器",
        tier=ComponentTier.CRITICAL,
        module_path="retrieval_evaluator",
        class_name="RetrievalEvaluator",
        description="检索结果质量评估",
        dependencies=[],
        fallback_component="accept_all"
    ),
    "knowledge_refiner": ComponentInfo(
        id="knowledge_refiner",
        name="知识精炼器",
        tier=ComponentTier.CRITICAL,
        module_path="knowledge_refiner",
        class_name="KnowledgeRefiner",
        description="知识精炼提取",
        dependencies=[],
        fallback_component="raw_knowledge"
    ),
    "knowledge_augmentor": ComponentInfo(
        id="knowledge_augmentor",
        name="知识补充器",
        tier=ComponentTier.CRITICAL,
        module_path="knowledge_augmentor",
        class_name="KnowledgeAugmentor",
        description="Web搜索知识补充",
        dependencies=[],
        fallback_component="no_augment"
    ),

    # 检索增强
    "proposition_retriever": ComponentInfo(
        id="proposition_retriever",
        name="命题检索器",
        tier=ComponentTier.ENHANCED,
        module_path="proposition_retriever",
        class_name="PropositionRetriever",
        description="命题级检索",
        dependencies=["unified_vector_store"],
        fallback_component="chunk_retrieval"
    ),
    "importance_scorer": ComponentInfo(
        id="importance_scorer",
        name="重要性评分器",
        tier=ComponentTier.ENHANCED,
        module_path="importance_scorer",
        class_name="ImportanceScorer",
        description="记忆重要性评分",
        dependencies=["memory_core"],
        fallback_component="equal_weight"
    ),
    "multimodal_memory": ComponentInfo(
        id="multimodal_memory",
        name="多模态记忆",
        tier=ComponentTier.ENHANCED,
        module_path="multimodal_memory",
        class_name="MultimodalMemoryStore",
        description="多模态记忆管理",
        dependencies=["memory_core"],
        fallback_component="text_only"
    ),

    # 反思引擎
    "reflection_engine": ComponentInfo(
        id="reflection_engine",
        name="反思引擎",
        tier=ComponentTier.OPTIONAL,
        module_path="reflection_engine",
        class_name="ReflectionEngine",
        description="深度反思引擎",
        dependencies=[],
        auto_reload=True
    ),
    "planning_engine": ComponentInfo(
        id="planning_engine",
        name="规划引擎",
        tier=ComponentTier.OPTIONAL,
        module_path="planning_engine",
        class_name="PlanningEngine",
        description="任务规划引擎",
        dependencies=[],
        auto_reload=True
    ),

    # 知识图谱 GNN
    "graph_constructor": ComponentInfo(
        id="graph_constructor",
        name="图谱构建器",
        tier=ComponentTier.ENHANCED,
        module_path="graph_constructor",
        class_name="GraphConstructor",
        description="知识图谱构建",
        dependencies=[],
        fallback_component="no_graph"
    ),
    "gat_layer": ComponentInfo(
        id="gat_layer",
        name="GAT层",
        tier=ComponentTier.OPTIONAL,
        module_path="gat_layer",
        class_name="GATLayer",
        description="图注意力网络层",
        dependencies=["graph_constructor"],
        auto_reload=True
    ),
    "graphsage_layer": ComponentInfo(
        id="graphsage_layer",
        name="GraphSAGE层",
        tier=ComponentTier.OPTIONAL,
        module_path="graphsage_layer",
        class_name="GraphSAGELayer",
        description="GraphSAGE网络层",
        dependencies=["graph_constructor"],
        auto_reload=True
    ),

    # 防幻觉集成
    "hallucination_integration": ComponentInfo(
        id="hallucination_integration",
        name="防幻觉集成器",
        tier=ComponentTier.CRITICAL,
        module_path="hallucination_integration",
        class_name="HallucinationIntegratedMemory",
        description="防幻觉系统集成接口",
        dependencies=["hallucination_guard", "memory_core"],
        fallback_component="no_protection"
    ),

    # 向量优化
    "quantization": ComponentInfo(
        id="quantization",
        name="向量量化",
        tier=ComponentTier.ENHANCED,
        module_path="quantization",
        class_name="INT8Quantizer",
        description="向量量化压缩",
        dependencies=["unified_vector_store"],
        fallback_component="full_precision"
    ),
    "sqlite_ext": ComponentInfo(
        id="sqlite_ext",
        name="SQLite扩展",
        tier=ComponentTier.OPTIONAL,
        module_path="sqlite_ext",
        class_name="",  # 无类，跳过
        description="SQLite向量扩展",
        dependencies=[],
        auto_reload=True
    ),
    "retrieval_eval": ComponentInfo(
        id="retrieval_eval",
        name="检索评估",
        tier=ComponentTier.ENHANCED,
        module_path="retrieval_eval",
        class_name="NDCGEvaluator",
        description="检索效果评估",
        dependencies=[],
        fallback_component="no_eval"
    ),

    # 其他
    "relation_predictor": ComponentInfo(
        id="relation_predictor",
        name="关系预测器",
        tier=ComponentTier.OPTIONAL,
        module_path="relation_predictor",
        class_name="RelationPredictor",
        description="实体关系预测",
        dependencies=[],
        auto_reload=True
    ),
    "dep_checker": ComponentInfo(
        id="dep_checker",
        name="依赖检查器",
        tier=ComponentTier.OPTIONAL,
        module_path="dep_checker",
        class_name="DependencyChecker",
        description="依赖关系检查",
        dependencies=[],
        auto_reload=True
    ),
}

COMPONENT_REGISTRY.update(REMAINING_MODULES)
