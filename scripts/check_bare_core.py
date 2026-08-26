"""Checks that the core works with no optional dependencies installed.

Two promises are on the line: `pip install alis-build-iam` pulls nothing, and
importing a subpackage without its extra says which extra to install rather
than failing with a bare ModuleNotFoundError from somewhere inside the package.
"""

from __future__ import annotations

import subprocess
import sys

CHECK = """
import alis.build.iam as iam
from alis.build.iam import MissingDependencyError, asgi, headers, testing

identity = iam.Identity(type=iam.IdentityType.USER, id="1", email="a@example.com")
assert iam.loads(iam.dumps(identity)) == identity
assert headers.identity_from_headers(headers.identity_headers(identity)) == identity
assert asgi.IdentityMiddleware is not None
assert testing.fresh_system_config is not None

for module, extra in [
    ("alis.build.iam.grpc", "grpc"),
    ("alis.build.iam.authz", "authz"),
    ("alis.build.iam.policypool", "authz"),
    ("alis.build.iam.authn", "authn"),
]:
    try:
        __import__(module)
    except MissingDependencyError as exc:
        message = str(exc)
        assert extra in message, f"{module}: {message} does not name the extra"
        assert "pip install" in message, f"{module}: {message} has no install command"
        assert isinstance(exc, ImportError), f"{module}: not an ImportError"
    else:
        raise AssertionError(f"{module} imported without its {extra!r} extra")

print("bare core ok")
"""


def main() -> int:
    python = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    result = subprocess.run([python, "-c", CHECK], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
