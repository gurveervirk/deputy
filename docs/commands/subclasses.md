---
title: subclasses
---

# `deputy subclasses`

Finds subclasses of a given class entity.

## Usage

```bash
deputy subclasses <full_path> [--transitive]
```

## Options

| Flag | Description |
|------|-------------|
| `--transitive` / `-t` | Include indirect subclasses (the full inheritance tree) |

## Behaviour

- **Default**: returns only direct subclasses (classes that directly inherit from `full_path`).
- **`--transitive`**: returns the complete subclass tree - classes that directly or indirectly inherit from `full_path`.

Results are displayed as a tree showing entity type, full path, and line number.

## Examples

```bash
# Direct subclasses only
deputy subclasses myapp.models.BaseModel

# Full subclass tree
deputy subclasses myapp.models.BaseModel -t
```
