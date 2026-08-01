---
title: resolve
---

# `deputy resolve`

Traces import alias chains to find the original definition of a symbol.

## Usage

```bash
deputy resolve <symbol> [options]
```

`symbol` must be in the form `<module_fqn>.<symbol_name>` (e.g. `deputy.utils.storage.FileMetadata`).

## Options

| Flag | Description |
|------|-------------|
| `--auto` | Only stop when multiple choices exist |
| `--step` | Stop at every step regardless of ambiguity |
| `--all` | Show all possible resolutions |
| `--compact` | Compact output with `--all` (terminal entities only) |

## Behaviour

1. Looks up the module, finds import aliases matching the symbol name.
2. Follows each alias to its target module, recurses.
3. When the target is a concrete class/function, that's the resolution.
4. With `--auto`, continues automatically unless there are multiple candidate aliases.
5. With `--step`, pauses at every branch point for manual selection.

## Examples

```bash
# Default resolution
deputy resolve deputy.utils.storage.FileMetadata

# Auto-mode: only stop when ambiguous
deputy resolve deputy.utils.storage.FileMetadata --auto

# Show all possible resolution trees
deputy resolve deputy.utils.storage.FileMetadata --all
```
