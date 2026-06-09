#!/usr/bin/env python3
"""
GPU 优化器 - GPU 检测与推理优化

2024-2026 行业关键功能：
- CUDA 设备检测（显存、算力、架构）
- 多 GPU 拓扑发现（NVLink、PCIe）
- Flash Attention 可用性检测
- 推理优化配置（Continuous Batching、PagedAttention）
- KV Cache 分页管理配置
- Speculative Decoding 配置
- Tensor/Pipeline 并行策略推荐
"""

import subprocess
import logging
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GPUDevice:
    """GPU 设备信息"""
    index: int
    name: str
    uuid: str = ''
    memory_total_mb: int = 0
    memory_free_mb: int = 0
    compute_capability: str = ''
    architecture: str = ''
    cuda_version: str = ''
    pcie_link: str = ''
    nvlink_connected: List[int] = field(default_factory=list)
    temperature_c: int = 0
    power_usage_w: int = 0
    power_limit_w: int = 0
    utilization_gpu: int = 0
    utilization_memory: int = 0


@dataclass
class KVCacheConfig:
    """KV Cache 配置"""
    paged_attention_enabled: bool = True
    page_size: int = 16            # tokens per page
    max_num_seqs: int = 256        # 最大并发序列数
    max_num_batched_tokens: int = 8192  # 每批最大 token 数
    gpu_memory_utilization: float = 0.9  # GPU 显存利用率
    swap_space_gb: int = 4         # CPU 交换空间
    block_size: int = 16           # KV block 大小
    prefix_caching: bool = True    # 前缀共享缓存
    cache_dtype: str = 'auto'      # 'auto', 'fp16', 'fp8_e5m2'


@dataclass
class InferenceConfig:
    """推理优化配置"""
    continuous_batching: bool = True
    speculative_decoding: bool = False
    speculative_model: str = ''
    speculative_max_tokens: int = 5
    speculative_accept_threshold: float = 0.1
    tensor_parallel_size: int = 1
    pipeline_parallel_size: int = 1
    max_model_len: int = 8192
    quantization: str = ''         # 'awq', 'gptq', 'squeezellm', ''
    enforce_eager: bool = False    # 禁用 CUDA Graphs（调试用）
    cuda_graphs: bool = True       # 启用 CUDA Graphs
    flash_attention: bool = True   # 启用 Flash Attention


class GPUDetector:
    """GPU 设备检测器"""

    def detect(self) -> Dict[str, Any]:
        """
        检测所有 GPU 设备

        Returns:
            Dict: GPU 检测结果
        """
        result = {
            'available': False,
            'cuda_available': False,
            'opencl_available': False,
            'opencl_devices': [],
            'nvidia_smi': False,
            'device_count': 0,
            'devices': [],
            'nvlink_pairs': [],
            'cuda_version': '',
            'opencl_version': '',
            'driver_version': '',
            'total_memory_mb': 0,
            'recommended_config': {},
        }

        # 1. 检测 PyTorch CUDA
        try:
            import torch
            result['cuda_available'] = torch.cuda.is_available()
            if result['cuda_available']:
                result['device_count'] = torch.cuda.device_count()
                result['cuda_version'] = torch.version.cuda or ''
        except ImportError:
            pass

        # 2. nvidia-smi 检测
        devices = self._detect_via_nvidia_smi()
        if devices:
            result['available'] = True
            result['nvidia_smi'] = True
            result['devices'] = devices
            result['device_count'] = len(devices)
            result['total_memory_mb'] = sum(d.memory_total_mb for d in devices)

        # 3. OpenCL 检测（覆盖 AMD/Intel GPU 等 NVIDIA 不可用的场景）
        opencl_devices = self._detect_via_opencl()
        if opencl_devices:
            result['opencl_available'] = True
            result['opencl_devices'] = opencl_devices
            if not result['available']:
                # 没有 NVIDIA GPU 时，使用 OpenCL 设备
                result['available'] = True
                result['devices'] = opencl_devices
                result['device_count'] = len(opencl_devices)
                result['total_memory_mb'] = sum(d.memory_total_mb for d in opencl_devices)

        # 4. NVLink 拓扑检测
        if result['nvidia_smi']:
            result['nvlink_pairs'] = self._detect_nvlink()

        # 5. 从 nvidia-smi 获取驱动和 CUDA 版本
        if result['nvidia_smi']:
            result['driver_version'], result['cuda_version'] = self._get_driver_cuda_version()

        # 6. 获取 OpenCL 版本
        if result['opencl_available']:
            result['opencl_version'] = self._get_opencl_version()

        # 7. 生成推荐配置
        if result['available']:
            result['recommended_config'] = self._generate_recommendations(result)

        return result

    def _detect_via_nvidia_smi(self) -> List[GPUDevice]:
        """通过 nvidia-smi 检测 GPU"""
        devices = []

        try:
            result = subprocess.run(
                [
                    'nvidia-smi',
                    '--query-gpu=index,name,uuid,memory.total,memory.free,'
                    'compute_cap,pcie.link.width.current,temperature.gpu,'
                    'power.draw,power.limit,utilization.gpu,utilization.memory',
                    '--format=csv,noheader,nounits',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                return devices

            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue

                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 12:
                    continue

                try:
                    device = GPUDevice(
                        index=int(parts[0]),
                        name=parts[1],
                        uuid=parts[2],
                        memory_total_mb=int(float(parts[3])),
                        memory_free_mb=int(float(parts[4])),
                        compute_capability=parts[5],
                        pcie_link=parts[6],
                        temperature_c=int(float(parts[7])),
                        power_usage_w=int(float(parts[8])),
                        power_limit_w=int(float(parts[9])),
                        utilization_gpu=int(float(parts[10])),
                        utilization_memory=int(float(parts[11])),
                    )

                    # 推断架构
                    device.architecture = self._infer_architecture(device.name, device.compute_capability)

                    devices.append(device)
                except (ValueError, IndexError):
                    continue

        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return devices

    def _detect_via_opencl(self) -> List[GPUDevice]:
        """
        通过 OpenCL 检测 GPU（AMD/Intel/Apple 等非 NVIDIA GPU）

        支持 pyopencl 或 clinfo 命令行工具
        """
        devices = []

        # 方式1: pyopencl
        try:
            import pyopencl as cl

            platforms = cl.get_platforms()
            for plat in platforms:
                for dev in plat.get_devices():
                    # 仅关注 GPU 设备
                    if dev.type != cl.device_type.GPU:
                        continue

                    name = dev.name.strip()
                    mem_bytes = dev.global_mem_size
                    compute_units = dev.max_compute_units
                    vendor = plat.name.strip()

                    # 推断架构
                    arch = self._infer_opencl_architecture(name, vendor)

                    device = GPUDevice(
                        index=len(devices),
                        name=name,
                        memory_total_mb=int(mem_bytes / (1024 * 1024)),
                        memory_free_mb=int(mem_bytes / (1024 * 1024)),  # OpenCL 不提供实时可用显存
                        architecture=arch,
                        pcie_link=f'{compute_units} CUs',
                    )
                    devices.append(device)

            return devices
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"pyopencl 检测失败: {e}")

        # 方式2: clinfo 命令行
        try:
            result = subprocess.run(
                ['clinfo', '--raw'],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                current_name = ''
                current_mem_mb = 0
                current_cu = 0
                current_vendor = ''
                is_gpu = False
                device_index = 0

                for line in result.stdout.split('\n'):
                    line = line.strip()

                    if 'CL_DEVICE_NAME' in line:
                        # 保存上一个设备
                        if current_name and is_gpu:
                            arch = self._infer_opencl_architecture(current_name, current_vendor)
                            devices.append(GPUDevice(
                                index=device_index,
                                name=current_name,
                                memory_total_mb=current_mem_mb,
                                memory_free_mb=current_mem_mb,
                                architecture=arch,
                                pcie_link=f'{current_cu} CUs',
                            ))
                            device_index += 1

                        # 提取设备名
                        if '\t' in line:
                            current_name = line.split('\t')[-1].strip()
                        elif ':' in line:
                            current_name = line.split(':', 1)[1].strip()
                        else:
                            current_name = line.replace('CL_DEVICE_NAME', '').strip()
                        current_mem_mb = 0
                        current_cu = 0
                        is_gpu = False

                    elif 'CL_DEVICE_TYPE' in line and 'GPU' in line:
                        is_gpu = True

                    elif 'CL_DEVICE_GLOBAL_MEM_SIZE' in line:
                        try:
                            val_str = line.split('\t')[-1].strip() if '\t' in line else line.split(':')[-1].strip()
                            current_mem_mb = int(int(val_str) / (1024 * 1024))
                        except (ValueError, IndexError):
                            pass

                    elif 'CL_DEVICE_MAX_COMPUTE_UNITS' in line:
                        try:
                            val_str = line.split('\t')[-1].strip() if '\t' in line else line.split(':')[-1].strip()
                            current_cu = int(val_str)
                        except (ValueError, IndexError):
                            pass

                    elif 'CL_DEVICE_VENDOR' in line:
                        if '\t' in line:
                            current_vendor = line.split('\t')[-1].strip()
                        elif ':' in line:
                            current_vendor = line.split(':', 1)[1].strip()

                # 最后一个设备
                if current_name and is_gpu:
                    arch = self._infer_opencl_architecture(current_name, current_vendor)
                    devices.append(GPUDevice(
                        index=device_index,
                        name=current_name,
                        memory_total_mb=current_mem_mb,
                        memory_free_mb=current_mem_mb,
                        architecture=arch,
                        pcie_link=f'{current_cu} CUs',
                    ))

        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        except Exception as e:
            logger.debug(f"clinfo 检测失败: {e}")

        return devices

    @staticmethod
    def _infer_opencl_architecture(name: str, vendor: str) -> str:
        """推断 OpenCL GPU 架构"""
        name_lower = name.lower()
        vendor_lower = vendor.lower()

        # AMD
        if 'amd' in vendor_lower or 'advanced micro' in vendor_lower:
            if 'mi300' in name_lower or 'mi325' in name_lower:
                return 'CDNA3'
            elif 'mi250' in name_lower or 'mi210' in name_lower:
                return 'CDNA2'
            elif 'mi100' in name_lower:
                return 'CDNA1'
            elif 'rx 90' in name_lower:
                return 'RDNA3'
            elif 'rx 68' in name_lower or 'rx 67' in name_lower:
                return 'RDNA2'
            elif 'rx 57' in name_lower or 'rx 56' in name_lower or 'vega' in name_lower:
                return 'GCN5'
            return 'AMD_GPU'

        # Intel
        if 'intel' in vendor_lower:
            if 'arc' in name_lower:
                return 'Xe_HPG'
            elif 'max' in name_lower and 'gpu' in name_lower:
                return 'Ponte_Vecchio'
            elif 'iris' in name_lower or 'uhd' in name_lower:
                return 'Xe_LP'
            return 'Intel_GPU'

        # Apple
        if 'apple' in vendor_lower:
            if 'm4' in name_lower:
                return 'Apple_GPU_M4'
            elif 'm3' in name_lower:
                return 'Apple_GPU_M3'
            elif 'm2' in name_lower:
                return 'Apple_GPU_M2'
            elif 'm1' in name_lower:
                return 'Apple_GPU_M1'
            return 'Apple_GPU'

        return 'Unknown'

    def _get_opencl_version(self) -> str:
        """获取 OpenCL 版本"""
        try:
            import pyopencl as cl
            platforms = cl.get_platforms()
            if platforms:
                return platforms[0].version
        except (ImportError, Exception):
            pass

        try:
            result = subprocess.run(
                ['clinfo', '-l'],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'OpenCL' in line:
                        return line.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return ''

    def _detect_nvlink(self) -> List[Tuple[int, int]]:
        """检测 NVLink 连接"""
        pairs = []

        try:
            result = subprocess.run(
                ['nvidia-smi', 'topo', '-m'],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'NV' in line and not line.startswith('GPU'):
                        # 解析 NVLink 连接
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                gpu_a = int(parts[0].replace('GPU', ''))
                                gpu_b = int(parts[1].replace('GPU', ''))
                                pairs.append((gpu_a, gpu_b))
                            except ValueError:
                                continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return pairs

    def _get_driver_cuda_version(self) -> Tuple[str, str]:
        """获取驱动和 CUDA 版本"""
        driver_ver = ''
        cuda_ver = ''

        try:
            result = subprocess.run(
                ['nvidia-smi'],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Driver Version' in line:
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if p == 'Driver':
                                driver_ver = parts[i + 2] if i + 2 < len(parts) else ''
                            elif p == 'CUDA':
                                cuda_ver = parts[i + 1] if i + 1 < len(parts) else ''
                                # 去除冒号
                                cuda_ver = cuda_ver.rstrip(':')
                                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return driver_ver, cuda_ver

    def _infer_architecture(self, name: str, compute_cap: str) -> str:
        """推断 GPU 架构"""
        name_lower = name.lower()

        arch_map = {
            '9.0': 'Hopper', '9.0a': 'Hopper',
            '8.9': 'Ada Lovelace',
            '8.6': 'Ampere', '8.0': 'Ampere',
            '7.5': 'Turing', '7.0': 'Volta',
            '6.1': 'Pascal', '6.0': 'Pascal',
            '5.2': 'Maxwell', '5.0': 'Maxwell',
        }

        if compute_cap in arch_map:
            return arch_map[compute_cap]

        # 基于名称推断
        if 'h100' in name_lower or 'h200' in name_lower:
            return 'Hopper'
        elif 'a100' in name_lower or 'a10' in name_lower or 'a30' in name_lower or 'a40' in name_lower:
            return 'Ampere'
        elif 'l40' in name_lower or 'l4' in name_lower:
            return 'Ada Lovelace'
        elif 'v100' in name_lower:
            return 'Volta'
        elif 't4' in name_lower:
            return 'Turing'
        elif 'p100' in name_lower or 'p40' in name_lower:
            return 'Pascal'
        elif '4090' in name_lower or '4080' in name_lower:
            return 'Ada Lovelace'
        elif '3090' in name_lower or '3080' in name_lower or '3070' in name_lower:
            return 'Ampere'

        return 'Unknown'

    def _generate_recommendations(self, info: Dict) -> Dict[str, Any]:
        """根据 GPU 信息生成推荐配置"""
        devices = info.get('devices', [])
        if not devices:
            return {}

        # 使用第一块 GPU 作为参考
        primary = devices[0]
        total_gpus = len(devices)
        total_vram = primary.memory_total_mb

        rec = {
            'kv_cache': KVCacheConfig(),
            'inference': InferenceConfig(),
        }

        # 根据 VRAM 调整参数
        if total_vram >= 80000:  # 80GB+ (A100-80G, H100)
            rec['kv_cache'].max_num_seqs = 512
            rec['kv_cache'].max_num_batched_tokens = 32768
            rec['kv_cache'].gpu_memory_utilization = 0.92
            rec['inference'].max_model_len = 32768
            rec['inference'].speculative_decoding = True
            rec['inference'].speculative_max_tokens = 8
        elif total_vram >= 40000:  # 40-80GB (A100-40G, A6000)
            rec['kv_cache'].max_num_seqs = 256
            rec['kv_cache'].max_num_batched_tokens = 16384
            rec['kv_cache'].gpu_memory_utilization = 0.9
            rec['inference'].max_model_len = 16384
            rec['inference'].speculative_decoding = True
            rec['inference'].speculative_max_tokens = 5
        elif total_vram >= 24000:  # 24-40GB (4090, 3090, A5000)
            rec['kv_cache'].max_num_seqs = 128
            rec['kv_cache'].max_num_batched_tokens = 8192
            rec['kv_cache'].gpu_memory_utilization = 0.88
            rec['inference'].max_model_len = 8192
        else:  # <24GB (T4, etc.)
            rec['kv_cache'].max_num_seqs = 64
            rec['kv_cache'].max_num_batched_tokens = 4096
            rec['kv_cache'].gpu_memory_utilization = 0.85
            rec['inference'].max_model_len = 4096
            rec['inference'].flash_attention = False  # 低显存可能不支持

        # 多 GPU 推荐
        if total_gpus >= 4 and info.get('nvlink_pairs'):
            rec['inference'].tensor_parallel_size = min(total_gpus, 4)
        elif total_gpus >= 2:
            rec['inference'].tensor_parallel_size = 2

        # Flash Attention 可用性
        arch = primary.architecture
        if arch in ('Hopper', 'Ada Lovelace', 'Ampere'):
            rec['inference'].flash_attention = True
        elif arch == 'Turing':
            rec['inference'].flash_attention = False  # SM75 部分支持
        elif arch in ('CDNA3', 'CDNA2', 'RDNA3', 'Xe_HPG'):
            # AMD/Intel GPU 通过 Flash Attention for ROCm / oneAPI 支持
            rec['inference'].flash_attention = True
        else:
            rec['inference'].flash_attention = False

        # Hopper 架构特有: FP8
        if arch == 'Hopper':
            rec['kv_cache'].cache_dtype = 'fp8_e5m2'

        # AMD CDNA3 (MI300) 特有: FP8 via ROCm
        if arch == 'CDNA3':
            rec['kv_cache'].cache_dtype = 'fp8_e5m2'

        # OpenCL 设备的兼容性调整
        is_opencl_device = info.get('opencl_available') and not info.get('nvidia_smi')
        if is_opencl_device:
            # OpenCL 设备不支持 CUDA Graphs
            rec['inference'].cuda_graphs = False
            # 推荐使用 ROCm/Triton 后端
            rec['inference']._backend_hint = 'rocm' if 'CDNA' in arch or 'RDNA' in arch else 'opencl'

        return {
            'kv_cache': {
                'paged_attention_enabled': rec['kv_cache'].paged_attention_enabled,
                'page_size': rec['kv_cache'].page_size,
                'max_num_seqs': rec['kv_cache'].max_num_seqs,
                'max_num_batched_tokens': rec['kv_cache'].max_num_batched_tokens,
                'gpu_memory_utilization': rec['kv_cache'].gpu_memory_utilization,
                'swap_space_gb': rec['kv_cache'].swap_space_gb,
                'prefix_caching': rec['kv_cache'].prefix_caching,
                'cache_dtype': rec['kv_cache'].cache_dtype,
            },
            'inference': {
                'continuous_batching': rec['inference'].continuous_batching,
                'speculative_decoding': rec['inference'].speculative_decoding,
                'speculative_max_tokens': rec['inference'].speculative_max_tokens,
                'tensor_parallel_size': rec['inference'].tensor_parallel_size,
                'max_model_len': rec['inference'].max_model_len,
                'flash_attention': rec['inference'].flash_attention,
                'cuda_graphs': rec['inference'].cuda_graphs,
            },
        }


class GPUOptimizer:
    """GPU 优化器 - 统一 GPU 相关优化"""

    def __init__(self):
        self.detector = GPUDetector()
        self.info: Dict[str, Any] = {}

    def detect(self) -> Dict[str, Any]:
        """检测并缓存 GPU 信息"""
        self.info = self.detector.detect()
        return self.info

    def is_available(self) -> bool:
        """GPU 是否可用"""
        if not self.info:
            self.detect()
        return self.info.get('available', False)

    def get_optimal_inference_config(self, model_params_b: float = 7.0) -> Dict[str, Any]:
        """
        根据模型大小和 GPU 配置，生成最优推理配置

        Args:
            model_params_b: 模型参数量（十亿）

        Returns:
            Dict: 推理配置
        """
        if not self.info:
            self.detect()

        if not self.is_available():
            return {
                'mode': 'cpu',
                'reason': 'No GPU available',
            }

        devices = self.info.get('devices', [])
        total_gpus = len(devices)
        total_vram = sum(d.memory_total_mb for d in devices)
        model_vram_gb = model_params_b * 2  # FP16 粗估

        config = self.info.get('recommended_config', {})

        # 判断是否需要量化
        if model_vram_gb > total_vram / 1024 * 0.8:
            if model_vram_gb / 4 < total_vram / 1024 * 0.8:
                config.setdefault('inference', {})['quantization'] = 'awq'
            else:
                config.setdefault('inference', {})['quantization'] = 'awq'
                config.setdefault('kv_cache', {})['cache_dtype'] = 'fp8_e5m2'

        config['mode'] = 'gpu'
        config['model_params_b'] = model_params_b
        config['total_gpus'] = total_gpus
        config['total_vram_mb'] = total_vram

        return config

    def get_kv_cache_config(self) -> KVCacheConfig:
        """获取 KV Cache 配置"""
        if not self.info:
            self.detect()

        rec = self.info.get('recommended_config', {})
        kv = rec.get('kv_cache', {})

        return KVCacheConfig(
            paged_attention_enabled=kv.get('paged_attention_enabled', True),
            page_size=kv.get('page_size', 16),
            max_num_seqs=kv.get('max_num_seqs', 256),
            max_num_batched_tokens=kv.get('max_num_batched_tokens', 8192),
            gpu_memory_utilization=kv.get('gpu_memory_utilization', 0.9),
            swap_space_gb=kv.get('swap_space_gb', 4),
            prefix_caching=kv.get('prefix_caching', True),
            cache_dtype=kv.get('cache_dtype', 'auto'),
        )

    def print_status(self):
        """打印 GPU 状态"""
        if not self.info:
            self.detect()

        print("=" * 60)
        print("   GPU 检测与优化状态")
        print("=" * 60)

        if not self.is_available():
            print("  ❌ GPU 不可用")
            print("  可能原因：")
            print("    1. 未安装 NVIDIA 驱动 / AMD ROCm / Intel GPU 驱动")
            print("    2. 未安装 CUDA Toolkit / OpenCL 运行时")
            print("    3. 无 GPU 硬件")
            print("    提示: 安装 pyopencl (pip install pyopencl) 或 clinfo 可检测 AMD/Intel GPU")
            print("=" * 60)
            return

        print(f"  驱动版本: {self.info.get('driver_version', 'N/A')}")
        if self.info.get('cuda_version'):
            print(f"  CUDA 版本: {self.info.get('cuda_version')}")
        if self.info.get('opencl_available'):
            print(f"  OpenCL 版本: {self.info.get('opencl_version', 'N/A')}")
        print(f"  GPU 数量: {self.info.get('device_count', 0)}")
        print(f"  总显存: {self.info.get('total_memory_mb', 0)} MB")
        print()

        for dev in self.info.get('devices', []):
            print(f"  GPU {dev.index}: {dev.name}")
            if dev.compute_capability:
                print(f"    架构: {dev.architecture} (SM {dev.compute_capability})")
            else:
                print(f"    架构: {dev.architecture}")
            print(f"    显存: {dev.memory_free_mb}/{dev.memory_total_mb} MB 可用")
            if dev.temperature_c:
                print(f"    温度: {dev.temperature_c}°C | 功耗: {dev.power_usage_w}/{dev.power_limit_w}W")
                print(f"    GPU利用率: {dev.utilization_gpu}% | 显存利用率: {dev.utilization_memory}%")
            print()

        nvlink = self.info.get('nvlink_pairs', [])
        if nvlink:
            print(f"  NVLink 连接: {nvlink}")
        print()

        rec = self.info.get('recommended_config', {})
        if rec:
            print("  推荐推理配置:")
            inf = rec.get('inference', {})
            for k, v in inf.items():
                print(f"    {k}: {v}")
            print()
            print("  推荐 KV Cache 配置:")
            kv = rec.get('kv_cache', {})
            for k, v in kv.items():
                print(f"    {k}: {v}")

        print("=" * 60)


# 全局实例
gpu_optimizer = GPUOptimizer()


def detect_gpu() -> Dict[str, Any]:
    """检测 GPU（便捷函数）"""
    return gpu_optimizer.detect()


def get_gpu_inference_config(model_params_b: float = 7.0) -> Dict[str, Any]:
    """获取 GPU 推理配置（便捷函数）"""
    return gpu_optimizer.get_optimal_inference_config(model_params_b)


# ============ 导出 ============

__all__ = [
    'GPUDetector',
    'GPUOptimizer',
    'GPUDevice',
    'KVCacheConfig',
    'InferenceConfig',
    'gpu_optimizer',
    'detect_gpu',
    'get_gpu_inference_config',
]


if __name__ == "__main__":
    optimizer = GPUOptimizer()
    optimizer.print_status()

    print("\n=== 7B 模型推理配置 ===")
    config = optimizer.get_optimal_inference_config(model_params_b=7.0)
    for k, v in config.items():
        print(f"  {k}: {v}")

    print("\n=== 70B 模型推理配置 ===")
    config = optimizer.get_optimal_inference_config(model_params_b=70.0)
    for k, v in config.items():
        print(f"  {k}: {v}")
