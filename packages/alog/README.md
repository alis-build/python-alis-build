# alis-build-alog

Structured logging for services on the [Alis Build](https://alisx.com/build) platform.

Cloud Logging's agents read a container's stdout and stderr and parse each line. A line that
happens to be a JSON object with the right keys becomes a structured entry — severity, a link
to its trace, labels, request metadata — and a line that is not becomes flat text with none of
that. This package makes sure the lines are the first kind, and prints something readable
instead when you are running locally.

Nothing here calls the Logging API. The package only decides what a line looks like; the
platform does the ingesting.

```bash
pip install alis-build-alog
```

The core has **no dependencies**.

| Install | Adds | Pulls in |
| --- | --- | --- |
| `alis-build-alog` | Formatters, levels, binding, ASGI middleware, test helpers | nothing |
| `alis-build-alog[grpc]` | gRPC server interceptors | `grpcio` |

Importing a subpackage without its extra tells you exactly what to install.

> Status: early development. The public API is not yet stable.

## Getting going

Call `configure()` once at startup and everything that logs through the standard library comes
out in the right format — your own loggers and every library you depend on alike.

```python
import logging
from alis.build import alog

alog.configure()

logging.getLogger(__name__).info("server started")
alog.info("or use the shortcut, which does the same thing")
```

There are shortcuts for each severity: `debug`, `info`, `notice`, `warning`, `error`,
`critical`, `alert`, `emergency`, plus `exception` and `fatal`. They work without `configure()`
too, so a script does not need any setup. Pass arguments rather than building an f-string —
for a line the level discards, the interpolation never has to happen:

```python
alog.info("processed %s in %dms", key, elapsed)
```

Importing the package changes no logging state. A library that installs handlers on import
fights `dictConfig`, a server's own setup and a test runner's capture, and makes import order
matter, so `configure()` is the explicit opt-in.

## Cloud and local output

`configure()` picks the format from the environment. `K_SERVICE`, `CLOUD_RUN_JOB` or
`KUBERNETES_SERVICE_HOST` means a runtime is collecting and parsing stdout, so it writes JSON;
anything else is taken to be a terminal, so it writes coloured text. `ALOG_MODE=cloud|local`
overrides it.

```json
{"message":"server started","severity":"NOTICE","logging.googleapis.com/trace":"projects/p/traces/abc","time":"2025-08-26T12:00:00.123456Z","logging.googleapis.com/spanId":"456"}
```

The local layout is **not** a contract — it exists to be read by a person and is free to change
whenever a change makes it easier to read. Do not parse it; parse the JSON. Colour turns off
when the stream is not a terminal, when `NO_COLOR` is set to anything, or when `TERM=dumb`;
`ALOG_COLOR=always|never` forces it either way.

## Severity levels

Cloud Logging recognises nine severities; the standard library ships five. The three missing
ones are registered as real levels, so ordinary comparisons and `setLevel` keep working:

| `NOTSET` | `DEBUG` | `INFO` | `NOTICE` | `WARNING` | `ERROR` | `CRITICAL` | `ALERT` | `EMERGENCY` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 10 | 20 | 25 | 30 | 40 | 50 | 60 | 70 |

Severity is derived from whatever number a record carries, so a library logging at plain `INFO`
is reported as `INFO` without knowing this package exists. A level between two of them takes
the severity below it, and anything under `DEBUG` becomes `DEFAULT`.

The minimum level defaults to `DEBUG` locally and `INFO` in the cloud — locally the detail is
the point, in production the volume is the problem. `ALOG_LEVEL` overrides it with a level name
in any casing or a number, and raises on anything else rather than falling back: a service that
silently logs at the wrong level looks healthy right up until the incident where its debug
lines were needed.

## Binding metadata to a request

Everything a log line carries beyond its message is ambient, bound to the context rather than
passed down through every call. A logging call deep in a handler still lands with the request's
trace on it.

```python
with alog.bind(labels={"route": "/v1/users"}, request_id="r-7"):
    alog.info("handling")  # carries both
```

Nesting merges the mapping-shaped arguments — `labels` and arbitrary fields — and replaces the
rest. On a key collision the inner value wins. Anything that is not a name the format defines
becomes a field on the line itself; `extra=` on an ordinary logging call does the same.

`bind` also takes `operation`, `insert_id` and `http_request`, which fill in the corresponding
parts of the structured entry.

Two behaviours worth knowing before you rely on this: a task created with `create_task`
inherits the binding and cannot leak its own changes back to its parent, but a plain thread
starts with nothing bound. Where a framework hands you separate enter and exit callbacks rather
than a block, drive `bind` with `contextlib.ExitStack`.

## Trace correlation

Correlating logs with their trace is what lets Logs Explorer show a request's lines against its
trace. The header comes in on the request; bind it at the transport boundary and every line
written while handling that request picks it up.

### Over HTTP

```python
from alis.build.alog.asgi import TraceContextMiddleware

app = TraceContextMiddleware(app)
```

Pure ASGI3 with no framework import, so it works under Starlette, FastAPI, Litestar, Quart and
async Django alike.

### Over gRPC

```python
from alis.build.alog.grpc import AsyncTraceContextInterceptor

server = grpc.aio.server(interceptors=[AsyncTraceContextInterceptor()])
```

`TraceContextInterceptor` is the synchronous equivalent. Both keep the binding open across a
streaming response, not merely while the generator is created.

The trace resource name needs a project, taken from the first non-empty of `ALIS_OS_PROJECT`,
`GOOGLE_CLOUD_PROJECT` or `GCLOUD_PROJECT`. Without one there is nothing to correlate against,
so the trace keys are left off rather than written half-formed.

## Errors

```python
try:
    ...
except ValueError:
    alog.exception("handler failed")
```

The traceback lands on `stack_trace`, which is the key Error Reporting groups by — an exception
logged any other way is one that never gets counted.

`alog.fatal(...)` writes a critical line and then raises `SystemExit(1)` rather than exiting
outright, so `finally` blocks, context managers and `atexit` handlers still run.

## Testing

```python
from alis.build.alog.testing import capture, fresh_logging_config


def test_it_logs_the_route():
    with capture() as captured:
        handle_request()

    assert captured.entries[0]["logging.googleapis.com/labels"] == {"route": "/v1"}
```

`capture()` collects the formatted lines instead of printing them, so a test can assert on the
line rather than only on the message. `fresh_logging_config()` puts process-wide logging
configuration back the way it was; use it as an autouse fixture, since a test that reconfigures
logging without restoring it changes the result of every test that runs afterwards.

## License

Apache-2.0
