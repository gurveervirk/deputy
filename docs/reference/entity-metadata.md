---
title: Entity Metadata
---

# Entity Metadata

Each entity stores a `metadata_json` blob with type-specific fields.

## All entities

| Field | Description |
|-------|-------------|
| `lineno` | Starting line number |
| `end_lineno` | Ending line number |
| `col_offset` | Starting column offset |
| `end_col_offset` | Ending column offset |
| `fqn` | Fully qualified name |

## Modules and packages

| Field | Description |
|-------|-------------|
| `path` | Relative file path |
| `all_exports` | Contents of `__all__` |

## Functions, methods, classes

| Field | Description |
|-------|-------------|
| `visibility` | `public` or `private` |
| `decorators` | List of decorator names |
| `exported` | Whether re-exported via `__all__` |

### Function/method specific

| Field | Description |
|-------|-------------|
| `arguments` | Arguments source location |
| `return_type` | Return type annotation location |
| `docstring` | Docstring source location |
| `signature` | Full signature source location |

## Classes

| Field | Description |
|-------|-------------|
| `parent_classes` | List of parent class FQNs (raw source strings) |
| `method_ids` | Entity IDs of methods belonging to this class |
| `property_ids` | Entity IDs of properties belonging to this class |
| `inner_type_ids` | Entity IDs of inner classes/types belonging to this class |

## Import aliases

| Field | Description |
|-------|-------------|
| `original_name` | Name before aliasing |
| `alias` | The alias (if any) |
| `import_type` | Import kind |
| `wildcard` | Whether it's a wildcard import |

## Dependency entities

| Field | Description |
|-------|-------------|
| `source` | Always `"dependency"` |
| `package_name` | Originating package name |

## Inherited members

`INHERITED_MEMBER` entities are synthetic - created by deputy during inheritance resolution, not present in source code.

| Field | Description |
|-------|-------------|
| `inherited` | Always `true` |
| `target_entity_id` | ID of the original member being inherited |
| `inherited_from` | FQN of the class providing this member |
| `mro_depth` | Position in the MRO chain (1 = direct base) |
| `own_name` | Short name of the inherited member |

Inherited member entities appear in the `entities` table with `type = "INHERITED_MEMBER"` and are linked to their parent class via `parent_id`. They are searchable and resolvable like any other entity.
