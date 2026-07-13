# deputy

Code intelligence CLI for Python. Parses source files with [deproc](https://github.com/gurveervirk/deproc), stores entities (classes, functions, imports, etc.) in a local SQLite database, and provides commands to search, inspect, and resolve symbols.

## Installation

```bash
pip install deputy
# or: uv tool install deputy
```

## Quick Start

```bash
deputy init                # create database
deputy sync                # scan & index project
deputy search "Model"      # find entities matching regex
deputy info deputy.core.create_context     # inspect an entity
deputy info --resolve deputy.utils.storage.FileMetadata  # resolve through imports
```

## Commands

### `deputy init [--path <db>]`

Creates a SQLite database at the given path (default `.deputy.db`). Records the current directory as the project root. If `--path` is provided, writes the absolute path to `.deputyconfig` so subsequent commands can find the database.

### `deputy sync [--force]`

Scans all `.py` / `.pyi` files under the project root, parses them with deproc, and persists all discovered entities to the database. Incremental by default: only re-parses files whose content hash changed. `--force` reprocesses everything.

The following directories are automatically excluded from discovery:

`.venv/`, `.git/`, `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `.hg/`, `.svn/`

### `deputy search <regex>`

Queries the database for entities whose `full_path` or `name` matches the regex pattern. Returns a table of Name, Type, Language, and Full Path.

### `deputy info <full-path> [--resolve] [--all]`

Looks up an entity by its exact `full_path`. Without flags, shows a single entity with its metadata JSON.

`--resolve` follows import aliases and re-exports to find the original definition. For example, if `deputy.utils.storage.FileMetadata` is a re-export from `deputy.utils.storage.models`, `--resolve` traces through the import chain and shows the actual class definition.

`--all` returns every entity matching that `full_path` (e.g., the same class name defined in multiple modules).

## Database

Default location is `.deputy.db` in the project root. A custom location is persisted in `.deputyconfig`.

### Schema

| Table | Purpose |
|---|---|
| `entities` | Parsed symbols — id, language, full_path, name, type, metadata_json |
| `branch_files` | Per-branch file tracking — content hash and mtime for incremental sync |
| `cache_entries` | Resolved symbol cache per (module_fqn, symbol_name) |
| `cache_module_links` | Cross-module cache linkage for invalidation |
| `config` | Key-value store (base_path, CLI version) |

## Architecture

```
deputy CLI (typer)
  └─ tools/core.py
       ├─ init → open_database + init_schema
       ├─ sync → get_source_files → deproc parse/link → upsert_entities
       ├─ search → SQLite REGEXP query on entities
       └─ info → get_entity_by_path + optional SqlitePythonResolver.resolve()
```

Dependencies: `deproc-core`, `deproc-python`, `typer`, `rich`.

## Development

```bash
uv sync
uv run pytest
```
