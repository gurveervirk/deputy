import json
import re
import sqlite3
from pathlib import Path

from deputy.logger import get_logger

logger = get_logger("database.sqlite")

INHERITANCE_ENTITY_TYPES = ("CLASS", "INTERFACE", "ENUM", "RECORD")
SUBCLASS_RELATION_KINDS = ("inherits", "extends")
JAVA_RELATION_KINDS = {"extends", "implements", "interface_extends"}


def open_database(db_path: str) -> sqlite3.Connection:
    logger.debug("opening database: %s", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("REGEXP", 2, _regexp)
    return conn


def _regexp(pattern: str, value: str) -> bool:
    return re.search(pattern, value) is not None


def init_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    conn.executescript(schema_path.read_text())
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(class_bases)").fetchall()
    }
    if "relation_kind" not in columns:
        conn.execute(
            "ALTER TABLE class_bases ADD COLUMN relation_kind TEXT NOT NULL DEFAULT 'inherits'"
        )
    _backfill_java_relation_kinds(conn)
    _rebuild_missing_java_relations(conn)


def _java_relation_kinds(entity: sqlite3.Row) -> dict[str, str]:
    try:
        metadata = json.loads(entity["metadata_json"])
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    relation_kinds: dict[str, str] = {}
    if entity["type"] == "INTERFACE":
        relation_kinds.update(
            dict.fromkeys(metadata.get("extends_interfaces", []), "interface_extends")
        )
    else:
        superclass = metadata.get("superclass")
        if superclass:
            relation_kinds[superclass] = "extends"
        relation_kinds.update(
            dict.fromkeys(metadata.get("implements", []), "implements")
        )

    for base in metadata.get("resolved_bases", []):
        if not isinstance(base, dict):
            continue
        name = base.get("name")
        full_path = base.get("full_path")
        relation_kind = relation_kinds.get(name) if isinstance(name, str) else None
        if relation_kind is None:
            relation_kind = base.get("relation_kind")
        if relation_kind not in {"extends", "implements", "interface_extends"}:
            continue
        if isinstance(name, str):
            relation_kinds[name] = relation_kind
        if isinstance(full_path, str):
            relation_kinds[full_path] = relation_kind
    return relation_kinds


def _java_relation_kind(
    entity_type: str,
    relation_kind: str | None,
    target_type: str | None,
) -> str:
    if relation_kind in JAVA_RELATION_KINDS:
        return relation_kind
    if entity_type == "INTERFACE":
        return "interface_extends"
    if entity_type in ("ENUM", "RECORD"):
        return "implements"
    if target_type == "INTERFACE":
        return "implements"
    if target_type in ("CLASS", "ENUM", "RECORD"):
        return "extends"
    return "inherits"


def _java_relation_rows(
    entity: sqlite3.Row,
    entities_by_fqn: dict[str, list[sqlite3.Row]],
    entities_by_name: dict[str, list[sqlite3.Row]],
    entities_by_id: dict[str, sqlite3.Row],
) -> list[dict]:
    try:
        metadata = json.loads(entity["metadata_json"])
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    relation_kinds = _java_relation_kinds(entity)
    resolved_bases = metadata.get("resolved_bases", [])
    if not isinstance(resolved_bases, list):
        resolved_bases = []

    base_specs: list[tuple[str, str | None, bool, str | None]] = []
    if resolved_bases:
        for base in resolved_bases:
            if not isinstance(base, dict):
                continue
            name_value = base.get("name")
            full_path_value = base.get("full_path")
            if isinstance(name_value, str):
                name = name_value
            elif isinstance(full_path_value, str):
                name = full_path_value
            else:
                continue
            full_path = full_path_value if isinstance(full_path_value, str) else None
            is_resolved = bool(base.get("is_resolved")) and isinstance(full_path, str)
            reference = full_path if full_path is not None else name
            target = None
            target_id = base.get("entity_id")
            if is_resolved:
                if isinstance(target_id, str):
                    target = entities_by_id.get(target_id)
                if target is None:
                    candidates = entities_by_fqn.get(reference, [])
                    if len(candidates) == 1:
                        target = candidates[0]
                if target is None and "." not in reference:
                    package = entity["full_path"].rsplit(".", 1)[0]
                    candidates = entities_by_fqn.get(f"{package}.{reference}", [])
                    if len(candidates) == 1:
                        target = candidates[0]
                if target is None:
                    candidates = entities_by_name.get(reference, [])
                    if len(candidates) == 1:
                        target = candidates[0]
                target_id = target["id"] if target is not None else None
                if target is None:
                    is_resolved = False
            else:
                target_id = None
            relation_kind = relation_kinds.get(name)
            if relation_kind is None and isinstance(full_path, str):
                relation_kind = relation_kinds.get(full_path)
            if relation_kind is None:
                relation_kind = base.get("relation_kind")
            target_type = target["type"] if target is not None else None
            relation_kind = _java_relation_kind(
                entity["type"], relation_kind, target_type
            )
            base_full_path = (
                target["full_path"] if is_resolved and target is not None else name
            )
            base_specs.append((base_full_path, target_id, is_resolved, relation_kind))

    if not base_specs:
        if entity["type"] == "INTERFACE":
            names = metadata.get("extends_interfaces", [])
            named_relations = (
                [(name, "interface_extends") for name in names if isinstance(name, str)]
                if isinstance(names, list)
                else []
            )
        else:
            named_relations = []
            superclass = metadata.get("superclass")
            if isinstance(superclass, str) and superclass:
                named_relations.append((superclass, "extends"))
            implements = metadata.get("implements", [])
            if isinstance(implements, list):
                named_relations.extend(
                    (name, "implements") for name in implements if isinstance(name, str)
                )

        for name, relation_kind in named_relations:
            target = None
            candidates = entities_by_fqn.get(name, [])
            if len(candidates) == 1:
                target = candidates[0]
            elif "." not in name:
                package = entity["full_path"].rsplit(".", 1)[0]
                candidates = entities_by_fqn.get(f"{package}.{name}", [])
                if len(candidates) == 1:
                    target = candidates[0]
                else:
                    candidates = entities_by_name.get(name, [])
                    if len(candidates) == 1:
                        target = candidates[0]
            if target is None:
                base_specs.append(
                    (
                        name,
                        None,
                        False,
                        _java_relation_kind(entity["type"], relation_kind, None),
                    )
                )
            else:
                base_specs.append(
                    (
                        target["full_path"],
                        target["id"],
                        True,
                        _java_relation_kind(
                            entity["type"], relation_kind, target["type"]
                        ),
                    )
                )

    return [
        {
            "base_full_path": base_full_path,
            "base_entity_id": target_id,
            "is_resolved": is_resolved,
            "branch_info": None,
            "relation_kind": relation_kind,
        }
        for base_full_path, target_id, is_resolved, relation_kind in base_specs
    ]


def _rebuild_missing_java_relations(conn: sqlite3.Connection) -> None:
    entities = conn.execute(
        """SELECT DISTINCT e.*
           FROM entities e
           JOIN branch_entities be ON be.entity_id = e.id
           WHERE e.language = 'java'
           AND e.type IN (?, ?, ?, ?)""",
        INHERITANCE_ENTITY_TYPES,
    ).fetchall()
    if not entities:
        return

    type_entities = conn.execute(
        """SELECT * FROM entities
           WHERE language = 'java'
           AND type IN (?, ?, ?, ?)""",
        INHERITANCE_ENTITY_TYPES,
    ).fetchall()
    entities_by_fqn: dict[str, list[sqlite3.Row]] = {}
    entities_by_name: dict[str, list[sqlite3.Row]] = {}
    entities_by_id = {entity["id"]: entity for entity in type_entities}
    for type_entity in type_entities:
        entities_by_fqn.setdefault(type_entity["full_path"], []).append(type_entity)
        entities_by_name.setdefault(type_entity["name"], []).append(type_entity)

    existing = {
        (row["class_entity_id"], row["base_full_path"])
        for row in conn.execute(
            "SELECT class_entity_id, base_full_path FROM class_bases"
        ).fetchall()
    }
    for entity in entities:
        for relation in _java_relation_rows(
            entity, entities_by_fqn, entities_by_name, entities_by_id
        ):
            key = (entity["id"], relation["base_full_path"])
            if key in existing:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO class_bases
                   (class_entity_id, base_full_path, base_entity_id, is_resolved,
                    branch_info, relation_kind)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entity["id"],
                    relation["base_full_path"],
                    relation["base_entity_id"],
                    1 if relation["is_resolved"] else 0,
                    relation["branch_info"],
                    relation["relation_kind"],
                ),
            )
            existing.add(key)


def _backfill_java_relation_kinds(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT cb.class_entity_id, cb.base_full_path, cb.base_entity_id,
                  e.type, e.metadata_json
           FROM class_bases cb
           JOIN entities e ON e.id = cb.class_entity_id
           WHERE e.language = 'java' AND cb.relation_kind = 'inherits'"""
    ).fetchall()
    if not rows:
        return

    entity_types = {
        row["id"]: row["type"]
        for row in conn.execute(
            "SELECT id, type FROM entities WHERE type IN (?, ?, ?, ?)",
            INHERITANCE_ENTITY_TYPES,
        ).fetchall()
    }

    for row in rows:
        relation_kind = _java_relation_kinds(row).get(row["base_full_path"])
        if relation_kind is None:
            if row["type"] == "INTERFACE":
                relation_kind = "interface_extends"
            elif row["type"] in ("ENUM", "RECORD"):
                relation_kind = "implements"
            elif row["type"] == "CLASS":
                target_type = entity_types.get(row["base_entity_id"])
                if target_type == "INTERFACE":
                    relation_kind = "implements"
                elif target_type in ("CLASS", "ENUM", "RECORD"):
                    relation_kind = "extends"

        if relation_kind is not None:
            conn.execute(
                """UPDATE class_bases
                   SET relation_kind = ?
                   WHERE class_entity_id = ?
                   AND base_full_path = ?
                   AND relation_kind = 'inherits'""",
                (relation_kind, row["class_entity_id"], row["base_full_path"]),
            )


def get_branch_files(
    conn: sqlite3.Connection, branch_name: str
) -> dict[str, tuple[str, float]]:
    rows = conn.execute(
        "SELECT filepath, content_hash, last_modified FROM branch_files WHERE branch_name = ?",
        (branch_name,),
    ).fetchall()
    return {
        row["filepath"]: (row["content_hash"], row["last_modified"]) for row in rows
    }


def content_hash_exists(conn: sqlite3.Connection, content_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM branch_files WHERE content_hash = ? LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row is not None


def upsert_branch_file(
    conn: sqlite3.Connection,
    branch_name: str,
    filepath: str,
    content_hash: str,
    last_modified: float,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO branch_files (branch_name, filepath, content_hash, last_modified)
           VALUES (?, ?, ?, ?)""",
        (branch_name, filepath, content_hash, last_modified),
    )


def delete_branch_file(
    conn: sqlite3.Connection,
    branch_name: str,
    filepath: str,
) -> None:
    conn.execute(
        "DELETE FROM branch_files WHERE branch_name = ? AND filepath = ?",
        (branch_name, filepath),
    )


def update_mtime(
    conn: sqlite3.Connection,
    branch_name: str,
    filepath: str,
    last_modified: float,
) -> None:
    conn.execute(
        "UPDATE branch_files SET last_modified = ? WHERE branch_name = ? AND filepath = ?",
        (last_modified, branch_name, filepath),
    )


def upsert_entity(
    conn: sqlite3.Connection,
    id: str,
    language: str,
    full_path: str,
    name: str,
    type: str,
    metadata_json: str,
    parent_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO entities (id, language, full_path, name, type, metadata_json, parent_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (id, language, full_path, name, type, metadata_json, parent_id),
    )


def delete_entity_by_module_fqn(conn: sqlite3.Connection, module_fqn: str) -> None:
    conn.execute(
        "DELETE FROM entities WHERE full_path = ? OR full_path LIKE ?",
        (module_fqn, f"{module_fqn}.%"),
    )
    clean_stale_inheritance_rows(conn)


def search_entities(
    conn: sqlite3.Connection,
    pattern: str,
    branch_name: str | None = None,
    type_filter: list[str] | None = None,
    language: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    exact: bool = False,
    name_only: bool = False,
) -> list[dict]:
    parts: list[str] = []
    params: list = []

    if branch_name:
        parts.append("be.branch_name = ?")
        params.append(branch_name)

    col = "e." if branch_name else ""
    if exact:
        parts.append(f"{col}full_path = ?")
        params.append(pattern)
    elif name_only:
        parts.append(f"{col}name REGEXP ?")
        params.append(pattern)
    else:
        parts.append(f"({col}full_path REGEXP ? OR {col}name REGEXP ?)")
        params.extend([pattern, pattern])

    parts.append(
        f"{col}type NOT IN ('IMPORT_STATEMENT', 'IMPORT', 'CONTROL_FLOW_BLOCK', 'CONTROL_FLOW_GROUP')"
    )

    if type_filter:
        placeholders = ",".join("?" for _ in type_filter)
        parts.append(f"{col}type IN ({placeholders})")
        params.extend(type_filter)

    if language:
        parts.append(f"{col}language = ?")
        params.append(language)

    where = " AND ".join(parts)

    if branch_name:
        sql = f"""SELECT e.* FROM entities e
                  JOIN branch_entities be ON e.id = be.entity_id
                  WHERE {where}
                  ORDER BY e.full_path, e.name"""
    else:
        sql = f"SELECT * FROM entities WHERE {where} ORDER BY full_path, name"

    if limit is not None or offset:
        limit_val = limit if limit is not None else -1
        sql += " LIMIT ?"
        params.append(limit_val)
    if offset:
        sql += " OFFSET ?"
        params.append(offset)

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
        (key, value),
    )


def get_config(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM config WHERE key = ?",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def get_entity_ids_by_fqn(conn: sqlite3.Connection, fqn: str) -> set[str]:
    rows = conn.execute(
        "SELECT id FROM entities WHERE full_path = ?",
        (fqn,),
    ).fetchall()
    return {row["id"] for row in rows}


def get_entity_by_id(conn: sqlite3.Connection, entity_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_entities_by_ids(conn: sqlite3.Connection, entity_ids: set[str]) -> list[dict]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"SELECT * FROM entities WHERE id IN ({placeholders})",
        tuple(entity_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def get_branch_entities(conn: sqlite3.Connection, branch_name: str) -> list[dict]:
    rows = conn.execute(
        """SELECT e.* FROM entities e
           JOIN branch_entities be ON e.id = be.entity_id
           WHERE be.branch_name = ?
           ORDER BY e.id""",
        (branch_name,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_entities_by_path(
    conn: sqlite3.Connection, full_path: str, branch_name: str | None = None
) -> list[dict]:
    if branch_name:
        rows = conn.execute(
            """SELECT e.* FROM entities e
               JOIN branch_entities be ON e.id = be.entity_id
               WHERE be.branch_name = ? AND e.full_path = ?
               ORDER BY json_extract(e.metadata_json, '$.lineno')""",
            (branch_name, full_path),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities WHERE full_path = ? ORDER BY json_extract(metadata_json, '$.lineno')",
            (full_path,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_entity_by_path(
    conn: sqlite3.Connection, full_path: str, branch_name: str | None = None
) -> dict | None:
    if branch_name:
        row = conn.execute(
            """SELECT e.* FROM entities e
               JOIN branch_entities be ON e.id = be.entity_id
               WHERE be.branch_name = ? AND e.full_path = ?
               LIMIT 1""",
            (branch_name, full_path),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM entities WHERE full_path = ? LIMIT 1",
            (full_path,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_filtered_entities_by_path(
    conn: sqlite3.Connection,
    full_path: str,
    branch_name: str | None = None,
    type_filter: str | None = None,
    lineno: int | None = None,
) -> list[dict]:
    if branch_name:
        sql = """SELECT e.* FROM entities e
                 JOIN branch_entities be ON e.id = be.entity_id
                 WHERE be.branch_name = ? AND e.full_path = ?"""
        params: list = [branch_name, full_path]
    else:
        sql = "SELECT * FROM entities WHERE full_path = ?"
        params = [full_path]
    if type_filter:
        sql += " AND e.type = ?" if branch_name else " AND type = ?"
        params.append(type_filter)
    if lineno is not None:
        sql += " AND json_extract(metadata_json, '$.lineno') = ?"
        params.append(lineno)
    sql += f" ORDER BY json_extract({'e.' if branch_name else ''}metadata_json, '$.lineno')"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def delete_entities_by_package(conn: sqlite3.Connection, package_name: str) -> None:
    conn.execute(
        "DELETE FROM entities WHERE json_extract(metadata_json, '$.source') = 'dependency' AND json_extract(metadata_json, '$.package_name') = ?",
        (package_name,),
    )


def upsert_dependency(
    conn: sqlite3.Connection,
    package_name: str,
    version: str | None,
    install_path: str | None,
    package_path: str | None,
    source: str | None,
    metadata_json: str | None,
    last_modified: float | None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO dependencies (package_name, version, install_path, package_path, source, metadata_json, last_modified)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            package_name,
            version,
            install_path,
            package_path,
            source,
            metadata_json,
            last_modified,
        ),
    )


def get_dependency(conn: sqlite3.Connection, package_name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM dependencies WHERE package_name = ?",
        (package_name,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def delete_dependency(conn: sqlite3.Connection, package_name: str) -> None:
    conn.execute(
        "DELETE FROM dependencies WHERE package_name = ?",
        (package_name,),
    )


def list_dependencies(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM dependencies ORDER BY package_name").fetchall()
    return [dict(row) for row in rows]


def upsert_branch_entities(
    conn: sqlite3.Connection, branch_name: str, entity_ids: list[str]
) -> None:
    if not entity_ids:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO branch_entities (branch_name, entity_id) VALUES (?, ?)",
        [(branch_name, eid) for eid in entity_ids],
    )


def get_dependency_entity_ids(
    conn: sqlite3.Connection, branch_name: str, package_name: str
) -> list[str]:
    rows = conn.execute(
        """SELECT be.entity_id FROM branch_entities be
           JOIN entities e ON be.entity_id = e.id
           WHERE be.branch_name = ?
           AND json_extract(e.metadata_json, '$.source') = 'dependency'
           AND json_extract(e.metadata_json, '$.package_name') = ?""",
        (branch_name, package_name),
    ).fetchall()
    return [r["entity_id"] for r in rows]


def delete_branch_entities(conn: sqlite3.Connection, branch_name: str) -> None:
    conn.execute("DELETE FROM branch_entities WHERE branch_name = ?", (branch_name,))


def delete_branch_entities_by_entity_ids(
    conn: sqlite3.Connection, branch_name: str, entity_ids: list[str]
) -> None:
    if not entity_ids:
        return
    placeholders = ",".join("?" for _ in entity_ids)
    conn.execute(
        f"DELETE FROM branch_entities WHERE branch_name = ? AND entity_id IN ({placeholders})",
        (branch_name, *entity_ids),
    )


def upsert_class_bases(
    conn: sqlite3.Connection,
    class_entity_id: str,
    bases: list[dict],
) -> None:
    for base in bases:
        conn.execute(
            """INSERT OR REPLACE INTO class_bases
               (class_entity_id, base_full_path, base_entity_id, is_resolved, branch_info, relation_kind)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                class_entity_id,
                base["base_full_path"],
                base.get("base_entity_id"),
                1 if base.get("is_resolved") else 0,
                base.get("branch_info"),
                base.get("relation_kind", "inherits"),
            ),
        )


def delete_class_bases_by_class(conn: sqlite3.Connection, class_entity_id: str) -> None:
    conn.execute(
        "DELETE FROM class_bases WHERE class_entity_id = ?",
        (class_entity_id,),
    )


def get_direct_bases(conn: sqlite3.Connection, class_entity_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM class_bases WHERE class_entity_id = ? ORDER BY rowid",
        (class_entity_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_direct_subclasses(
    conn: sqlite3.Connection, base_full_path: str, branch_name: str | None = None
) -> list[dict]:
    placeholders = ",".join("?" for _ in SUBCLASS_RELATION_KINDS)
    if branch_name:
        rows = conn.execute(
            f"""SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               JOIN branch_entities be ON e.id = be.entity_id
               WHERE cb.base_full_path = ? AND be.branch_name = ?
               AND cb.relation_kind IN ({placeholders})""",
            (base_full_path, branch_name, *SUBCLASS_RELATION_KINDS),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               WHERE cb.base_full_path = ?
               AND cb.relation_kind IN ({placeholders})""",
            (base_full_path, *SUBCLASS_RELATION_KINDS),
        ).fetchall()
    return [dict(row) for row in rows]


def get_direct_implementations(
    conn: sqlite3.Connection, interface_full_path: str, branch_name: str | None = None
) -> list[dict]:
    if branch_name:
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               JOIN branch_entities be ON e.id = be.entity_id
               WHERE cb.base_full_path = ?
               AND cb.relation_kind = 'implements'
               AND be.branch_name = ?""",
            (interface_full_path, branch_name),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               WHERE cb.base_full_path = ?
               AND cb.relation_kind = 'implements'""",
            (interface_full_path,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_direct_subinterfaces(
    conn: sqlite3.Connection, interface_full_path: str, branch_name: str | None = None
) -> list[dict]:
    if branch_name:
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               JOIN branch_entities be ON e.id = be.entity_id
               WHERE cb.base_full_path = ?
               AND cb.relation_kind = 'interface_extends'
               AND be.branch_name = ?""",
            (interface_full_path, branch_name),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               WHERE cb.base_full_path = ?
               AND cb.relation_kind = 'interface_extends'""",
            (interface_full_path,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_transitive_subclasses(
    conn: sqlite3.Connection, base_full_path: str, branch_name: str | None = None
) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    todo = [base_full_path]
    while todo:
        current = todo.pop()
        subs = get_direct_subclasses(conn, current, branch_name=branch_name)
        for sub in subs:
            if sub["full_path"] not in seen:
                seen.add(sub["full_path"])
                results.append(sub)
                todo.append(sub["full_path"])
    return results


def upsert_inheritance_pin(
    conn: sqlite3.Connection,
    class_entity_id: str,
    base_name: str,
    pinned_entity_id: str,
    branch_name: str,
) -> None:
    row = conn.execute(
        "SELECT type FROM entities WHERE id = ?", (pinned_entity_id,)
    ).fetchone()
    if row is None or row["type"] != "CLASS":
        raise ValueError("inheritance pins must target a CLASS entity")
    conn.execute(
        """INSERT OR REPLACE INTO inheritance_pins
           (class_entity_id, base_name, pinned_entity_id, branch_name)
           VALUES (?, ?, ?, ?)""",
        (class_entity_id, base_name, pinned_entity_id, branch_name),
    )


def get_inheritance_pin(
    conn: sqlite3.Connection,
    class_entity_id: str,
    base_name: str,
    branch_name: str,
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM inheritance_pins WHERE class_entity_id = ? AND base_name = ? AND branch_name = ?",
        (class_entity_id, base_name, branch_name),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def delete_inheritance_pin(
    conn: sqlite3.Connection,
    class_entity_id: str,
    base_name: str,
    branch_name: str,
) -> None:
    conn.execute(
        "DELETE FROM inheritance_pins WHERE class_entity_id = ? AND base_name = ? AND branch_name = ?",
        (class_entity_id, base_name, branch_name),
    )


def list_inheritance_pins(conn: sqlite3.Connection, branch_name: str) -> list[dict]:
    rows = conn.execute(
        """SELECT ip.*, e.full_path AS class_full_path, e.name AS class_name
           FROM inheritance_pins ip
           LEFT JOIN entities e ON e.id = ip.class_entity_id
           WHERE ip.branch_name = ?
           ORDER BY e.full_path""",
        (branch_name,),
    ).fetchall()
    return [dict(row) for row in rows]


def clean_orphan_entities(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM branch_entities)"
    )
    clean_stale_inheritance_rows(conn)


def clean_stale_inheritance_rows(
    conn: sqlite3.Connection, branch_name: str | None = None
) -> None:
    type_placeholders = ",".join("?" for _ in INHERITANCE_ENTITY_TYPES)
    conn.execute(
        f"""DELETE FROM class_bases
           WHERE class_entity_id NOT IN (
               SELECT id FROM entities WHERE type IN ({type_placeholders})
           )
           OR class_entity_id NOT IN (
               SELECT entity_id FROM branch_entities
           )
           OR (
               base_entity_id IS NOT NULL
               AND base_entity_id NOT IN (
                   SELECT id FROM entities WHERE type IN ({type_placeholders})
               )
           )
           OR (
               base_entity_id IS NOT NULL
               AND base_entity_id NOT IN (
                   SELECT entity_id FROM branch_entities
               )
           )""",
        (*INHERITANCE_ENTITY_TYPES, *INHERITANCE_ENTITY_TYPES),
    )
    conn.execute(
        """DELETE FROM inheritance_pins
           WHERE class_entity_id NOT IN (
               SELECT id FROM entities WHERE type = 'CLASS'
           )
           OR pinned_entity_id NOT IN (
               SELECT id FROM entities WHERE type = 'CLASS'
           )"""
    )
    if branch_name is not None:
        conn.execute(
            """DELETE FROM inheritance_pins
               WHERE branch_name = ?
               AND class_entity_id NOT IN (
                   SELECT entity_id FROM branch_entities WHERE branch_name = ?
               )""",
            (branch_name, branch_name),
        )
