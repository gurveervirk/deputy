---
title: Configuration
---

# Configuration

deputy uses a flat key-value config file (`.deputyconfig`) in the project root.

## All keys

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | `.deputy.db` | Path to the SQLite database |
| `venv_path` | *(auto-detected)* | Path to the project virtual environment. It must contain `pyvenv.cfg`; otherwise deputy uses `$VIRTUAL_ENV`, then `<project>/.venv` |
| `sync_deps` | `false` | Whether `deputy sync` should also index `.venv` dependencies |
| `max_dep_files` | `5000` | Maximum files to index from dependencies |
| `enable_cache` | `false` | Cache symbol resolution results |
| `auto_sync` | `false` | Auto-sync before `search` and `info` if source files changed |
| `log_level` | `WARNING` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `log_file` | `.deputy/deputy.log` | Path to the log file |
| `display_mode` | `table` | Search result display: `table` or `tree` |
| `project_roots` | *(the project directory)* | Directories indexed as the project. Replaces the default when set |
| `source_roots` | *(none)* | Extra source directories |
| `generated_roots` | *(none)* | Generated-code directories |
| `declaration_roots` | *(none)* | Declaration and stub directories, for example `.pyi` files |
| `dependency_roots` | *(none)* | Dependency source directories indexed during `deputy sync` |
| `analysis_languages` | *(all supported)* | Comma-separated languages to index, for example `python,java` |
| `analysis_extensions` | *(all supported)* | Comma-separated file extensions to index, for example `.py,.java` |
| `analysis_exclusions` | *(none)* | Comma-separated name patterns to skip, in addition to the built-in excluded paths |

## Analysis roots

By default deputy indexes the directory where `deputy init` ran. Extra roots let one database cover generated code, stubs, or source that lives outside that directory.

```
source_roots=../shared/src:vendor/tools
generated_roots=build/generated
declaration_roots=typings
dependency_roots=../libs/acme
```

- The five `*_roots` keys are lists separated by the operating system's path separator: `:` on macOS and Linux, `;` on Windows. Spaces around each entry are ignored.
- Relative paths resolve against the directory where `deputy init` ran. Absolute paths are used as given.
- Setting `project_roots` replaces the default project directory. Only the listed directories are indexed as project roots, and the directory where `deputy init` ran is not indexed unless you list it.

### How files are identified

- A file under the default project directory is tracked by its relative path, for example `top.py`.
- A file in any other root is tracked as `kind:root_id::relative/path`, for example `source:source-5c6b531272969254::v.py`. The root id is the kind plus 16 hex characters derived from the kind and the root's absolute path, so it changes if the root's absolute path changes.
- Module and package names are relative to the file's own root. A file `vendor/v.py` indexed from a source root named `vendor` is module `v`, not `vendor.v`.
- When roots overlap, each file is indexed once, under the most specific root, meaning the deepest path. Ties go to the kind order `project`, `source`, `generated`, `declaration`, `dependency`, then to the root id.

### Selecting languages, extensions and exclusions

- `analysis_languages` and `analysis_extensions` are authoritative when set. With `analysis_extensions=.py`, `.pyi` files are not indexed. With `analysis_languages=java`, no Python is indexed.
- `analysis_exclusions` patterns use shell-style wildcards and match file and directory names, not paths. `skip_*,*_tmp.py` skips `skip_me.py` and `data_tmp.py` wherever they are.

### Packages that span roots

- A Python namespace package contributed by several roots is one package entity. Its `roots` metadata lists every contributing root.
- A regular Python package (one with `__init__.py`) wins over a same-named directory without `__init__.py` in another root, as it does in Python's import system. The modules in the shadowed directory stay indexed but have no parent package, and a message is printed to standard error during `deputy sync`. It is not written to `.deputy/deputy.log`.
- Two regular packages with the same name in different roots are both indexed as separate packages. Python imports only the first one on its path, so lookups can show modules Python would not import.
- Java packages declared in two named modules are separate package entities, one per module, each listing only its own classes. Their `module_name` metadata names the module.

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
