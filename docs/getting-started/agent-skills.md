---
title: Agent Skills
---

# Agent Skills

deputy ships an [Agent Skills](https://agentskills.io) definition that teaches AI coding agents how to use deputy for code intelligence tasks.

## What is this?

[Agent Skills](https://agentskills.io) is an open standard supported by 30+ agent tools (Claude Code, OpenCode, Cursor, GitHub Copilot, VS Code, Codex, Gemini CLI, Junie, and many more). Skills package specialized knowledge into portable, version-controlled folders that agents load on demand.

deputy's skill definition lives at `.agents/skills/deputy/SKILL.md` in the repository.

## Auto-discovery

When an AI agent works in this repository, the skill is discovered automatically -- no installation needed. The agent sees deputy as an available skill and can load it when relevant (e.g. when searching for symbols, tracing imports, or analyzing class hierarchies).

## Install in other projects

To add the deputy skill to a different project so that AI agents working there can use deputy:

```bash
npx skills add gurveervirk/deputy
```

This installs `.agents/skills/deputy/SKILL.md` into your project.

## Compatible agents

Agent Skills are supported by a large ecosystem of AI coding tools:

- Claude Code
- OpenCode
- Cursor
- GitHub Copilot
- VS Code
- OpenAI Codex
- Gemini CLI
- JetBrains Junie
- Goose
- Roo Code
- Factory
- And 20+ more

See the full list at [agentskills.io](https://agentskills.io).

## What the skill teaches agents

The deputy skill teaches agents to:

- Use `deputy search` to find symbols by regex
- Use `deputy info` to inspect entity metadata, inheritance, and MRO
- Use `deputy resolve` to trace import aliases to their original definitions
- Use `deputy subclasses` to find direct and transitive subclasses
- Use `deputy pin-inheritance` to resolve ambiguous base class references
- Run `deputy --help` for the most accurate and up-to-date usage information
