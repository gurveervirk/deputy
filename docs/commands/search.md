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
| `--language` / `-l TEXT` | Filter by language (e.g. `python`) |
| `--limit INT` | Max results |
| `--offset INT` | Result offset |
| `--exact` / `-e` | Exact match on `full_path` (no regex) |
| `--name-only` / `-n` | Match name only, not `full_path` |

**Entity types:** `FUNCTION`, `CLASS`, `MODULE`, `PACKAGE`, `CONSTANT`, `TYPE_ALIAS`, `IMPORT_ALIAS`, `INHERITED_MEMBER`

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
deputy search "detect_venv"
# Entities
# └── src
#     └── deputy
#         └── venv
#             └── detect
#                 └── FUNCTION detect_venv src.deputy.venv.detect.detect_venv
```

The `--fqn` / `-f` flag appends the full path to each entry in tree mode.

The hierarchy is derived from dot-separated `full_path` values, not from entity types - so `--type` filters preserve the tree structure.
