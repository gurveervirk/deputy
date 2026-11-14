import json
import os
import shutil

from typer.testing import CliRunner

from deputy import app
from deputy.database.sqlite import init_schema, open_database, set_metadata
from deputy.tools.core import run_sync


def _install_package(site_packages, dist_name, package, modules):
    package_dir = site_packages / package
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")
    for module, source in modules.items():
        (package_dir / f"{module}.py").write_text(source)
    dist_info = site_packages / f"{dist_name}-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: 1.0\n"
    )
    (dist_info / "top_level.txt").write_text(f"{package}\n")


def _sync_with_dependencies(tmp_path, monkeypatch, project, venv):
    db_path = tmp_path / "deputy.db"
    conn = open_database(str(db_path))
    init_schema(conn)
    set_metadata(conn, "base_path", str(project))
    conn.commit()
    conn.close()
    (project / ".deputyconfig").write_text(f"venv_path={venv}\n")
    monkeypatch.chdir(project)
    monkeypatch.setattr(
        "deputy.tools.core._open_database", lambda: open_database(str(db_path))
    )
    monkeypatch.setattr("deputy.tools.core.get_current_branch", lambda: "main")
    run_sync(force=True, sync_deps=True)
    return open_database(str(db_path))


def test_dependency_sync_indexes_installed_package_entities(tmp_path, monkeypatch):
    project, venv, site_packages = _make_project(tmp_path)
    cache = site_packages / "acme_billing" / "__pycache__"
    cache.mkdir()
    (cache / "stale.py").write_text("class Stale:\n    pass\n")

    conn = _sync_with_dependencies(tmp_path, monkeypatch, project, venv)

    rows = conn.execute(
        "SELECT full_path, type, metadata_json FROM entities "
        "WHERE full_path LIKE 'acme_billing%'"
    ).fetchall()
    by_path = {row["full_path"]: row for row in rows}
    assert "acme_billing.invoice.Invoice" in by_path
    metadata = json.loads(by_path["acme_billing.invoice.Invoice"]["metadata_json"])
    assert metadata["package_name"] == "acme-billing"
    assert metadata["source"] == "dependency"
    assert not os.path.isabs(metadata.get("path", "relative"))
    assert not any("stale" in path.lower() for path in by_path)
    project_paths = {
        row["full_path"] for row in conn.execute("SELECT full_path FROM entities")
    }
    assert "app.App" in project_paths


def _make_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("class App:\n    pass\n")
    venv = project / ".venv"
    site_packages = venv / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    _install_package(
        site_packages,
        "acme-billing",
        "acme_billing",
        {"invoice": "class Invoice:\n    pass\n"},
    )
    return project, venv, site_packages


def _install_shipping(site_packages):
    _install_package(
        site_packages,
        "acme-shipping",
        "acme_shipping",
        {"parcel": "class Parcel:\n    pass\n"},
    )


def _add_extra_class(project):
    (project / "app.py").write_text(
        "class App:\n    pass\n\n\nclass Extra:\n    pass\n"
    )


def _resync(tmp_path, sync_deps=True):
    run_sync(force=False, sync_deps=sync_deps)
    return open_database(str(tmp_path / "deputy.db"))


def _dependency_paths(conn, branch=None):
    if branch is None:
        rows = conn.execute(
            "SELECT full_path FROM entities "
            "WHERE json_extract(metadata_json, '$.source') = 'dependency'"
        )
    else:
        rows = conn.execute(
            "SELECT e.full_path FROM entities e "
            "JOIN branch_entities be ON be.entity_id = e.id "
            "WHERE be.branch_name = ? "
            "AND json_extract(e.metadata_json, '$.source') = 'dependency'",
            (branch,),
        )
    return {row["full_path"] for row in rows}


def _dependency_names(conn):
    return {row["package_name"] for row in conn.execute("SELECT * FROM dependencies")}


def _dependency_version(conn, package_name):
    row = conn.execute(
        "SELECT version FROM dependencies WHERE package_name = ?", (package_name,)
    ).fetchone()
    return row["version"] if row else None


def _dangling_mappings(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM branch_entities "
        "WHERE entity_id NOT IN (SELECT id FROM entities)"
    ).fetchone()[0]


def _upgrade_billing(site_packages):
    shutil.rmtree(site_packages / "acme-billing-1.0.dist-info")
    dist_info = site_packages / "acme-billing-2.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acme-billing\nVersion: 2.0\n"
    )
    (dist_info / "top_level.txt").write_text("acme_billing\n")
    (site_packages / "acme_billing" / "invoice.py").write_text(
        "class Bill:\n    pass\n"
    )


def test_dependency_installed_without_project_change_is_indexed(tmp_path, monkeypatch):
    project, venv, site_packages = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    _install_shipping(site_packages)
    conn = _resync(tmp_path)

    assert "acme_shipping.parcel.Parcel" in _dependency_paths(conn)
    assert "acme_shipping.parcel.Parcel" in _dependency_paths(conn, "main")
    assert "acme-shipping" in _dependency_names(conn)
    assert "acme_billing.invoice.Invoice" in _dependency_paths(conn, "main")


def test_dependency_removed_without_project_change_is_cleaned(tmp_path, monkeypatch):
    project, venv, site_packages = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    shutil.rmtree(site_packages / "acme_billing")
    shutil.rmtree(site_packages / "acme-billing-1.0.dist-info")
    conn = _resync(tmp_path)

    assert _dependency_paths(conn) == set()
    assert "acme-billing" not in _dependency_names(conn)
    assert _dangling_mappings(conn) == 0


def test_dependency_installed_with_mtime_only_project_change_is_mapped(
    tmp_path, monkeypatch
):
    project, venv, site_packages = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    _install_shipping(site_packages)
    source = project / "app.py"
    stat = source.stat()
    os.utime(source, (stat.st_atime, stat.st_mtime + 100))
    conn = _resync(tmp_path)

    assert "acme_shipping.parcel.Parcel" in _dependency_paths(conn, "main")


def test_dependency_upgraded_without_project_change_replaces_entities(
    tmp_path, monkeypatch
):
    project, venv, site_packages = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    _upgrade_billing(site_packages)
    conn = _resync(tmp_path)

    assert "acme_billing.invoice.Bill" in _dependency_paths(conn, "main")
    assert "acme_billing.invoice.Invoice" not in _dependency_paths(conn)
    assert _dependency_version(conn, "acme-billing") == "2.0"
    assert _dangling_mappings(conn) == 0


def test_dependency_upgraded_with_mtime_only_project_change_replaces_entities(
    tmp_path, monkeypatch
):
    project, venv, site_packages = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    _upgrade_billing(site_packages)
    source = project / "app.py"
    stat = source.stat()
    os.utime(source, (stat.st_atime, stat.st_mtime + 100))
    conn = _resync(tmp_path)

    assert "acme_billing.invoice.Bill" in _dependency_paths(conn, "main")
    assert "acme_billing.invoice.Invoice" not in _dependency_paths(conn)
    assert _dependency_version(conn, "acme-billing") == "2.0"
    assert _dangling_mappings(conn) == 0


def test_unchanged_dependency_is_not_reprocessed(tmp_path, monkeypatch):
    import deputy.tools.core as core

    project, venv, _ = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    processed = []
    original = core.process_dependency

    def recording(name, *args, **kwargs):
        processed.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(core, "process_dependency", recording)

    conn = _resync(tmp_path)
    assert processed == []
    assert "acme_billing.invoice.Invoice" in _dependency_paths(conn, "main")

    _add_extra_class(project)
    conn = _resync(tmp_path)
    assert processed == []
    assert "acme_billing.invoice.Invoice" in _dependency_paths(conn, "main")
    assert "app.Extra" in {
        row["full_path"] for row in conn.execute("SELECT full_path FROM entities")
    }


def test_dependency_entities_return_after_sync_deps_is_toggled(tmp_path, monkeypatch):
    project, venv, _ = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    _add_extra_class(project)
    conn = _resync(tmp_path, sync_deps=False)
    assert _dependency_paths(conn, "main") == set()
    conn.close()

    conn = _resync(tmp_path, sync_deps=True)
    assert "acme_billing.invoice.Invoice" in _dependency_paths(conn, "main")


def test_dependency_entities_are_mapped_to_a_new_branch(tmp_path, monkeypatch):
    project, venv, _ = _make_project(tmp_path)
    _sync_with_dependencies(tmp_path, monkeypatch, project, venv).close()

    monkeypatch.setattr("deputy.tools.core.get_current_branch", lambda: "feature")
    _add_extra_class(project)
    conn = _resync(tmp_path)

    assert "acme_billing.invoice.Invoice" in _dependency_paths(conn, "feature")
    assert "acme_billing.invoice.Invoice" in _dependency_paths(conn, "main")


PARCEL = "acme_shipping.parcel.Parcel"
INVOICE = "acme_billing.invoice.Invoice"


def _cli(*args):
    return CliRunner().invoke(app, list(args))


def _init_cli_project(tmp_path, monkeypatch):
    project, venv, site_packages = _make_project(tmp_path)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.chdir(project)
    assert _cli("init").exit_code == 0
    (project / ".deputyconfig").write_text(f"venv_path={venv}\n")
    assert _cli("sync", "--sync-deps").exit_code == 0
    return project, site_packages


def _cli_finds(fqn):
    result = _cli("search", "--exact", fqn)
    assert result.exit_code == 0
    return "No matching entities found" not in result.output


def test_cli_finds_dependency_installed_without_project_change(tmp_path, monkeypatch):
    _project, site_packages = _init_cli_project(tmp_path, monkeypatch)
    assert _cli_finds(INVOICE)
    assert not _cli_finds(PARCEL)

    _install_shipping(site_packages)
    sync = _cli("sync", "--sync-deps")

    assert sync.exit_code == 0
    assert "Sync complete" in sync.output
    assert _cli_finds(PARCEL)
    info = _cli("info", PARCEL)
    assert info.exit_code == 0
    assert PARCEL in info.output
    assert "CLASS" in info.output
    assert _cli_finds(INVOICE)


def test_cli_stops_finding_dependency_removed_without_project_change(
    tmp_path, monkeypatch
):
    project, site_packages = _init_cli_project(tmp_path, monkeypatch)
    _install_shipping(site_packages)
    _add_extra_class(project)
    assert _cli("sync", "--sync-deps").exit_code == 0
    assert _cli_finds(PARCEL)

    shutil.rmtree(site_packages / "acme_shipping")
    shutil.rmtree(site_packages / "acme-shipping-1.0.dist-info")
    assert _cli("sync", "--sync-deps").exit_code == 0

    assert not _cli_finds(PARCEL)
    assert "Entity not found" in _cli("info", PARCEL).output
    assert _cli_finds(INVOICE)


def test_cli_finds_dependency_again_after_sync_deps_is_toggled(tmp_path, monkeypatch):
    project, _site_packages = _init_cli_project(tmp_path, monkeypatch)
    assert _cli_finds(INVOICE)

    _add_extra_class(project)
    assert _cli("sync", "--no-sync-deps").exit_code == 0
    assert not _cli_finds(INVOICE)

    assert _cli("sync", "--sync-deps").exit_code == 0
    assert _cli_finds(INVOICE)


def test_cli_dependency_sync_is_repeatable(tmp_path, monkeypatch):
    _project, site_packages = _init_cli_project(tmp_path, monkeypatch)
    _install_shipping(site_packages)
    assert _cli("sync", "--sync-deps").exit_code == 0
    first = _cli("search", "acme_")
    assert _cli("sync", "--sync-deps").exit_code == 0
    second = _cli("search", "acme_")

    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output
    assert PARCEL in first.output
