# Development

## Setting Up uv

This project is set up to use [uv](https://docs.astral.sh/uv/) to manage Python and
dependencies. First, be sure you
[have uv installed](https://docs.astral.sh/uv/getting-started/installation/).

Then
[fork the jlevy/strif repo](https://github.com/jlevy/strif/fork)
(having your own fork will make it easier to contribute) and
[clone it](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository).

## Basic Developer Workflows

The `Makefile` simply offers shortcuts to `uv` commands for developer convenience.
(For clarity, GitHub Actions don’t use the Makefile and just call `uv` directly.)

```shell
# First, install all dependencies and set up your virtual environment.
# This simply runs `uv sync --all-extras` to install all packages,
# including dev dependencies and optional dependencies.
make install

# Run uv sync, lint, and test:
make

# Build wheel:
make build

# Linting:
make lint

# Run tests:
make test

# Delete all the build artifacts:
make clean

# Upgrade dependencies to compatible versions:
make upgrade

# To run tests by hand:
uv run pytest   # all tests
uv run pytest -s src/module/some_file.py  # one test, showing outputs

# Build and install current dev executables, to let you use your dev copies
# as local tools:
uv tool install --editable .

# Dependency management directly with uv:
# Add a new dependency:
uv add package_name
# Add a development dependency:
uv add --dev package_name
# Update to latest compatible versions (including dependencies on git repos):
uv sync --upgrade
# Update a specific package:
uv lock --upgrade-package package_name
# Update dependencies on a package:
uv add package_name@latest

# Run a shell within the Python environment:
uv venv
source .venv/bin/activate
```

See [uv docs](https://docs.astral.sh/uv/) for details.

## IDE setup

If you use VSCode or a fork like Cursor or Windsurf, you can install the following
extensions:

- [Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python)

- [Based Pyright](https://marketplace.visualstudio.com/items?itemName=detachhead.basedpyright)
  for type checking. Note that this extension works with non-Microsoft VSCode forks like
  Cursor.

## Publishing Releases

See [publishing.md](publishing.md) for instructions on publishing to PyPI.

## Release Checks

`make release-check` runs the reentrant release state machine in
`devtools/release.py`. It is the single entry point for version resolution:
an explicit `--version` argument wins, otherwise the git tag on `HEAD` (with
any `v` prefix stripped) is used, otherwise a local version carrying the commit
short hash (e.g. `0.2.25+g1a2b3c4`).

The check verifies that `pyproject.toml`'s `project.version`,
`src/strif/__init__.py`'s `__version__`, and the `strif` version recorded in
`uv.lock` are all equal, that any git tag matches, and that the working tree
has no uncommitted changes. It then runs the lint, type-check, and
test/coverage gates, prints a summary table (including sha256 of the built
sdist/wheel), and exits non-zero if anything fails. The sdist/wheel are only
written to `dist/` once every blocking gate passes; a failed run leaves no
`dist/` behind, and re-running with the same inputs reuses existing artifacts
instead of rebuilding.

The script only reports problems; it never rewrites project files. Gate
strictness is configured in `pyproject.toml` under `[tool.release]` (all
default to blocking):

```toml
[tool.release]
fail_on_type_errors = true   # basedpyright errors block the release
fail_on_coverage = true      # low coverage blocks the release
coverage_min_percent = 80    # coverage threshold
```

## Documentation

- [uv docs](https://docs.astral.sh/uv/)

- [basedpyright docs](https://docs.basedpyright.com/latest/)

* * *

*This file was built with
[simple-modern-uv](https://github.com/jlevy/simple-modern-uv).*
