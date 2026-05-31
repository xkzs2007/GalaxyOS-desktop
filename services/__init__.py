"""
LLM Memory Integration - 私有增强包

提供横向能力：
1. 工具注册（Tools Registry）
2. ACP 多智能体协作（ACP Server）
3. 跨平台兼容（Platform Adapter）
4. 沙箱隔离与自动更新（Sandbox Manager）
5. GPU 优化检测（GPU Optimizer）
6. 安全与对齐（Safety Alignment）
7. 检索质量评估（Retrieval Eval）
8. 统一异常体系（Exceptions）
9. 统一日志配置（Logging Config）
10. 统一缓存接口（Unified Cache）
"""

from .tools_registry import (
    ToolsRegistry,
    ToolDefinition,
    registry,
    register_tool
)

from .acp_server import (
    ACPServer,
    ACPTool
)

from .platform_adapter import (
    PlatformAdapter,
    PlatformInfo,
    adapter,
    get_platform_info,
    get_binary,
    get_platform_config
)

from .sandbox_manager import (
    SandboxManager,
    VersionInfo
)

from .dep_checker import (
    DependencyChecker,
    DependencyInfo,
    ModuleStatus,
    checker,
    check_dependencies,
    print_dependency_report,
    get_missing_dependencies,
    get_module_status
)

from .gpu_optimizer import (
    GPUDetector,
    GPUOptimizer,
    GPUDevice,
    KVCacheConfig,
    InferenceConfig,
    gpu_optimizer,
    detect_gpu,
    get_gpu_inference_config,
)

from .safety_alignment import (
    InputSafetyFilter,
    HallucinationDetector,
    ContentPolicyEnforcer,
    RedTeamHelper,
    SafetyResult,
    input_safety,
    hallucination_detector,
    content_policy,
    redteam_helper,
)

from .retrieval_eval import (
    NDCGEvaluator,
    MRREvaluator,
    RAGEvaluator,
    RelevanceJudgment,
    RetrievalEvalResult,
    ndcg_evaluator,
    mrr_evaluator,
    rag_evaluator,
)

from .exceptions import (
    SkillError,
    EmbeddingError,
    LLMError,
    CacheError,
    SafetyError,
    SearchError,
    AuthenticationError,
    DependencyError,
    SerializationError,
)

from .logging_config import (
    setup_logging,
    get_logger,
)

from .unified_cache import UnifiedCache

# ---- 系统级 / 硬件级优化模块 (v2.1 新增) ----
from .vector_api import (
    VectorAPI, SIMDArch, VectorBackendInfo,
    get_vector_api, detect_simd_arch,
)
from .hardware_optimize import (
    HardwareOptimizer,
    AMXAccelerator,
    FMAAccelerator,
    NeuralEngineAccelerator,
    NEONAccelerator,
    CacheBlocker,
)
from .numa_optimizer import (
    NUMATopology,
    NUMAOptimizer,
    get_numa_optimizer,
    check_numa_status,
)
from .cache_aware_scheduler import (
    CacheTopology,
    CacheAwareScheduler,
    get_cache_aware_scheduler,
    check_cas_status,
)
from .realtime_scheduler import (
    SCHED_OTHER, SCHED_FIFO, SCHED_RR, SCHED_BATCH,
    SCHED_IDLE, SCHED_DEADLINE,
    MIN_RT_PRIO, MAX_RT_PRIO,
    SchedParam, SchedInfo, SchedDeadlineAttr,
    RealtimeScheduler, DeadlineScheduler, ThreadPriority,
    set_deadline_scheduler, check_deadline_capability,
    check_realtime_capability, print_realtime_status,
    apply_chrt,
    get_scheduler, set_scheduler,
    get_priority, set_priority,
    get_policy_name, get_cpu_affinity, set_cpu_affinity,
)
from .power_manager import (
    RAPLMonitor, DVFSController, ThermalMonitor,
    HWPEnergyPerformanceHint, PowerManager,
    get_power_manager, check_power_status,
)
from .cache_allocator import (
    CATDetector, CacheAllocator, CLOSConfig,
    get_cache_allocator, check_cache_allocation_support,
)
from .cxl_optimizer import (
    MemoryType, MemoryNode,
    CXLMemoryDetector, AdaptiveScheduler,
    HotDataMigrator, CXLOptimizer,
)
from .mkl_accelerator import (
    MKLAccelerator, FMALAccelerator,
    OptimizedMatrixOps, INT8QuantizedOps,
    check_mkl_available, check_amx_available,
    check_intel_cpu, print_mkl_status,
    check_mkl_status, check_fmal_status,
)
from .io_optimizer import (
    BlockDeviceScanner, IOOptimizer,
    IODevice, IOOptimizationRecommendation,
    get_io_optimizer, check_io_status,
)
from .resource_orchestrator import (
    ResourceOrchestrator, Priority,
    TaskProfile, DeploymentPlan, DeploymentPlanItem,
    ResourceConstraint, create_orchestrator,
)

from .module_coordinator import (
    ModuleCoordinator,
    EventBus,
    ProcessManager,
    CrossLayerOrchestrator,
    HealthDashboard,
    PipelineBuilder,
    PipelineExecutor,
    PipelineSpec,
    PipelineStage,
    ScriptsCoreBridge,
    WorkerSpec,
    WorkerInfo,
    ModuleLayer,
    ProcessStatus,
    EventType,
    Event,
    generic_worker,
    create_coordinator,
    create_worker_spec,
)


# 版本信息
__version__ = "3.0.5"
__author__ = "xkzs2007"


# ============ 便捷函数 ============

def get_registry() -> ToolsRegistry:
    """获取工具注册中心"""
    return registry


def get_acp_server() -> ACPServer:
    """获取 ACP Server"""
    return ACPServer()


def get_sandbox_manager() -> SandboxManager:
    """获取沙箱管理器"""
    return SandboxManager()


def initialize():
    """初始化私有包"""
    # 检测平台
    platform_info = get_platform_info()
    print(f"Platform: {platform_info.system} {platform_info.machine}")

    # 获取优化配置
    config = get_platform_config()
    optimizations = config.get('optimizations', {})

    enabled_opts = [k for k, v in optimizations.items() if v]
    if enabled_opts:
        print(f"Optimizations: {', '.join(enabled_opts)}")

    # 初始化沙箱
    sandbox = get_sandbox_manager()
    version = sandbox.get_current_version()

    if version:
        print(f"Version: {version.version} ({version.checksum})")
    else:
        print("Version: not initialized")

    # 注册工具
    tools = registry.list_tools()
    print(f"Tools registered: {len(tools)}")

    # 依赖检测
    dep_results = check_dependencies()
    missing = [d for d in dep_results.values() if not d.installed]
    if missing:
        print(f"Dependencies: {len(dep_results) - len(missing)}/{len(dep_results)} installed")
    else:
        print(f"Dependencies: all {len(dep_results)} installed")

    return {
        'platform': platform_info,
        'optimizations': optimizations,
        'version': version,
        'tools': tools,
        'dependencies': dep_results
    }


# ============ 导出 ============

__all__ = [
    # 工具注册
    'ToolsRegistry',
    'ToolDefinition',
    'registry',
    'register_tool',
    'get_registry',

    # ACP Server
    'ACPServer',
    'ACPTool',
    'get_acp_server',

    # 平台适配
    'PlatformAdapter',
    'PlatformInfo',
    'adapter',
    'get_platform_info',
    'get_binary',
    'get_platform_config',

    # 沙箱管理
    'SandboxManager',
    'VersionInfo',
    'get_sandbox_manager',

    # 依赖检测
    'DependencyChecker',
    'DependencyInfo',
    'ModuleStatus',
    'checker',
    'check_dependencies',
    'print_dependency_report',
    'get_missing_dependencies',
    'get_module_status',

    # GPU 优化
    'GPUDetector',
    'GPUOptimizer',
    'GPUDevice',
    'KVCacheConfig',
    'InferenceConfig',
    'gpu_optimizer',
    'detect_gpu',
    'get_gpu_inference_config',

    # 安全与对齐
    'InputSafetyFilter',
    'HallucinationDetector',
    'ContentPolicyEnforcer',
    'RedTeamHelper',
    'SafetyResult',
    'input_safety',
    'hallucination_detector',
    'content_policy',
    'redteam_helper',

    # 检索评估
    'NDCGEvaluator',
    'MRREvaluator',
    'RAGEvaluator',
    'RelevanceJudgment',
    'RetrievalEvalResult',
    'ndcg_evaluator',
    'mrr_evaluator',
    'rag_evaluator',

    # 统一异常
    'SkillError',
    'EmbeddingError',
    'LLMError',
    'CacheError',
    'SafetyError',
    'SearchError',
    'AuthenticationError',
    'DependencyError',
    'SerializationError',

    # 统一日志
    'setup_logging',
    'get_logger',

    # 统一缓存
    'UnifiedCache',

    # 硬件优化
    'HardwareOptimizer',
    'AMXAccelerator',
    'FMAAccelerator',
    'NeuralEngineAccelerator',
    'NEONAccelerator',

    # 跨平台 Vector API
    'VectorAPI', 'SIMDArch', 'VectorBackendInfo',
    'get_vector_api', 'detect_simd_arch',
    'CacheBlocker',

    # NUMA 优化
    'NUMATopology',
    'NUMAOptimizer',
    'get_numa_optimizer',
    'check_numa_status',

    # 缓存感知调度
    'CacheTopology',
    'CacheAwareScheduler',
    'get_cache_aware_scheduler',
    'check_cas_status',

    # 实时调度
    'SCHED_OTHER', 'SCHED_FIFO', 'SCHED_RR',
    'SCHED_BATCH', 'SCHED_IDLE', 'SCHED_DEADLINE',
    'MIN_RT_PRIO', 'MAX_RT_PRIO',
    'SchedParam', 'SchedInfo', 'SchedDeadlineAttr',
    'RealtimeScheduler', 'DeadlineScheduler', 'ThreadPriority',
    'set_deadline_scheduler', 'check_deadline_capability',
    'check_realtime_capability', 'print_realtime_status',
    'apply_chrt',
    'get_scheduler', 'set_scheduler',
    'get_priority', 'set_priority',
    'get_policy_name', 'get_cpu_affinity', 'set_cpu_affinity',

    # 电源管理
    'RAPLMonitor', 'DVFSController', 'ThermalMonitor',
    'HWPEnergyPerformanceHint', 'PowerManager',
    'get_power_manager', 'check_power_status',

    # 缓存分配 (CAT/ABMC)
    'CATDetector', 'CacheAllocator', 'CLOSConfig',
    'get_cache_allocator', 'check_cache_allocation_support',

    # CXL 优化
    'MemoryType', 'MemoryNode',
    'CXLMemoryDetector', 'AdaptiveScheduler',
    'HotDataMigrator', 'CXLOptimizer',

    # MKL 加速
    'MKLAccelerator', 'FMALAccelerator',
    'OptimizedMatrixOps', 'INT8QuantizedOps',
    'check_mkl_available', 'check_amx_available',
    'check_intel_cpu', 'print_mkl_status',
    'check_mkl_status', 'check_fmal_status',

    # I/O 优化
    'BlockDeviceScanner', 'IOOptimizer',
    'IODevice', 'IOOptimizationRecommendation',
    'get_io_optimizer', 'check_io_status',

    # 资源编排器
    'ResourceOrchestrator', 'Priority',
    'TaskProfile', 'DeploymentPlan', 'DeploymentPlanItem',
    'ResourceConstraint', 'create_orchestrator',

    # 模块协同与进程管理
    'ModuleCoordinator',
    'EventBus',
    'ProcessManager',
    'CrossLayerOrchestrator',
    'HealthDashboard',
    'PipelineBuilder',
    'PipelineExecutor',
    'PipelineSpec',
    'PipelineStage',
    'ScriptsCoreBridge',
    'WorkerSpec',
    'WorkerInfo',
    'ModuleLayer',
    'ProcessStatus',
    'EventType',
    'Event',
    'generic_worker',
    'create_coordinator',
    'create_worker_spec',

    # 初始化
    'initialize'
]
