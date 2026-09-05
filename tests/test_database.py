from unittest.mock import patch

from deputy.database.sqlite import (
    clean_orphan_entities,
    delete_branch_entities,
    delete_branch_file,
    delete_class_bases_by_class,
    delete_entity_by_module_fqn,
    delete_inheritance_pin,
    get_branch_entities,
    get_branch_files,
    get_config,
    get_direct_bases,
    get_direct_subclasses,
    get_entities_by_ids,
    get_entities_by_path,
    get_entity_by_id,
    get_entity_by_path,
    get_entity_ids_by_fqn,
    get_filtered_entities_by_path,
    get_inheritance_pin,
    get_transitive_subclasses,
    list_inheritance_pins,
    search_entities,
    set_config,
    update_mtime,
    upsert_branch_entities,
    upsert_branch_file,
    upsert_class_bases,
    upsert_entity,
    upsert_inheritance_pin,
)
from deputy.tools.utils import _detect_file_changes
from deputy.utils.storage.models import FileMetadata


class TestBranchFiles:
    def test_upsert_and_get(self, db):
        upsert_branch_file(db, "main", "a.py", "hash1", 100.0)
        upsert_branch_file(db, "main", "b.py", "hash2", 200.0)
        tracked = get_branch_files(db, "main")
        assert tracked == {"a.py": ("hash1", 100.0), "b.py": ("hash2", 200.0)}

    def test_upsert_replaces(self, db):
        upsert_branch_file(db, "main", "a.py", "hash1", 100.0)
        upsert_branch_file(db, "main", "a.py", "hash2", 200.0)
        tracked = get_branch_files(db, "main")
        assert tracked == {"a.py": ("hash2", 200.0)}

    def test_isolated_by_branch(self, db):
        upsert_branch_file(db, "main", "a.py", "h1", 1.0)
        upsert_branch_file(db, "other", "a.py", "h2", 2.0)
        assert len(get_branch_files(db, "main")) == 1

    def test_delete(self, db):
        upsert_branch_file(db, "main", "a.py", "h1", 1.0)
        upsert_branch_file(db, "main", "b.py", "h2", 2.0)
        delete_branch_file(db, "main", "a.py")
        assert list(get_branch_files(db, "main")) == ["b.py"]

    def test_update_mtime(self, db):
        upsert_branch_file(db, "main", "a.py", "h1", 1.0)
        update_mtime(db, "main", "a.py", 99.0)
        assert get_branch_files(db, "main")["a.py"][1] == 99.0


class TestEntities:
    def test_upsert_entity(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.f",
            name="f",
            type="FUNCTION",
            metadata_json='{"fqn":"mod.f"}',
        )
        row = get_entity_by_path(db, "mod.f")
        assert row["name"] == "f"

    def test_upsert_replaces(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.f",
            name="f",
            type="FUNCTION",
            metadata_json='{"fqn":"mod.f","a":1}',
        )
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.f",
            name="f",
            type="FUNCTION",
            metadata_json='{"fqn":"mod.f","a":2}',
        )
        row = get_entity_by_id(db, "e1")
        assert row["metadata_json"] == '{"fqn":"mod.f","a":2}'

    def test_get_entities_by_path_multiple(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json="{}",
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="CLASS",
            metadata_json="{}",
        )
        rows = get_entities_by_path(db, "mod.dup")
        assert len(rows) == 2

    def test_get_entity_by_path_returns_first(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json="{}",
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="CLASS",
            metadata_json="{}",
        )
        row = get_entity_by_path(db, "mod.dup")
        assert row["id"] == "e1"

    def test_get_entity_ids_by_fqn(self, db, sample_entities):
        row = get_entity_by_path(db, "pkg.mod.ClassA")
        assert row is not None, "no data in db"
        ids = get_entity_ids_by_fqn(db, "pkg.mod.ClassA")
        assert ids == {"id1"}

    def test_get_entities_by_ids(self, db, sample_entities):
        rows = get_entities_by_ids(db, {"id1", "id3", "nonexistent"})
        ids = {r["id"] for r in rows}
        assert ids == {"id1", "id3"}

    def test_delete_entity_by_module_fqn(self, db, sample_entities):
        delete_entity_by_module_fqn(db, "pkg.mod")
        assert get_entity_by_path(db, "pkg.mod.ClassA") is None
        assert get_entity_by_path(db, "pkg.mod.func") is None
        assert get_entity_by_path(db, "pkg.mod2.ClassA") is not None

    def test_delete_entity_by_module_fqn_exact(self, db, sample_entities):
        delete_entity_by_module_fqn(db, "pkg.mod2")
        assert get_entity_by_path(db, "pkg.mod2") is None
        assert get_entity_by_path(db, "pkg.mod2.ClassA") is None
        assert get_entity_by_path(db, "pkg.mod.ClassA") is not None

    def test_get_entities_by_path_sorted_by_lineno(self, db):
        upsert_entity(
            db,
            id="e3",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":30}',
        )
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":10}',
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":20}',
        )
        rows = get_entities_by_path(db, "mod.dup")
        assert [r["id"] for r in rows] == ["e1", "e2", "e3"]


class TestConfig:
    def test_set_and_get(self, db):
        set_config(db, "key1", "val1")
        set_config(db, "key2", "val2")
        assert get_config(db, "key1") == "val1"
        assert get_config(db, "key2") == "val2"

    def test_get_missing(self, db):
        assert get_config(db, "nonexistent") is None

    def test_set_replaces(self, db):
        set_config(db, "key", "old")
        set_config(db, "key", "new")
        assert get_config(db, "key") == "new"


class TestBranchEntities:
    def test_get_branch_entities_is_scoped(self, db, sample_entities):
        upsert_branch_entities(db, "main", ["id1", "id2"])
        upsert_branch_entities(db, "other", ["id3"])
        rows = get_branch_entities(db, "main")
        assert {row["id"] for row in rows} == {"id1", "id2"}

    def test_upsert_and_delete(self, db):
        upsert_branch_entities(db, "main", ["a", "b", "c"])
        upsert_branch_entities(db, "other", ["a", "d"])
        delete_branch_entities(db, "main")
        rows = db.execute("SELECT * FROM branch_entities").fetchall()
        assert len(rows) == 2
        assert all(r["branch_name"] == "other" for r in rows)

    def test_upsert_ignores_duplicates(self, db):
        upsert_branch_entities(db, "main", ["a", "a", "b"])
        rows = db.execute("SELECT * FROM branch_entities").fetchall()
        assert len(rows) == 2

    def test_clean_orphan_entities(self, db, sample_entities):
        upsert_branch_entities(db, "main", ["id1", "id2"])
        clean_orphan_entities(db)
        remaining = {r["id"] for r in db.execute("SELECT id FROM entities").fetchall()}
        assert remaining == {"id1", "id2"}

    def test_search_scoped_by_branch(self, db, sample_entities):
        upsert_branch_entities(db, "branch-a", ["id1", "id2"])
        upsert_branch_entities(db, "branch-b", ["id3"])
        results = search_entities(db, ".*")
        assert len(results) == 5
        results_a = search_entities(db, ".*", branch_name="branch-a")
        assert len(results_a) == 2
        results_b = search_entities(db, ".*", branch_name="branch-b")
        assert len(results_b) == 1

    def test_get_entity_by_path_scoped(self, db, sample_entities):
        upsert_branch_entities(db, "branch-a", ["id5"])
        row = get_entity_by_path(db, "pkg.mod2")
        assert row is not None
        row = get_entity_by_path(db, "pkg.mod2", branch_name="branch-a")
        assert row is not None
        row = get_entity_by_path(db, "pkg.mod2", branch_name="branch-b")
        assert row is None

    def test_get_entities_by_path_scoped(self, db, sample_entities):
        upsert_branch_entities(db, "branch-a", ["id1"])
        rows = get_entities_by_path(db, "pkg.mod.ClassA", branch_name="branch-a")
        assert len(rows) == 1
        rows = get_entities_by_path(db, "pkg.mod.ClassA", branch_name="branch-b")
        assert len(rows) == 0


class TestFilteredEntitiesByPath:
    def test_type_filter(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":10}',
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="CLASS",
            metadata_json='{"lineno":20}',
        )
        rows = get_filtered_entities_by_path(db, "mod.dup", type_filter="FUNCTION")
        assert len(rows) == 1
        assert rows[0]["id"] == "e1"

    def test_lineno_filter(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":10}',
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="CLASS",
            metadata_json='{"lineno":20}',
        )
        rows = get_filtered_entities_by_path(db, "mod.dup", lineno=20)
        assert len(rows) == 1
        assert rows[0]["id"] == "e2"

    def test_type_and_lineno(self, db):
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":10}',
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="CLASS",
            metadata_json='{"lineno":20}',
        )
        upsert_entity(
            db,
            id="e3",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":30}',
        )
        rows = get_filtered_entities_by_path(
            db, "mod.dup", type_filter="FUNCTION", lineno=30
        )
        assert len(rows) == 1
        assert rows[0]["id"] == "e3"

    def test_no_match(self, db):
        rows = get_filtered_entities_by_path(db, "mod.dup", type_filter="VARIABLE")
        assert len(rows) == 0

    def test_scoped_by_branch(self, db, sample_entities):
        upsert_branch_entities(db, "feature", ["id1", "id2"])
        rows = get_filtered_entities_by_path(
            db, "pkg.mod.ClassA", branch_name="feature", type_filter="CLASS"
        )
        assert len(rows) == 1
        assert rows[0]["id"] == "id1"
        rows = get_filtered_entities_by_path(
            db, "pkg.mod.ClassA", branch_name="other", type_filter="CLASS"
        )
        assert len(rows) == 0

    def test_sorted_by_lineno(self, db):
        upsert_entity(
            db,
            id="e3",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":30}',
        )
        upsert_entity(
            db,
            id="e1",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":10}',
        )
        upsert_entity(
            db,
            id="e2",
            language="python",
            full_path="mod.dup",
            name="dup",
            type="FUNCTION",
            metadata_json='{"lineno":20}',
        )
        rows = get_filtered_entities_by_path(db, "mod.dup")
        assert [r["id"] for r in rows] == ["e1", "e2", "e3"]


class TestSearchFilters:
    def test_type_filter(self, db, sample_entities):
        results = search_entities(db, ".*", type_filter=["CLASS"])
        assert all(r["type"] == "CLASS" for r in results)
        assert len(results) == 2

    def test_type_filter_multiple(self, db, sample_entities):
        results = search_entities(db, ".*", type_filter=["CLASS", "FUNCTION"])
        types = {r["type"] for r in results}
        assert types == {"CLASS", "FUNCTION"}

    def test_language_filter(self, db, sample_entities):
        results = search_entities(db, ".*", language="python")
        assert len(results) == 5

    def test_language_filter_no_match(self, db, sample_entities):
        results = search_entities(db, ".*", language="rust")
        assert len(results) == 0

    def test_limit(self, db, sample_entities):
        results = search_entities(db, ".*", limit=2)
        assert len(results) == 2

    def test_offset(self, db, sample_entities):
        all_results = search_entities(db, ".*")
        offset_results = search_entities(db, ".*", offset=2)
        assert len(offset_results) == len(all_results) - 2
        assert offset_results[0]["id"] == all_results[2]["id"]

    def test_exact_match(self, db, sample_entities):
        results = search_entities(db, "pkg.mod.ClassA", exact=True)
        assert len(results) == 1
        assert results[0]["full_path"] == "pkg.mod.ClassA"

    def test_exact_match_no_results(self, db, sample_entities):
        results = search_entities(db, "pkg.mod.Class", exact=True)
        assert len(results) == 0

    def test_name_only(self, db, sample_entities):
        results = search_entities(db, "func", name_only=True)
        assert len(results) == 1
        assert results[0]["name"] == "func"

    def test_name_only_no_full_path_match(self, db, sample_entities):
        results = search_entities(db, "pkg\\.mod", name_only=True)
        assert len(results) == 0

    def test_type_and_language_combined(self, db, sample_entities):
        results = search_entities(db, ".*", type_filter=["CLASS"], language="python")
        assert len(results) == 2

    def test_with_branch_scope(self, db, sample_entities):
        upsert_branch_entities(db, "feature", ["id1", "id5"])
        results = search_entities(
            db, "ClassA", branch_name="feature", type_filter=["CLASS"]
        )
        assert len(results) == 1
        assert results[0]["id"] == "id1"


class TestDetectFileChanges:
    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_force_flag_processes_files_with_matching_mtime(self, mock_hash):
        """With force=True, files are processed even when mtime matches tracked record."""
        files = [FileMetadata(path="src/main.py", mtime=100.0)]
        tracked = {"src/main.py": ("abc123", 100.0)}

        _, changed, mtime_only, _ = _detect_file_changes(
            files, tracked, "/tmp", force=True
        )
        assert "src/main.py" in changed
        assert "src/main.py" not in mtime_only
        mock_hash.assert_called_once()

    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_without_force_skips_on_mtime_match(self, mock_hash):
        """Without force, files with matching mtime are skipped entirely."""
        files = [FileMetadata(path="src/main.py", mtime=100.0)]
        tracked = {"src/main.py": ("abc123", 100.0)}

        _, changed, mtime_only, _ = _detect_file_changes(
            files, tracked, "/tmp", force=False
        )
        assert "src/main.py" not in changed
        assert "src/main.py" not in mtime_only
        mock_hash.assert_not_called()

    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_force_flag_detects_changed_files(self, mock_hash):
        """With force=True and changed hash, file goes into changed set."""
        from deputy.tools.utils import _detect_file_changes
        from deputy.utils.storage.models import FileMetadata

        files = [FileMetadata(path="src/main.py", mtime=100.0)]
        tracked = {"src/main.py": ("oldhash", 100.0)}

        _, changed, mtime_only, _ = _detect_file_changes(
            files, tracked, "/tmp", force=True
        )
        assert "src/main.py" in changed
        assert "src/main.py" not in mtime_only

    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_deleted_files_detected(self, mock_hash):
        """Files in tracked but not in files list appear as deleted."""
        files = [FileMetadata(path="src/kept.py", mtime=100.0)]
        tracked = {"src/kept.py": ("h1", 100.0), "src/deleted.py": ("h2", 200.0)}

        _, changed, mtime_only, deleted = _detect_file_changes(
            files, tracked, "/tmp", force=True
        )
        assert "src/deleted.py" in deleted
        assert "src/kept.py" in changed or "src/kept.py" in mtime_only


class TestClassBases:
    def test_upsert_and_get_direct_bases(self, db):
        bases = [
            {
                "base_full_path": "pkg.base.BaseA",
                "base_entity_id": "id_a",
                "is_resolved": True,
            },
            {
                "base_full_path": "pkg.mixins.Mixin",
                "base_entity_id": None,
                "is_resolved": False,
                "branch_info": '{"branch":"try"}',
            },
        ]
        upsert_class_bases(db, "class_foo", bases)
        result = get_direct_bases(db, "class_foo")
        assert len(result) == 2
        assert result[0]["base_full_path"] == "pkg.base.BaseA"
        assert result[0]["is_resolved"] == 1
        assert result[1]["base_full_path"] == "pkg.mixins.Mixin"
        assert result[1]["is_resolved"] == 0

    def test_replaces_on_reupsert(self, db):
        upsert_class_bases(
            db,
            "c1",
            [
                {
                    "base_full_path": "pkg.Base",
                    "base_entity_id": "old",
                    "is_resolved": True,
                }
            ],
        )
        upsert_class_bases(
            db,
            "c1",
            [
                {
                    "base_full_path": "pkg.Base",
                    "base_entity_id": "new",
                    "is_resolved": True,
                }
            ],
        )
        result = get_direct_bases(db, "c1")
        assert len(result) == 1
        assert result[0]["base_entity_id"] == "new"

    def test_delete_class_bases(self, db):
        upsert_class_bases(
            db, "c1", [{"base_full_path": "pkg.Base", "is_resolved": True}]
        )
        delete_class_bases_by_class(db, "c1")
        assert get_direct_bases(db, "c1") == []

    def test_isolated_by_class(self, db):
        upsert_class_bases(
            db, "c1", [{"base_full_path": "pkg.Base", "is_resolved": True}]
        )
        upsert_class_bases(
            db, "c2", [{"base_full_path": "pkg.Base", "is_resolved": True}]
        )
        assert len(get_direct_bases(db, "c1")) == 1
        assert len(get_direct_bases(db, "c2")) == 1

    def test_get_direct_subclasses(self, db, sample_entities):
        upsert_class_bases(
            db,
            "id1",
            [
                {
                    "base_full_path": "pkg.base.Base",
                    "base_entity_id": None,
                    "is_resolved": True,
                }
            ],
        )
        upsert_class_bases(
            db,
            "id4",
            [
                {
                    "base_full_path": "pkg.base.Base",
                    "base_entity_id": None,
                    "is_resolved": True,
                }
            ],
        )
        subs = get_direct_subclasses(db, "pkg.base.Base")
        paths = {s["full_path"] for s in subs}
        assert paths == {"pkg.mod.ClassA", "pkg.mod2.ClassA"}

    def test_get_direct_subclasses_scoped(self, db, sample_entities):
        upsert_branch_entities(db, "br", ["id1"])
        upsert_class_bases(
            db, "id1", [{"base_full_path": "pkg.Base", "is_resolved": True}]
        )
        upsert_class_bases(
            db, "id4", [{"base_full_path": "pkg.Base", "is_resolved": True}]
        )
        subs = get_direct_subclasses(db, "pkg.Base", branch_name="br")
        assert len(subs) == 1
        assert subs[0]["id"] == "id1"

    def test_transitive_subclasses(self, db, sample_entities):
        upsert_class_bases(
            db,
            "id4",
            [
                {
                    "base_full_path": "pkg.mod.ClassA",
                    "base_entity_id": "id1",
                    "is_resolved": True,
                }
            ],
        )
        subs = get_transitive_subclasses(db, "pkg.mod.ClassA")
        assert len(subs) == 1
        assert subs[0]["id"] == "id4"

    def test_transitive_subclasses_multi_level(self, db, sample_entities):
        upsert_entity(
            db,
            id="id6",
            language="python",
            full_path="pkg.mod3.ClassC",
            name="ClassC",
            type="CLASS",
            metadata_json='{"fqn":"pkg.mod3.ClassC","lineno":1}',
        )
        upsert_class_bases(
            db,
            "id4",
            [
                {
                    "base_full_path": "pkg.mod.ClassA",
                    "base_entity_id": "id1",
                    "is_resolved": True,
                }
            ],
        )
        upsert_class_bases(
            db,
            "id6",
            [
                {
                    "base_full_path": "pkg.mod2.ClassA",
                    "base_entity_id": "id4",
                    "is_resolved": True,
                }
            ],
        )
        subs = get_transitive_subclasses(db, "pkg.mod.ClassA", branch_name=None)
        paths = {s["full_path"] for s in subs}
        assert "pkg.mod2.ClassA" in paths
        assert "pkg.mod3.ClassC" in paths


class TestInheritancePins:
    def test_upsert_and_get(self, db):
        upsert_inheritance_pin(db, "class1", "Base", "entity_a", "main")
        pin = get_inheritance_pin(db, "class1", "Base", "main")
        assert pin is not None
        assert pin["pinned_entity_id"] == "entity_a"

    def test_replaces_on_reupsert(self, db):
        upsert_inheritance_pin(db, "class1", "Base", "old", "main")
        upsert_inheritance_pin(db, "class1", "Base", "new", "main")
        pin = get_inheritance_pin(db, "class1", "Base", "main")
        assert pin["pinned_entity_id"] == "new"

    def test_isolated_by_branch(self, db):
        upsert_inheritance_pin(db, "class1", "Base", "entity_a", "main")
        upsert_inheritance_pin(db, "class1", "Base", "entity_b", "other")
        pin_main = get_inheritance_pin(db, "class1", "Base", "main")
        pin_other = get_inheritance_pin(db, "class1", "Base", "other")
        assert pin_main["pinned_entity_id"] == "entity_a"
        assert pin_other["pinned_entity_id"] == "entity_b"

    def test_delete(self, db):
        upsert_inheritance_pin(db, "class1", "Base", "entity_a", "main")
        delete_inheritance_pin(db, "class1", "Base", "main")
        assert get_inheritance_pin(db, "class1", "Base", "main") is None

    def test_get_missing(self, db):
        assert get_inheritance_pin(db, "nonexistent", "Base", "main") is None

    def test_list_pins(self, db, sample_entities):
        upsert_inheritance_pin(db, "id1", "Base", "e1", "main")
        upsert_inheritance_pin(db, "id5", "Mixin", "e2", "main")
        pins = list_inheritance_pins(db, "main")
        assert len(pins) == 2
        paths = {p["class_full_path"] for p in pins}
        assert paths == {"pkg.mod.ClassA", "pkg.mod2"}

    def test_list_pins_empty_branch(self, db):
        assert list_inheritance_pins(db, "nonexistent") == []
