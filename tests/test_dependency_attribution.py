import json

from test_dependency_sync import _install_package, _sync_with_dependencies


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
