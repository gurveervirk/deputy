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


def _scope_for(project, generated, language, extension):
    return AnalysisScope(
        roots=[
            RootDescriptor(str(project), kind="project", root_id="project-root"),
            RootDescriptor(str(generated), kind="generated", root_id="generated-root"),
        ],
        selected_languages=[language],
        selected_file_extensions=[extension],
    )


def test_shared_python_namespace_package_aggregates_across_roots(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "ns").mkdir(parents=True)
    (generated / "ns").mkdir(parents=True)
    (project / "ns" / "a.py").write_text("class A: pass\n")
    (generated / "ns" / "b.py").write_text("class B: pass\n")

    context = create_context(
        str(project), None, scope=_scope_for(project, generated, "python", ".py")
    )
    records, _ = _process_files(context, get_source_files(context), str(project))

    namespaces = [
        r for r in records if r["full_path"] == "ns" and r["parent_id"] is None
    ]
    assert len(namespaces) == 1
    modules = {r["full_path"]: r for r in records if r["type"] == "PYTHON_MODULE"}
    assert set(modules) == {"ns.a", "ns.b"}
    assert {m["parent_id"] for m in modules.values()} == {namespaces[0]["id"]}
    namespace_meta = json.loads(namespaces[0]["metadata_json"])
    assert "root_id" not in namespace_meta
    assert "root_path" not in namespace_meta
    assert [
        (root["root_id"], root["root_kind"], root["root_path"])
        for root in namespace_meta["roots"]
    ] == [
        ("generated-root", "generated", os.path.abspath(str(generated))),
        ("project-root", "project", os.path.abspath(str(project))),
    ]
    assert json.loads(modules["ns.a"]["metadata_json"])["root_id"] == "project-root"
    assert json.loads(modules["ns.b"]["metadata_json"])["root_id"] == "generated-root"


def test_shared_java_package_aggregates_across_roots(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "com" / "acme").mkdir(parents=True)
    (generated / "com" / "acme").mkdir(parents=True)
    (project / "com" / "acme" / "A.java").write_text(
        "package com.acme;\npublic class A {}\n"
    )
    (generated / "com" / "acme" / "B.java").write_text(
        "package com.acme;\npublic class B {}\n"
    )

    context = create_context(
        str(project), None, scope=_scope_for(project, generated, "java", ".java")
    )
    records, _ = _process_files(context, get_source_files(context), str(project))

    packages = [r for r in records if r["full_path"] == "com.acme"]
    assert len(packages) == 1
    names = {r["name"] for r in records if r["type"] == "CLASS"}
    assert names == {"A", "B"}
    package_meta = json.loads(packages[0]["metadata_json"])
    assert "root_id" not in package_meta
    assert [root["root_id"] for root in package_meta["roots"]] == [
        "generated-root",
        "project-root",
    ]


def test_shared_namespace_package_is_root_order_independent(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "ns").mkdir(parents=True)
    (generated / "ns").mkdir(parents=True)
    (project / "ns" / "a.py").write_text("class A: pass\n")
    (generated / "ns" / "b.py").write_text("class B: pass\n")
    roots = [
        RootDescriptor(str(project), kind="project", root_id="project-root"),
        RootDescriptor(str(generated), kind="generated", root_id="generated-root"),
    ]

    def snapshot(ordered_roots):
        scope = AnalysisScope(
            roots=ordered_roots,
            selected_languages=["python"],
            selected_file_extensions=[".py"],
        )
        context = create_context(str(project), None, scope=scope)
        records, _ = _process_files(context, get_source_files(context), str(project))
        return sorted(
            (r["id"], r["full_path"], r["parent_id"], r["metadata_json"])
            for r in records
        )

    assert snapshot(roots) == snapshot(list(reversed(roots)))


def test_shared_namespace_package_survives_rehydration(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "ns").mkdir(parents=True)
    (generated / "ns").mkdir(parents=True)
    (project / "ns" / "a.py").write_text("class A: pass\n")
    (generated / "ns" / "b.py").write_text("class B: pass\n")
    context = create_context(
        str(project), None, scope=_scope_for(project, generated, "python", ".py")
    )
    records, _ = _process_files(context, get_source_files(context), str(project))

    restored = build_context_from_records(records)

    namespace_ids = restored.entity_registry.get_ids_by_fqn("ns")
    assert len(namespace_ids) == 1
    for fqn in ("ns.a", "ns.b"):
        (module_id,) = restored.entity_registry.get_ids_by_fqn(fqn)
        module = restored.entity_registry.get(module_id)
        assert module.parent_id in namespace_ids


def test_shared_namespace_package_across_three_roots(tmp_path):
    roots = []
    for index, name in enumerate(("one", "two", "three")):
        root = tmp_path / name
        (root / "ns").mkdir(parents=True)
        (root / "ns" / f"m{index}.py").write_text(f"class C{index}: pass\n")
        roots.append(RootDescriptor(str(root), kind="project", root_id=f"{name}-root"))
    scope = AnalysisScope(
        roots=roots,
        selected_languages=["python"],
        selected_file_extensions=[".py"],
    )
    context = create_context(str(tmp_path / "one"), None, scope=scope)

    records, _ = _process_files(
        context, get_source_files(context), str(tmp_path / "one")
    )

    namespaces = [r for r in records if r["full_path"] == "ns"]
    assert len(namespaces) == 1
    modules = [r for r in records if r["type"] == "PYTHON_MODULE"]
    assert {m["parent_id"] for m in modules} == {namespaces[0]["id"]}
    assert len(modules) == 3


def test_container_from_one_root_keeps_scalar_root_metadata(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "only_here").mkdir(parents=True)
    generated.mkdir()
    (project / "only_here" / "a.py").write_text("class A: pass\n")
    (generated / "other.py").write_text("class B: pass\n")
    context = create_context(
        str(project), None, scope=_scope_for(project, generated, "python", ".py")
    )

    records, _ = _process_files(context, get_source_files(context), str(project))

    (container,) = [r for r in records if r["full_path"] == "only_here"]
    meta = json.loads(container["metadata_json"])
    assert meta["root_id"] == "project-root"
    assert "roots" not in meta


def test_shared_java_package_is_root_order_independent_and_rehydrates(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    for root, name in ((project, "A"), (generated, "B")):
        (root / "com" / "acme").mkdir(parents=True)
        (root / "com" / "acme" / f"{name}.java").write_text(
            f"package com.acme;\npublic class {name} {{}}\n"
        )
    roots = [
        RootDescriptor(str(project), kind="project", root_id="project-root"),
        RootDescriptor(str(generated), kind="generated", root_id="generated-root"),
    ]

    def run(ordered_roots):
        scope = AnalysisScope(
            roots=ordered_roots,
            selected_languages=["java"],
            selected_file_extensions=[".java"],
        )
        context = create_context(str(project), None, scope=scope)
        records, _ = _process_files(context, get_source_files(context), str(project))
        return records

    first = run(roots)
    second = run(list(reversed(roots)))

    def snapshot(records):
        return sorted(
            (r["id"], r["full_path"], r["parent_id"], r["metadata_json"])
            for r in records
        )

    assert snapshot(first) == snapshot(second)

    restored = build_context_from_records(first)
    (package_id,) = restored.entity_registry.get_ids_by_fqn("com.acme")
    package = restored.entity_registry.get(package_id)
    assert len(package.compilation_unit_ids) == 2


def _regular_and_namespace_roots(tmp_path):
    project = tmp_path / "project"
    generated = tmp_path / "generated"
    (project / "ns").mkdir(parents=True)
    (generated / "ns").mkdir(parents=True)
    (project / "ns" / "__init__.py").write_text("")
    (project / "ns" / "a.py").write_text("class A: pass\n")
    (generated / "ns" / "b.py").write_text("class B: pass\n")
    return project, generated


def test_regular_package_wins_over_namespace_portion_in_another_root(tmp_path):
    project, generated = _regular_and_namespace_roots(tmp_path)
    context = create_context(
        str(project), None, scope=_scope_for(project, generated, "python", ".py")
    )

    records, _ = _process_files(context, get_source_files(context), str(project))

    packages = [r for r in records if r["full_path"] == "ns"]
    assert [r["type"] for r in packages] == ["PACKAGE"]
    modules = {r["full_path"]: r for r in records if r["type"] == "PYTHON_MODULE"}
    assert set(modules) == {"ns.a", "ns.b"}
    assert modules["ns.a"]["parent_id"] == packages[0]["id"]
    assert modules["ns.b"]["parent_id"] is None


def test_regular_package_precedence_is_root_order_independent(tmp_path):
    project, generated = _regular_and_namespace_roots(tmp_path)
    roots = [
        RootDescriptor(str(project), kind="project", root_id="project-root"),
        RootDescriptor(str(generated), kind="generated", root_id="generated-root"),
    ]

    def snapshot(ordered_roots):
        scope = AnalysisScope(
            roots=ordered_roots,
            selected_languages=["python"],
            selected_file_extensions=[".py"],
        )
        context = create_context(str(project), None, scope=scope)
        records, _ = _process_files(context, get_source_files(context), str(project))
        return sorted(
            (r["id"], r["type"], r["full_path"], r["parent_id"]) for r in records
        )

    assert snapshot(roots) == snapshot(list(reversed(roots)))


def test_regular_package_precedence_survives_rehydration(tmp_path):
    project, generated = _regular_and_namespace_roots(tmp_path)
    context = create_context(
        str(project), None, scope=_scope_for(project, generated, "python", ".py")
    )
    records, _ = _process_files(context, get_source_files(context), str(project))

    restored = build_context_from_records(records)

    (package_id,) = restored.entity_registry.get_ids_by_fqn("ns")
    (shadowed_id,) = restored.entity_registry.get_ids_by_fqn("ns.b")
    assert restored.entity_registry.get(shadowed_id).parent_id is None
    (kept_id,) = restored.entity_registry.get_ids_by_fqn("ns.a")
    assert restored.entity_registry.get(kept_id).parent_id == package_id


def test_java_package_shared_by_two_named_modules_stays_separate(tmp_path):
    project = tmp_path / "project"
    for module, cls in (("m.one", "A"), ("m.two", "B")):
        (project / module / "com" / "acme").mkdir(parents=True)
        (project / module / "module-info.java").write_text(
            f"module {module} {{ exports com.acme; }}\n"
        )
        (project / module / "com" / "acme" / f"{cls}.java").write_text(
            f"package com.acme;\npublic class {cls} {{}}\n"
        )
    context = create_context(str(project), None)

    records, _ = _process_files(context, get_source_files(context), str(project))

    packages = [r for r in records if r["full_path"] == "com.acme"]
    assert len(packages) == 2
    assert len({r["id"] for r in packages}) == 2
    modules = {r["full_path"]: r for r in records if r["type"] == "JAVA_MODULE"}
    by_id = {r["id"]: r for r in records}
    for module_name, class_name in (("m.one", "A"), ("m.two", "B")):
        package_ids = json.loads(modules[module_name]["metadata_json"])["package_ids"]
        assert len(package_ids) == 1
        package = by_id[package_ids[0]]
        assert json.loads(package["metadata_json"])["module_name"] == module_name
        classes = {
            by_id[unit_id]["name"]
            for unit_id in json.loads(package["metadata_json"])["compilation_unit_ids"]
        }
        assert classes == {class_name}
