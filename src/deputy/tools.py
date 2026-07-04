import os
import sqlite3
from pathlib import Path
from deputy._version import __version__
from deputy.database.sqlite import (
    open_database,
    init_schema,
    set_config,
    search_entities as db_search_entities,
    get_entity_by_path,
)

_CONFIG_FILE = ".deputyconfig"
_DEFAULT_DB = ".deputy.db"

def _resolve_db_path() -> str:
    if os.path.exists(_CONFIG_FILE):
        path = Path(_CONFIG_FILE).read_text().strip()
        if path:
            return path
    if os.path.exists(_DEFAULT_DB):
        return _DEFAULT_DB
    raise FileNotFoundError(
        "No database found. Run 'deputy init' first."
    )

def _open_database() -> sqlite3.Connection:
    return open_database(_resolve_db_path())

def init_database(path: str) -> None:
    conn = open_database(path)
    init_schema(conn)
    set_config(conn, "base_path", os.getcwd())
    set_config(conn, "deputy_cli_version", __version__)
    conn.commit()
    conn.close()
    if path != _DEFAULT_DB:
        Path(_CONFIG_FILE).write_text(os.path.abspath(path))

def run_sync(force: bool) -> None:
    pass

def search_entities(pattern: str) -> list[dict]:
    conn = _open_database()
    results = db_search_entities(conn, pattern)
    conn.close()
    return results

def get_entity_info(full_path: str) -> dict | None:
    conn = _open_database()
    entity = get_entity_by_path(conn, full_path)
    conn.close()
    return entity
