#!/bin/bash
# ===============================================================
# 小艺 Claw 系统环境部署脚本
# 用途：新容器/新机器上一键安装所有依赖
# 用法：bash scripts/setup.sh
# 要求：Linux x86_64, Python 3.12, 已安装 pip
#
# 不依赖 conda。所有核心库走 PyPI wheels（scipy-openblas 为 BLAS/LAPACK）
# MKL（Intel Math Kernel Library）未使用，因为 Python 3.12 从源码编译
# numpy+MKL 存在 distutils 缺失问题，改走 PyPI 预编译 wheel 方案。
# ===============================================================

set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "=== 1/6: 系统包（gcc, sqlite3, ssl）==="
# 华为云 EulerOS 用 yum; Ubuntu/Debian 用 apt
if command -v yum &>/dev/null; then
    sudo yum install -y gcc gcc-c++ make sqlite-devel openssl-devel 2>/dev/null || \
    echo "[跳过] yum 失败，可能需要 sudo"
elif command -v apt &>/dev/null; then
    sudo apt update -qq && sudo apt install -y gcc g++ make libsqlite3-dev libssl-dev 2>/dev/null || \
    echo "[跳过] apt 失败，可能需要 sudo"
else
    echo "[警告] 请手动安装: gcc g++ make sqlite3-dev libssl-dev"
fi

echo "=== 2/6: pip 升级 + 依赖安装 ==="
pip install --upgrade pip setuptools wheel --quiet 2>&1 | tail -1

echo "=== 3/6: 核心基础库 ==="
pip install \
    numpy==1.26.4 \
    scipy==1.16.2 \
    --quiet 2>&1 | tail -1

echo "=== 4/6: 向量与数据库 ==="
pip install \
    faiss-cpu==1.13.2 \
    hnswlib==0.8.0 \
    duckdb==1.5.2 \
    lancedb==0.29.2 \
    pysqlite3-binary \
    --quiet 2>&1 | tail -1

echo "=== 5/6: 推理加速与序列化 ==="
pip install \
    onnxruntime==1.24.4 \
    polars==1.40.1 \
    orjson==3.11.8 \
    uvloop==0.22.1 \
    --quiet 2>&1 | tail -1

echo "=== 6/6: NLP 与 ML 框架 ==="
pip install \
    jieba \
    sentence-transformers \
    transformers \
    --quiet 2>&1 | tail -1

echo "=== 检查: numpy SIMD 能力 ==="
python3 -c "
import numpy as np
c = np.show_config(mode='dicts')
simd = c.get('SIMD Extensions', {})
found = simd.get('found', [])
if 'AVX512_ICL' in found:
    print('✅ AVX-512 ICL（最佳性能）')
elif 'AVX512' in ''.join(found):
    print('⚠️ AVX-512 部分可用')
elif 'V4' in ''.join(found):
    print('✅ AVX-512 VNNI/AVX2（主流性能）')
else:
    print(f'ℹ️ SIMD: {found}')
"

echo "=== 验证: 关键模块 ==="
python3 -c "
import numpy, scipy, faiss, duckdb, lancedb, orjson, polars
print('✅ numpy', numpy.__version__)
print('✅ scipy', scipy.__version__)
print('✅ faiss', faiss.__version__)
print('✅ duckdb', duckdb.__version__)
print('✅ lancedb', lancedb.__version__)
print('✅ orjson', orjson.__version__)
print('✅ polars', polars.__version__)
" 2>&1

echo ""
echo "=== ✅ 环境部署完成 ==="
echo "BLAS 后端: scipy-openblas（PyPI wheel）"
echo "数据目录复制说明:"
echo "  cp -r workspace /新路径/workspace"
echo "  cp -r .dag_context.db /新路径/"
echo "  cp -r vectors.db /新路径/"
echo "启动 Worker:"
echo "  python3 scripts/claw_worker.py"
