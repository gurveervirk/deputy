import json
import os
import tempfile
from unittest.mock import MagicMock

from deproc.core.interfaces.parser.models import FunctionLike

from deputy.database.sqlite import (
    delete_dependency,
    delete_entities_by_package,
    get_dependency,
    get_entity_by_path,
    list_dependencies,
    upsert_dependency,
    upsert_entity,
)
from deputy.tools.utils import _entity_record
from deputy.utils.config_file import get_config, read_config, write_config


class TestConfigFile:
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                write_config("db_path", "/tmp/test.db")
                write_config("venv_path", "/tmp/.venv")
                cfg = read_config()
                assert cfg["db_path"] == "/tmp/test.db"
                assert cfg["venv_path"] == "/tmp/.venv"
                assert get_config("db_path") == "/tmp/test.db"
                assert get_config("nonexistent") is None
            finally:
                os.chdir(old_cwd)

    def test_legacy_single_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, ".deputyconfig")
            with open(cfg_path, "w") as f:
                f.write("/some/path.db")
            old = os.getcwd()
            os.chdir(tmp)
            try:
                cfg = read_config()
                assert cfg == {"db_path": "/some/path.db"}
            finally:
                os.chdir(old)

    def test_empty_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                cfg = read_config()
                assert cfg == {}
                assert get_config("anything") is None
            finally:
                os.chdir(old)


class TestDatabaseDeps:
    def test_upsert_and_get_dependency(self, db):
        upsert_dependency(
            db, "requests", "2.31.0", "/path", "requests", "venv", "{}", 100.0
        )
        row = get_dependency(db, "requests")
        assert row["version"] == "2.31.0"
        assert row["install_path"] == "/path"

    def test_upsert_replaces_dependency(self, db):
        upsert_dependency(db, "flask", "2.0", "/a", "flask", "venv", "{}", 1.0)
        upsert_dependency(db, "flask", "3.0", "/b", "flask", "venv", "{}", 2.0)
        row = get_dependency(db, "flask")
        assert row["version"] == "3.0"

    def test_get_missing_dependency(self, db):
        assert get_dependency(db, "nonexistent") is None

    def test_delete_dependency(self, db):
        upsert_dependency(db, "pkg", "1.0", "/p", "pkg", "venv", "{}", 1.0)
        delete_dependency(db, "pkg")
        assert get_dependency(db, "pkg") is None

    def test_list_dependencies(self, db):
        upsert_dependency(db, "a", "1", "/a", "a", "venv", "{}", 1.0)
        upsert_dependency(db, "b", "2", "/b", "b", "venv", "{}", 2.0)
        deps = list_dependencies(db)
        assert len(deps) == 2
        assert deps[0]["package_name"] == "a"

    def test_delete_entities_by_package(self, db):
        upsert_entity(
            db,
            id="d1",
            language="python",
            full_path="requests.get",
            name="get",
            type="FUNCTION",
            metadata_json='{"source":"dependency","package_name":"requests","fqn":"requests.get"}',
        )
        upsert_entity(
            db,
            id="d2",
            language="python",
            full_path="requests.post",
            name="post",
            type="FUNCTION",
            metadata_json='{"source":"dependency","package_name":"requests","fqn":"requests.post"}',
        )
        upsert_entity(
            db,
            id="p1",
            language="python",
            full_path="mymod.func",
            name="func",
            type="FUNCTION",
            metadata_json='{"source":"project","fqn":"mymod.func"}',
        )
        delete_entities_by_package(db, "requests")
        assert get_entity_by_path(db, "requests.get") is None
        assert get_entity_by_path(db, "requests.post") is None
        assert get_entity_by_path(db, "mymod.func") is not None

    def test_delete_entities_by_package_no_match(self, db):
        upsert_entity(
            db,
            id="p1",
            language="python",
            full_path="mymod.func",
            name="func",
            type="FUNCTION",
            metadata_json='{"source":"project","fqn":"mymod.func"}',
        )
        delete_entities_by_package(db, "nonexistent")
        assert get_entity_by_path(db, "mymod.func") is not None


class TestEntityRecordSource:
    def _registry(self, values):
        r = MagicMock(spec=["values"])
        r.values = lambda: values
        return r

    def test_default_is_project_no_change(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.f"
        entity.type = "FUNCTION"

        record = _entity_record(entity, self._registry([]), {})
        meta = json.loads(record["metadata_json"])
        assert "source" not in meta

    def test_dependency_source_added(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.f"
        entity.type = "FUNCTION"

        record = _entity_record(
            entity,
            self._registry([]),
            {},
            source="dependency",
            package_name="requests",
            is_stub=True,
        )
        meta = json.loads(record["metadata_json"])
        assert meta["source"] == "dependency"
        assert meta["package_name"] == "requests"
        assert meta["is_stub"] is True

    def test_dependency_without_package_name(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.f"
        entity.type = "FUNCTION"

        record = _entity_record(
            entity, self._registry([]), {}, source="dependency", package_name=None
        )
        meta = json.loads(record["metadata_json"])
        assert meta["source"] == "dependency"
        assert "package_name" not in meta

    def test_project_ignores_source_param(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.f"
        entity.type = "FUNCTION"

        record = _entity_record(
            entity, self._registry([]), {}, source="project", package_name="ignored"
        )
        meta = json.loads(record["metadata_json"])
        assert "source" not in meta
        assert "package_name" not in meta
