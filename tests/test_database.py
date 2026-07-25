from unittest.mock import patch
from deputy.database.sqlite import (
    clean_orphan_entities,
    delete_branch_entities,
    get_branch_files,
    get_entities_by_path,
    get_entity_by_path,
    upsert_branch_entities,
    upsert_branch_file,
    delete_branch_file,
    update_mtime,
    upsert_entity,
    delete_entity_by_module_fqn,
    get_entity_ids_by_fqn,
    get_entity_by_id,
    get_entities_by_ids,
    search_entities,
    set_config,
    get_config,
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
        upsert_entity(db, id="e1", language="python", full_path="mod.f", name="f", type="FUNCTION",
                      metadata_json='{"fqn":"mod.f"}')
        row = get_entity_by_path(db, "mod.f")
        assert row["name"] == "f"

    def test_upsert_replaces(self, db):
        upsert_entity(db, id="e1", language="python", full_path="mod.f", name="f", type="FUNCTION",
                      metadata_json='{"fqn":"mod.f","a":1}')
        upsert_entity(db, id="e1", language="python", full_path="mod.f", name="f", type="FUNCTION",
                      metadata_json='{"fqn":"mod.f","a":2}')
        row = get_entity_by_id(db, "e1")
        assert row["metadata_json"] == '{"fqn":"mod.f","a":2}'

    def test_get_entities_by_path_multiple(self, db):
        upsert_entity(db, id="e1", language="python", full_path="mod.dup", name="dup", type="FUNCTION",
                      metadata_json="{}")
        upsert_entity(db, id="e2", language="python", full_path="mod.dup", name="dup", type="CLASS",
                      metadata_json="{}")
        rows = get_entities_by_path(db, "mod.dup")
        assert len(rows) == 2

    def test_get_entity_by_path_returns_first(self, db):
        upsert_entity(db, id="e1", language="python", full_path="mod.dup", name="dup", type="FUNCTION",
                      metadata_json="{}")
        upsert_entity(db, id="e2", language="python", full_path="mod.dup", name="dup", type="CLASS",
                      metadata_json="{}")
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
        results = search_entities(db, "ClassA", branch_name="feature", type_filter=["CLASS"])
        assert len(results) == 1
        assert results[0]["id"] == "id1"

class TestDetectFileChanges:
    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_force_flag_processes_files_with_matching_mtime(self, mock_hash):
        """With force=True, files are processed even when mtime matches tracked record."""
        files = [FileMetadata(path="src/main.py", mtime=100.0)]
        tracked = {"src/main.py": ("abc123", 100.0)}

        _, changed, mtime_only, _ = _detect_file_changes(files, tracked, "/tmp", force=True)
        assert "src/main.py" in changed
        assert "src/main.py" not in mtime_only
        mock_hash.assert_called_once()

    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_without_force_skips_on_mtime_match(self, mock_hash):
        """Without force, files with matching mtime are skipped entirely."""
        files = [FileMetadata(path="src/main.py", mtime=100.0)]
        tracked = {"src/main.py": ("abc123", 100.0)}

        _, changed, mtime_only, _ = _detect_file_changes(files, tracked, "/tmp", force=False)
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

        _, changed, mtime_only, _ = _detect_file_changes(files, tracked, "/tmp", force=True)
        assert "src/main.py" in changed
        assert "src/main.py" not in mtime_only

    @patch("deputy.tools.utils.compute_sha256", return_value="abc123")
    def test_deleted_files_detected(self, mock_hash):
        """Files in tracked but not in files list appear as deleted."""
        files = [FileMetadata(path="src/kept.py", mtime=100.0)]
        tracked = {"src/kept.py": ("h1", 100.0), "src/deleted.py": ("h2", 200.0)}

        _, changed, mtime_only, deleted = _detect_file_changes(files, tracked, "/tmp", force=True)
        assert "src/deleted.py" in deleted
        assert "src/kept.py" in changed or "src/kept.py" in mtime_only
