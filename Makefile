.PHONY: sync lint fmt typecheck test check build clean check-pip check-bare changelog new

sync:
	uv sync --all-packages --all-extras

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

# Prove each package's core works with none of its extras installed.
check-bare:
	@for pkg in $$(ls packages); do \
		rm -rf dist .bare; \
		uv build --package alis-build-$$pkg --out-dir dist || exit 1; \
		python3 -m venv .bare; \
		.bare/bin/pip install --quiet dist/*.whl || exit 1; \
		python3 scripts/check_bare_core.py $$pkg .bare/bin/python || exit 1; \
		rm -rf .bare; \
	done

# Rebuild one package's changelog from the commits that touched it.
changelog:
	@test -n "$(PKG)" || (echo "usage: make changelog PKG=<package>" && exit 1)
	uv run git-cliff --config cliff.toml \
		--include-path "packages/$(PKG)/**" \
		--tag-pattern "$(PKG)/v[0-9].*" \
		--output packages/$(PKG)/CHANGELOG.md

new:
	@test -n "$(NAME)" || (echo "usage: make new NAME=<package>" && exit 1)
	uv run python scripts/new_package.py $(NAME)

clean:
	rm -rf dist .bare .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
