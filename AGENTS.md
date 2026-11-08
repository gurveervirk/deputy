# deputy agent instructions

deputy is a Python/Java code intelligence CLI built on deproc (`../deproc`), the source-analysis layer. Humans follow `CONTRIBUTING.md`; this file adds the rules agents must follow. Owner-specific rules live in `AGENTS.local.md` when present (not tracked).

## Style and sequencing
- Do not add code comments unless the owner asks for them.
- Work linearly: inspect, implement, validate, in that order, to keep context and traceability.
- Use `ruff format` (two blank lines between top-level functions and classes). Follow existing naming and typing.

## Git and branch safety
- Never commit or push to `main`. Work on a dedicated `<type>/<slug>` branch (`feat/`, `fix/`, `docs/`, `ci/`, `refactor/`, `test/`, `chore/`).
- Identify the intended base branch before starting; see "Choosing a base" in `CONTRIBUTING.md`.
- Do not merge, close, or delete branches or pull requests without explicit authorization.
- Never discard unrelated working-tree changes. Do not rewrite shared branch history.
- Commit messages are a single line starting with a Conventional Commits tag (`feat:`, `fix(scope):`). No body.
- Do not commit `.gitignore` changes unless asked.
- Request the owner's approval before pushing. The owner reviews and merges pull requests.

## Engineering discipline
- Read the existing implementation and the `deputy` skill before changing anything.
- Keep changes minimal and cohesive; no unrelated refactoring.
- Add a regression test for each bug fix, and tests for new behaviour and meaningful edge cases.
- Update `docs/` and the README when CLI behaviour, configuration, or the database change.
- The CLI, `.deputyconfig`, and `.deputy.db` are used by people and agents. Check compatibility before changing them.

## Validation
- Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright` (src only), and `uv run pytest`.
- Report exactly what ran and the result. State skipped or unavailable checks. Never claim a check passed that was not run.
- Do not suppress failures to make CI green.

## Pull requests
- Use `.github/pull_request_template.md`; keep the description in step with the final diff.
- Title format: `<type>[scope]: <imperative description>`, lowercase type, no trailing period.
- State compatibility and dependency impact, prerequisite PRs, and branch dependencies. Give a specific review focus.
- Open the pull request as a draft (`gh pr create --draft`). Code Provenance skips drafts. When the work is final and the other checks are green, mark it ready with `gh pr ready` to start the scan.
- After opening, wait for checks with `gh pr checks --watch --fail-fast`; hand off only when green, or report the failing gate. If Code Provenance fails because the scanner is rate-limited, report the retry time instead of waiting; it is not a required check, but it is required before a release.
- Keep titles, descriptions, and comments free of private planning information.

## Cross-repository work
- deproc is the source-analysis layer; deputy consumes it. Make the deproc API dependency explicit in every deputy change that needs it.
- Name the deproc ref used for integration testing. CI reads it from `.github/deproc-ref` (default `main`); the `lint` and `pytests` `workflow_dispatch` input `deproc_ref` overrides the file.
- A branch that edits `.github/deproc-ref` must restore `main` before merging. Do not silently change the deproc contract or the `deproc-*` lower bounds in `pyproject.toml`.
- Open the deproc PR first, then the deputy PR, then comment the deproc PR URL on the deputy PR. Validate both sides when relevant.

## Project facts
- Python 3.12+, `uv`, `hatchling`, `typer`, `rich`. Entry point `deputy = "deputy:main"` (`src/deputy/__init__.py`).
- The deproc packages are editable path sources from `../deproc` (`[tool.uv.sources]`). To test another deproc branch locally, check it out in `../deproc` and `uv sync`.
- `src/deputy/core.py` creates the deproc `Context`; `src/deputy/database/sqlite/` is the SQLite layer (WAL, custom `REGEXP`, `schema.sql`); `src/deputy/tools/` holds init, sync, search, info, resolve; `src/deputy/utils/` and `src/deputy/venv/` hold support code.
- Entity lookup is branch-aware: `branch_entities` and `branch_files` track entities per git branch.
- `.deputyconfig` is flat `key=value`, not JSON or TOML. It, `.deputy.db`, and `.deputy/` are developer-local and gitignored.
- Tests use `open_database(":memory:")` with the `db` and `sample_entities` fixtures in `tests/conftest.py`. `pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`.

## Skills
- `.agents/skills/deputy`: using the deputy CLI to search, inspect, and resolve symbols.

## Branch model and workflow standards (set up 2026-11-01)
- `main` is reviewed work; `integration` is the unapproved agent frontier and is never merged to `main` wholesale; task branches are `<type>/<slug>`.
- `lint` and `pytests` also run on `integration` and on PRs against `integration`, `feat/**`, `fix/**`. Code Provenance and `docs` remain `main`-only. Code Provenance skips draft pull requests and also runs, blocking, before `publish` on release tags. The `deproc_ref` mechanism is unchanged.
- Contribution files: `CONTRIBUTING.md`, `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/`, `.github/RELEASE_CHECKLIST.md`, `.github/adr/0000-template.md`.
- `CLAUDE.md` imports `AGENTS.md` and `AGENTS.local.md`.
- The same set exists in deproc; keep shared conventions identical in both.
