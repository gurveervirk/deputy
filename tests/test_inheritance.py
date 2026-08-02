import json
from deputy.database.sqlite import (
    upsert_entity,
    upsert_class_bases,
    get_entity_by_path,
    get_entity_by_id,
)
from deputy.tools.inheritance import (
    clean_inherited_member_entities,
    _create_inherited_base_aliases,
    _create_inherited_inner_class_aliases,
    eager_resolve_all_inherited_members,
    compute_class_mro,
    resolve_entity_through_mro,
)
from deproc.plugins.python.inheritance import c3_merge
from deputy.tools.core import _compute_source

def _upsert_class(conn, eid, fqn, name, method_ids=None, inner_type_ids=None, property_ids=None, lineno=1, parent_classes=None):
    meta = {
        "fqn": fqn,
        "lineno": lineno,
        "parent_classes": parent_classes or [],
        "method_ids": method_ids or [],
        "inner_type_ids": inner_type_ids or [],
        "property_ids": property_ids or [],
    }
    upsert_entity(conn, id=eid, language="python", full_path=fqn, name=name,
                  type="CLASS", metadata_json=json.dumps(meta, default=str))

def _upsert_method(conn, eid, fqn, name, lineno=1):
    meta = {"fqn": fqn, "lineno": lineno}
    upsert_entity(conn, id=eid, language="python", full_path=fqn, name=name,
                  type="METHOD", metadata_json=json.dumps(meta, default=str))

def _upsert_property(conn, eid, fqn, name, lineno=1):
    meta = {"fqn": fqn, "lineno": lineno}
    upsert_entity(conn, id=eid, language="python", full_path=fqn, name=name,
                  type="PROPERTY", metadata_json=json.dumps(meta, default=str))

def _build_records(conn):
    rows = conn.execute("SELECT * FROM entities WHERE type = 'CLASS' ORDER BY full_path").fetchall()
    return [dict(r) for r in rows]

class TestCleanSyntheticEntities:
    def test_removes_synthetic_leaves_real(self, db):
        upsert_entity(db, id="real1", language="python", full_path="mod.real", name="real",
                      type="FUNCTION", metadata_json='{"fqn":"mod.real"}')
        upsert_entity(db, id="syn1", language="python", full_path="mod.syn", name="syn",
                      type="INHERITED_MEMBER", metadata_json='{"inherited": true}')
        clean_inherited_member_entities(db)
        assert get_entity_by_path(db, "mod.real") is not None
        assert get_entity_by_path(db, "mod.syn") is None

    def test_no_synthetic_entities_is_harmless(self, db):
        clean_inherited_member_entities(db)

class TestCreateInheritedBaseAliases:
    def test_single_base_method_inherited(self, db):
        _upsert_method(db, "m1", "Base.foo", "foo")
        _upsert_class(db, "c1", "Base", "Base", method_ids=["m1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        created = _create_inherited_base_aliases(db, records, "main")
        assert len(created) == 1
        assert created[0]["full_path"] == "Child.foo"
        assert created[0]["type"] == "INHERITED_MEMBER"

    def test_mro_precedence_shadows_second_base(self, db):
        _upsert_method(db, "m1", "Base1.foo", "foo")
        _upsert_method(db, "m2", "Base2.foo", "foo")
        _upsert_class(db, "c1", "Base1", "Base1", method_ids=["m1"])
        _upsert_class(db, "c2", "Base2", "Base2", method_ids=["m2"])
        _upsert_class(db, "c3", "Child", "Child", parent_classes=["Base1", "Base2"])
        upsert_class_bases(db, "c3", [
            {"base_full_path": "Base1", "base_entity_id": "c1", "is_resolved": True},
            {"base_full_path": "Base2", "base_entity_id": "c2", "is_resolved": True},
        ])
        records = _build_records(db)
        created = _create_inherited_base_aliases(db, records, "main")
        paths = [c["full_path"] for c in created]
        assert len(paths) == 1
        syn = get_entity_by_path(db, "Child.foo")
        assert syn is not None
        assert json.loads(syn["metadata_json"]).get("target_entity_id") == "m1"

    def test_direct_definition_shadows_inherited(self, db):
        _upsert_method(db, "m1", "Base.foo", "foo")
        _upsert_class(db, "c1", "Base", "Base", method_ids=["m1"])
        _upsert_method(db, "m2", "Child.foo", "foo")
        _upsert_class(db, "c2", "Child", "Child", method_ids=["m2"], parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        created = _create_inherited_base_aliases(db, records, "main")
        syn_ids = [c["id"] for c in created]
        assert "Child.foo" not in syn_ids, "should not create synthetic alias for directly-defined member"

    def test_property_inherited(self, db):
        _upsert_property(db, "p1", "Base.prop_x", "prop_x")
        _upsert_class(db, "c1", "Base", "Base", property_ids=["p1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        created = _create_inherited_base_aliases(db, records, "main")
        assert len(created) == 1
        assert created[0]["full_path"] == "Child.prop_x"

    def test_inner_type_inherited(self, db):
        _upsert_class(db, "ic1", "Base.Inner", "Inner")
        _upsert_class(db, "c1", "Base", "Base", inner_type_ids=["ic1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        created = _create_inherited_base_aliases(db, records, "main")
        assert len(created) == 1
        assert created[0]["full_path"] == "Child.Inner"

    def test_grandparent_method_inherited(self, db):
        _upsert_method(db, "m1", "Grandparent.foo", "foo")
        _upsert_class(db, "c1", "Grandparent", "Grandparent", method_ids=["m1"])
        _upsert_class(db, "c2", "Parent", "Parent", parent_classes=["Grandparent"])
        _upsert_class(db, "c3", "Child", "Child", parent_classes=["Parent"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Grandparent", "base_entity_id": "c1", "is_resolved": True}])
        upsert_class_bases(db, "c3", [{"base_full_path": "Parent", "base_entity_id": "c2", "is_resolved": True}])
        records = _build_records(db)
        created = _create_inherited_base_aliases(db, records, "main")
        paths = [c["full_path"] for c in created]
        assert "Parent.foo" in paths
        assert "Child.foo" in paths

class TestCreateInheritedInnerClassAliases:
    def test_inner_class_own_member_inherited(self, db):
        _upsert_method(db, "m1", "Base.Inner.base_method", "base_method")
        _upsert_class(db, "ic1", "Base.Inner", "Inner", method_ids=["m1"])
        _upsert_class(db, "c1", "Base", "Base", inner_type_ids=["ic1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        created_pass2 = _create_inherited_inner_class_aliases(db, records, "main")
        paths = [c["full_path"] for c in created_pass2]
        assert "Child.base_method" in paths

    def test_inner_class_chain(self, db):
        _upsert_method(db, "m1", "Grandparent.Inner.grand_method", "grand_method")
        _upsert_class(db, "ic1", "Grandparent.Inner", "Inner", method_ids=["m1"])
        _upsert_class(db, "c1", "Grandparent", "Grandparent", inner_type_ids=["ic1"])
        _upsert_class(db, "c2", "Parent", "Parent", parent_classes=["Grandparent"])
        _upsert_class(db, "c3", "Child", "Child", parent_classes=["Parent"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Grandparent", "base_entity_id": "c1", "is_resolved": True}])
        upsert_class_bases(db, "c3", [{"base_full_path": "Parent", "base_entity_id": "c2", "is_resolved": True}])
        records = _build_records(db)
        created_pass2 = _create_inherited_inner_class_aliases(db, records, "main")
        paths = [c["full_path"] for c in created_pass2]
        assert "Parent.grand_method" in paths
        assert "Child.grand_method" in paths

class TestEagerResolveFullPipeline:
    def test_full_pipeline_creates_searchable_aliases(self, db):
        _upsert_method(db, "m1", "Base.foo", "foo")
        _upsert_method(db, "m2", "Base.bar", "bar")
        _upsert_class(db, "c1", "Base", "Base", method_ids=["m1", "m2"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        eager_resolve_all_inherited_members(db, records, "main")
        foo_entity = get_entity_by_path(db, "Child.foo")
        assert foo_entity is not None
        assert foo_entity["type"] == "INHERITED_MEMBER"
        meta = json.loads(foo_entity["metadata_json"])
        assert meta.get("target_entity_id") == "m1"
        assert meta.get("inherited_from") == "Base"
        bar_entity = get_entity_by_path(db, "Child.bar")
        assert bar_entity is not None

    def test_search_finds_inherited_members(self, db):
        _upsert_method(db, "m1", "Base.foo", "foo")
        _upsert_class(db, "c1", "Base", "Base", method_ids=["m1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = _build_records(db)
        eager_resolve_all_inherited_members(db, records, "main")
        child_result = get_entity_by_path(db, "Child.foo")
        assert child_result is not None
        base_result = get_entity_by_path(db, "Base.foo")
        assert base_result is not None
        assert base_result["type"] == "METHOD"

    def test_non_existent_path_returns_none(self, db):
        result = resolve_entity_through_mro(db, "Nonexistent.thing")
        assert result == (None, None)

class TestComputeClassMro:
    def test_single_inheritance(self, db):
        _upsert_class(db, "c1", "Base", "Base")
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        mro = compute_class_mro(db, "c2")
        assert mro == ["Child", "Base"]

    def test_unresolved_base_returns_self_only(self, db):
        _upsert_class(db, "c1", "Child", "Child", parent_classes=["Unknown"])
        upsert_class_bases(db, "c1", [{"base_full_path": "Unknown", "base_entity_id": None, "is_resolved": False}])
        mro = compute_class_mro(db, "c1")
        assert mro == ["Child"]

    def test_no_bases_returns_self(self, db):
        _upsert_class(db, "c1", "Standalone", "Standalone")
        mro = compute_class_mro(db, "c1")
        assert mro == ["Standalone"]

    def test_memoization_prevents_redundant_work(self, db):
        _upsert_class(db, "c1", "Base", "Base")
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        _upsert_class(db, "c3", "Grandchild", "Grandchild", parent_classes=["Child"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        upsert_class_bases(db, "c3", [{"base_full_path": "Child", "base_entity_id": "c2", "is_resolved": True}])

        memo = {}
        mro_child = compute_class_mro(db, "c2", memo)
        mro_gc = compute_class_mro(db, "c3", memo)
        assert mro_child == ["Child", "Base"]
        assert mro_gc == ["Grandchild", "Child", "Base"]

class TestResolveEntityThroughMro:
    def test_direct_entity_returns_immediately(self, db):
        _upsert_method(db, "m1", "mod.foo", "foo")
        entity, inherited_from = resolve_entity_through_mro(db, "mod.foo")
        assert entity is not None
        assert inherited_from is None

    def test_inherited_member_resolves(self, db):
        _upsert_method(db, "m1", "Base.foo", "foo")
        _upsert_class(db, "c1", "Base", "Base", method_ids=["m1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        entity, inherited_from = resolve_entity_through_mro(db, "Child.foo")
        assert entity is not None
        assert "_inherited_from" in entity
        assert entity["_inherited_from"] == "Base"

    def test_nested_class_member(self, db):
        _upsert_method(db, "m1", "Base.Inner.inner_foo", "inner_foo")
        _upsert_class(db, "ic1", "Base.Inner", "Inner", method_ids=["m1"])
        _upsert_class(db, "c1", "Base", "Base", inner_type_ids=["ic1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        entity, inherited_from = resolve_entity_through_mro(db, "Child.Inner.inner_foo")
        assert entity is not None
        assert entity["_inherited_from"] == "Base"


class TestInheritedMemberSource:
    def test_source_points_to_target(self, db):
        upsert_entity(db, id="mod", language="python", full_path="mymod",
                      name="mymod", type="MODULE",
                      metadata_json='{"fqn":"mymod","path":"mymod.py"}')
        upsert_entity(db, id="m1", language="python", full_path="mymod.Base.foo",
                      name="foo", type="METHOD",
                      metadata_json='{"fqn":"mymod.Base.foo","lineno":5,"source_id":"mod"}')
        upsert_entity(db, id="c1", language="python", full_path="mymod.Base",
                      name="Base", type="CLASS",
                      metadata_json='{"fqn":"mymod.Base","lineno":1,"parent_classes":[],"method_ids":["m1"],"source_id":"mod"}')
        _upsert_class(db, "c2", "mymod.Child", "Child", parent_classes=["mymod.Base"])

        upsert_class_bases(db, "c2", [{"base_full_path": "mymod.Base", "base_entity_id": "c1", "is_resolved": True}])
        records = [{"id": "c2", "type": "CLASS", "full_path": "mymod.Child",
                    "metadata_json": json.dumps({"fqn": "mymod.Child", "lineno": 1, "parent_classes": ["mymod.Base"]})}]
        _create_inherited_base_aliases(db, records, "main")
        syn = get_entity_by_path(db, "mymod.Child.foo")
        assert syn is not None
        src = _compute_source(syn, db)
        assert "mymod.py:5" in src

    def test_non_inherited_member_source(self, db):
        upsert_entity(db, id="mod", language="python", full_path="mymod",
                      name="mymod", type="MODULE",
                      metadata_json='{"fqn":"mymod","path":"mymod.py"}')
        upsert_entity(db, id="m1", language="python", full_path="mymod.foo",
                      name="foo", type="METHOD",
                      metadata_json='{"fqn":"mymod.foo","lineno":10,"source_id":"mod"}')
        entity = get_entity_by_path(db, "mymod.foo")
        src = _compute_source(entity, db)
        assert "mymod.py:10" in src


class TestInheritedMemberAutoResolve:
    def test_resolve_redirects_to_target(self, db):
        _upsert_method(db, "m1", "Base.foo", "foo", lineno=5)
        _upsert_class(db, "c1", "Base", "Base", method_ids=["m1"])
        _upsert_class(db, "c2", "Child", "Child", parent_classes=["Base"])
        upsert_class_bases(db, "c2", [{"base_full_path": "Base", "base_entity_id": "c1", "is_resolved": True}])
        records = [{"id": "c2", "type": "CLASS", "full_path": "Child",
                    "metadata_json": json.dumps({"fqn": "Child", "lineno": 1, "parent_classes": ["Base"]})}]
        _create_inherited_base_aliases(db, records, "main")
        entity = get_entity_by_path(db, "Child.foo")
        assert entity is not None
        meta = json.loads(entity["metadata_json"])
        target_id = meta.get("target_entity_id")
        assert target_id == "m1"
        target = get_entity_by_id(db, target_id)
        assert target is not None
        assert target["full_path"] == "Base.foo"
