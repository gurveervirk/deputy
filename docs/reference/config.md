---
title: Configuration
---

# Configuration

deputy uses a flat key-value config file (`.deputyconfig`) in the project root.

## All keys

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `.deputy.db` | Path to the SQLite database |
| `venv_path` | *(auto-detected)* | Path to the project virtual environment |
| `sync_deps` | `false` | Whether `deputy sync` should also index `.venv` dependencies |
| `max_dep_files` | `5000` | Maximum files to index from dependencies |
| `enable_cache` | `false` | Cache symbol resolution results |
| `auto_sync` | `false` | Auto-sync before `search` and `info` if source files changed |
| `log_level` | `WARNING` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `log_file` | `.deputy/deputy.log` | Path to the log file |
| `display_mode` | `table` | Search result display: `table` or `tree` |

## Managing config

```bash
# Read a value
deputy config log_level

# Write a value
deputy config log_level DEBUG
deputy config enable_cache true
deputy config auto_sync true
deputy config display_mode tree
deputy config sync_deps true
```

## File format

`.deputyconfig` is a plain `key=value` file, one entry per line:

```
db_path=.deputy.db
venv_path=.venv
sync_deps=true
log_level=WARNING
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `DEPUTY_LOG_LEVEL` | Overrides `log_level` config key |

## Gitignore

Both `.deputy.db` and `.deputyconfig` are developer-local and should be gitignored (they are by default via the `.deputy*` pattern).
