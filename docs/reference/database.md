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
| `config` | Key-value store (base_path, CLI version, sync_deps) |
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

**Primary key:** `(class_entity_id, base_full_path)`.

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
| `filepath` | TEXT | Relative file path |
| `content_hash` | TEXT | SHA-256 hash of file content |
| `last_modified` | REAL | File mtime |

## `branch_entities`

Maps entities to branches for cross-branch entity sharing.

| Column | Type | Description |
|--------|------|-------------|
| `branch_name` | TEXT | Git branch name |
| `entity_id` | TEXT | Entity ID from `entities` table |
