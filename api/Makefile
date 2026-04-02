.PHONY: build clean clean-build install test lint format dev setup-hooks

# =============================================================================
# Lambda zip build
# =============================================================================

BUILD_DIR := build

build: clean-build
	@echo "Building cache-layer-api.zip..."
	mkdir -p $(BUILD_DIR)/api-package
	uv export --no-hashes --no-dev > $(BUILD_DIR)/requirements.txt
	# Separate git/source deps (no pre-built wheels) from binary-only deps
	grep -E '^[a-zA-Z0-9_-]+ @ (git\+|file:)' $(BUILD_DIR)/requirements.txt > $(BUILD_DIR)/requirements-source.txt || true
	grep -vE '^[a-zA-Z0-9_-]+ @ (git\+|file:)|^-e ' $(BUILD_DIR)/requirements.txt > $(BUILD_DIR)/requirements-binary.txt || true
	uv pip install \
		--python-platform aarch64-manylinux2014 \
		--python-version 3.12 \
		--only-binary :all: \
		--target $(BUILD_DIR)/api-package \
		-r $(BUILD_DIR)/requirements-binary.txt
	if [ -s $(BUILD_DIR)/requirements-source.txt ]; then \
		uv pip install \
			--python-platform aarch64-manylinux2014 \
			--python-version 3.12 \
			--target $(BUILD_DIR)/api-package \
			-r $(BUILD_DIR)/requirements-source.txt; \
	fi
	cp -r src $(BUILD_DIR)/api-package/
	cd $(BUILD_DIR)/api-package && zip -q -r ../cache-layer-api.zip .
	rm -rf $(BUILD_DIR)/api-package $(BUILD_DIR)/requirements.txt $(BUILD_DIR)/requirements-binary.txt $(BUILD_DIR)/requirements-source.txt
	@echo "Built $(BUILD_DIR)/cache-layer-api.zip"

clean-build:
	rm -rf $(BUILD_DIR)

# =============================================================================
# Development
# =============================================================================

install:
	uv sync

test:
	uv run pytest -v --cov=src --cov-report=term-missing

lint:
	uv run ruff check src tests
	uv run mypy src

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

clean:
	rm -rf $(BUILD_DIR) __pycache__ .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

dev:
	uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8001

setup-hooks:
	uv tool install pre-commit
	cd .. && pre-commit install && pre-commit install --hook-type pre-push
