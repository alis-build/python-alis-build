# alis-build-lro

Long-running operations for services on the [Alis Build](https://alisx.com/build)
platform. Create an operation, persist it, resume it through a named handler,
and serve the standard `google.longrunning.Operations` API.

An operation usually outlives the request that created it. The callback that
resumes it may arrive at a different instance — one that never ran that request
— which is why handlers are registered at startup and why everything the handler
needs lives in the operation rather than in process memory.

```bash
pip install alis-build-lro
```

## Install matrix

| Install | Adds |
| --- | --- |
| `alis-build-lro` | Operations, handlers, private state, storage seam, local resume, ASGI route |
| `alis-build-lro[grpc]` | The `google.longrunning.Operations` servicer |
| `alis-build-lro[spanner]` | Persistence in the neuron's operations table |
| `alis-build-lro[tasks]` | Resuming through Cloud Tasks |

A deployed service wants all three: `pip install 'alis-build-lro[grpc,spanner,tasks]'`.

Unlike the other packages in this monorepo, the core is not dependency-free: it
carries `googleapis-common-protos` and `protobuf`, because
`google.longrunning.Operation` is a protobuf message and a core that could not
name it would be a strange thing to install. Both are pure Python.

## The shape of it

```python
from dataclasses import dataclass

from alis.build.lro import Client
from alis.build.lro.asgi import ResumeMiddleware
from alis.build.lro.grpc import register_grpc


@dataclass
class CreateAgentState:
    owner: str
    poll_count: int = 0


def create_agent(operation):
    state = operation.decode_private_state(CreateAgentState)
    if still_working(state):
        state.poll_count += 1
        operation.save_private_state(state)
        operation.resume_via_tasks("create-agent", 2)
        return
    operation.complete(result)


# At startup, once.
client = Client.from_env("launchpad-v1", store=store)
client.add_resumable_handlers({"create-agent": create_agent})
register_grpc(grpc_server, client)
app = ResumeMiddleware(app, client)
```

```python
# In the RPC that starts the work.
operation = client.new_operation(f"operations/{uuid4()}", metadata)
operation.save_private_state(CreateAgentState(owner="users/123"))
operation.resume_via_tasks("create-agent", 0)
return operation.pb
```

**Metadata is what callers see; private state is what the workflow needs.** Poll
counts, upstream resource names and serialized requests belong in private state.
A status message a client will render belongs in metadata.

## Local and deployed

`resume_via_tasks` either runs the handler in this process or schedules a Cloud
Task. `Config.local` decides:

| `local` | Behaviour |
| --- | --- |
| `None` (default) | Inferred per call from `K_SERVICE`: Cloud Run sets it, a laptop does not |
| `True` | Always resume in process |
| `False` | Always schedule a Cloud Task |

The default means a config you build by hand behaves the way a deployed one
does, without you having to think about it. Setting it explicitly takes the
environment out of the picture, which is what tests want.

## Backends

`Client(config)` connects to both: a Spanner store and a Cloud Tasks queue built
from the config. Pass your own to avoid that:

```python
client = Client(config, store=InMemoryStore())  # no cloud at all
client = Client(config, store=my_store, queue=my_queue)
```

Defaulting is all or nothing. Supplying any backend means you are saying which
ones you want, so a test that passes an in-memory store does not then try to
open a Cloud Tasks connection it has no credentials for.

## Infrastructure contract

**The table must exist before the client is built**, and its name is derived,
never configured:

```
${replace(project, "-", "_")}_${replace(neuron, "-", "_")}_Operations
```

For project `my-project` and neuron `launchpad-v1` that is
`my_project_launchpad_v1_Operations`. The commonest way to get this wrong is a
neuron name that differs from the deployed service name.

| Column | Type | Notes |
| --- | --- | --- |
| `key` | `STRING` | Primary key, computed and stored from `Operation.name` |
| `Operation` | `PROTO<google.longrunning.Operation>` | Required |
| `State` | `BYTES` | Nullable. The private state, JSON-encoded |
| `ResumePoint` | `STRING` | Nullable |
| `UpdateTime` | `TIMESTAMP` | Required. Carries the TTL policy |

Also required: a Cloud Tasks queue named `{neuron}-operations`, and a callback
host the queue can reach — the `Host` in the config, or the Cloud Run URL
`NewFromEnv` infers.

For a service following the ALIS module layout, keep these in
`infra/modules/alis.lro.v2/` and wire the module from `infra/main.tf`:

```hcl
locals {
  project = replace(var.alis_os_project, "-", "_")
  neuron  = replace(var.neuron, "-", "_")
}

resource "alis_google_spanner_table" "operations" {
  project         = var.alis_managed_spanner_project
  instance        = var.alis_managed_spanner_instance
  database        = var.alis_managed_spanner_db
  name            = "${local.project}_${local.neuron}_Operations"
  prevent_destroy = true

  schema = {
    columns = [
      {
        name            = "key"
        is_computed     = true
        computation_ddl = "Operation.name"
        is_stored       = true
        type            = "STRING"
        is_primary_key  = true
        required        = true
        unique          = true
      },
      {
        name          = "Operation"
        type          = "PROTO"
        proto_package = "google.longrunning.Operation"
        required      = true
      },
      { name = "State", type = "BYTES", required = false },
      { name = "ResumePoint", type = "STRING", required = false },
      { name = "UpdateTime", type = "TIMESTAMP", required = true },
    ]
  }
}

resource "alis_google_spanner_table_ttl_policy" "operations" {
  project  = alis_google_spanner_table.operations.project
  instance = alis_google_spanner_table.operations.instance
  database = alis_google_spanner_table.operations.database
  table    = alis_google_spanner_table.operations.name
  column   = "UpdateTime"
  ttl      = 90
}
```

The TTL is what eventually clears finished operations. An operation that never
completes is removed by it too, which is why a handler failure marks the
operation failed rather than leaving it to expire silently.

### Environment

`Client.from_env(neuron)` reads these:

| Variable | Becomes |
| --- | --- |
| `ALIS_OS_PROJECT` | `project` and `cloud_tasks_project` |
| `ALIS_REGION` | The Cloud Tasks location, after fallback resolution |
| `ALIS_PROJECT_NR` | Part of the inferred Cloud Run host |
| `ALIS_MANAGED_SPANNER_PROJECT` | `spanner_project` |
| `ALIS_MANAGED_SPANNER_INSTANCE` | `spanner_instance` |
| `ALIS_MANAGED_SPANNER_DB` | `spanner_database` |

Derived rather than read: the queue is `{neuron}-operations`, the service
account is `alis-build@{project}.iam.gserviceaccount.com`, and the host is
`https://{neuron}-{ALIS_PROJECT_NR}.{ALIS_REGION}.run.app`.

**Cloud Tasks is not available in every region Cloud Run is.** A neuron deployed
in `africa-south1` queues in `europe-west1`; its callback host stays on
`africa-south1`, because that is where it is reachable. An unsupported region
with no fallback is an error at construction, listing the regions that work.

## Async

`AsyncClient` and `AsyncOperation` mirror the sync pair method for method.
Reads that only touch memory stay synchronous; anything that writes is
awaitable.

```python
operation = await client.new_operation(name, metadata)
await operation.save_private_state(state)
await operation.resume_via_tasks("create-agent", 0)
```

There is no async database client for Spanner, so these await a worker thread.
The point is that an ASGI service does not stall its event loop on a blocking
read, not that the read stopped blocking.

## Testing

`alis.build.lro.testing.InMemoryStore` runs the whole workflow with no cloud, no
credentials and no emulator:

```python
from dataclasses import replace

from alis.build.lro.testing import InMemoryStore

client = Client(replace(config, local=True), store=InMemoryStore())
```

## Failure

A handler that raises leaves its operation **done, with the error recorded**,
and logs the traceback through the standard `alis.build.lro` logger. A caller
polling that operation gets a real failure rather than one that never completes.
A handler that already called `fail()` keeps its own message.

`fail()` defaults `google.rpc.Status.code` to `UNKNOWN`. A code of `0` is `OK`,
which would make a failed operation read as a successful one.

## License

Apache-2.0
