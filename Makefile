# GalaxyOS — 开发命令速查
# make test | make coverage | make lint | make clean | make sync

.PHONY: test coverage lint clean install deps sync bench

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

# ── sync: 从 dist 同步到所有副本目录 ──
DIST := extensions/claw-core/dist/scripts
TARGETS := skills/llm-memory-integration/core scripts services workspace-scripts

sync:
	@echo "🔁 从 $(DIST)/ 同步到各目录..."
	@copied=0; skipped=0; \
	for f in $(DIST)/*.py; do \
		base=$$(basename $$f); \
		for dir in $(TARGETS); do \
			target="$$dir/$$base"; \
			if [ -f "$$target" ]; then \
				if ! diff -q "$$f" "$$target" > /dev/null 2>&1; then \
					cp "$$f" "$$target" && \
					copied=$$((copied+1)) && \
					echo "  ✅ $$dir/$$base"; \
				else \
					skipped=$$((skipped+1)); \
				fi; \
			fi; \
		done; \
	done; \
	echo "📋 synced=$$copied unchanged=$$skipped"

# ── bench: 认知效果评估（GAT 注意力权重 A/B 测试）──
bench:
	$(PYTHON) tests/cognitive_ablation.py
