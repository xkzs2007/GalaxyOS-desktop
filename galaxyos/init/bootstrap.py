#!/usr/bin/env python3
"""
GalaxyOS 一键三举编排器 (Galaxy Bootstrap Orchestrator)
======================================================

统一编排三大自动化能力:
  1. EnvCapability   -- 环境探测与适配 (auto_env_adapter)
  2. DepCapability   -- 依赖健康检查   (auto_dep_manager)
  3. VersionCapability -- 版本一致性校验 (version)
  4. UpdateCapability -- 按需更新       (on_demand_updater)

核心设计:
  - Capability ABC: 每个能力是独立插件, 声明依赖关系
  - GalaxyBootstrap: 按依赖拓扑排序执行, 支持并行/断点续举
  - BootstrapContext: 跨能力共享上下文
  - BootstrapReport: 统一报告, 兼容旧 auto_bootstrap() 返回格式

用法:
  from .galaxy_bootstrap import GalaxyBootstrap, auto_bootstrap

  # 新 API -- 编排器
  bootstrap = GalaxyBootstrap(profile="dev", dry_run=False)
  bootstrap.register(EnvCapability())
  bootstrap.register(DepCapability())
  bootstrap.register(VersionCapability())
  report = await bootstrap.run()

  # 旧 API -- 向后兼容
  result = await auto_bootstrap(dry_run=False, profile="dev")
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from galaxyos.init.deployment_profile import get_profile as _get_deploy_profile

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  多环境 Profile 配置
# ═══════════════════════════════════════════════════════════════════

PROFILE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "dev": {
        "description": "Development profile -- all capabilities enabled, relaxed timeouts",
        "dry_run": False,
        "capabilities": {
            "env":     {"enabled": True, "timeout": 120.0},
            "dep":     {"enabled": True, "timeout": 120.0},
            "version": {"enabled": True, "timeout": 60.0},
            "update":  {"enabled": True, "timeout": 120.0},
        },
    },
    "staging": {
        "description": "Staging profile -- update enabled, moderate timeouts",
        "dry_run": False,
        "capabilities": {
            "env":     {"enabled": True, "timeout": 60.0},
            "dep":     {"enabled": True, "timeout": 60.0},
            "version": {"enabled": True, "timeout": 30.0},
            "update":  {"enabled": True, "timeout": 60.0},
        },
    },
    "prod": {
        "description": "Production profile -- all enabled, strict timeouts",
        "dry_run": False,
        "capabilities": {
            "env":     {"enabled": True, "timeout": 30.0},
            "dep":     {"enabled": True, "timeout": 30.0},
            "version": {"enabled": True, "timeout": 15.0},
            "update":  {"enabled": True, "timeout": 30.0},
        },
    },
}
"""Pre-defined environment profiles for dev/staging/prod.

Each profile specifies:
  - dry_run:      default dry_run mode
  - capabilities: per-capability enabled flag and timeout (seconds)

Usage:
    bootstrap = GalaxyBootstrap(profile="prod")
    # prod profile defaults to dry_run=True on first run
"""


# ═══════════════════════════════════════════════════════════════════
#  数据类
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BootstrapContext:
    """编排器共享上下文 -- 在各 Capability 之间传递状态。

    Attributes:
        env_profile:  环境探测结果 (platform/gpu/memory/adaptations)
        dep_status:   依赖健康状态 (total/installed/missing/groups)
        ver_info:     版本信息 (consistent/sources)
        update_plan:  更新计划 (has_updates/changed_files/diff_summary)
        errors:       错误收集 (跨能力共享)
        warnings:     警告收集 (跨能力共享)
        dry_run:      只检测不修改
        profile:      运行环境 (dev/staging/prod)
        completed:    已完成的 capability 名称集合
    """

    env_profile: Dict[str, Any] = field(default_factory=dict)
    dep_status: Dict[str, Any] = field(default_factory=dict)
    ver_info: Dict[str, Any] = field(default_factory=dict)
    update_plan: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dry_run: bool = False
    profile: str = "dev"
    completed: Set[str] = field(default_factory=set)


@dataclass
class CapabilityResult:
    """单个 Capability 的执行结果。

    Attributes:
        name:           能力名称
        success:        是否成功
        degraded:       是否降级运行
        degradation_note: 降级说明 (如 "torch not available, using CPU fallback")
        data:           结果数据 (能力特定的返回内容)
        errors:         错误列表
        warnings:       警告列表
        duration_ms:    执行耗时 (毫秒)
    """

    name: str
    success: bool
    degraded: bool = False
    degradation_note: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class BootstrapReport:
    """编排器统一报告 -- 汇总所有 Capability 的执行结果。

    Attributes:
        success:          整体是否成功 (所有 capability 均成功)
        total_duration_ms: 总耗时 (毫秒)
        results:          各 capability 的执行结果 {name: CapabilityResult}
        context:          最终上下文快照
    """

    success: bool
    total_duration_ms: float
    results: Dict[str, CapabilityResult] = field(default_factory=dict)
    context: BootstrapContext = field(default_factory=BootstrapContext)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典, 兼容旧 auto_bootstrap() 返回格式。

        Returns:
            包含 env_adaptation / dep_health / version_consistency / status
            以及新格式 results / context 的字典。
        """
        result: Dict[str, Any] = {
            "success": self.success,
            "total_duration_ms": self.total_duration_ms,
            "status": "ok",
            "env_adaptation": None,
            "dep_health": None,
            "version_consistency": None,
            "update_plan": None,
        }

        # 从各 CapabilityResult 提取旧格式字段
        env_result = self.results.get("env")
        if env_result is not None:
            result["env_adaptation"] = env_result.data if env_result.success else {"error": "; ".join(env_result.errors)} if env_result.errors else env_result.data

        dep_result = self.results.get("dep")
        if dep_result is not None:
            result["dep_health"] = dep_result.data if dep_result.success else {"error": "; ".join(dep_result.errors)} if dep_result.errors else dep_result.data

        ver_result = self.results.get("version")
        if ver_result is not None:
            result["version_consistency"] = ver_result.data if ver_result.success else {"error": "; ".join(ver_result.errors)} if ver_result.errors else ver_result.data

        update_result = self.results.get("update")
        if update_result is not None:
            result["update_plan"] = update_result.data if update_result.success else {"error": "; ".join(update_result.errors)} if update_result.errors else update_result.data

        # 推导 status -- 与旧 auto_bootstrap() 逻辑一致
        has_error = any(not r.success for r in self.results.values())
        has_degraded = any(r.degraded for r in self.results.values())
        has_version_mismatch = False

        ver_data = result.get("version_consistency")
        if isinstance(ver_data, dict) and not ver_data.get("consistent", True):
            has_version_mismatch = True

        dep_data = result.get("dep_health")
        has_missing_core = isinstance(dep_data, dict) and dep_data.get("missing_core")

        if has_error:
            result["status"] = "partial"
        elif has_version_mismatch:
            result["status"] = "version_mismatch"
        elif has_missing_core:
            result["status"] = "degraded"
        elif has_degraded:
            result["status"] = "degraded"
        else:
            result["status"] = "ok"

        # 新格式: 详细结果
        result["results"] = {
            name: {
                "success": r.success,
                "degraded": r.degraded,
                "degradation_note": r.degradation_note,
                "data": r.data,
                "errors": r.errors,
                "warnings": r.warnings,
                "duration_ms": r.duration_ms,
            }
            for name, r in self.results.items()
        }

        # 新格式: 上下文摘要
        result["context"] = {
            "profile": self.context.profile,
            "dry_run": self.context.dry_run,
            "completed": sorted(self.context.completed),
            "errors": self.context.errors,
            "warnings": self.context.warnings,
        }

        return result

    def to_json(self) -> str:
        """转换为 JSON 字符串。

        Returns:
            格式化的 JSON 字符串。
        """
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str)

    def summary(self) -> str:
        """生成人类可读的摘要报告。

        Returns:
            多行文本摘要。
        """
        lines = [
            "=== GalaxyOS Bootstrap Report ===",
            "",
            f"Overall: {'OK' if self.success else 'FAILED'}",
            f"Duration: {self.total_duration_ms:.1f}ms",
            f"Profile:  {self.context.profile}",
            f"Dry Run:  {self.context.dry_run}",
            "",
        ]

        for name, r in self.results.items():
            status_tag = "OK" if r.success else "FAIL"
            if r.degraded:
                status_tag = "DEGRADED"
            lines.append(f"  [{status_tag}] {name} ({r.duration_ms:.1f}ms)")
            if r.degradation_note:
                lines.append(f"         note: {r.degradation_note}")
            for err in r.errors:
                lines.append(f"         error: {err}")
            for warn in r.warnings:
                lines.append(f"         warn:  {warn}")

        if self.context.errors:
            lines.append("")
            lines.append("Global errors:")
            for err in self.context.errors:
                lines.append(f"  - {err}")

        if self.context.warnings:
            lines.append("")
            lines.append("Global warnings:")
            for warn in self.context.warnings:
                lines.append(f"  - {warn}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Capability ABC
# ═══════════════════════════════════════════════════════════════════

class Capability(ABC):
    """能力插件基类 -- 所有编排能力必须继承此类。

    子类需实现:
      - execute(): 实际执行逻辑
      - check():   dry-run 检测逻辑 (只读, 不修改环境)

    Attributes:
        name:        能力唯一标识名
        description: 能力描述
        requires:    前置依赖的 capability 名称列表
        fallbacks:   降级链 -- 主能力失败时依次尝试的降级能力名称列表
    """

    name: str = ""
    description: str = ""
    requires: List[str] = []
    fallbacks: List[str] = []

    @abstractmethod
    async def execute(self, ctx: BootstrapContext) -> CapabilityResult:
        """执行能力逻辑。

        Args:
            ctx: 共享上下文

        Returns:
            执行结果
        """
        ...

    @abstractmethod
    async def check(self, ctx: BootstrapContext) -> CapabilityResult:
        """Dry-run 检测 -- 只读, 不修改环境。

        Args:
            ctx: 共享上下文

        Returns:
            检测结果
        """
        ...

    def can_run(self, ctx: BootstrapContext) -> bool:
        """检查前置依赖是否已满足。

        Args:
            ctx: 共享上下文

        Returns:
            所有 requires 中的能力是否已完成
        """
        return all(r in ctx.completed for r in self.requires)


# ═══════════════════════════════════════════════════════════════════
#  内置 Capability 实现
# ═══════════════════════════════════════════════════════════════════

class EnvCapability(Capability):
    """环境探测与适配能力 -- 封装 auto_env_adapter。

    探测 OS/架构/GPU/内存/容器/K8s, 补全缺失环境变量,
    推导最优 Worker 池大小和内存限制。
    """

    name = "env"
    description = "Environment detection and adaptation"
    requires: List[str] = []
    fallbacks: List[str] = ["env_cpu_fallback", "env_openblas_fallback"]

    async def execute(self, ctx: BootstrapContext) -> CapabilityResult:
        """执行环境适配。

        Args:
            ctx: 共享上下文

        Returns:
            包含 platform/gpu/memory/adaptations 的结果
        """
        start = time.perf_counter()
        try:
            from galaxyos.config.auto_env_adapter import auto_adapt_environment
            data = auto_adapt_environment()
            ctx.env_profile = data
            elapsed = (time.perf_counter() - start) * 1000
            return CapabilityResult(
                name=self.name,
                success=True,
                data=data,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning(f"execute: Exception - {exc}")
            return CapabilityResult(
                name=self.name,
                success=False,
                errors=[str(exc)],
                duration_ms=elapsed,
            )

    async def check(self, ctx: BootstrapContext) -> CapabilityResult:
        """Dry-run 环境探测 (只读, 不补全环境变量)。

        Args:
            ctx: 共享上下文

        Returns:
            包含 platform/gpu/memory 的探测结果 (无 adaptations)
        """
        start = time.perf_counter()
        try:
            from galaxyos.config.auto_env_adapter import detect_platform, detect_gpu, detect_memory
            data = {
                "platform": detect_platform(),
                "gpu": detect_gpu(),
                "memory": detect_memory(),
                "adaptations": [],
            }
            ctx.env_profile = data
            elapsed = (time.perf_counter() - start) * 1000
            return CapabilityResult(
                name=self.name,
                success=True,
                data=data,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return CapabilityResult(
                name=self.name,
                success=False,
                errors=[str(exc)],
                duration_ms=elapsed,
            )


class DepCapability(Capability):
    """依赖健康检查能力 -- 封装 auto_dep_manager。

    扫描所有依赖组, 报告缺失/版本不匹配,
    核心依赖缺失标记为 degraded。
    智能解析 torch/faiss 变体 (CPU/GPU)。
    """

    name = "dep"
    description = "Dependency health check"
    requires: List[str] = []
    fallbacks: List[str] = ["dep_onnxruntime_fallback"]

    # ── CUDA 版本 -> torch 变体映射 ──
    _CUDA_TORCH_MAP: Dict[str, tuple] = {
        "11.8": ("cu118", "https://download.pytorch.org/whl/cu118"),
        "12.1": ("cu121", "https://download.pytorch.org/whl/cu121"),
        "12.4": ("cu124", "https://download.pytorch.org/whl/cu124"),
        "12.6": ("cu126", "https://download.pytorch.org/whl/cu126"),
    }

    def _resolve_torch_variant(self, ctx: BootstrapContext) -> dict:
        """根据环境智能选择torch变体。

        逻辑:
        1. 检测GPU: nvidia-smi + torch.cuda.is_available()
        2. 无GPU -> torch+cpu (pip install torch --index-url CPU索引)
        3. 有GPU -> 检测CUDA版本 -> 匹配对应的torch+cuXXX
        4. CUDA版本不匹配任何预编译版 -> 降级到CPU版 + 警告

        CUDA版本映射:
          CUDA 11.8 -> torch+cu118
          CUDA 12.1 -> torch+cu121
          CUDA 12.4 -> torch+cu124
          CUDA 12.6 -> torch+cu126
          其他 -> torch+cpu (降级)
        """
        # Profile预选: 允许DeploymentProfile指定torch_variant
        _profile = _get_deploy_profile()
        _profile_torch = _profile.get('torch_variant')
        if _profile_torch and _profile_torch != 'auto':
            return {
                "variant": _profile_torch,
                "install_cmd": f"pip install torch --index-url https://download.pytorch.org/whl/{_profile_torch}",
                "reason": f"torch variant pre-selected by deployment profile: {_profile_torch}",
                "cuda_version": None,
                "degraded": False,
            }

        result = {
            "variant": "cpu",
            "install_cmd": "pip install torch --index-url https://download.pytorch.org/whl/cpu",
            "reason": "No GPU detected, using CPU-only torch (200MB vs 2.5GB)",
            "cuda_version": None,
            "degraded": False,
        }

        gpu_info = ctx.env_profile.get("gpu", {})
        if not gpu_info.get("available", False):
            return result  # 无GPU, CPU版

        cuda_ver = gpu_info.get("cuda_version")  # 如 "12.1"
        if not cuda_ver:
            return result  # 有GPU但无法检测CUDA版本, 保守用CPU

        # 精确匹配
        if cuda_ver in self._CUDA_TORCH_MAP:
            tag, url = self._CUDA_TORCH_MAP[cuda_ver]
            result["variant"] = tag
            result["install_cmd"] = f"pip install torch --index-url {url}"
            result["reason"] = f"GPU detected with CUDA {cuda_ver}, using torch+{tag}"
            result["cuda_version"] = cuda_ver
            return result

        # 模糊匹配(取最接近的较低版本)
        major_minor = ".".join(cuda_ver.split(".")[:2])
        if major_minor in self._CUDA_TORCH_MAP:
            tag, url = self._CUDA_TORCH_MAP[major_minor]
            result["variant"] = tag
            result["install_cmd"] = f"pip install torch --index-url {url}"
            result["reason"] = f"GPU detected with CUDA {cuda_ver}, using closest torch+{tag}"
            result["cuda_version"] = cuda_ver
            return result

        # 无法匹配 -> 降级CPU
        result["degraded"] = True
        result["reason"] = f"CUDA {cuda_ver} has no pre-built torch, falling back to CPU"
        return result

    def _resolve_faiss_variant(self, ctx: BootstrapContext) -> dict:
        """根据环境智能选择faiss变体。

        有GPU+torch+cuXXX -> faiss-gpu
        无GPU -> faiss-cpu
        """
        torch_variant = self._resolve_torch_variant(ctx)

        if torch_variant["variant"] != "cpu":
            return {
                "variant": "gpu",
                "install_cmd": "pip install faiss-gpu",
                "reason": "GPU available, using faiss-gpu for accelerated vector search",
            }

        return {
            "variant": "cpu",
            "install_cmd": "pip install faiss-cpu",
            "reason": "No GPU, using faiss-cpu (sufficient for GalaxyOS workloads)",
        }

    def _resolve_numpy_variant(self, ctx: BootstrapContext) -> dict:
        """numpy智能解析: 根据平台和MKL可用性选择BLAS后端。

        - Windows ARM不支持mkl==2026.0.0
        - 有MKL -> numpy+mkl (Intel优化, 矩阵运算快3-5x)
        - 无MKL/不兼容 -> numpy+OpenBLAS (默认, 通用)
        - ARM平台(鲲鹏/Apple Silicon) -> numpy无mkl, 用系统BLAS
        """
        import platform
        machine = platform.machine().lower()
        system = platform.system().lower()

        # Windows ARM: MKL不支持
        if system == "windows" and machine in ("arm64", "aarch64"):
            return {
                "variant": "openblas",
                "install_cmd": "pip install numpy",
                "reason": "Windows ARM does not support MKL, using OpenBLAS (bundled)",
                "degraded": False,
            }

        # ARM平台(鲲鹏/Apple Silicon): 无MKL, 用系统BLAS
        if machine in ("aarch64", "arm64"):
            return {
                "variant": "system_blas",
                "install_cmd": "pip install numpy",
                "reason": f"ARM platform ({machine}), MKL unavailable, using system BLAS",
                "degraded": False,
            }

        # x86_64: 检查MKL可用性
        mkl_info = self._resolve_mkl_variant(ctx)
        blas_variant = mkl_info.get("blas_backend", "openblas")

        if blas_variant == "mkl":
            return {
                "variant": "mkl",
                "install_cmd": "pip install numpy mkl==2026.0.0",
                "reason": "x86_64 with MKL, numpy+mkl for 3-5x faster matrix ops",
                "degraded": False,
            }

        # x86_64无MKL -> OpenBLAS
        return {
            "variant": "openblas",
            "install_cmd": "pip install numpy",
            "reason": "x86_64 without MKL, using OpenBLAS (bundled with numpy)",
            "degraded": False,
        }

    def _resolve_scipy_variant(self, ctx: BootstrapContext) -> dict:
        """scipy智能解析: 依赖numpy+BLAS, 跟随numpy选择。

        - 有MKL -> scipy+mkl优化
        - 无MKL -> scipy默认
        """
        numpy_resolution = self._resolve_numpy_variant(ctx)
        numpy_variant = numpy_resolution.get("variant", "openblas")

        if numpy_variant == "mkl":
            return {
                "variant": "mkl",
                "install_cmd": "pip install scipy mkl==2026.0.0",
                "reason": "numpy+mkl selected, scipy+mkl for optimized LAPACK/FFT",
                "degraded": False,
            }

        return {
            "variant": "default",
            "install_cmd": "pip install scipy",
            "reason": "numpy without MKL, using default scipy (OpenBLAS)",
            "degraded": False,
        }

    def _resolve_mkl_variant(self, ctx: BootstrapContext) -> dict:
        """MKL智能解析: 根据平台/架构/容器环境选择最优BLAS后端。

        平台检测:
          - Windows ARM       -> MKL不支持, 标记degraded, 用OpenBLAS替代
          - macOS ARM (Apple Silicon) -> MKL不支持, 用Accelerate框架
          - Linux x86_64      -> mkl==2026.0.0 (Intel优化, 矩阵运算快3-5x)
          - Linux aarch64 (鲲鹏) -> MKL不支持, 用OpenBLAS
          - 容器内 (Docker/K8s) -> mkl可用但体积大, 建议用OpenBLAS (镜像更小)

        Returns:
            dict with keys: variant, install_cmd, reason, degraded, blas_backend
        """
        import platform
        # Profile预选: 允许DeploymentProfile指定blas_backend
        _profile = _get_deploy_profile()
        _profile_blas = _profile.get('blas_backend')
        if _profile_blas and _profile_blas != 'auto':
            return {
                "variant": _profile_blas,
                "install_cmd": "pip install numpy" if _profile_blas != "mkl" else "pip install mkl==2026.0.0",
                "reason": f"BLAS backend pre-selected by deployment profile: {_profile_blas}",
                "degraded": False,
                "blas_backend": _profile_blas,
            }

        machine = platform.machine().lower()
        system = platform.system().lower()

        # 检测容器环境
        env_profile = ctx.env_profile if ctx else {}
        is_container = bool(env_profile.get("container", {}).get("detected", False))

        # Windows ARM: MKL不支持
        if system == "windows" and machine in ("arm64", "aarch64"):
            return {
                "variant": "openblas",
                "install_cmd": "pip install numpy",
                "reason": "Windows ARM does not support MKL, using OpenBLAS fallback",
                "degraded": True,
                "blas_backend": "openblas",
            }

        # macOS ARM (Apple Silicon): MKL不支持, 用Accelerate框架
        if system == "darwin" and machine == "arm64":
            return {
                "variant": "accelerate",
                "install_cmd": "pip install numpy",
                "reason": "Apple Silicon: MKL not available, using Accelerate framework (native optimized)",
                "degraded": False,
                "blas_backend": "accelerate",
            }

        # Linux aarch64 (鲲鹏): MKL不支持, 用OpenBLAS
        if system == "linux" and machine in ("aarch64", "arm64"):
            return {
                "variant": "openblas",
                "install_cmd": "pip install numpy",
                "reason": "Linux aarch64 (Kunpeng): MKL not available, using OpenBLAS",
                "degraded": False,
                "blas_backend": "openblas",
            }

        # x86_64/AMD64: MKL可用
        if machine in ("x86_64", "amd64"):
            # 容器内: mkl可用但体积大, 建议用OpenBLAS (镜像更小)
            if is_container:
                return {
                    "variant": "openblas",
                    "install_cmd": "pip install numpy",
                    "reason": "Container environment: MKL available but large (~800MB), OpenBLAS recommended for smaller images",
                    "degraded": False,
                    "blas_backend": "openblas",
                }
            # 裸机/VM: 使用MKL获得最佳性能
            return {
                "variant": "mkl",
                "install_cmd": "pip install mkl==2026.0.0",
                "reason": "x86_64 platform: Intel MKL for 3-5x faster matrix operations",
                "degraded": False,
                "blas_backend": "mkl",
            }

        # 其他平台: 保守回退到OpenBLAS
        return {
            "variant": "openblas",
            "install_cmd": "pip install numpy",
            "reason": f"Unsupported platform ({system}/{machine}), using OpenBLAS fallback",
            "degraded": True,
            "blas_backend": "openblas",
        }

    def _resolve_tbb_variant(self, ctx: BootstrapContext) -> dict:
        """TBB智能解析: Threading Building Blocks依赖平台和编译工具链。

        平台检测:
          - Windows       -> tbb需编译, 无build-essential时可能失败
          - Linux/macOS   -> tbb==2023.0.0 正常
          - 容器内        -> tbb可用但可选 (多线程已有Python GIL绕过方案)

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import platform
        import shutil
        system = platform.system().lower()

        # 检测容器环境
        env_profile = ctx.env_profile if ctx else {}
        is_container = bool(env_profile.get("container", {}).get("detected", False))

        # Windows: tbb需编译, 检查是否有编译工具
        if system == "windows":
            has_msvc = shutil.which("cl") is not None
            has_gcc = shutil.which("gcc") is not None
            if has_msvc or has_gcc:
                return {
                    "variant": "tbb",
                    "install_cmd": "pip install tbb==2023.0.0",
                    "reason": "Windows with compiler available, TBB install should succeed",
                    "degraded": False,
                }
            return {
                "variant": "none",
                "install_cmd": None,
                "reason": "Windows without compiler (MSVC/GCC), TBB build may fail, skipping",
                "degraded": True,
            }

        # 容器内: tbb可用但可选
        if is_container:
            return {
                "variant": "tbb_optional",
                "install_cmd": "pip install tbb==2023.0.0",
                "reason": "Container: TBB available but optional (Python GIL workarounds exist for multithreading)",
                "degraded": False,
            }

        # Linux/macOS: 正常安装
        return {
            "variant": "tbb",
            "install_cmd": "pip install tbb==2023.0.0",
            "reason": f"{system.capitalize()}: TBB for parallel algorithm acceleration",
            "degraded": False,
        }

    def _resolve_jieba_variant(self, ctx: BootstrapContext) -> dict:
        """jieba分词智能解析: Python纯实现, 跨平台通用。

        - jieba可用 -> 保留 (纯Python, 无编译依赖)
        - jieba不可用 -> 尝试pkuseg降级 (北大分词, 更准但更慢)
        - 都不可用 -> 标记degraded

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib

        # jieba可用 -> 首选
        try:
            importlib.import_module("jieba")
            return {
                "variant": "jieba",
                "install_cmd": None,
                "reason": "jieba available (pure Python, cross-platform)",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_jieba_variant: ImportError - <no detail>")

        # jieba不可用, 尝试pkuseg降级
        try:
            importlib.import_module("pkuseg")
            return {
                "variant": "pkuseg",
                "install_cmd": None,
                "reason": "jieba unavailable, pkuseg available as fallback (more accurate but slower)",
                "degraded": True,
            }
        except ImportError:
            logger.warning("_resolve_jieba_variant: ImportError - <no detail>")

        # 都不可用 -> degraded, 建议安装jieba
        return {
            "variant": "none",
            "install_cmd": "pip install jieba>=0.42.0",
            "reason": "Neither jieba nor pkuseg available, Chinese segmentation disabled",
            "degraded": True,
        }

    def _resolve_snownlp_variant(self, ctx: BootstrapContext) -> dict:
        """snownlp智能解析: Python纯实现, 跨平台通用。

        - snownlp依赖较小, 无平台差异, 无编译依赖
        - 可用 -> 保留; 不可用 -> 建议安装

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib

        try:
            importlib.import_module("snownlp")
            return {
                "variant": "snownlp",
                "install_cmd": None,
                "reason": "snownlp available (pure Python, cross-platform, minimal deps)",
                "degraded": False,
            }
        except ImportError:
            return {
                "variant": "none",
                "install_cmd": "pip install snownlp>=0.12.0",
                "reason": "snownlp unavailable, sentiment analysis disabled",
                "degraded": True,
            }

    def _resolve_tiktoken_variant(self, ctx: BootstrapContext) -> dict:
        """tiktoken智能解析: OpenAI BPE分词器, Rust实现核心。

        - Windows: 需要Rust编译或预编译wheel
        - Linux/macOS: 正常安装
        - 不可用时降级到tokenizers (HuggingFace, 同样Rust核心但wheel覆盖更广)

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform

        system = platform.system().lower()

        # tiktoken可用 -> 首选
        try:
            importlib.import_module("tiktoken")
            return {
                "variant": "tiktoken",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: tiktoken available (OpenAI BPE tokenizer)",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_tiktoken_variant: ImportError - <no detail>")

        # tiktoken不可用, 尝试tokenizers降级
        try:
            importlib.import_module("tokenizers")
            return {
                "variant": "tokenizers",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: tiktoken unavailable, tokenizers (HuggingFace) available as fallback",
                "degraded": True,
            }
        except ImportError:
            logger.warning("_resolve_tiktoken_variant: ImportError - <no detail>")

        # 都不可用 -> 根据平台给出安装建议
        if system == "windows":
            return {
                "variant": "none",
                "install_cmd": "pip install tiktoken",
                "reason": "Windows: neither tiktoken nor tokenizers available (requires Rust or prebuilt wheel)",
                "degraded": True,
            }

        return {
            "variant": "none",
            "install_cmd": "pip install tiktoken",
            "reason": f"{system.capitalize()}: neither tiktoken nor tokenizers available",
            "degraded": True,
        }

    def _resolve_pyzmq_variant(self, ctx: BootstrapContext) -> dict:
        """pyzmq智能解析: ZeroMQ Python绑定, 依赖libzmq C库。

        - Linux: 预编译wheel可用, 或需build-essential+libzmq3-dev
        - macOS: 预编译wheel可用
        - Windows: 需要Visual Studio编译或预编译wheel(仅x86_64)
        - Termux(Android): 需先pkg install libzmq

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import sys
        import platform

        system = platform.system().lower()

        # pyzmq已安装 -> 直接可用
        try:
            importlib.import_module("zmq")
            return {
                "variant": "pyzmq",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: pyzmq (libzmq binding) already available",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_pyzmq_variant: ImportError - <no detail>")

        # pyzmq未安装 -> 按平台给出安装建议
        # Termux/Android
        if system == "linux" and hasattr(sys, "getandroidapilevel"):
            return {
                "variant": "termux",
                "install_cmd": "pkg install libzmq && pip install pyzmq",
                "reason": "Termux/Android: libzmq must be installed via pkg before pyzmq",
                "degraded": False,
            }

        # Windows
        if sys.platform == "win32":
            import shutil
            machine = platform.machine().lower()
            has_compiler = shutil.which("cl") is not None  # MSVC
            if machine not in ("amd64", "x86_64"):
                return {
                    "variant": "none",
                    "install_cmd": "pip install pyzmq",
                    "reason": f"Windows {machine}: no pre-built wheel, requires Visual Studio build tools",
                    "degraded": True,
                }
            if has_compiler:
                return {
                    "variant": "compiled",
                    "install_cmd": "pip install pyzmq",
                    "reason": "Windows x86_64 with MSVC: pyzmq can be compiled from source",
                    "degraded": False,
                }
            return {
                "variant": "wheel",
                "install_cmd": "pip install pyzmq",
                "reason": "Windows x86_64 without MSVC: using pre-built wheel (pip auto-selects)",
                "degraded": False,
            }

        # Linux
        if system == "linux":
            import shutil
            has_compiler = shutil.which("gcc") is not None or shutil.which("cc") is not None
            if has_compiler:
                return {
                    "variant": "compiled",
                    "install_cmd": "pip install pyzmq",
                    "reason": "Linux with compiler: pyzmq can build from source or use pre-built wheel",
                    "degraded": False,
                }
            return {
                "variant": "wheel",
                "install_cmd": "pip install pyzmq",
                "reason": "Linux without compiler: using pre-built wheel (install build-essential+libzmq3-dev if build needed)",
                "degraded": False,
            }

        # macOS
        if system == "darwin":
            return {
                "variant": "wheel",
                "install_cmd": "pip install pyzmq",
                "reason": "macOS: pre-built wheel available for pyzmq",
                "degraded": False,
            }

        # 其他平台
        return {
            "variant": "wheel",
            "install_cmd": "pip install pyzmq",
            "reason": f"{system.capitalize()}: attempting pre-built wheel for pyzmq",
            "degraded": False,
        }

    def _resolve_uvloop_variant(self, ctx: BootstrapContext) -> dict:
        """uvloop智能解析: 高性能事件循环替代(Cython实现)。

        - Linux/macOS: uvloop可用, asyncio性能提升2-4x
        - Windows: uvloop不可用(无兼容实现), 自动回退asyncio默认循环
        - 容器内: uvloop可用但可选

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import sys
        import importlib

        # Windows: uvloop不支持, 直接回退
        if sys.platform == "win32":
            return {
                "variant": "none",
                "install_cmd": None,
                "reason": "Windows does not support uvloop, using asyncio default event loop (ProactorEventLoop)",
                "degraded": True,
            }

        # POSIX平台: 检查uvloop是否已安装
        try:
            importlib.import_module("uvloop")
            return {
                "variant": "uvloop",
                "install_cmd": None,
                "reason": "uvloop available, asyncio performance improved 2-4x over default loop",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_uvloop_variant: ImportError - <no detail>")

        # uvloop未安装, 给出安装建议
        import platform
        system = platform.system().lower()

        # 容器环境检测
        import os
        in_container = (
            os.path.exists("/.dockerenv")
            or os.path.exists("/run/.containerenv")
            or "KUBERNETES_SERVICE_HOST" in os.environ
        )

        if in_container:
            return {
                "variant": "optional",
                "install_cmd": "pip install uvloop>=0.19.0",
                "reason": "Container environment: uvloop available but optional (install for 2-4x asyncio speedup)",
                "degraded": False,
            }

        # Linux/macOS非容器: 推荐安装
        return {
            "variant": "uvloop",
            "install_cmd": "pip install uvloop>=0.19.0",
            "reason": f"{system.capitalize()}: uvloop recommended for 2-4x faster asyncio event loop",
            "degraded": False,
        }
    def _resolve_pillow_variant(self, ctx: BootstrapContext) -> dict:
        """Pillow智能解析: Python图像库, 跨平台通用但有平台差异。

        平台检测:
          - Linux: 需libjpeg/libpng系统库(或预编译wheel)
          - Windows/macOS: 预编译wheel可用
          - ARM: 预编译wheel可能缺失, 需编译

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform
        import shutil

        system = platform.system().lower()
        machine = platform.machine().lower()

        # Pillow已安装 -> 直接可用
        try:
            importlib.import_module("PIL")
            return {
                "variant": "pillow",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: Pillow already available",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_pillow_variant: ImportError - <no detail>")

        # Pillow未安装 -> 按平台给出安装建议
        # Linux: 需系统库或预编译wheel
        if system == "linux":
            has_compiler = shutil.which("gcc") is not None or shutil.which("cc") is not None
            if machine in ("aarch64", "arm64"):
                if has_compiler:
                    return {
                        "variant": "compiled",
                        "install_cmd": "pip install Pillow>=10.0.0",
                        "reason": "Linux ARM with compiler: Pillow can build from source (ensure libjpeg-dev/libpng-dev installed)",
                        "degraded": False,
                    }
                return {
                    "variant": "wheel",
                    "install_cmd": "pip install Pillow>=10.0.0",
                    "reason": "Linux ARM without compiler: attempting pre-built wheel (may fail, install gcc+libjpeg-dev+libpng-dev if needed)",
                    "degraded": True,
                }
            if has_compiler:
                return {
                    "variant": "compiled",
                    "install_cmd": "pip install Pillow>=10.0.0",
                    "reason": "Linux with compiler: Pillow can build from source (ensure libjpeg-dev/libpng-dev installed)",
                    "degraded": False,
                }
            return {
                "variant": "wheel",
                "install_cmd": "pip install Pillow>=10.0.0",
                "reason": "Linux without compiler: using pre-built wheel (install libjpeg-dev+libpng-dev if build needed)",
                "degraded": False,
            }

        # Windows ARM: 预编译wheel可能缺失
        if system == "windows" and machine not in ("amd64", "x86_64"):
            return {
                "variant": "none",
                "install_cmd": "pip install Pillow>=10.0.0",
                "reason": f"Windows {machine}: no pre-built Pillow wheel, may require compilation",
                "degraded": True,
            }

        # Windows x86_64 / macOS: 预编译wheel可用
        return {
            "variant": "wheel",
            "install_cmd": "pip install Pillow>=10.0.0",
            "reason": f"{system.capitalize()}: pre-built Pillow wheel available",
            "degraded": False,
        }

    def _resolve_aiohttp_variant(self, ctx: BootstrapContext) -> dict:
        """aiohttp智能解析: 异步HTTP客户端/服务端, 需C扩展编译。

        平台检测:
          - 有gcc/MSVC -> 编译安装(性能最优)
          - 无编译器 -> 预编译wheel(仅x86_64)
          - Windows ARM -> 可能无预编译wheel, degraded

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform
        import shutil

        system = platform.system().lower()
        machine = platform.machine().lower()

        # aiohttp已安装 -> 直接可用
        try:
            importlib.import_module("aiohttp")
            return {
                "variant": "aiohttp",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: aiohttp already available",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_aiohttp_variant: ImportError - <no detail>")

        # aiohttp未安装 -> 按平台给出安装建议
        # Windows ARM: 可能无预编译wheel
        if system == "windows" and machine not in ("amd64", "x86_64"):
            has_msvc = shutil.which("cl") is not None
            has_gcc = shutil.which("gcc") is not None
            if has_msvc or has_gcc:
                return {
                    "variant": "compiled",
                    "install_cmd": "pip install aiohttp>=3.9.0",
                    "reason": f"Windows {machine} with compiler: aiohttp can build from source",
                    "degraded": False,
                }
            return {
                "variant": "none",
                "install_cmd": "pip install aiohttp>=3.9.0",
                "reason": f"Windows {machine}: no pre-built aiohttp wheel and no compiler, install may fail",
                "degraded": True,
            }

        # 检测编译器
        if system == "windows":
            has_compiler = shutil.which("cl") is not None
        else:
            has_compiler = shutil.which("gcc") is not None or shutil.which("cc") is not None

        if has_compiler:
            return {
                "variant": "compiled",
                "install_cmd": "pip install aiohttp>=3.9.0",
                "reason": f"{system.capitalize()} with compiler: aiohttp C extensions will be compiled (optimal performance)",
                "degraded": False,
            }

        # 无编译器 -> 预编译wheel
        if machine in ("x86_64", "amd64"):
            return {
                "variant": "wheel",
                "install_cmd": "pip install aiohttp>=3.9.0",
                "reason": f"{system.capitalize()} x86_64 without compiler: using pre-built wheel",
                "degraded": False,
            }

        # ARM无编译器: 可能无预编译wheel
        return {
            "variant": "wheel",
            "install_cmd": "pip install aiohttp>=3.9.0",
            "reason": f"{system.capitalize()} {machine} without compiler: pre-built wheel may not exist, install gcc if possible",
            "degraded": True,
        }

    def _resolve_httpx_variant(self, ctx: BootstrapContext) -> dict:
        """httpx智能解析: 纯Python HTTP客户端+httpcore(C扩展)。

        httpx是纯Python+httpcore(C扩展), 跨平台通用, 无特殊处理。

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform

        system = platform.system().lower()

        # httpx已安装 -> 直接可用
        try:
            importlib.import_module("httpx")
            return {
                "variant": "httpx",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: httpx already available",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_httpx_variant: ImportError - <no detail>")

        # httpx未安装 -> 通用安装
        return {
            "variant": "httpx",
            "install_cmd": "pip install httpx>=0.27.0",
            "reason": f"{system.capitalize()}: httpx is pure Python + httpcore (cross-platform, no special handling)",
            "degraded": False,
        }

    def _resolve_orjson_variant(self, ctx: BootstrapContext) -> dict:
        """orjson智能解析: Rust实现的JSON库, 需要Rust编译或预编译wheel。

        平台检测:
          - Linux x86_64/ARM64: 预编译wheel可用
          - Windows: 预编译wheel可用(x86_64)
          - Windows ARM: 可能无预编译wheel, 降级到json标准库
          - 不可用 -> 降级ujson或标准json

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform

        system = platform.system().lower()
        machine = platform.machine().lower()

        # orjson已安装 -> 直接可用
        try:
            importlib.import_module("orjson")
            return {
                "variant": "orjson",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: orjson already available (Rust-based, 2-3x faster than stdlib json)",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_orjson_variant: ImportError - <no detail>")

        # orjson未安装 -> 按平台给出安装建议
        # Windows ARM: 可能无预编译wheel
        if system == "windows" and machine not in ("amd64", "x86_64"):
            # 尝试ujson降级
            try:
                importlib.import_module("ujson")
                return {
                    "variant": "ujson",
                    "install_cmd": None,
                    "reason": f"Windows {machine}: orjson pre-built wheel unavailable, ujson available as fallback",
                    "degraded": True,
                }
            except ImportError:
                logger.warning("_resolve_orjson_variant: ImportError - <no detail>")

            return {
                "variant": "stdlib_json",
                "install_cmd": None,
                "reason": f"Windows {machine}: orjson/ujson unavailable, falling back to stdlib json",
                "degraded": True,
            }

        # Linux x86_64/ARM64, Windows x86_64, macOS: 预编译wheel可用
        if machine in ("x86_64", "amd64", "aarch64", "arm64"):
            return {
                "variant": "orjson",
                "install_cmd": "pip install orjson>=3.9.0",
                "reason": f"{system.capitalize()} {machine}: orjson pre-built wheel available (Rust-based, 2-3x faster than stdlib json)",
                "degraded": False,
            }

        # 其他平台: 尝试ujson降级
        try:
            importlib.import_module("ujson")
            return {
                "variant": "ujson",
                "install_cmd": None,
                "reason": f"{system.capitalize()} {machine}: orjson pre-built wheel may not exist, ujson available as fallback",
                "degraded": True,
            }
        except ImportError:
            logger.warning("_resolve_orjson_variant: ImportError - <no detail>")

        return {
            "variant": "stdlib_json",
            "install_cmd": None,
            "reason": f"{system.capitalize()} {machine}: orjson/ujson unavailable, falling back to stdlib json",
            "degraded": True,
        }

    def _resolve_polars_variant(self, ctx: BootstrapContext) -> dict:
        """polars智能解析: Rust实现的DataFrame库。

        预编译wheel覆盖:
          - Linux x86_64/ARM64
          - macOS x86_64/ARM64
          - Windows x86_64
        Windows ARM: 可能无预编译wheel, 降级pandas

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform

        system = platform.system().lower()
        machine = platform.machine().lower()

        # polars已安装 -> 直接可用
        try:
            importlib.import_module("polars")
            return {
                "variant": "polars",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: polars already available (Rust-based DataFrame, 5-10x faster than pandas)",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_polars_variant: ImportError - <no detail>")

        # polars未安装 -> 按平台给出安装建议
        # Windows ARM: 可能无预编译wheel, 降级pandas
        if system == "windows" and machine not in ("amd64", "x86_64"):
            try:
                importlib.import_module("pandas")
                return {
                    "variant": "pandas",
                    "install_cmd": None,
                    "reason": f"Windows {machine}: polars pre-built wheel unavailable, pandas available as fallback",
                    "degraded": True,
                }
            except ImportError:
                logger.warning("_resolve_polars_variant: ImportError - <no detail>")

            return {
                "variant": "pandas",
                "install_cmd": "pip install pandas>=2.0.0",
                "reason": f"Windows {machine}: polars pre-built wheel unavailable, falling back to pandas",
                "degraded": True,
            }

        # 主流平台: 预编译wheel可用
        if machine in ("x86_64", "amd64", "aarch64", "arm64"):
            return {
                "variant": "polars",
                "install_cmd": "pip install polars>=0.20.0",
                "reason": f"{system.capitalize()} {machine}: polars pre-built wheel available (Rust-based DataFrame)",
                "degraded": False,
            }

        # 其他平台: 降级pandas
        try:
            importlib.import_module("pandas")
            return {
                "variant": "pandas",
                "install_cmd": None,
                "reason": f"{system.capitalize()} {machine}: polars pre-built wheel may not exist, pandas available as fallback",
                "degraded": True,
            }
        except ImportError:
            logger.warning("_resolve_polars_variant: ImportError - <no detail>")

        return {
            "variant": "pandas",
            "install_cmd": "pip install pandas>=2.0.0",
            "reason": f"{system.capitalize()} {machine}: polars unavailable, falling back to pandas",
            "degraded": True,
        }

    def _resolve_duckdb_variant(self, ctx: BootstrapContext) -> dict:
        """duckdb智能解析: C++实现的嵌入式分析DB。

        预编译wheel覆盖主流平台。
        ARM/冷门平台: 可能无预编译wheel。
        不可用 -> 降级sqlite+polars

        Returns:
            dict with keys: variant, install_cmd, reason, degraded
        """
        import importlib
        import platform

        system = platform.system().lower()
        machine = platform.machine().lower()

        # duckdb已安装 -> 直接可用
        try:
            importlib.import_module("duckdb")
            return {
                "variant": "duckdb",
                "install_cmd": None,
                "reason": f"{system.capitalize()}: duckdb already available (C++ embedded analytical DB)",
                "degraded": False,
            }
        except ImportError:
            logger.warning("_resolve_duckdb_variant: ImportError - <no detail>")

        # duckdb未安装 -> 按平台给出安装建议
        # 主流平台: 预编译wheel可用
        if machine in ("x86_64", "amd64"):
            return {
                "variant": "duckdb",
                "install_cmd": "pip install duckdb>=0.10.0",
                "reason": f"{system.capitalize()} x86_64: duckdb pre-built wheel available (C++ embedded analytical DB)",
                "degraded": False,
            }

        if system == "linux" and machine in ("aarch64", "arm64"):
            return {
                "variant": "duckdb",
                "install_cmd": "pip install duckdb>=0.10.0",
                "reason": "Linux ARM64: duckdb pre-built wheel available",
                "degraded": False,
            }

        if system == "darwin" and machine in ("x86_64", "arm64", "aarch64"):
            return {
                "variant": "duckdb",
                "install_cmd": "pip install duckdb>=0.10.0",
                "reason": f"macOS {machine}: duckdb pre-built wheel available",
                "degraded": False,
            }

        # Windows ARM / 冷门平台: 可能无预编译wheel, 降级sqlite+polars
        # 检查polars是否可用
        polars_available = False
        try:
            importlib.import_module("polars")
            polars_available = True
        except ImportError:
            logger.warning("_resolve_duckdb_variant: ImportError - <no detail>")

        if polars_available:
            return {
                "variant": "sqlite_polars",
                "install_cmd": None,
                "reason": f"{system.capitalize()} {machine}: duckdb pre-built wheel may not exist, falling back to sqlite+polars",
                "degraded": True,
            }

        return {
            "variant": "sqlite_pandas",
            "install_cmd": "pip install pandas>=2.0.0",
            "reason": f"{system.capitalize()} {machine}: duckdb unavailable, falling back to sqlite+pandas",
            "degraded": True,
        }

    def _resolve_onnxruntime_variant(self, ctx: BootstrapContext) -> dict:
        """onnxruntime智能解析: 根据平台和GPU选择Provider。

        - Linux+GPU -> onnxruntime-gpu (CUDA加速)
        - macOS -> onnxruntime+CoreMLExecutionProvider
        - Windows/Linux无GPU -> onnxruntime (CPU)
        - Android -> onnxruntime+NNAPI delegate
        """
        import sys
        import platform
        gpu_info = ctx.env_profile.get("gpu", {})
        system = platform.system().lower()

        # Android -> NNAPI delegate
        if system == "linux" and hasattr(sys, "getandroidapilevel"):
            return {
                "variant": "nnapi",
                "install_cmd": "pip install onnxruntime>=1.15.0",
                "reason": "Android platform, using NNAPI delegate for hardware acceleration",
                "providers": ["NNAPIExecutionProvider", "CPUExecutionProvider"],
                "degraded": False,
            }

        # Linux+GPU -> CUDA加速
        if system == "linux" and gpu_info.get("available", False):
            return {
                "variant": "gpu",
                "install_cmd": "pip install onnxruntime-gpu>=1.15.0",
                "reason": "Linux with GPU, using CUDA/TensorRT providers for acceleration",
                "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                "degraded": False,
            }

        # macOS -> CoreML ExecutionProvider
        if sys.platform == "darwin":
            if platform.machine() == "arm64":
                return {
                    "variant": "coreml",
                    "install_cmd": "pip install onnxruntime>=1.15.0",
                    "reason": "Apple Silicon, using CoreML ExecutionProvider",
                    "providers": ["CoreMLExecutionProvider", "CPUExecutionProvider"],
                    "degraded": False,
                }
            return {
                "variant": "cpu",
                "install_cmd": "pip install onnxruntime>=1.15.0",
                "reason": "macOS Intel, using CPU providers",
                "providers": ["CPUExecutionProvider"],
                "degraded": False,
            }

        # Windows/Linux无GPU -> CPU
        return {
            "variant": "cpu",
            "install_cmd": "pip install onnxruntime>=1.15.0",
            "reason": "No GPU available, using CPU-only providers",
            "providers": ["CPUExecutionProvider"],
            "degraded": False,
        }

    def _resolve_ncps_variant(self, ctx: BootstrapContext) -> dict:
        """ncps智能解析: Neural Circuit Policies, 依赖torch。

        - torch不可用 -> 标记degraded, 跳过ncps
        - torch可用 -> 正常安装ncps
        """
        try:
            import torch
            return {
                "variant": "ncps",
                "install_cmd": "pip install ncps>=1.0.0",
                "reason": "torch available, ncps (Neural Circuit Policies) enabled",
                "degraded": False,
            }
        except ImportError:
            return {
                "variant": "none",
                "install_cmd": None,
                "reason": "torch not available, ncps skipped (degraded to simple memory mode)",
                "degraded": True,
            }

    def _resolve_sklearn_variant(self, ctx: BootstrapContext) -> dict:
        """scikit-learn智能解析: 跟随numpy的BLAS后端。

        - 有MKL -> sklearn+mkl优化
        - 无MKL -> sklearn默认
        """
        numpy_resolution = self._resolve_numpy_variant(ctx)
        numpy_variant = numpy_resolution.get("variant", "openblas")

        if numpy_variant == "mkl":
            return {
                "variant": "mkl",
                "install_cmd": "pip install scikit-learn>=1.3.0",
                "reason": "numpy+mkl selected, sklearn+mkl for optimized linear algebra",
                "degraded": False,
            }

        return {
            "variant": "default",
            "install_cmd": "pip install scikit-learn>=1.3.0",
            "reason": "numpy without MKL, using default scikit-learn (OpenBLAS)",
            "degraded": False,
        }

    def _resolve_nlp_variant(self, ctx: BootstrapContext) -> dict:
        """中文NLP智能解析: 根据语言需求安装。

        - 中文环境(LANG/zh_CN) -> jieba + snownlp
        - 非中文环境 -> 可选安装
        """
        import os
        import locale

        # 检测中文环境
        lang = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "") or ""
        try:
            loc = locale.getdefaultlocale()[0] or ""
        except Exception:
            loc = ""

        is_chinese = "zh" in lang.lower() or "zh" in loc.lower() or "chinese" in lang.lower()

        if is_chinese:
            return {
                "variant": "chinese",
                "install": ["jieba>=0.42.0", "snownlp>=0.12.0"],
                "reason": "Chinese environment detected, installing Chinese NLP packages",
            }

        return {
            "variant": "optional",
            "install": ["jieba>=0.42.0", "snownlp>=0.12.0"],
            "reason": "Chinese NLP packages (optional for non-Chinese environments)",
            "optional": True,
        }

    async def execute(self, ctx: BootstrapContext) -> CapabilityResult:
        """执行依赖健康检查。

        Args:
            ctx: 共享上下文

        Returns:
            包含 total/installed/missing/groups 的健康报告
        """
        start = time.perf_counter()
        try:
            from galaxyos.config.auto_dep_manager import get_health_report
            data = get_health_report()
            ctx.dep_status = data
            elapsed = (time.perf_counter() - start) * 1000

            degraded = bool(data.get("missing_core"))
            note = ""
            if degraded:
                missing_names = [d["name"] for d in data.get("missing_core", []) if isinstance(d, dict)]
                note = f"Missing core deps: {', '.join(missing_names)}"

            # 智能解析 torch/faiss 变体
            torch_resolution = self._resolve_torch_variant(ctx)
            faiss_resolution = self._resolve_faiss_variant(ctx)

            data["torch_resolution"] = torch_resolution
            data["faiss_resolution"] = faiss_resolution

            # ML/AI核心依赖智能解析
            result_numpy = self._resolve_numpy_variant(ctx)
            result_scipy = self._resolve_scipy_variant(ctx)
            result_mkl = self._resolve_mkl_variant(ctx)
            result_tbb = self._resolve_tbb_variant(ctx)
            result_uvloop = self._resolve_uvloop_variant(ctx)
            result_onnxruntime = self._resolve_onnxruntime_variant(ctx)
            result_pyzmq = self._resolve_pyzmq_variant(ctx)
            result_ncps = self._resolve_ncps_variant(ctx)
            result_sklearn = self._resolve_sklearn_variant(ctx)
            result_nlp = self._resolve_nlp_variant(ctx)
            result_jieba = self._resolve_jieba_variant(ctx)
            result_snownlp = self._resolve_snownlp_variant(ctx)
            result_tiktoken = self._resolve_tiktoken_variant(ctx)
            result_pillow = self._resolve_pillow_variant(ctx)
            result_aiohttp = self._resolve_aiohttp_variant(ctx)
            result_httpx = self._resolve_httpx_variant(ctx)
            result_orjson = self._resolve_orjson_variant(ctx)
            result_polars = self._resolve_polars_variant(ctx)
            result_duckdb = self._resolve_duckdb_variant(ctx)

            data["numpy_resolution"] = result_numpy
            data["scipy_resolution"] = result_scipy
            data["mkl_resolution"] = result_mkl
            data["tbb_resolution"] = result_tbb
            data["uvloop_resolution"] = result_uvloop
            data["onnxruntime_resolution"] = result_onnxruntime
            data["pyzmq_resolution"] = result_pyzmq
            data["ncps_resolution"] = result_ncps
            data["sklearn_resolution"] = result_sklearn
            data["nlp_resolution"] = result_nlp
            data["jieba_resolution"] = result_jieba
            data["snownlp_resolution"] = result_snownlp
            data["tiktoken_resolution"] = result_tiktoken
            data["pillow_resolution"] = result_pillow
            data["aiohttp_resolution"] = result_aiohttp
            data["httpx_resolution"] = result_httpx
            data["orjson_resolution"] = result_orjson
            data["polars_resolution"] = result_polars
            data["duckdb_resolution"] = result_duckdb

            if torch_resolution.get("degraded"):
                degraded = True
                note = note or torch_resolution["reason"]
            if result_uvloop.get("degraded"):
                degraded = True
                note = note or result_uvloop["reason"]
            if result_ncps.get("degraded"):
                degraded = True
                note = note or result_ncps["reason"]
            if result_mkl.get("degraded"):
                degraded = True
                note = note or result_mkl["reason"]
            if result_tbb.get("degraded"):
                degraded = True
                note = note or result_tbb["reason"]
            if result_jieba.get("degraded"):
                degraded = True
                note = note or result_jieba["reason"]
            if result_snownlp.get("degraded"):
                degraded = True
                note = note or result_snownlp["reason"]
            if result_tiktoken.get("degraded"):
                degraded = True
                note = note or result_tiktoken["reason"]
            if result_pyzmq.get("degraded"):
                degraded = True
                note = note or result_pyzmq["reason"]
            if result_pillow.get("degraded"):
                degraded = True
                note = note or result_pillow["reason"]
            if result_aiohttp.get("degraded"):
                degraded = True
                note = note or result_aiohttp["reason"]
            if result_orjson.get("degraded"):
                degraded = True
                note = note or result_orjson["reason"]
            if result_polars.get("degraded"):
                degraded = True
                note = note or result_polars["reason"]
            if result_duckdb.get("degraded"):
                degraded = True
                note = note or result_duckdb["reason"]

            return CapabilityResult(
                name=self.name,
                success=True,
                degraded=degraded,
                degradation_note=note,
                data=data,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning(f"execute: Exception - {exc}")
            return CapabilityResult(
                name=self.name,
                success=False,
                errors=[str(exc)],
                duration_ms=elapsed,
            )

    async def check(self, ctx: BootstrapContext) -> CapabilityResult:
        """Dry-run 依赖检查 (与 execute 相同, 依赖检查本身只读)。

        Args:
            ctx: 共享上下文

        Returns:
            健康报告
        """
        return await self.execute(ctx)


class VersionCapability(Capability):
    """版本一致性校验能力 -- 封装 version.check_version_consistency。

    校验 __init__.py / setup.py / package.json 版本号是否一致。
    """

    name = "version"
    description = "Version consistency check"
    requires: List[str] = []

    async def execute(self, ctx: BootstrapContext) -> CapabilityResult:
        """执行版本一致性校验。

        Args:
            ctx: 共享上下文

        Returns:
            包含 consistent/sources 的校验结果
        """
        start = time.perf_counter()
        try:
            from galaxyos.config.version import check_version_consistency
            data = check_version_consistency()
            ctx.ver_info = data
            elapsed = (time.perf_counter() - start) * 1000

            warnings: List[str] = []
            if not data.get("consistent"):
                warnings.append("Version mismatch detected across sources")

            return CapabilityResult(
                name=self.name,
                success=True,
                data=data,
                warnings=warnings,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning(f"execute: Exception - {exc}")
            return CapabilityResult(
                name=self.name,
                success=False,
                errors=[str(exc)],
                duration_ms=elapsed,
            )

    async def check(self, ctx: BootstrapContext) -> CapabilityResult:
        """Dry-run 版本校验 (与 execute 相同, 版本检查本身只读)。

        Args:
            ctx: 共享上下文

        Returns:
            校验结果
        """
        return await self.execute(ctx)


class UpdateCapability(Capability):
    """按需更新能力 -- 封装 on_demand_updater.OnDemandUpdater。

    默认只做 status 检查, 不自动拉取更新。
    需显式启用或在 execute 中指定 action。
    """

    name = "update"
    description = "On-demand update check"
    requires: List[str] = ["env", "version"]

    async def execute(self, ctx: BootstrapContext) -> CapabilityResult:
        """执行更新状态检查 (默认只检查, 不拉取)。

        Args:
            ctx: 共享上下文

        Returns:
            包含 version/commit/remotes/branch/dirty 的状态信息
        """
        start = time.perf_counter()
        try:
            # [LAYER-FIX] Lazy import (was upward ref to galaxyos.operations.monitor.on_demand_updater)
            try:
                import importlib as _lazy_mod_5506_il
                _lazy_mod_5506 = _lazy_mod_5506_il.import_module('galaxyos.operations.monitor.on_demand_updater')
                OnDemandUpdater = _lazy_mod_5506.OnDemandUpdater
            except (ImportError, AttributeError):
                OnDemandUpdater = None
            updater = OnDemandUpdater()
            data = updater.status()
            ctx.update_plan = data
            elapsed = (time.perf_counter() - start) * 1000
            return CapabilityResult(
                name=self.name,
                success=True,
                data=data,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning(f"execute: Exception - {exc}")
            return CapabilityResult(
                name=self.name,
                success=False,
                errors=[str(exc)],
                duration_ms=elapsed,
            )

    async def check(self, ctx: BootstrapContext) -> CapabilityResult:
        """Dry-run 更新检查 (只获取状态, 不做任何修改)。

        Args:
            ctx: 共享上下文

        Returns:
            更新状态
        """
        return await self.execute(ctx)


# ═══════════════════════════════════════════════════════════════════
#  GalaxyBootstrap 编排器
# ═══════════════════════════════════════════════════════════════════

class GalaxyBootstrap:
    """一键三举编排器 -- 按依赖拓扑排序执行各 Capability。

    支持:
      - 依赖拓扑排序 (分层, 同层可并行)
      - 串行执行 (run)
      - 并行执行 (run_parallel, 同层无依赖的能力并行)
      - 断点续举 (run_from, 从指定 checkpoint 继续)
      - Dry-run 模式 (只检测不修改)
      - 配置文件加载 (YAML)

    Args:
        profile:     运行环境 (dev/staging/prod)
        dry_run:     只检测不修改
        config_path: 配置文件路径 (可选)
    """

    def __init__(
        self,
        profile: str = "dev",
        dry_run: bool = False,
        config_path: Optional[str] = None,
    ) -> None:
        self.ctx = BootstrapContext(profile=profile, dry_run=dry_run)
        self.capabilities: Dict[str, Capability] = {}
        self._config: Dict[str, Any] = {}

        # ── 应用 Profile 预设 ──
        profile_cfg = PROFILE_CONFIGS.get(profile)
        if profile_cfg is not None:
            # Profile dry_run 仅在用户未显式指定时生效
            # (user-supplied dry_run param takes precedence)
            self.ctx.dry_run = dry_run or profile_cfg.get("dry_run", False)
            # 将 profile 的 capability 配置合并到 _config
            self._config.setdefault("bootstrap", {})["capabilities"] = (
                profile_cfg.get("capabilities", {})
            )
            logger.info("Applied profile '%s': %s", profile, profile_cfg.get("description", ""))

        self._load_config(config_path)

    def _load_config(self, config_path: Optional[str]) -> None:
        """加载 YAML 配置文件。

        Args:
            config_path: 配置文件路径, None 则跳过
        """
        if config_path is None:
            return

        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            logger.info("Loaded bootstrap config from: %s", config_path)

            # 从配置覆盖 profile / dry_run
            bs_cfg = self._config.get("bootstrap", {})
            if "profile" in bs_cfg and self.ctx.profile == "dev":
                self.ctx.profile = bs_cfg["profile"]
            if "dry_run" in bs_cfg and not self.ctx.dry_run:
                self.ctx.dry_run = bs_cfg["dry_run"]

        except ImportError:
            logger.warning("PyYAML not installed, config loading skipped")
        except FileNotFoundError:
            logger.warning("Config file not found: %s", config_path)
        except Exception as exc:
            logger.warning("Failed to load config: %s", exc)

    def _is_capability_enabled(self, name: str) -> bool:
        """检查 capability 是否在配置中启用。

        Args:
            name: capability 名称

        Returns:
            未配置时默认 True, 配置存在时按 enabled 字段判断
        """
        caps_cfg = self._config.get("bootstrap", {}).get("capabilities", {})
        if name not in caps_cfg:
            return True
        return caps_cfg[name].get("enabled", True)

    def register(self, cap: Capability) -> None:
        """注册一个 Capability 插件。

        Args:
            cap: Capability 实例

        Raises:
            ValueError: 名称重复或名称为空
        """
        if not cap.name:
            raise ValueError("Capability name must not be empty")
        if cap.name in self.capabilities:
            raise ValueError(f"Capability '{cap.name}' already registered")
        self.capabilities[cap.name] = cap
        logger.debug("Registered capability: %s (requires: %s)", cap.name, cap.requires)

    def _topological_sort(self) -> List[List[str]]:
        """按依赖关系分层拓扑排序。

        返回分层列表, 同层内的 capability 无相互依赖, 可并行执行。

        Returns:
            分层列表, 如 [["env", "dep"], ["version"], ["update"]]

        Raises:
            RuntimeError: 检测到循环依赖
        """
        # 构建入度表
        in_degree: Dict[str, int] = {name: 0 for name in self.capabilities}
        dependents: Dict[str, List[str]] = {name: [] for name in self.capabilities}

        for name, cap in self.capabilities.items():
            for req in cap.requires:
                if req not in self.capabilities:
                    logger.warning(
                        "Capability '%s' requires '%s' which is not registered; skipping",
                        name, req,
                    )
                    continue
                in_degree[name] += 1
                dependents[req].append(name)

        # Kahn 算法分层
        layers: List[List[str]] = []
        remaining = set(self.capabilities.keys())

        while remaining:
            # 当前层: 入度为 0 的节点
            current_layer = [n for n in remaining if in_degree[n] == 0]
            if not current_layer:
                raise RuntimeError(
                    f"Circular dependency detected among: {sorted(remaining)}"
                )

            layers.append(sorted(current_layer))

            for node in current_layer:
                remaining.remove(node)
                for dep in dependents[node]:
                    in_degree[dep] -= 1

        return layers

    async def _execute_capability(self, cap: Capability) -> CapabilityResult:
        """执行单个 capability, 包含错误隔离、enabled 检查和降级链。

        当主能力执行失败时, 按 cap.fallbacks 列表依次尝试降级能力。
        降级能力需已注册到 self.capabilities 中。

        Args:
            cap: Capability 实例

        Returns:
            执行结果 (主能力成功则返回主结果, 否则返回首个成功的降级结果)
        """
        # 检查是否在配置中被禁用
        if not self._is_capability_enabled(cap.name):
            return CapabilityResult(
                name=cap.name,
                success=True,
                degraded=True,
                degradation_note="Disabled in config",
                duration_ms=0.0,
            )

        # 检查前置依赖
        if not cap.can_run(self.ctx):
            missing = [r for r in cap.requires if r not in self.ctx.completed]
            return CapabilityResult(
                name=cap.name,
                success=False,
                errors=[f"Prerequisites not met: {', '.join(missing)}"],
                duration_ms=0.0,
            )

        # 执行 (try/except 隔离)
        try:
            if self.ctx.dry_run:
                result = await cap.check(self.ctx)
            else:
                result = await cap.execute(self.ctx)
        except Exception as exc:
            logger.warning(f"_execute_capability: Exception - {exc}")
            result = CapabilityResult(
                name=cap.name,
                success=False,
                errors=[f"Unhandled exception: {exc}"],
            )

        # 标记完成
        if result.success:
            self.ctx.completed.add(cap.name)

        # 收集全局错误/警告
        for err in result.errors:
            self.ctx.errors.append(f"[{cap.name}] {err}")
        for warn in result.warnings:
            self.ctx.warnings.append(f"[{cap.name}] {warn}")

        # ── 降级链: 主能力失败时依次尝试 fallback ──
        if not result.success and cap.fallbacks:
            for fallback_name in cap.fallbacks:
                fallback_cap = self.capabilities.get(fallback_name)
                if fallback_cap is None:
                    logger.warning(
                        "Fallback '%s' for '%s' not registered, skipping",
                        fallback_name, cap.name,
                    )
                    continue

                if not self._is_capability_enabled(fallback_name):
                    logger.info("Fallback '%s' disabled in config, skipping", fallback_name)
                    continue

                logger.info(
                    "Capability '%s' failed, trying fallback '%s'",
                    cap.name, fallback_name,
                )
                try:
                    if self.ctx.dry_run:
                        fallback_result = await fallback_cap.check(self.ctx)
                    else:
                        fallback_result = await fallback_cap.execute(self.ctx)
                except Exception as exc:
                    logger.warning("Fallback '%s' also failed: %s", fallback_name, exc)
                    continue

                if fallback_result.success:
                    # 降级成功: 标记主能力为 degraded
                    result = CapabilityResult(
                        name=cap.name,
                        success=True,
                        degraded=True,
                        degradation_note=(
                            f"Fallback to '{fallback_name}': "
                            f"{fallback_result.degradation_note or 'OK'}"
                        ),
                        data=fallback_result.data,
                        warnings=fallback_result.warnings,
                        duration_ms=result.duration_ms + fallback_result.duration_ms,
                    )
                    self.ctx.completed.add(cap.name)
                    self.ctx.warnings.append(
                        f"[{cap.name}] Degraded: fallback to '{fallback_name}'"
                    )
                    break
                else:
                    logger.warning(
                        "Fallback '%s' for '%s' also failed",
                        fallback_name, cap.name,
                    )

        return result

    async def run(
        self,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> BootstrapReport:
        """按依赖拓扑排序串行执行所有 Capability。

        Args:
            progress_callback: 进度回调函数, 签名 (name: str, progress: float) -> None
                               name:     当前正在执行的 capability 名称
                               progress: 总体进度 0.0~1.0

        Returns:
            编排报告
        """
        start = time.perf_counter()
        results: Dict[str, CapabilityResult] = {}

        layers = self._topological_sort()
        logger.info("Bootstrap execution plan: %s", layers)

        # 计算总 capability 数量用于进度计算
        total_caps = sum(len(layer) for layer in layers)
        completed_count = 0

        for layer in layers:
            for name in layer:
                cap = self.capabilities[name]
                logger.info("Executing capability: %s", name)

                # 进度回调: 开始执行
                if progress_callback is not None:
                    progress_callback(name, completed_count / max(total_caps, 1))

                result = await self._execute_capability(cap)
                results[name] = result
                completed_count += 1

                if result.success:
                    logger.info(
                        "Capability '%s' completed (%.1fms)",
                        name, result.duration_ms,
                    )
                else:
                    logger.warning(
                        "Capability '%s' failed: %s",
                        name, result.errors,
                    )

                # 进度回调: 完成执行
                if progress_callback is not None:
                    progress_callback(name, completed_count / max(total_caps, 1))

        elapsed = (time.perf_counter() - start) * 1000
        success = all(r.success for r in results.values())

        return BootstrapReport(
            success=success,
            total_duration_ms=elapsed,
            results=results,
            context=self.ctx,
        )

    async def run_parallel(self) -> BootstrapReport:
        """按依赖拓扑排序执行, 同层无依赖的 Capability 并行执行。

        Returns:
            编排报告
        """
        import asyncio

        start = time.perf_counter()
        results: Dict[str, CapabilityResult] = {}

        layers = self._topological_sort()
        logger.info("Bootstrap parallel execution plan: %s", layers)

        for layer in layers:
            # 同层并行执行
            tasks = []
            for name in layer:
                cap = self.capabilities[name]
                tasks.append(self._execute_capability(cap))

            layer_results = await asyncio.gather(*tasks, return_exceptions=True)

            for name, r in zip(layer, layer_results):
                if isinstance(r, Exception):
                    # asyncio.gather return_exceptions=True 返回异常对象
                    result = CapabilityResult(
                        name=name,
                        success=False,
                        errors=[f"Exception in parallel execution: {r}"],
                    )
                    self.ctx.errors.append(f"[{name}] {r}")
                else:
                    result = r
                results[name] = result

        elapsed = (time.perf_counter() - start) * 1000
        success = all(r.success for r in results.values())

        return BootstrapReport(
            success=success,
            total_duration_ms=elapsed,
            results=results,
            context=self.ctx,
        )

    async def run_from(
        self,
        checkpoint: Set[str],
        checkpoint_file: Optional[str] = None,
    ) -> BootstrapReport:
        """断点续举 -- 从指定 checkpoint 继续, 跳过已完成的能力。

        支持两种 checkpoint 来源:
          1. 显式传入的 checkpoint 集合
          2. checkpoint_file: JSON 文件路径, 包含 {"completed": [...], "context": {...}}

        当 checkpoint_file 存在时, 从文件恢复上下文状态,
        并与显式 checkpoint 合并 (显式优先)。

        Args:
            checkpoint:      已完成的 capability 名称集合
            checkpoint_file: checkpoint JSON 文件路径 (可选)

        Returns:
            编排报告 (包含跳过的和本次执行的结果)
        """
        start = time.perf_counter()
        results: Dict[str, CapabilityResult] = {}

        # ── 从 checkpoint 文件恢复 ──
        restored_checkpoint: Set[str] = set(checkpoint)
        if checkpoint_file is not None:
            try:
                with open(checkpoint_file, encoding="utf-8") as f:
                    ckpt_data = json.load(f)
                # 恢复已完成的 capability
                file_completed = set(ckpt_data.get("completed", []))
                restored_checkpoint |= file_completed
                # 恢复上下文字段
                ctx_data = ckpt_data.get("context", {})
                if ctx_data:
                    if ctx_data.get("env_profile"):
                        self.ctx.env_profile = ctx_data["env_profile"]
                    if ctx_data.get("dep_status"):
                        self.ctx.dep_status = ctx_data["dep_status"]
                    if ctx_data.get("ver_info"):
                        self.ctx.ver_info = ctx_data["ver_info"]
                    if ctx_data.get("update_plan"):
                        self.ctx.update_plan = ctx_data["update_plan"]
                logger.info(
                    "Restored checkpoint from %s: %d completed capabilities",
                    checkpoint_file, len(file_completed),
                )
            except FileNotFoundError:
                logger.info("Checkpoint file not found: %s, starting fresh", checkpoint_file)
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Invalid checkpoint file %s: %s", checkpoint_file, exc)

        # 标记 checkpoint 为已完成
        self.ctx.completed = set(restored_checkpoint)

        # 为跳过的能力生成占位结果
        for name in restored_checkpoint:
            if name in self.capabilities:
                results[name] = CapabilityResult(
                    name=name,
                    success=True,
                    degraded=True,
                    degradation_note="Skipped (from checkpoint)",
                    duration_ms=0.0,
                )

        layers = self._topological_sort()
        logger.info("Bootstrap resume from checkpoint %s, plan: %s", restored_checkpoint, layers)

        for layer in layers:
            for name in layer:
                if name in restored_checkpoint:
                    continue
                cap = self.capabilities[name]
                logger.info("Executing capability: %s (resumed)", name)
                result = await self._execute_capability(cap)
                results[name] = result

        elapsed = (time.perf_counter() - start) * 1000
        success = all(r.success for r in results.values())

        # ── 保存 checkpoint 文件 (供下次恢复) ──
        if checkpoint_file is not None:
            try:
                ckpt_out = {
                    "completed": sorted(self.ctx.completed),
                    "context": {
                        "env_profile": self.ctx.env_profile,
                        "dep_status": self.ctx.dep_status,
                        "ver_info": self.ctx.ver_info,
                        "update_plan": self.ctx.update_plan,
                    },
                    "timestamp": time.time(),
                }
                with open(checkpoint_file, "w", encoding="utf-8") as f:
                    json.dump(ckpt_out, f, indent=2, ensure_ascii=False, default=str)
                logger.info("Checkpoint saved to: %s", checkpoint_file)
            except Exception as exc:
                logger.warning("Failed to save checkpoint: %s", exc)

        return BootstrapReport(
            success=success,
            total_duration_ms=elapsed,
            results=results,
            context=self.ctx,
        )


# ═══════════════════════════════════════════════════════════════════
#  健康看门狗
# ═══════════════════════════════════════════════════════════════════

class BootstrapWatchdog:
    """Bootstrap 后健康看门狗 -- 持续监控关键指标。

    在 bootstrap 完成后, 以指定间隔持续检查关键健康指标,
    发现异常时记录警告并可选触发回调。

    监控指标:
      - env_profile:  环境适配是否仍然有效 (GPU/内存未变化)
      - dep_status:   依赖是否仍然健康 (无新增缺失)
      - ver_info:     版本是否仍然一致

    Args:
        check_env:     是否监控环境指标
        check_dep:     是否监控依赖指标
        check_version: 是否监控版本指标
        on_unhealthy:  不健康时的回调, 签名 (indicator: str, detail: str) -> None
    """

    def __init__(
        self,
        check_env: bool = True,
        check_dep: bool = True,
        check_version: bool = True,
        on_unhealthy: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.check_env = check_env
        self.check_dep = check_dep
        self.check_version = check_version
        self.on_unhealthy = on_unhealthy
        self._running = False
        self._last_snapshot: Dict[str, Any] = {}

    def snapshot(self, ctx: BootstrapContext) -> Dict[str, Any]:
        """对当前上下文做快照, 作为后续监控的基准。

        Args:
            ctx: 编排器上下文

        Returns:
            快照字典, 包含 env/dep/version 的关键指标
        """
        snap: Dict[str, Any] = {
            "timestamp": time.time(),
        }
        if self.check_env:
            snap["env"] = {
                "gpu_type": ctx.env_profile.get("gpu", {}).get("type", "unknown"),
                "memory_total_gb": ctx.env_profile.get("memory", {}).get("total_gb", 0),
            }
        if self.check_dep:
            snap["dep"] = {
                "missing_core": list(ctx.dep_status.get("missing_core", [])),
                "total": ctx.dep_status.get("total", 0),
                "installed": ctx.dep_status.get("installed", 0),
            }
        if self.check_version:
            snap["version"] = {
                "consistent": ctx.ver_info.get("consistent", True),
            }
        self._last_snapshot = snap
        return snap

    def check_health(self, ctx: BootstrapContext) -> Dict[str, Any]:
        """单次健康检查 -- 对比当前上下文与快照基准。

        Args:
            ctx: 编排器上下文

        Returns:
            健康检查结果, 包含 healthy (bool) 和 details (list)
        """
        if not self._last_snapshot:
            logger.warning("Watchdog: no snapshot taken, skipping health check")
            return {"healthy": True, "details": []}

        details: List[str] = []
        healthy = True

        # 环境检查
        if self.check_env and "env" in self._last_snapshot:
            base = self._last_snapshot["env"]
            current_gpu = ctx.env_profile.get("gpu", {}).get("type", "unknown")
            current_mem = ctx.env_profile.get("memory", {}).get("total_gb", 0)
            if current_gpu != base.get("gpu_type", "unknown"):
                detail = f"GPU changed: {base.get('gpu_type')} -> {current_gpu}"
                details.append(detail)
                healthy = False
                logger.warning("Watchdog: %s", detail)
            if abs(current_mem - base.get("memory_total_gb", 0)) > 1.0:
                detail = f"Memory changed: {base.get('memory_total_gb', 0):.1f}GB -> {current_mem:.1f}GB"
                details.append(detail)
                healthy = False
                logger.warning("Watchdog: %s", detail)

        # 依赖检查
        if self.check_dep and "dep" in self._last_snapshot:
            base = self._last_snapshot["dep"]
            current_missing_core = list(ctx.dep_status.get("missing_core", []))
            if len(current_missing_core) > len(base.get("missing_core", [])):
                detail = f"New missing core deps: {current_missing_core}"
                details.append(detail)
                healthy = False
                logger.warning("Watchdog: %s", detail)

        # 版本检查
        if self.check_version and "version" in self._last_snapshot:
            base = self._last_snapshot["version"]
            current_consistent = ctx.ver_info.get("consistent", True)
            if not current_consistent and base.get("consistent", True):
                detail = "Version consistency lost"
                details.append(detail)
                healthy = False
                logger.warning("Watchdog: %s", detail)

        # 触发回调
        if not healthy and self.on_unhealthy is not None:
            for d in details:
                try:
                    self.on_unhealthy("watchdog", d)
                except Exception as exc:
                    logger.warning("Watchdog callback error: %s", exc)

        return {"healthy": healthy, "details": details}

    async def monitor(
        self,
        ctx: BootstrapContext,
        interval: float = 60.0,
        max_rounds: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """持续监控 -- 以指定间隔循环执行健康检查。

        Args:
            ctx:         编排器上下文 (每次检查时重新读取)
            interval:    检查间隔 (秒), 默认 60.0
            max_rounds:  最大检查轮数, None 表示无限

        Returns:
            各轮检查结果列表
        """
        import asyncio

        self._running = True
        all_checks: List[Dict[str, Any]] = []
        round_count = 0

        # 首次快照
        self.snapshot(ctx)

        while self._running:
            if max_rounds is not None and round_count >= max_rounds:
                break

            await asyncio.sleep(interval)
            round_count += 1

            check_result = self.check_health(ctx)
            check_result["round"] = round_count
            all_checks.append(check_result)

            logger.debug(
                "Watchdog round %d: healthy=%s",
                round_count, check_result["healthy"],
            )

        self._running = False
        return all_checks

    def stop(self) -> None:
        """停止监控循环。"""
        self._running = False


# ═══════════════════════════════════════════════════════════════════
#  向后兼容入口
# ═══════════════════════════════════════════════════════════════════

async def auto_bootstrap(
    dry_run: bool = False,
    profile: str = "dev",
) -> Dict[str, Any]:
    """向后兼容入口 -- 内部委托 GalaxyBootstrap 编排器。

    返回格式与旧 auto_bootstrap.auto_bootstrap() 兼容,
    额外包含新格式的 results 和 context 字段。

    Args:
        dry_run:  只检测不修改
        profile:  运行环境 (dev/staging/prod)

    Returns:
        自举报告字典, 包含:
          - env_adaptation:    环境适配结果
          - dep_health:        依赖健康报告
          - version_consistency: 版本一致性校验
          - status:            "ok" / "partial" / "degraded" / "version_mismatch"
          - results:           各 capability 详细结果 (新)
          - context:           上下文摘要 (新)
    """
    bootstrap = GalaxyBootstrap(profile=profile, dry_run=dry_run)
    bootstrap.register(EnvCapability())
    bootstrap.register(DepCapability())
    bootstrap.register(VersionCapability())
    bootstrap.register(UpdateCapability())
    report = await bootstrap.run()
    return report.to_dict()


# ═══════════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════════

def cli_main():
    """CLI entry point for galaxyos-bootstrap command.

    Usage:
        galaxyos-bootstrap                    # full bootstrap
        galaxyos-bootstrap --dry-run          # check only, no modifications
        galaxyos-bootstrap --profile prod     # production profile
        galaxyos-bootstrap --only env dep     # run only specified capabilities
    """
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(
        prog="galaxyos-bootstrap",
        description="GalaxyOS One-Click Bootstrap (Environment + Dependencies + Version + Update)"
    )
    parser.add_argument("--dry-run", action="store_true",
                       help="Check only, do not modify environment")
    parser.add_argument("--profile", default="dev",
                       choices=["dev", "staging", "prod"],
                       help="Environment profile (default: dev)")
    parser.add_argument("--only", nargs="+",
                       choices=["env", "dep", "version", "update"],
                       help="Run only specified capabilities")
    parser.add_argument("--quiet", action="store_true",
                       help="Minimal output")
    args = parser.parse_args()

    async def _run():
        bootstrap = GalaxyBootstrap(profile=args.profile, dry_run=args.dry_run)

        if args.only:
            cap_map = {
                "env": EnvCapability,
                "dep": DepCapability,
                "version": VersionCapability,
                "update": UpdateCapability,
            }
            for name in args.only:
                if name in cap_map:
                    bootstrap.register(cap_map[name]())
        else:
            bootstrap.register(EnvCapability())
            bootstrap.register(DepCapability())
            bootstrap.register(VersionCapability())
            bootstrap.register(UpdateCapability())

        report = await bootstrap.run()
        if not args.quiet:
            print(report.summary())
        return 0 if report.success else 1

    sys.exit(asyncio.run(_run()))


# ═══════════════════════════════════════════════════════════════════
#  导出列表
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    # 数据类
    "BootstrapContext",
    "CapabilityResult",
    "BootstrapReport",
    # ABC
    "Capability",
    # 内置 Capability
    "EnvCapability",
    "DepCapability",
    "VersionCapability",
    "UpdateCapability",
    # 编排器
    "GalaxyBootstrap",
    # Profile 配置
    "PROFILE_CONFIGS",
    # 健康看门狗
    "BootstrapWatchdog",
    # 兼容入口
    "auto_bootstrap",
    # CLI 入口
    "cli_main",
]
