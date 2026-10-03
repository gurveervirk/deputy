import json
import os

from deproc.core.scope import AnalysisScope, RootDescriptor

from deputy.core import create_context
from deputy.database.sqlite import (
    get_branch_files,
    init_schema,
    open_database,
    set_config,
)
from deputy.tools.core import run_sync
from deputy.tools.deproc_resolution import build_context_from_records
from deputy.tools.utils import _detect_file_changes, _process_files
from deputy.utils.storage import get_source_files


def test_source_files_keep_root_identity_and_physical_path(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    project.mkdir()
    generated.mkdir()
    (project / "app.py").write_text("class ProjectApp: pass\n")
    (generated / "app.py").write_text("class GeneratedApp: pass\n")

    scope = AnalysisScope(
        project_roots=[str(project)],
        generated_roots=[str(generated)],
        selected_languages=["python"],
        selected_file_extensions=[".py"],
    )
    context = create_context(str(project), None, scope=scope)

    files = get_source_files(context)

    assert len(files) == 2
    assert {file.root_kind for file in files} == {"project", "generated"}
    assert len({file.logical_path for file in files}) == 2
    assert all(".." not in file.relative_path.split("/") for file in files)
    assert {file.absolute_path for file in files} == {
        os.path.abspath(str(project / "app.py")),
        os.path.abspath(str(generated / "app.py")),
    }


def test_change_detection_uses_root_physical_paths_and_logical_keys(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    project.mkdir()
    generated.mkdir()
    (project / "app.py").write_text("class ProjectApp: pass\n")
    (generated / "app.py").write_text("class GeneratedApp: pass\n")
    scope = AnalysisScope(
        project_roots=[str(project)],
        generated_roots=[str(generated)],
        selected_languages=["python"],
        selected_file_extensions=[".py"],
    )
    files = get_source_files(create_context(str(project), None, scope=scope))

    hashes, changed, mtime_only, deleted = _detect_file_changes(
        files, {}, str(project), force=False
    )

    assert set(hashes) == {file.logical_path for file in files}
    assert changed == set(hashes)
    assert mtime_only == set()
    assert deleted == set()


def test_processing_preserves_root_metadata_for_entities(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    project.mkdir()
    generated.mkdir()
    (project / "app.py").write_text("class ProjectApp: pass\n")
    (generated / "app.py").write_text("class GeneratedApp: pass\n")
    scope = AnalysisScope(
        project_roots=[str(project)],
        generated_roots=[str(generated)],
        selected_languages=["python"],
        selected_file_extensions=[".py"],
    )
    context = create_context(str(project), None, scope=scope)
    files = get_source_files(context)

    records, _ = _process_files(context, files, str(project))

    module_records = [record for record in records if record["type"] == "PYTHON_MODULE"]
    assert len(module_records) == 2
    root_kinds = {
        json.loads(record["metadata_json"]).get("root_kind", "project")
        for record in module_records
    }
    assert root_kinds == {"project", "generated"}


def test_run_sync_persists_root_qualified_file_keys(tmp_path, monkeypatch):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    project.mkdir()
    generated.mkdir()
    (project / "app.py").write_text("class ProjectApp: pass\n")
    (generated / "app.py").write_text("class GeneratedApp: pass\n")
    (tmp_path / ".deputyconfig").write_text(f"generated_roots={generated}\n")
    db_path = tmp_path / "deputy.db"
    conn = open_database(str(db_path))
    init_schema(conn)
    set_config(conn, "base_path", str(project))
    conn.commit()
    conn.close()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "deputy.tools.core._open_database",
        lambda: open_database(str(db_path)),
    )
    monkeypatch.setattr("deputy.tools.core.get_current_branch", lambda: "main")

    run_sync(force=True, sync_deps=False)

    conn = open_database(str(db_path))
    tracked = get_branch_files(conn, "main")
    conn.close()

    assert "app.py" in tracked
    assert any(key.endswith("::app.py") for key in tracked if key != "app.py")


def test_package_linking_is_root_order_independent(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "pkg_a").mkdir(parents=True)
    (generated / "pkg_b").mkdir(parents=True)
    (project / "pkg_a" / "__init__.py").write_text("")
    (project / "pkg_a" / "mod.py").write_text("class A: pass\n")
    (generated / "pkg_b" / "__init__.py").write_text("")
    (generated / "pkg_b" / "mod.py").write_text("class B: pass\n")

    roots = [
        RootDescriptor(str(project), kind="project", root_id="project-root"),
        RootDescriptor(str(generated), kind="generated", root_id="generated-root"),
    ]

    def snapshot(scope):
        context = create_context(str(project), None, scope=scope)
        records, _ = _process_files(context, get_source_files(context), str(project))
        by_id = {record["id"]: record for record in records}
        result = []
        for record in records:
            metadata = json.loads(record["metadata_json"])
            parent = by_id.get(record["parent_id"])
            parent_key = None
            if parent is not None:
                parent_metadata = json.loads(parent["metadata_json"])
                parent_key = (
                    parent_metadata.get("root_id", "project"),
                    parent["type"],
                    parent["full_path"],
                )
            result.append(
                (
                    metadata.get("root_id", "project"),
                    record["type"],
                    record["full_path"],
                    parent_key,
                )
            )
        return sorted(result)

    first = snapshot(
        AnalysisScope(
            roots=roots,
            selected_languages=["python"],
            selected_file_extensions=[".py"],
        )
    )
    second = snapshot(
        AnalysisScope(
            roots=list(reversed(roots)),
            selected_languages=["python"],
            selected_file_extensions=[".py"],
        )
    )

    assert first == second
    assert (
        "generated-root",
        "PYTHON_MODULE",
        "pkg_b.mod",
        ("generated-root", "PACKAGE", "pkg_b"),
    ) in first
    assert (
        "project-root",
        "PYTHON_MODULE",
        "pkg_a.mod",
        ("project-root", "PACKAGE", "pkg_a"),
    ) in first


def test_identical_files_keep_root_distinct_semantic_ids_after_rehydration(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "pkg").mkdir(parents=True)
    (generated / "pkg").mkdir(parents=True)
    source = "class Shared:\n    def method(self):\n        return 1\n"
    (project / "pkg" / "mod.py").write_text(source)
    (generated / "pkg" / "mod.py").write_text(source)
    scope = AnalysisScope(
        roots=[
            RootDescriptor(str(project), kind="project", root_id="project-root"),
            RootDescriptor(str(generated), kind="generated", root_id="generated-root"),
        ],
        selected_languages=["python"],
        selected_file_extensions=[".py"],
    )
    context = create_context(str(project), None, scope=scope)
    records, _ = _process_files(context, get_source_files(context), str(project))

    modules = [record for record in records if record["type"] == "PYTHON_MODULE"]
    classes = [record for record in records if record["type"] == "CLASS"]
    methods = [record for record in records if record["type"] == "METHOD"]
    assert len(modules) == len(classes) == len(methods) == 2
    assert len({record["id"] for record in records}) == len(records)
    assert len({record["id"] for record in modules}) == 2
    assert len({record["id"] for record in classes}) == 2
    assert len({record["id"] for record in methods}) == 2

    restored = build_context_from_records(records)
    restored_modules = [
        entity
        for entity in restored.entity_registry.values()
        if getattr(entity, "fqn", None) == "pkg.mod"
    ]
    assert {entity.source_root_id for entity in restored_modules} == {
        "project-root",
        "generated-root",
    }
    assert {entity.id for entity in restored_modules} == {
        record["id"] for record in modules
    }


def test_cross_root_python_resolution_uses_shared_registry(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    project.mkdir()
    generated.mkdir()
    (project / "app.py").write_text("from models import Model\n")
    (generated / "models.py").write_text("class Model: pass\n")
    scope = AnalysisScope(
        project_roots=[str(project)],
        generated_roots=[str(generated)],
        selected_languages=["python"],
        selected_file_extensions=[".py"],
    )
    context = create_context(str(project), None, scope=scope)
    linked = {}
    _process_files(
        context,
        get_source_files(context),
        str(project),
        context_sink=lambda language, value: linked.__setitem__(language, value),
    )

    result = (
        linked["python"]
        .get_resolver("python")
        .resolve("app", "Model", linked["python"])
    )
    assert result.status.value == "resolved"
    assert {
        getattr(entity, "fqn", None)
        for entity in linked["python"].entity_registry.values()
        if entity.id in result.resolved_ids
    } == {"models.Model"}
