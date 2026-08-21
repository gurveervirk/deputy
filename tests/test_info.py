import json
import os
import tempfile
from unittest.mock import patch

import pytest

from deputy import _format_range, _get_column_value, _get_file_path
from deputy.database.sqlite import set_config, upsert_branch_entities, upsert_entity
from deputy.tools.core import _compute_source, get_entity_info


class TestGetFilePath:
    def test_with_lineno_suffix(self):
        assert _get_file_path("src/mod.py:10") == "src/mod.py"

    def test_without_lineno(self):
        assert _get_file_path("src/mod.py") == "src/mod.py"

    def test_empty_string(self):
        assert _get_file_path("") == ""

    def test_only_colon(self):
        assert _get_file_path(":") == ""


class TestFormatRange:
    def test_single_line(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"signature_lineno": 5, "signature_end_lineno": 5}
        assert _format_range(entity, meta, "signature") == "src/mod.py:5"

    def test_multi_line(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"signature_lineno": 5, "signature_end_lineno": 10}
        assert _format_range(entity, meta, "signature") == "src/mod.py:5-10"

    def test_no_range(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {}
        assert _format_range(entity, meta, "signature") == ""

    def test_with_extracted(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"signature_lineno": 5, "signature_end_lineno": 5}
        extracted = {"signature": "def foo():"}
        assert _format_range(entity, meta, "signature", extracted) == "def foo():"

    def test_docstring_column(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"docstring_lineno": 6, "docstring_end_lineno": 8}
        assert _format_range(entity, meta, "docstring") == "src/mod.py:6-8"

    def test_docstring_single_line(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"docstring_lineno": 6, "docstring_end_lineno": 6}
        assert _format_range(entity, meta, "docstring") == "src/mod.py:6"

    def test_no_source(self):
        entity = {"_source": ""}
        meta = {"signature_lineno": 5, "signature_end_lineno": 5}
        assert _format_range(entity, meta, "signature") == ":5"


class TestGetColumnValue:
    def test_full_path(self):
        assert (
            _get_column_value({"full_path": "mod.func"}, "full_path", {}) == "mod.func"
        )

    def test_language(self):
        assert _get_column_value({"language": "python"}, "language", {}) == "python"

    def test_type(self):
        assert _get_column_value({"type": "FUNCTION"}, "type", {}) == "FUNCTION"

    def test_lineno(self):
        assert _get_column_value({}, "lineno", {"lineno": 10}) == "10"

    def test_lineno_missing(self):
        assert _get_column_value({}, "lineno", {}) == ""

    def test_end_lineno(self):
        assert _get_column_value({}, "end_lineno", {"end_lineno": 20}) == "20"

    def test_source(self):
        assert (
            _get_column_value({"_source": "src/mod.py:10"}, "source", {})
            == "src/mod.py:10"
        )

    def test_signature_delegates_to_format_range(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"signature_lineno": 5, "signature_end_lineno": 5}
        assert _get_column_value(entity, "signature", meta) == "src/mod.py:5"

    def test_arguments_delegates_to_format_range(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"arguments_lineno": 5, "arguments_end_lineno": 5}
        assert _get_column_value(entity, "arguments", meta) == "src/mod.py:5"

    def test_return_type_delegates_to_format_range(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"return_type_lineno": 6, "return_type_end_lineno": 6}
        assert _get_column_value(entity, "return_type", meta) == "src/mod.py:6"

    def test_docstring_delegates_to_format_range(self):
        entity = {"_source": "src/mod.py:5"}
        meta = {"docstring_lineno": 6, "docstring_end_lineno": 8}
        assert _get_column_value(entity, "docstring", meta) == "src/mod.py:6-8"

    def test_decorators(self):
        assert (
            _get_column_value(
                {}, "decorators", {"decorators": ["staticmethod", "property"]}
            )
            == "staticmethod, property"
        )

    def test_decorators_empty(self):
        assert _get_column_value({}, "decorators", {"decorators": []}) == ""

    def test_decorators_missing(self):
        assert _get_column_value({}, "decorators", {}) == ""

    def test_parent_classes(self):
        assert (
            _get_column_value(
                {}, "parent_classes", {"parent_classes": ["Base1", "Base2"]}
            )
            == "Base1, Base2"
        )

    def test_visibility(self):
        assert _get_column_value({}, "visibility", {"visibility": "public"}) == "public"

    def test_exported(self):
        assert _get_column_value({}, "exported", {"exported": True}) == "True"

    def test_exported_false(self):
        assert _get_column_value({}, "exported", {"exported": False}) == "False"

    def test_exported_missing(self):
        assert _get_column_value({}, "exported", {}) == ""

    def test_module_list_columns(self):
        meta = {
            "requires": ["java.base", "java.logging"],
            "requires_static": ["java.sql"],
            "requires_transitive": ["java.logging"],
            "exports": ["com.example.models"],
            "opens": ["com.example.service"],
            "uses": ["com.example.service.Zoo"],
        }
        for col in (
            "requires",
            "requires_static",
            "requires_transitive",
            "exports",
            "opens",
            "uses",
        ):
            assert _get_column_value({}, col, meta) == ", ".join(meta[col])

    def test_module_list_columns_empty(self):
        meta = {"requires": [], "exports": [], "uses": []}
        for col in ("requires", "exports", "uses"):
            assert _get_column_value({}, col, meta) == ""

    def test_module_list_columns_missing(self):
        for col in ("requires", "exports", "uses"):
            assert _get_column_value({}, col, {}) == ""

    def test_module_mapping_columns(self):
        meta = {
            "qualified_exports": {"com.example.service": ["com.example.consumer"]},
            "qualified_opens": {"com.example.models": ["com.example.consumer"]},
            "provides": {"com.example.models.Runnable": ["com.example.service.Zoo"]},
        }
        assert (
            _get_column_value({}, "qualified_exports", meta)
            == "com.example.service -> com.example.consumer"
        )
        assert (
            _get_column_value({}, "qualified_opens", meta)
            == "com.example.models -> com.example.consumer"
        )
        assert (
            _get_column_value({}, "provides", meta)
            == "com.example.models.Runnable -> com.example.service.Zoo"
        )

    def test_module_mapping_columns_empty(self):
        assert _get_column_value({}, "provides", {"provides": {}}) == ""

    def test_module_mapping_columns_missing(self):
        assert _get_column_value({}, "provides", {}) == ""


class TestComputeSource:
    def test_module_with_path_no_lineno(self, db):
        db.execute(
            "INSERT INTO entities (id, language, full_path, name, type, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "mod1",
                "python",
                "pkg.mod",
                "mod",
                "PYTHON_MODULE",
                '{"fqn":"pkg.mod","path":"pkg/mod.py"}',
            ),
        )
        entity = dict(db.execute("SELECT * FROM entities WHERE id='mod1'").fetchone())
        assert _compute_source(entity, db) == "pkg/mod.py"

    def test_non_module_with_source_id(self, db):
        db.execute(
            "INSERT INTO entities (id, language, full_path, name, type, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "mod1",
                "python",
                "pkg.mod",
                "mod",
                "PYTHON_MODULE",
                '{"fqn":"pkg.mod","path":"pkg/mod.py"}',
            ),
        )
        db.execute(
            "INSERT INTO entities (id, language, full_path, name, type, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "func1",
                "python",
                "pkg.mod.func",
                "func",
                "FUNCTION",
                '{"fqn":"pkg.mod.func","lineno":5,"source_id":"mod1"}',
            ),
        )
        entity = dict(db.execute("SELECT * FROM entities WHERE id='func1'").fetchone())
        assert _compute_source(entity, db) == "pkg/mod.py:5"

    def test_non_module_no_source_id(self, db):
        db.execute(
            "INSERT INTO entities (id, language, full_path, name, type, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "func1",
                "python",
                "pkg.mod.func",
                "func",
                "FUNCTION",
                '{"fqn":"pkg.mod.func","lineno":5}',
            ),
        )
        entity = dict(db.execute("SELECT * FROM entities WHERE id='func1'").fetchone())
        assert _compute_source(entity, db) == ""

    def test_no_lineno(self, db):
        db.execute(
            "INSERT INTO entities (id, language, full_path, name, type, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "func1",
                "python",
                "pkg.mod.func",
                "func",
                "FUNCTION",
                '{"fqn":"pkg.mod.func"}',
            ),
        )
        entity = dict(db.execute("SELECT * FROM entities WHERE id='func1'").fetchone())
        assert _compute_source(entity, db) == ""

    def test_unknown_source_id(self, db):
        db.execute(
            "INSERT INTO entities (id, language, full_path, name, type, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "func1",
                "python",
                "pkg.mod.func",
                "func",
                "FUNCTION",
                '{"fqn":"pkg.mod.func","lineno":5,"source_id":"nonexistent"}',
            ),
        )
        entity = dict(db.execute("SELECT * FROM entities WHERE id='func1'").fetchone())
        assert _compute_source(entity, db) == ""


MOCK_CONFIG = {}
MOCK_BRANCH = "main"


class TestGetEntityInfo:
    @pytest.fixture
    def info_db(self):
        from deputy.database.sqlite import init_schema, open_database

        conn = open_database(":memory:")
        init_schema(conn)
        upsert_entity(
            conn,
            id="e1",
            language="python",
            full_path="mod.func",
            name="func",
            type="FUNCTION",
            metadata_json='{"fqn":"mod.func","lineno":5}',
        )
        upsert_entity(
            conn,
            id="e2",
            language="python",
            full_path="mod.func",
            name="func",
            type="CLASS",
            metadata_json='{"fqn":"mod.func","lineno":10}',
        )
        upsert_entity(
            conn,
            id="e3",
            language="python",
            full_path="mod.other",
            name="other",
            type="FUNCTION",
            metadata_json='{"fqn":"mod.other","lineno":15}',
        )
        upsert_branch_entities(conn, "main", ["e1", "e2", "e3"])
        conn.commit()
        return conn

    def _setup_mocks(self, info_db):
        stack = [
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ]
        for s in stack:
            s.start()
        self.addCleanup(lambda: [s.stop() for s in stack])

    def test_single_unique_match(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.other")
            assert result is not None
            assert result["full_path"] == "mod.other"
            assert result["_match_count"] == 1
            assert "_source" in result

    def test_multiple_matches_sets_count(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.func")
            assert result is not None
            assert result["_match_count"] == 2

    def test_no_match_returns_none(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.nonexistent")
            assert result is None

    def test_all_matches_returns_list(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            results = get_entity_info("mod.func", all_matches=True)
            assert isinstance(results, list)
            assert len(results) == 2

    def test_all_matches_no_results(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            results = get_entity_info("mod.nonexistent", all_matches=True)
            assert isinstance(results, list)
            assert len(results) == 0

    def test_type_filter_narrows(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.func", type_filter="CLASS")
            assert result["type"] == "CLASS"
            assert result["_match_count"] == 1

    def test_lineno_filter_narrows(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.func", lineno=10)
            assert result["full_path"] == "mod.func"
            meta = json.loads(result["metadata_json"])
            assert meta["lineno"] == 10

    def test_combined_filters(self, info_db):
        upsert_entity(
            info_db,
            id="e4",
            language="python",
            full_path="mod.func",
            name="func",
            type="FUNCTION",
            metadata_json='{"fqn":"mod.func","lineno":20}',
        )
        upsert_branch_entities(info_db, "main", ["e4"])
        info_db.commit()
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.func", type_filter="FUNCTION", lineno=20)
            assert result is not None
            assert result["id"] == "e4"

    def test_no_results_with_filters(self, info_db):
        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.func", type_filter="VARIABLE")
            assert result is None

    def test_extract_populates_extracted(self, info_db):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def hash_password(password: str) -> str:\n    return ''\n")
            tmp_path = f.name
        subdir = os.path.dirname(tmp_path)
        try:
            set_config(info_db, "base_path", subdir)
            info_db.commit()

            source_meta = json.dumps({"fqn": "mod", "path": os.path.basename(tmp_path)})
            upsert_entity(
                info_db,
                id="mod1",
                language="python",
                full_path="mod",
                name="mod",
                type="PYTHON_MODULE",
                metadata_json=source_meta,
            )
            upsert_branch_entities(info_db, "main", ["mod1"])

            func_meta = json.dumps(
                {
                    "fqn": "mod.func",
                    "lineno": 1,
                    "end_lineno": 2,
                    "source_id": "mod1",
                    "signature_lineno": 1,
                    "signature_end_lineno": 1,
                    "arguments_lineno": 1,
                    "arguments_end_lineno": 1,
                    "return_type_lineno": 1,
                    "return_type_end_lineno": 1,
                }
            )
            upsert_entity(
                info_db,
                id="func1",
                language="python",
                full_path="mod.func",
                name="func",
                type="FUNCTION",
                metadata_json=func_meta,
            )
            upsert_branch_entities(info_db, "main", ["func1"])
            info_db.commit()

            with (
                patch("deputy.tools.core._open_database", return_value=info_db),
                patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
                patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
            ):
                result = get_entity_info("mod.func", extract=True)
                assert result is not None
                extracted = result.get("_extracted", {})
                assert "signature" in extracted
                assert "def hash_password" in extracted["signature"]
        finally:
            os.unlink(tmp_path)

    def test_extract_no_file_graceful(self, info_db):
        upsert_entity(
            info_db,
            id="mod1",
            language="python",
            full_path="mod",
            name="mod",
            type="PYTHON_MODULE",
            metadata_json='{"fqn":"mod","path":"nonexistent.py"}',
        )
        upsert_branch_entities(info_db, "main", ["mod1"])
        func_meta = json.dumps(
            {
                "fqn": "mod.func",
                "lineno": 1,
                "source_id": "mod1",
                "signature_lineno": 1,
                "signature_end_lineno": 1,
            }
        )
        upsert_entity(
            info_db,
            id="func1",
            language="python",
            full_path="mod.func",
            name="func",
            type="FUNCTION",
            metadata_json=func_meta,
        )
        upsert_branch_entities(info_db, "main", ["func1"])
        info_db.commit()

        with (
            patch("deputy.tools.core._open_database", return_value=info_db),
            patch("deputy.tools.core.get_current_branch", return_value=MOCK_BRANCH),
            patch("deputy.tools.core.read_config", return_value=MOCK_CONFIG),
        ):
            result = get_entity_info("mod.func", extract=True)
            assert result is not None
            assert result.get("_extracted", {}) == {}
