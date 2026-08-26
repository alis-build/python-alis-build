"""Scaffold a new package under packages/<name> with the alis.build namespace."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = """\
[project]
name = "alis-build-{name}"
version = "0.1.0"
description = "TODO"
readme = "README.md"
license = "Apache-2.0"
license-files = ["LICENSE"]
requires-python = ">=3.11"
authors = [
    {{ name = "Alis Build", email = "admin@alis.build" }},
    {{ name = "Daniel van Niekerk", email = "daniel.van.niekerk@alisx.com" }},
    {{ name = "Newton Nthiga", email = "newton@alisx.com" }},
    {{ name = "Jan Krynauw", email = "jan@alisx.com" }},
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Typing :: Typed",
]
dependencies = []

[project.urls]
Homepage = "https://alisx.com/build"
Repository = "https://github.com/alis-build/python-alis-build"
Issues = "https://github.com/alis-build/python-alis-build/issues"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/alis"]

[tool.hatch.build.targets.sdist]
include = ["src", "tests", "README.md", "LICENSE"]
"""

INIT = '''\
"""TODO: describe alis.build.{name}."""

__version__ = "0.1.0"

__all__ = ["__version__"]
'''

TEST = """\
from alis.build import {name}


def test_version_is_exposed() -> None:
    assert {name}.__version__
"""

README = """\
# alis-build-{name}

TODO

```bash
pip install alis-build-{name}
```

```python
from alis.build import {name}
```

## License

Apache-2.0
"""


def main(name: str) -> int:
    if not name.isidentifier() or name != name.lower():
        print(f"invalid package name: {name!r} (lowercase identifier)", file=sys.stderr)
        return 1

    package = ROOT / "packages" / name
    if package.exists():
        print(f"{package} already exists", file=sys.stderr)
        return 1

    module = package / "src" / "alis" / "build" / name
    module.mkdir(parents=True)
    (package / "tests").mkdir()

    (package / "pyproject.toml").write_text(PYPROJECT.format(name=name))
    (package / "README.md").write_text(README.format(name=name))
    (package / "LICENSE").write_text((ROOT / "LICENSE").read_text())
    (module / "__init__.py").write_text(INIT.format(name=name))
    (module / "py.typed").touch()
    (package / "tests" / f"test_{name}.py").write_text(TEST.format(name=name))

    print(f"created packages/{name}")
    print("next: add it to the root pyproject.toml [tool.uv.sources] + dev group,")
    print("      extend [tool.mypy] mypy_path, and list it in README.md")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: new_package.py <name>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
