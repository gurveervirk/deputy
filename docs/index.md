---
title: deputy - Code intelligence CLI for Python
---

# deputy

Code intelligence CLI for Python. Parses source files with [deproc](https://github.com/gurveervirk/deproc), stores entities (classes, functions, imports, etc.) in a local SQLite database, and provides commands to search, inspect, and resolve symbols.

## Install

```bash
pip install deputy-cli
# or
uv tool install deputy-cli
```

## Quick start

```bash
deputy init
deputy sync
deputy search "Model"
deputy info deputy.core.create_context
deputy resolve deputy.utils.storage.FileMetadata
```

## Learn more

- [Installation](getting-started/installation.md) - setup options and prerequisites
- [Quick Start](getting-started/quickstart.md) - basic workflow walkthrough
- [Commands](commands/overview.md) - full command reference
- [Inheritance Guide](guides/inheritance.md) - understanding class hierarchy resolution
- [Database Schema](reference/database.md) - tables and entity metadata
