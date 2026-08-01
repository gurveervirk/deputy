---
title: Inheritance Guide
---

# Inheritance Guide

deputy understands Python class inheritance, computing full MRO chains and synthesising inherited member entities so that search and inspection work across the entire class hierarchy.

## Overview

When you run `deputy sync`, deputy parses each class's `parent_classes` and resolves them to concrete entities via the `class_bases` table. It then eagerly creates `INHERITED_MEMBER` synthetic entities for methods, properties, and inner classes inherited through the MRO.

This means:

- `deputy search "my_method"` finds the method whether you defined it in the class or inherited it
- `deputy info MyClass.my_method` shows where it comes from
- `deputy subclasses MyMixin` finds every class in the tree

## C3 linearisation

deputy computes the method resolution order (MRO) using Python's C3 linearisation algorithm (`c3_merge`). This ensures:

- Depth-first, left-to-right traversal
- Monotonic ordering (a class always appears before its bases)
- Consistent ordering across the hierarchy

The MRO is computed lazily and memoised per entity.

## Resolved vs unresolved bases

Each base class entry in `class_bases` has an `is_resolved` flag:

- **Resolved** (`is_resolved = 1`): the base class maps to a concrete entity in the database
- **Unresolved** (`is_resolved = 0`): the base name could not be uniquely mapped (e.g. ambiguous imports)

When `deputy info` is run on a class with unresolved bases, it shows the candidates and prints a pin hint.

## Pinning inheritance

When a base class is ambiguous (imported from multiple locations), use `pin-inheritance` to disambiguate:

```bash
# Step 1: see what's unresolved
deputy info myapp.models.Model

# Step 2: find the right import
deputy resolve myapp.aliases.Base

# Step 3: pin it
deputy pin-inheritance myapp.models.Model Base models.py:12
```

After pinning, all `INHERITED_MEMBER` entities are re-resolved to reflect the updated MRO.

## Eager resolution

When `deputy sync` runs (or after pinning), deputy performs **eager inheritance resolution**:

1. **Pass 1** - Direct MRO: walks each class's direct bases and creates `INHERITED_MEMBER` entities for inherited methods, properties, and inner classes
2. **Pass 2** - Inner class MRO: walks inherited inner classes and creates synthetic aliases for their members

This ensures inherited members appear in search results immediately, without requiring runtime resolution.

## Inherited member entities

`INHERITED_MEMBER` entities have these metadata fields:

| Field | Description |
|-------|-------------|
| `inherited` | Always `true` |
| `target_entity_id` | ID of the original member being inherited |
| `inherited_from` | FQN of the class providing this member |
| `mro_depth` | Position in the MRO chain (1 = direct base, 2 = grandparent, etc.) |
| `own_name` | Short name of the inherited member |

Example:

```json
{
  "inherited": true,
  "target_entity_id": "m1",
  "inherited_from": "myapp.base.BaseModel",
  "mro_depth": 1,
  "own_name": "save"
}
```

## Direct definitions shadow inherited members

If a class defines a method with the same name as an inherited one, deputy does **not** create an `INHERITED_MEMBER` entity - the directly defined method takes precedence, exactly as Python does.

## Inner classes

Inherited inner classes are handled in Pass 2 of eager resolution. If `Base` has an inner class `Inner` with a method `process`, and `Child(Base)` inherits it, deputy creates:

- `Child.Inner` - inherited inner class alias
- `Child.process` - member of the inherited inner class
