---
title: search
---

# `deputy search`

Queries the database for entities matching a regex pattern.

## Usage

```bash
deputy search [options] <regex>
```

## Filter options

| Flag | Description |
|------|-------------|
| `--type` / `-t TEXT` | Filter by entity type. Repeatable. |
| `--language` / `-l TEXT` | Filter by language: `python` or `java` |
| `--limit INT` | Max results |
| `--offset INT` | Result offset |
| `--exact` / `-e` | Exact match on `full_path` (no regex) |
| `--name-only` / `-n` | Match name only, not `full_path` |

## Entity types

| Language | Types |
|----------|-------|
| Python | `PYTHON_MODULE`, `PACKAGE`, `NAMESPACE_PACKAGE`, `CLASS`, `FUNCTION`, `METHOD`, `CONSTANT`, `VARIABLE`, `IMPORT_ALIAS`, `INHERITED_MEMBER` |
| Java | `COMPILATION_UNIT`, `PACKAGE`, `PACKAGE_INFO`, `JAVA_MODULE`, `CLASS`, `INTERFACE`, `ENUM`, `ENUM_CONSTANT`, `RECORD`, `RECORD_COMPONENT`, `ANNOTATION_TYPE`, `CONSTRUCTOR`, `METHOD`, `FIELD` |

`IMPORT_STATEMENT`, `IMPORT`, `CONTROL_FLOW_BLOCK` and `CONTROL_FLOW_GROUP` entities are stored in the database but never appear in search results.

```bash
deputy search "Controller" --language java --type CLASS
```

## Display modes

Controlled by the `display_mode` config key in `.deputyconfig`:

### Table (default)

```bash
deputy search "Model"
# Name   Type   Language   Full Path
# Model  CLASS  python     myapp.models.Model
```

### Tree

```bash
deputy config display_mode tree
deputy search "search_entities"
# Entities
# └── src
#     └── deputy
#         └── tools
#             └── core
#                 └── FUNCTION search_entities deputy.tools.core.search_entities
```

The `--fqn` / `-f` flag appends the full path to each entry in tree mode.

The hierarchy is derived from dot-separated `full_path` values, not from entity types - so `--type` filters preserve the tree structure.
