from deputy.database.sqlite import (
    open_database,
    init_schema,
    search_entities,
    get_entity_by_path
)

def init_database(path: str) -> None:
    conn = open_database(path)
    init_schema(conn)
    conn.close()

def run_sync(force: bool) -> None:
    pass

def search_entities(pattern: str) -> list[dict]:
    conn = open_database(".deputy.db")
    results = search_entities(conn, pattern)
    conn.close()
    return results

def get_entity_info(full_path: str) -> dict | None:
    conn = open_database(".deputy.db")
    entity = get_entity_by_path(conn, full_path)
    conn.close()
    return entity
