---
title: pin-inheritance
---

# `deputy pin-inheritance`

Disambiguates unresolved base classes by pinning a specific definition.

When a class inherits from an ambiguous or unresolved base (e.g. a name imported from multiple locations), `pin-inheritance` tells deputy exactly which definition to use for MRO computation.

## Usage

```bash
deputy pin-inheritance <class_fqn> <base_name> <entity_ref> [options]
```

## Options

| Flag | Description |
|------|-------------|
| `--remove` / `-r` | Remove an existing pin |
| `--list` / `-l` | List all pins for the current branch |

## Arguments

| Argument | Description |
|----------|-------------|
| `class_fqn` | Fully qualified name of the class to pin a base for |
| `base_name` | Short name of the unresolved base class |
| `entity_ref` | `file_path:lineno[:col_offset]` of the candidate to pin |

## Examples

```bash
# Pin a base class to a specific import location
deputy pin-inheritance myapp.models.Model Base models.py:10

# List all pins for the current branch
deputy pin-inheritance --list

# Remove a pin
deputy pin-inheritance myapp.models.Model Base --remove
```

## Finding the right entity_ref

1. Look up the class to see its unresolved bases:
   ```bash
   deputy info myapp.models.Model
   ```
2. The `unresolved_bases` column shows candidate locations - pick the right one.
3. Use the module path and line number as `entity_ref`.

## What happens after pinning

After pinning or removing a pin, deputy:

1. Updates the `class_bases` table to reflect the new resolution
2. Re-runs eager inherited member resolution across all classes
3. `INHERITED_MEMBER` entities are re-created to reflect the updated MRO

Pins are branch-scoped - pinning on `main` does not affect other branches.
