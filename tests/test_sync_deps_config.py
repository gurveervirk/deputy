import pytest
from test_dependency_sync import (
    INVOICE,
    _cli,
    _cli_finds,
    _make_project,
)

from deputy.database.sqlite import open_database, set_metadata


@pytest.fixture
def cli_project(tmp_path, monkeypatch):
    project, venv, _site_packages = _make_project(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.chdir(project)
    assert _cli("init").exit_code == 0
    (project / ".deputyconfig").write_text(f"venv_path={venv}\n")
    return project


def test_config_key_set_through_the_cli_enables_dependency_sync(cli_project):
    assert _cli("config", "sync_deps", "true").exit_code == 0

    result = _cli("sync")

    assert result.exit_code == 0
    assert "sync_deps: enabled" in result.output
    assert _cli_finds(INVOICE)


def test_plain_sync_without_the_config_key_skips_dependencies(cli_project):
    result = _cli("sync")

    assert result.exit_code == 0
    assert "sync_deps: enabled" not in result.output
    assert not _cli_finds(INVOICE)


def test_no_sync_deps_flag_overrides_the_config_key(cli_project):
    assert _cli("config", "sync_deps", "true").exit_code == 0

    result = _cli("sync", "--no-sync-deps")

    assert result.exit_code == 0
    assert not _cli_finds(INVOICE)


def test_sync_deps_flag_overrides_a_false_config_key(cli_project):
    assert _cli("config", "sync_deps", "false").exit_code == 0

    result = _cli("sync", "--sync-deps")

    assert result.exit_code == 0
    assert _cli_finds(INVOICE)


def test_config_key_false_keeps_dependency_sync_off(cli_project):
    assert _cli("config", "sync_deps", "false").exit_code == 0

    assert _cli("sync").exit_code == 0

    assert not _cli_finds(INVOICE)


def _store_in_database(project, value):
    conn = open_database(str(project / ".deputy.db"))
    set_metadata(conn, "sync_deps", value)
    conn.commit()
    conn.close()


def test_value_stored_in_the_database_is_ignored(cli_project):
    _store_in_database(cli_project, "true")

    assert _cli("sync").exit_code == 0

    assert not _cli_finds(INVOICE)
