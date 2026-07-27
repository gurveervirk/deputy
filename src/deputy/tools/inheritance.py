import json
import sqlite3
from deputy.database.sqlite import (
    get_entity_ids_by_fqn,
    get_entity_by_id,
    get_entities_by_ids,
    delete_class_bases_by_class,
    upsert_class_bases,
    get_direct_bases,
    get_inheritance_pin,
)
from deputy.logger import get_logger
from deputy.utils.git import get_current_branch
from deputy.tools.utils import (
    get_parent_id,
    get_containing_module_fqn,
)

logger = get_logger("tools.inheritance")

def resolve_base_name_in_module(
    conn: sqlite3.Connection,
    base_name: str,
    module_fqn: str,
    class_lineno: int | None = None,
) -> list[dict]:
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
            if class_lineno is not None and ent_lineno is not None and ent_lineno >= class_lineno:
                continue
            # Classify scope: walk parent chain to check for ControlFlowBlock
            scope = _classify_candidate_scope(conn, ent)
            candidates.append({
                "entity": ent,
                "full_path": ent["full_path"],
                "entity_id": ent["id"],
                "lineno": ent_lineno,
                "scope": scope,
                "kind": "IMPORT_ALIAS",
            })

    # Find direct CLASS definitions in the same module with the same name
    class_ids = get_entity_ids_by_fqn(conn, module_fqn)
    all_class_entities = get_entities_by_ids(conn, class_ids)
    for ent in all_class_entities:
        if ent["type"] == "CLASS" and ent["name"] == base_name:
            meta = json.loads(ent["metadata_json"])
            ent_lineno = meta.get("lineno")
            if class_lineno is not None and ent_lineno is not None and ent_lineno >= class_lineno:
                continue
            scope = _classify_candidate_scope(conn, ent)
            candidates.append({
                "entity": ent,
                "full_path": ent["full_path"],
                "entity_id": ent["id"],
                "lineno": ent_lineno,
                "scope": scope,
                "kind": "CLASS",
            })

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
            meta = json.loads(current["metadata_json"])
            return f"conditional:{current.get('name', '')}"
        if current["type"] in ("MODULE", "PACKAGE", "NAMESPACE_PACKAGE"):
            return "module_level"
        current_id = get_parent_id(current)
    return "module_level"

def pick_closest_module_level_candidate(candidates: list[dict]) -> dict | None:
    module_level = [c for c in candidates if c["scope"] == "module_level"]
    if not module_level:
        return None
    module_level.sort(key=lambda c: c["lineno"] if c["lineno"] is not None else -1, reverse=True)
    return module_level[0]

def has_multiple_candidates(candidates: list[dict]) -> bool:
    module_level = [c for c in candidates if c["scope"] == "module_level"]
    return len(module_level) > 1

def resolve_all_inherits(
    conn: sqlite3.Connection,
    records: list[dict],
) -> None:
    class_records = [r for r in records if r["type"] == "CLASS"]

    for record in class_records:
        meta = json.loads(record["metadata_json"])
        parent_classes = meta.get("parent_classes", [])
        if not parent_classes:
            continue

        class_entity_id = record["id"]
        class_lineno = meta.get("lineno")

        module_fqn = get_containing_module_fqn(conn, class_entity_id)
        if not module_fqn:
            logger.warning("cannot determine module for class %s", record["full_path"])
            continue

        resolved_bases = []
        for base_name in parent_classes:
            candidates = resolve_base_name_in_module(conn, base_name, module_fqn, class_lineno)

            closest = pick_closest_module_level_candidate(candidates)

            if closest and not has_multiple_candidates(candidates) and len([c for c in candidates if c["scope"].startswith("module_level")]) == 1:
                resolved_bases.append({
                    "base_full_path": closest["full_path"],
                    "base_entity_id": closest["entity_id"],
                    "is_resolved": True,
                    "branch_info": None,
                })
            elif candidates:
                candidates_info = []
                for c in candidates:
                    candidates_info.append({
                        "full_path": c["full_path"],
                        "entity_id": c["entity_id"],
                        "lineno": c["lineno"],
                        "scope": c["scope"],
                        "kind": c["kind"],
                    })
                resolved_bases.append({
                    "base_full_path": base_name,
                    "base_entity_id": None,
                    "is_resolved": False,
                    "branch_info": json.dumps(candidates_info),
                })
                logger.info(
                    "unresolved base %s for %s: %d candidates (conditional)",
                    base_name, record["full_path"], len(candidates),
                )
            else:
                resolved_bases.append({
                    "base_full_path": base_name,
                    "base_entity_id": None,
                    "is_resolved": False,
                    "branch_info": None,
                })
                logger.info("unresolved base %s for %s: no candidates found", base_name, record["full_path"])

        delete_class_bases_by_class(conn, class_entity_id)
        upsert_class_bases(conn, class_entity_id, resolved_bases)

        resolved_bases_meta = []
        for i, base_name in enumerate(parent_classes):
            entry = resolved_bases[i] if i < len(resolved_bases) else None
            if entry:
                resolved_bases_meta.append({
                    "name": base_name,
                    "full_path": entry["base_full_path"] if entry["is_resolved"] else None,
                    "entity_id": entry["base_entity_id"] if entry["is_resolved"] else None,
                    "is_resolved": entry["is_resolved"],
                })
            else:
                resolved_bases_meta.append({
                    "name": base_name,
                    "full_path": None,
                    "entity_id": None,
                    "is_resolved": False,
                })
        meta["resolved_bases"] = resolved_bases_meta
        record["metadata_json"] = json.dumps(meta, default=str)

def c3_merge(seqs: list[list[str]]) -> list[str]:
    """Standard C3 linearization merge.

    Raises ValueError on inconsistent hierarchy.
    """
    result = []
    while True:
        nonempty = [s for s in seqs if s]
        if not nonempty:
            return result
        for seq in nonempty:
            candidate = seq[0]
            if not any(candidate in s[1:] for s in nonempty):
                result.append(candidate)
                for s in nonempty:
                    if s and s[0] == candidate:
                        s.pop(0)
                break
        else:
            raise ValueError(f"Inconsistent MRO hierarchy: cannot merge {seqs}")

def compute_class_mro(
    conn: sqlite3.Connection,
    class_entity_id: str,
    memo: dict[str, list[str] | None] | None = None,
    _visiting: set[str] | None = None,
) -> list[str] | None:
    if memo is None:
        memo = {}
    if _visiting is None:
        _visiting = set()

    if class_entity_id in memo:
        return memo[class_entity_id]

    if class_entity_id in _visiting:
        logger.warning("cycle detected in MRO for entity %s", class_entity_id)
        memo[class_entity_id] = None
        return None

    entity = get_entity_by_id(conn, class_entity_id)
    if not entity or entity["type"] != "CLASS":
        return None

    _visiting.add(class_entity_id)

    meta = {}
    try:
        meta = json.loads(entity["metadata_json"])
    except (json.JSONDecodeError, TypeError):
        pass
    parent_classes = meta.get("parent_classes", [])
    if not parent_classes:
        result = [entity["full_path"]]
        memo[class_entity_id] = result
        _visiting.discard(class_entity_id)
        return result

    branch = get_current_branch()
    direct_bases = get_direct_bases(conn, class_entity_id)

    resolved_directs: list[dict] = []
    unresolved: list[dict] = []

    for base in direct_bases:
        if base.get("is_resolved"):
            resolved_directs.append(base)
        else:
            base_name = base["base_full_path"]
            pin = get_inheritance_pin(conn, class_entity_id, base_name, branch) if branch else None
            if pin:
                resolved_directs.append({
                    "base_full_path": base["base_full_path"],
                    "base_entity_id": pin["pinned_entity_id"],
                    "is_resolved": True,
                    "branch_info": None,
                })
            else:
                unresolved.append(base)

    if unresolved:
        memo[class_entity_id] = None
        _visiting.discard(class_entity_id)
        return None

    if not resolved_directs:
        result = [entity["full_path"]]
        memo[class_entity_id] = result
        _visiting.discard(class_entity_id)
        return result

    base_mros = []
    for base in resolved_directs:
        base_mro = compute_class_mro(conn, base["base_entity_id"], memo, _visiting)
        if base_mro is None:
            memo[class_entity_id] = None
            _visiting.discard(class_entity_id)
            return None
        base_mros.append(base_mro)

    merge_lists = list(base_mros) + [[b["base_full_path"] for b in resolved_directs]]

    try:
        class_fqn = entity["full_path"]
        result = [class_fqn] + c3_merge(merge_lists)
    except ValueError:
        logger.warning("inconsistent MRO for %s", entity["full_path"])
        memo[class_entity_id] = None
        _visiting.discard(class_entity_id)
        return None

    memo[class_entity_id] = result
    _visiting.discard(class_entity_id)
    return result

def get_inherited_members(
    conn: sqlite3.Connection,
    class_entity_id: str,
    mro_fqns: list[str] | None = None,
) -> dict[str, list[dict]]:
    from deputy.database.sqlite import get_entities_by_path, get_entity_by_id

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

    self_fqn = mro[0]
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
                    if member["full_path"] not in seen_names and member.get("name"):
                        seen_names.add(member["full_path"])
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
    mro = compute_class_mro(conn, class_entity_id)
    direct_bases = get_direct_bases(conn, class_entity_id)

    resolved = []
    unresolved = []
    for base in direct_bases:
        if base.get("is_resolved"):
            resolved.append({
                "base_full_path": base["base_full_path"],
                "base_entity_id": base["base_entity_id"],
            })
        else:
            candidates = []
            bi = base.get("branch_info")
            if bi:
                try:
                    candidates = json.loads(bi)
                except (json.JSONDecodeError, TypeError):
                    pass
            unresolved.append({
                "base_full_path": base["base_full_path"],
                "candidates": candidates,
            })

    inherited_members = get_inherited_members(conn, class_entity_id, mro)

    return {
        "mro": mro,
        "resolved_bases": resolved,
        "unresolved_bases": unresolved,
        "inherited_members": inherited_members,
    }
