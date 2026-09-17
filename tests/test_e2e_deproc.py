import json
import os
import tempfile

import pytest
from deproc.core.context import Context
from deproc.core.interfaces.resolver import ResolutionStatus

from deputy import pin_inheritance
from deputy.core import create_context
from deputy.database.sqlite import (
    get_branch_entities,
    get_direct_bases,
    get_direct_implementations,
    get_direct_subclasses,
    get_direct_subinterfaces,
    get_entity_by_id,
    get_entity_by_path,
    get_entity_ids_by_fqn,
    get_inheritance_pin,
    init_schema,
    open_database,
    set_config,
    upsert_branch_entities,
    upsert_branch_file,
    upsert_entity,
    upsert_inheritance_pin,
)
from deputy.tools.core import run_sync
from deputy.tools.deproc_resolution import (
    DeprocResolutionAdapter,
    build_context_from_records,
)
from deputy.tools.inheritance import (
    clean_inherited_member_entities,
    eager_resolve_all_inherited_members,
    get_class_inheritance_info,
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

    python_mro_results = resolve_all_inherits(db, records, branch=branch)

    for record in records:
        if record["type"] == "CLASS" or (
            record.get("language") == "java"
            and record["type"] in ("INTERFACE", "ENUM", "RECORD")
        ):
            upsert_entity(db, **record)

    eager_resolve_all_inherited_members(
        db,
        records,
        branch,
        python_mro_results=python_mro_results,
    )
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
        getattr(result, "value", None),
        tuple(sorted(getattr(result, "resolved_ids", ()) or ())),
        tuple(sorted(getattr(result, "unresolved_ids", ()) or ())),
        tuple(sorted(getattr(result, "inaccessible_ids", ()) or ())),
        tuple(sorted(getattr(result, "ambiguous_ids", ()) or ())),
        tuple(sorted(getattr(result, "candidates", ()) or ())),
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
                "pkg/first.py": "class Thing:\n    pass\n",
                "pkg/second.py": "class Thing:\n    pass\n",
                "pkg/ambiguous.py": (
                    "from .first import Thing\nfrom .second import Thing\n"
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

        original_ambiguous = original_resolver.resolve(
            "pkg.ambiguous", "Thing", original
        )
        restored_ambiguous = restored_resolver.resolve(
            "pkg.ambiguous", "Thing", restored
        )
        assert original_ambiguous.status is ResolutionStatus.AMBIGUOUS
        assert _resolution_snapshot(original_ambiguous) == _resolution_snapshot(
            restored_ambiguous
        )

        original_child = _entity_by_fqn(original, "pkg.child.Child")
        restored_child = _entity_by_fqn(restored, "pkg.child.Child")
        assert original_resolver._class_mro_ids(
            original_child.id, original, {}, set()
        ) == restored_resolver._class_mro_ids(restored_child.id, restored, {}, set())


class TestPythonInheritanceEndToEnd:
    def test_sync_uses_deproc_mro_and_projects_inherited_members(self, db):
        project = _write_project(
            {
                "pkg/base.py": "class Base:\n    def run(self):\n        pass\n",
                "pkg/child.py": (
                    "from .base import Base\nclass Child(Base):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)

        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        base = next(r for r in records if r["full_path"] == "pkg.base.Base")
        base_method = next(r for r in records if r["full_path"] == "pkg.base.Base.run")
        bases = get_direct_bases(db, child["id"])
        assert len(bases) == 1
        assert bases[0]["base_full_path"] == "pkg.base.Base"
        assert bases[0]["base_entity_id"] == base["id"]
        assert bases[0]["is_resolved"] == 1
        assert bases[0]["relation_kind"] == "inherits"

        adapter = DeprocResolutionAdapter(db, "main")
        mro = adapter.resolve_python_class_mro(child["id"])
        inherited = adapter.get_python_inherited_members(child["id"], mro)
        assert mro.status is ResolutionStatus.RESOLVED
        assert mro.mro_ids == (child["id"], base["id"])
        assert inherited.status is ResolutionStatus.RESOLVED
        assert [(member.name, member.owner_id) for member in inherited.members] == [
            ("run", base["id"])
        ]

        synthetic = get_entity_by_id(db, "pkg.child.Child.run")
        assert synthetic is not None
        assert (
            json.loads(synthetic["metadata_json"])["target_entity_id"]
            == base_method["id"]
        )

    def test_inheritance_info_uses_branch_local_deproc_member_identity(
        self, db, monkeypatch
    ):
        project = _write_project(
            {
                "pkg/base.py": "class Base:\n    def run(self):\n        pass\n",
                "pkg/child.py": (
                    "from .base import Base\nclass Child(Base):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)

        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        base = next(r for r in records if r["full_path"] == "pkg.base.Base")
        base_method = next(r for r in records if r["full_path"] == "pkg.base.Base.run")
        base_module = next(r for r in records if r["full_path"] == "pkg.base")

        feature_module = dict(base_module)
        feature_module["id"] = "feature-base-module"
        feature_module_meta = json.loads(feature_module["metadata_json"])
        feature_module_meta["type_ids"] = ["feature-base"]
        feature_module["metadata_json"] = json.dumps(feature_module_meta)

        feature_base = dict(base)
        feature_base["id"] = "feature-base"
        feature_base["parent_id"] = feature_module["id"]
        feature_base_meta = json.loads(feature_base["metadata_json"])
        feature_base_meta["parent_id"] = feature_module["id"]
        feature_base_meta["method_ids"] = ["feature-base-run"]
        feature_base["metadata_json"] = json.dumps(feature_base_meta)

        feature_method = dict(base_method)
        feature_method["id"] = "feature-base-run"
        feature_method["parent_id"] = feature_base["id"]
        feature_method_meta = json.loads(feature_method["metadata_json"])
        feature_method_meta["parent_id"] = feature_base["id"]
        feature_method["metadata_json"] = json.dumps(feature_method_meta)

        for record in (feature_module, feature_base, feature_method):
            upsert_entity(db, **record)
        upsert_branch_entities(
            db,
            "feature",
            [feature_module["id"], feature_base["id"], feature_method["id"]],
        )
        db.commit()

        monkeypatch.setattr(
            "deputy.tools.inheritance.get_current_branch", lambda: "main"
        )

        def unexpected_global_path_lookup(*args, **kwargs):
            pytest.fail("Python inherited-member query used global FQN lookup")

        monkeypatch.setattr(
            "deputy.tools.inheritance.get_entities_by_path",
            unexpected_global_path_lookup,
        )

        info = get_class_inheritance_info(db, child["id"])
        inherited_methods = info["inherited_members"]["METHOD"]

        assert [member["id"] for member in inherited_methods] == [base_method["id"]]
        assert inherited_methods[0]["_inherited_from"] == base["full_path"]
        assert inherited_methods[0]["id"] != feature_method["id"]

    def test_adapter_applies_deputy_pin_to_ambiguous_python_base(self, db):
        project = _write_project(
            {
                "pkg/a.py": "class Base:\n    def from_a(self):\n        pass\n",
                "pkg/b.py": "class Base:\n    def from_b(self):\n        pass\n",
                "pkg/child.py": (
                    "from .a import *\nfrom .b import *\nclass Child(Base):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        base = next(r for r in records if r["full_path"] == "pkg.a.Base")

        adapter = DeprocResolutionAdapter(db, "main")
        ambiguous = adapter.resolve_python_class_mro(child["id"])
        assert ambiguous.status is ResolutionStatus.AMBIGUOUS

        upsert_inheritance_pin(db, child["id"], "Base", base["id"], "main")
        db.commit()
        pinned = DeprocResolutionAdapter(db, "main").resolve_python_class_mro(
            child["id"]
        )
        assert pinned.status is ResolutionStatus.RESOLVED
        assert pinned.mro_ids == (child["id"], base["id"])

    def test_structured_python_branch_info_is_unpacked_for_presentation(self, db):
        project = _write_project(
            {
                "pkg/a.py": "class Base:\n    pass\n",
                "pkg/b.py": "class Base:\n    pass\n",
                "pkg/child.py": (
                    "from .a import *\nfrom .b import *\nclass Child(Base):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")

        info = get_class_inheritance_info(db, child["id"])

        assert len(info["unresolved_bases"]) == 1
        unresolved = info["unresolved_bases"][0]
        assert unresolved["status"] == "ambiguous"
        assert {candidate["full_path"] for candidate in unresolved["candidates"]} == {
            "pkg.a.Base",
            "pkg.b.Base",
        }
        assert all(
            isinstance(candidate, dict) for candidate in unresolved["candidates"]
        )

    def test_python_dependency_resolution_is_branch_local_and_matches_adapter(self, db):
        project = _write_project(
            {
                "pkg/child.py": (
                    "from dependency import Base\nclass Child(Base):\n    pass\n"
                )
            }
        )
        records = _run_sync(db, project)

        dependency_rows = []
        for branch in ("main", "feature"):
            module_id = f"{branch}-dependency-module"
            base_id = f"{branch}-dependency-base"
            dependency_rows.extend(
                [
                    {
                        "id": module_id,
                        "language": "python",
                        "full_path": "dependency",
                        "name": "dependency",
                        "type": "PYTHON_MODULE",
                        "metadata_json": json.dumps(
                            {
                                "fqn": "dependency",
                                "path": f"{branch}/dependency.py",
                                "type_ids": [base_id],
                                "source": "dependency",
                                "package_name": "dependency",
                            }
                        ),
                    },
                    {
                        "id": base_id,
                        "language": "python",
                        "full_path": "dependency.Base",
                        "name": "Base",
                        "type": "CLASS",
                        "parent_id": module_id,
                        "metadata_json": json.dumps(
                            {
                                "fqn": "dependency.Base",
                                "parent_id": module_id,
                                "source": "dependency",
                                "package_name": "dependency",
                            }
                        ),
                    },
                ]
            )

        for row in dependency_rows:
            upsert_entity(db, **row)
        upsert_branch_entities(
            db, "main", ["main-dependency-module", "main-dependency-base"]
        )
        upsert_branch_entities(
            db, "feature", ["feature-dependency-module", "feature-dependency-base"]
        )
        db.commit()

        results = resolve_all_inherits(db, records, branch="main")
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        adapter = DeprocResolutionAdapter(db, "main")
        adapter_result = adapter.resolve_python_class_mro(child["id"])

        assert results[child["id"]].status is ResolutionStatus.RESOLVED
        assert results[child["id"]].mro_ids == (
            child["id"],
            "main-dependency-base",
        )
        assert adapter_result.mro_ids == results[child["id"]].mro_ids
        assert "feature-dependency-base" not in adapter_result.mro_ids

    def test_partial_python_mro_uses_one_eager_projection_policy(self, db):
        project = _write_project(
            {
                "pkg/base.py": (
                    "class Base:\n"
                    "    def run(self):\n"
                    "        pass\n"
                    "    class Inner:\n"
                    "        def nested(self):\n"
                    "            pass\n"
                ),
                "pkg/child.py": (
                    "from .base import Base\nclass Child(Base, Missing):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")

        assert get_entity_by_path(db, "pkg.child.Child.run") is not None
        assert get_entity_by_path(db, "pkg.child.Child.nested") is not None
        assert get_class_inheritance_info(db, child["id"])["mro"] is None

    def test_normalized_generic_python_pin_survives_sync_and_rehydration(self, db):
        project = _write_project(
            {
                "pkg/a.py": "class Base:\n    pass\n",
                "pkg/b.py": "class Base:\n    pass\n",
                "pkg/child.py": (
                    "from .a import *\n"
                    "from .b import *\n"
                    "class Child(Base[T]):\n"
                    "    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        base = next(r for r in records if r["full_path"] == "pkg.a.Base")

        upsert_inheritance_pin(db, child["id"], "Base", base["id"], "main")
        db.commit()
        resolve_all_inherits(db, records, branch="main")
        db.commit()

        pin = get_inheritance_pin(db, child["id"], "Base", "main")
        fresh_result = DeprocResolutionAdapter(db, "main").resolve_python_class_mro(
            child["id"]
        )

        assert pin is not None
        assert fresh_result.status is ResolutionStatus.RESOLVED
        assert fresh_result.mro_ids == (child["id"], base["id"])

    def test_ancestor_python_pin_propagates_to_descendant_mro(self, db, monkeypatch):
        project = _write_project(
            {
                "pkg/a.py": "class Base:\n    def from_a(self):\n        pass\n",
                "pkg/b.py": "class Base:\n    def from_b(self):\n        pass\n",
                "pkg/parent.py": (
                    "from .a import *\n"
                    "from .b import *\n"
                    "class Parent(Base):\n"
                    "    pass\n"
                ),
                "pkg/child.py": (
                    "from .parent import Parent\nclass Child(Parent):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)
        parent = next(r for r in records if r["full_path"] == "pkg.parent.Parent")
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        base = next(r for r in records if r["full_path"] == "pkg.a.Base")

        upsert_inheritance_pin(db, parent["id"], "Base", base["id"], "main")
        db.commit()

        results = resolve_all_inherits(db, records, branch="main")
        db.commit()
        adapter = DeprocResolutionAdapter(db, "main")
        resolver = adapter.context.get_resolver("python")
        assert resolver is not None
        unpinned_result = resolver.resolve_class_mro(child["id"], adapter.context)
        fresh_result = adapter.resolve_python_class_mro(child["id"])

        assert results[parent["id"]].mro_ids == (parent["id"], base["id"])
        assert results[child["id"]].status is ResolutionStatus.RESOLVED
        assert results[child["id"]].mro_ids == (
            child["id"],
            parent["id"],
            base["id"],
        )
        assert unpinned_result.status is ResolutionStatus.AMBIGUOUS
        assert fresh_result.status is ResolutionStatus.RESOLVED
        assert fresh_result.mro_ids == results[child["id"]].mro_ids

        fresh_members = DeprocResolutionAdapter(
            db, "main"
        ).get_python_inherited_members(child["id"])
        assert fresh_members.status is ResolutionStatus.RESOLVED
        assert fresh_members.mro_ids == fresh_result.mro_ids
        assert [(member.name, member.owner_id) for member in fresh_members.members] == [
            ("from_a", base["id"])
        ]

        monkeypatch.setattr(
            "deputy.tools.inheritance.get_current_branch", lambda: "main"
        )
        info = get_class_inheritance_info(db, child["id"])
        assert [member["id"] for member in info["inherited_members"]["METHOD"]] == [
            next(r for r in records if r["full_path"] == "pkg.a.Base.from_a")["id"]
        ]

    def test_inherited_inner_type_is_presented_as_inner_type(self, db, monkeypatch):
        project = _write_project(
            {
                "pkg/base.py": ("class Base:\n    class Inner:\n        pass\n"),
                "pkg/child.py": (
                    "from .base import Base\nclass Child(Base):\n    pass\n"
                ),
            }
        )
        records = _run_sync(db, project)
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        inner = next(r for r in records if r["full_path"] == "pkg.base.Base.Inner")

        adapter = DeprocResolutionAdapter(db, "main")
        mro = adapter.resolve_python_class_mro(child["id"])
        semantic_members = adapter.get_python_inherited_members(child["id"], mro)

        assert semantic_members.status is ResolutionStatus.RESOLVED
        assert [
            (member.name, member.member_id) for member in semantic_members.members
        ] == [("Inner", inner["id"])]

        monkeypatch.setattr(
            "deputy.tools.inheritance.get_current_branch", lambda: "main"
        )
        info = get_class_inheritance_info(db, child["id"])
        assert [member["id"] for member in info["inherited_members"]["INNER_TYPE"]] == [
            inner["id"]
        ]
        assert "CLASS" not in info["inherited_members"]

    def test_pin_inheritance_uses_deproc_alias_identity(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        (project / "pkg").mkdir()
        (project / "pkg/a.py").write_text("class Base:\n    pass\n")
        (project / "pkg/child.py").write_text(
            "from .a import Base\nclass Child(Base):\n    pass\n"
        )
        db_path = tmp_path / "deputy.db"
        conn = open_database(str(db_path))
        init_schema(conn)
        records = _run_sync(conn, str(project))
        conn.close()

        calls = []
        original = DeprocResolutionAdapter.resolve_python_import_alias

        def resolve_alias(adapter, alias_id):
            calls.append(alias_id)
            return original(adapter, alias_id)

        monkeypatch.setattr(
            DeprocResolutionAdapter,
            "resolve_python_import_alias",
            resolve_alias,
        )
        monkeypatch.setattr(
            "deputy._open_database", lambda: open_database(str(db_path))
        )
        monkeypatch.setattr("deputy.get_current_branch", lambda: "main")

        pin_inheritance(
            "pkg.child.Child",
            "Base",
            "pkg/child.py:1",
            remove=False,
            list_pins=False,
        )

        check = open_database(str(db_path))
        child = next(r for r in records if r["full_path"] == "pkg.child.Child")
        base = next(r for r in records if r["full_path"] == "pkg.a.Base")
        pin = get_inheritance_pin(check, child["id"], "Base", "main")
        check.close()

        assert calls
        assert pin is not None
        assert pin["pinned_entity_id"] == base["id"]


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

    def test_adapter_preserves_resolved_candidates_for_mixed_visibility(self, db):
        project = _write_project(
            {
                "src/moda/module-info.java": (
                    "module mod.a { requires mod.b; requires mod.c; }\n"
                ),
                "src/moda/moda/consumer/Use.java": (
                    "package moda.consumer;\n"
                    "import static modb.api.Owner.VALUE;\n"
                    "import static modc.api.Owner.VALUE;\n"
                    "public class Use { int value = VALUE; }\n"
                ),
                "src/modb/module-info.java": ("module mod.b { exports modb.api; }\n"),
                "src/modb/modb/api/Owner.java": (
                    "package modb.api;\n"
                    "public class Owner { public static int VALUE; }\n"
                ),
                "src/modc/module-info.java": "module mod.c {}\n",
                "src/modc/modc/api/Owner.java": (
                    "package modc.api;\n"
                    "public class Owner { public static int VALUE; }\n"
                ),
            }
        )
        _run_sync(db, project)

        result = DeprocResolutionAdapter(db, "main").resolve(
            "moda.consumer.Use", "VALUE", language="java"
        )

        assert result.status is ResolutionStatus.RESOLVED
        assert result.reason is None
        assert [record["full_path"] for record in result.resolved] == [
            "modb.api.Owner.VALUE"
        ]
        assert [record["full_path"] for record in result.inaccessible] == [
            "modc.api.Owner.VALUE"
        ]
        assert result.ambiguous == ()
        assert [record["full_path"] for record in result.candidates] == [
            "modb.api.Owner.VALUE"
        ]

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
                "src/com/other/Hidden.java": ("package com.other;\nclass Hidden {}\n"),
                "src/com/example/UseHidden.java": (
                    "package com.example;\n"
                    "import com.other.Hidden;\n"
                    "public class UseHidden {}\n"
                ),
                "src/com/first/Thing.java": (
                    "package com.first;\npublic class Thing {}\n"
                ),
                "src/com/second/Thing.java": (
                    "package com.second;\npublic class Thing {}\n"
                ),
                "src/com/example/Ambiguous.java": (
                    "package com.example;\n"
                    "import com.first.*;\n"
                    "import com.second.*;\n"
                    "public class Ambiguous {}\n"
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
            assert _resolution_snapshot(original_result) == _resolution_snapshot(
                restored_result
            )

        original_hidden = _entity_by_fqn(original, "com.example.UseHidden")
        restored_hidden = _entity_by_fqn(restored, "com.example.UseHidden")
        original_hidden_result = original_resolver.resolve_type_reference(
            "Hidden", original_hidden, original
        )
        restored_hidden_result = restored_resolver.resolve_type_reference(
            "Hidden", restored_hidden, restored
        )
        assert original_hidden_result.status is ResolutionStatus.INACCESSIBLE
        assert _resolution_snapshot(original_hidden_result) == _resolution_snapshot(
            restored_hidden_result
        )

        original_ambiguous = _entity_by_fqn(original, "com.example.Ambiguous")
        restored_ambiguous = _entity_by_fqn(restored, "com.example.Ambiguous")
        original_result = original_resolver.resolve_type_reference(
            "Thing", original_ambiguous, original
        )
        restored_result = restored_resolver.resolve_type_reference(
            "Thing", restored_ambiguous, restored
        )
        assert original_result.status is ResolutionStatus.AMBIGUOUS
        assert _resolution_snapshot(original_result) == _resolution_snapshot(
            restored_result
        )

        assert original_child.superclass == restored_child.superclass
        assert original_child.implements == restored_child.implements
