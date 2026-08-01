---
title: init
---

# `deputy init`

Creates a SQLite database and initialises the project.

## Usage

```bash
deputy init [--path <db>]
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--path` / `-p` | `.deputy.db` | Database file path |

## What it does

1. Creates a SQLite database at the specified path
2. Records the current directory as the project root
3. Auto-detects the active virtual environment and writes `venv_path` to `.deputyconfig`

!!! note
    `.deputy.db` and `.deputyconfig` are gitignored - they are developer-local.
