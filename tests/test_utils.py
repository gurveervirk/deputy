import json
from deproc.core.interfaces.parser.models import (
    FunctionLike,
    TypeDefinition,
    ControlFlowBlock,
    ControlFlowGroup,
    SourceRange,
    VariableDeclaration,
)
from deproc.plugins.python.linker.models import PythonModule
from deproc.plugins.python.parser.models import (
    PythonConstant,
    PythonImportAlias,
    PythonTypeAlias,
)
from deputy.tools.utils import _entity_fqn, _entity_record, _build_module_exports
from unittest.mock import MagicMock

class TestEntityFqn:
    def test_direct_fqn(self):
        entity = MagicMock(spec=["fqn"])
        entity.fqn = "pkg.mod.func"
        assert _entity_fqn(entity) == "pkg.mod.func"

    def test_variable_binding_fqn(self):
        entity = MagicMock()
        entity.fqn = None
        entity.variable_binding = MagicMock(fqn="pkg.mod.var")
        assert _entity_fqn(entity) == "pkg.mod.var"

    def test_no_fqn(self):
        entity = MagicMock(spec=[])
        assert _entity_fqn(entity) is None

class TestBuildModuleExports:
    def test_with_all_exports(self):
        m1 = MagicMock(spec=PythonModule)
        m1.fqn = "pkg.mod"
        m1.all_exports = ["func", "ClassA"]
        result = _build_module_exports(MagicMock(values=lambda: [m1]))
        assert result == {"pkg.mod": {"func", "ClassA"}}

    def test_without_all_exports(self):
        m1 = MagicMock(spec=PythonModule)
        m1.fqn = "pkg.mod"
        m1.all_exports = None
        result = _build_module_exports(MagicMock(values=lambda: [m1]))
        assert result == {}

    def test_ignores_non_module(self):
        func = MagicMock()
        func.fqn = "pkg.mod.func"
        result = _build_module_exports(MagicMock(values=lambda: [func]))
        assert result == {}

class TestEntityRecord:
    def _registry(self, values):
        r = MagicMock(spec=["values"])
        r.values = lambda: values
        return r

    def test_import_alias(self):
        entity = MagicMock(spec=PythonImportAlias)
        entity.fqn = "pkg.mod.SomeName"
        entity.alias = None
        entity.name = "SomeName"

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod.SomeName"
        assert record["type"] == "IMPORT_ALIAS"
        assert record["name"] == "SomeName"

    def test_import_alias_with_alias(self):
        entity = MagicMock(spec=PythonImportAlias)
        entity.fqn = "pkg.mod.AliasName"
        entity.alias = "AliasName"
        entity.name = "OriginalName"

        record = _entity_record(entity, self._registry([]), {})
        assert record["name"] == "AliasName"

    def test_import_alias_no_fqn_returns_none(self):
        entity = MagicMock(spec=["fqn", "alias", "name"])
        entity.fqn = None

        assert _entity_record(entity, self._registry([]), {}) is None

    def test_function_like(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "my_func"
        entity.fqn = "pkg.mod.my_func"
        entity.type = "FUNCTION"

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod.my_func"
        assert record["type"] == "FUNCTION"

    def test_class(self):
        entity = MagicMock(spec=TypeDefinition)
        entity.name = "MyClass"
        entity.fqn = "pkg.mod.MyClass"
        entity.type = "CLASS"

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod.MyClass"
        assert record["type"] == "CLASS"

    def test_module(self):
        entity = MagicMock(spec=PythonModule)
        entity.fqn = "pkg.mod"
        entity.path = "pkg/mod.py"
        entity.all_exports = None

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod"
        assert record["type"] == "MODULE"
        assert record["name"] == "mod"

    def test_constant(self):
        entity = MagicMock(spec=PythonConstant)
        entity.variable_binding = MagicMock(name="MY_CONST", fqn="pkg.mod.MY_CONST")

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod.MY_CONST"
        assert record["type"] == "CONSTANT"

    def test_type_alias(self):
        entity = MagicMock(spec=PythonTypeAlias)
        entity.variable_binding = MagicMock(name="MyType", fqn="pkg.mod.MyType")

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod.MyType"
        assert record["type"] == "TYPE_ALIAS"

    def test_variable_declaration(self):
        entity = MagicMock(spec=VariableDeclaration)
        entity.variable_binding = MagicMock(name="myVar", fqn="pkg.mod.myVar")

        record = _entity_record(entity, self._registry([]), {})
        assert record["full_path"] == "pkg.mod.myVar"
        assert record["type"] == "VARIABLE"

    def test_variable_declaration_no_binding_returns_none(self):
        entity = MagicMock(spec=VariableDeclaration)
        entity.variable_binding = None

        assert _entity_record(entity, self._registry([]), {}) is None

    def test_unknown_type_returns_none(self):
        entity = MagicMock()
        assert _entity_record(entity, self._registry([]), {}) is None

    def test_exported_flag(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "ExportedClass"
        entity.fqn = "pkg.mod.ExportedClass"
        entity.type = "CLASS"

        module_exports = {"pkg.mod": {"ExportedClass"}}
        record = _entity_record(entity, self._registry([]), module_exports)
        assert record["metadata_json"] == '{"fqn": "pkg.mod.ExportedClass", "exported": true}'

    def test_lineno_in_metadata(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.mod.f"
        entity.type = "FUNCTION"
        entity.source_range = MagicMock(lineno=5, end_lineno=20)

        record = _entity_record(entity, self._registry([]), {})
        assert '"lineno": 5' in record["metadata_json"]
        assert '"end_lineno": 20' in record["metadata_json"]

    def test_language_is_python(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.mod.f"
        entity.type = "FUNCTION"

        record = _entity_record(entity, self._registry([]), {})
        assert record["language"] == "python"

    def test_parent_id_in_top_level_dict(self):
        entity = MagicMock(spec=PythonImportAlias)
        entity.fqn = "pkg.mod.SomeName"
        entity.alias = None
        entity.name = "SomeName"
        entity.parent_id = "parent123"

        record = _entity_record(entity, self._registry([]), {})
        assert record["parent_id"] == "parent123"
        meta = json.loads(record["metadata_json"])
        assert "parent_id" not in meta

    def test_parent_id_none_when_missing(self):
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.mod.f"
        entity.type = "FUNCTION"

        record = _entity_record(entity, self._registry([]), {})
        assert "parent_id" in record

    def test_control_flow_block(self):
        sr = SourceRange(lineno=10, end_lineno=20, col_offset=0, end_col_offset=8)
        cr = SourceRange(lineno=10, end_lineno=10, col_offset=3, end_col_offset=7)
        entity = ControlFlowBlock(
            id="cfb1",
            parent_id="parent1",
            branch="if",
            source_range=sr,
            condition_range=cr,
        )

        record = _entity_record(entity, {}, {})
        assert record["type"] == "CONTROL_FLOW_BLOCK"
        assert record["name"] == "if"
        assert record["parent_id"] == "parent1"
        meta = json.loads(record["metadata_json"])
        assert meta["condition_lineno"] == 10
        assert meta["condition_col_offset"] == 3

    def test_control_flow_block_no_condition(self):
        sr = SourceRange(lineno=30, end_lineno=35, col_offset=0, end_col_offset=4)
        entity = ControlFlowBlock(
            id="cfb2",
            parent_id="parent2",
            branch="else",
            source_range=sr,
            condition_range=None,
        )

        record = _entity_record(entity, {}, {})
        assert record["type"] == "CONTROL_FLOW_BLOCK"
        assert record["name"] == "else"
        meta = json.loads(record["metadata_json"])
        assert "condition_lineno" not in meta

    def test_control_flow_group(self):
        sr = SourceRange(lineno=5, end_lineno=25, col_offset=0, end_col_offset=2)
        entity = ControlFlowGroup(
            id="cfg1",
            parent_id="parent0",
            group_type="if_statement",
            source_range=sr,
        )

        record = _entity_record(entity, {}, {})
        assert record["type"] == "CONTROL_FLOW_GROUP"
        assert record["name"] == "if_statement"
        assert record["parent_id"] == "parent0"

    def test_control_flow_block_with_registry_fallback(self):
        sr = SourceRange(lineno=10, end_lineno=20, col_offset=0, end_col_offset=8)
        group_sr = SourceRange(lineno=5, end_lineno=25, col_offset=0, end_col_offset=2)
        group = ControlFlowGroup(
            id="grp1",
            parent_id=None,
            group_type="if_statement",
            source_range=group_sr,
        )
        block = ControlFlowBlock(
            id="cfb3",
            parent_id="grp1",
            branch="if",
            source_range=sr,
            condition_range=None,
        )

        registry = {"grp1": group, "cfb3": block}
        record = _entity_record(block, registry, {})
        assert "__branch__" in record["full_path"]
        assert "if" in record["full_path"]

    def test_source_id_stored_in_metadata(self):
        sr = SourceRange(lineno=5, end_lineno=5, col_offset=0, end_col_offset=10, source_id="mod123")
        module = PythonModule(id="mod123", fqn="pkg.mod", path="pkg/mod.py", source="", docstring_range=None)
        entity = MagicMock(spec=FunctionLike)
        entity.name = "f"
        entity.fqn = "pkg.mod.f"
        entity.type = "FUNCTION"
        entity.source_range = sr

        registry = {"mod123": module}
        record = _entity_record(entity, registry, {})
        meta = json.loads(record["metadata_json"])
        assert meta["source_id"] == "mod123"

    def test_control_flow_group_with_registry_fallback(self):
        sr = SourceRange(lineno=1, end_lineno=30, col_offset=0, end_col_offset=2)
        group = ControlFlowGroup(
            id="cfg2",
            parent_id=None,
            group_type="if_statement",
            source_range=sr,
        )

        registry = {"cfg2": group}
        record = _entity_record(group, registry, {})
        assert "__group__" in record["full_path"]
        assert "if_statement" in record["full_path"]
