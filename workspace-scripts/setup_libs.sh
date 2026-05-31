#!/bin/bash
# ===============================================================
# 小艺 Claw 系统 — 预编译原生库部署脚本
# 用途：将 libs/ 下的预编译包解压到 repo/lib/ 或 site-packages
# 用法：bash workspace-scripts/setup_libs.sh
# 要求：Linux x86_64, Python 3.12
# ===============================================================

set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

REPO_LIB="$DIR/repo/lib"
PYTHON_SITE="$DIR/repo/lib/python3.12/site-packages"
LIBS_DIR="$DIR/libs"

# 确定 Python site-packages 路径
if [ -z "$PYTHON_SITE" ]; then
    PYTHON_SITE=$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>/dev/null) || true
fi

echo "=== 1/3: 部署 MKL 核心库 → repo/lib/ ==="
if [ -f "$LIBS_DIR/mkl-core-2026.0.0-x86_64.tar.gz" ]; then
    mkdir -p "$REPO_LIB"
    tar xzf "$LIBS_DIR/mkl-core-2026.0.0-x86_64.tar.gz" -C "$REPO_LIB/"
    echo "  ✅ MKL 库已解压到 $REPO_LIB"
    echo "     文件: $(ls $REPO_LIB/libmkl*.so.* | wc -l) 个"
else
    echo "  ⚠️  未找到 mkl-core 预编译包，跳过"
fi

echo ""
echo "=== 2/3: 部署 TBB 线程库 → repo/lib/ ==="
if [ -f "$LIBS_DIR/tbb-2023.0.0-x86_64.tar.gz" ]; then
    mkdir -p "$REPO_LIB"
    tar xzf "$LIBS_DIR/tbb-2023.0.0-x86_64.tar.gz" -C "$REPO_LIB/"
    echo "  ✅ TBB 库已解压到 $REPO_LIB"
    echo "     文件: $(ls $REPO_LIB/libtbb*.so.* | wc -l) 个"
else
    echo "  ⚠️  未找到 tbb 预编译包，跳过"
fi

echo ""
echo "=== 3/3: 部署 hnswlib → site-packages ==="
TARGET_SITE=""
if [ -d "$PYTHON_SITE" ]; then
    TARGET_SITE="$PYTHON_SITE"
elif [ -d "$DIR/repo/lib/python3.12/site-packages" ]; then
    TARGET_SITE="$DIR/repo/lib/python3.12/site-packages"
else
    # 尝试自动发现
    SITE_CANDIDATE=$(python3 -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>/dev/null) || true
    if [ -n "$SITE_CANDIDATE" ] && [ -d "$SITE_CANDIDATE" ]; then
        TARGET_SITE="$SITE_CANDIDATE"
    fi
fi

if [ -n "$TARGET_SITE" ] && [ -f "$LIBS_DIR/hnswlib-0.8.0-x86_64.tar.gz" ]; then
    tar xzf "$LIBS_DIR/hnswlib-0.8.0-x86_64.tar.gz" -C "$TARGET_SITE/"
    echo "  ✅ hnswlib 已解压到 $TARGET_SITE"
elif [ -z "$TARGET_SITE" ]; then
    echo "  ⚠️  无法确定 site-packages 路径，跳过"
else
    echo "  ⚠️  未找到 hnswlib 预编译包，跳过"
fi

echo ""
echo "=== 完成 ==="
echo "建议运行 ldconfig 或 export LD_LIBRARY_PATH=$REPO_LIB 让系统找到 MKL/TBB"
echo ""
echo "MKL 用法（可选，加速 numpy）："
echo "  export LD_PRELOAD=$REPO_LIB/libmkl_rt.so.3"
echo "  export MKL_NUM_THREADS=1"
echo "  python3 -c \"import numpy; print('OK')\""
