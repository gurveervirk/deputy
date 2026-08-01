# deputy

Code intelligence CLI for Python. Parses source files with [deproc](https://github.com/gurveervirk/deproc), stores entities (classes, functions, imports, etc.) in a local SQLite database, and provides commands to search, inspect, and resolve symbols.

**[Full documentation →](https://gurveervirk.github.io/deputy/)**

## Installation

```bash
pip install deputy
# or: uv tool install deputy
```

## Quick start

```bash
deputy init                                    # create database
deputy sync                                    # scan & index project
deputy search "Model"                          # find entities matching regex
deputy info deputy.core.create_context          # inspect an entity
deputy resolve deputy.utils.storage.FileMetadata  # resolve through imports
deputy subclasses myapp.models.BaseModel       # find direct subclasses
deputy pin-inheritance myapp.models.Model Base models.py:10  # pin ambiguous base
```

## Configuration

Deputy uses a key-value config file (`.deputyconfig`) in the project root:

```bash
deputy config enable_cache true       # cache resolution results
deputy config auto_sync true          # auto-sync before queries
deputy config display_mode tree       # tree view for search results
deputy config sync_deps true          # index .venv dependencies
```

## Logging

```bash
deputy config log_level DEBUG         # most verbose
deputy config log_level WARNING       # default
deputy -v sync                        # one-off debug mode
deputy --quiet search "Model"         # errors only
```

## Development

```bash
git clone https://github.com/gurveervirk/deputy
cd deputy
uv sync
uv run pytest
```

## License

See [LICENSE](LICENSE).
