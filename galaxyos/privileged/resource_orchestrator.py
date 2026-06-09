#!/usr/bin/env python3
"""
统一资源编排层 (Resource Orchestrator)

协调所有底层硬件/系统模块，提供全局最优的部署配置。

架构：
┌──────────────────────────────────────────────┐
│              ResourceOrchestrator             │
│                                               │
│  ┌─────────────┐ ┌──────────────────┐       │
│  │ GPUOptimizer │ │ NUMAOptimizer    │       │
│  │ → 显存预算   │ │ → 内存亲和性      │       │
│  ├─────────────┤ ├──────────────────┤       │
│  │ RealtimeSched│ │ CacheAllocator   │       │
│  │ → 核心隔离   │ │ → L3 Way 分配    │       │
│  ├─────────────┤ ├──────────────────┤       │
│  │ PowerManager │ │ DVFSController   │       │
│  │ → 功耗墙     │ │ → 频率/EPP       │       │
│  └─────────────┘ └──────────────────┘       │
│                                               │
│  输出: DeploymentPlan (全局最优配置)          │
└──────────────────────────────────────────────┘

核心能力：
- 全局资源发现与冲突检测
- 多维度约束求解（功耗 × 性能 × 温度 × 缓存）
- 自动化部署计划生成
- 运行时动态调整（基于反馈闭环）
"""

import os
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class Priority(Enum):
    """任务优先级"""
    CRITICAL = "critical"       # 关键路径 (推理请求)
    HIGH = "high"               # 高优先 (索引构建)
    NORMAL = "normal"           # 普通 (批量搜索)
    LOW = "low"                 # 低优 (后台维护)
    BACKGROUND = "background"   # 后台 (统计/日志)


@dataclass
class ResourceConstraint:
    """单个资源约束"""
    resource_type: str          # gpu_memory, numa_node, cpu_cores, cache_ways, power_w
    value: Any                  # 约束值
    operator: str = "="         # =, <=, >=, in
    hard: bool = True           # True=硬约束(必须满足), False=软约束(尽量满足)


@dataclass
class TaskProfile:
    """任务特征描述"""
    name: str
    priority: Priority
    constraints: List[ResourceConstraint] = field(default_factory=list)
    estimated_load: float = 1.0  # 相对负载 (0.0-1.0)
    memory_footprint_gb: float = 0.0
    cpu_intensive: bool = False
    io_intensive: bool = False
    latency_sensitive: bool = False


@dataclass
class DeploymentPlanItem:
    """部署计划的单项"""
    task_name: str
    assigned_resources: Dict[str, Any] = field(default_factory=dict)
    scheduling_config: Dict[str, Any] = field(default_factory=dict)
    cache_config: Optional[Dict[str, Any]] = None
    power_config: Optional[Dict[str, Any]] = None
    numa_config: Optional[Dict[str, Any]] = None
    priority_adjustment: int = 0


@dataclass
class DeploymentPlan:
    """完整部署计划"""
    items: List[DeploymentPlanItem] = field(default_factory=list)
    global_settings: Dict[str, Any] = field(default_factory=dict)
    conflicts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    score: float = 0.0  # 计划质量评分 (0-100)


class ResourceOrchestrator:
    """
    统一资源编排器

    协调 GPU、NUMA、实时调度、缓存分配、电源管理等子系统，
    生成全局最优的任务部署方案。
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._modules: Dict[str, Any] = {}
        self._discovered_resources: Dict[str, Any] = {}
        self._task_profiles: Dict[str, TaskProfile] = {}
        self._feedback_history: List[Dict] = []  # 反馈历史用于动态调整

        # 延迟加载各模块（按需初始化，避免启动开销）
        self._initialized = False

        logger.info("ResourceOrchestrator 初始化完成（延迟加载模式）")

    def initialize(self):
        """显式初始化所有模块"""
        if self._initialized:
            return
        self._initialize_modules()
        self._discover_resources()
        self._initialized = True
        logger.info(f"资源编排器完全初始化完成，"
                    f"发现 {len(self._discovered_resources)} 种资源")

    def _initialize_modules(self):
        """初始化各子系统模块"""
        # --- NUMA ---
        try:
            from numa_optimizer import NUMAOptimizer
            self._modules['numa'] = NUMAOptimizer(
                {'verbose': False, 'auto_bind': False})
        except Exception as e:
            logger.warning(f"NUMA 模块初始化失败: {e}")

        # --- GPU ---
        try:
            from gpu_optimizer import GPUOptimizer
            self._modules['gpu'] = GPUOptimizer({'verbose': False})
        except Exception as e:
            logger.warning(f"GPU 模块初始化失败: {e}")

        # --- 实时调度 ---
        try:
            from realtime_scheduler import (
                RealtimeScheduler, check_deadline_capability
            )
            self._modules['rt_sched'] = RealtimeScheduler()
            self._modules['deadline_cap'] = check_deadline_capability()
        except Exception as e:
            logger.warning(f"实时调度模块初始化失败: {e}")

        # --- 缓存分配 ---
        try:
            from cache_allocator import CacheAllocator
            self._modules['cache'] = CacheAllocator(auto_detect=True)
        except Exception as e:
            logger.warning(f"缓存分配模块初始化失败: {e}")

        # --- 缓存感知调度 ---
        try:
            from cache_aware_scheduler import CacheAwareScheduler, CacheTopology
            self._modules['cas'] = CacheAwareScheduler({'verbose': False})
            self._modules['cas_topology'] = CacheTopology()
        except Exception as e:
            logger.warning(f"CAS 模块初始化失败: {e}")

        # --- 电源管理 ---
        try:
            from power_manager import PowerManager
            self._modules['power'] = PowerManager({
                'verbose': False, 'auto_throttle': False,
                'start_monitor': False,
            })
        except Exception as e:
            logger.warning(f"电源管理模块初始化失败: {e}")

        # --- DVFS ---
        try:
            from power_manager import DVFSController
            self._modules['dvfs'] = DVFSController(0)
        except Exception as e:
            logger.warning(f"DVFS 模块初始化失败: {e}")

        # --- CXL ---
        try:
            from cxl_optimizer import CXLOptimizer
            self._modules['cxl'] = CXLOptimizer()
        except Exception as e:
            logger.warning(f"CXL 模块初始化失败: {e}")

        # --- 硬件优化 ---
        try:
            from hardware_optimize import HardwareOptimizer
            self._modules['hw_opt'] = HardwareOptimizer()
        except Exception as e:
            logger.warning(f"硬件优化模块初始化失败: {e}")

    def _discover_resources(self):
        """发现系统中所有可用的资源"""
        resources = {}

        # CPU 信息
        resources['cpu'] = {
            'cores': os.cpu_count() or 1,
            'arch': os.uname().machine,
        }

        # NUMA 信息
        if 'numa' in self._modules:
            topo = self._modules['numa'].topology.topology
            resources['numa'] = {
                'available': topo.get('numa_available', False),
                'nodes': topo.get('nodes', {}),
                'distances': topo.get('distances', []),
            }

        # GPU 信息
        if 'gpu' in self._modules:
            try:
                gpu_info = self._modules['gpu'].get_gpu_info()
                resources['gpu'] = gpu_info
            except Exception:
                resources['gpu'] = {'available': False}

        # 缓存拓扑
        if 'cas_topology' in self._modules:
            try:
                cas_topo = self._modules['cas_topology'].topology
                resources['cache'] = {
                    'l3_size_kb': cas_topo.get('l3', {}).get('size_kb', 0),
                    'domains': cas_topo.get('cache_domains', []),
                    'cas_supported': cas_topo.get('cas_supported', False),
                }
            except Exception:
                pass

        # CAT/ABMC
        if 'cache' in self._modules:
            resources['cache_allocation'] = {
                'available': self._modules['cache'].available,
                'cat_supported': self._modules['cache'].supports_cat,
                'abmc_supported': self._modules['cache'].supports_abmc,
                'mba_supported': self._modules['cache'].supports_mba,
                'ways': self._modules['cache'].cache_way_count,
            }

        # 电源/热
        if 'power' in self._modules:
            try:
                power_status = self._modules['power'].get_status()
                resources['power'] = {
                    'rapl_available': power_status['power'].get('rapl_available', False),
                    'thermal_zones': power_status['thermal'].get('thermal_zones', []),
                    'dvfs_cpus': power_status['frequency'].get('dvfs_cpus', []),
                    'epp_available': power_status['capabilities'].get('epp_available', False),
                }
            except Exception:
                pass

        # CXL
        if 'cxl' in self._modules:
            try:
                cxl_status = self._modules['cxl'].get_status()
                resources['cxl'] = cxl_status.get('detector', {})
            except Exception:
                pass

        # 硬件加速路径
        if 'hw_opt' in self._modules:
            try:
                hw_info = self._modules['hw_opt'].get_info()
                resources['hw_accel'] = {
                    'optimal_path': hw_info.get('optimal_path'),
                    'simd': hw_info.get('simd', []),
                    'vendor': hw_info.get('cpu_vendor'),
                }
            except Exception:
                pass

        # SCHED_DEADLINE
        if 'deadline_cap' in self._modules:
            resources['deadline'] = self._modules['deadline_cap']

        self._discovered_resources = resources

    def register_task(self, profile: TaskProfile):
        """注册任务特征"""
        self._task_profiles[profile.name] = profile

    def unregister_task(self, task_name: str):
        """取消注册任务"""
        self._task_profiles.pop(task_name, None)

    def generate_deployment_plan(self) -> DeploymentPlan:
        """
        生成全局最优部署计划。

        策略：
        1. CRITICAL 任务获得最优 NUMA 节点、最多 cache ways、最高 RT 优先级
        2. HIGH 任务获得次优资源
        3. NORMAL 任务共享剩余资源
        4. LOW/BACKGROUND 任务使用最低优先级
        5. 检测并解决资源冲突
        6. 应用电源约束
        """
        if not self._initialized:
            self.initialize()

        plan = DeploymentPlan()
        conflicts = []
        warnings_list = []

        # 按 priority 排序
        priority_order = {
            Priority.CRITICAL: 0, Priority.HIGH: 1,
            Priority.NORMAL: 2, Priority.LOW: 3,
            Priority.BACKGROUND: 4,
        }
        sorted_tasks = sorted(
            self._task_profiles.values(),
            key=lambda t: (priority_order.get(t.priority, 99), -t.estimated_load)
        )

        # 已分配资源追踪
        allocated_cpus: List[int] = []
        allocated_numa_nodes: List[str] = []
        _allocated_gpu_memory_mb: int = 0
        allocated_cache_ways: int = 0
        total_cache_ways = self._discovered_resources.get(
            'cache_allocation', {}).get('ways', 0)

        for task in sorted_tasks:
            item = DeploymentPlanItem(task_name=task.name)

            # ---- CPU 分配 ----
            available_cpus = self._discovered_resources.get(
                'cpu', {}).get('cores', 1)
            needed_cpus = max(1, int(task.estimated_load * available_cpus))
            free_cpus = [c for c in range(available_cpus)
                         if c not in allocated_cpus]

            if task.priority == Priority.CRITICAL and free_cpus:
                item.assigned_resources['cpus'] = \
                    free_cpus[:min(needed_cpus, len(free_cpus))]
                allocated_cpus.extend(item.assigned_resources['cpus'])
            elif free_cpus:
                item.assigned_resources['cpus'] = \
                    free_cpus[-min(needed_cpus, len(free_cpus)):][::-1]
                allocated_cpus.extend(item.assigned_resources['cpus'])
            else:
                item.assigned_resources['cpus'] = list(range(available_cpus))
                warnings_list.append(
                    f"{task.name}: CPU 资源不足，共享所有核")

            # ---- NUMA 分配 ----
            numa_res = self._discovered_resources.get('numa', {})
            if numa_res.get('available') and 'numa' in self._modules:
                optimal_node = self._modules['numa'].topology.get_optimal_node()
                if optimal_node not in allocated_numa_nodes or \
                   task.priority == Priority.CRITICAL:
                    item.numa_config = {
                        'node': optimal_node,
                        'bind_command': self._modules['numa'].get_binding_command(
                            optimal_node, ''),
                    }
                    if task.priority.value in ('critical', 'high'):
                        allocated_numa_nodes.append(optimal_node)
                else:
                    # 使用其他节点
                    nodes = list(numa_res.get('nodes', {}).keys())
                    other_nodes = [n for n in nodes
                                   if n not in allocated_numa_nodes]
                    if other_nodes:
                        alt_node = other_nodes[0]
                        item.numa_config = {
                            'node': alt_node,
                            'bind_command':
                                self._modules['numa'].get_binding_command(
                                    alt_node, ''),
                        }
                        allocated_numa_nodes.append(alt_node)

            # ---- 缓存分配 (CAT/ABMC) ----
            cache_res = self._discovered_resources.get('cache_allocation', {})
            if cache_res.get('available') and total_cache_ways > 0:
                remaining_ways = total_cache_ways - allocated_cache_ways

                if task.priority == Priority.CRITICAL:
                    ways_needed = min(total_cache_ways // 2, remaining_ways)
                elif task.priority == Priority.HIGH:
                    ways_needed = min(total_cache_ways // 4, remaining_ways)
                elif task.priority == Priority.NORMAL:
                    ways_needed = min(total_cache_ways // 8, max(1, remaining_ways))
                else:
                    ways_needed = 0  # 低优先级不需要专门分配

                if ways_needed > 0:
                    mask = hex((1 << ways_needed) - 1)
                    item.cache_config = {
                        'l3_mask': mask,
                        'ways_allocated': ways_needed,
                        'group_name': f'task_{task.name}',
                    }
                    allocated_cache_ways += ways_needed

            # ---- 实时调度配置 ----
            rt_configs = {
                Priority.CRITICAL: {
                    'policy': 'fifo' if self._can_use_fifo() else 'rr',
                    'priority': 85,
                    'use_deadline': self._can_use_deadline(),
                    'deadline_params': {'runtime_ms': 5, 'period_ms': 10},
                },
                Priority.HIGH: {
                    'policy': 'rr',
                    'priority': 60,
                    'use_deadline': False,
                },
                Priority.NORMAL: {
                    'policy': 'other',
                    'priority': 0,
                    'use_deadline': False,
                },
                Priority.LOW: {
                    'policy': 'idle',
                    'priority': 0,
                    'use_deadline': False,
                },
                Priority.BACKGROUND: {
                    'policy': 'idle',
                    'priority': 0,
                    'use_deadline': False,
                },
            }
            item.scheduling_config = rt_configs.get(task.priority, {})

            # ---- 电源配置 ----
            if task.latency_sensitive:
                item.power_config = {
                    'epp': 'performance',
                    'turbo': True,
                    'governor': 'performance',
                }
            elif task.priority == Priority.LOW or \
                    task.priority == Priority.BACKGROUND:
                item.power_config = {
                    'epp': 'power',
                    'governor': 'powersave',
                }

            plan.items.append(item)

        # ---- 冲突检测 ----
        total_requested_cpus = sum(
            len(i.assigned_resources.get('cpus', []))
            for i in plan.items)
        total_cpus = self._discovered_resources.get('cpu', {}).get('cores', 1)
        if total_requested_cpus > total_cpus:
            conflicts.append(
                f"CPU 过载: 请求 {total_requested_cpus} 核，可用 {total_cpus} 核")

        if allocated_cache_ways > total_cache_ways:
            conflicts.append(
                f"Cache overcommit: 分配 {allocated_cache_ways} ways > "
                f"可用 {total_cache_ways} ways")

        plan.conflicts = conflicts
        plan.warnings = warnings_list
        plan.score = self._score_plan(plan)

        return plan

    def _can_use_fifo(self) -> bool:
        """判断是否可以使用 FIFO 实时调度"""
        cap = self._discovered_resources.get('deadline', {})
        return cap.get('can_set_realtime', False) or os.geteuid() == 0

    def _can_use_deadline(self) -> bool:
        """判断是否可以使用 DEADLINE 调度"""
        cap = self._discovered_resources.get('deadline', {})
        return cap.get('supported', False)

    def _score_plan(self, plan: DeploymentPlan) -> float:
        """评估部署计划质量 (0-100)"""
        score = 100.0

        # 每个冲突扣 15 分
        score -= len(plan.conflicts) * 15

        # 每个警告扣 5 分
        score -= len(plan.warnings) * 5

        # CRITICAL 任务是否都有专用资源
        critical_items = [i for i in plan.items
                          if self._task_profiles.get(i.task_name)
                          and self._task_profiles[i.task_name].priority
                          == Priority.CRITICAL]
        for item in critical_items:
            if not item.assigned_resources.get('cpus'):
                score -= 20
            if not item.cache_config:
                score -= 10

        return max(0.0, min(100.0, score))

    def apply_plan(self, plan: DeploymentPlan, dry_run: bool = True) -> bool:
        """
        应用部署计划。

        Args:
            plan: 部署计划
            dry_run: 如果为 True 只打印命令而不实际执行

        Returns:
            bool: 是否全部应用成功
        """
        if dry_run:
            print("=" * 60)
            print("部署计划预览 (dry-run)")
            print("=" * 60)

        all_ok = True

        for item in plan.items:
            if dry_run:
                print(f"\n--- 任务: {item.task_name} ---")

            # 1. NUMA 绑定
            if item.numa_config:
                cmd = item.numa_config.get('bind_command', '')
                if dry_run:
                    print(f"  NUMA: {cmd}")
                else:
                    # 实际绑定需要在进程启动前执行
                    logger.info(f"[{item.task_name}] NUMA bind: {cmd}")

            # 2. 缓存分配
            if item.cache_config and 'cache' in self._modules:
                ca = self._modules['cache']
                group_name = item.cache_config.get('group_name', item.task_name)
                mask = item.cache_config.get('l3_mask', '')
                if dry_run:
                    print(f"  CACHE: group='{group_name}' L3={mask}")
                else:
                    result = ca.create_group(group_name, l3_mask=mask)
                    if not result.success:
                        logger.warning(f"缓存分配失败: {result.message}")
                        all_ok = False

            # 3. 调度策略
            sc = item.scheduling_config
            if sc and dry_run:
                policy = sc.get('policy', 'other')
                prio = sc.get('priority', 0)
                dl = sc.get('use_deadline', False)
                dl_params = sc.get('deadline_params', {})
                s = f"SCHED={policy.upper()} prio={prio}"
                if dl:
                    s += f" DL(runtime={dl_params.get('runtime_ms')}ms," \
                         f"period={dl_params.get('period_ms')}ms)"
                print(f"  RT: {s}")

            # 4. 电源配置
            pc = item.power_config
            if pc and dry_run:
                print(f"  POWER: epp={pc.get('epp')} "
                      f"gov={pc.get('governor')} "
                      f"turbo={pc.get('turbo')}")

        if dry_run:
            print(f"\n{'='*60}")
            print(f"计划评分: {plan.score}/100")
            if plan.conflicts:
                print(f"冲突: {len(plan.conflicts)}")
                for c in plan.conflicts:
                    print(f"  ⚠️ {c}")
            if plan.warnings:
                print(f"警告: {len(plan.warnings)}")
                for w in plan.warnings:
                    print(f"  ℹ️ {w}")
            print(f"{'='*60}")

        return all_ok

    def record_feedback(
        self,
        task_name: str,
        metric_name: str,
        value: float,
        tags: Optional[Dict] = None
    ):
        """记录运行时反馈（用于动态调整）"""
        entry = {
            'timestamp': time.time(),
            'task': task_name,
            'metric': metric_name,
            'value': value,
            'tags': tags or {},
        }
        self._feedback_history.append(entry)

        # 保持最近 1000 条记录
        if len(self._feedback_history) > 1000:
            self._feedback_history = self._feedback_history[-1000:]

    def suggest_rebalancing(self) -> List[Dict[str, Any]]:
        """
        基于反馈历史给出重均衡建议。

        分析最近 N 分钟的数据，检测热点和不均衡。
        """
        suggestions = []

        # 按任务分组聚合指标
        from collections import defaultdict
        metrics_by_task: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list))

        cutoff = time.time() - 300  # 最近5分钟
        recent = [e for e in self._feedback_history if e['timestamp'] > cutoff]

        for entry in recent:
            metrics_by_task[entry['task']][entry['metric']].append(entry['value'])

        for task_name, metrics in metrics_by_task.items():
            # 检测延迟异常
            latencies = metrics.get('latency_ms', [])
            if latencies:
                avg_lat = sum(latencies) / len(latencies)
                max_lat = max(latencies)
                if avg_lat > 100:  # ms
                    suggestions.append({
                        'task': task_name,
                        'type': 'latency_warning',
                        'message': f"平均延迟 {avg_lat:.1f}ms "
                        f"(峰值 {max_lat:.1f}ms)",
                        'action': 'consider_upgrading_priority_or_add_cache',
                    })

            # 检测 CPU 利用率不均
            cpu_utils = metrics.get('cpu_utilization', [])
            if cpu_utils:
                avg_cpu = sum(cpu_utils) / len(cpu_utils)
                if avg_cpu > 90:
                    suggestions.append({
                        'task': task_name,
                        'type': 'cpu_saturation',
                        'message': f"CPU 平均利用率 {avg_cpu:.1f}%",
                        'action': 'add_more_cpus_or_reduce_load',
                    })

        return suggestions

    def get_resource_summary(self) -> Dict[str, Any]:
        """获取资源总览"""
        if not self._initialized:
            self.initialize()
        return {
            **self._discovered_resources,
            'registered_tasks': list(self._task_profiles.keys()),
            'module_status': {
                name: 'loaded' for name in self._modules.keys()
            },
        }


def create_orchestrator(config: Optional[Dict] = None) -> ResourceOrchestrator:
    """工厂函数"""
    return ResourceOrchestrator(config)


if __name__ == "__main__":
    print("=== 资源编排器演示 ===\n")

    orch = ResourceOrchestrator()

    # 注册示例任务
    orch.register_task(TaskProfile(
        name="vector_search_inference",
        priority=Priority.CRITICAL,
        estimated_load=0.4,
        latency_sensitive=True,
        cpu_intensive=True,
    ))

    orch.register_task(TaskProfile(
        name="embedding_index_build",
        priority=Priority.HIGH,
        estimated_load=0.7,
        cpu_intensive=True,
        memory_footprint_gb=8.0,
    ))

    orch.register_task(TaskProfile(
        name="batch_similarity",
        priority=Priority.NORMAL,
        estimated_load=0.3,
        io_intensive=True,
    ))

    orch.register_task(TaskProfile(
        name="stats_collection",
        priority=Priority.LOW,
        estimated_load=0.05,
    ))

    # 生成部署计划
    plan = orch.generate_deployment_plan()
    orch.apply_plan(dry_run=True)

    # 资源总览
    summary = orch.get_resource_summary()
    print(f"\n发现的资源类型: {list(summary.keys())}")
