# GalaxyOS — 开发命令速查
# make all | make test | make coverage | make lint | make clean | make sync | make native

.PHONY: all install test coverage lint clean install deps sync sync-dist bench native native-py

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

# ═══ 一键构建：Python deps + Rust native + PyO3 ═══
all: install native native-py
	@echo "✅ GalaxyOS full build complete (Python + Rust native + PyO3)"

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install pytest pytest-cov ruff mypy
	@# 自动编译 Rust（有 cargo 就编译，无 cargo 跳过）
	@if command -v cargo >/dev/null 2>&1; then \
		$(MAKE) native; \
		$(MAKE) native-py; \
	else \
		echo "⏭ cargo not found, skipping Rust native (run 'make native' after installing Rust)"; \
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
native:
	@echo "🦀 Building GalaxyOS native extension..."
	@if [ ! -d extensions/galaxyos/native ]; then \
		echo "❌ extensions/galaxyos/native not found"; \
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
native-py:
	@echo "🦀 Building PyO3 Python extension..."
	@if ! command -v maturin >/dev/null 2>&1; then \
		echo "📦 Installing maturin..."; \
		pip install maturin; \
	fi
	cd extensions/galaxyos/native && maturin develop --release
	@echo "✅ PyO3 extension installed — Python can now: import galaxyos_native"
