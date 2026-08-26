# Contributing to Alis Build packages for Python

Thank you for helping improve these packages. Clear contribution guidelines save
time for both maintainers and contributors. GitHub surfaces this file on the
repository **Contributing** tab and when opening issues or pull requests.

## Code of conduct

Be respectful and constructive. Assume good intent, keep feedback specific, and
focus on the problem or the code.

## What to contribute

Useful contributions include bug reports with a minimal reproduction, failing
tests, documentation fixes, and focused pull requests that solve one problem at
a time.

## Before you start

- Read [README.md](../README.md) for the repository layout and the development
  workflow.
- Read the README of the package you are changing, under `packages/<name>/`.

## Development setup

### Requirements

- **Python 3.11 or newer.** The test matrix runs 3.11, 3.12 and 3.13.
- **[uv](https://docs.astral.sh/uv/)** — this repo is a uv workspace.

### Clone and install

```shell
git clone https://github.com/alis-build/python-alis-build.git
cd python-alis-build
uv sync --all-packages --all-extras
```

`--all-extras` matters: the optional subpackages are not installed without it,
and their tests will be skipped rather than run.

## Common commands

| Command | Purpose |
| ------- | ------- |
| `make check` | Lint, format check, type check, and the full test suite |
| `make fmt` | Autofix and format Python and TOML |
| `make test` | pytest across the workspace |
| `make typecheck` | mypy in strict mode |
| `make build` | Build an sdist and wheel for every package |
| `make check-pip` | Install the built wheels with plain pip and import them |
| `make check-bare` | Prove a package's core works with none of its extras |
| `make changelog PKG=<name>` | Rebuild one package's changelog |
| `make new NAME=<name>` | Scaffold a new package |

Run `make check` before opening a pull request.

## Code style

- **ruff** handles both linting and formatting; the configuration lives in the
  root `pyproject.toml` and applies to the whole repo. Run `make fmt` on files
  you touch, or configure your editor to format on save.
- **mypy runs in strict mode.** Annotate public APIs; internal helpers can rely
  on inference. Reach for `# type: ignore[code]` only with a specific rule and a
  comment saying why.
- **Target 3.11+ idioms**: `list[int]`, `X | None`, `match`, `StrEnum`,
  `asyncio.TaskGroup`. Prefer `collections.abc` types in parameter positions.
- **Google-style docstrings** with `Args:`, `Returns:` and `Raises:`.
- Comments should explain **why**, name a gotcha, and skip the obvious. Fewer
  and better beats more.

## Testing

- Tests live in `packages/<name>/tests/` and run under pytest with
  `asyncio_mode = "auto"`, so `async def` tests need no decorator.
- **Test behaviour, not implementation.** A test that pins a surprising-but-
  intended behaviour is worth writing precisely because someone will otherwise
  "fix" it.
- **No live network in the suite.** Use a mock transport, an in-process server,
  or a hand-rolled harness.
- Anything process-wide — a registry, a cache — needs a fixture that restores it,
  or one test silently changes the next one's result.

## Adding a package

```shell
make new NAME=lro
```

Then register it in the root `pyproject.toml` (`[tool.uv.sources]`, the `dev`
group, and `mypy_path`) and add a row to the table in the root README. The
scaffold prints the same reminder.

Keep a package's core dependency-free and put anything needing a third-party
library behind an extra, so installing it stays cheap for consumers who only use
part of it.

Before the new package can be released, it needs its own trusted publisher on
PyPI — see [The first release of a package](#the-first-release-of-a-package).

## Making a change

1. **Prefer small pull requests** — one logical change each.
2. **Add or extend tests** for behaviour you fix or introduce.
3. **Run `make check`** before opening the pull request.
4. **Update the package README** when the change affects how the package is
   used. Internal refactors do not need it.

## Commit messages

Commit subjects generate the changelog, so they are worth writing carefully.
Use [Conventional Commits](https://www.conventionalcommits.org):

```
feat(iam): concurrent policy fetching
fix(iam): keep an unrotated refresh token on renewal
docs(iam): package guide
chore: bump the ruff floor
```

- `feat`, `fix`, `perf`, `refactor`, `docs` and `revert` appear in the
  changelog. `chore`, `test`, `ci`, `build` and `style` do not.
- Scope with the package directory name when the change belongs to one package.
- Write the subject as the completed change, lowercase, no trailing period.
- Use the body to say **why**, and to record decisions someone will otherwise
  have to reverse-engineer.

## Releasing

Each package versions independently.

1. Bump `version` in `packages/<name>/pyproject.toml` and `__version__` in the
   package's `__init__.py`. They must match, and the release workflow refuses to
   build if the tag disagrees with them.
2. Merge to `main`.
3. Tag and push:

   ```shell
   git tag iam-v0.2.0
   git push origin iam-v0.2.0
   ```

The workflow rebuilds the changelog, then builds and uploads that one package to
PyPI through [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so
no API token is stored in the repository.

### The first release of a package

Trusted Publishing has to be told which workflow is allowed to publish which
project, and that is done once per package by someone with PyPI admin rights.
Until it exists, a tag push fails at the upload step.

Because the project does not exist on PyPI yet, register a **pending
publisher** at [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/).
Every field has to match exactly:

| Field | Value |
| ----- | ----- |
| PyPI Project Name | the distribution name, for example `alis-build-iam` |
| Owner | `alis-build` |
| Repository name | `python-alis-build` |
| Workflow name | `publish.yml` — the filename only, not a path |
| Environment name | `pypi` |

The pending publisher becomes an ordinary one on the project as soon as the
first release lands, so this is not needed again for that package.

A mismatch in any field surfaces as an OIDC or permissions error at upload
time rather than as "your configuration does not match", so it is worth
checking them character by character before tagging. Two other prerequisites:
the PyPI account needs two-factor authentication enabled, and the distribution
name has to be free.

Nothing needs to go in GitHub secrets. The repository already has the `pypi`
environment and the workflow already requests the `id-token: write` permission
that Trusted Publishing uses. The environment is also where to add a required
reviewer if you want a person to approve a release before it reaches PyPI;
without one, pushing the tag publishes immediately.

## Security

Please do not open a public issue for a security problem — see
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contributions are licensed under the same terms
as the project — see [LICENSE](../LICENSE).
