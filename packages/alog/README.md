# alis-build-alog

Structured logging for services on the [Alis Build](https://alisx.com/build) platform.

Cloud Logging's agents read a container's stdout and stderr and parse each line. A line that
happens to be a JSON object with the right keys becomes a structured entry — severity, a link
to its trace, labels, request metadata — and a line that is not becomes flat text with none of
that. This package makes sure the lines are the first kind, and prints something readable
instead when you are running locally.

```bash
pip install alis-build-alog
```

The core has **no dependencies**.

> Status: early development. The public API is not yet stable.

## Severity levels

Cloud Logging recognises nine severities; the standard library ships five. The three missing
ones are registered as real levels, so ordinary level comparisons and `setLevel` keep working:

| | | | | | | | | |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `NOTSET` | `DEBUG` | `INFO` | `NOTICE` | `WARNING` | `ERROR` | `CRITICAL` | `ALERT` | `EMERGENCY` |
| 0 | 10 | 20 | 25 | 30 | 40 | 50 | 60 | 70 |

Severity is derived from whatever number a record carries, so a library logging at plain
`INFO` is reported as `INFO` without knowing this package exists. A level between two of them
takes the severity below it, and anything under `DEBUG` becomes `DEFAULT`.

```python
from alis.build import alog

alog.severity_for(35)  # Severity.WARNING
alog.level_for("notice")  # 25
```

`level_for` raises `ConfigurationError` rather than falling back to a default, because a
service that silently logs at the wrong level looks healthy right up until the incident where
its debug lines were needed.

## License

Apache-2.0
