# LLM Memory Integration - 私有增强包

高性能 LLM 记忆集成框架，提供向量计算、硬件优化、RAG 增强和智能缓存能力。

**当前版本：v3.2.0（Python 3.12 预编译适配）**

## 特性

- **高性能向量计算** - 自动检测 FMA/MKL/SIMD 指令集加速
- **多级缓存系统** - L1/L2/L3/NUMA/DRAM/CXL 智能预取
- **硬件感知优化** - CPU/GPU/内存/IO 协同调度
- **前沿 RAG 能力** - 投机解码、混合检索、命题级检索、上下文压缩
- **预编译沙箱部署** - 无 root/sudo、无编译器即可运行，支持 sing-box 等容器环境
- **跨平台支持** - Linux/macOS/Windows 自动适配，优雅降级

## 快速开始

```bash
# 安装（自动拉取私有增强包）
clawhub install llm-memory-integration

# 私有包自动克隆到：
# ~/.openclaw/workspace/skills/llm-memory-integration/src/privileged/
```

```python
from llm import initialize

result = initialize()
# Platform: Linux x86_64 | Optimizations: numa, hugepages | Tools: 9
```

## 无 Root 沙箱部署

在 sing-box、Docker 等无 root/sudo 权限的容器环境中，预编译方案可免编译直接运行：

```bash
# 1. 构建机上预编译（需要 gcc/编译器，只需执行一次）
python scripts/prebuild.py --all

# 2. 产物自动生成到 prebuilt/ 目录
#    prebuilt/
#    ├── extensions/hnswlib/     # 预编译 hnswlib C 扩展 wheel
#    ├── toolchain/              # 预编译 gcc/g++/cmake 工具链归档
#    └── wheels/                 # 预缓存 wheel

# 3. 将 prebuilt/ 随项目部署到容器，运行时零编译
```

```python
# 容器内使用（无需 root）
from sandbox_manager import CppToolchain, PrebuiltManager

# 加载预编译 hnswlib
pm = PrebuiltManager()
hnswlib = pm.load_prebuilt_extension('hnswlib')

# 安装预编译工具链到用户空间
tc = CppToolchain()
tc.setup_portable_compiler()
```

### 搜索引擎四级降级

`VectorStore` 自动选择最优搜索引擎，无任何 C 扩展也能正常工作：

| 优先级 | 引擎 | 说明 |
|--------|------|------|
| 1 | sqlite-vec | C 扩展，最快 |
| 2 | hnswlib C 扩展 | 预编译嵌入或 pip 安装，O(log n) |
| 3 | 内置 HNSW | 纯 Python，沙箱直接可用，O(log n) |
| 4 | numpy 暴力搜索 | 精确搜索 O(n)，兜底 |

### 关键模块

- **`PrebuiltManager`** - 管理预编译嵌入的二进制产物（扩展 + 工具链 + wheel）
- **`CppToolchain`** - 无 root 安装 C++ 编译工具链（conda-forge 便携版）
- **`SudoTerminal`** - 沙箱环境终端适配器，自动降级到用户空间方案
- **`VectorStore`** - 持久化向量存储，四级搜索降级链

## 核心模块

### 向量计算
- `vector_api.py` - 向量运算、Top-K 搜索、余弦相似度
- `vector_store.py` - 持久化向量存储（SQLite + HNSW + hnswlib）
- `mkl_accelerator.py` - Intel MKL 矩阵加速
- `quantization.py` - 向量量化压缩

### 缓存系统
- `unified_cache.py` - 统一多级缓存
- `rag_cache.py` - RAG 知识树缓存
- `semantic_cache.py` - 语义相似度缓存

### 硬件优化
- `hardware_optimize.py` - 自动硬件检测与调优
- `numa_optimizer.py` - NUMA 亲和性绑定
- `gpu_optimizer.py` - CUDA/OpenCL GPU 加速
- `kunpeng_optimizer.py` - 华为鲲鹏专用优化

### RAG 增强
- `hybrid_search.py` - Dense+Sparse 混合检索
- `speculative_decoder.py` - 投机解码加速推理
- `streaming_llm.py` - 无限长度流式推理
- `crag_pipeline.py` - CRAG 自纠正管线
- `context_compressor.py` - LLMLingua 上下文压缩
- `proposition_retriever.py` - 原子命题级检索

### 沙箱与部署
- `sandbox_manager.py` - 沙箱隔离、无 root 工具链、预编译管理
- `scripts/prebuild.py` - 预编译构建脚本（hnswlib + 工具链）

### 资源管理
- `resource_orchestrator.py` - 统一资源编排
- `power_manager.py` - DVFS 电源管理
- `io_optimizer.py` - I/O 调度优化

## 版本历史

| 版本 | 主要变更 |
|------|----------|
| **v3.2.0** | Python 3.12 预编译适配：hnswlib/toolchain 重编译为 cp312、修复 `_is_wheel_compatible()` 严格版本匹配、修复 prebuild 脚本构建隔离策略 |
| v3.1.0 | 预编译沙箱支持：PrebuiltManager、无 root 工具链、hnswlib C 扩展嵌入、四级搜索降级 |
| v3.0.5 | 模块协同与进程管理优化；15 项稳定性修复；生产就绪 |
| v3.0.0 | 7 项论文级模块（投机解码、混合检索、命题检索、上下文压缩、RAG 失败检测、Late Chunking、StreamingLLM）；CRAG 自纠正管线 |
| v2.1.0 | 资源编排器、统一缓存、向量 API、I/O 优化器 |
| v2.0.0 | GPU 检测、安全对齐、检索评估 |
| v1.4.0 | 14 个搜索增强子模块 |
| v1.0.0 | 初始版本 |

## 依赖

| 级别 | 依赖 | 用途 |
|------|------|------|
| 必须 | numpy (Python 3.8+) | 向量计算 |
| 推荐 | pysqlite3-binary | 向量搜索 |
| 可选 | scipy, scikit-learn, aiohttp, pyopencl | 扩展功能 |

## 测试

```bash
python3 -m unittest test_suite -v
```

## 安全保障

- 参数类型与范围校验
- SHA-256 路径哈希防护
- 扩展白名单机制
- 线程安全锁保护
- 连接资源安全释放
- 预编译产物平台/版本兼容性校验

## 许可证

MIT License

## 链接

- [CNB 仓库](https://cnb.cool/llm-memory-integrat/llm)
- [ClawHub 主页](https://clawhub.ai/xkzs2007/llm-memory-integration)
- [Gitee 镜像](https://gitee.com/xkzs2007/llm-memory-integration)
