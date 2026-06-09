# GalaxyOS — 开发命令速查
# make test | make coverage | make lint | make clean | make sync | make native

.PHONY: test coverage lint clean install deps sync bench native

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install pytest pytest-cov ruff mypy

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

# ── sync: 从 galaxyos/engine/ 同步到 extensions/galaxyos/dist/scripts/ ──
sync:
	@echo "🔁 Syncing galaxyos/engine/ → extensions/galaxyos/dist/scripts/"
	@cp galaxyos/engine/*.py extensions/galaxyos/dist/scripts/
	@cp galaxyos/engine/pil_worker.py extensions/galaxyos/dist/scripts/ 2>/dev/null || true
	@echo "✅ Sync complete"

# ── bench: 认知效果评估（GAT 注意力权重 A/B 测试）──
bench:
	$(PYTHON) tests/cognitive_ablation.py

# ── native: 编译 Rust 原生扩展 ──
native:
	@echo "🦀 Building GalaxyOS native extension..."
	cd extensions/claw-core/native && cargo build --release
	@echo "✅ Native binary: extensions/claw-core/native/target/release/galaxyos-native"
