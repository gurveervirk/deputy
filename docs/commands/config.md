---
title: config
---

# `deputy config`

Read or write a configuration key in `.deputyconfig`.

## Usage

```bash
deputy config <key> [value]
```

With a value, persists the key-value pair. Without a value, prints the current setting.

## Available keys

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `.deputy.db` | Path to the SQLite database |
| `venv_path` | *(auto-detected)* | Path to the project virtual environment |
| `sync_deps` | `false` | Whether `deputy sync` should also index `.venv` dependencies |
| `max_dep_files` | `5000` | Maximum files to index from dependencies |
| `enable_cache` | `false` | Cache symbol resolution results (`deputy info --resolve`) |
| `auto_sync` | `false` | Auto-sync before `search` and `info` if source files changed |
| `log_level` | `WARNING` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `log_file` | `.deputy/deputy.log` | Path to the log file |
| `display_mode` | `table` | Search result display: `table` or `tree` |

## Examples

```bash
deputy config enable_cache true
deputy config display_mode tree
deputy config log_level DEBUG
deputy config auto_sync true
```

Log level also respects the `DEPUTY_LOG_LEVEL` environment variable.
