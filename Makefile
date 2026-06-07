# GalaxyOS — 开发命令速查
# make test | make coverage | make lint | make clean

.PHONY: test coverage lint clean install deps

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
