import json
import os
import tempfile

from deputy.core import create_context
from deputy.database.sqlite import (
    get_entity_ids_by_fqn,
    search_entities,
    upsert_entity,
)
from deputy.tools.utils import (
    _language_for_path,
    _process_files,
    get_containing_module_fqn,
)
from deputy.utils.storage.models import FileMetadata


def _make_project(files: dict[str, str]) -> str:
    tmp = tempfile.mkdtemp()
    for rel, content in files.items():
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    return tmp


def _upsert_records(conn, records):
    for r in records:
        conn.execute(
            "INSERT OR REPLACE INTO entities (id, language, full_path, name, type, metadata_json, parent_id) VALUES (?,?,?,?,?,?,?)",
            (
                r["id"],
                r["language"],
                r["full_path"],
                r["name"],
                r["type"],
                r["metadata_json"],
                r["parent_id"],
            ),
        )
    conn.commit()


class TestLanguageDetection:
    def test_python_extension(self):
        assert _language_for_path("src/mod.py") == "python"
        assert _language_for_path("src/mod.pyi") == "python"

    def test_java_extension(self):
        assert _language_for_path("src/Main.java") == "java"

    def test_unknown_extension(self):
        assert _language_for_path("src/readme.md") is None


class TestMultiLanguageProcessFiles:
    def test_processes_both_languages(self):
        tmp = _make_project(
            {
                "src/Main.java": """
package com.example;
import java.util.List;
public class Main {
    private String name;
    public int add(int a, int b) { return a + b; }
}
""",
                "src/mod.py": "def helper():\n    pass\n",
            }
        )
        files = [
            FileMetadata(path="src/Main.java", mtime=1.0),
            FileMetadata(path="src/mod.py", mtime=1.0),
        ]
        ctx = create_context(tmp, None)
        records, rel = _process_files(ctx, files, tmp)

        langs = {r["language"] for r in records}
        assert langs == {"java", "python"}

        java_types = {r["type"] for r in records if r["language"] == "java"}
        assert {
            "CLASS",
            "METHOD",
            "FIELD",
            "IMPORT",
            "COMPILATION_UNIT",
            "PACKAGE",
        } <= java_types

        java_classes = [
            r for r in records if r["language"] == "java" and r["type"] == "CLASS"
        ]
        assert any(r["full_path"] == "com.example.Main" for r in java_classes)

        python_funcs = [
            r for r in records if r["language"] == "python" and r["type"] == "FUNCTION"
        ]
        assert any(r["full_path"] == "src.mod.helper" for r in python_funcs)

        assert rel == {"src/Main.java": "com.example.Main", "src/mod.py": "src.mod"}

    def test_java_only_project(self):
        tmp = _make_project(
            {
                "Animal.java": "abstract class Animal {\n    abstract void makeSound();\n}\n",
            }
        )
        files = [FileMetadata(path="Animal.java", mtime=1.0)]
        ctx = create_context(tmp, None)
        records, _ = _process_files(ctx, files, tmp)

        assert all(r["language"] == "java" for r in records)
        classes = [r for r in records if r["type"] == "CLASS"]
        assert len(classes) == 1
        assert classes[0]["full_path"] == "Animal"


class TestJavaModuleResolution:
    def test_containing_module_is_compilation_unit(self, db):
        tmp = _make_project(
            {
                "src/Main.java": "package com.example;\npublic class Main {\n    public int add(int a, int b) { return a + b; }\n}\n",
            }
        )
        files = [FileMetadata(path="src/Main.java", mtime=1.0)]
        ctx = create_context(tmp, db)
        records, _ = _process_files(ctx, files, tmp)
        _upsert_records(db, records)

        class_ids = get_entity_ids_by_fqn(db, "com.example.Main")
        class_id = next(iter(class_ids))
        assert get_containing_module_fqn(db, class_id) == "com.example.Main"

        method_ids = [r["id"] for r in records if r["type"] == "METHOD"]
        assert get_containing_module_fqn(db, method_ids[0]) == "com.example.Main"


class TestJavaModuleSync:
    def test_module_info_and_class(self):
        tmp = _make_project(
            {
                "src/module-info.java": """module com.example.app {
    requires java.base;
    requires transitive java.logging;
    exports com.example;
    opens com.example.internal to com.example.impl;
    uses com.example.Service;
    provides com.example.Service with com.example.impl.ServiceImpl;
}
""",
                "src/com/example/Main.java": "package com.example;\npublic class Main {\n    public void run() {}\n}\n",
            }
        )
        files = [
            FileMetadata(path="src/module-info.java", mtime=1.0),
            FileMetadata(path="src/com/example/Main.java", mtime=1.0),
        ]
        ctx = create_context(tmp, None)
        records, rel = _process_files(ctx, files, tmp)

        java_types = {r["type"] for r in records}
        assert {"MODULE", "PACKAGE", "COMPILATION_UNIT", "CLASS"} <= java_types
        assert all(r["language"] == "java" for r in records)

        modules = [r for r in records if r["type"] == "MODULE"]
        assert len(modules) == 1
        assert modules[0]["full_path"] == "com.example.app"
        assert modules[0]["name"] == "com.example.app"
        assert modules[0]["parent_id"] is None

        meta = json.loads(modules[0]["metadata_json"])
        assert meta["module_name"] == "com.example.app"
        assert meta["requires"] == ["java.base"]
        assert meta["requires_transitive"] == ["java.logging"]
        assert meta["exports"] == ["com.example"]
        assert meta["qualified_opens"] == {"com.example.internal": ["com.example.impl"]}
        assert meta["uses"] == ["com.example.Service"]
        assert meta["provides"] == {
            "com.example.Service": ["com.example.impl.ServiceImpl"]
        }
        assert meta["path"] == "src/module-info.java"

        classes = [r for r in records if r["type"] == "CLASS"]
        assert any(r["full_path"] == "com.example.Main" for r in classes)

        assert rel == {
            "src/module-info.java": "com.example.app",
            "src/com/example/Main.java": "com.example.Main",
        }

    def test_no_module_info_has_no_module_record(self):
        tmp = _make_project(
            {
                "src/Main.java": "package com.example;\npublic class Main {}\n",
            }
        )
        files = [FileMetadata(path="src/Main.java", mtime=1.0)]
        ctx = create_context(tmp, None)
        records, _ = _process_files(ctx, files, tmp)

        assert "MODULE" not in {r["type"] for r in records}
        assert any(r["type"] == "CLASS" for r in records)


class TestJavaSearch:
    def test_search_language_filter(self, db):
        tmp = _make_project(
            {
                "src/Main.java": "package com.example;\npublic class Main {\n    public void run() {}\n}\n",
            }
        )
        files = [FileMetadata(path="src/Main.java", mtime=1.0)]
        ctx = create_context(tmp, db)
        records, _ = _process_files(ctx, files, tmp)
        _upsert_records(db, records)

        results = search_entities(db, "com.example.Main", language="java", exact=True)
        assert len(results) == 2
        assert all(r["language"] == "java" for r in results)
        assert {r["type"] for r in results} == {"CLASS", "COMPILATION_UNIT"}

        results = search_entities(db, "Main", language="java")
        assert len(results) >= 1

    def test_import_excluded_from_search(self, db):
        upsert_entity(
            db,
            id="imp_1",
            language="java",
            full_path="java.util.List",
            name="List",
            type="IMPORT",
            metadata_json='{"import_kind":"single_type"}',
        )
        upsert_entity(
            db,
            id="cls_1",
            language="java",
            full_path="com.example.Main",
            name="Main",
            type="CLASS",
            metadata_json='{"fqn":"com.example.Main"}',
        )
        results = search_entities(db, "List")
        assert results == []
        results = search_entities(db, "Main")
        assert len(results) == 1
