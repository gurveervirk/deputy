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
deputy sync --sync-deps    # also index dependencies from .venv
deputy search "Model"      # find entities matching regex
deputy info deputy.core.create_context     # inspect an entity
deputy info --resolve deputy.utils.storage.FileMetadata  # resolve through imports
```

## Configuration

Deputy uses a key-value config file (`.deputyconfig`) in the project root:

```
db_path=/absolute/path/to/custom.db
venv_path=/path/to/venv
sync_deps=true
max_dep_files=5000
```

Settings are managed with `deputy config <key> <value>`.

## Commands

### `deputy init [--path <db>]`

Creates a SQLite database at the given path (default `.deputy.db`). Records the current directory as the project root. Auto-detects the active virtual environment and writes `venv_path` to `.deputyconfig`.

### `deputy sync [--force] [--sync-deps] [--no-sync-deps]`

Scans all `.py` / `.pyi` files under the project root, parses them with deproc, and persists all discovered entities to the database. Incremental by default: only re-parses files whose content hash changed. `--force` reprocesses everything.

`--sync-deps` also indexes dependency packages from the project's `.venv`. Each package's top-level modules are parsed and linked into the database as separate entities (tagged with `source: "dependency"`). Editable (path-based) dependencies are followed to their source directories automatically.

`--no-sync-deps` skips dependency indexing even if `sync_deps=true` in config.

The following directories and patterns are excluded from discovery and linking:

- `__pycache__/` (hardcoded)
- `*.egg-info`, `*.dist-info`
- `node_modules/`
- `.git/`, `.venv/`, `.mypy_cache/`, `.pytest_cache/`
- `build/`, `dist/`

### `deputy search <regex>`

Queries the database for entities whose `full_path` or `name` matches the regex pattern. Returns a table of Name, Type, Language, and Full Path.

### `deputy info <full-path> [--resolve] [--all]`

Looks up an entity by its exact `full_path`. Without flags, shows a single entity with its metadata JSON.

`--resolve` follows import aliases and re-exports to find the original definition. For example, if `deputy.utils.storage.FileMetadata` is a re-export from `deputy.utils.storage.models`, `--resolve` traces through the import chain and shows the actual class definition.

`--all` returns every entity matching that `full_path` (e.g., the same class name defined in multiple modules).

### `deputy config <key> [value]`

Read or write a config key. With a value, persists it to `.deputyconfig`. Without, prints the current value.

## Database

Default location is `.deputy.db` in the project root. A custom location is persisted in `.deputyconfig`.

### Schema

| Table | Purpose |
|---|---|
| `entities` | Parsed symbols — id, language, full_path, name, type, metadata_json |
| `branch_files` | Per-branch file tracking — content hash and mtime for incremental sync |
| `cache_entries` | Resolved symbol cache per (module_fqn, symbol_name) |
| `cache_module_links` | Cross-module cache linkage for invalidation |
| `dependencies` | Indexed dependency packages — name, version, install_path, source |
| `config` | Key-value store (base_path, CLI version, sync_deps) |

### Entity Metadata

Each entity stores a `metadata_json` blob with type-specific fields:

| Field | Applies To | Description |
|---|---|---|
| `lineno`, `end_lineno`, `col_offset`, `end_col_offset` | All entities | Source location |
| `fqn` | All entities | Fully qualified name |
| `path` | Modules, packages | Relative file path |
| `visibility` | Functions, methods, classes | `public` / `private` |
| `all_exports` | Modules | Contents of `__all__` |
| `exported` | All entities | Whether re-exported via `__all__` |
| `original_name` | Import aliases | Name before aliasing |
| `alias` | Import aliases | The alias (if any) |
| `import_type`, `wildcard` | Import statements | Import kind |
| `source` | Dependency entities | `"dependency"` (absent for project entities) |
| `package_name` | Dependency entities | Originating package name |

## Architecture

```
deputy CLI (typer)
  └─ tools/core.py
       ├─ init → open_database + init_schema + detect_venv
       ├─ sync → get_source_files → deproc parse/link → upsert_entities
       │   └─ --sync-deps → detect_venv → list_installed_packages → process_dependency
       ├─ search → SQLite REGEXP query on entities
       ├─ info → get_entity_by_path + optional SqlitePythonResolver.resolve()
       └─ config → read/write .deputyconfig
```

Dependencies: `deproc-core`, `deproc-python`, `typer`, `rich`.

## Development

```bash
git clone https://github.com/gurveervirk/deputy
cd deputy
uv sync
uv run pytest
```
