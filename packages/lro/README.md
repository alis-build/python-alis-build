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

Unlike the other packages in this monorepo, the core is not dependency-free: it
carries `googleapis-common-protos` and `protobuf`, because
`google.longrunning.Operation` is a protobuf message and a core that could not
name it would be a strange thing to install. Both are pure Python.

Persistence and Cloud Tasks scheduling arrive in a following release. Until
then a client takes an explicit store and resumes in process.

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
