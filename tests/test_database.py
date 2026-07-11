from deputy.database.sqlite import (
    get_branch_files,
    upsert_branch_file,
    delete_branch_file,
    update_mtime,
    upsert_entity,
    delete_entity_by_module_fqn,
    get_entity_ids_by_fqn,
    get_entity_by_id,
    get_entities_by_ids,
    get_entities_by_path,
    get_entity_by_path,
    set_config,
    get_config,
)

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
