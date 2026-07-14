import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from deproc.core.interfaces.parser.models import FunctionLike
from deputy.database.sqlite import (
    delete_entities_by_package,
    delete_dependency,
    get_dependency,
    list_dependencies,
    upsert_dependency,
    upsert_entity,
    get_entity_by_path,
)
from deputy.tools.utils import _entity_record
from deputy.utils.config_file import get_config, read_config, write_config
from deputy.venv.detect import detect_venv, parse_pyvenv_cfg, _is_venv
from deputy.venv.discovery import (
    find_site_packages,
    list_installed_packages,
    _parse_metadata,
    _parse_top_level,
)


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


class TestDetectVenv:
    def test_detect_venv_no_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                result = detect_venv(tmp, {})
                assert result is None

    def test_detect_venv_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = os.path.join(tmp, ".venv")
            os.mkdir(venv_dir)
            open(os.path.join(venv_dir, "pyvenv.cfg"), "w").close()
            result = detect_venv(tmp, {"venv_path": venv_dir})
            assert result == os.path.abspath(venv_dir)

    def test_detect_venv_base_path_convention(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = os.path.join(tmp, ".venv")
            os.mkdir(venv_dir)
            open(os.path.join(venv_dir, "pyvenv.cfg"), "w").close()
            with patch.dict(os.environ, {}, clear=True):
                result = detect_venv(tmp, {})
                assert result == os.path.abspath(venv_dir)

    def test_is_venv_with_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert not _is_venv(tmp)
            open(os.path.join(tmp, "pyvenv.cfg"), "w").close()
            assert _is_venv(tmp)

    def test_parse_pyvenv_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "pyvenv.cfg")
            with open(cfg_path, "w") as f:
                f.write("home = /usr/bin\nversion = 3.12\ninclude-system-site-packages = false\n")
            result = parse_pyvenv_cfg(tmp)
            assert result["home"] == "/usr/bin"
            assert result["version"] == "3.12"
            assert result["include-system-site-packages"] == "false"

    def test_parse_pyvenv_cfg_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = parse_pyvenv_cfg(tmp)
            assert result == {}


class TestDiscovery:
    def test_find_site_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib_python = os.path.join(tmp, "lib", "python3.12", "site-packages")
            os.makedirs(lib_python)
            result = find_site_packages(tmp)
            assert result == os.path.abspath(lib_python)

    def test_find_site_packages_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = find_site_packages(tmp)
            assert result is None

    def test_parse_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta_path = os.path.join(tmp, "METADATA")
            with open(meta_path, "w") as f:
                f.write("Name: requests\nVersion: 2.31.0\nSummary: HTTP library\n\n")
            result = _parse_metadata(meta_path)
            assert result["Name"] == "requests"
            assert result["Version"] == "2.31.0"

    def test_parse_metadata_missing(self):
        result = _parse_metadata("/nonexistent")
        assert result == {}

    def test_parse_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            tl_path = os.path.join(tmp, "top_level.txt")
            with open(tl_path, "w") as f:
                f.write("requests\nrequests.models\n")
            result = _parse_top_level(tl_path)
            assert result == ["requests", "requests.models"]

    def test_parse_top_level_missing(self):
        result = _parse_top_level("/nonexistent")
        assert result == []

    def test_list_installed_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist_info = os.path.join(tmp, "requests-2.31.0.dist-info")
            os.mkdir(dist_info)
            with open(os.path.join(dist_info, "METADATA"), "w") as f:
                f.write("Name: requests\nVersion: 2.31.0\n\n")
            with open(os.path.join(dist_info, "top_level.txt"), "w") as f:
                f.write("requests\n")
            results = list_installed_packages(tmp)
            assert len(results) == 1
            assert results[0].name == "requests"
            assert results[0].version == "2.31.0"
            assert results[0].top_level_modules == ["requests"]
            assert results[0].editable_origin is None


class TestDatabaseDeps:
    def test_upsert_and_get_dependency(self, db):
        upsert_dependency(db, "requests", "2.31.0", "/path", "requests", "venv", '{}', 100.0)
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
        upsert_entity(db, id="d1", language="python", full_path="requests.get", name="get", type="FUNCTION",
                      metadata_json='{"source":"dependency","package_name":"requests","fqn":"requests.get"}')
        upsert_entity(db, id="d2", language="python", full_path="requests.post", name="post", type="FUNCTION",
                      metadata_json='{"source":"dependency","package_name":"requests","fqn":"requests.post"}')
        upsert_entity(db, id="p1", language="python", full_path="mymod.func", name="func", type="FUNCTION",
                      metadata_json='{"source":"project","fqn":"mymod.func"}')
        delete_entities_by_package(db, "requests")
        assert get_entity_by_path(db, "requests.get") is None
        assert get_entity_by_path(db, "requests.post") is None
        assert get_entity_by_path(db, "mymod.func") is not None

    def test_delete_entities_by_package_no_match(self, db):
        upsert_entity(db, id="p1", language="python", full_path="mymod.func", name="func", type="FUNCTION",
                      metadata_json='{"source":"project","fqn":"mymod.func"}')
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

        record = _entity_record(entity, self._registry([]), {}, source="dependency", package_name="requests", is_stub=True)
        meta = json.loads(record["metadata_json"])
        assert meta["source"] == "dependency"
        assert meta["package_name"] == "requests"
        assert meta["is_stub"] is True

    def test_dependency_without_package_name(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.f"
        entity.type = "FUNCTION"

        record = _entity_record(entity, self._registry([]), {}, source="dependency", package_name=None)
        meta = json.loads(record["metadata_json"])
        assert meta["source"] == "dependency"
        assert "package_name" not in meta

    def test_project_ignores_source_param(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.f"
        entity.type = "FUNCTION"

        record = _entity_record(entity, self._registry([]), {}, source="project", package_name="ignored")
        meta = json.loads(record["metadata_json"])
        assert "source" not in meta
        assert "package_name" not in meta
