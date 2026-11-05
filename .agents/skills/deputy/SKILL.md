---
name: deputy
description: Use deputy CLI to search symbols, inspect entities, resolve imports, find subclasses, and manage inheritance pins in Python projects. USE FOR: finding code definitions, tracing imports, analyzing class hierarchies, checking MRO chains, exploring unfamiliar codebases. DO NOT USE FOR: editing code, running tests, git operations.
metadata:
  category: code-intelligence
  language: python
---

# deputy — Code Intelligence CLI

## Setup
```bash
deputy init    # create database
deputy sync    # scan and index project
```

## Commands

Use `deputy <command> --help` for the most accurate and up-to-date usage information.

`deputy search <regex>` — find entities matching pattern. `-t CLASS` filter by type. `--exact` for exact full_path match.

`deputy info <full_path>` — inspect entity metadata. `-c` select columns (mro, inherited_from, parent_classes, etc). `--extract` show actual source text. `--list-columns` for options.

`deputy resolve <module.symbol>` — trace import alias chain to definition.

`deputy subclasses <class_fqn>` — find subclasses. `-t` for transitive.

`deputy pin-inheritance <class> <base> <file:line>` — pin unresolved base. `--list` view pins. `--remove` unpin.

## Widening the indexed scope

By default only the directory where `deputy init` ran is indexed. If an entity you expect is missing because it lives in generated code, stubs or a sibling directory, add a root and sync again:

```bash
deputy config source_roots ../shared/src:vendor/tools
deputy config generated_roots build/generated
deputy sync
```

Other keys: `declaration_roots`, `dependency_roots`, `project_roots` (replaces the default project directory), `analysis_languages`, `analysis_extensions`, `analysis_exclusions`. Lists use `:` as the separator (`;` on Windows). Module names are relative to their own root, so a file `vendor/tools/x.py` indexed from the source root `vendor/tools` is module `x`. Read `docs/reference/config.md` for the full rules.

## When to use me
- Finding where a function or class is defined
- Understanding class inheritance and MRO
- Tracing imports to find original definitions
- Checking which classes inherit from a base class
- Resolving ambiguous base class references
- Exploring an unfamiliar codebase
