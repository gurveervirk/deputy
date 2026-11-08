# Release checklist

Pushing a tag named `v<version>` runs `.github/workflows/publish.yml`, which validates the version, runs the tests, a clean-wheel install check and a full Code Provenance scan, and publishes `deputy-cli` through PyPI trusted publishing in the `pypi-deputy` environment. Releases are cut from `main` by the owner.

## Before tagging

- [ ] Release PR is merged to `main`; the release is cut from the merged commit
- [ ] `version` in `pyproject.toml` and `__version__` in `src/deputy/_version.py` are bumped to the same value, and the bump follows the compatibility impact of the changes since the last tag
- [ ] `.github/deproc-ref` is `main`
- [ ] The `deproc-*` lower bounds in `pyproject.toml` match the deproc versions the code needs, and those versions are already published on PyPI
- [ ] `uv.lock` is regenerated with `uv lock` and committed
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright` and `uv run pytest` pass locally
- [ ] CI is green on the release commit (`lint`, `pytests`, and Code Provenance)
- [ ] Documentation under `docs/` and the README match the released behaviour
- [ ] Release notes are drafted: user-visible changes, config or database schema changes, and migration steps. The repository has no changelog file

## Downstream compatibility

- [ ] A wheel built with `uv build --wheel` installs with `uv pip install --no-sources` in a clean environment, as the `clean-install` job does, so deproc dependencies resolve from PyPI and not from `../deproc`
- [ ] Existing `.deputy.db` databases and `.deputyconfig` files still work, or the migration is documented

## Tag and publish

- [ ] Tag the merged `main` commit as `v<version>`; the tag must equal `pyproject.toml` `version`, and `_version.py` must match, or `validate-release` fails
- [ ] Push the tag; `publish.yml` runs `validate-release`, then `test`, `clean-install` and `provenance`, then `publish`; if the scanner is rate-limited, rerun the failed job once the limit lifts
- [ ] Confirm the new version is visible on PyPI and installs in a clean environment
- [ ] Publish the GitHub release for the tag with the release notes
- [ ] The `docs` workflow deploys the site when `docs/**` or `mkdocs.yml` change on `main`; confirm it succeeded if docs changed

## After release

- [ ] If an `integration` branch exists, bring it up to date with `main`
