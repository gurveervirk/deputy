import json
import os

from deputy.database.sqlite import init_schema, open_database, set_config
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
    set_config(conn, "base_path", str(project))
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
