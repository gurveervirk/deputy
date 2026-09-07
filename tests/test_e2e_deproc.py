import json
import os
import tempfile

from deputy.core import create_context
from deputy.database.sqlite import (
    get_branch_entities,
    get_direct_bases,
    upsert_branch_entities,
    upsert_entity,
)
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
from deputy.utils.storage import get_source_files


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

        missing = next(r for r in records if r["full_path"] == "com.example.Missing")
        missing_bases = get_direct_bases(db, missing["id"])
        assert len(missing_bases) == 1
        assert missing_bases[0]["is_resolved"] == 0
        assert missing_bases[0]["base_full_path"] == "NoSuchBase"
        branch_info = json.loads(missing_bases[0]["branch_info"])
        assert branch_info["status"] == "unresolved"

    def test_ambiguous_type_reference_preserved(self, db):
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
        assert bases[0]["is_resolved"] == 0
        branch_info = json.loads(bases[0]["branch_info"])
        assert branch_info["status"] == "ambiguous"
        assert len(branch_info["candidates"]) >= 2

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
