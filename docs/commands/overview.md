---
title: Commands Overview
---

# Commands Overview

All commands accept these global flags:

| Flag | Description |
|------|-------------|
| `--verbose` / `-v` | Set log level to `DEBUG` |
| `--quiet` / `-q` | Set log level to `ERROR` |

## Available commands

| Command | Description |
|---------|-------------|
| [`init`](init.md) | Create a new database |
| [`sync`](sync.md) | Scan and index project files |
| [`search`](search.md) | Search entities by regex |
| [`info`](info.md) | Inspect entity details |
| [`resolve`](resolve.md) | Trace import chains to original definition |
| [`subclasses`](subclasses.md) | Find direct or transitive subclasses |
| [`pin-inheritance`](pin-inheritance.md) | Disambiguate unresolved base classes |
| [`config`](config.md) | Read or write configuration |

## Typical workflow

```bash
# Setup
deputy init
deputy sync

# Query
deputy search "MyClass"
deputy info mypackage.module.MyClass
deputy resolve mypackage.aliases.MyClass

# Inheritance
deputy subclasses mypackage.module.MyClass
deputy pin-inheritance mypackage.module.MyClass Base src/module.py:12
```
