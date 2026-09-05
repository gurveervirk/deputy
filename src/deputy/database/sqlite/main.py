import re
import sqlite3
from pathlib import Path

from deputy.logger import get_logger

logger = get_logger("database.sqlite")


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
               (class_entity_id, base_full_path, base_entity_id, is_resolved, branch_info)
               VALUES (?, ?, ?, ?, ?)""",
            (
                class_entity_id,
                base["base_full_path"],
                base.get("base_entity_id"),
                1 if base.get("is_resolved") else 0,
                base.get("branch_info"),
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
    if branch_name:
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               JOIN branch_entities be ON e.id = be.entity_id
               WHERE cb.base_full_path = ? AND be.branch_name = ?""",
            (base_full_path, branch_name),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN class_bases cb ON e.id = cb.class_entity_id
               WHERE cb.base_full_path = ?""",
            (base_full_path,),
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
