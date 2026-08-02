---
title: Architecture
---

# Architecture

## Module structure

```
deputy CLI (typer)
  └─ tools/core.py
       ├─ init → open_database + init_schema + detect_venv
       ├─ sync → get_source_files → deproc parse/link → upsert_entities
       │   └─ --sync-deps → detect_venv → list_installed_packages → process_dependency
       ├─ search → SQLite REGEXP query on entities
       ├─ info → get_entity_by_path / get_entities_by_path + inheritance info
       ├─ subclasses → get_direct_subclasses / get_transitive_subclasses
       ├─ pin-inheritance → upsert_inheritance_pin + eager_resolve_all_inherited_members
       ├─ resolve → InteractiveResolver (import alias chain tracing)
       └─ config → read/write .deputyconfig

  tools/inheritance.py
       ├─ compute_class_mro → delegates to deproc C3 linearisation
       ├─ get_inherited_members → collect from MRO chain
       ├─ eager_resolve_all_inherited_members → create INHERITED_MEMBER entities
       └─ resolve_entity_through_mro → resolve dotted paths via MRO
```

## Data flow

```
Source files (.py/.pyi)
    │
    ▼
deproc parser (linker + models)
    │
    ▼
SQLite database (.deputy.db)
    │
    ├─ search → REGEXP queries on entities table
    ├─ info → entity lookup + class_bases + inheritance_pins
    ├─ resolve → import alias chain tracing
    └─ subclasses → class_bases + branch_entities
```

## Dependencies

| Package | Role |
|---------|------|
| `deproc-core` | Plugin core — AST models, linker, resolver, entity registry interfaces |
| `deproc-python` | Python language parser — tree-sitter based, also provides C3 MRO utilities |
| `deproc-utils-tree-sitter` | Tree-sitter utilities for parsing |
| `typer` | CLI framework |
| `rich` | Terminal output (tables, trees, panels) |

## Branch awareness

deputy is branch-aware. The `branch_entities` and `branch_files` tables track which entities and files belong to which git branch. The current branch is auto-detected via `git branch --show-current`.

- `deputy sync` only re-parses files that changed on the current branch
- `deputy subclasses` scopes results to the current branch by default
- `deputy pin-inheritance` pins are branch-scoped
