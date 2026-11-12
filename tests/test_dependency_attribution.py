import json

import pytest
from test_dependency_sync import (
    _cli,
    _cli_finds,
    _init_cli_project,
    _install_package,
    _install_shipping,
    _sync_with_dependencies,
)

import deputy.tools.core as core
from deputy.database.sqlite import open_database, upsert_branch_entities, upsert_entity
from deputy.tools.core import run_sync


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("class App:\n    pass\n")
    venv = project / ".venv"
    site_packages = venv / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return project, venv, site_packages


def _dependency_entities(conn):
    rows = conn.execute(
        "SELECT full_path, type, metadata_json FROM entities "
        "WHERE json_extract(metadata_json, '$.source') = 'dependency'"
    )
    return [
        (
            row["full_path"],
            row["type"],
            json.loads(row["metadata_json"])["package_name"],
        )
        for row in rows
    ]


def _owned_by(entities, package_name):
    return sorted(
        (path, kind) for path, kind, owner in entities if owner == package_name
    )


def test_dependency_package_owns_only_its_own_top_level_module(tmp_path, monkeypatch):
    project, venv, site_packages = _project(tmp_path)
    _install_package(
        site_packages,
        "acme-billing",
        "acme_billing",
        {"invoice": "class Invoice:\n    pass\n"},
    )
    _install_package(
        site_packages,
        "acme-shipping",
        "acme_shipping",
        {"parcel": "class Parcel:\n    pass\n"},
    )

    entities = _dependency_entities(
        _sync_with_dependencies(tmp_path, monkeypatch, project, venv)
    )

    assert _owned_by(entities, "acme-billing") == [
        ("acme_billing", "PACKAGE"),
        ("acme_billing.invoice", "PYTHON_MODULE"),
        ("acme_billing.invoice.Invoice", "CLASS"),
    ]
    assert _owned_by(entities, "acme-shipping") == [
        ("acme_shipping", "PACKAGE"),
        ("acme_shipping.parcel", "PYTHON_MODULE"),
        ("acme_shipping.parcel.Parcel", "CLASS"),
    ]


def test_dependency_package_does_not_take_sibling_with_longer_name(
    tmp_path, monkeypatch
):
    project, venv, site_packages = _project(tmp_path)
    _install_package(site_packages, "acme", "acme", {"core": "class Core:\n    pass\n"})
    _install_package(
        site_packages,
        "acme-extras",
        "acme_extras",
        {"tools": "class Tools:\n    pass\n"},
    )

    entities = _dependency_entities(
        _sync_with_dependencies(tmp_path, monkeypatch, project, venv)
    )

    assert _owned_by(entities, "acme") == [
        ("acme", "PACKAGE"),
        ("acme.core", "PYTHON_MODULE"),
        ("acme.core.Core", "CLASS"),
    ]
    assert _owned_by(entities, "acme-extras") == [
        ("acme_extras", "PACKAGE"),
        ("acme_extras.tools", "PYTHON_MODULE"),
        ("acme_extras.tools.Tools", "CLASS"),
    ]


def _install_both(site_packages):
    _install_package(
        site_packages,
        "acme-billing",
        "acme_billing",
        {"invoice": "class Invoice:\n    pass\n"},
    )
    _install_package(
        site_packages,
        "acme-shipping",
        "acme_shipping",
        {"parcel": "class Parcel:\n    pass\n"},
    )


def _plant_stray_container(db_path, branch):
    conn = open_database(str(db_path))
    upsert_entity(
        conn,
        id="stray-container",
        language="python",
        full_path="acme_shipping",
        name="acme_shipping",
        type="NAMESPACE_PACKAGE",
        metadata_json=json.dumps(
            {"source": "dependency", "package_name": "acme-billing"}
        ),
    )
    upsert_branch_entities(conn, branch, ["stray-container"])
    conn.commit()
    conn.close()


def _stray_container_count(db_path):
    conn = open_database(str(db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE id = 'stray-container'"
    ).fetchone()[0]
    conn.close()
    return count


def _record_processing(monkeypatch):
    processed = []
    original = core.process_dependency

    def recording(name, *args, **kwargs):
        processed.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(core, "process_dependency", recording)
    return processed


def test_sync_force_deps_reprocesses_unchanged_dependencies(tmp_path, monkeypatch):
    project, venv, site_packages = _project(tmp_path)
    _install_both(site_packages)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()
    processed = _record_processing(monkeypatch)

    run_sync(force=False, sync_deps=True)
    assert processed == []

    run_sync(force=False, sync_deps=True, force_deps=True)
    assert sorted(processed) == ["acme-billing", "acme-shipping"]


def test_sync_force_alone_does_not_reprocess_dependencies(tmp_path, monkeypatch):
    project, venv, site_packages = _project(tmp_path)
    _install_both(site_packages)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()
    processed = _record_processing(monkeypatch)

    run_sync(force=True, sync_deps=True)

    assert processed == []


def test_sync_force_deps_does_not_force_project_reparse(tmp_path, monkeypatch):
    project, venv, site_packages = _project(tmp_path)
    _install_both(site_packages)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()
    forced = []
    original = core._detect_file_changes

    def recording(files, tracked, base_path, force):
        forced.append(force)
        return original(files, tracked, base_path, force)

    monkeypatch.setattr(core, "_detect_file_changes", recording)

    run_sync(force=False, sync_deps=True, force_deps=True)
    run_sync(force=True, sync_deps=True, force_deps=True)

    assert forced == [False, True]


def test_sync_force_deps_enables_dependency_sync(tmp_path, monkeypatch):
    project, venv, site_packages = _project(tmp_path)
    _install_both(site_packages)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()
    processed = _record_processing(monkeypatch)

    run_sync(force=False, force_deps=True)

    assert sorted(processed) == ["acme-billing", "acme-shipping"]


def test_sync_force_deps_conflicts_with_disabled_dependency_sync(tmp_path, monkeypatch):
    project, venv, site_packages = _project(tmp_path)
    _install_both(site_packages)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()
    processed = _record_processing(monkeypatch)

    with pytest.raises(ValueError, match="force_deps"):
        run_sync(force=False, sync_deps=False, force_deps=True)

    assert processed == []


def test_sync_force_deps_removes_stray_containers_from_an_existing_database(
    tmp_path, monkeypatch
):
    project, venv, site_packages = _project(tmp_path)
    _install_both(site_packages)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()
    db_path = tmp_path / "deputy.db"
    _plant_stray_container(db_path, "main")

    run_sync(force=False, sync_deps=True)
    assert _stray_container_count(db_path) == 1

    run_sync(force=False, sync_deps=True, force_deps=True)
    assert _stray_container_count(db_path) == 0
    conn = open_database(str(db_path))
    owned = {
        row["full_path"]
        for row in conn.execute(
            "SELECT full_path FROM entities "
            "WHERE json_extract(metadata_json, '$.source') = 'dependency'"
        )
    }
    assert {"acme_billing", "acme_billing.invoice.Invoice"} <= owned
    assert {"acme_shipping", "acme_shipping.parcel.Parcel"} <= owned


def test_cli_sync_force_deps_removes_stray_containers(tmp_path, monkeypatch):
    project, site_packages = _init_cli_project(tmp_path, monkeypatch)
    _install_shipping(site_packages)
    assert _cli("sync", "--sync-deps").exit_code == 0
    _plant_stray_container(project / ".deputy.db", "default")

    assert _cli("sync", "--sync-deps").exit_code == 0
    assert _stray_container_count(project / ".deputy.db") == 1

    result = _cli("sync", "--force", "--sync-deps")
    assert result.exit_code == 0
    assert _stray_container_count(project / ".deputy.db") == 1

    result = _cli("sync", "--force-deps")

    assert result.exit_code == 0
    assert _stray_container_count(project / ".deputy.db") == 0
    assert _cli_finds("acme_billing.invoice.Invoice")
    assert _cli_finds("acme_shipping.parcel.Parcel")


def test_cli_sync_force_deps_rejects_no_sync_deps(tmp_path, monkeypatch):
    project, site_packages = _init_cli_project(tmp_path, monkeypatch)
    _install_shipping(site_packages)
    assert _cli("sync", "--sync-deps").exit_code == 0
    _plant_stray_container(project / ".deputy.db", "default")

    result = _cli("sync", "--force-deps", "--no-sync-deps")

    assert result.exit_code != 0
    assert "force-deps" in result.output
    assert _stray_container_count(project / ".deputy.db") == 1
