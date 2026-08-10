# deputy

Code intelligence CLI for Python and Java. Parses source files with [deproc](https://github.com/gurveervirk/deproc), stores entities (classes, functions, imports, etc.) in a local SQLite database, and provides commands to search, inspect, and resolve symbols.

**[Full documentation →](https://gurveervirk.github.io/deputy/)**

## Installation

```bash
pip install deputy-cli
# or: uv tool install deputy-cli
```

## Quick start

```bash
deputy init                                    # create database
deputy sync                                    # scan & index project (.py/.pyi/.java)
deputy search "Model"                          # find entities matching regex
deputy search "Controller" --language java     # Java-only search
deputy info deputy.core.create_context          # inspect an entity
deputy info com.example.Main --columns full_path,type,superclass,implements
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

## Agent Skills

deputy ships an [Agent Skills](https://agentskills.io) definition at `.agents/skills/deputy/SKILL.md`. This teaches AI coding agents how to use deputy for code intelligence tasks.

**Supported agents:** Claude Code, OpenCode, Cursor, GitHub Copilot, VS Code, Codex, Gemini CLI, Junie, and 20+ others.

**Auto-discovery:** When an agent works in this repository, the skill is discovered automatically.

**Install in other projects:**

```bash
npx skills add gurveervirk/deputy
```


## License

See [LICENSE](LICENSE).