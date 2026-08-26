"""Install every built wheel with plain pip in a throwaway venv and import it.

The workspace is developed with uv, but published packages must install for
anyone using stock pip. This runs that path end to end: `python -m venv`,
`pip install <wheel>`, `import <module>` — uv is never invoked.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def import_name(package_dir: Path) -> str:
    """Derive the import path from the src layout, e.g. alis.build.iam."""
    src = package_dir / "src"
    module = next(p for p in src.rglob("__init__.py"))
    return ".".join(module.parent.relative_to(src).parts)


def main() -> int:
    packages = [
        d for d in sorted((ROOT / "packages").glob("*")) if (d / "pyproject.toml").is_file()
    ]
    if not packages:
        print("no packages in the workspace yet — nothing to check")
        return 0

    wheels = sorted(DIST.glob("*.whl"))
    if not wheels:
        print("no wheels in dist/ — run `make build` first", file=sys.stderr)
        return 1

    failures: list[str] = []
    for package_dir in packages:
        pyproject = package_dir / "pyproject.toml"
        name = tomllib.loads(pyproject.read_text())["project"]["name"]
        wheel_prefix = name.replace("-", "_") + "-"
        matches = [w for w in wheels if w.name.startswith(wheel_prefix)]
        if not matches:
            # A single-package release build only puts one wheel in dist/.
            print(f"skip {name} (no wheel in dist/)")
            continue

        module = import_name(package_dir)
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            subprocess.run([sys.executable, "-m", "venv", venv], check=True)
            python = venv / "bin" / "python"
            if not python.exists():  # Windows
                python = venv / "Scripts" / "python.exe"
            try:
                subprocess.run(
                    [python, "-m", "pip", "install", "--quiet", *map(str, matches)],
                    check=True,
                )
                subprocess.run(
                    [python, "-c", f"import {module}; print({module}.__version__)"],
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                failures.append(f"{name}: {exc}")
                continue
        print(f"ok  {name} -> import {module}")

    if failures:
        print("\npip compatibility failures:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
