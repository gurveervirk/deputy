import pytest
from unittest.mock import patch
from deputy.database.sqlite import upsert_entity, upsert_branch_entities
from deputy.tools.resolve import InteractiveResolver

@pytest.fixture
def resolver(db):
    return InteractiveResolver(db, mode="default")

@pytest.fixture
def sample_import_graph(db):
    rows = [
        dict(id="mod_core", language="python", full_path="pkg.core", name="core", type="MODULE",
             metadata_json='{"fqn":"pkg.core","path":"pkg/core.py"}'),
        dict(id="mod_utils", language="python", full_path="pkg.utils", name="utils", type="MODULE",
             metadata_json='{"fqn":"pkg.utils","path":"pkg/utils.py"}'),
        dict(id="func_foo", language="python", full_path="pkg.core.foo", name="foo", type="FUNCTION",
             metadata_json='{"fqn":"pkg.core.foo","lineno":1,"path":"pkg/core.py"}'),
        dict(id="import_stmt", language="python",
             full_path="pkg.utils.__import__.stmt1", name="pkg.core",
             type="IMPORT_STATEMENT",
             metadata_json='{"import_type":"from","parent_id":"mod_utils","path":"pkg/utils.py"}'),
        dict(id="alias_foo", language="python", full_path="pkg.utils.foo", name="foo",
             type="IMPORT_ALIAS",
             metadata_json='{"original_name":"foo","parent_id":"import_stmt","fqn":"pkg.utils.foo"}'),
    ]
    for kwargs in rows:
        upsert_entity(db, **kwargs)
    db.commit()
    return db

class TestResolveConcrete:
    def test_resolve_concrete_entity(self, resolver, sample_import_graph):
        result = resolver.resolve("pkg.core", "foo")
        assert result is not None
        assert result["type"] == "FUNCTION"
        assert result["full_path"] == "pkg.core.foo"

    def test_resolve_not_found(self, resolver, db):
        result = resolver.resolve("nonexistent", "symbol")
        assert result is None

class TestResolveAlias:
    def test_resolve_follows_single_alias(self, resolver, sample_import_graph):
        result = resolver.resolve("pkg.utils", "foo")
        assert result is not None
        assert result["full_path"] == "pkg.core.foo"
        assert result["type"] == "FUNCTION"

    def test_resolve_alias_multiple_choices(self, resolver, sample_import_graph):
        func2_id = "func_bar"
        upsert_entity(resolver.conn,
            id=func2_id, language="python", full_path="pkg.core.bar", name="bar",
            type="FUNCTION",
            metadata_json='{"fqn":"pkg.core.bar","lineno":10,"path":"pkg/core.py"}')
        alias_id = "alias_bar"
        upsert_entity(resolver.conn,
            id=alias_id, language="python",
            full_path="pkg.utils.bar", name="bar",
            type="IMPORT_ALIAS",
            metadata_json='{"original_name":"bar","parent_id":"import_stmt","fqn":"pkg.utils.bar"}')
        resolver.conn.commit()

        result = resolver.resolve("pkg.utils", "bar")
        assert result is not None
        assert result["full_path"] == "pkg.core.bar"

    def test_resolve_alias_chain(self, resolver, db):
        upsert_entity(db,
            id="mod_a", language="python", full_path="a", name="a", type="MODULE",
            metadata_json='{"fqn":"a","path":"a.py"}')
        upsert_entity(db,
            id="mod_b", language="python", full_path="b", name="b", type="MODULE",
            metadata_json='{"fqn":"b","path":"b.py"}')
        upsert_entity(db,
            id="mod_c", language="python", full_path="c", name="c", type="MODULE",
            metadata_json='{"fqn":"c","path":"c.py"}')
        upsert_entity(db,
            id="func_real", language="python", full_path="c.hello", name="hello",
            type="FUNCTION",
            metadata_json='{"fqn":"c.hello","lineno":1,"path":"c.py"}')
        upsert_entity(db,
            id="stmt_b_to_c", language="python",
            full_path="b.__import__.c_hello", name="c",
            type="IMPORT_STATEMENT",
            metadata_json='{"import_type":"from","parent_id":"mod_b"}')
        upsert_entity(db,
            id="alias_b", language="python", full_path="b.hello", name="hello",
            type="IMPORT_ALIAS",
            metadata_json='{"original_name":"hello","parent_id":"stmt_b_to_c","fqn":"b.hello"}')
        upsert_entity(db,
            id="stmt_a_to_b", language="python",
            full_path="a.__import__.b_hello", name="b",
            type="IMPORT_STATEMENT",
            metadata_json='{"import_type":"from","parent_id":"mod_a"}')
        upsert_entity(db,
            id="alias_a", language="python", full_path="a.hello", name="hello",
            type="IMPORT_ALIAS",
            metadata_json='{"original_name":"hello","parent_id":"stmt_a_to_b","fqn":"a.hello"}')
        db.commit()

        resolver2 = InteractiveResolver(db, mode="default")
        result = resolver2.resolve("a", "hello")
        assert result is not None
        assert result["full_path"] == "c.hello"
        assert result["type"] == "FUNCTION"

class TestResolveModes:
    def test_auto_mode_skips_unambiguous_aliases(self, db, sample_import_graph):
        resolver = InteractiveResolver(db, mode="auto")
        result = resolver.resolve("pkg.utils", "foo")
        assert result is not None
        assert result["full_path"] == "pkg.core.foo"

    def test_step_mode_shows_all_steps(self, db, sample_import_graph):
        resolver = InteractiveResolver(db, mode="step")
        with patch("rich.prompt.Prompt.ask", return_value="a"):
            result = resolver.resolve("pkg.utils", "foo")
        assert result is None  # aborted

class TestRelativeImport:
    @pytest.fixture
    def relative_import_graph(self, db):
        upsert_entity(db,
            id="mod_pkg_init", language="python",
            full_path="mypkg", name="mypkg", type="PACKAGE",
            metadata_json='{"fqn":"mypkg","path":"mypkg/__init__.py"}')
        upsert_entity(db,
            id="mod_pkg_sub", language="python",
            full_path="mypkg.sub", name="sub", type="MODULE",
            metadata_json='{"fqn":"mypkg.sub","path":"mypkg/sub.py"}')
        upsert_entity(db,
            id="func_target", language="python",
            full_path="mypkg.sub.target", name="target", type="FUNCTION",
            metadata_json='{"fqn":"mypkg.sub.target","lineno":1,"path":"mypkg/sub.py"}')
        upsert_entity(db,
            id="stmt_relative", language="python",
            full_path="mypkg.__import__.relative_import", name=".sub",
            type="IMPORT_STATEMENT",
            metadata_json='{"import_type":"from","parent_id":"mod_pkg_init"}')
        upsert_entity(db,
            id="alias_relative", language="python",
            full_path="mypkg.target", name="target",
            type="IMPORT_ALIAS",
            metadata_json='{"original_name":"target","parent_id":"stmt_relative","fqn":"mypkg.target"}')
        db.commit()
        return db

    def test_relative_import_resolution(self, relative_import_graph):
        resolver = InteractiveResolver(relative_import_graph, mode="default")
        result = resolver.resolve("mypkg", "target")
        assert result is not None
        assert result["full_path"] == "mypkg.sub.target"
        assert result["type"] == "FUNCTION"

class TestResolveWithBranch:
    def test_resolve_scoped_to_branch(self, db):
        upsert_entity(db,
            id="mod_x", language="python", full_path="x.mod", name="mod", type="MODULE",
            metadata_json='{"fqn":"x.mod","path":"x/mod.py"}')
        upsert_entity(db,
            id="func_real", language="python", full_path="x.mod.real_func", name="real_func",
            type="FUNCTION",
            metadata_json='{"fqn":"x.mod.real_func","lineno":1,"path":"x/mod.py"}')
        upsert_branch_entities(db, "feature", ["mod_x", "func_real"])
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        result = resolver.resolve("x.mod", "real_func")
        assert result is not None
        assert result["full_path"] == "x.mod.real_func"
