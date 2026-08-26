.PHONY: sync lint fmt typecheck test check build clean check-pip new

sync:
	uv sync --all-packages

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run taplo fmt --check

fmt:
	uv run ruff format .
	uv run ruff check --fix .
	uv run taplo fmt

typecheck:
	@if [ -n "$$(ls -d packages/*/pyproject.toml 2>/dev/null)" ]; then \
		uv run mypy packages; \
	else echo "no packages yet - skipping mypy"; fi

test:
	@if [ -n "$$(ls -d packages/*/pyproject.toml 2>/dev/null)" ]; then \
		uv run pytest; \
	else echo "no packages yet - skipping pytest"; fi

check: lint typecheck test

build:
	rm -rf dist
	@if [ -n "$$(ls -d packages/*/pyproject.toml 2>/dev/null)" ]; then \
		uv build --all-packages --out-dir dist; \
	else echo "no packages yet - nothing to build"; fi

# Prove the wheels install and import under plain pip, no uv involved.
check-pip: build
	uv run python scripts/check_pip_install.py

new:
	@test -n "$(NAME)" || (echo "usage: make new NAME=<package>" && exit 1)
	uv run python scripts/new_package.py $(NAME)

clean:
	rm -rf dist .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
