import contextlib
import json
import sqlite3

from deproc.core.context import Context
from deproc.core.interfaces.resolver import ResolutionStatus
from deproc.plugins.python.resolver.models import (
    PythonClassMROResult,
    PythonInheritedMembersResult,
)
from deproc.plugins.python.utils.mro import compute_mro_from_bases

from deputy.database.sqlite import (
    delete_class_bases_by_class,
    get_branch_entities,
    get_direct_bases,
    get_entities_by_path,
    get_entity_by_id,
    get_entity_by_path,
    get_inheritance_pin,
    upsert_class_bases,
    upsert_entity,
)
from deputy.logger import get_logger
from deputy.tools.deproc_resolution import (
    DeprocResolutionAdapter,
    build_context_from_records,
)
from deputy.utils.git import get_current_branch

logger = get_logger("tools.inheritance")


def _projection_entities_by_path(
    conn: sqlite3.Connection, full_path: str, branch: str
) -> list[dict]:
    has_branch_entities = conn.execute(
        "SELECT 1 FROM branch_entities WHERE branch_name = ? LIMIT 1",
        (branch,),
    ).fetchone()
    if has_branch_entities is None:
        return get_entities_by_path(conn, full_path)
    return get_entities_by_path(conn, full_path, branch_name=branch)


def _java_base_relationships(record: dict) -> list[tuple[str, str]]:
    meta = {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        meta = json.loads(record["metadata_json"])
    if record["type"] == "INTERFACE":
        return [
            (name, "interface_extends") for name in meta.get("extends_interfaces", [])
        ]
    relationships = []
    superclass = meta.get("superclass")
    if superclass:
        relationships.append((superclass, "extends"))
    relationships.extend((name, "implements") for name in meta.get("implements", []))
    return relationships


def _java_base_branch_info(
    status: ResolutionStatus, reason: str | None, candidates
) -> str:
    info = {
        "status": status.value,
        "reason": reason,
        "candidates": [
            {"full_path": fqn, "entity_id": entity_id} for entity_id, fqn in candidates
        ],
    }
    return json.dumps(info, default=str)


def resolve_java_type_references(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str | None = None,
) -> None:
    """Resolve Java superclass/interface type references via deproc and persist class_bases."""
    java_type_records = [
        r
        for r in records
        if r.get("language") == "java"
        and r["type"] in ("CLASS", "INTERFACE", "ENUM", "RECORD")
    ]
    if not java_type_records:
        return

    ctx = build_context_from_records(records)
    resolver = ctx.get_resolver("java")
    resolve_type_reference = getattr(resolver, "resolve_type_reference", None)
    if resolve_type_reference is None:
        logger.warning("no Java resolver available for type reference resolution")
        return

    registry = ctx.entity_registry

    for record in java_type_records:
        entity = registry.get(record["id"])
        if entity is None:
            continue
        base_relationships = _java_base_relationships(record)
        base_names = [name for name, _ in base_relationships]
        class_entity_id = record["id"]
        delete_class_bases_by_class(conn, class_entity_id)

        if branch is not None:
            if base_names:
                placeholders = ",".join("?" for _ in base_names)
                conn.execute(
                    f"""DELETE FROM inheritance_pins
                        WHERE class_entity_id = ? AND branch_name = ?
                        AND base_name NOT IN ({placeholders})""",
                    (class_entity_id, branch, *base_names),
                )
            else:
                conn.execute(
                    "DELETE FROM inheritance_pins WHERE class_entity_id = ? AND branch_name = ?",
                    (class_entity_id, branch),
                )

        if not base_names:
            resolved_bases: list[dict] = []
        else:
            resolved_bases = []
            for base_name, relation_kind in base_relationships:
                result = resolve_type_reference(base_name, entity, ctx)
                if (
                    result.status == ResolutionStatus.RESOLVED
                    and result.value is not None
                ):
                    target = registry.get(result.value)
                    target_fqn = getattr(target, "fqn", None) or base_name
                    resolved_bases.append(
                        {
                            "base_full_path": target_fqn,
                            "base_entity_id": result.value,
                            "is_resolved": True,
                            "branch_info": None,
                            "relation_kind": relation_kind,
                        }
                    )
                else:
                    candidate_fqns = []
                    for candidate_id in result.candidates:
                        candidate = registry.get(candidate_id)
                        candidate_fqns.append(
                            (candidate_id, getattr(candidate, "fqn", None) or base_name)
                        )
                    resolved_bases.append(
                        {
                            "base_full_path": base_name,
                            "base_entity_id": None,
                            "is_resolved": False,
                            "branch_info": _java_base_branch_info(
                                result.status, result.reason, candidate_fqns
                            ),
                            "relation_kind": relation_kind,
                        }
                    )

        upsert_class_bases(conn, class_entity_id, resolved_bases)

        resolved_bases_meta = []
        for i, base_name in enumerate(base_names):
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
                        "relation_kind": entry["relation_kind"],
                    }
                )
        meta = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            meta = json.loads(record["metadata_json"])
        meta["resolved_bases"] = resolved_bases_meta
        meta["parent_classes"] = base_names
        record["metadata_json"] = json.dumps(meta, default=str)


def _normalize_python_base_name(base_name: str) -> str:
    return base_name.split("[", 1)[0].strip()


def _python_base_overrides(
    conn: sqlite3.Connection,
    class_entity_id: str,
    parent_classes: list[str],
    branch: str | None,
) -> dict[tuple[str, str], str]:
    if branch is None:
        return {}

    overrides: dict[tuple[str, str], str] = {}
    for base_name in parent_classes:
        normalized_name = _normalize_python_base_name(base_name)
        pin = get_inheritance_pin(conn, class_entity_id, base_name, branch)
        if pin is None and normalized_name != base_name:
            pin = get_inheritance_pin(conn, class_entity_id, normalized_name, branch)
        if pin is not None:
            overrides[(class_entity_id, normalized_name)] = pin["pinned_entity_id"]
    return overrides


def _python_base_overrides_for_records(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str | None,
) -> dict[tuple[str, str], str]:
    if branch is None:
        return {}

    overrides: dict[tuple[str, str], str] = {}
    for record in records:
        if record.get("language") != "python" or record.get("type") != "CLASS":
            continue
        meta = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            meta = json.loads(record["metadata_json"])
        parent_classes = meta.get("parent_classes", [])
        if not isinstance(parent_classes, list):
            continue
        overrides.update(
            _python_base_overrides(conn, record["id"], parent_classes, branch)
        )
    return overrides


def _python_semantic_base_info(
    context: Context, base
) -> tuple[str, str | None, bool, str | None]:
    if base.status is ResolutionStatus.RESOLVED and base.resolved_id is not None:
        target = context.entity_registry.get(base.resolved_id)
        target_fqn = getattr(target, "fqn", None)
        if target_fqn:
            return target_fqn, base.resolved_id, True, None

    candidates = []
    for candidate_id in base.candidates:
        target = context.entity_registry.get(candidate_id)
        candidates.append(
            {
                "entity_id": candidate_id,
                "full_path": getattr(target, "fqn", None),
            }
        )
    branch_info = {
        "status": base.status.value,
        "candidates": candidates,
    }
    if base.reason:
        branch_info["reason"] = base.reason
    return base.name, None, False, json.dumps(branch_info, sort_keys=True)


def _python_semantic_records(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str | None = None,
) -> list[dict]:
    by_id = {record["id"]: record for record in records}
    if branch is None:
        dependency_rows = conn.execute(
            """SELECT * FROM entities
               WHERE json_extract(metadata_json, '$.source') = 'dependency'"""
        ).fetchall()
    else:
        dependency_rows = conn.execute(
            """SELECT e.* FROM entities e
               JOIN branch_entities be ON be.entity_id = e.id
               WHERE be.branch_name = ?
                 AND json_extract(e.metadata_json, '$.source') = 'dependency'""",
            (branch,),
        ).fetchall()
    for row in dependency_rows:
        record = dict(row)
        by_id.setdefault(record["id"], record)
    return list(by_id.values())


def _resolve_python_inherits(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str | None,
) -> tuple[dict[str, PythonClassMROResult], Context]:
    semantic_records = _python_semantic_records(conn, records, branch=branch)
    context = build_context_from_records(semantic_records)
    resolver = context.get_resolver("python")
    resolve_class_mro = getattr(resolver, "resolve_class_mro", None)
    if resolve_class_mro is None:
        return {}, context

    results: dict[str, PythonClassMROResult] = {}
    overrides = _python_base_overrides_for_records(conn, semantic_records, branch)
    for record in records:
        if record["type"] != "CLASS" or record.get("language") != "python":
            continue
        results[record["id"]] = resolve_class_mro(
            record["id"], context, base_overrides=overrides
        )
    return results, context


def resolve_all_inherits(
    conn: sqlite3.Connection,
    records: list[dict],
    branch: str | None = None,
) -> dict[str, PythonClassMROResult]:
    """Resolve inheritance through deproc and persist Deputy's projection."""
    resolve_java_type_references(conn, records, branch=branch)
    python_results, context = _resolve_python_inherits(conn, records, branch)

    class_records = [
        r for r in records if r["type"] == "CLASS" and r.get("language") == "python"
    ]
    for record in class_records:
        meta = json.loads(record["metadata_json"])
        parent_classes = meta.get("parent_classes", [])
        class_entity_id = record["id"]
        delete_class_bases_by_class(conn, class_entity_id)
        if branch is not None:
            pin_names = {
                pin_name
                for parent_class in parent_classes
                for pin_name in (
                    parent_class,
                    _normalize_python_base_name(parent_class),
                )
            }
            if pin_names:
                placeholders = ",".join("?" for _ in pin_names)
                conn.execute(
                    f"""DELETE FROM inheritance_pins
                        WHERE class_entity_id = ? AND branch_name = ?
                        AND base_name NOT IN ({placeholders})""",
                    (class_entity_id, branch, *pin_names),
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

        result = python_results.get(class_entity_id)
        result_bases = result.bases if result is not None else ()
        if result is None:
            logger.warning(
                "Python MRO resolver unavailable for %s", record["full_path"]
            )

        resolved_bases = []
        resolved_bases_meta = []
        for index, base_name in enumerate(parent_classes):
            base = result_bases[index] if index < len(result_bases) else None
            if base is None:
                base_full_path, base_entity_id, is_resolved, branch_info = (
                    base_name,
                    None,
                    False,
                    None,
                )
            else:
                base_full_path, base_entity_id, is_resolved, branch_info = (
                    _python_semantic_base_info(context, base)
                )
            entry = {
                "base_full_path": base_full_path,
                "base_entity_id": base_entity_id,
                "is_resolved": is_resolved,
                "branch_info": branch_info,
                "relation_kind": "inherits",
            }
            resolved_bases.append(entry)
            resolved_bases_meta.append(
                {
                    "name": base_name,
                    "full_path": base_full_path if is_resolved else None,
                    "entity_id": base_entity_id if is_resolved else None,
                    "is_resolved": is_resolved,
                    "relation_kind": "inherits",
                }
            )

        upsert_class_bases(conn, class_entity_id, resolved_bases)
        meta["resolved_bases"] = resolved_bases_meta
        record["metadata_json"] = json.dumps(meta, default=str)

    return python_results


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
    semantic_members: dict[str, PythonInheritedMembersResult] | None = None,
) -> list[dict]:
    """Pass 1: Walk MRO for each CLASS and create synthetic aliases for inherited methods, properties, inner types."""
    created: list[dict] = []

    for record in records:
        if record["type"] != "CLASS":
            continue

        class_fqn = record["full_path"]
        class_entity_id = record["id"]

        semantic_result = (semantic_members or {}).get(class_entity_id)
        if record.get("language") == "python" and semantic_result is not None:
            if len(semantic_result.mro_ids) < 2:
                continue
            for member in semantic_result.members:
                target = get_entity_by_id(conn, member.member_id)
                owner = get_entity_by_id(conn, member.owner_id)
                if not target or not owner:
                    continue
                syn = _create_synthetic_entity(
                    class_fqn,
                    target,
                    owner["full_path"],
                    member.mro_depth,
                    class_entity_id,
                    conn,
                )
                if syn:
                    created.append(syn)
            continue

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
            source_entities = _projection_entities_by_path(conn, source_fqn, branch)
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
        "language": target.get("language", "python"),
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
    semantic_mros: dict[str, list[str] | None] | None = None,
) -> list[dict]:
    """Pass 2: Walk inner class MRO chains to create synthetic aliases for members accessed through inherited inner classes."""
    created: list[dict] = []

    for record in records:
        if record["type"] != "CLASS":
            continue

        class_fqn = record["full_path"]
        class_entity_id = record["id"]
        known_semantic_mros = semantic_mros or {}
        if (
            record.get("language") == "python"
            and class_entity_id in known_semantic_mros
        ):
            mro = known_semantic_mros[class_entity_id]
            if mro is None:
                continue
        else:
            mro = _compute_partial_class_mro(conn, class_entity_id)
        if mro is None or len(mro) < 2:
            continue

        own_meta = {}
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            own_meta = json.loads(record["metadata_json"])

        own_inner_type_ids = own_meta.get("inner_type_ids", [])

        seen_member_full_paths: set[str] = set()

        for mro_idx, source_fqn in enumerate(mro[1:], 1):
            source_entities = _projection_entities_by_path(conn, source_fqn, branch)
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

                inner_entities = _projection_entities_by_path(
                    conn, inner_class_fqn, branch
                )
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
    python_mro_results: dict[str, PythonClassMROResult] | None = None,
) -> None:
    """Clean all inherited members and recreate them via Pass 1 (direct MRO) and Pass 2 (inner class MRO)."""
    if not branch:
        import os

        branch = os.environ.get("DEPUTY_BRANCH", "default")

    semantic_records = records
    if records is None:
        if branch:
            semantic_records = get_branch_entities(conn, branch)
            records = [
                entity for entity in semantic_records if entity["type"] == "CLASS"
            ]
        else:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = 'CLASS'"
            ).fetchall()
            records = [dict(r) for r in rows]

    if semantic_records:
        semantic_records = _python_semantic_records(
            conn, semantic_records, branch=branch
        )

    semantic_members: dict[str, PythonInheritedMembersResult] = {}
    semantic_mros: dict[str, list[str] | None] = {}
    if (
        python_mro_results is None
        and semantic_records
        and any(record["type"] == "PYTHON_MODULE" for record in semantic_records)
    ):
        python_mro_results, _ = _resolve_python_inherits(conn, semantic_records, branch)
    if python_mro_results and semantic_records:
        context = build_context_from_records(semantic_records)
        resolver = context.get_resolver("python")
        get_inherited = getattr(resolver, "get_inherited_members", None)
        for class_id, mro_result in python_mro_results.items():
            if get_inherited is not None:
                semantic_members[class_id] = get_inherited(
                    class_id, context, mro_result=mro_result
                )
            if len(mro_result.mro_ids) < 2:
                semantic_mros[class_id] = None
            else:
                semantic_mros[class_id] = [
                    getattr(context.entity_registry.get(entity_id), "fqn", "")
                    for entity_id in mro_result.mro_ids
                ]

    clean_inherited_member_entities(
        conn, branch=branch, class_entity_ids=[r["id"] for r in records]
    )

    pass1 = _create_inherited_base_aliases(
        conn, records, branch, semantic_members=semantic_members
    )
    logger.debug("eager resolution pass 1: %d synthetic entities created", len(pass1))

    pass2 = _create_inherited_inner_class_aliases(
        conn, records, branch, semantic_mros=semantic_mros
    )
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


def _resolve_deproc_python_mro(
    conn: sqlite3.Connection,
    class_entity_id: str,
) -> tuple[bool, list[str] | None]:
    branch = get_current_branch()
    records = get_branch_entities(conn, branch)
    class_record = next(
        (
            record
            for record in records
            if record["id"] == class_entity_id
            and record["type"] == "CLASS"
            and record.get("language") == "python"
        ),
        None,
    )
    if class_record is None:
        return False, None

    adapter = DeprocResolutionAdapter(conn, branch)
    result = adapter.resolve_python_class_mro(class_entity_id)
    if result.status is not ResolutionStatus.RESOLVED:
        return True, None
    mro = []
    for entity_id in result.mro_ids:
        entity = adapter.context.entity_registry.get(entity_id)
        fqn = getattr(entity, "fqn", None)
        if not fqn:
            return True, None
        mro.append(fqn)
    return True, mro


def compute_class_mro(
    conn: sqlite3.Connection,
    class_entity_id: str,
    memo: dict[str, list[str] | None] | None = None,
    _visiting: set[str] | None = None,
) -> list[str] | None:
    if memo is not None and class_entity_id in memo:
        return memo[class_entity_id]

    semantic_available, semantic_mro = _resolve_deproc_python_mro(conn, class_entity_id)
    if semantic_available:
        if memo is not None:
            memo[class_entity_id] = semantic_mro
        return semantic_mro

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


def _deproc_python_inherited_members(
    conn: sqlite3.Connection,
    class_entity_id: str,
) -> tuple[bool, dict[str, list[dict]] | None]:
    branch = get_current_branch()
    records = get_branch_entities(conn, branch)
    class_record = next(
        (
            record
            for record in records
            if record["id"] == class_entity_id
            and record["type"] == "CLASS"
            and record.get("language") == "python"
        ),
        None,
    )
    if class_record is None:
        return False, None

    adapter = DeprocResolutionAdapter(conn, branch)
    resolver = adapter.context.get_resolver("python")
    if resolver is None or not callable(
        getattr(resolver, "get_inherited_members", None)
    ):
        return False, None

    semantic_result = adapter.get_python_inherited_members(class_entity_id)
    inherited: dict[str, list[dict]] = {}
    for member in semantic_result.members:
        target = adapter.records.get(member.member_id)
        owner = adapter.records.get(member.owner_id)
        if target is None or owner is None:
            continue
        display_type = target.get("type")
        if display_type not in {"METHOD", "PROPERTY", "INNER_TYPE"}:
            continue
        entry = dict(target)
        entry["_inherited_from"] = owner.get("full_path", "")
        entry["_mro_index"] = member.mro_depth
        inherited.setdefault(display_type, []).append(entry)
    return True, inherited


def get_inherited_members(
    conn: sqlite3.Connection,
    class_entity_id: str,
    mro_fqns: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Collect inherited methods, properties, and inner types from the MRO, deduped by name."""
    semantic_available, semantic_members = _deproc_python_inherited_members(
        conn, class_entity_id
    )
    if semantic_available:
        return semantic_members or {}

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
            status = None
            reason = None
            bi = base.get("branch_info")
            if bi:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    branch_info = json.loads(bi)
                    if isinstance(branch_info, dict):
                        status = branch_info.get("status")
                        reason = branch_info.get("reason")
                        candidates = branch_info.get("candidates", [])
                    elif isinstance(branch_info, list):
                        candidates = branch_info
            unresolved_entry = {
                "base_full_path": base["base_full_path"],
                "candidates": candidates,
            }
            if status is not None:
                unresolved_entry["status"] = status
            if reason is not None:
                unresolved_entry["reason"] = reason
            unresolved.append(unresolved_entry)

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
