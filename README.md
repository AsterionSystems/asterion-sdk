# Asterion SDK

Asterion SDK is a modern Python monorepo for independently published space
systems packages. Packages share the PEP 420 `asterion` namespace, so consumers
can install only what they need while using consistent imports such as
`asterion.ccsds` and, in the future, `asterion.pus`.

The repository contains `asterion-ccsds`, a typed CCSDS Space Packet
implementation, and `asterion-mdb`, a protocol-neutral mission database and
telemetry decoder. Both packages have dependency-free runtimes.

## Repository layout

```text
.
├── packages/
│   └── asterion-ccsds/
│       ├── src/asterion/ccsds/
│       └── tests/
├── pyproject.toml          # Shared development tool configuration
└── uv.lock                 # Reproducible workspace dependencies
```

Each directory under `packages/` is an independently buildable distribution.
The root project provides the shared development environment and workspace
configuration.

## Development setup

[Install uv](https://docs.astral.sh/uv/getting-started/installation/), then
create the virtual environment and install all locked dependencies:

```console
uv venv
uv sync
```

`uv sync` creates `.venv` automatically if it does not already exist, so the
explicit `uv venv` step is optional.

Run the test suite:

```console
uv run pytest
```

Run tests with the required coverage threshold:

```console
uv run pytest --cov=asterion.ccsds --cov-report=term-missing
```

Format and lint the repository:

```console
uv run ruff format .
uv run ruff check .
```

Ruff's `E501` line-length lint is disabled because it can conflict with Ruff's
own formatter. The formatter still uses the configured 88-character target.

Run static type checking:

```console
uv run basedpyright
```

BasedPyright runs in strict mode. Its redundant-`isinstance` diagnostic is
disabled because public models deliberately validate runtime inputs from callers
that may not use static typing; missing third-party stub warnings are disabled
because packages should not be blocked by dependencies they do not control.

Build the package:

```console
uv build --package asterion-ccsds
```

Enable the pre-commit hooks after synchronizing dependencies:

```console
uv run pre-commit install
uv run pre-commit run --all-files
```

The hooks format with Ruff, apply safe Ruff lint fixes, and run BasedPyright.
