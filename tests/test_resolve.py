import json
import pytest
import io
from unittest.mock import patch
from rich.console import Console
from deputy.database.sqlite import upsert_entity, upsert_branch_entities
from deputy.tools.resolve import InteractiveResolver, ResolveStep

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

class TestResolveAll:
    def test_collects_all_terminal_entities(self, db):
        """resolve_all returns all terminal concrete entities through alias chains."""
        upsert_entity(db, id="mod_a", language="python", full_path="pkg", name="pkg", type="MODULE",
                      metadata_json='{"fqn":"pkg","path":"pkg.py"}')
        upsert_entity(db, id="mod_b", language="python", full_path="pkg.mod", name="mod", type="MODULE",
                      metadata_json='{"fqn":"pkg.mod","path":"pkg/mod.py"}')
        upsert_entity(db, id="func1", language="python", full_path="pkg.mod.func", name="func",
                      type="FUNCTION", metadata_json='{"fqn":"pkg.mod.func","lineno":1}')
        upsert_entity(db, id="stmt", language="python",
                      full_path="pkg.__import__.mod", name="pkg.mod",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_a"}')
        upsert_entity(db, id="alias_f", language="python", full_path="pkg.func", name="func",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"func","parent_id":"stmt","fqn":"pkg.func"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        results = resolver.resolve_all("pkg", "func")
        assert len(results) == 1
        assert results[0]["id"] == "func1"

    def test_multiple_concrete_entities(self, db):
        """resolve_all collects multiple concrete entities for the same FQN."""
        upsert_entity(db, id="mod_m", language="python", full_path="mod", name="mod", type="MODULE",
                      metadata_json='{"fqn":"mod","path":"mod.py"}')
        for i, bid in enumerate(["v1", "v2", "v3"]):
            upsert_entity(db, id=bid, language="python", full_path="mod.VAL",
                          name="VAL", type="VARIABLE",
                          metadata_json=json.dumps({"lineno": i * 5 + 1}))
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        results = resolver.resolve_all("mod", "VAL")
        assert len(results) == 3
        assert all(r["full_path"] == "mod.VAL" for r in results)

    def test_multi_level_collects_all(self, db):
        """resolve_all follows all alias chains to collect terminal entities."""
        upsert_entity(db, id="mod_top", language="python", full_path="top", name="top", type="MODULE",
                      metadata_json='{"fqn":"top","path":"top.py"}')
        upsert_entity(db, id="mod_bot", language="python", full_path="bot", name="bot", type="MODULE",
                      metadata_json='{"fqn":"bot","path":"bot.py"}')
        upsert_entity(db, id="func_bot", language="python", full_path="bot.run", name="run",
                      type="FUNCTION", metadata_json='{"fqn":"bot.run","lineno":5,"path":"bot.py"}')
        upsert_entity(db, id="stmt_bot", language="python",
                      full_path="top.__import__.bot", name="bot",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_top"}')
        upsert_entity(db, id="alias_top", language="python", full_path="top.run", name="run",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"run","parent_id":"stmt_bot","fqn":"top.run"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        results = resolver.resolve_all("top", "run")
        assert len(results) == 1
        assert results[0]["full_path"] == "bot.run"

    def test_cycle_detection(self, db):
        """resolve_all handles cycles without infinite recursion."""
        upsert_entity(db, id="mod_a", language="python", full_path="a", name="a", type="MODULE",
                      metadata_json='{"fqn":"a","path":"a.py"}')
        upsert_entity(db, id="stmt_a2b", language="python",
                      full_path="a.__import__.b", name="b",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_a"}')
        upsert_entity(db, id="alias_a2b", language="python", full_path="a.val", name="val",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"val","parent_id":"stmt_a2b","fqn":"a.val"}')
        upsert_entity(db, id="stmt_b2a", language="python",
                      full_path="b.__import__.a", name="a",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_a"}')
        upsert_entity(db, id="alias_b2a", language="python", full_path="b.val", name="val",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"val","parent_id":"stmt_b2a","fqn":"b.val"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        results = resolver.resolve_all("a", "val")
        assert len(results) == 0  # no terminal concretes, but no crash

    def test_all_compact_output(self, db):
        """_print_all_compact prints one line per terminal entity."""
        upsert_entity(db, id="mod_c", language="python", full_path="pkg", name="pkg", type="MODULE",
                      metadata_json='{"fqn":"pkg","path":"pkg.py"}')
        upsert_entity(db, id="c1", language="python", full_path="pkg.T", name="T", type="CLASS",
                      metadata_json=json.dumps({"fqn": "pkg.T", "lineno": 1, "source_id": "mod_c"}))
        upsert_entity(db, id="c2", language="python", full_path="pkg.T", name="T", type="CLASS",
                      metadata_json=json.dumps({"fqn": "pkg.T", "lineno": 5, "source_id": "mod_c"}))
        db.commit()

        output = io.StringIO()
        resolver = InteractiveResolver(db, mode="default")
        resolver.console = Console(file=output)
        resolver._print_all_compact("pkg", "T")
        printed = output.getvalue()
        assert "CLASS  pkg.T" in printed
        assert printed.count("\n") >= 2  # two lines

    def test_all_not_found(self, db):
        """resolve_all returns empty list for non-existent symbols."""
        resolver = InteractiveResolver(db, mode="default")
        results = resolver.resolve_all("nonexistent", "sym")
        assert results == []

    def test_all_tree_no_alias_direct_concrete(self, db):
        """_print_all_tree shows directly found concrete in tree with leaves."""
        upsert_entity(db, id="mod_d", language="python", full_path="dir.mod", name="mod", type="MODULE",
                      metadata_json='{"fqn":"dir.mod","path":"dir/mod.py"}')
        upsert_entity(db, id="f1", language="python", full_path="dir.mod.act", name="act",
                      type="FUNCTION",
                      metadata_json='{"fqn":"dir.mod.act","lineno":1,"source_id":"mod_d"}')
        db.commit()

        output = io.StringIO()
        resolver = InteractiveResolver(db, mode="default")
        resolver.console = Console(file=output)
        resolver._print_all_tree("dir.mod", "act")
        printed = output.getvalue()
        assert "FUNCTION act @ dir/mod.py:1" in printed
        assert "Resolved leaves:" in printed


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

class TestMultiVariant:
    def test_multiple_variable_entities_with_same_fqn(self, db):
        """Resolver shows prompt when same FQN has multiple VARIABLE entities (e.g., from control flow branches)."""
        upsert_entity(db, id="mod_a", language="python", full_path="pkg.mod", name="mod", type="MODULE",
                      metadata_json='{"fqn":"pkg.mod","path":"pkg/mod.py"}')
        for i, bid in enumerate(["v1", "v2", "v3"]):
            upsert_entity(db, id=bid, language="python", full_path="pkg.mod.VAL",
                          name="VAL", type="VARIABLE",
                          metadata_json=json.dumps({"lineno": i * 5 + 1}))
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        with patch("rich.prompt.Prompt.ask", return_value="2"):
            result = resolver.resolve("pkg.mod", "VAL")
        assert result is not None
        assert result["entity"]["full_path"] == "pkg.mod.VAL"
        assert result["entity"]["id"] == "v2"

    def test_multiple_class_entities_with_same_fqn(self, db):
        """Same FQN with multiple CLASS entities (from conditional class definitions)."""
        upsert_entity(db, id="mod_p", language="python", full_path="pkg", name="pkg", type="MODULE",
                      metadata_json='{"fqn":"pkg"}')
        for i, cid in enumerate(["c1", "c2", "c3"]):
            upsert_entity(db, id=cid, language="python", full_path="pkg.Handler",
                          name="Handler", type="CLASS",
                          metadata_json=json.dumps({"fqn": "pkg.Handler", "lineno": i * 3 + 1}))
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        with patch("rich.prompt.Prompt.ask", return_value="1"):
            result = resolver.resolve("pkg", "Handler")
        assert result is not None
        assert result["entity"]["id"] == "c1"

    def test_mixed_concrete_and_aliases_picks_correctly(self, db):
        """When both concrete and alias entities exist for same FQN, user can pick either."""
        upsert_entity(db, id="mod_m", language="python", full_path="mix.mod", name="mod", type="MODULE",
                      metadata_json='{"fqn":"mix.mod","path":"mix/mod.py"}')
        upsert_entity(db, id="real_func", language="python", full_path="mix.mod.func", name="func",
                      type="FUNCTION",
                      metadata_json='{"fqn":"mix.mod.func","lineno":1}')
        upsert_entity(db, id="stmt_alias", language="python",
                      full_path="mix.mod.__import__.imp", name="other",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_m"}')
        upsert_entity(db, id="alias_func", language="python", full_path="mix.mod.func", name="func",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"func","parent_id":"stmt_alias","fqn":"mix.mod.func"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        with patch("rich.prompt.Prompt.ask", return_value="1"):
            result = resolver.resolve("mix.mod", "func")
        assert result is not None
        assert result["type"] == "FUNCTION"
        assert result["id"] == "real_func"


class TestMultiLevel:
    def test_two_level_alias_chain(self, db):
        """Resolve through two layers of IMPORT_ALIAS: a.handler → b.Handler → c.Handler."""
        upsert_entity(db, id="mod_a", language="python", full_path="a", name="a", type="MODULE",
                      metadata_json='{"fqn":"a","path":"a.py"}')
        upsert_entity(db, id="mod_b", language="python", full_path="b", name="b", type="MODULE",
                      metadata_json='{"fqn":"b","path":"b.py"}')
        upsert_entity(db, id="mod_c", language="python", full_path="c", name="c", type="MODULE",
                      metadata_json='{"fqn":"c","path":"c.py"}')
        upsert_entity(db, id="func_handler", language="python", full_path="c.Handler", name="Handler",
                      type="CLASS",
                      metadata_json='{"fqn":"c.Handler","lineno":1,"path":"c.py"}')
        upsert_entity(db, id="stmt_b_to_c", language="python",
                      full_path="b.__import__.c_handler", name="c",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_b"}')
        upsert_entity(db, id="alias_b", language="python", full_path="b.Handler", name="Handler",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"Handler","parent_id":"stmt_b_to_c","fqn":"b.Handler"}')
        upsert_entity(db, id="stmt_a_to_b", language="python",
                      full_path="a.__import__.b_handler", name="b",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_a"}')
        upsert_entity(db, id="alias_a", language="python", full_path="a.handler", name="handler",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"Handler","parent_id":"stmt_a_to_b","fqn":"a.handler"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="auto")
        result = resolver.resolve("a", "handler")
        assert result is not None
        assert result["full_path"] == "c.Handler"
        assert result["type"] == "CLASS"

    def test_multi_level_with_branch_choices(self, db):
        """Alias → aliases in branches → concretes in branches: two levels of choices."""
        upsert_entity(db, id="mod_top", language="python", full_path="top", name="top", type="MODULE",
                      metadata_json='{"fqn":"top","path":"top.py"}')
        upsert_entity(db, id="mod_mid", language="python", full_path="mid", name="mid", type="MODULE",
                      metadata_json='{"fqn":"mid","path":"mid.py"}')
        upsert_entity(db, id="mod_bot", language="python", full_path="bot", name="bot", type="MODULE",
                      metadata_json='{"fqn":"bot","path":"bot.py"}')

        upsert_entity(db, id="func_bot", language="python", full_path="bot.run", name="run",
                      type="FUNCTION",
                      metadata_json='{"fqn":"bot.run","lineno":5,"path":"bot.py"}')

        # mid.py has 3 import aliases for bot.run in different branches
        for i, bid in enumerate(["stmt_mid1", "stmt_mid2", "stmt_mid3"]):
            upsert_entity(db, id=bid, language="python",
                          full_path=f"mid.__import__.mid_{i}", name="bot",
                          type="IMPORT_STATEMENT",
                          metadata_json='{"import_type":"from","parent_id":"mod_mid"}')
        for i, aid in enumerate(["alias_mid1", "alias_mid2", "alias_mid3"]):
            upsert_entity(db, id=aid, language="python", full_path="mid.run", name="run",
                          type="IMPORT_ALIAS",
                          metadata_json=json.dumps({
                              "original_name": "run", "parent_id": f"stmt_mid{i+1}", "fqn": "mid.run"
                          }))

        # top.run imports from mid.run
        upsert_entity(db, id="stmt_top", language="python",
                      full_path="top.__import__.top_run", name="mid",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_top"}')
        upsert_entity(db, id="alias_top", language="python", full_path="top.run", name="run",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"run","parent_id":"stmt_top","fqn":"top.run"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        with patch("rich.prompt.Prompt.ask", return_value="2"):
            result = resolver.resolve("top", "run")
        assert result is not None
        assert result["full_path"] == "bot.run"
        assert result["type"] == "FUNCTION"


class TestControlFlowAlias:
    def test_import_alias_inside_control_flow_is_resolved(self, db):
        """IMPORT_ALIAS inside a control flow block resolves correctly when it has proper FQN."""
        upsert_entity(db, id="mod_cf", language="python", full_path="cf", name="cf", type="MODULE",
                      metadata_json='{"fqn":"cf","path":"cf.py"}')
        upsert_entity(db, id="mod_target", language="python", full_path="target", name="target",
                      type="MODULE",
                      metadata_json='{"fqn":"target","path":"target.py"}')
        upsert_entity(db, id="func_target", language="python", full_path="target.util", name="util",
                      type="FUNCTION",
                      metadata_json='{"fqn":"target.util","lineno":1,"path":"target.py"}')

        # Import statement inside a control flow block
        upsert_entity(db, id="stmt_cf", language="python",
                      full_path="cf.__import__.stmt_cf", name="target",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_cf"}')
        upsert_entity(db, id="alias_cf", language="python", full_path="cf.util", name="util",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"util","parent_id":"stmt_cf","fqn":"cf.util"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        result = resolver.resolve("cf", "util")
        assert result is not None
        assert result["full_path"] == "target.util"

class TestPeekTarget:
    def test_peek_returns_display_with_source(self, db):
        """_peek_target includes type, name, and file:line when source_path is available."""
        upsert_entity(db, id="src_mod", language="python", full_path="pkg.run_src", name="run_src",
                      type="MODULE",
                      metadata_json='{"fqn":"pkg.run_src","path":"pkg/run_src.py"}')
        upsert_entity(db, id="mod_pk", language="python", full_path="pkg", name="pkg", type="MODULE",
                      metadata_json='{"fqn":"pkg","path":"pkg.py"}')
        upsert_entity(db, id="func_target", language="python", full_path="pkg.run", name="run",
                      type="FUNCTION",
                      metadata_json='{"fqn":"pkg.run","lineno":10,"source_id":"src_mod"}')
        upsert_entity(db, id="stmt_alias", language="python",
                      full_path="pkg.__import__.stmt", name="pkg",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_pk"}')
        upsert_entity(db, id="alias_pk", language="python", full_path="pkg.start", name="start",
                      type="IMPORT_ALIAS",
                      metadata_json='{"original_name":"run","parent_id":"stmt_alias","fqn":"pkg.start"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        alias = {"id": "alias_pk", "name": "start", "metadata_json": '{"original_name":"run","parent_id":"stmt_alias","fqn":"pkg.start"}'}
        peek = resolver._peek_target(alias)
        assert peek is not None
        assert peek["display"] == "FUNCTION run"
        assert peek["loc"] == "pkg/run_src.py:10"

    def test_peek_returns_none_for_unresolvable(self, db):
        """_peek_target returns None when alias cannot be followed."""
        upsert_entity(db, id="mod_orphan", language="python", full_path="orphan", name="orphan",
                      type="MODULE",
                      metadata_json='{"fqn":"orphan","path":"orphan.py"}')
        db.commit()

        resolver = InteractiveResolver(db, mode="default")
        alias = {"name": "missing", "metadata_json": '{}'}
        peek = resolver._peek_target(alias)
        assert peek is None

class TestResolveDisplay:
    def test_prompt_shows_abort_option(self):
        """Prompt text includes '(a - abort)'."""
        assert hasattr(InteractiveResolver, "_prompt")

    def test_auto_trace_shows_source_path(self, db):
        """_print_auto_trace includes @ file:line when source data is available."""
        upsert_entity(db, id="mod_disp", language="python", full_path="disp", name="disp",
                      type="MODULE",
                      metadata_json='{"fqn":"disp","path":"disp.py"}')
        upsert_entity(db, id="func_target", language="python", full_path="disp.util", name="util",
                      type="FUNCTION",
                      metadata_json='{"fqn":"disp.util","lineno":5,"path":"disp/util.py"}')
        upsert_entity(db, id="stmt_disp", language="python",
                      full_path="disp.__import__.stmt", name="other",
                      type="IMPORT_STATEMENT",
                      metadata_json='{"import_type":"from","parent_id":"mod_disp"}')
        upsert_entity(db, id="alias_disp", language="python", full_path="disp.util", name="util",
                      type="IMPORT_ALIAS",
                      metadata_json=json.dumps({
                          "original_name": "util", "parent_id": "stmt_disp",
                          "fqn": "disp.util", "lineno": 3,
                          "source_id": "mod_disp"
                      }))
        db.commit()

        output = io.StringIO()
        resolver = InteractiveResolver(db, mode="default")
        resolver.console = Console(file=output)

        step = ResolveStep(module_fqn="pkg", symbol_name="util")
        alias = {"id": "alias_disp", "name": "util", "type": "IMPORT_ALIAS",
                 "metadata_json": json.dumps({
                     "original_name": "util", "parent_id": "stmt_disp",
                     "fqn": "disp.util", "lineno": 3,
                     "source_id": "mod_disp"
                 })}
        resolver._print_auto_trace(step, alias, {
            "target_fqn": "disp.util",
            "display": "FUNCTION util",
            "loc": "disp/util.py:5"
        })
        printed = output.getvalue()
        assert "IMPORT_ALIAS util @ disp.py:3" in printed
        assert "disp.util  (FUNCTION util) @ disp/util.py:5" in printed or "disp.util  (FUNCTION util)" in printed
