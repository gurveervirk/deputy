import contextlib
import json
import sqlite3

from deproc.plugins.python.utils.mro import compute_mro_from_bases

from deputy.database.sqlite import (
    delete_class_bases_by_class,
    get_branch_entities,
    get_direct_bases,
    get_entities_by_ids,
    get_entities_by_path,
    get_entity_by_id,
    get_entity_by_path,
    get_entity_ids_by_fqn,
    get_inheritance_pin,
    upsert_class_bases,
    upsert_entity,
)
from deputy.logger import get_logger
from deputy.tools.utils import (
    get_containing_module_fqn,
    get_parent_id,
)
from deputy.utils.git import get_current_branch

logger = get_logger("tools.inheritance")


def _resolve_alias_target(
    conn: sqlite3.Connection, alias_entity: dict
) -> tuple[str | None, str | None]:
    """Resolve an IMPORT_ALIAS entity to the FQN and entity ID of its target class/function."""
    meta = json.loads(alias_entity["metadata_json"])
    original_name = meta.get("original_name", alias_entity.get("name", ""))
    parent_id = alias_entity.get("parent_id")
    if not parent_id:
        return None, None
    import_stmt = get_entity_by_id(conn, parent_id)
    if not import_stmt:
        return None, None
    import_path = import_stmt.get("name", "")
    target_fqn = f"{import_path}.{original_name}" if import_path else original_name
    target_ids = get_entity_ids_by_fqn(conn, target_fqn)
    for tid in target_ids:
        target = get_entity_by_id(conn, tid)
        if target and target["type"] in ("CLASS", "FUNCTION", "VARIABLE", "METHOD"):
            return target["full_path"], target["id"]
    return None, None


def resolve_base_name_in_module(
    conn: sqlite3.Connection,
    base_name: str,
    module_fqn: str,
    class_lineno: int | None = None,
) -> list[dict]:
    """Find all candidate entities (IMPORT_ALIAS or CLASS) in a module that match a base class name."""
    module_ids = get_entity_ids_by_fqn(conn, module_fqn)
    if not module_ids:
        return []

    candidates: list[dict] = []

    # Find import aliases matching the base name in the same module
    alias_ids = get_entity_ids_by_fqn(conn, f"{module_fqn}.{base_name}")
    alias_entities = get_entities_by_ids(conn, alias_ids)
    for ent in alias_entities:
        if ent["type"] == "IMPORT_ALIAS":
            meta = json.loads(ent["metadata_json"])
            ent_lineno = meta.get("lineno")
            if (
                class_lineno is not None
                and ent_lineno is not None
                and ent_lineno >= class_lineno
            ):
                continue
            # Classify scope: walk parent chain to check for ControlFlowBlock
            scope = _classify_candidate_scope(conn, ent)
            # Resolve IMPORT_ALIAS to its target entity (the actual class being imported)
            resolved_fqn, resolved_entity_id = _resolve_alias_target(conn, ent)
            candidates.append(
                {
                    "entity": ent,
                    "full_path": ent["full_path"],
                    "entity_id": ent["id"],
                    "resolved_fqn": resolved_fqn,
                    "resolved_entity_id": resolved_entity_id,
                    "lineno": ent_lineno,
                    "scope": scope,
                    "kind": "IMPORT_ALIAS",
                }
            )

    # Find direct CLASS definitions in the same module with the same name
    class_ids = get_entity_ids_by_fqn(conn, f"{module_fqn}.{base_name}")
    all_class_entities = get_entities_by_ids(conn, class_ids)
    for ent in all_class_entities:
        if ent["type"] == "CLASS":
            meta = json.loads(ent["metadata_json"])
            ent_lineno = meta.get("lineno")
            if (
                class_lineno is not None
                and ent_lineno is not None
                and ent_lineno >= class_lineno
            ):
                continue
            scope = _classify_candidate_scope(conn, ent)
            candidates.append(
                {
                    "entity": ent,
                    "full_path": ent["full_path"],
                    "entity_id": ent["id"],
                    "resolved_fqn": ent["full_path"],
                    "resolved_entity_id": ent["id"],
                    "lineno": ent_lineno,
                    "scope": scope,
                    "kind": "CLASS",
                }
            )

    return candidates


def _classify_candidate_scope(conn: sqlite3.Connection, entity: dict) -> str:
    """Walk parent chain to determine if the entity is module-level or conditional.

    Returns 'module_level' or 'conditional' with branch info encoded.
    """
    current_id = get_parent_id(entity)
    while current_id:
        current = get_entity_by_id(conn, current_id)
        if not current:
            break
        if current["type"] == "CONTROL_FLOW_BLOCK":
            return f"conditional:{current.get('name', '')}"
        if current["type"] in (
            "PYTHON_MODULE",
            "JAVA_MODULE",
            "PACKAGE",
            "NAMESPACE_PACKAGE",
            "PACKAGE_INFO",
        ):
            return "module_level"
        current_id = get_parent_id(current)
    return "module_level"


def pick_closest_module_level_candidate(candidates: list[dict]) -> dict | None:
    """Return the module-level candidate closest to (just before) the class definition."""
    module_level = [c for c in candidates if c["scope"] == "module_level"]
    if not module_level:
        return None
    module_level.sort(
        key=lambda c: c["lineno"] if c["lineno"] is not None else -1, reverse=True
    )
    return module_level[0]


def has_multiple_candidates(candidates: list[dict]) -> bool:
    """Return True if multiple module-level candidates exist (ambiguous import)."""
    module_level = [c for c in candidates if c["scope"] == "module_level"]
    return len(module_level) > 1


def resolve_all_inherits(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str | None = None,
) -> None:
    """Resolve base classes for all CLASS records and write results to class_bases table."""
    class_records = [r for r in records if r["type"] == "CLASS"]

    for record in class_records:
        meta = json.loads(record["metadata_json"])
        parent_classes = meta.get("parent_classes", [])
        class_entity_id = record["id"]
        delete_class_bases_by_class(conn, class_entity_id)
        if branch is not None:
            if parent_classes:
                placeholders = ",".join("?" for _ in parent_classes)
                conn.execute(
                    f"""DELETE FROM inheritance_pins
                        WHERE class_entity_id = ? AND branch_name = ?
                        AND base_name NOT IN ({placeholders})""",
                    (class_entity_id, branch, *parent_classes),
                )
            else:
                conn.execute(
                    "DELETE FROM inheritance_pins WHERE class_entity_id = ? AND branch_name = ?",
                    (class_entity_id, branch),
                )
        if not parent_classes:
            meta["resolved_bases"] = []
            record["metadata_json"] = json.dumps(meta, default=str)
            continue

        class_lineno = meta.get("lineno")

        module_fqn = get_containing_module_fqn(conn, class_entity_id)
        if not module_fqn:
            logger.warning("cannot determine module for class %s", record["full_path"])
            continue

        resolved_bases = []
        for base_name in parent_classes:
            candidates = resolve_base_name_in_module(
                conn, base_name, module_fqn, class_lineno
            )

            closest = pick_closest_module_level_candidate(candidates)

            if (
                closest
                and not has_multiple_candidates(candidates)
                and len(
                    [c for c in candidates if c["scope"].startswith("module_level")]
                )
                == 1
            ):
                resolved_entity_id = closest.get("resolved_entity_id")
                if resolved_entity_id is None:
                    candidates_info = []
                    for c in candidates:
                        candidates_info.append(
                            {
                                "full_path": c["full_path"],
                                "entity_id": c["entity_id"],
                                "lineno": c["lineno"],
                                "scope": c["scope"],
                                "kind": c["kind"],
                            }
                        )
                    resolved_bases.append(
                        {
                            "base_full_path": base_name,
                            "base_entity_id": None,
                            "is_resolved": False,
                            "branch_info": json.dumps(candidates_info),
                        }
                    )
                    logger.info(
                        "unresolved base %s for %s: target not in DB",
                        base_name,
                        record["full_path"],
                    )
                else:
                    resolved_bases.append(
                        {
                            "base_full_path": closest["resolved_fqn"],
                            "base_entity_id": resolved_entity_id,
                            "is_resolved": True,
                            "branch_info": None,
                        }
                    )
            elif candidates:
                candidates_info = []
                for c in candidates:
                    candidates_info.append(
                        {
                            "full_path": c["full_path"],
                            "entity_id": c["entity_id"],
                            "lineno": c["lineno"],
                            "scope": c["scope"],
                            "kind": c["kind"],
                        }
                    )
                resolved_bases.append(
                    {
                        "base_full_path": base_name,
                        "base_entity_id": None,
                        "is_resolved": False,
                        "branch_info": json.dumps(candidates_info),
                    }
                )
                logger.info(
                    "unresolved base %s for %s: %d candidates (conditional)",
                    base_name,
                    record["full_path"],
                    len(candidates),
                )
            else:
                resolved_bases.append(
                    {
                        "base_full_path": base_name,
                        "base_entity_id": None,
                        "is_resolved": False,
                        "branch_info": None,
                    }
                )
                logger.info(
                    "unresolved base %s for %s: no candidates found",
                    base_name,
                    record["full_path"],
                )

        delete_class_bases_by_class(conn, class_entity_id)
        upsert_class_bases(conn, class_entity_id, resolved_bases)

        resolved_bases_meta = []
        for i, base_name in enumerate(parent_classes):
            entry = resolved_bases[i] if i < len(resolved_bases) else None
            if entry:
                resolved_bases_meta.append(
                    {
                        "name": base_name,
                        "full_path": entry["base_full_path"]
                        if entry["is_resolved"]
                        else None,
                        "entity_id": entry["base_entity_id"]
                        if entry["is_resolved"]
                        else None,
                        "is_resolved": entry["is_resolved"],
                    }
                )
            else:
                resolved_bases_meta.append(
                    {
                        "name": base_name,
                        "full_path": None,
                        "entity_id": None,
                        "is_resolved": False,
                    }
                )
        meta["resolved_bases"] = resolved_bases_meta
        record["metadata_json"] = json.dumps(meta, default=str)


def clean_inherited_member_entities(
    conn: sqlite3.Connection,
    branch: str | None = None,
    class_entity_ids: list[str] | None = None,
) -> None:
    if branch is None:
        rows = conn.execute(
            "SELECT id FROM entities WHERE json_extract(metadata_json, '$.inherited') = 1"
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"DELETE FROM branch_entities WHERE entity_id IN ({placeholders})",
                ids,
            )
            conn.execute(f"DELETE FROM entities WHERE id IN ({placeholders})", ids)
        return

    if class_entity_ids:
        placeholders = ",".join("?" for _ in class_entity_ids)
        rows = conn.execute(
            f"""SELECT DISTINCT e.id
                FROM entities e
                LEFT JOIN branch_entities be ON be.entity_id = e.id
                    AND be.branch_name = ?
                WHERE json_extract(e.metadata_json, '$.inherited') = 1
                  AND (be.entity_id IS NOT NULL
                       OR e.parent_id IN ({placeholders}))""",
            (branch, *class_entity_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT e.id
                FROM entities e
                JOIN branch_entities be ON be.entity_id = e.id
                WHERE json_extract(e.metadata_json, '$.inherited') = 1
                  AND be.branch_name = ?""",
            (branch,),
        ).fetchall()
    ids = [row["id"] for row in rows]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"DELETE FROM branch_entities WHERE branch_name = ? AND entity_id IN ({placeholders})",
        (branch, *ids),
    )
    conn.execute(
        f"""DELETE FROM entities
            WHERE id IN ({placeholders})
              AND id NOT IN (SELECT entity_id FROM branch_entities)""",
        ids,
    )


def _create_inherited_base_aliases(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str,
) -> list[dict]:
    """Pass 1: Walk MRO for each CLASS and create synthetic aliases for inherited methods, properties, inner types."""
    created: list[dict] = []

    for record in records:
        if record["type"] != "CLASS":
            continue

        class_fqn = record["full_path"]
        class_entity_id = record["id"]
        mro = _compute_partial_class_mro(conn, class_entity_id)
        if mro is None or len(mro) < 2:
            continue

        # Collect names that class directly defines (these are shadowed)
        own_meta = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            own_meta = json.loads(record["metadata_json"])

        own_member_ids = set()
        own_member_ids.update(own_meta.get("method_ids", []))
        own_member_ids.update(own_meta.get("property_ids", []))
        own_member_ids.update(own_meta.get("inner_type_ids", []))

        direct_own_names: set[str] = set()
        for mid in own_member_ids:
            ment = get_entity_by_id(conn, mid)
            if ment:
                name = ment.get("name", "")
                if name:
                    direct_own_names.add(name)

        seen_member_names: set[str] = set()

        for mro_idx, source_fqn in enumerate(mro[1:], 1):
            source_entities = get_entities_by_path(conn, source_fqn)
            source_class = next(
                (e for e in source_entities if e["type"] == "CLASS"), None
            )
            if not source_class:
                continue

            source_meta = {}
            try:
                source_meta = json.loads(source_class["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            for member_id in source_meta.get("method_ids", []):
                member = get_entity_by_id(conn, member_id)
                if not member:
                    continue
                member_name = member.get("name", "")
                if (
                    not member_name
                    or member_name in direct_own_names
                    or member_name in seen_member_names
                ):
                    continue
                seen_member_names.add(member_name)
                syn = _create_synthetic_entity(
                    class_fqn, member, source_fqn, mro_idx, class_entity_id, conn
                )
                if syn:
                    created.append(syn)

            for member_id in source_meta.get("property_ids", []):
                member = get_entity_by_id(conn, member_id)
                if not member:
                    continue
                member_name = member.get("name", "")
                if (
                    not member_name
                    or member_name in direct_own_names
                    or member_name in seen_member_names
                ):
                    continue
                seen_member_names.add(member_name)
                syn = _create_synthetic_entity(
                    class_fqn, member, source_fqn, mro_idx, class_entity_id, conn
                )
                if syn:
                    created.append(syn)

            for inner_id in source_meta.get("inner_type_ids", []):
                inner = get_entity_by_id(conn, inner_id)
                if not inner:
                    continue
                member_name = inner.get("name", "")
                if (
                    not member_name
                    or member_name in direct_own_names
                    or member_name in seen_member_names
                ):
                    continue
                seen_member_names.add(member_name)
                syn = _create_synthetic_entity(
                    class_fqn, inner, source_fqn, mro_idx, class_entity_id, conn
                )
                if syn:
                    created.append(syn)

    return created


def _create_synthetic_entity(
    class_fqn: str,
    target: dict,
    inherited_from: str,
    mro_depth: int,
    class_entity_id: str,
    conn: sqlite3.Connection,
) -> dict | None:
    """Create an INHERITED_MEMBER synthetic entity pointing to a target member from a base class."""
    own_name = target.get("name", "")
    if not own_name:
        return None

    alias_full_path = f"{class_fqn}.{own_name}"

    syn_meta = {
        "inherited": True,
        "target_entity_id": target["id"],
        "inherited_from": inherited_from,
        "mro_depth": mro_depth,
        "own_name": own_name,
    }

    syn_record = {
        "id": alias_full_path,
        "language": "python",
        "full_path": alias_full_path,
        "name": own_name,
        "type": "INHERITED_MEMBER",
        "metadata_json": json.dumps(syn_meta, default=str),
        "parent_id": class_entity_id,
    }
    upsert_entity(conn, **syn_record)
    return syn_record


def _create_inherited_inner_class_aliases(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str,
) -> list[dict]:
    """Pass 2: Walk inner class MRO chains to create synthetic aliases for members accessed through inherited inner classes."""
    created: list[dict] = []

    for record in records:
        if record["type"] != "CLASS":
            continue

        class_fqn = record["full_path"]
        class_entity_id = record["id"]
        mro = _compute_partial_class_mro(conn, class_entity_id)
        if mro is None or len(mro) < 2:
            continue

        own_meta = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            own_meta = json.loads(record["metadata_json"])

        own_inner_type_ids = own_meta.get("inner_type_ids", [])

        seen_member_full_paths: set[str] = set()

        for mro_idx, source_fqn in enumerate(mro[1:], 1):
            source_entities = get_entities_by_path(conn, source_fqn)
            source_class = next(
                (e for e in source_entities if e["type"] == "CLASS"), None
            )
            if not source_class:
                continue

            source_meta = {}
            try:
                source_meta = json.loads(source_class["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            source_inner_type_ids = source_meta.get("inner_type_ids", [])

            for inner_id in source_inner_type_ids:
                if inner_id in own_inner_type_ids:
                    continue

                inner = get_entity_by_id(conn, inner_id)
                if not inner:
                    continue

                inner_class_fqn = inner["full_path"]

                inner_entities = get_entities_by_path(conn, inner_class_fqn)
                inner_class = next(
                    (e for e in inner_entities if e["type"] == "CLASS"), None
                )
                if not inner_class:
                    continue

                inner_meta = {}
                try:
                    inner_meta = json.loads(inner_class["metadata_json"])
                except (json.JSONDecodeError, TypeError):
                    continue

                # Recursively set up inherited members for this inner class
                inner_methods = inner_meta.get("method_ids", [])
                inner_properties = inner_meta.get("property_ids", [])
                inner_inner_types = inner_meta.get("inner_type_ids", [])

                for member_id in inner_methods + inner_properties + inner_inner_types:
                    member = get_entity_by_id(conn, member_id)
                    if not member:
                        continue
                    full_p = member["full_path"]
                    if full_p in seen_member_full_paths:
                        continue
                    seen_member_full_paths.add(full_p)

                    syn = _create_synthetic_entity(
                        class_fqn,
                        member,
                        inner_class_fqn,
                        mro_idx + 1,
                        class_entity_id,
                        conn,
                    )
                    if syn:
                        created.append(syn)

    return created


def eager_resolve_all_inherited_members(
    conn: sqlite3.Connection,
    records: list[dict] | None = None,
    branch: str | None = None,
) -> None:
    """Clean all inherited members and recreate them via Pass 1 (direct MRO) and Pass 2 (inner class MRO)."""
    if not branch:
        import os

        branch = os.environ.get("DEPUTY_BRANCH", "default")

    if records is None:
        if branch:
            records = [
                entity
                for entity in get_branch_entities(conn, branch)
                if entity["type"] == "CLASS"
            ]
        else:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = 'CLASS'"
            ).fetchall()
            records = [dict(r) for r in rows]

    clean_inherited_member_entities(
        conn, branch=branch, class_entity_ids=[r["id"] for r in records]
    )

    pass1 = _create_inherited_base_aliases(conn, records, branch)
    logger.debug("eager resolution pass 1: %d synthetic entities created", len(pass1))

    pass2 = _create_inherited_inner_class_aliases(conn, records, branch)
    logger.debug("eager resolution pass 2: %d synthetic entities created", len(pass2))

    all_ids = [s["id"] for s in pass1 + pass2]
    if all_ids:
        from deputy.database.sqlite import upsert_branch_entities

        upsert_branch_entities(conn, branch, all_ids)
        conn.commit()


def _compute_mro_parts(
    conn: sqlite3.Connection,
    class_entity_id: str,
    memo: dict[str, tuple[list[str] | None, bool]] | None = None,
    _visiting: set[str] | None = None,
) -> tuple[list[str] | None, bool]:
    if memo is None:
        memo = {}
    if _visiting is None:
        _visiting = set()

    if class_entity_id in memo:
        return memo[class_entity_id]

    if class_entity_id in _visiting:
        logger.warning("cycle detected in MRO for entity %s", class_entity_id)
        return None, False

    entity = get_entity_by_id(conn, class_entity_id)
    if not entity or entity["type"] != "CLASS":
        return None, False

    _visiting.add(class_entity_id)

    meta = {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        meta = json.loads(entity["metadata_json"])

    parent_classes = meta.get("parent_classes", [])
    if not parent_classes:
        result = [entity["full_path"]]
        memo[class_entity_id] = (result, True)
        _visiting.discard(class_entity_id)
        return result, True

    branch = get_current_branch()
    direct_bases = get_direct_bases(conn, class_entity_id)

    resolved_prefix: list[dict] = []
    base_mros: list[list[str]] = []
    complete = len(direct_bases) == len(parent_classes)

    for base in direct_bases:
        if base.get("is_resolved"):
            base_full_path = base["base_full_path"]
            base_entity_id = base["base_entity_id"]
        else:
            base_name = base["base_full_path"]
            pin = (
                get_inheritance_pin(conn, class_entity_id, base_name, branch)
                if branch
                else None
            )
            if pin:
                pinned_entity = get_entity_by_id(conn, pin["pinned_entity_id"])
                if pinned_entity and pinned_entity["type"] == "CLASS":
                    base_full_path = pinned_entity["full_path"]
                    base_entity_id = pin["pinned_entity_id"]
                else:
                    complete = False
                    break
            else:
                complete = False
                break

        base_mro, base_complete = _compute_mro_parts(
            conn, base_entity_id, memo, _visiting
        )
        if base_mro is None:
            complete = False
            break

        resolved_prefix.append(
            {"base_full_path": base_full_path, "base_entity_id": base_entity_id}
        )
        base_mros.append(base_mro)
        if not base_complete:
            complete = False
            break

    if len(resolved_prefix) != len(direct_bases):
        complete = False

    if not resolved_prefix:
        result = [entity["full_path"]]
        memo[class_entity_id] = (result, complete)
        _visiting.discard(class_entity_id)
        return result, complete

    base_mro_dict: dict[str, list[str] | None] = {
        str(b["base_full_path"]): base_mros[i] for i, b in enumerate(resolved_prefix)
    }
    base_fqns = [str(b["base_full_path"]) for b in resolved_prefix]

    result = compute_mro_from_bases(entity["full_path"], base_mro_dict, base_fqns)
    if result is None:
        logger.warning("inconsistent MRO for %s", entity["full_path"])
        complete = False

    if result is None:
        result = [entity["full_path"]]
    memo[class_entity_id] = (result, complete)
    _visiting.discard(class_entity_id)
    return result, complete


def _compute_partial_class_mro(
    conn: sqlite3.Connection, class_entity_id: str
) -> list[str] | None:
    result, _ = _compute_mro_parts(conn, class_entity_id)
    return result


def compute_class_mro(
    conn: sqlite3.Connection,
    class_entity_id: str,
    memo: dict[str, list[str] | None] | None = None,
    _visiting: set[str] | None = None,
) -> list[str] | None:
    if memo is not None and class_entity_id in memo:
        return memo[class_entity_id]
    parts_memo = (
        {entity_id: (value, value is not None) for entity_id, value in memo.items()}
        if memo is not None
        else None
    )
    result, complete = _compute_mro_parts(
        conn, class_entity_id, memo=parts_memo, _visiting=_visiting
    )
    if memo is not None:
        for entity_id, (partial, is_complete) in (parts_memo or {}).items():
            memo[entity_id] = partial if is_complete else None
    return result if complete else None


def get_inherited_members(
    conn: sqlite3.Connection,
    class_entity_id: str,
    mro_fqns: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Collect inherited methods, properties, and inner types from the MRO, deduped by name."""
    if mro_fqns is None:
        mro = compute_class_mro(conn, class_entity_id)
    else:
        entity = get_entity_by_id(conn, class_entity_id)
        if entity:
            class_fqn = entity["full_path"]
            mro = [class_fqn] if class_fqn else None
            if mro and mro_fqns:
                if class_fqn in mro_fqns:
                    idx = mro_fqns.index(class_fqn)
                    mro = mro_fqns[idx:]
                else:
                    mro = mro_fqns
        else:
            mro = None

    if mro is None or len(mro) < 2:
        return {}

    inherited: dict[str, list[dict]] = {}

    seen_names: set[str] = set()

    for i, fqn in enumerate(mro[1:], 1):
        entities = get_entities_by_path(conn, fqn)
        class_entity = next((e for e in entities if e["type"] == "CLASS"), None)
        if not class_entity:
            continue

        cm = {}
        try:
            cm = json.loads(class_entity["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        member_types = {
            "METHOD": "method_ids",
            "INNER_TYPE": "inner_type_ids",
            "PROPERTY": "property_ids",
        }

        for display_type, id_key in member_types.items():
            member_ids = cm.get(id_key, [])
            for member_id in member_ids:
                member = get_entity_by_id(conn, member_id)
                if member and member.get("full_path"):
                    member_name = member.get("name", "")
                    if member_name and member_name not in seen_names:
                        seen_names.add(member_name)
                        entry = dict(member)
                        entry["_inherited_from"] = fqn
                        entry["_mro_index"] = i
                        if display_type not in inherited:
                            inherited[display_type] = []
                        inherited[display_type].append(entry)

    return inherited


def get_class_inheritance_info(
    conn: sqlite3.Connection,
    class_entity_id: str,
) -> dict:
    """Return full inheritance info: MRO, resolved/unresolved bases, and inherited members."""
    mro = compute_class_mro(conn, class_entity_id)
    direct_bases = get_direct_bases(conn, class_entity_id)

    resolved = []
    unresolved = []
    for base in direct_bases:
        if base.get("is_resolved"):
            resolved.append(
                {
                    "base_full_path": base["base_full_path"],
                    "base_entity_id": base["base_entity_id"],
                }
            )
        else:
            candidates = []
            bi = base.get("branch_info")
            if bi:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    candidates = json.loads(bi)
            unresolved.append(
                {
                    "base_full_path": base["base_full_path"],
                    "candidates": candidates,
                }
            )

    inherited_members = get_inherited_members(conn, class_entity_id, mro)

    return {
        "mro": mro,
        "resolved_bases": resolved,
        "unresolved_bases": unresolved,
        "inherited_members": inherited_members,
    }


def resolve_entity_through_mro(
    conn: sqlite3.Connection,
    full_path: str,
) -> tuple[dict | None, str | None]:
    """Resolve a dotted path (e.g. Child.Inner.foo) through the MRO chain. Returns (entity, inherited_from_fqn)."""
    entity = get_entity_by_path(conn, full_path)
    if entity is not None:
        return entity, None

    parts = full_path.split(".")
    for split_idx in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:split_idx])
        remainder = ".".join(parts[split_idx:])
        prefix_entity = get_entity_by_path(conn, prefix)
        if prefix_entity is None or prefix_entity["type"] != "CLASS":
            continue
        mro = compute_class_mro(conn, prefix_entity["id"])
        if mro is None:
            continue
        for mro_idx, mro_fqn in enumerate(mro):
            candidate_path = f"{mro_fqn}.{remainder}"
            candidate = get_entity_by_path(conn, candidate_path)
            if candidate is not None:
                candidate["_inherited_from"] = mro_fqn
                candidate["_mro_index"] = mro_idx
                return candidate, mro_fqn
        return None, None

    return None, None
