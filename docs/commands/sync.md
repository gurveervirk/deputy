---
title: sync
---

# `deputy sync`

Scans Python and Java source files, parses them with deproc, and indexes all discovered entities.

## Usage

```bash
deputy sync [--force] [--sync-deps] [--no-sync-deps]
```

## Options

| Flag | Description |
|------|-------------|
| `--force` / `-f` | Full re-sync - re-parses every file regardless of content hash |
| `--sync-deps` | Also index packages from the project's `.venv` |
| `--no-sync-deps` | Skip dependency indexing even if `sync_deps=true` in config |

## Behaviour

- **Incremental by default**: only files whose content hash changed since the last sync are re-parsed.
- **Configured roots**: besides the project directory, `deputy sync` indexes the directories in `source_roots`, `generated_roots`, `declaration_roots` and `dependency_roots`, and applies `analysis_languages`, `analysis_extensions` and `analysis_exclusions`. See [Analysis roots](../reference/config.md#analysis-roots).
- **`--sync-deps`** indexes each dependency package's top-level modules as separate entities tagged with `source: "dependency"`. Editable (path-based) dependencies are followed to their source directories.
- **Dependency config**: can also be set persistently via `deputy config sync_deps true`.
- **Which environment**: deputy uses `venv_path` from `.deputyconfig` if that directory contains `pyvenv.cfg`, then `$VIRTUAL_ENV`, then `<project>/.venv`.
- **Packages without `top_level.txt`**: only packages that ship a `top_level.txt` in their `.dist-info` directory get entities. Other installed packages are recorded in the `dependencies` table but contribute no entities.

## Excluded paths

The following are always excluded from discovery and linking:

- `__pycache__/`
- `*.egg-info`, `*.dist-info`
- `node_modules/`
- `.git/`, `.venv/`, `.mypy_cache/`, `.pytest_cache/`
- `build/`, `dist/`
