---
title: Database Schema
---

# Database Schema

Default location: `.deputy.db` in the project root. A custom path can be set via `.deputyconfig`.

SQLite database in WAL mode with a custom `REGEXP` function registered for search queries.

## Tables

| Table | Purpose |
|---|---|
| `entities` | Parsed symbols - id, language, full_path, name, type, metadata_json, parent_id |
| `branch_entities` | Branch-to-entity mapping for cross-branch entity sharing |
| `branch_files` | Per-branch file tracking - content hash and mtime for incremental sync |
| `cache_entries` | Resolved symbol cache per (module_fqn, symbol_name) |
| `cache_module_links` | Cross-module cache linkage for invalidation |
| `dependencies` | Indexed dependency packages - name, version, install_path, source |
| `config` | Key-value store (`base_path`, `deputy_cli_version`) |
| `class_bases` | Base class relationships - class_entity_id, base_full_path, base_entity_id, is_resolved |
| `inheritance_pins` | User-managed pins for disambiguating unresolved base classes |

## `entities`

The core table. Every parsed symbol - functions, classes, modules, imports, constants, inherited members - is stored here.

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | Entity identifier (typically the full path) |
| `language` | TEXT | Programming language (e.g. `python`) |
| `full_path` | TEXT | Dot-separated fully qualified name |
| `name` | TEXT | Short name |
| `type` | TEXT | Entity type |
| `metadata_json` | TEXT | JSON blob with type-specific fields |
| `parent_id` | TEXT | ID of the containing entity |

**Index:** `idx_entities_full_path` on `full_path`, `idx_entities_name` on `name`, `idx_entities_parent_id` on `parent_id`.

## `class_bases`

Stores base class relationships for inheritance tracking.

| Column | Type | Description |
|--------|------|-------------|
| `class_entity_id` | TEXT | Entity ID of the subclass |
| `base_full_path` | TEXT | Full path of the base class |
| `base_entity_id` | TEXT | Entity ID if resolved, NULL otherwise |
| `is_resolved` | INTEGER | 1 = resolved to a concrete entity, 0 = unresolved |
| `branch_info` | TEXT | JSON with branch-specific candidate info |
| `relation_kind` | TEXT | `inherits` for Python bases. For Java: `extends`, `implements` or `interface_extends` |

**Primary key:** `(class_entity_id, base_full_path)`.

Databases created before `relation_kind` existed gain the column automatically the first time they are opened.

## `inheritance_pins`

User-managed pins to resolve ambiguous base class references.

| Column | Type | Description |
|--------|------|-------------|
| `class_entity_id` | TEXT | Entity ID of the subclass |
| `base_name` | TEXT | Short name of the base class being pinned |
| `pinned_entity_id` | TEXT | Entity ID of the resolved target |
| `branch_name` | TEXT | Branch this pin applies to |

**Primary key:** `(class_entity_id, base_name, branch_name)`.

Pins are branch-scoped: pinning on `main` does not affect other branches.

## `branch_files`

Tracks per-branch file state for incremental sync.

| Column | Type | Description |
|--------|------|-------------|
| `branch_name` | TEXT | Git branch name |
| `filepath` | TEXT | Relative path for files under the default project directory, otherwise `kind:root_id::relative/path` |
| `content_hash` | TEXT | SHA-256 hash of file content |
| `last_modified` | REAL | File mtime |

## `branch_entities`

Maps entities to branches for cross-branch entity sharing.

| Column | Type | Description |
|--------|------|-------------|
| `branch_name` | TEXT | Git branch name |
| `entity_id` | TEXT | Entity ID from `entities` table |

## `dependencies`

One row per package indexed by `deputy sync --sync-deps`.

| Column | Type | Description |
|--------|------|-------------|
| `package_name` | TEXT PK | Distribution name |
| `version` | TEXT | Installed version |
| `install_path` | TEXT | Directory the package's modules were indexed from |
| `package_path` | TEXT | Currently the same as `install_path` |
| `source` | TEXT | Where the package came from: `venv` |
| `metadata_json` | TEXT | Currently `NULL` |
| `last_modified` | REAL | mtime of `install_path` when the package was indexed |

A package whose `version` and `last_modified` have not changed is skipped on the next sync. A package that is no longer installed has its entities and its row removed.

## `config`

| Column | Type | Description |
|--------|------|-------------|
| `key` | TEXT PK | Setting name |
| `value` | TEXT | Setting value |

`deputy init` writes two keys: `base_path`, the directory where `deputy init` ran, and `deputy_cli_version`, the version that created the database. This table is separate from `.deputyconfig`.

## `cache_entries`

Used only when `enable_cache=true`. Holds the cached result of resolving one symbol.

| Column | Type | Description |
|--------|------|-------------|
| `module_fqn` | TEXT | Module the symbol was resolved in |
| `symbol_name` | TEXT | Symbol that was resolved |
| `resolved_ids` | TEXT | JSON array of entity IDs it resolved to |
| `unresolved_ids` | TEXT | JSON array of entity IDs that could not be resolved |

**Primary key:** `(module_fqn, symbol_name)`.

## `cache_module_links`

Used only when `enable_cache=true`. Records that `module_fqn` depends on the cached result for (`cache_module_fqn`, `cache_symbol_name`), so entries can be invalidated when a module changes.

| Column | Type | Description |
|--------|------|-------------|
| `module_fqn` | TEXT | Module that depends on a cached result |
| `cache_module_fqn` | TEXT | Module of the cached result |
| `cache_symbol_name` | TEXT | Symbol of the cached result |

**Primary key:** `(module_fqn, cache_module_fqn, cache_symbol_name)`.
