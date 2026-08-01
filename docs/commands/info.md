---
title: info
---

# `deputy info`

Looks up an entity by its exact `full_path` and displays metadata and inheritance information.

## Usage

```bash
deputy info <full_path> [options]
```

## Options

| Flag | Description |
|------|-------------|
| `--all` / `-a` | Return every entity matching that `full_path` |
| `--extract` / `-x` | Extract and display actual source text for signature/docstring |
| `--columns` / `-c` | Comma-separated columns to display |
| `--list-columns` | List all available columns and descriptions |
| `--type` / `-t` | Filter by entity type (e.g. `FUNCTION`, `CLASS`) |
| `--lineno` | Filter by line number |

## Available columns

Use `deputy info --list-columns` to see this list, or `-c` to select specific columns:

| Column | Description |
|--------|-------------|
| `full_path` | Entity full path |
| `language` | Language |
| `type` | Entity type |
| `lineno` | Starting line number |
| `end_lineno` | Ending line number |
| `source` | Source file:lineno |
| `signature` | Signature location as path:line |
| `arguments` | Arguments location as path:line |
| `return_type` | Return type annotation location |
| `docstring` | Docstring location as path:line |
| `decorators` | Decorator names |
| `parent_classes` | Parent/inherited class names |
| `resolved_bases` | Resolved base class FQNs |
| `unresolved_bases` | Unresolved base class names with candidate details |
| `mro` | Full MRO chain |
| `inherited_from` | Class in MRO providing this member (if inherited) |
| `visibility` | `public` / `private` |
| `exported` | Whether exported in `__all__` |

## Class entities

For class entities, `deputy info` automatically displays:

- **Resolved and unresolved base classes**
- **Full MRO chain** (method resolution order)
- **Inherited members**

When unresolved bases are found, it prints a hint:

```
Found unresolved bases: Base (conditional)
Hint: use 'deputy resolve <module>.<name>' to trace imports,
      then 'deputy pin-inheritance <class> <name> <file>:<line>' to pin
```

## Examples

```bash
# Show default columns
deputy info myapp.models.Model

# Show specific columns with extracted source text
deputy info myapp.models.Model -c full_path,signature,docstring -x

# Find all entities with this path
deputy info myapp.models.Model --all

# Filter by type and line number
deputy info myapp.models.Model --type CLASS --lineno 42
```
