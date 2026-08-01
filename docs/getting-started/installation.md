---
title: Installation
---

# Installation

deputy requires Python 3.12+ and works best with the [`uv`](https://docs.astral.sh/uv/) package manager.

## pip

```bash
pip install deputy-cli
```

## uv tool install

```bash
uv tool install deputy-cli
```

This installs deputy globally as a standalone CLI tool, isolated from your project dependencies.

## Development

For contributing to deputy itself:

```bash
git clone https://github.com/gurveervirk/deputy
cd deputy
uv sync
```

## Verify

```bash
deputy --help
```

!!! note
    deputy needs a local SQLite database to operate. Run `deputy init` in your project root before using any other command.
