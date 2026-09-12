import json
import os
import tempfile

from deproc.core.context import Context

from deputy.core import create_context
from deputy.database.sqlite import (
    get_branch_entities,
    get_direct_bases,
    get_direct_implementations,
    get_direct_subclasses,
    get_direct_subinterfaces,
    get_entity_by_id,
    get_entity_ids_by_fqn,
    init_schema,
    open_database,
    set_config,
    upsert_branch_entities,
    upsert_branch_file,
    upsert_entity,
)
from deputy.tools.core import run_sync
from deputy.tools.deproc_resolution import (
    DeprocResolutionAdapter,
    build_context_from_records,
)
from deputy.tools.inheritance import (
    clean_inherited_member_entities,
    eager_resolve_all_inherited_members,
    resolve_all_inherits,
)
from deputy.tools.resolve import InteractiveResolver
from deputy.tools.utils import _process_files
from deputy.utils.storage import compute_sha256, get_source_files


def _write_project(files: dict[str, str]) -> str:
    tmp = tempfile.mkdtemp()
    for rel, content in files.items():
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    return tmp


def _run_sync(db, project_dir: str, branch: str = "main") -> list[dict]:
    ctx = create_context(project_dir, db)
    files = get_source_files(ctx)
    records, _ = _process_files(ctx, files, project_dir)

    clean_inherited_member_entities(db, branch=branch)
    db.execute("DELETE FROM branch_entities WHERE branch_name = ?", (branch,))
    for record in records:
        upsert_entity(db, **record)

    resolve_all_inherits(db, records, branch=branch)

    for record in records:
        if record["type"] == "CLASS" or (
            record.get("language") == "java"
            and record["type"] in ("INTERFACE", "ENUM", "RECORD")
        ):
            upsert_entity(db, **record)

    eager_resolve_all_inherited_members(db, records, branch)
    upsert_branch_entities(db, branch, [r["id"] for r in records])
    db.commit()
    return records


def _capture_processed_context(
    ctx: Context, files: list, base_path: str
) -> tuple[list[dict], dict[str, Context]]:
    contexts: dict[str, Context] = {}
    records, _ = _process_files(
        ctx,
        files,
        base_path,
        context_sink=lambda language, linked: contexts.__setitem__(language, linked),
    )
    return records, contexts


def _entity_by_fqn(context: Context, fqn: str):
    return next(
        entity
        for entity in context.entity_registry.values()
        if getattr(entity, "fqn", None) == fqn
    )


def _resolution_snapshot(result) -> tuple:
    return (
        result.status,
        result.reason,
        tuple(sorted(result.resolved_ids)),
        tuple(sorted(result.unresolved_ids)),
        tuple(sorted(result.inaccessible_ids)),
        tuple(sorted(result.ambiguous_ids)),
        tuple(sorted(result.candidates)),
    )


def _assert_serialized_registry_is_complete(
    records: list[dict], context: Context
) -> None:
    restored_ids = {entity.id for entity in context.entity_registry.values()}
    for record in records:
        assert record["id"] in restored_ids

    for entity in context.entity_registry.values():
        parent_id = getattr(entity, "parent_id", None)
        if parent_id is not None:
            assert context.entity_registry.get(parent_id) is not None


class TestPythonExportEndToEnd:
    def test_wildcard_re_export_resolves_through_sync(self, db):
        project = _write_project(
            {
                "pkg/__init__.py": ("from .models import *\n__all__ = ['User']\n"),
                "pkg/models.py": ("__all__ = ['User']\nclass User:\n    pass\n"),
            }
        )
        records = _run_sync(db, project)

        module_records = [r for r in records if r["full_path"] == "pkg"]
        assert module_records
        meta = json.loads(module_records[0]["metadata_json"])
        assert meta.get("all_exports") == ["User"]

        result = DeprocResolutionAdapter(db, "main").resolve("pkg", "User")
        assert result.language == "python"
        assert result.status == "resolved"
        assert [r["full_path"] for r in result.resolved] == ["pkg.models.User"]

    def test_dynamic_all_marks_exports_dynamic(self, db):
        project = _write_project(
            {
                "pkg/__init__.py": (
                    "_names = ['Thing']\n"
                    "__all__ = ['Thing', *_names]\n"
                    "from .models import *\n"
                ),
                "pkg/models.py": "class Thing:\n    pass\n",
            }
        )
        records = _run_sync(db, project)

        pkg = next(r for r in records if r["full_path"] == "pkg")
        meta = json.loads(pkg["metadata_json"])
        assert meta.get("exports_dynamic") is True

    def test_alias_constant_and_rehydration(self, db):
        project = _write_project(
            {
                "pkg/__init__.py": ("from .models import User\n__all__ = ['User']\n"),
                "pkg/models.py": ("UserId = str\nVALUE = 1\nclass User:\n    pass\n"),
            }
        )
        records = _run_sync(db, project)

        result = DeprocResolutionAdapter(db, "main").resolve("pkg", "User")
        assert [r["full_path"] for r in result.resolved] == ["pkg.models.User"]

        value_records = [r for r in records if r["full_path"] == "pkg.models.VALUE"]
        assert value_records
        assert value_records[0]["full_path"] == "pkg.models.VALUE"
        user_records = [r for r in records if r["full_path"] == "pkg.models.User"]
        assert user_records
        assert json.loads(user_records[0]["metadata_json"]).get("exported") is True

    def test_unresolved_status_round_trips(self, db):
        project = _write_project(
            {
                "pkg/__init__.py": "__all__ = ['Missing']\n",
            }
        )
        _run_sync(db, project)

        result = DeprocResolutionAdapter(db, "main").resolve("pkg", "Missing")
        assert result.language == "python"
        assert result.status == "unresolved"
        assert result.resolved == ()

    def test_semantic_queries_survive_sync_record_round_trip(self, db):
        project = _write_project(
            {
                "pkg/base.py": (
                    "__all__ = ['Base', 'Hidden']\n"
                    "class Base:\n"
                    "    pass\n"
                    "class Hidden:\n"
                    "    pass\n"
                ),
                "pkg/facade.py": ("from .base import *\n__all__ = ['Base']\n"),
                "pkg/child.py": (
                    "from .base import Base\nclass Child(Base):\n    pass\n"
                ),
            }
        )
        ctx = create_context(project, None)
        records, contexts = _capture_processed_context(
            ctx, get_source_files(ctx), project
        )
        original = contexts["python"]
        restored = build_context_from_records(records)
        _assert_serialized_registry_is_complete(records, restored)

        original_resolver = original.get_resolver("python")
        restored_resolver = restored.get_resolver("python")
        assert original_resolver is not None
        assert restored_resolver is not None

        for module_fqn, symbol_name in (
            ("pkg.facade", "Base"),
            ("pkg.facade", "Hidden"),
            ("pkg.child", "Base"),
        ):
            assert _resolution_snapshot(
                original_resolver.resolve(module_fqn, symbol_name, original)
            ) == _resolution_snapshot(
                restored_resolver.resolve(module_fqn, symbol_name, restored)
            )

        original_child = _entity_by_fqn(original, "pkg.child.Child")
        restored_child = _entity_by_fqn(restored, "pkg.child.Child")
        assert original_resolver._class_mro_ids(
            original_child.id, original, {}, set()
        ) == restored_resolver._class_mro_ids(restored_child.id, restored, {}, set())


class TestJavaTypeReferenceEndToEnd:
    def test_superclass_resolved_and_projected(self, db):
        project = _write_project(
            {
                "src/com/example/Base.java": (
                    "package com.example;\n"
                    "public class Base {\n"
                    "    public void hello() {}\n"
                    "}\n"
                ),
                "src/com/example/Child.java": (
                    "package com.example;\npublic class Child extends Base {\n}\n"
                ),
            }
        )
        records = _run_sync(db, project)

        child = next(r for r in records if r["full_path"] == "com.example.Child")
        bases = get_direct_bases(db, child["id"])
        assert len(bases) == 1
        assert bases[0]["is_resolved"] == 1
        assert bases[0]["base_full_path"] == "com.example.Base"

        type_result = DeprocResolutionAdapter(db, "main").resolve_type_reference(
            child["id"], "Base"
        )
        assert type_result.status == "resolved"
        assert [r["full_path"] for r in type_result.resolved] == ["com.example.Base"]
        assert type_result.ambiguous == ()
        assert [r["full_path"] for r in type_result.candidates] == ["com.example.Base"]

        child_meta = json.loads(child["metadata_json"])
        assert child_meta["resolved_bases"][0]["is_resolved"] is True

        inherited = [
            r
            for r in get_branch_entities(db, "main")
            if r.get("type") == "INHERITED_MEMBER"
            and r["full_path"] == "com.example.Child.hello"
        ]
        assert inherited, "inherited member should be projected"

    def test_interface_and_unresolved_superclass(self, db):
        project = _write_project(
            {
                "src/com/example/Runnable2.java": (
                    "package com.example;\n"
                    "public interface Runnable2 {\n"
                    "    void run();\n"
                    "}\n"
                ),
                "src/com/example/Worker.java": (
                    "package com.example;\n"
                    "public class Worker implements Runnable2 {\n"
                    "}\n"
                ),
                "src/com/example/Missing.java": (
                    "package com.example;\n"
                    "public class Missing extends NoSuchBase {\n"
                    "}\n"
                ),
            }
        )
        records = _run_sync(db, project)

        worker = next(r for r in records if r["full_path"] == "com.example.Worker")
        worker_bases = get_direct_bases(db, worker["id"])
        assert any(
            b["is_resolved"] == 1 and b["base_full_path"] == "com.example.Runnable2"
            for b in worker_bases
        )
        assert (
            next(
                b
                for b in worker_bases
                if b["base_full_path"] == "com.example.Runnable2"
            )["relation_kind"]
            == "implements"
        )

        missing = next(r for r in records if r["full_path"] == "com.example.Missing")
        missing_bases = get_direct_bases(db, missing["id"])
        assert len(missing_bases) == 1
        assert missing_bases[0]["is_resolved"] == 0
        assert missing_bases[0]["base_full_path"] == "NoSuchBase"
        branch_info = json.loads(missing_bases[0]["branch_info"])
        assert branch_info["status"] == "unresolved"

    def test_same_package_type_shadows_wildcard_import(self, db):
        project = _write_project(
            {
                "src/com/example/Base.java": (
                    "package com.example;\npublic class Base {}\n"
                ),
                "src/com/other/Base.java": (
                    "package com.other;\npublic class Base {}\n"
                ),
                "src/com/example/Child.java": (
                    "package com.example;\n"
                    "import com.other.*;\n"
                    "public class Child extends Base {}\n"
                ),
            }
        )
        records = _run_sync(db, project)

        child = next(r for r in records if r["full_path"] == "com.example.Child")
        bases = get_direct_bases(db, child["id"])
        assert len(bases) == 1
        assert bases[0]["is_resolved"] == 1
        assert bases[0]["base_full_path"] == "com.example.Base"

    def test_interface_record_enum_relationships_survive_run_sync(
        self, tmp_path, monkeypatch
    ):
        project = tmp_path / "project"
        project.mkdir()
        files = {
            "src/com/example/Marker.java": (
                "package com.example;\npublic interface Marker {}\n"
            ),
            "src/com/example/ChildMarker.java": (
                "package com.example;\npublic interface ChildMarker extends Marker {}\n"
            ),
            "src/com/example/Worker.java": (
                "package com.example;\npublic class Worker implements Marker {}\n"
            ),
            "src/com/example/Data.java": (
                "package com.example;\n"
                "public record Data(int id) implements Marker {}\n"
            ),
            "src/com/example/Kind.java": (
                "package com.example;\npublic enum Kind implements Marker { ONE }\n"
            ),
        }
        for relative_path, content in files.items():
            path = project / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        db_path = tmp_path / "deputy.db"
        conn = open_database(str(db_path))
        init_schema(conn)
        set_config(conn, "base_path", str(project))
        conn.commit()
        conn.close()

        monkeypatch.setattr(
            "deputy.tools.core._open_database",
            lambda: open_database(str(db_path)),
        )
        monkeypatch.setattr("deputy.tools.core.get_current_branch", lambda: "main")
        run_sync(force=True, sync_deps=False)

        conn = open_database(str(db_path))

        def entity_id(full_path, entity_type):
            return next(
                entity_id
                for entity_id in get_entity_ids_by_fqn(conn, full_path)
                if get_entity_by_id(conn, entity_id)["type"] == entity_type
            )

        child_marker_id = entity_id("com.example.ChildMarker", "INTERFACE")
        worker_id = entity_id("com.example.Worker", "CLASS")
        data_id = entity_id("com.example.Data", "RECORD")
        kind_id = entity_id("com.example.Kind", "ENUM")

        child_marker = get_entity_by_id(conn, child_marker_id)
        worker = get_entity_by_id(conn, worker_id)
        data = get_entity_by_id(conn, data_id)
        kind = get_entity_by_id(conn, kind_id)
        assert child_marker["type"] == "INTERFACE"
        assert worker["type"] == "CLASS"
        assert data["type"] == "RECORD"
        assert kind["type"] == "ENUM"

        assert (
            get_direct_bases(conn, child_marker_id)[0]["relation_kind"]
            == "interface_extends"
        )
        assert get_direct_bases(conn, worker_id)[0]["relation_kind"] == "implements"
        assert get_direct_bases(conn, data_id)[0]["relation_kind"] == "implements"
        assert get_direct_bases(conn, kind_id)[0]["relation_kind"] == "implements"
        implementors = get_direct_implementations(conn, "com.example.Marker", "main")
        assert {row["id"] for row in implementors} == {worker_id, data_id, kind_id}
        subinterfaces = get_direct_subinterfaces(conn, "com.example.Marker", "main")
        assert {row["id"] for row in subinterfaces} == {child_marker_id}
        assert get_direct_subclasses(conn, "com.example.Marker", "main") == []
        conn.close()

    def test_run_sync_migrates_legacy_java_relation_kinds_without_file_changes(
        self, tmp_path, monkeypatch
    ):
        project = tmp_path / "project"
        project.mkdir()
        db_path = tmp_path / "deputy.db"

        conn = open_database(str(db_path))
        init_schema(conn)
        set_config(conn, "base_path", str(project))
        conn.execute("DROP TABLE class_bases")
        conn.execute(
            """CREATE TABLE class_bases (
                class_entity_id TEXT NOT NULL,
                base_full_path TEXT NOT NULL,
                base_entity_id TEXT,
                is_resolved INTEGER NOT NULL DEFAULT 0,
                branch_info TEXT,
               PRIMARY KEY (class_entity_id, base_full_path)
            )"""
        )
        entities = [
            (
                "marker",
                "com.example.Marker",
                "Marker",
                "INTERFACE",
                {},
            ),
            (
                "base",
                "com.example.Base",
                "Base",
                "CLASS",
                {},
            ),
            (
                "worker",
                "com.example.Worker",
                "Worker",
                "CLASS",
                {"implements": ["com.example.Marker"]},
            ),
            (
                "child",
                "com.example.Child",
                "Child",
                "CLASS",
                {"superclass": "com.example.Base"},
            ),
            (
                "child_marker",
                "com.example.ChildMarker",
                "ChildMarker",
                "INTERFACE",
                {"extends_interfaces": ["com.example.Marker"]},
            ),
        ]
        for entity_id, full_path, name, entity_type, metadata in entities:
            upsert_entity(
                conn,
                id=entity_id,
                language="java",
                full_path=full_path,
                name=name,
                type=entity_type,
                metadata_json=json.dumps(metadata),
            )
        upsert_branch_entities(conn, "main", [entity[0] for entity in entities])
        legacy_relations = [
            ("worker", "com.example.Marker", "marker"),
            ("child", "com.example.Base", "base"),
            ("child_marker", "com.example.Marker", "marker"),
        ]
        conn.executemany(
            """INSERT INTO class_bases
               (class_entity_id, base_full_path, base_entity_id, is_resolved, branch_info)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (owner, base, target, 1, None)
                for owner, base, target in legacy_relations
            ],
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("deputy.tools.utils._resolve_db_path", lambda: str(db_path))
        monkeypatch.setattr("deputy.tools.core.get_current_branch", lambda: "main")

        run_sync(force=False, sync_deps=False)

        conn = open_database(str(db_path))
        relation_kinds = {
            owner: get_direct_bases(conn, owner)[0]["relation_kind"]
            for owner, _, _ in legacy_relations
        }
        assert relation_kinds == {
            "worker": "implements",
            "child": "extends",
            "child_marker": "interface_extends",
        }
        assert {
            row["id"]
            for row in get_direct_implementations(conn, "com.example.Marker", "main")
        } == {"worker"}
        assert {
            row["id"] for row in get_direct_subclasses(conn, "com.example.Base", "main")
        } == {"child"}
        assert {
            row["id"]
            for row in get_direct_subinterfaces(conn, "com.example.Marker", "main")
        } == {"child_marker"}
        assert get_direct_subclasses(conn, "com.example.Marker", "main") == []
        conn.close()

    def test_run_sync_rebuilds_missing_legacy_java_relations_without_file_changes(
        self, tmp_path, monkeypatch
    ):
        project = tmp_path / "project"
        project.mkdir()
        db_path = tmp_path / "deputy.db"
        files = {
            "src/com/example/Marker.java": "package com.example; public interface Marker {}",
            "src/com/example/ChildMarker.java": (
                "package com.example; public interface ChildMarker extends Marker {}"
            ),
            "src/com/example/Worker.java": (
                "package com.example; public class Worker implements Marker {}"
            ),
            "src/com/example/Data.java": (
                "package com.example; public record Data(int id) implements Marker {}"
            ),
            "src/com/example/Kind.java": (
                "package com.example; public enum Kind implements Marker { ONE }"
            ),
        }
        for relative_path, content in files.items():
            path = project / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        conn = open_database(str(db_path))
        init_schema(conn)
        set_config(conn, "base_path", str(project))
        conn.execute("DROP TABLE class_bases")
        conn.execute(
            """CREATE TABLE class_bases (
                class_entity_id TEXT NOT NULL,
                base_full_path TEXT NOT NULL,
                base_entity_id TEXT,
                is_resolved INTEGER NOT NULL DEFAULT 0,
                branch_info TEXT,
                PRIMARY KEY (class_entity_id, base_full_path)
            )"""
        )
        entities = [
            (
                "marker",
                "com.example.Marker",
                "Marker",
                "INTERFACE",
                {},
            ),
            (
                "child_marker",
                "com.example.ChildMarker",
                "ChildMarker",
                "INTERFACE",
                {
                    "extends_interfaces": ["com.example.Marker"],
                    "resolved_bases": [
                        {
                            "name": "com.example.Marker",
                            "full_path": "com.example.Marker",
                            "entity_id": "marker",
                            "is_resolved": True,
                        }
                    ],
                },
            ),
            (
                "worker",
                "com.example.Worker",
                "Worker",
                "CLASS",
                {
                    "implements": ["com.example.Marker"],
                    "resolved_bases": [
                        {
                            "name": "com.example.Marker",
                            "full_path": "com.example.Marker",
                            "entity_id": "marker",
                            "is_resolved": True,
                        }
                    ],
                },
            ),
            (
                "data",
                "com.example.Data",
                "Data",
                "RECORD",
                {
                    "implements": ["com.example.Marker"],
                    "resolved_bases": [
                        {
                            "name": "com.example.Marker",
                            "full_path": "com.example.Marker",
                            "entity_id": "marker",
                            "is_resolved": True,
                        }
                    ],
                },
            ),
            (
                "kind",
                "com.example.Kind",
                "Kind",
                "ENUM",
                {
                    "implements": ["com.example.Marker"],
                    "resolved_bases": [
                        {
                            "name": "com.example.Marker",
                            "full_path": "com.example.Marker",
                            "entity_id": "marker",
                            "is_resolved": True,
                        }
                    ],
                },
            ),
        ]
        for entity_id, full_path, name, entity_type, metadata in entities:
            upsert_entity(
                conn,
                id=entity_id,
                language="java",
                full_path=full_path,
                name=name,
                type=entity_type,
                metadata_json=json.dumps(metadata),
            )
        upsert_branch_entities(conn, "main", [entity[0] for entity in entities])
        for relative_path in files:
            path = project / relative_path
            upsert_branch_file(
                conn,
                "main",
                relative_path,
                compute_sha256(str(path)),
                path.stat().st_mtime,
            )
        assert conn.execute("SELECT COUNT(*) FROM class_bases").fetchone()[0] == 0
        conn.commit()
        conn.close()

        monkeypatch.setattr("deputy.tools.utils._resolve_db_path", lambda: str(db_path))
        monkeypatch.setattr("deputy.tools.core.get_current_branch", lambda: "main")

        run_sync(force=False, sync_deps=False)

        conn = open_database(str(db_path))
        relation_kinds = {
            entity_id: get_direct_bases(conn, entity_id)[0]["relation_kind"]
            for entity_id in ("child_marker", "worker", "data", "kind")
        }
        assert relation_kinds == {
            "child_marker": "interface_extends",
            "worker": "implements",
            "data": "implements",
            "kind": "implements",
        }
        assert {
            row["id"]
            for row in get_direct_implementations(conn, "com.example.Marker", "main")
        } == {"worker", "data", "kind"}
        assert {
            row["id"]
            for row in get_direct_subinterfaces(conn, "com.example.Marker", "main")
        } == {"child_marker"}
        assert get_direct_subclasses(conn, "com.example.Marker", "main") == []
        conn.close()

    def test_interactive_resolver_uses_deproc_backend(self, db):
        project = _write_project(
            {
                "pkg/__init__.py": "from .models import *\n__all__ = ['User']\n",
                "pkg/models.py": "__all__ = ['User']\nclass User:\n    pass\n",
            }
        )
        _run_sync(db, project)

        resolver = InteractiveResolver(db, branch_name="main", backend="deproc")
        result = resolver.resolve("pkg", "User")
        assert result is not None
        assert result["full_path"] == "pkg.models.User"


class TestResolveJavaTypeReferencesDirect:
    def test_build_context_from_records_round_trip(self, db):
        project = _write_project(
            {
                "src/com/example/Base.java": (
                    "package com.example;\npublic class Base {}\n"
                ),
                "src/com/example/Child.java": (
                    "package com.example;\npublic class Child extends Base {}\n"
                ),
            }
        )
        ctx = create_context(project, None)
        files = get_source_files(ctx)
        records, _ = _process_files(ctx, files, project)

        context = build_context_from_records(records)
        assert context is not None

        child_entities = [
            entity
            for entity in context.entity_registry.values()
            if getattr(entity, "fqn", None) == "com.example.Child"
        ]
        assert child_entities

    def test_semantic_queries_survive_sync_record_round_trip(self, db):
        project = _write_project(
            {
                "src/com/example/Base.java": (
                    "package com.example;\npublic class Base {}\n"
                ),
                "src/com/example/Contract.java": (
                    "package com.example;\npublic interface Contract {}\n"
                ),
                "src/com/example/Child.java": (
                    "package com.example;\n"
                    "public class Child extends Base implements Contract {}\n"
                ),
            }
        )
        ctx = create_context(project, None)
        records, contexts = _capture_processed_context(
            ctx, get_source_files(ctx), project
        )
        original = contexts["java"]
        restored = build_context_from_records(records)
        _assert_serialized_registry_is_complete(records, restored)

        original_resolver = original.get_resolver("java")
        restored_resolver = restored.get_resolver("java")
        assert original_resolver is not None
        assert restored_resolver is not None
        original_child = _entity_by_fqn(original, "com.example.Child")
        restored_child = _entity_by_fqn(restored, "com.example.Child")

        for raw_name in ("Base", "Contract", "Missing"):
            original_result = original_resolver.resolve_type_reference(
                raw_name, original_child, original
            )
            restored_result = restored_resolver.resolve_type_reference(
                raw_name, restored_child, restored
            )
            assert (
                original_result.status,
                original_result.value,
                original_result.candidates,
                original_result.reason,
            ) == (
                restored_result.status,
                restored_result.value,
                restored_result.candidates,
                restored_result.reason,
            )

        assert original_child.superclass == restored_child.superclass
        assert original_child.implements == restored_child.implements
