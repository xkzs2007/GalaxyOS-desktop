#!/bin/bash
# mkl_wrapper.sh — LD_PRELOAD 劫持 numpy BLAS 到 MKL
# 无需重新编译 numpy。原理：numpy 2.4.5 的 64-bit BLAS 符号
# (scipy_cblas_dgemm64_ 等) 通过 wrapper .so 转发到 MKL 的 cblas_dgemm_64_。
#
# 用法:
#   eval "$(mkl_wrapper.sh)"          → 设置 MKL 环境变量
#   mkl_wrapper.sh verify             → 验证 MKL 是否生效
#   mkl_wrapper.sh test               → 基准测试对比
#   mkl_wrapper.sh build              → 仅编译 wrapper .so
#   mkl_wrapper.sh install            → 安装到 ~/.local/lib
#
# 环境变量:
#   MKL_LIB_DIR — MKL lib 目录（默认 /home/sandbox/.local/mkl/mkl/lib）

set -eo pipefail

MKL_LIB_DIR="${MKL_LIB_DIR:-/home/sandbox/.local/mkl/mkl/lib}"
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
WRAPPER_DIR="${WRAPPER_DIR:-/tmp}"
WRAPPER_SO="${WRAPPER_DIR}/libmkl_wrapper.so"
WRAPPER_SRC="${SCRIPT_DIR}/mkl_wrapper.c"

detect_mkl() {
    local so="${MKL_LIB_DIR}/libmkl_rt.so.3"
    if [ ! -f "$so" ]; then
        echo "[mkl] MKL 未找到: $so" >&2
        return 1
    fi
    if ! nm -D "$so" 2>/dev/null | grep "cblas_dgemm_64_" >/dev/null; then
        echo "[mkl] MKL 库缺少 cblas_dgemm_64_ 符号" >&2
        return 1
    fi
    return 0
}

build_wrapper() {
    if [ ! -f "$WRAPPER_SRC" ]; then
        echo "[build] C 源码未找到: $WRAPPER_SRC" >&2
        return 1
    fi
    local cc=""
    for c in /usr/bin/gcc /usr/bin/cc /bin/gcc /bin/cc; do
        [ -x "$c" ] && cc="$c" && break
    done
    [ -z "$cc" ] && { echo "[build] 找不到 C 编译器" >&2; return 1; }

    $cc -O2 -fPIC -shared -o "$WRAPPER_SO" "$WRAPPER_SRC" -ldl -lc
    local n=$(nm -D "$WRAPPER_SO" 2>/dev/null | grep -c "T scipy")
    echo "[build] $WRAPPER_SO ($(du -h "$WRAPPER_SO" | cut -f1), ${n}/22 symbols)" >&2
}

print_env() {
    detect_mkl 2>/dev/null || return 1
    [ -f "$WRAPPER_SO" ] || build_wrapper
    echo "export LD_PRELOAD=\"$WRAPPER_SO\""
    echo "export LD_LIBRARY_PATH=\"$MKL_LIB_DIR\""
    echo "export MKL_THREADING_LAYER=GNU"
}

benchmark() {
    detect_mkl >&2 || return 1
    [ -f "$WRAPPER_SO" ] || build_wrapper

    echo "=== OpenBLAS (500x500, 200次) ==="
    unset LD_PRELOAD
    unset MKL_THREADING_LAYER
    python3 -c "
import numpy as np, time
a = np.random.rand(500, 500)
b = np.random.rand(500, 500)
_ = a @ b
t0 = time.perf_counter()
for _ in range(200): c = a @ b
dt = time.perf_counter() - t0
print('  OpenBLAS: {:.0f}ms'.format(dt*1000))
"

    echo "=== MKL (500x500, 200次) ==="
    LD_PRELOAD="$WRAPPER_SO" LD_LIBRARY_PATH="$MKL_LIB_DIR" MKL_THREADING_LAYER=GNU \
    python3 -c "
import numpy as np, time
a = np.random.rand(500, 500)
b = np.random.rand(500, 500)
_ = a @ b
t0 = time.perf_counter()
for _ in range(200): c = a @ b
dt = time.perf_counter() - t0
print('  MKL:       {:.0f}ms'.format(dt*1000))
"
}

verify() {
    detect_mkl >&2 || return 1
    [ -f "$WRAPPER_SO" ] || build_wrapper

    export LD_PRELOAD="$WRAPPER_SO"
    export LD_LIBRARY_PATH="$MKL_LIB_DIR"
    export MKL_THREADING_LAYER=GNU

    MKL_VERBOSE=1 python3 -c "
import numpy as np
a = np.random.rand(50, 50)
b = np.random.rand(50, 50)
c = a @ b
ok = c.sum() == c.sum() and c.sum() > 0
print('OK' if ok else 'FAIL')
" 2>&1
}

install_wrapper() {
    local d="${HOME}/.local/lib"
    mkdir -p "$d"
    build_wrapper
    cp "$WRAPPER_SO" "$d/libmkl_wrapper.so"
    echo "已安装: $d/libmkl_wrapper.so"
    echo ""
    echo "添加到 ~/.bashrc:"
    echo "  export LD_PRELOAD=\"\$HOME/.local/lib/libmkl_wrapper.so\""
    echo "  export LD_LIBRARY_PATH=\"$MKL_LIB_DIR\""
    echo "  export MKL_THREADING_LAYER=GNU"
}

case "${1:-}" in
    env)    print_env ;;
    build)  build_wrapper ;;
    test|bench) benchmark ;;
    verify) verify ;;
    install) install_wrapper ;;
    status)
        detect_mkl 2>/dev/null && echo "[mkl] OK" || echo "[mkl] 未找到"
        [ -f "$WRAPPER_SO" ] && echo "[wrap] $WRAPPER_SO ($(du -h "$WRAPPER_SO" | cut -f1))" || echo "[wrap] 未编译"
        ;;
    *)      print_env ;;
esac
