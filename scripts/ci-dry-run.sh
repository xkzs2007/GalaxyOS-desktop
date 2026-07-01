#!/usr/bin/env bash
# ci:dry — 本地预检 GitHub Actions release.yml 的每一步
#
# 用法:
#   bash scripts/ci-dry-run.sh           # 只编译
#   bash scripts/ci-dry-run.sh --full    # 含 PyInstaller sidecar
#   bash scripts/ci-dry-run.sh --rust    # 只 Rust
#   bash scripts/ci-dry-run.sh --js      # 只 JS
#
# 放在 CI 前跑一遍，90% 的构建问题本地 2 分钟就能发现，
# 不用等 GitHub Actions 排队 15 分钟。

set -e
cd "$(dirname "$0")/.."

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

pass=0
fail=0

check() {
  local label="$1"
  shift
  echo -ne "${CYAN}[check]${NC} ${label} ... "
  if "$@" > /tmp/ci-dry-out.txt 2>&1; then
    echo -e "${GREEN}OK${NC}"
    ((pass++))
  else
    echo -e "${RED}FAIL${NC}"
    cat /tmp/ci-dry-out.txt | tail -20
    ((fail++))
  fi
}

# ── Rust (lfm_server + cdylib) ────────────────────────────
rust_step() {
  echo ""
  echo "═══ Rust native ═══"

  cd extensions/galaxyos/native

  check "lfm_server (--no-default-features)" \
    cargo build --release --bin lfm_server --no-default-features

  # Verify binary exists (separate from the build check)
  local lfm_bin="target/release/lfm_server"
  if [ -f "$lfm_bin" ]; then
    echo -e "  ${GREEN}  ✓ $lfm_bin ($(du -h "$lfm_bin" | cut -f1))${NC}"
  else
    echo -e "  ${RED}  ✗ $lfm_bin MISSING${NC}"
    ((fail++))
  fi

  check "cdylib (--no-default-features)" \
    cargo build --release --lib --no-default-features

  local so="target/release/libgalaxyos_native.so"
  if [ -f "$so" ]; then
    echo -e "  ${GREEN}  ✓ $so ($(du -h "$so" | cut -f1))${NC}"
  else
    echo -e "  ${RED}  ✗ $so MISSING${NC}"
    ((fail++))
  fi

  cd - > /dev/null
}

# ── JS (esbuild main + preload) ──────────────────────────
js_step() {
  echo ""
  echo "═══ JS (esbuild) ═══"

  cd desktop-shell
  mkdir -p dist

  check "main.ts → dist/main.cjs" \
    npx esbuild src/main.ts \
      --bundle --platform=node --target=node20 --format=cjs \
      --outfile=dist/main.cjs \
      --external:electron --external:fsevents --external:zeromq --external:@jboltai/tokui

  check "preload.ts → dist/preload.cjs" \
    npx esbuild src/preload.ts \
      --bundle --platform=node --target=node20 --format=cjs \
      --outfile=dist/preload.cjs \
      --external:electron

  local main_size=$(du -h dist/main.cjs 2>/dev/null | cut -f1 || echo "MISSING")
  local preload_size=$(du -h dist/preload.cjs 2>/dev/null | cut -f1 || echo "MISSING")
  echo -e "  main.cjs: ${GREEN}${main_size}${NC}  preload.cjs: ${GREEN}${preload_size}${NC}"

  cd - > /dev/null
}

# ── NSIS installer 语法检查 ──────────────────────────────
nsis_step() {
  echo ""
  echo "═══ NSIS syntax ═══"

  if command -v makensis &> /dev/null; then
    check "setup.nsh 语法" \
      makensis -PP "$(pwd)/desktop-shell/installer" \
        -O/dev/null desktop-shell/installer/setup.nsh
  else
    echo "  $(tput setaf 3)⏭  makensis 未安装，跳过 NSIS 检查$(tput sgr0)"
  fi
}

# ── Python import 检查（不编译，只验证导入链）────────────
python_step() {
  echo ""
  echo "═══ Python imports ═══"

  cd desktop-shell/python

  # 检查 sibling modules 都能 import（用 python -c "import x"）
  local modules=(
    path_resolver_desktop tokui_dsl llm_providers agent_loop
    memo_stages memo_adapter executive_client ac_router
    tools mcp_client skill_graph galaxy_agent cumulative_regret
    galaxyos_sidecar
  )

  for mod in "${modules[@]}"; do
    check "import ${mod}" \
      python3 -c "import ${mod}" 2>&1
  done

  cd - > /dev/null
}

# ── PyInstaller sidecar（仅 --full）─────────────────────
sidecar_step() {
  echo ""
  echo "═══ PyInstaller sidecar ═══"
  echo "  ⚠  这步需要 2-5 分钟，已安装的 Python 依赖 ~500MB"

  cd desktop-shell/python

  check "pyinstaller galaxyos-sidecar.spec" \
    pyinstaller --clean --noconfirm galaxyos-sidecar.spec

  local sidecar="dist/galaxyos-sidecar"
  if [ -f "$sidecar" ]; then
    echo -e "  ${GREEN}→ $sidecar $(du -h "$sidecar" | cut -f1)${NC}"
  else
    echo -e "  ${RED}→ $sidecar MISSING${NC}"
    ((fail++))
  fi

  cd - > /dev/null
}

# ── Main ─────────────────────────────────────────────────
echo "╔══════════════════════════════════════════╗"
echo "║  GalaxyOS CI Dry Run                    ║"
echo "╚══════════════════════════════════════════╝"

MODE="${1:---all}"

case "$MODE" in
  --rust)   rust_step ;;
  --js)     js_step ;;
  --python) python_step ;;
  --nsis)   nsis_step ;;
  --sidecar) sidecar_step ;;
  --full)
    rust_step
    js_step
    python_step
    nsis_step
    sidecar_step
    ;;
  *)
    rust_step
    js_step
    python_step
    nsis_step
    echo ""
    echo "  $(tput setaf 3)💡 加 --full 运行 PyInstaller 打包（2-5 分钟）$(tput sgr0)"
    ;;
esac

# ── Summary ───────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════"
if [ $fail -eq 0 ]; then
  echo -e "  ${GREEN}✅ 全部通过 ($pass/$((pass+fail)))${NC}"
  echo "  → 可以 push 了"
  exit 0
else
  echo -e "  ${RED}❌ $fail 项失败 ($pass/$((pass+fail)))${NC}"
  exit 1
fi
