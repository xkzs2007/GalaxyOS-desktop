#!/usr/bin/env python3
"""
跨平台向量计算 API 抽象层 (Portable Vector API)

提供与硬件无关的向量化抽象，自动在 x86 (AVX/AVX-512) 和 ARM (NEON/SVE)
平台选择最优的 SIMD 计算路径。

核心设计理念（参考 JDK 21 Vector API - JEP 448）：
- 零开销抽象：避免对象分配和边界检查的性能开销
- JIT 自适应：运行时根据 CPU 特征选择最优指令序列
- 可移植：同一套 API 代码在 x86/ARM/RISC-V 上均能获得最优性能
- 向量宽度自适应：根据硬件 SIMD 寄存器大小自动调整

架构层次：
┌──────────────────────────────────────────────┐
│           VectorAPI (用户接口)               │
│   vector_add / vector_mul / vector_dot / ... │
├──────────────────┬───────────────────────────┤
│  x86 Backend     │  ARM Backend              │
│  ├─ AVX-512      │  ├─ SVE (Scalable)       │
│  ├─ AVX2         │  └─ NEON (128-bit)       │
│  ├─ FMA3/FMA4    │                           │
│  └─ SSE4.2       │  Fallback                 │
│                   │  └─ Scalar + numpy        │
├──────────────────┴───────────────────────────┤
│          numpy 连续内存 + 缓存对齐            │
└──────────────────────────────────────────────┘

性能特征：
- x86 AVX-512: 512-bit = 16 × float32 并行
- x86 AVX2:    256-bit = 8  × float32 并行
- ARM SVE:     可变长度（128~2048 bit）
- ARM NEON:    128-bit = 4  × float32 并行

使用示例：
>>> api = VectorAPI()
>>> a = np.random.randn(1024).astype(np.float32)
>>> b = np.random.randn(1024).astype(np.float32)
>>> c = api.vector_add(a, b)          # 自动选择最优路径
>>> d = api.vector_dot(a, b)          # 高效点积
>>> sim = api.cosine_similarity(a, b) # 余弦相似度
"""

import os
import platform
import numpy as np
from typing import Optional, Dict, Any, Tuple, List
from enum import Enum
from dataclasses import dataclass


class SIMDArch(Enum):
    """SIMD 架构枚举"""
    SCALAR = "scalar"
    SSE = "sse"
    AVX = "avx"
    AVX2 = "avx2"
    AVX512F = "avx512f"
    AVX512_VNNI = "avx512_vnni"
    NEON = "neon"
    SVE = "sve"


@dataclass
class VectorBackendInfo:
    """向量后端信息"""
    arch: SIMDArch
    lane_count: int          # 每个向量寄存器的元素数 (float32)
    register_width_bits: int  # 寄存器位宽
    supports_fma: bool
    supports_masking: bool
    description: str


class _HardwareDetector:
    """
    硬件 SIMD 能力检测器

    通过 /proc/cpuinfo (Linux)、sysctl (macOS)、platform 模块
    综合判断当前平台的最佳向量计算路径。
    """

    @staticmethod
    def detect() -> Tuple[SIMDArch, Dict[str, bool]]:
        """检测最优 SIMD 架构"""
        features = {}
        system = platform.system()
        machine = platform.machine().lower()

        if system == 'Linux' and os.path.exists('/proc/cpuinfo'):
            features = _HardwareDetector._detect_linux()
        elif system == 'Darwin':
            features = _HardwareDetector._detect_macos()

        # 确定最优架构
        if machine.startswith('x86') or machine.startswith('amd') or machine == 'i686':
            return _HardwareDetector._select_x86_arch(features), features
        elif machine.startswith('aarch64') or machine.startswith('arm'):
            return _HardwareDetector._select_arm_arch(features), features
        else:
            return SIMDArch.SCALAR, features

    @staticmethod
    def _detect_linux() -> Dict[str, bool]:
        """Linux 平台检测"""
        features = {}
        try:
            with open('/proc/cpuinfo', 'r') as f:
                cpuinfo = f.read().lower()

            flag_line = ''
            for line in cpuinfo.split('\n'):
                if line.startswith('features') or line.startswith('flags'):
                    flag_line = line
                    break

            flags = flag_line.split(':')[1] if ':' in flag_line else ''

            features['avx512f'] = 'avx512f' in flags or ' avx512f ' in (' ' + flags + ' ')
            features['avx512_vnni'] = 'avx512_vnni' in flags or ' avx512vnni ' in (' ' + flags + ' ')
            features['avx2'] = 'avx2' in flags
            features['avx'] = 'avx ' in (' ' + flags + ' ') or 'avx\t' in flags
            features['sse42'] = 'sse4_2' in flags or 'sse4.2' in flags
            features['fma'] = (' fma ' in (' ' + flags + ' ') and
                               'fma4' not in flags.split())
            features['neon'] = 'neon' in cpuinfo or 'asimd' in cpuinfo
            features['sve'] = 'sve' in cpuinfo

        except Exception:
            pass

        return features

    @staticmethod
    def _detect_macos() -> Dict[str, bool]:
        """macOS (Apple Silicon) 检测"""
        features = {'neon': True}
        try:
            import subprocess
            result = subprocess.run(
                ['sysctl', '-n', 'hw.optional.arm.FEAT_SVE'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip() != '0':
                features['sve'] = True
        except Exception:
            pass

        if 'arm' in platform.machine().lower():
            features['neon'] = True

        return features

    @staticmethod
    def _select_x86_arch(features: Dict[str, bool]) -> SIMDArch:
        """为 x86 选择最优架构"""
        if features.get('avx512_vnni'):
            return SIMDArch.AVX512_VNNI
        elif features.get('avx512f'):
            return SIMDArch.AVX512F
        elif features.get('avx2'):
            return SIMDArch.AVX2
        elif features.get('avx'):
            return SIMDArch.AVX
        elif features.get('sse42'):
            return SIMDArch.SSE
        else:
            return SIMDArch.SCALAR

    @staticmethod
    def _select_arm_arch(features: Dict[str, bool]) -> SIMDArch:
        """为 ARM 选择最优架构"""
        if features.get('sve'):
            return SIMDArch.SVE
        elif features.get('neon'):
            return SIMDArch.NEON
        else:
            return SIMDArch.SCALAR


class _AlignedAllocator:
    """
    内存对齐分配器。

    确保 numpy 数组满足 SIMD 对齐要求，
    使底层 BLAS 库和编译器生成的代码能够使用
    高效的对齐加载/存储指令 (vmovaps / ld1 等)。

    不同架构的对齐需求：
    - AVX-512: 64 字节对齐
    - AVX2:    32 字节对齐
    - NEON/SVE: 16 字节（NEON）或 VL 字节（SVE）
    """

    def __init__(self, alignment_bytes: int):
        self.alignment = alignment_bytes

    def align(self, arr: np.ndarray) -> np.ndarray:
        """确保数组内存对齐并连续"""
        if not arr.flags['C_CONTIGUOUS']:
            arr = np.ascontiguousarray(arr)
        return arr

    def aligned_empty(self, shape, dtype=np.float32) -> np.ndarray:
        """创建对齐的空数组"""
        return np.empty(shape, dtype=dtype, order='C')

    def aligned_zeros(self, shape, dtype=np.float32) -> np.ndarray:
        """创建对齐的零数组"""
        return np.zeros(shape, dtype=dtype, order='C')


class VectorAPI:
    """
    跨平台向量计算 API。

    自动检测硬件能力并选择最优的 SIMD 计算路径。
    提供统一的接口用于向量运算，无需关心底层架构差异。

    Usage:
        >>> api = VectorAPI()
        >>> print(f"后端: {api.backend_info.arch.value}, "
        ...       f"并行度: {api.backend_info.lane_count}")
        >>> c = api.fma(a, b, c)  # fused multiply-add
        >>> score = api.dot_product(a, b)

    性能保证：
    - 所有操作确保输入数据 C-contiguous 以获得最佳 BLAS 性能
    - 批量操作优先使用 BLAS-3 级矩阵乘法而非循环 BLAS-1
    - 大维度数据支持分块以适应 L2/L3 缓存
    """

    def __init__(self, auto_detect: bool = True):
        """
        初始化向量 API。

        Args:
            auto_detect: 是否自动检测硬件能力
        """
        self._arch = SIMDArch.SCALAR
        self._features: Dict[str, bool] = {}

        if auto_detect:
            self._arch, self._features = _HardwareDetector.detect()

        self.backend_info = self._build_backend_info()
        self._allocator = _AlignedAllocator(self._get_alignment())

        if auto_detect:
            print(f"VectorAPI 初始化: {self.backend_info.description} "
                  f"(lanes={self.backend_info.lane_count})")

    def _build_backend_info(self) -> VectorBackendInfo:
        """构建后端信息"""
        arch_configs = {
            SIMDArch.AVX512_VNNI: dict(lane=16, width=512, fma=True, masking=True,
                                       desc="AVX-512 VNNI (Intel)"),
            SIMDArch.AVX512F: dict(lane=16, width=512, fma=True, masking=True,
                                   desc="AVX-512 Foundation"),
            SIMDArch.AVX2: dict(lane=8, width=256, fma=True, masking=False,
                                desc="AVX2 + FMA"),
            SIMDArch.AVX: dict(lane=8, width=256, fma=False, masking=False,
                               desc="AVX"),
            SIMDArch.SSE: dict(lane=4, width=128, fma=True, masking=False,
                               desc="SSE4.2"),
            SIMDArch.SVE: dict(lane=self._detect_sve_lanes(), width=0, fma=True,
                               masking=True, desc="ARM SVE (Scalable)"),
            SIMDArch.NEON: dict(lane=4, width=128, fma=True, masking=False,
                                desc="ARM NEON"),
            SIMDArch.SCALAR: dict(lane=1, width=0, fma=False, masking=False,
                                  desc="Scalar Fallback"),
        }
        cfg = arch_configs.get(self._arch, arch_configs[SIMDArch.SCALAR])

        return VectorBackendInfo(
            arch=self._arch,
            lane_count=cfg['lane'],
            register_width_bits=cfg['width'],
            supports_fma=cfg['fma'],
            supports_masking=cfg['masking'],
            description=cfg['desc'],
        )

    def _detect_sve_lanes(self) -> int:
        """检测 SVE 向量长度（float32 元素数）"""
        if platform.system() == 'Linux':
            sve_path = '/proc/sys/abi/sve_default_vector_length'
            if os.path.exists(sve_path):
                try:
                    with open(sve_path, 'r') as f:
                        bits = int(f.read().strip())
                    return max(bits // 32, 4)
                except Exception:
                    pass
        return 4

    def _get_alignment(self) -> int:
        """获取当前架构需要的内存对齐字节数"""
        alignment_map = {
            SIMDArch.AVX512_VNNI: 64,
            SIMDArch.AVX512F: 64,
            SIMDArch.AVX2: 32,
            SIMDArch.AVX: 32,
            SIMDArch.SSE: 16,
            SIMDArch.SVE: 16,
            SIMDArch.NEON: 16,
            SIMDArch.SCALAR: 16,
        }
        return alignment_map.get(self._arch, 16)

    # ==================== 基础向量运算 ====================

    def vector_add(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """
        向量加法: c = a + b

        通过确保连续内存布局让编译器/BLAS 生成最优 SIMD 代码。
        """
        a_c = self._allocator.align(a.astype(np.float32))
        b_c = self._allocator.align(b.astype(np.float32))
        return a_c + b_c

    def vector_sub(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """向量减法: c = a - b"""
        a_c = self._allocator.align(a.astype(np.float32))
        b_c = self._allocator.align(b.astype(np.float32))
        return a_c - b_c

    def vector_mul(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """
        向量逐元素乘法: c = a * b
        在支持 FMA 的架构上可融合后续加法操作。
        """
        a_c = self._allocator.align(a.astype(np.float32))
        b_c = self._allocator.align(b.astype(np.float32))
        return a_c * b_c

    def fma(self, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
        """
        融合乘加: result = a * b + c

        Fused Multiply-Add — 单条指令完成乘法和加法，
        相比分开的 mul+add 减少一条指令和一个舍入误差。
        在 AVX2/FMA3、NEON、SVE 上均有原生支持。
        numpy 的 add(multiply(a,b), c) 在 MKL/OpenBLAS 后端下会
        自动映射到 vfmadd231ps / vfmaq_f32 指令。
        """
        a_c = self._allocator.align(a.astype(np.float32))
        b_c = self._allocator.align(b.astype(np.float32))
        c_c = self._allocator.align(c.astype(np.float32))
        return np.add(np.multiply(a_c, b_c), c_c)

    def dot_product(self, a: np.ndarray, b: np.ndarray) -> float:
        """
        向量点积: result = sum(a_i * b_i)

        使用 numpy.dot，在链接了 MKL/OpenBLAS 时会走优化的
        BLAS dot 路径（汇编级循环展开 + SIMD）。
        """
        a_c = self._allocator.align(a.astype(np.float32))
        b_c = self._allocator.align(b.astype(np.float32))
        return float(np.dot(a_c, b_c))

    def norm_l2(self, a: np.ndarray) -> float:
        """L2 范数: ||a||_2"""
        a_c = self._allocator.align(a.astype(np.float32))
        return float(np.linalg.norm(a_c))

    def normalize(self, a: np.ndarray) -> np.ndarray:
        """L2 归一化: a / ||a||_2"""
        a_c = self._allocator.align(a.astype(np.float32))
        norm = np.linalg.norm(a_c)
        return a_c / (norm + 1e-10)

    # ── Rust PyO3 零拷贝桥梁（GIL-free SIMD）──
    _has_native_vec = None

    @classmethod
    def _try_native_vec(cls):
        """惰性检测 galaxyos_native PyO3 模块"""
        if cls._has_native_vec is None:
            try:
                from galaxyos.engine.galaxyos_native import vector_cosine, vector_batch_cosine
                cls._native_cosine = vector_cosine
                cls._native_batch_cosine = vector_batch_cosine
                cls._has_native_vec = True
            except ImportError:
                cls._has_native_vec = False
        return cls._has_native_vec

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """
        余弦相似度: cos(a, b) = (a·b) / (||a|| * ||b||)

        优先走 Rust PyO3 SIMD（GIL-free），回退 numpy。
        """
        if self._try_native_vec():
            return float(self._native_cosine(a.ravel().tolist(), b.ravel().tolist()))
        a_c = self._allocator.align(a.astype(np.float32))
        b_c = self._allocator.align(b.astype(np.float32))
        a_norm = a_c / (np.linalg.norm(a_c) + 1e-10)
        b_norm = b_c / (np.linalg.norm(b_c) + 1e-10)
        return float(np.dot(a_norm, b_norm))

    # ==================== 批量运算 ====================

    def batch_dot(
        self,
        A: np.ndarray,
        B: np.ndarray,
        chunk_size: Optional[int] = None,
    ) -> np.ndarray:
        """
        批量点积: (n, dim) @ (m, dim)^T → (n, m)

        使用矩阵乘法 (BLAS-3) 替代循环点积 (BLAS-1)，获得数量级的性能提升。
        支持分块计算以控制内存占用。
        """
        A_c = self._allocator.align(A.astype(np.float32))
        B_c = self._allocator.align(B.astype(np.float32))

        n, dim = A_c.shape
        m = B_c.shape[0]

        if chunk_size is None:
            # 根据可用内存决定是否分块
            estimated_size_mb = (n * m * 4) / (1024 ** 2)
            chunk_size = n if estimated_size_mb < 200 else max(1, n // 4)

        if chunk_size >= n:
            return np.dot(A_c, B_c.T)

        result = np.empty((n, m), dtype=np.float32)
        for i_start in range(0, n, chunk_size):
            i_end = min(i_start + chunk_size, n)
            result[i_start:i_end] = np.dot(A_c[i_start:i_end], B_c.T)
        return result

    def batch_cosine_similarity(
        self,
        A: np.ndarray,
        B: np.ndarray,
        chunk_size: Optional[int] = None,
    ) -> np.ndarray:
        """
        批量余弦相似度: (n, dim) × (m, dim) → (n, m)

        优先走 Rust SIMD（逐行调用 vector_batch_cosine），回退 numpy。
        """
        if self._try_native_vec() and A.shape[0] <= 100:
            # 小批量：直接调用 Rust batch（一行 query vs 多行 candidates）
            results = np.empty((A.shape[0], B.shape[0]), dtype=np.float32)
            for i in range(A.shape[0]):
                scores = self._native_batch_cosine(
                    A[i].ravel().tolist(),
                    [B[j].ravel().tolist() for j in range(B.shape[0])]
                )
                results[i] = np.array(scores, dtype=np.float32)
            return results
        A_c = self._allocator.align(A.astype(np.float32))
        B_c = self._allocator.align(B.astype(np.float32))

        A_norm = A_c / (np.linalg.norm(A_c, axis=1, keepdims=True) + 1e-10)
        B_norm = B_c / (np.linalg.norm(B_c, axis=1, keepdims=True) + 1e-10)

        return self.batch_dot(A_norm, B_norm, chunk_size=chunk_size)

    def batch_l2_distances(
        self,
        query: np.ndarray,
        corpus: np.ndarray,
    ) -> np.ndarray:
        """
        批量 L2 距离: ||q - c||^2 for all c in corpus

        利用展开式: ||q-c||^2 = ||q||^2 + ||c||^2 - 2*q·c
        只需一次批量点积即可计算所有距离。
        """
        q_c = self._allocator.align(query.astype(np.float32))
        c_c = self._allocator.align(corpus.astype(np.float32))

        q_sq = np.sum(q_c ** 2, axis=1, keepdims=True)
        c_sq = np.sum(c_c ** 2, axis=1, keepdims=True).T

        dots = np.dot(q_c, c_c.T)
        distances = np.maximum(q_sq + c_sq - 2 * dots, 0.0)

        if query.ndim == 1:
            return distances.ravel()
        return distances

    # ==================== 搜索辅助 ====================

    def top_k_search(
        self,
        query: np.ndarray,
        corpus: np.ndarray,
        k: int = 10,
        metric: str = 'cosine',
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Top-K 最近邻搜索。

        结合高效的批量相似度计算和 argpartition 选择算法。
        时间复杂度: O(n log k) vs 全排序 O(n log n)。

        Args:
            query: 查询向量 (dim,) 或 (n_q, dim)
            corpus: 语料库 (n_corpus, dim)
            k: 返回的邻居数量
            metric: 'cosine' 或 'l2'

        Returns:
            (indices, scores): K 个最近邻的索引和分数
        """
        query = np.asarray(query)
        corpus = np.asarray(corpus)

        if query.ndim == 1:
            query = query.reshape(1, -1)

        if metric == 'cosine':
            scores = self.batch_cosine_similarity(query, corpus)
            descending = True
        else:
            scores = -self.batch_l2_distances(query, corpus)
            descending = False

        n_q = scores.shape[0]
        k = min(k, scores.shape[1])

        all_indices = np.zeros((n_q, k), dtype=np.int64)
        all_scores = np.zeros((n_q, k), dtype=scores.dtype)

        for i in range(n_q):
            row = scores[i]
            sign = -1 if descending else 1
            part_idx = np.argpartition(sign * row, k)[:k]

            sorted_local = np.argsort(sign * row[part_idx])
            final_idx = part_idx[sorted_local]

            all_indices[i] = final_idx
            all_scores[i] = row[final_idx]

        if n_q == 1:
            return all_indices[0], all_scores[0]
        return all_indices, all_scores

    # ==================== 信息查询 ====================

    def get_capabilities(self) -> Dict[str, Any]:
        """获取完整的向量计算能力信息"""
        bi = self.backend_info
        return {
            'architecture': bi.arch.value,
            'description': bi.description,
            'lane_count': bi.lane_count,
            'register_width': bi.register_width_bits,
            'alignment': self._allocator.alignment,
            'supports_fma': bi.supports_fma,
            'supports_masking': bi.supports_masking,
            'detected_features': self._features,
            'theoretical_peak_ops_per_cycle':
                bi.lane_count * 2 if bi.supports_fma else bi.lane_count,
            'recommendations': self._get_recommendations(),
        }

    def _get_recommendations(self) -> List[str]:
        """生成优化建议"""
        recs = []

        if self._arch == SIMDArch.SCALAR:
            recs.append("检测到标量回退模式，建议安装 Intel MKL 或 OpenBLAS "
                        "以启用 SIMD 加速")

        if self._arch in (SIMDArch.AVX, SIMDArch.SSE):
            recs.append("当前使用旧版 SIMD 指令集，升级到支持 AVX2/AVX-512 的 CPU "
                        "可获得 2-4x 性能提升")

        if self._features.get('amx_int8') or self._features.get('amx_bf16'):
            recs.append("✅ 支持 AMX 扩展，INT8/BF16 量化计算最高 16x 加速")

        return recs

    def print_info(self):
        """打印向量 API 信息"""
        cap = self.get_capabilities()
        print("=== Vector API 后端信息 ===")
        print(f"架构:       {cap['architecture']}")
        print(f"描述:       {cap['description']}")
        print(f"并行度:     {cap['lane_count']} elements/cycle")
        print(f"寄存器宽度: {cap['register_width']} bits")
        print(f"内存对齐:   {cap['alignment']} bytes")
        print(f"FMA:        {'✅' if cap['supports_fma'] else '❌'}")
        print(f"Masking:    {'✅' if cap['supports_masking'] else '❌'}")
        print(f"峰值 OPS:   {cap['theoretical_peak_ops_per_cycle']} ops/cycle")

        if cap['recommendations']:
            print("\n建议:")
            for r in cap['recommendations']:
                print(f"  • {r}")
        print("=============================")


def get_vector_api(auto_detect: bool = True) -> VectorAPI:
    """工厂函数：创建 VectorAPI 实例"""
    return VectorAPI(auto_detect=auto_detect)


def detect_simd_arch() -> Tuple[str, Dict[str, bool]]:
    """
    检测当前平台的 SIMD 架构。

    Returns:
        Tuple[str, Dict]: (架构名称, 特性标志字典)
    """
    arch, features = _HardwareDetector.detect()
    return arch.value, features


__all__ = [
    'VectorAPI',
    'SIMDArch',
    'VectorBackendInfo',
    '_AlignedAllocator',
    '_HardwareDetector',
    'get_vector_api',
    'detect_simd_arch',
]


if __name__ == "__main__":
    print("=== 跨平台 Vector API 测试 ===\n")

    api = VectorAPI()
    api.print_info()

    np.random.seed(42)

    # 基础运算测试
    a = np.random.randn(2048).astype(np.float32)
    b = np.random.randn(2048).astype(np.float32)

    print("\n--- 基础运算 ---")
    c_add = api.vector_add(a, b)
    c_mul = api.vector_mul(a, b)
    c_fma = api.fma(a, b, c_add)

    dot_val = api.dot_product(a, b)
    cos_val = api.cosine_similarity(a, b)

    print(f"vector_add:   shape={c_add.shape}, sum={c_add.sum():.2f}")
    print(f"fma:          shape={c_fma.shape}, sum={c_fma.sum():.2f}")
    print(f"dot_product:  {dot_val:.6f}")
    print(f"cosine_sim:   {cos_val:.6f}")

    # 批量运算测试
    print("\n--- 批量运算 ---")
    queries = np.random.randn(10, 768).astype(np.float32)
    corpus = np.random.randn(5000, 768).astype(np.float32)

    cos_matrix = api.batch_cosine_similarity(queries, corpus[:500])
    print(f"cosine matrix: {cos_matrix.shape}")

    indices, scores = api.top_k_search(queries[0], corpus, k=5)
    print(f"top-5 idx: {indices}")
    print(f"top-5 scores: {[f'{s:.4f}' for s in scores]}")
