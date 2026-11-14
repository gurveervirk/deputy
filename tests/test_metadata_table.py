import sqlite3

from deputy.database.sqlite import (
    get_metadata,
    init_schema,
    open_database,
    set_metadata,
)


def _tables(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _old_database(path, rows, extra_metadata=None):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executemany("INSERT INTO config VALUES (?, ?)", rows)
    if extra_metadata is not None:
        conn.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.executemany("INSERT INTO metadata VALUES (?, ?)", extra_metadata)
    conn.commit()
    conn.close()


def test_new_database_has_a_metadata_table_and_no_config_table(tmp_path):
    conn = open_database(str(tmp_path / "new.db"))
    init_schema(conn)

    tables = _tables(conn)

    assert "metadata" in tables
    assert "config" not in tables


def test_config_table_of_an_existing_database_becomes_metadata(tmp_path):
    path = str(tmp_path / "old.db")
    _old_database(path, [("base_path", "/work/acme"), ("deputy_cli_version", "0.3.0")])

    conn = open_database(path)
    init_schema(conn)

    assert get_metadata(conn, "base_path") == "/work/acme"
    assert get_metadata(conn, "deputy_cli_version") == "0.3.0"
    assert "config" not in _tables(conn)


def test_migrating_twice_changes_nothing(tmp_path):
    path = str(tmp_path / "old.db")
    _old_database(path, [("base_path", "/work/acme")])
    conn = open_database(path)
    init_schema(conn)
    set_metadata(conn, "base_path", "/work/acme-moved")
    conn.commit()

    init_schema(conn)

    assert get_metadata(conn, "base_path") == "/work/acme-moved"
    assert "config" not in _tables(conn)


def test_a_config_table_recreated_by_an_older_version_is_merged(tmp_path):
    path = str(tmp_path / "round-trip.db")
    _old_database(
        path,
        [("base_path", "/work/stale"), ("extra", "kept")],
        extra_metadata=[("base_path", "/work/acme")],
    )

    conn = open_database(path)
    init_schema(conn)

    assert get_metadata(conn, "base_path") == "/work/acme"
    assert get_metadata(conn, "extra") == "kept"
    assert "config" not in _tables(conn)
