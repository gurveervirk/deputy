import re
import sqlite3
from pathlib import Path

def open_database(db_path: str) -> sqlite3.Connection:
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
    return {row["filepath"]: (row["content_hash"], row["last_modified"]) for row in rows}

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
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO entities (id, language, full_path, name, type, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, language, full_path, name, type, metadata_json),
    )

def delete_entity_by_module_fqn(conn: sqlite3.Connection, module_fqn: str) -> None:
    conn.execute(
        "DELETE FROM entities WHERE full_path = ? OR full_path LIKE ?",
        (module_fqn, f"{module_fqn}.%"),
    )

def search_entities(
    conn: sqlite3.Connection, pattern: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM entities WHERE full_path REGEXP ? OR name REGEXP ? ORDER BY full_path, name",
        (pattern, pattern),
    ).fetchall()
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

def get_entity_ids_by_fqn(
    conn: sqlite3.Connection, fqn: str
) -> set[str]:
    rows = conn.execute(
        "SELECT id FROM entities WHERE full_path = ?",
        (fqn,),
    ).fetchall()
    return {row["id"] for row in rows}

def get_entity_by_id(
    conn: sqlite3.Connection, entity_id: str
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)

def get_entities_by_ids(
    conn: sqlite3.Connection, entity_ids: set[str]
) -> list[dict]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"SELECT * FROM entities WHERE id IN ({placeholders})",
        tuple(entity_ids),
    ).fetchall()
    return [dict(row) for row in rows]

def get_entities_by_path(
    conn: sqlite3.Connection, full_path: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM entities WHERE full_path = ?",
        (full_path,),
    ).fetchall()
    return [dict(row) for row in rows]

def get_entity_by_path(
    conn: sqlite3.Connection, full_path: str
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM entities WHERE full_path = ? LIMIT 1",
        (full_path,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)
