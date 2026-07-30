# SPDX-License-Identifier: Apache-2.0
# One command per gate, matching .github/workflows/ci.yml exactly — if `make
# check` passes locally, CI passes. Bare `pip`/`python` may be Python 2 on
# macOS, so everything goes through python3 -m.
PY := python3

.DEFAULT_GOAL := help
.PHONY: help install check test cov lint types spdx hooks clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Install the package with dev extras, and the pre-commit hooks
	$(PY) -m pip install -e ".[dev]"
	pre-commit install

check: lint types test  ## Everything CI runs (lint, types, tests)

test:  ## Run the test suite
	$(PY) -m pytest -q -m "not allow_network"

cov:  ## Run the suite with coverage, enforcing the floor in pyproject.toml
	$(PY) -m pytest -q -m "not allow_network" --cov --cov-report=term-missing

lint:  ## ruff + SPDX headers
	ruff check src tests tools
	$(PY) tools/check_spdx.py

types:  ## mypy
	mypy src/jobhunter

hooks:  ## Run every pre-commit hook over the whole tree
	pre-commit run --all-files

clean:  ## Remove caches and coverage artefacts
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
