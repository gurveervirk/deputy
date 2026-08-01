---
title: Quick Start
---

# Quick Start

A minimal workflow to get deputy working with your project.

## 1. Initialize the database

```bash
deputy init
```

Creates `.deputy.db` in the current directory and auto-detects your virtual environment.

## 2. Sync your project

```bash
deputy sync
```

Parses all Python source files under the current directory and indexes them into the database. Incremental by default - only changed files are re-parsed.

## 3. Search for entities

```bash
deputy search "Model"
```

Finds all entities whose name or full path matches the regex `Model`.

## 4. Inspect an entity

```bash
deputy info deputy.core.create_context
```

Shows metadata, source location, and (for classes) inheritance information.

## 5. Resolve through imports

```bash
deputy resolve deputy.utils.storage.FileMetadata
```

Traces import aliases and re-exports to find the original definition.

## Include dependencies

To also index third-party packages from your virtual environment:

```bash
deputy sync --sync-deps
```

## Auto-sync

When auto-sync is enabled, search and info commands automatically re-sync if source files have changed:

```bash
deputy config auto_sync true
```
