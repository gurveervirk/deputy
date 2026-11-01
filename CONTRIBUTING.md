# Contributing to deputy

deputy is a code intelligence CLI. It parses source with [deproc](https://github.com/gurveervirk/deproc), stores entities in a local SQLite database, and provides commands to search, inspect, and resolve symbols. The README and the published [documentation](https://gurveervirk.github.io/deputy/) describe current behaviour.

## Development environment

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required. CI runs the tests on Python 3.12 and 3.13.

deputy installs its deproc dependencies as editable path sources from `../deproc` (see `[tool.uv.sources]` in `pyproject.toml`), so clone both repositories side by side:

```bash
git clone https://github.com/gurveervirk/deproc
git clone https://github.com/gurveervirk/deputy
cd deputy
uv sync
```

To develop against a deproc branch, check that branch out in `../deproc` and run `uv sync`; the path sources follow the checkout. `.pre-commit-config.yaml` defines ruff, ruff-format, and basedpyright hooks for a pre-commit runner you install yourself.

`.deputyconfig`, `.deputy.db`, and `.deputy/` are developer-local state and are not committed.

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Reviewed and accepted work. Releases are tagged from here. Never commit or push to it directly. |
| `integration` | Advancing frontier that may contain work not yet approved. Used to test unapproved work together. May not exist yet. |
| `<type>/<slug>` | One task per branch, for example `feat/multi-root-analysis-scope`, `fix/java-hierarchy-persistence`, `docs/...`, `ci/...`, `refactor/...`, `test/...`, `chore/...`. |

### Choosing a base

- **Independent work:** branch from `main` and open the pull request against `main`.
- **Work that needs an unmerged branch:** branch from that prerequisite branch and open the pull request against it (a stacked pull request). Name the prerequisite under *Related Work* in the pull request. When the prerequisite merges, retarget the pull request to `main`.
- **Work that needs unapproved frontier work in `integration`:** branch from `integration`. That branch stays integration-only until its prerequisites are approved, then it is moved onto `main` before a pull request to `main` is opened.
- Stack only on `feat/*`, `fix/*`, or `integration` bases. `lint` and `pytests` run for pull requests against those bases and `main`.

### Promoting reviewed work

A reviewed task branch is merged to `main` through its own pull request. `integration` is then brought up to date by merging `main` into it. `integration` is never merged into `main` wholesale: it contains other branches' unreviewed commits, and merging it would land them without their own review.

Task branches may be merged into `integration` so unapproved work can be tested together. Do not rewrite the history of `main`, `integration`, or any pushed branch that others build on. Rebuilding `integration` from `main` requires the owner's approval.

Code Provenance runs only for pull requests against `main`. The `docs` workflow runs only on pushes to `main`.

## Commits

Use a [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) tag on a single-line message, with an optional scope:

```
fix(scope): preserve hierarchy relation kinds
feat: add resolution conformance coverage
```

Types in use: `feat`, `fix`, `docs`, `test`, `ci`, `refactor`, `build`, `chore`. Pull request titles use the same form, in lowercase and without a trailing period.

## Local validation

These are the commands CI runs (`.github/workflows/lint.yml`, `.github/workflows/pytest.yml`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
```

`basedpyright` checks `src` only; tests are excluded. Run one test with `uv run pytest -k <name>`. CI also collects coverage with `uv run pytest --cov --cov-report=xml`. Format with `uv run ruff format .`.

To check behaviour end to end, run the CLI against a scratch project (`uv run deputy init`, then `uv run deputy sync`) in a disposable directory, not in this repository.

## Testing expectations

- Fix a bug with a regression test that fails without the fix.
- Cover new behaviour and its meaningful edge cases.
- Use the in-memory database for database tests: `open_database(":memory:")`, with the `db` and `sample_entities` fixtures in `tests/conftest.py`.
- Do not weaken or skip a test to make a check pass.

## Pull requests

- Open pull requests with the repository template and keep one purpose per pull request.
- Report each check as passed, failed, skipped, or not run, with the command.
- Keep the description in step with the final diff.
- Call out CLI, configuration, database schema, and persisted-data impact.
- If the change depends on deproc, name the deproc pull request and the deproc ref used for testing.
- Do not put private planning information in pull request text.
- Wait for the required checks (`gh pr checks --watch --fail-fast`) before asking for review.

## Dependencies and compatibility

- The `deproc-*` entries in `pyproject.toml` are minimum versions that must exist on PyPI; the path sources only apply to local development and CI. The `clean-install` job in `publish.yml` installs the built wheel with `--no-sources` to prove this.
- Raise a `deproc-*` lower bound only when deputy needs the new behaviour, and do not change what deputy expects from deproc without saying so in the pull request.
- Dependency updates arrive through Dependabot (`uv`, weekly). Commit lockfile changes with `uv lock`.
- Persisted data (`.deputy.db`, `.deputyconfig`) outlives a release. Schema or config changes need a compatibility note.

## Releases and versioning

Set `version` in `pyproject.toml` and `__version__` in `src/deputy/_version.py` to the same value, then push a tag `v<version>`. `publish.yml` validates the tag, runs the tests and a clean-wheel install, and publishes. Follow [.github/RELEASE_CHECKLIST.md](.github/RELEASE_CHECKLIST.md).

## Working across deproc and deputy

deproc is the source-analysis layer and deputy is its consumer.

- Open the deproc pull request first, then the deputy pull request.
- Add the deproc pull request URL to the deputy pull request as a separate comment.
- CI clones deproc into `../deproc` at the ref named in `.github/deproc-ref` (default `main`). To test a deputy branch against a deproc branch, edit that file on the deputy branch, or run `lint` or `pytests` manually with the `deproc_ref` input, which overrides the file without a commit. The ref must exist on the deproc remote.
- `.github/deproc-ref` must be `main` again before the deputy pull request merges.
- Merge deproc first, and publish it to PyPI before releasing a deputy version that requires it.

## Issues and decisions

Use the issue templates for bugs, feature requests, and engineering tasks. Architecture decision records follow [.github/adr/0000-template.md](.github/adr/0000-template.md).
