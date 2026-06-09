#!/usr/bin/env python3
"""
模块协同与进程管理器 (Module Coordinator)

解决核心问题：
1. 各模块独立运行，利用率低，无法并行协作
2. 硬件/LLM/RAG 三层割裂，互不感知
3. 无进程级隔离，一个模块崩溃影响全局
4. scripts_core/ 和顶层模块重复实现，无法共享基础设施

架构：
┌────────────────────────────────────────────────────────────┐
│                    ModuleCoordinator                        │
│                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  EventBus    │  │  ProcessPool │  │ HealthDashboard│   │
│  │  事件总线    │  │  进程池管理  │  │  健康仪表盘    │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                  │             │
│  ┌──────┴─────────────────┴──────────────────┴──────┐     │
│  │           CrossLayerOrchestrator                 │     │
│  │     硬件层 ↔ LLM层 ↔ RAG层 联动编排             │     │
│  └─────────────────────────────────────────────────┘     │
│                                                            │
│  ┌──────────────────────────────────────────────────┐     │
│  │           PipelineBuilder                         │     │
│  │     声明式多模块流水线编排                         │     │
│  └──────────────────────────────────────────────────┘     │
│                                                            │
│  ┌──────────────────────────────────────────────────┐     │
│  │           ScriptsCoreBridge                       │     │
│  │     scripts_core/ 复用顶层基础设施               │     │
│  └──────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────┘

进程模型：
- Worker 进程: 独立运行重型计算 (向量搜索、LLM 推理、RAG 管线)
- 主进程: 轻量调度 + 事件路由 + 健康监控
- 进程间通信: multiprocessing.Queue + 共享内存 (可选)
- 故障隔离: Worker 崩溃不影响主进程和其他 Worker

性能预期：
- 多核利用率: 1核 → N核 (N = CPU 核数)
- 模块利用率: 各模块并行执行而非串行等待
- 故障恢复: <1s 自动重启崩溃的 Worker
- 事件延迟: <1ms (进程内) / <10ms (跨进程)
"""

import os
import time
import logging
import multiprocessing
import queue as _stdlib_queue  # 标准库 queue，避免与 multiprocessing.Queue 冲突
from multiprocessing import Process, Queue, Value, Array, shared_memory
from typing import Dict, List, Optional, Any, Callable, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor, Future, as_completed
import threading
import asyncio
import traceback
import signal
import json
import pickle
import weakref

logger = logging.getLogger(__name__)


# ==================== 枚举与数据类 ====================

class ModuleLayer(Enum):
    """模块所属层级"""
    HARDWARE = "hardware"       # 硬件优化层 (NUMA, GPU, cache, power)
    LLM = "llm"                 # LLM 层 (client, streaming, speculative)
    RAG = "rag"                 # RAG 层 (CRAG, hybrid search, proposition)
    SEARCH = "search"           # 搜索层 (vector, sparse, multimodal)
    CACHE = "cache"             # 缓存层 (semantic, unified, approximate)
    INFRASTRUCTURE = "infra"    # 基础设施层 (registry, failover, sandbox)


class ProcessStatus(Enum):
    """进程状态"""
    IDLE = "idle"
    RUNNING = "running"
    BUSY = "busy"
    CRASHED = "crashed"
    STOPPED = "stopped"


class EventType(Enum):
    """事件类型"""
    # 缓存事件
    CACHE_EVICTION = "cache.eviction"
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"

    # LLM 事件
    LLM_REQUEST_START = "llm.request.start"
    LLM_REQUEST_END = "llm.request.end"
    LLM_OVERLOAD = "llm.overload"
    LLM_ERROR = "llm.error"

    # RAG 事件
    RAG_QUERY_START = "rag.query.start"
    RAG_QUERY_END = "rag.query.end"
    RAG_RETRIEVAL_DONE = "rag.retrieval.done"
    RAG_GENERATION_DONE = "rag.generation.done"

    # 硬件事件
    HARDWARE_LATENCY_SPIKE = "hardware.latency.spike"
    HARDWARE_RESOURCE_CHANGE = "hardware.resource.change"
    HARDWARE_THERMAL = "hardware.thermal"

    # 进程事件
    PROCESS_STARTED = "process.started"
    PROCESS_CRASHED = "process.crashed"
    PROCESS_RESTARTED = "process.restarted"
    PROCESS_STOPPED = "process.stopped"

    # 跨层联动事件
    CROSS_LAYER_ADJUST = "cross_layer.adjust"
    PIPELINE_STAGE_DONE = "pipeline.stage.done"
    PIPELINE_COMPLETE = "pipeline.complete"

    # 自定义
    CUSTOM = "custom"


@dataclass
class Event:
    """事件对象"""
    event_type: EventType
    source: str                   # 发送模块名
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    priority: int = 0             # 0=普通, 1=高, 2=紧急

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class WorkerSpec:
    """Worker 进程规格"""
    name: str
    module_name: str             # 对应的模块名
    layer: ModuleLayer
    target: Callable             # Worker 入口函数
    args: Tuple = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    cpu_affinity: Optional[List[int]] = None
    max_restarts: int = 3        # 最大自动重启次数
    restart_delay: float = 1.0   # 重启延迟 (秒)
    memory_limit_mb: int = 0     # 内存限制 (0=不限)
    priority: int = 0            # 调度优先级


@dataclass
class WorkerInfo:
    """Worker 运行时信息"""
    spec: WorkerSpec
    pid: int = 0
    status: ProcessStatus = ProcessStatus.IDLE
    start_time: float = 0.0
    restart_count: int = 0
    last_heartbeat: float = 0.0
    task_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None


@dataclass
class PipelineStage:
    """流水线阶段"""
    name: str
    module_name: str
    process_mode: str = "auto"   # "auto", "process", "thread", "inline"
    timeout: float = 30.0
    retry: int = 0
    retry_delay: float = 1.0
    fallback: Optional[str] = None  # 失败时的备选模块


@dataclass
class PipelineSpec:
    """流水线规格"""
    name: str
    stages: List[PipelineStage]
    parallel_stages: List[List[int]] = field(default_factory=list)  # 可并行的阶段索引组


# ==================== 事件总线 ====================

class EventBus:
    """
    进程内事件总线 (发布/订阅)

    支持同步和异步回调。跨进程事件通过 ProcessManager 转发。

    用法：
        bus = EventBus()
        bus.subscribe("cache.eviction", on_cache_evict)
        bus.publish(Event(EventType.CACHE_EVICTION, "semantic_cache", {"key": "xxx"}))
    """

    _MAX_SUBSCRIBERS_PER_TYPE = 256
    _ASYNC_POOL_SIZE = 4

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        # 通配符订阅: prefix -> [callbacks]，如 "llm" -> [cb1, cb2]
        self._wildcard_subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._lock = threading.Lock()
        self._event_count = 0
        self._error_count = 0
        # 异步发布线程池，避免每次创建新线程
        self._async_pool: Optional[List[threading.Thread]] = None
        self._async_queue: _stdlib_queue.SimpleQueue = _stdlib_queue.SimpleQueue()
        self._async_running = False

    def _ensure_async_pool(self):
        """懒启动异步发布线程池"""
        if self._async_pool is not None:
            return
        self._async_running = True
        self._async_pool = []
        for i in range(self._ASYNC_POOL_SIZE):
            t = threading.Thread(target=self._async_worker, daemon=True, name=f"eventbus-async-{i}")
            t.start()
            self._async_pool.append(t)

    def _async_worker(self):
        """异步发布工作线程"""
        while self._async_running:
            try:
                event = self._async_queue.get(timeout=1.0)
                if event is None:
                    break
                self.publish(event)
            except Exception:
                continue

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """
        订阅事件

        Args:
            event_type: 事件类型名称 (如 "cache.eviction", "llm.*")
            callback: 回调函数 callback(event: Event)
        """
        with self._lock:
            if event_type.endswith(".*"):
                prefix = event_type[:-2]  # "llm.*" -> "llm"
                subs = self._wildcard_subscribers[prefix]
                if len(subs) < self._MAX_SUBSCRIBERS_PER_TYPE:
                    subs.append(callback)
            else:
                subs = self._subscribers[event_type]
                if len(subs) < self._MAX_SUBSCRIBERS_PER_TYPE:
                    subs.append(callback)
        logger.debug(f"事件订阅: {event_type} -> {callback.__name__}")

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """取消订阅"""
        with self._lock:
            if event_type.endswith(".*"):
                prefix = event_type[:-2]
                if prefix in self._wildcard_subscribers:
                    subs = self._wildcard_subscribers[prefix]
                    if callback in subs:
                        subs.remove(callback)
                    if not subs:
                        del self._wildcard_subscribers[prefix]
            elif event_type in self._subscribers:
                subs = self._subscribers[event_type]
                if callback in subs:
                    subs.remove(callback)
                if not subs:
                    del self._subscribers[event_type]

    def publish(self, event: Event) -> int:
        """
        发布事件

        Args:
            event: 事件对象

        Returns:
            int: 收到事件的订阅者数量
        """
        delivered = 0
        type_name = event.event_type.value

        with self._lock:
            # 精确匹配
            subscribers = list(self._subscribers.get(type_name, []))
            # 通配符匹配：只匹配对应前缀
            prefix = type_name.split(".")[0] if "." in type_name else type_name
            wildcard = list(self._wildcard_subscribers.get(prefix, []))

        all_subscribers = subscribers + wildcard

        for callback in all_subscribers:
            try:
                callback(event)
                delivered += 1
            except Exception as e:
                self._error_count += 1
                logger.error(f"事件回调异常: {type_name} -> {callback.__name__}: {e}")

        self._event_count += 1
        return delivered

    def publish_async(self, event: Event) -> None:
        """异步发布事件 (不阻塞调用者，使用线程池)"""
        self._ensure_async_pool()
        self._async_queue.put(event)

    def shutdown(self):
        """关闭异步线程池"""
        self._async_running = False
        if self._async_pool:
            for _ in self._async_pool:
                self._async_queue.put(None)
            for t in self._async_pool:
                t.join(timeout=2.0)
            self._async_pool = None

    def get_stats(self) -> Dict[str, Any]:
        """获取统计"""
        with self._lock:
            total_exact = sum(len(v) for v in self._subscribers.values())
            total_wildcard = sum(len(v) for v in self._wildcard_subscribers.values())
        return {
            "total_events": self._event_count,
            "total_subscribers": total_exact + total_wildcard,
            "event_types": list(self._subscribers.keys()),
            "wildcard_prefixes": list(self._wildcard_subscribers.keys()),
            "callback_errors": self._error_count,
        }


# ==================== 进程池管理 ====================

class ProcessManager:
    """
    进程池管理器

    管理多个 Worker 进程的生命周期:
    - 启动/停止/重启 Worker
    - 健康监控与自动重启
    - 任务分发与结果收集
    - CPU 亲和性绑定
    - 跨进程事件转发

    用法:
        pm = ProcessManager()
        pm.register_worker(WorkerSpec(
            name="vector_search",
            module_name="vector_api",
            layer=ModuleLayer.SEARCH,
            target=vector_search_worker,
        ))
        pm.start_all()
        result = pm.submit("vector_search", search_func, query_vector)
        pm.stop_all()
    """

    _TASK_QUEUE_MAXSIZE = 1024
    _RESULT_QUEUE_MAXSIZE = 1024
    _EVENT_QUEUE_MAXSIZE = 2048
    _RESULT_POLL_TIMEOUT = 30.0
    _MAX_ADJUSTMENTS = 200
    _COMPLETED_FUTURES_CLEANUP_INTERVAL = 100

    def __init__(self, max_workers: int = 0):
        """
        Args:
            max_workers: 最大 Worker 数 (0=自动, =CPU 核数)
        """
        self.max_workers = max_workers or os.cpu_count() or 4
        self._workers: Dict[str, WorkerInfo] = {}
        self._processes: Dict[str, Process] = {}
        self._executor: Optional[ProcessPoolExecutor] = None
        self._task_queue: Queue = Queue(maxsize=self._TASK_QUEUE_MAXSIZE)
        self._result_queue: Queue = Queue(maxsize=self._RESULT_QUEUE_MAXSIZE)
        self._event_bridge_queue: Queue = Queue(maxsize=self._EVENT_QUEUE_MAXSIZE)
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._futures: Dict[str, Future] = {}
        self._futures_counter = 0
        # 结果缓存：task_id -> result，避免从 queue 中误取他人结果后丢失
        self._result_cache: Dict[str, Any] = {}
        self._result_cache_lock = threading.Lock()

    def register_worker(self, spec: WorkerSpec) -> None:
        """注册 Worker 规格"""
        with self._lock:
            self._workers[spec.name] = WorkerInfo(spec=spec)

    def start_worker(self, name: str) -> bool:
        """启动指定 Worker"""
        with self._lock:
            if name not in self._workers:
                logger.error(f"Worker 未注册: {name}")
                return False

            info = self._workers[name]
            if info.status == ProcessStatus.RUNNING:
                return True

            spec = info.spec
            try:
                p = Process(
                    target=self._worker_wrapper,
                    args=(spec.name, spec.target, spec.args, spec.kwargs,
                          self._task_queue, self._result_queue, self._event_bridge_queue,
                          spec.memory_limit_mb),
                    name=f"worker-{spec.name}",
                    daemon=True,
                )
                p.start()

                info.pid = p.pid
                info.status = ProcessStatus.RUNNING
                info.start_time = time.time()
                info.last_heartbeat = time.time()
                self._processes[name] = p

                # CPU 亲和性
                if spec.cpu_affinity:
                    self._set_cpu_affinity(p.pid, spec.cpu_affinity)

                logger.info(f"Worker 启动: {name} (PID={p.pid})")
                return True

            except Exception as e:
                info.status = ProcessStatus.CRASHED
                info.last_error = str(e)
                logger.error(f"Worker 启动失败: {name}: {e}")
                return False

    def start_all(self) -> Dict[str, bool]:
        """启动所有已注册的 Worker"""
        results = {}
        # 按优先级排序启动
        sorted_names = sorted(
            self._workers.keys(),
            key=lambda n: self._workers[n].spec.priority,
            reverse=True
        )
        for name in sorted_names:
            results[name] = self.start_worker(name)
        self._start_monitor()
        return results

    def stop_worker(self, name: str, timeout: float = 5.0) -> bool:
        """停止指定 Worker"""
        with self._lock:
            if name not in self._processes:
                return True

            p = self._processes[name]
            info = self._workers.get(name)

        try:
            p.terminate()
            p.join(timeout=timeout)
            if p.is_alive():
                p.kill()
                p.join(1.0)

            with self._lock:
                if info:
                    info.status = ProcessStatus.STOPPED
                self._processes.pop(name, None)

            logger.info(f"Worker 停止: {name}")
            return True
        except Exception as e:
            logger.error(f"Worker 停止失败: {name}: {e}")
            return False

    def stop_all(self, timeout: float = 5.0) -> None:
        """停止所有 Worker"""
        self._running = False
        for name in list(self._processes.keys()):
            self.stop_worker(name, timeout)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
        # 排空队列，防止子进程阻塞在 put 上
        self._drain_queue(self._task_queue)
        self._drain_queue(self._result_queue)
        self._drain_queue(self._event_bridge_queue)
        # 清理 Future 缓存
        self._futures.clear()
        with self._result_cache_lock:
            self._result_cache.clear()

    @staticmethod
    def _drain_queue(q: Queue, timeout: float = 0.5):
        """排空队列，防止子进程因满队列而阻塞"""
        while True:
            try:
                q.get(timeout=timeout)
            except Exception:
                break

    def submit(self, worker_name: str, func: Callable, *args, **kwargs) -> Optional[Any]:
        """
        向 Worker 提交任务

        通过 task_queue 发送任务，等待 result_queue 返回结果。
        使用结果缓存避免误取他人结果后丢失。
        """
        task_id = f"{worker_name}_{int(time.time() * 1e6)}"
        task = {
            "task_id": task_id,
            "worker_name": worker_name,
            "func": func,
            "args": args,
            "kwargs": kwargs,
        }

        try:
            self._task_queue.put(pickle.dumps(task), timeout=5.0)
        except Exception as e:
            logger.error(f"任务提交失败: {worker_name}: {e}")
            return None

        # 等待结果，使用结果缓存避免丢失非本任务的结果
        deadline = time.time() + self._RESULT_POLL_TIMEOUT
        while time.time() < deadline:
            # 先检查结果缓存
            with self._result_cache_lock:
                if task_id in self._result_cache:
                    result = self._result_cache.pop(task_id)
                    if result.get("error"):
                        raise RuntimeError(result["error"])
                    return result.get("data")

            # 从 result_queue 取结果
            try:
                result_data = self._result_queue.get(timeout=1.0)
                result = pickle.loads(result_data)
                rid = result.get("task_id")
                if rid == task_id:
                    if result.get("error"):
                        raise RuntimeError(result["error"])
                    return result.get("data")
                else:
                    # 不是我们的结果，缓存起来
                    with self._result_cache_lock:
                        self._result_cache[rid] = result
                        # 防止缓存无限增长
                        if len(self._result_cache) > 256:
                            oldest_key = next(iter(self._result_cache))
                            del self._result_cache[oldest_key]
            except Exception:
                # 检查是否超时
                if time.time() >= deadline:
                    break
                continue

        logger.error(f"任务超时: {task_id}")
        return None

    def submit_to_executor(self, func: Callable, *args, **kwargs) -> Future:
        """
        使用 ProcessPoolExecutor 提交一次性任务

        适合不需要长驻进程的并行计算。
        """
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
        future = self._executor.submit(func, *args, **kwargs)
        fid = f"exec_{self._futures_counter}"
        self._futures_counter += 1
        self._futures[fid] = future
        # 定期清理已完成的 Future
        if self._futures_counter % self._COMPLETED_FUTURES_CLEANUP_INTERVAL == 0:
            self._cleanup_futures()
        return future

    def _cleanup_futures(self):
        """清理已完成的 Future，防止内存泄漏"""
        done_keys = [k for k, f in self._futures.items() if f.done()]
        for k in done_keys:
            # 确保异常被消费，避免 "exception was never retrieved" 警告
            try:
                self._futures[k].result(timeout=0)
            except Exception:
                pass
            del self._futures[k]

    def submit_batch(self, func: Callable, items: List[Any]) -> List[Any]:
        """
        批量并行执行

        Args:
            func: 处理函数
            items: 输入列表

        Returns:
            结果列表 (顺序与输入一致)
        """
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)

        futures = {self._executor.submit(func, item): i for i, item in enumerate(items)}
        results = [None] * len(items)

        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = e
                logger.error(f"批量任务 [{idx}] 失败: {e}")

        return results

    def get_worker_info(self, name: str) -> Optional[WorkerInfo]:
        """获取 Worker 信息"""
        return self._workers.get(name)

    def get_all_worker_info(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Worker 信息"""
        return {
            name: {
                "pid": info.pid,
                "status": info.status.value,
                "layer": info.spec.layer.value,
                "module": info.spec.module_name,
                "start_time": info.start_time,
                "restart_count": info.restart_count,
                "task_count": info.task_count,
                "error_count": info.error_count,
                "last_error": info.last_error,
                "uptime_s": round(time.time() - info.start_time, 1) if info.start_time else 0,
            }
            for name, info in self._workers.items()
        }

    def drain_events(self, event_bus: EventBus, max_drain: int = 100) -> int:
        """
        从跨进程事件队列中取出事件并发布到事件总线

        Returns:
            int: 处理的事件数量
        """
        count = 0
        while count < max_drain:
            try:
                event_data = self._event_bridge_queue.get_nowait()
                event = pickle.loads(event_data)
                event_bus.publish(event)
                count += 1
            except Exception:
                break
        return count

    # ---- 内部方法 ----

    @staticmethod
    def _worker_wrapper(name, target, args, kwargs, task_q, result_q, event_q,
                         memory_limit_mb=0):
        """Worker 进程入口包装"""
        # 忽略 SIGINT，由主进程控制退出
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # 内存限制 (Linux only)
        if memory_limit_mb > 0:
            try:
                import resource
                soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                limit_bytes = memory_limit_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
            except (ImportError, ValueError, OSError):
                pass

        try:
            # 调用用户的 Worker 入口
            target(name, task_q, result_q, event_q, *args, **kwargs)
        except Exception as e:
            error_event = Event(
                event_type=EventType.PROCESS_CRASHED,
                source=name,
                data={"error": str(e), "traceback": traceback.format_exc()},
                priority=2,
            )
            try:
                event_q.put(pickle.dumps(error_event), timeout=2.0)
            except Exception:
                pass

    @staticmethod
    def _set_cpu_affinity(pid: int, cpus: List[int]) -> bool:
        """设置进程 CPU 亲和性"""
        try:
            os.sched_setaffinity(pid, set(cpus))
            return True
        except (OSError, AttributeError):
            return False

    def _start_monitor(self):
        """启动健康监控线程"""
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        """监控循环：检测崩溃并自动重启"""
        while self._running:
            try:
                to_restart = []
                for name, p in list(self._processes.items()):
                    if not p.is_alive():
                        info = self._workers.get(name)
                        if info and info.status == ProcessStatus.RUNNING:
                            info.status = ProcessStatus.CRASHED
                            info.error_count += 1
                            info.last_error = "Process exited unexpectedly"

                            logger.warning(f"Worker 崩溃: {name} (exitcode={p.exitcode})")

                            # 检查是否可以重启
                            if info.restart_count < info.spec.max_restarts:
                                to_restart.append(name)
                            else:
                                logger.error(
                                    f"Worker 重启次数已达上限: {name} "
                                    f"({info.spec.max_restarts}次)"
                                )

                # 在锁外执行重启，避免死锁
                for name in to_restart:
                    info = self._workers.get(name)
                    if info:
                        time.sleep(info.spec.restart_delay)
                        with self._lock:
                            self._processes.pop(name, None)
                        info.restart_count += 1
                        self.start_worker(name)
                        if info.status == ProcessStatus.RUNNING:
                            logger.info(f"Worker 重启成功: {name} (第{info.restart_count}次)")

                time.sleep(2.0)
            except Exception as e:
                logger.error(f"监控异常: {e}")
                time.sleep(5.0)


# ==================== 跨层编排器 ====================

class CrossLayerOrchestrator:
    """
    跨层编排器

    打通硬件/LLM/RAG 三层，让它们能互相感知和联动:

    1. 硬件 → LLM: GPU 显存不足时，自动切换到小模型或 KV 量化
    2. LLM → RAG: 推理延迟飙升时，降低 RAG 检索精度换取速度
    3. RAG → 硬件: 向量搜索规模变化时，调整缓存分配和 NUMA 策略
    4. 缓存 → 全局: 缓存淘汰事件触发资源重新分配

    联动规则示例:
        - IF hardware.latency.spike AND llm.overload → 启用 KV 量化 + 降低 batch
        - IF cache.eviction AND rag.query.start → 预热缓存 + 扩大缓存配额
        - IF rag.retrieval.done AND hardware.thermal → 降低搜索精度 + 降频
    """

    def __init__(self, event_bus: EventBus, process_manager: ProcessManager):
        self.event_bus = event_bus
        self.pm = process_manager
        self._rules: List[Dict[str, Any]] = []
        self._adjustments: deque = deque(maxlen=200)  # 有界队列，防止内存泄漏
        self._lock = threading.Lock()

        # 订阅关键事件
        self._subscribe_events()

    def _subscribe_events(self):
        """订阅跨层联动所需的事件"""
        self.event_bus.subscribe(EventType.HARDWARE_LATENCY_SPIKE.value, self._on_hw_latency)
        self.event_bus.subscribe(EventType.LLM_OVERLOAD.value, self._on_llm_overload)
        self.event_bus.subscribe(EventType.CACHE_EVICTION.value, self._on_cache_eviction)
        self.event_bus.subscribe(EventType.RAG_QUERY_START.value, self._on_rag_query_start)
        self.event_bus.subscribe(EventType.HARDWARE_THERMAL.value, self._on_hw_thermal)

    def register_rule(self, rule: Dict[str, Any]) -> None:
        """
        注册联动规则

        Args:
            rule: {
                "name": str,
                "trigger": EventType,         # 触发事件
                "conditions": List[Dict],      # 附加条件
                "actions": List[Callable],     # 执行动作
                "cooldown": float,             # 冷却时间 (秒)
            }
        """
        rule.setdefault("cooldown", 30.0)
        rule.setdefault("last_triggered", 0.0)
        with self._lock:
            self._rules.append(rule)

    def _on_hw_latency(self, event: Event):
        """硬件延迟飙升 → 通知 LLM/RAG 降级"""
        self._check_and_trigger("hardware_latency", event)

    def _on_llm_overload(self, event: Event):
        """LLM 过载 → 启用 KV 量化 + RAG 降级"""
        self._check_and_trigger("llm_overload", event)

    def _on_cache_eviction(self, event: Event):
        """缓存淘汰 → RAG 查询前预热 + 扩大配额"""
        self._check_and_trigger("cache_eviction", event)

    def _on_rag_query_start(self, event: Event):
        """RAG 查询开始 → 调整硬件资源分配"""
        self._check_and_trigger("rag_query", event)

    def _on_hw_thermal(self, event: Event):
        """硬件过热 → 降低计算强度"""
        self._check_and_trigger("hardware_thermal", event)

    def _check_and_trigger(self, trigger_name: str, event: Event):
        """检查并触发联动规则"""
        now = time.time()
        actions_to_run = []

        with self._lock:
            for rule in self._rules:
                if rule.get("trigger_name") != trigger_name:
                    continue
                # 冷却检查
                if now - rule["last_triggered"] < rule["cooldown"]:
                    continue
                # 条件检查
                conditions = rule.get("conditions", [])
                if not self._check_conditions(conditions, event):
                    continue
                # 标记触发时间
                rule["last_triggered"] = now
                # 收集要执行的 action，稍后在锁外执行
                actions_to_run.append((rule, list(rule.get("actions", []))))

        # 在锁外执行 action，避免持锁时间过长或死锁
        for rule, actions in actions_to_run:
            for action in actions:
                try:
                    action(event)
                except Exception as e:
                    logger.error(f"联动规则执行失败: {rule['name']}: {e}")

            # 发布跨层调整事件
            self.event_bus.publish(Event(
                event_type=EventType.CROSS_LAYER_ADJUST,
                source="cross_layer_orchestrator",
                data={
                    "rule": rule["name"],
                    "trigger": trigger_name,
                    "original_event": event.data,
                },
            ))

            with self._lock:
                self._adjustments.append({
                    "rule": rule["name"],
                    "trigger": trigger_name,
                    "timestamp": now,
                })

    def _check_conditions(self, conditions: List[Dict], event: Event) -> bool:
        """检查附加条件"""
        for cond in conditions:
            field_path = cond.get("field", "")
            op = cond.get("op", "==")
            value = cond.get("value")

            # 从事件 data 中获取值
            actual = event.data
            for part in field_path.split("."):
                if isinstance(actual, dict):
                    actual = actual.get(part)
                else:
                    actual = None
                    break

            if op == "==" and actual != value:
                return False
            if op == ">" and not (actual is not None and actual > value):
                return False
            if op == "<" and not (actual is not None and actual < value):
                return False
            if op == ">=" and not (actual is not None and actual >= value):
                return False
            if op == "<=" and not (actual is not None and actual <= value):
                return False

        return True

    def get_adjustments(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的联动调整记录"""
        with self._lock:
            return self._adjustments[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """获取编排统计"""
        with self._lock:
            return {
                "total_rules": len(self._rules),
                "total_adjustments": len(self._adjustments),
                "rules": [
                    {"name": r["name"], "trigger": r.get("trigger_name", "?"), "cooldown": r["cooldown"]}
                    for r in self._rules
                ],
            }


# ==================== 健康仪表盘 ====================

class HealthDashboard:
    """
    全局健康仪表盘

    聚合各模块的健康状态，提供全局视角:
    - 进程状态
    - 事件流量
    - 错误率
    - 资源利用率
    - 联动调整历史
    """

    _MAX_MODULE_HEALTH_ENTRIES = 128

    def __init__(self, event_bus: EventBus, process_manager: ProcessManager,
                 orchestrator: CrossLayerOrchestrator):
        self.event_bus = event_bus
        self.pm = process_manager
        self.orchestrator = orchestrator
        self._module_health: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        # 订阅所有事件用于统计
        self.event_bus.subscribe(".*", self._on_any_event)

    def _on_any_event(self, event: Event):
        """记录所有事件用于健康统计"""
        with self._lock:
            source = event.source
            if source not in self._module_health:
                # 防止无限增长：超过上限时清理最旧的条目
                if len(self._module_health) >= self._MAX_MODULE_HEALTH_ENTRIES:
                    oldest = min(self._module_health, key=lambda k: self._module_health[k].get("last_event_time", 0))
                    del self._module_health[oldest]
                self._module_health[source] = {
                    "event_count": 0,
                    "error_count": 0,
                    "last_event_time": 0.0,
                    "last_event_type": "",
                    "status": "unknown",
                }
            health = self._module_health[source]
            health["event_count"] += 1
            health["last_event_time"] = event.timestamp
            health["last_event_type"] = event.event_type.value

            if event.event_type in (EventType.PROCESS_CRASHED, EventType.LLM_ERROR):
                health["error_count"] += 1
                health["status"] = "degraded"
            elif event.event_type == EventType.PROCESS_STARTED:
                health["status"] = "healthy"
            elif event.event_type == EventType.PROCESS_RESTARTED:
                health["status"] = "recovering"

    def update_module_health(self, module_name: str, status: str, metrics: Dict[str, Any] = None):
        """
        手动更新模块健康状态

        Args:
            module_name: 模块名
            status: "healthy", "degraded", "unhealthy", "unknown"
            metrics: 附加指标
        """
        with self._lock:
            if module_name not in self._module_health:
                self._module_health[module_name] = {}
            self._module_health[module_name].update({
                "status": status,
                "last_update": time.time(),
                **(metrics or {}),
            })

    def get_overview(self) -> Dict[str, Any]:
        """获取全局健康概览"""
        workers = self.pm.get_all_worker_info()
        event_stats = self.event_bus.get_stats()
        orch_stats = self.orchestrator.get_stats()

        healthy_count = 0
        degraded_count = 0
        unhealthy_count = 0

        with self._lock:
            for health in self._module_health.values():
                s = health.get("status", "unknown")
                if s == "healthy":
                    healthy_count += 1
                elif s in ("degraded", "recovering"):
                    degraded_count += 1
                elif s == "unhealthy":
                    unhealthy_count += 1

        running_workers = sum(1 for w in workers.values() if w["status"] == "running")
        total_workers = len(workers)

        return {
            "overall_status": "healthy" if degraded_count == 0 and unhealthy_count == 0
                              else "degraded" if unhealthy_count == 0
                              else "unhealthy",
            "workers": {
                "total": total_workers,
                "running": running_workers,
                "crashed": sum(1 for w in workers.values() if w["status"] == "crashed"),
            },
            "modules": {
                "healthy": healthy_count,
                "degraded": degraded_count,
                "unhealthy": unhealthy_count,
            },
            "events": event_stats,
            "orchestrator": orch_stats,
            "timestamp": time.time(),
        }

    def get_module_details(self) -> Dict[str, Dict[str, Any]]:
        """获取各模块详细健康信息"""
        with self._lock:
            return dict(self._module_health)

    def get_bottlenecks(self) -> List[Dict[str, Any]]:
        """识别瓶颈模块"""
        bottlenecks = []
        with self._lock:
            for name, health in self._module_health.items():
                if health.get("status") in ("degraded", "unhealthy"):
                    bottlenecks.append({
                        "module": name,
                        "status": health["status"],
                        "error_count": health.get("error_count", 0),
                        "last_event": health.get("last_event_type", ""),
                    })
        return bottlenecks


# ==================== 流水线构建器 ====================

class PipelineBuilder:
    """
    声明式流水线构建器

    将多个模块组合为可执行的工作流，支持:
    - 串行阶段
    - 并行阶段
    - 超时与重试
    - 降级备选

    用法:
        pipeline = PipelineBuilder("rag_search") \\
            .stage("rewrite", "query_rewriter", process_mode="inline") \\
            .stage("search", "hybrid_search", process_mode="process", timeout=10.0) \\
            .stage("rerank", "reranker", process_mode="inline") \\
            .stage("generate", "llm_client", process_mode="process", timeout=30.0) \\
            .parallel(["search", "rerank"]) \\
            .build()
    """

    def __init__(self, name: str):
        self._name = name
        self._stages: List[PipelineStage] = []
        self._parallel_groups: List[List[int]] = []
        self._current_stage = 0

    def stage(self, name: str, module_name: str, **kwargs) -> "PipelineBuilder":
        """添加流水线阶段"""
        self._stages.append(PipelineStage(
            name=name, module_name=module_name, **kwargs
        ))
        return self

    def parallel(self, stage_names: List[str]) -> "PipelineBuilder":
        """标记可并行的阶段"""
        indices = []
        for sname in stage_names:
            for i, s in enumerate(self._stages):
                if s.name == sname:
                    indices.append(i)
                    break
        if indices:
            self._parallel_groups.append(indices)
        return self

    def build(self) -> PipelineSpec:
        """构建流水线规格"""
        return PipelineSpec(
            name=self._name,
            stages=self._stages,
            parallel_stages=self._parallel_groups,
        )


class PipelineExecutor:
    """
    流水线执行器

    执行 PipelineSpec，管理阶段间的数据流转和错误处理。
    """

    def __init__(self, process_manager: ProcessManager, event_bus: EventBus):
        self.pm = process_manager
        self.event_bus = event_bus
        self._module_registry: Dict[str, Any] = {}

    def register_module(self, name: str, instance: Any):
        """注册模块实例"""
        self._module_registry[name] = instance

    def execute(self, spec: PipelineSpec, initial_input: Any = None) -> Dict[str, Any]:
        """
        执行流水线

        Args:
            spec: 流水线规格
            initial_input: 初始输入

        Returns:
            Dict: {"output": 最终输出, "stage_results": 各阶段结果, "errors": 错误}
        """
        stage_results: Dict[str, Any] = {}
        errors: List[Dict[str, Any]] = []
        current_input = initial_input

        # 识别并行阶段集合
        parallel_stages_set: Set[int] = set()
        parallel_groups_map: Dict[int, int] = {}  # stage_idx -> group_idx
        for gi, group in enumerate(spec.parallel_stages):
            for idx in group:
                parallel_stages_set.add(idx)
                parallel_groups_map[idx] = gi

        executed = set()

        for i, stage in enumerate(spec.stages):
            if i in executed:
                continue

            # 检查是否有并行组
            if i in parallel_stages_set:
                group_idx = parallel_groups_map[i]
                group = spec.parallel_stages[group_idx]
                parallel_results = self._execute_parallel_group(
                    spec, group, current_input, stage_results, errors
                )
                executed.update(group)
                # 合并并行结果作为下一阶段输入
                current_input = parallel_results
            else:
                # 串行执行
                result = self._execute_stage(stage, current_input)
                if result.get("error"):
                    errors.append({
                        "stage": stage.name,
                        "error": result["error"],
                    })
                    # 尝试降级
                    if stage.fallback and stage.fallback in self._module_registry:
                        fallback_stage = PipelineStage(
                            name=f"{stage.name}_fallback",
                            module_name=stage.fallback,
                        )
                        result = self._execute_stage(fallback_stage, current_input)
                stage_results[stage.name] = result.get("output")
                current_input = result.get("output")
                executed.add(i)

            # 发布阶段完成事件
            self.event_bus.publish(Event(
                event_type=EventType.PIPELINE_STAGE_DONE,
                source=spec.name,
                data={"stage": stage.name, "pipeline": spec.name},
            ))

        # 发布流水线完成事件
        self.event_bus.publish(Event(
            event_type=EventType.PIPELINE_COMPLETE,
            source=spec.name,
            data={"pipeline": spec.name, "stages": len(spec.stages)},
        ))

        return {
            "output": current_input,
            "stage_results": stage_results,
            "errors": errors,
            "pipeline": spec.name,
        }

    def _execute_stage(self, stage: PipelineStage, input_data: Any) -> Dict[str, Any]:
        """执行单个阶段"""
        module = self._module_registry.get(stage.module_name)
        if module is None:
            return {"error": f"模块未注册: {stage.module_name}"}

        for attempt in range(stage.retry + 1):
            try:
                if stage.process_mode == "process":
                    # 在独立进程中执行
                    future = self.pm.submit_to_executor(
                        self._call_module, module, input_data
                    )
                    try:
                        result = future.result(timeout=stage.timeout)
                        return {"output": result}
                    except TimeoutError:
                        # 取消 future（如果可能），防止进程浪费
                        future.cancel()
                        return {"error": f"阶段超时: {stage.name} ({stage.timeout}s)"}
                elif stage.process_mode == "thread":
                    # 在线程中执行，使用 Event 替代 join+is_alive 检测
                    done_event = threading.Event()
                    result_holder = [None, None]  # [result, error]

                    def _run():
                        try:
                            result_holder[0] = self._call_module(module, input_data)
                        except Exception as e:
                            result_holder[1] = e
                        finally:
                            done_event.set()

                    t = threading.Thread(target=_run, daemon=True)
                    t.start()
                    finished = done_event.wait(timeout=stage.timeout)
                    if not finished:
                        # 线程仍在运行但已超时，daemon=True 保证不会阻止退出
                        # 但我们不返回引用，让 GC 处理
                        return {"error": f"阶段超时: {stage.name} ({stage.timeout}s)"}
                    if result_holder[1]:
                        return {"error": str(result_holder[1])}
                    return {"output": result_holder[0]}
                else:
                    # 内联执行
                    result = self._call_module(module, input_data)
                    return {"output": result}

            except Exception as e:
                if attempt < stage.retry:
                    time.sleep(stage.retry_delay)
                    continue
                return {"error": f"阶段 {stage.name} 失败: {e}"}

        return {"error": f"阶段 {stage.name} 重试耗尽"}

    def _execute_parallel_group(
        self, spec: PipelineSpec, group: List[int],
        input_data: Any, stage_results: Dict, errors: List
    ) -> Dict[str, Any]:
        """并行执行一组阶段"""
        futures = {}
        stage_timeouts = {}
        for idx in group:
            stage = spec.stages[idx]
            module = self._module_registry.get(stage.module_name)
            if module is None:
                errors.append({"stage": stage.name, "error": f"模块未注册: {stage.module_name}"})
                continue
            future = self.pm.submit_to_executor(self._call_module, module, input_data)
            futures[stage.name] = future
            stage_timeouts[stage.name] = stage.timeout

        results = {}
        for name, future in futures.items():
            try:
                timeout = stage_timeouts.get(name, 30.0)
                results[name] = future.result(timeout=timeout)
                stage_results[name] = results[name]
            except TimeoutError:
                future.cancel()
                errors.append({"stage": name, "error": f"超时 ({stage_timeouts.get(name, 30.0)}s)"})
                results[name] = None
            except Exception as e:
                errors.append({"stage": name, "error": str(e)})
                results[name] = None

        return results

    @staticmethod
    def _call_module(module: Any, input_data: Any) -> Any:
        """调用模块处理数据"""
        if callable(module):
            return module(input_data)
        if hasattr(module, "process"):
            return module.process(input_data)
        if hasattr(module, "run"):
            return module.run(input_data)
        if hasattr(module, "execute"):
            return module.execute(input_data)
        raise TypeError(f"模块 {type(module).__name__} 没有可调用的方法 (process/run/execute/__call__)")


# ==================== ScriptsCore 桥接 ====================

class ScriptsCoreBridge:
    """
    scripts_core/ 与顶层模块的桥接层

    解决的问题：
    scripts_core/ 的 14 个子模块各自实现了 HTTP 客户端、缓存等，
    与顶层模块 (llm_client.py, semantic_cache.py 等) 完全重复。

    桥接策略：
    - LLM 调用: 重定向到顶层 llm_client.LLMClient
    - Embedding 调用: 重定向到顶层 llm_client (embedding 能力)
    - 缓存: 重定向到顶层 semantic_cache.SemanticCache / unified_cache.UnifiedCache
    - 搜索增强: 复用顶层 hybrid_search / rrf 融合等

    用法:
        bridge = ScriptsCoreBridge(llm_client=client, cache=semantic_cache)
        # 在 scripts_core 模块中使用
        result = bridge.llm_complete(prompt)
        cached = bridge.cache_get(query)
    """

    def __init__(
        self,
        llm_client: Any = None,
        embedding_client: Any = None,
        semantic_cache: Any = None,
        unified_cache: Any = None,
        hybrid_search: Any = None,
    ):
        self._llm_client = llm_client
        self._embedding_client = embedding_client
        self._semantic_cache = semantic_cache
        self._unified_cache = unified_cache
        self._hybrid_search = hybrid_search

        self._call_stats = {
            "llm_calls": 0,
            "embedding_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "search_calls": 0,
        }

    def llm_complete(self, prompt: str, **kwargs) -> str:
        """LLM 补全 (桥接到顶层 llm_client)"""
        self._call_stats["llm_calls"] += 1
        if self._llm_client and hasattr(self._llm_client, "complete"):
            return self._llm_client.complete(prompt, **kwargs)
        # 降级：返回提示
        return f"[Bridge] LLM unavailable for: {prompt[:50]}..."

    def llm_chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """LLM 对话 (桥接到顶层 llm_client)"""
        self._call_stats["llm_calls"] += 1
        if self._llm_client and hasattr(self._llm_client, "chat"):
            return self._llm_client.chat(messages, **kwargs)
        return "[Bridge] LLM chat unavailable"

    def embed(self, text: str, **kwargs) -> Any:
        """Embedding (桥接到顶层)"""
        self._call_stats["embedding_calls"] += 1
        if self._embedding_client and hasattr(self._embedding_client, "embed"):
            return self._embedding_client.embed(text, **kwargs)
        if self._llm_client and hasattr(self._llm_client, "embed"):
            return self._llm_client.embed(text, **kwargs)
        return None

    def cache_get(self, key: str) -> Optional[Any]:
        """缓存查询 (桥接到顶层)"""
        # 先查语义缓存
        if self._semantic_cache and hasattr(self._semantic_cache, "get"):
            result = self._semantic_cache.get(key)
            if result is not None:
                self._call_stats["cache_hits"] += 1
                return result
        # 再查统一缓存
        if self._unified_cache and hasattr(self._unified_cache, "get"):
            result = self._unified_cache.get(key)
            if result is not None:
                self._call_stats["cache_hits"] += 1
                return result
        self._call_stats["cache_misses"] += 1
        return None

    def cache_set(self, key: str, value: Any, ttl: float = 3600.0):
        """缓存写入 (桥接到顶层)"""
        if self._unified_cache and hasattr(self._unified_cache, "set"):
            self._unified_cache.set(key, value, ttl)
        elif self._semantic_cache and hasattr(self._semantic_cache, "set"):
            self._semantic_cache.set(key, value, ttl)

    def search(self, query: str, **kwargs) -> Any:
        """混合搜索 (桥接到顶层)"""
        self._call_stats["search_calls"] += 1
        if self._hybrid_search and hasattr(self._hybrid_search, "search"):
            return self._hybrid_search.search(query, **kwargs)
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取桥接统计"""
        return dict(self._call_stats)


# ==================== 通用 Worker 模板 ====================

def generic_worker(
    name: str,
    task_queue: Queue,
    result_queue: Queue,
    event_queue: Queue,
    handler: Callable = None,
    **kwargs,
):
    """
    通用 Worker 入口函数

    从 task_queue 读取任务，调用 handler 处理，结果写入 result_queue。
    支持心跳和事件上报。

    Args:
        name: Worker 名称
        task_queue: 任务队列
        result_queue: 结果队列
        event_queue: 事件队列
        handler: 任务处理函数 handler(task) -> result
    """
    logger.info(f"Worker [{name}] 启动, PID={os.getpid()}")

    # 上报启动事件
    event_queue.put(pickle.dumps(Event(
        event_type=EventType.PROCESS_STARTED,
        source=name,
        data={"pid": os.getpid()},
    )))

    while True:
        try:
            # 非阻塞获取任务，周期性检查
            try:
                task_data = task_queue.get(timeout=1.0)
            except Exception:
                continue

            if task_data is None:
                # 停止信号
                break

            task = pickle.loads(task_data)
            task_id = task.get("task_id", "unknown")
            func = task.get("func")
            args = task.get("args", ())
            task_kwargs = task.get("kwargs", {})

            try:
                if handler:
                    result = handler(task)
                elif func:
                    result = func(*args, **task_kwargs)
                else:
                    result = None

                result_queue.put(pickle.dumps({
                    "task_id": task_id,
                    "data": result,
                }))

            except Exception as e:
                result_queue.put(pickle.dumps({
                    "task_id": task_id,
                    "error": str(e),
                }))
                event_queue.put(pickle.dumps(Event(
                    event_type=EventType.LLM_ERROR,
                    source=name,
                    data={"task_id": task_id, "error": str(e)},
                )))

        except Exception as e:
            logger.error(f"Worker [{name}] 异常: {e}")
            break

    # 上报停止事件
    event_queue.put(pickle.dumps(Event(
        event_type=EventType.PROCESS_STOPPED,
        source=name,
        data={"pid": os.getpid()},
    )))
    logger.info(f"Worker [{name}] 退出")


# ==================== 主协调器 ====================

class ModuleCoordinator:
    """
    模块协同与进程管理器

    统一入口，整合:
    - EventBus: 事件总线
    - ProcessManager: 进程管理
    - CrossLayerOrchestrator: 跨层编排
    - HealthDashboard: 健康仪表盘
    - PipelineBuilder + PipelineExecutor: 流水线编排
    - ScriptsCoreBridge: 桥接层

    用法:
        coordinator = ModuleCoordinator()

        # 注册模块
        coordinator.register_module("llm_client", llm_client)
        coordinator.register_module("hybrid_search", search_engine)

        # 注册 Worker 进程
        coordinator.register_worker(WorkerSpec(
            name="vector_search",
            module_name="vector_api",
            layer=ModuleLayer.SEARCH,
            target=generic_worker,
            kwargs={"handler": vector_search_handler},
        ))

        # 启动
        coordinator.start()

        # 构建并执行流水线
        pipeline = (coordinator.build_pipeline("rag_search")
            .stage("rewrite", "query_rewriter")
            .stage("search", "hybrid_search", process_mode="process")
            .stage("generate", "llm_client", process_mode="process", timeout=30.0)
            .build())
        result = coordinator.execute_pipeline(pipeline, query="...")

        # 查看健康状态
        overview = coordinator.get_health_overview()

        # 停止
        coordinator.stop()
    """

    def __init__(self, max_workers: int = 0):
        self.event_bus = EventBus()
        self.process_manager = ProcessManager(max_workers=max_workers)
        self.cross_layer = CrossLayerOrchestrator(self.event_bus, self.process_manager)
        self.health_dashboard = HealthDashboard(
            self.event_bus, self.process_manager, self.cross_layer
        )
        self.pipeline_executor = PipelineExecutor(self.process_manager, self.event_bus)
        self._scripts_bridge: Optional[ScriptsCoreBridge] = None
        self._started = False
        self._finalizer = weakref.finalize(self, self._cleanup_on_del, self.process_manager, self.event_bus)

    @staticmethod
    def _cleanup_on_del(pm, eb):
        """对象被 GC 时的安全清理，防止孤儿进程"""
        try:
            pm.stop_all(timeout=3.0)
        except Exception:
            pass
        try:
            eb.shutdown()
        except Exception:
            pass

    def register_module(self, name: str, instance: Any):
        """注册模块实例"""
        self.pipeline_executor.register_module(name, instance)

    def register_worker(self, spec: WorkerSpec):
        """注册 Worker 进程"""
        self.process_manager.register_worker(spec)

    def setup_scripts_bridge(self, **clients):
        """设置 scripts_core 桥接"""
        self._scripts_bridge = ScriptsCoreBridge(**clients)
        return self._scripts_bridge

    def build_pipeline(self, name: str) -> PipelineBuilder:
        """创建流水线构建器"""
        return PipelineBuilder(name)

    def execute_pipeline(self, spec: PipelineSpec, input_data: Any = None) -> Dict[str, Any]:
        """执行流水线"""
        return self.pipeline_executor.execute(spec, input_data)

    def submit_task(self, worker_name: str, func: Callable, *args, **kwargs) -> Optional[Any]:
        """向 Worker 提交任务"""
        return self.process_manager.submit(worker_name, func, *args, **kwargs)

    def submit_parallel(self, func: Callable, items: List[Any]) -> List[Any]:
        """批量并行执行"""
        return self.process_manager.submit_batch(func, items)

    def publish_event(self, event_type: EventType, source: str, data: Dict = None, priority: int = 0):
        """发布事件"""
        event = Event(event_type=event_type, source=source, data=data or {}, priority=priority)
        return self.event_bus.publish(event)

    def subscribe_event(self, event_type: str, callback: Callable):
        """订阅事件"""
        self.event_bus.subscribe(event_type, callback)

    def register_cross_layer_rule(self, name: str, trigger_name: str,
                                   conditions: List[Dict] = None,
                                   actions: List[Callable] = None,
                                   cooldown: float = 30.0):
        """
        注册跨层联动规则

        Args:
            name: 规则名称
            trigger_name: 触发器名称 (如 "hardware_latency", "llm_overload")
            conditions: 附加条件 [{"field": "data.value", "op": ">", "value": 100}]
            actions: 执行动作 [callable(event)]
            cooldown: 冷却时间
        """
        self.cross_layer.register_rule({
            "name": name,
            "trigger_name": trigger_name,
            "conditions": conditions or [],
            "actions": actions or [],
            "cooldown": cooldown,
            "last_triggered": 0.0,
        })

    def start(self) -> Dict[str, bool]:
        """启动所有 Worker 和监控"""
        if self._started:
            return {}
        self._started = True
        results = self.process_manager.start_all()
        logger.info(f"ModuleCoordinator 启动: {sum(results.values())}/{len(results)} Worker 成功")
        return results

    def stop(self):
        """停止所有 Worker 和监控"""
        self._started = False
        self.process_manager.stop_all()
        self.event_bus.shutdown()
        logger.info("ModuleCoordinator 已停止")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    def get_health_overview(self) -> Dict[str, Any]:
        """获取全局健康概览"""
        return self.health_dashboard.get_overview()

    def get_health_details(self) -> Dict[str, Dict[str, Any]]:
        """获取各模块健康详情"""
        return self.health_dashboard.get_module_details()

    def get_bottlenecks(self) -> List[Dict[str, Any]]:
        """识别瓶颈模块"""
        return self.health_dashboard.get_bottlenecks()

    def get_worker_info(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Worker 信息"""
        return self.process_manager.get_all_worker_info()

    def get_event_stats(self) -> Dict[str, Any]:
        """获取事件统计"""
        return self.event_bus.get_stats()

    def get_bridge_stats(self) -> Dict[str, Any]:
        """获取桥接统计"""
        if self._scripts_bridge:
            return self._scripts_bridge.get_stats()
        return {}

    def get_full_report(self) -> Dict[str, Any]:
        """获取完整报告"""
        return {
            "health": self.get_health_overview(),
            "workers": self.get_worker_info(),
            "events": self.get_event_stats(),
            "orchestrator": self.cross_layer.get_stats(),
            "bridge": self.get_bridge_stats(),
            "bottlenecks": self.get_bottlenecks(),
        }


# ==================== 便捷函数 ====================

def create_coordinator(max_workers: int = 0) -> ModuleCoordinator:
    """创建模块协调器"""
    return ModuleCoordinator(max_workers=max_workers)


def create_worker_spec(
    name: str,
    module_name: str,
    layer: ModuleLayer,
    target: Callable = generic_worker,
    **kwargs,
) -> WorkerSpec:
    """创建 Worker 规格"""
    return WorkerSpec(
        name=name, module_name=module_name, layer=layer, target=target, **kwargs
    )


# ==================== 测试 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("模块协同与进程管理器测试")
    print("=" * 60)

    # 1. 创建协调器
    coordinator = ModuleCoordinator(max_workers=4)

    # 2. 注册跨层联动规则
    coordinator.register_cross_layer_rule(
        name="llm_overload_kv_quant",
        trigger_name="llm_overload",
        conditions=[{"field": "pending_requests", "op": ">", "value": 10}],
        actions=[lambda e: print(f"  [联动] LLM 过载，启用 KV 量化: {e.data}")],
        cooldown=10.0,
    )

    coordinator.register_cross_layer_rule(
        name="cache_eviction_warmup",
        trigger_name="cache_eviction",
        actions=[lambda e: print(f"  [联动] 缓存淘汰，触发预热: {e.data}")],
        cooldown=5.0,
    )

    # 3. 构建流水线
    pipeline = (coordinator.build_pipeline("test_rag")
        .stage("input", "input_handler", process_mode="inline")
        .stage("process", "data_processor", process_mode="inline")
        .build())

    # 4. 注册模拟模块
    coordinator.register_module("input_handler", lambda x: {"query": x, "rewritten": f"enhanced_{x}"})
    coordinator.register_module("data_processor", lambda x: {**x, "result": f"processed_{x.get('query', '')}"})

    # 5. 执行流水线
    print("\n--- 流水线执行 ---")
    result = coordinator.execute_pipeline(pipeline, "test_query")
    print(f"  流水线结果: {result}")

    # 6. 事件系统测试
    print("\n--- 事件系统测试 ---")
    events_received = []
    coordinator.subscribe_event("llm.*", lambda e: events_received.append(e))

    coordinator.publish_event(
        EventType.LLM_REQUEST_START, "test_module",
        {"prompt": "hello", "model": "gpt-4"}
    )
    coordinator.publish_event(
        EventType.LLM_OVERLOAD, "llm_client",
        {"pending_requests": 15, "avg_latency_ms": 500}
    )
    print(f"  收到事件数: {len(events_received)}")

    # 7. 批量并行执行
    print("\n--- 批量并行执行 ---")

    def square(x):
        import time
        time.sleep(0.1)
        return x * x

    items = list(range(10))
    results = coordinator.submit_parallel(square, items)
    print(f"  输入: {items}")
    print(f"  结果: {results}")

    # 8. 注册并启动 Worker 进程
    print("\n--- Worker 进程测试 ---")

    def vector_search_handler(task):
        """模拟向量搜索处理"""
        query = task.get("args", ("", ))[0] if task.get("args") else "default"
        return {"results": [1, 2, 3], "query": query, "scores": [0.9, 0.8, 0.7]}

    coordinator.register_worker(create_worker_spec(
        name="vector_search",
        module_name="vector_api",
        layer=ModuleLayer.SEARCH,
        target=generic_worker,
        kwargs={"handler": vector_search_handler},
    ))

    coordinator.register_worker(create_worker_spec(
        name="llm_inference",
        module_name="llm_client",
        layer=ModuleLayer.LLM,
        target=generic_worker,
    ))

    start_results = coordinator.start()
    print(f"  Worker 启动结果: {start_results}")

    # 等待 Worker 就绪
    time.sleep(1.0)

    # 9. 健康状态
    print("\n--- 健康状态 ---")
    overview = coordinator.get_health_overview()
    print(f"  整体状态: {overview['overall_status']}")
    print(f"  Worker: {overview['workers']}")

    worker_info = coordinator.get_worker_info()
    for name, info in worker_info.items():
        print(f"  - {name}: PID={info['pid']}, 状态={info['status']}, 层={info['layer']}")

    # 10. ScriptsCore 桥接
    print("\n--- ScriptsCore 桥接测试 ---")
    bridge = coordinator.setup_scripts_bridge()
    result = bridge.llm_complete("测试提示")
    print(f"  LLM 补全: {result}")
    cached = bridge.cache_get("test_key")
    print(f"  缓存查询: {cached}")
    print(f"  桥接统计: {bridge.get_stats()}")

    # 11. 完整报告
    print("\n--- 完整报告 ---")
    report = coordinator.get_full_report()
    print(f"  事件统计: {report['events']}")
    print(f"  编排统计: {report['orchestrator']}")
    print(f"  瓶颈模块: {report['bottlenecks']}")

    # 12. 停止
    coordinator.stop()
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
