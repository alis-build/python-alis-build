# alis-build-iam

Authentication and authorization for services on the
[Alis Build](https://alisx.com/build) platform.

One identity model that survives every hop: decoded from a token at the edge,
carried through the request, forwarded to the services you call, and used to
answer authorization questions when it gets there.

```bash
pip install alis-build-iam
```

The core has **no dependencies**. Everything that needs a third-party library
lives behind an extra:

| Install | Adds | Pulls in |
| --- | --- | --- |
| `alis-build-iam` | Identity, wire format, context, HTTP headers, ASGI | nothing |
| `alis-build-iam[grpc]` | gRPC metadata and interceptors | `grpcio` |
| `alis-build-iam[authz]` | Role and permission checks, policy fetching | `grpc-google-iam-v1` |
| `alis-build-iam[authn]` | OAuth2/OIDC client, token verification | `httpx`, `cryptography` |
| `alis-build-iam[all]` | everything | all of the above |

Importing a subpackage without its extra tells you exactly what to install.

> Status: early development. The public API is not yet stable.

## The identity

An `Identity` is a frozen value describing a user, a service account, or a
system actor.

```python
from alis.build import iam

identity = iam.Identity(
    type=iam.IdentityType.USER,
    id="1934872948",
    email="john@example.com",
    group_ids=["df913r888"],
)

identity.policy_member()  # 'user:1934872948'
identity.user()  # 'users/1934872948'
identity.is_privileged()  # False
```

System identities bypass authorization entirely, which is what makes trusted
internal calls work. Register the emails that should count as system, and the
humans who get the same bypass, once at startup:

```python
iam.add_system_email("alis-build@my-project.iam.gserviceaccount.com")
iam.add_admin_email("admin@example.com")
```

An admin keeps their identity type, so resource names and audit trails still
say "user". The environment's own service account is registered automatically
when `ALIS_OS_PROJECT` is set.

## Carrying it through a request

The identity is ambient, held in a context variable rather than passed down
through every call:

```python
with iam.identity_context(identity):
    ...
    iam.from_context()  # raises if there is none
    iam.from_context_or_none()  # None if there is none
```

Child asyncio tasks inherit it and cannot leak changes back to the parent.
Threads start empty, so use `contextvars.copy_context().run(fn)` if you need it
to cross one.

### Over HTTP

```python
from alis.build.iam.asgi import IdentityMiddleware

app = IdentityMiddleware(app)  # 401s anonymous requests
app = IdentityMiddleware(app, required=False, exclude_paths={"/healthz"})
```

Pure ASGI3, so it works under Starlette, FastAPI, Litestar, Quart and async
Django without importing any of them. For anything else, the primitives take a
plain header mapping:

```python
from alis.build.iam.headers import identity_from_headers, identity_headers

identity = identity_from_headers(request.headers)
response = httpx.get(url, headers=identity_headers(identity))
```

### Over gRPC

```python
from alis.build.iam.grpc import AsyncIdentityInterceptor, IdentityClientInterceptor

server = grpc.aio.server(interceptors=[AsyncIdentityInterceptor()])
channel = grpc.intercept_channel(channel, IdentityClientInterceptor())
```

The server interceptor binds the identity for the handler and refuses calls
without one as `UNAUTHENTICATED`. The client interceptor forwards whatever
identity is bound, so a service handling one request and calling another passes
the caller through without threading it by hand. Calls with no identity bound
are left alone, which keeps health checks working.

## Authorization

Register what each role grants once at startup, then close the registry:

```python
from alis.build.iam.authz import add_role_permissions, default_registry


def configure_authorization() -> None:
    add_role_permissions("roles/viewer", ["/example.v1.Books/Get"])
    add_role_permissions("roles/admin", ["/example.v1.Books/Delete"], inherits=["roles/viewer"])
    default_registry().freeze()
```

`freeze()` closes the registry to further changes and logs anything suspicious
about it, such as a permission an open role has made reachable by everyone.

Then, per request:

```python
from alis.build.iam.authz import Authorizer

authorizer = Authorizer(iam.from_context())
if not authorizer.has_permission("/example.v1.Books/Get", policy):
    raise PermissionDenied
```

Roles are seeded from the policy the identity carries, and any policies you
pass to a check apply to that check only. To reuse them across several checks,
`with_roles_from_policies(...)` returns a new authorizer rather than mutating
the one you have.

Two behaviours worth knowing before you rely on them:

- An **open role** is an allow-list of role *names*, not a grant to anyone in
  particular. A check for an open role succeeds for every caller, and every
  permission that role grants becomes reachable along with it.
- A **conditional policy binding** cannot be evaluated here, because the
  condition needs the resource being accessed and a check is never given it. By
  default such a binding grants and logs a warning naming the condition. Set
  `ConditionMode.DENY` once those warnings have stopped appearing.

### Fetching policies

An authorization decision often needs several resources' policies. Fetching
them one after another adds up their round trips:

```python
from alis.build.iam.policypool import PolicyPool

pool = PolicyPool()
pool.add("resources/a", client.get_iam_policy)
pool.add("resources/b", client.get_iam_policy)
collected = await pool.gather()

authorizer.has_role(["roles/admin"], *collected.policies)
```

A failed fetch does not discard the successful ones — collapsing a partial
failure to "no policies" turns a transient outage into a blanket refusal. Call
`collected.raise_for_errors()` when a partial answer will not do.
`SyncPolicyPool` does the same on threads.

## Authentication

```python
from alis.build.iam.authn import AuthnClient

client = AuthnClient.from_base_url("https://auth.example.com", client_id=..., client_secret=...)
```

Signing a user in — send them to `start.url` and set `start.cookie` on that
same response:

```python
start = client.start_login("https://app.example.com/callback", return_to="/dashboard")
```

On the callback route:

```python
result = await client.complete_login(request.query, request.cookies, redirect_uri)
client.validate_id_token_nonce(result.tokens.id_token, result.nonce)
```

Checking a token on each request:

```python
identity = await client.verified_identity(bearer_token)
```

`authenticate(tokens)` checks a stored token set and renews it only if the
access token has actually expired, returning whether it did so you know when to
persist. `SyncAuthnClient` offers the same surface for a blocking server.

Only RS256 signatures are verified. Published keys are cached with a TTL and a
minimum refresh interval, and concurrent misses trigger one fetch rather than
one per request.

## Testing

```python
from alis.build.iam.testing import fresh_authz_registry, fresh_system_config


@pytest.fixture(autouse=True)
def _iam():
    with fresh_system_config(), fresh_authz_registry():
        yield
```

The registries are process-wide, so without this a test that registers an admin
email privileges that address for every test after it. The authorization
registry defaults to stricter settings in tests than in production: an
unregistered permission raises rather than denying, and a conditional binding
raises rather than granting, so either shows up as a failing test.

`alis.build.iam.authz.testing` has `policy()`, `binding()` and
`encode_policy()` for writing readable authorization tests.

## License

Apache-2.0
