# GalaxyOS — 开发命令速查
# make all | make test | make coverage | make lint | make clean | make sync | make native

.PHONY: all install test coverage lint clean install deps sync sync-dist bench native native-py native-libs

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

# ═══ 一键构建：Python deps + (Rust native + PyO3) 或 pre-built libs ═══
all: install native native-py
	@echo "✅ GalaxyOS full build complete (Python + Rust native + PyO3)"

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install pytest pytest-cov ruff mypy
	@# 优先编译 Rust；无 cargo 则从 libs/ 解包预编译版本
	@if command -v cargo >/dev/null 2>&1; then \
		$(MAKE) native; \
		$(MAKE) native-py; \
	else \
		echo "⏭ cargo not found — extracting pre-built libs/ instead"; \
		$(MAKE) native-libs; \
	fi

$(VENV):
	$(PYTHON) -m venv $(VENV)

deps:
	$(PIP) install -r requirements.txt

test:
	$(PYTEST) tests/ -v --tb=short -p no:warnings

coverage:
	$(PYTEST) tests/ -v --tb=short \
		--cov=services \
		--cov-report=term-missing \
		--cov-report=html \
		-p no:warnings

lint:
	ruff check --select=E,F,W --ignore=E501,E402 services/ || true
	ruff check --select=E,F,W --ignore=E501,E402 tests/ || true

typecheck:
	mypy services/ --ignore-missing-imports --no-error-summary || true

ci: lint test
	@echo "✅ CI checks passed"

clean:
	rm -rf __pycache__ .pytest_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true

# ── sync: 从 galaxyos/engine/ 同步到 extensions/galaxyos/scripts/ + 自动编译 Rust ──
sync:
	@echo "🔁 Syncing galaxyos/engine/ → extensions/galaxyos/scripts/"
	@mkdir -p extensions/galaxyos/scripts
	@cp galaxyos/engine/*.py extensions/galaxyos/scripts/
	@cp galaxyos/engine/pil_worker.py extensions/galaxyos/scripts/ 2>/dev/null || true
	@if command -v cargo >/dev/null 2>&1; then \
		$(MAKE) native; \
		$(MAKE) native-py; \
	fi
	@echo "✅ Sync complete"

# ── sync+dist: 同时复制并构建 JS dist ──
sync-dist: sync
	@echo "📦 Creating dist/ entry for extensions/galaxyos..."
	@mkdir -p extensions/galaxyos/dist
	@cp extensions/galaxyos/index.js extensions/galaxyos/dist/index.js 2>/dev/null || true
	@echo "✅ dist/ ready"

# ── bench: 认知效果评估（GAT 注意力权重 A/B 测试）──
bench:
	$(PYTHON) tests/cognitive_ablation.py

# ── native: 编译 Rust 原生扩展（二进制）──
#    使用 native/.cargo/config.toml 中的 rsproxy 镜像源
native:
	@echo "🦀 Building GalaxyOS native extension..."
	@if [ ! -d extensions/galaxyos/native ]; then \
		echo "❌ extensions/galaxyos/native not found"; \
		exit 1; \
	fi
	@if ! command -v cargo >/dev/null 2>&1; then \
		echo "❌ cargo not found — run: make rustup-cn"; \
		exit 1; \
	fi
	cd extensions/galaxyos/native && cargo build --release
	@echo "✅ Native binary: extensions/galaxyos/native/target/release/galaxyos-native"
	@# 复制到所有查找路径（fast_pil.py 多路径搜索 + JS 插件搜索）
	@mkdir -p native/target/release
	@mkdir -p extensions/galaxyos/scripts
	cp extensions/galaxyos/native/target/release/galaxyos-native native/target/release/galaxyos-native 2>/dev/null || true
	cp extensions/galaxyos/native/target/release/galaxyos-native extensions/galaxyos/scripts/galaxyos-native 2>/dev/null || true
	@echo "✅ Copied to native/target/release/ + extensions/galaxyos/scripts/"

# ── native-py: 编译 PyO3 Python 扩展（pip 安装后直接 import galaxyos_native）──
#    使用 native/.cargo/config.toml 中的 rsproxy 镜像源
native-py:
	@echo "🦀 Building PyO3 Python extension..."
	@if ! command -v cargo >/dev/null 2>&1; then \
		echo "❌ cargo not found — run: make rustup-cn"; \
		exit 1; \
	fi
	@if ! command -v maturin >/dev/null 2>&1; then \
		echo "📦 Installing maturin..."; \
		pip install maturin; \
	fi
	cd extensions/galaxyos/native && maturin develop --release
	@echo "✅ PyO3 extension installed — Python can now: import galaxyos_native"

# ── rustup-cn: 一键安装 Rust 工具链（国内镜像加速）──
rustup-cn:
	@echo "🦀 Installing Rust via Chinese mirrors..."
	@if command -v cargo >/dev/null 2>&1; then \
		echo "✅ Rust already installed: $$(rustc --version)"; \
	else \
		echo "  → Detecting architecture..."; \
		ARCH=$$(uname -m); \
		case "$$ARCH" in \
			x86_64) RUSTUP_ARCH="x86_64-unknown-linux-gnu" ;; \
			aarch64|arm64) RUSTUP_ARCH="aarch64-unknown-linux-gnu" ;; \
			armv7l) RUSTUP_ARCH="armv7-unknown-linux-gnueabihf" ;; \
			*) echo "❌ Unsupported arch: $$ARCH"; exit 1 ;; \
		esac; \
		echo "  → Arch: $$ARCH, downloading rustup-init from TUNA mirror..."; \
		curl --proto '=https' --tlsv1.2 -sSf "https://mirrors.tuna.tsinghua.edu.cn/rustup/rustup/archive/1.28.1/$$RUSTUP_ARCH/rustup-init" -o /tmp/rustup-init; \
		chmod +x /tmp/rustup-init; \
		RUSTUP_DIST_SERVER=https://mirrors.tuna.tsinghua.edu.cn/rustup \
		RUSTUP_UPDATE_ROOT=https://mirrors.tuna.tsinghua.edu.cn/rustup/rustup \
		/tmp/rustup-init -y --default-toolchain stable; \
		rm -f /tmp/rustup-init; \
		echo "  → Rust installed: $$($$HOME/.cargo/bin/rustc --version)"; \
		echo "  → Run: source $$HOME/.cargo/env"; \
	fi
	@echo "✅ Cargo mirror configured: extensions/galaxyos/native/.cargo/config.toml → rsproxy.cn"

# ── native-libs: 从 libs/ 解包安装预编译扩展（无 cargo 时的 fallback）──
#    当 Rust 不可用时，使用 libs/ 中的预编译包：
#    - hnswlib-*.tar.gz  → 原生向量检索 .so
#    - galaxyos_native-*.tar.gz → 图像/向量计算（纯 Python shim 或编译 .so）
#    - mkl-core-*.tar.gz / tbb-*.tar.gz → Intel 数学库（提取到 openclaw 扩展目录）
native-libs:
	@echo "📦 Extracting pre-built native libs..."
	@mkdir -p $(VENV)/lib/python3.12/site-packages/galaxyos_native
	@for f in libs/galaxyos_native-*.tar.gz; do \
		if [ -f "$$f" ]; then \
			echo "  → $$f"; \
			tar xzf "$$f" -C $(VENV)/lib/python3.12/site-packages/; \
		fi; \
	done
	@# hnswlib: 向量检索加速
	@for f in libs/hnswlib-*.tar.gz; do \
		if [ -f "$$f" ]; then \
			echo "  → $$f"; \
			tar xzf "$$f" -C $(VENV)/lib/python3.12/site-packages/; \
		fi; \
	done
	@# 同步到 extensions/galaxyos/scripts/ 供 JS 端 import
	@mkdir -p extensions/galaxyos/scripts
	@cp $(VENV)/lib/python3.12/site-packages/galaxyos_native/__init__.py extensions/galaxyos/scripts/galaxyos_native.py 2>/dev/null || true
	@echo "✅ Pre-built libs installed — import galaxyos_native ready"

# ════════════════════════════════════════════════════════════════
# 跨平台交叉编译 — 4 目标: Linux x64/ARM64 + Windows x64/ARM64
# ════════════════════════════════════════════════════════════════

# 目标三元组
TARGET_LINUX_X64   := x86_64-unknown-linux-gnu
TARGET_LINUX_ARM64 := aarch64-unknown-linux-gnu
TARGET_WIN_X64     := x86_64-pc-windows-msvc
TARGET_WIN_ARM64   := aarch64-pc-windows-msvc

# 预编译包输出目录
PREBUILT_DIR := extensions/galaxyos/native/prebuilt

# ── native-cross: 安装交叉编译工具链 ──
native-cross:
	@echo "📦 Installing cross-compile targets..."
	@rustup target add $(TARGET_LINUX_X64) 2>/dev/null || true
	@rustup target add $(TARGET_LINUX_ARM64) 2>/dev/null || true
	@echo "✅ Cross-compile targets installed"
	@echo "⚠️  Windows targets require Visual Studio Build Tools (MSVC) on Windows host"
	@echo "   Or use 'cargo install cross' for Docker-based cross compilation"

# ── native-build-all: 编译所有平台 ──
native-build-all: native-build-linux-x64 native-build-linux-arm64
	@echo "✅ All available targets built (see $(PREBUILT_DIR)/)"

# ── native-build-linux-x64: Linux x86_64 ──
native-build-linux-x64:
	@echo "🦀 Building for Linux x86_64..."
	@cd extensions/galaxyos/native && cargo build --release --target $(TARGET_LINUX_X64)
	@mkdir -p $(PREBUILT_DIR)/linux-x64
	@cp extensions/galaxyos/native/target/$(TARGET_LINUX_X64)/release/galaxyos-native $(PREBUILT_DIR)/linux-x64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_LINUX_X64)/release/libgalaxyos_native.so $(PREBUILT_DIR)/linux-x64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_LINUX_X64)/release/lfm_server $(PREBUILT_DIR)/linux-x64/ 2>/dev/null || true
	@echo "✅ Linux x86_64 → $(PREBUILT_DIR)/linux-x64/"

# ── native-build-linux-arm64: Linux ARM64 ──
native-build-linux-arm64:
	@echo "🦀 Building for Linux ARM64 (aarch64)..."
	@cd extensions/galaxyos/native && cargo build --release --target $(TARGET_LINUX_ARM64)
	@mkdir -p $(PREBUILT_DIR)/linux-arm64
	@cp extensions/galaxyos/native/target/$(TARGET_LINUX_ARM64)/release/galaxyos-native $(PREBUILT_DIR)/linux-arm64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_LINUX_ARM64)/release/libgalaxyos_native.so $(PREBUILT_DIR)/linux-arm64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_LINUX_ARM64)/release/lfm_server $(PREBUILT_DIR)/linux-arm64/ 2>/dev/null || true
	@echo "✅ Linux ARM64 → $(PREBUILT_DIR)/linux-arm64/"

# ── native-build-win-x64: Windows x86_64 (需 Windows 主机或 cross) ──
native-build-win-x64:
	@echo "🦀 Building for Windows x86_64..."
	@cd extensions/galaxyos/native && cargo build --release --target $(TARGET_WIN_X64)
	@mkdir -p $(PREBUILT_DIR)/windows-x64
	@cp extensions/galaxyos/native/target/$(TARGET_WIN_X64)/release/galaxyos-native.exe $(PREBUILT_DIR)/windows-x64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_WIN_X64)/release/galaxyos_native.dll $(PREBUILT_DIR)/windows-x64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_WIN_X64)/release/lfm_server.exe $(PREBUILT_DIR)/windows-x64/ 2>/dev/null || true
	@echo "✅ Windows x86_64 → $(PREBUILT_DIR)/windows-x64/"

# ── native-build-win-arm64: Windows ARM64 (需 Windows 主机或 cross) ──
native-build-win-arm64:
	@echo "🦀 Building for Windows ARM64..."
	@cd extensions/galaxyos/native && cargo build --release --target $(TARGET_WIN_ARM64)
	@mkdir -p $(PREBUILT_DIR)/windows-arm64
	@cp extensions/galaxyos/native/target/$(TARGET_WIN_ARM64)/release/galaxyos-native.exe $(PREBUILT_DIR)/windows-arm64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_WIN_ARM64)/release/galaxyos_native.dll $(PREBUILT_DIR)/windows-arm64/ 2>/dev/null || true
	@cp extensions/galaxyos/native/target/$(TARGET_WIN_ARM64)/release/lfm_server.exe $(PREBUILT_DIR)/windows-arm64/ 2>/dev/null || true
	@echo "✅ Windows ARM64 → $(PREBUILT_DIR)/windows-arm64/"

# ── native-package: 打包预编译二进制为 tar.gz ──
native-package:
	@echo "📦 Packaging prebuilt binaries..."
	@mkdir -p libs
	@for platform in linux-x64 linux-arm64 windows-x64 windows-arm64; do \
		dir="$(PREBUILT_DIR)/$$platform"; \
		if [ -d "$$dir" ] && [ "$$(ls -A $$dir 2>/dev/null)" ]; then \
			tarball="libs/galaxyos-native-0.2.0-$$platform.tar.gz"; \
			tar czf "$$tarball" -C "$$dir" .; \
			echo "  ✅ $$tarball"; \
		else \
			echo "  ⏭️  $$platform: not built (skip)"; \
		fi; \
	done
	@echo "✅ Packaging complete — see libs/galaxyos-native-*.tar.gz"

# ── native-install-prebuilt: 安装预编译二进制（自动检测平台）──
native-install-prebuilt:
	@echo "📦 Installing prebuilt binary (auto-detect platform)..."
	@ARCH=$$(uname -m); \
	OS=$$(uname -s 2>/dev/null || echo Linux); \
	case "$$OS:$$ARCH" in \
		Linux:x86_64|Linux:amd64) PLATFORM="linux-x64" ;; \
		Linux:aarch64|Linux:arm64) PLATFORM="linux-arm64" ;; \
		MINGW*:x86_64|CYGWIN*:x86_64|Windows:x86_64) PLATFORM="windows-x64" ;; \
		MINGW*:aarch64|MINGW*:arm64|Windows:arm64) PLATFORM="windows-arm64" ;; \
		Darwin:x86_64|Darwin:amd64) PLATFORM="linux-x64" ;; \
		Darwin:arm64|Darwin:aarch64) PLATFORM="linux-arm64" ;; \
		*) echo "❌ Unsupported platform: $$OS:$$ARCH"; exit 1 ;; \
	esac; \
	echo "  → Platform: $$PLATFORM"; \
	TARBALL="libs/galaxyos-native-0.2.0-$$PLATFORM.tar.gz"; \
	if [ ! -f "$$TARBALL" ]; then \
		echo "❌ Prebuilt package not found: $$TARBALL"; \
		echo "   Run: make native-build-$$PLATFORM && make native-package"; \
		exit 1; \
	fi; \
	mkdir -p extensions/galaxyos/scripts extensions/galaxyos/native; \
	tar xzf "$$TARBALL" -C extensions/galaxyos/scripts/; \
	case "$$PLATFORM" in \
		linux-*) chmod +x extensions/galaxyos/scripts/galaxyos-native 2>/dev/null || true ;; \
		windows-*) echo "  → Windows binaries installed" ;; \
	esac; \
	echo "✅ Prebuilt binary installed from $$PLATFORM"
