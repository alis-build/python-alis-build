# Alis Build packages for Python

Monorepo of Python packages for builders on the
[Alis Build](https://alisx.com/build) platform.

## Packages

| Package | PyPI | Import | Description |
| --- | --- | --- | --- |
| `packages/iam` | [`alis-build-iam`](https://pypi.org/project/alis-build-iam/) | `alis.build.iam` | Authentication and authorization. Dependency-free core; gRPC, authz and authn behind extras. |

Every package publishes independently to PyPI and installs with plain `pip`.
The shared `alis.build.*` import prefix is a [PEP 420][pep420] implicit
namespace, so packages can be installed in any combination.

[pep420]: https://peps.python.org/pep-0420/

## Development

The repo is a [uv workspace][uvws]. One virtualenv at the root, every package
installed editable into it.

[uvws]: https://docs.astral.sh/uv/concepts/projects/workspaces/

```bash
uv sync            # create .venv and install all packages + dev tools
make test          # pytest across the workspace
make lint          # ruff check + format check, taplo TOML format check
make typecheck     # mypy (strict)
make fmt           # autofix: ruff format/fix + taplo fmt
make check         # lint + typecheck + test
make build         # build sdist + wheel for every package
make check-pip     # build, then install each wheel with plain pip in a clean venv
make check-bare    # prove the core works with none of its extras installed
```

`make check-pip` is the guard on the "must work with normal pip" requirement —
it installs the built wheels into a bare `venv` using `pip` only, with uv
nowhere in the picture, and imports them.

`make check-bare` guards the other packaging promise: that a package's core
installs with no dependencies, and that importing a subpackage without its
extra names the extra to install rather than failing with a raw
`ModuleNotFoundError`. Both run in CI.

### Adding a package

```bash
make new NAME=lro
```

Scaffolds `packages/lro` with the `alis.build.lro` import path, then register it
in the root `pyproject.toml` (`[tool.uv.sources]` and the `dev` group) and in
the table above.

## Contributing

See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for the development workflow,
code style, commit-message convention, and how to add a package. Security
problems go through [SECURITY.md](SECURITY.md) rather than a public issue.

## Releasing

Each package versions independently. Bump `version` in the package's
`pyproject.toml` and its `__version__`, then push a tag shaped
`<package-dir>-v<version>`:

```bash
git tag iam-v0.1.0
git push origin iam-v0.1.0
```

The `publish` workflow rebuilds that package's changelog, then builds and
uploads the package to PyPI via [Trusted Publishing][tp] (OIDC — no API tokens
stored in the repo).

[tp]: https://docs.pypi.org/trusted-publishers/

Changelogs are generated from commit subjects, so they are only as good as the
messages. Follow [Conventional Commits][cc]; `feat`, `fix`, `perf`, `refactor`,
`docs` and `revert` appear in the changelog, and `chore`, `test`, `ci`, `build`
and `style` do not. Rebuild one by hand with `make changelog PKG=<name>`.

[cc]: https://www.conventionalcommits.org

## License

Apache-2.0
