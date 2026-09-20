import json
import os

from deproc.core.scope import AnalysisScope

from deputy.core import create_context
from deputy.database.sqlite import (
    get_branch_files,
    init_schema,
    open_database,
    set_config,
)
from deputy.tools.core import run_sync
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
