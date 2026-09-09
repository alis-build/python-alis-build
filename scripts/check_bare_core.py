"""Checks that a package's core works with no optional dependencies installed.

Two promises are on the line: `pip install <distribution>` pulls nothing, and
importing a subpackage without its extra says which extra to install rather
than failing with a bare ModuleNotFoundError from somewhere inside the package.

Usage:
    python scripts/check_bare_core.py <package> [interpreter]
"""

from __future__ import annotations

import subprocess
import sys

IAM = """
import alis.build.iam as iam
from alis.build.iam import MissingDependencyError, asgi, headers, testing

identity = iam.Identity(type=iam.IdentityType.USER, id="1", email="a@example.com")
assert iam.loads(iam.dumps(identity)) == identity
assert headers.identity_from_headers(headers.identity_headers(identity)) == identity
assert asgi.IdentityMiddleware is not None
assert testing.fresh_system_config is not None

EXTRAS = [
    ("alis.build.iam.grpc", "grpc"),
    ("alis.build.iam.authz", "authz"),
    ("alis.build.iam.policypool", "authz"),
    ("alis.build.iam.authn", "authn"),
]
"""

ALOG = """

import alis.build.alog as alog
from alis.build.alog import MissingDependencyError, asgi, testing

with testing.capture() as captured:
    with alog.bind(labels={"a": "b"}, request_id="r-1"):
        alog.info("hello")

entry = captured.entries[0]
assert entry["message"] == "hello", entry
assert entry["severity"] == "INFO", entry
assert entry["logging.googleapis.com/labels"] == {"a": "b"}, entry
assert entry["request_id"] == "r-1", entry
assert asgi.TraceContextMiddleware is not None

EXTRAS = [("alis.build.alog.grpc", "grpc")]
"""

# The part every package's check ends with: each subpackage behind an extra must
# refuse to import in a way that names the extra and the command that fixes it.
EPILOGUE = """
for module, extra in EXTRAS:
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

LRO = """
from dataclasses import dataclass

import alis.build.lro as lro
from alis.build.lro import Config, MissingDependencyError
from alis.build.lro.testing import InMemoryStore


@dataclass
class State:
    owner: str
    polls: int = 0


config = Config(
    neuron="launchpad-v1",
    project="p",
    spanner_project="sp",
    spanner_instance="si",
    spanner_database="sd",
    cloud_tasks_project="p",
    cloud_tasks_location="europe-west1",
    cloud_tasks_queue="launchpad-v1-operations",
    cloud_tasks_service_account="alis-build@p.iam.gserviceaccount.com",
    host="https://launchpad-v1-42.europe-west1.run.app",
    local=True,
)
assert lro.resolve_cloud_tasks_location("africa-south1") == "europe-west1"
assert lro.decode_state(lro.encode_state(State("users/1", 2)), State) == State("users/1", 2)

from google.protobuf.struct_pb2 import Struct

store = InMemoryStore()
client = lro.Client(config, store=store)
finished = []


def create_agent(op):
    op.complete(Struct())
    finished.append(op.name)


client.add_resumable_handler("create-agent", create_agent)
op = client.new_operation("operations/abc", Struct())
op.save_private_state(State(owner="users/1"))
client.resume_now("create-agent", "operations/abc")
assert finished == ["operations/abc"], finished
assert client.get_operation_pb("operations/abc").done

from alis.build.lro.asgi import ResumeMiddleware

assert ResumeMiddleware is not None

EXTRAS = [("alis.build.lro.grpc", "grpc")]
"""

CHECKS = {"alog": ALOG, "iam": IAM, "lro": LRO}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {sys.argv[0]} <{'|'.join(CHECKS)}> [interpreter]", file=sys.stderr)
        return 2

    package = sys.argv[1]
    python = sys.argv[2] if len(sys.argv) > 2 else sys.executable
    result = subprocess.run([python, "-c", CHECKS[package] + EPILOGUE], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
