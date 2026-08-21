import json
from unittest.mock import MagicMock

from deproc.core.interfaces.parser.models import (
    Annotation,
    ControlFlowBlock,
    ControlFlowGroup,
    FunctionLike,
    Signature,
    SourceRange,
    TypeDefinition,
    VariableDeclaration,
)
from deproc.plugins.python.linker.models import PythonModule
from deproc.plugins.python.parser.models import (
    PythonClass,
    PythonConstant,
    PythonFunctionLike,
    PythonImportAlias,
    PythonTypeAlias,
)

from deputy.tools.utils import _entity_record


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
        assert record["type"] == "PYTHON_MODULE"
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
        entity.signature = None

        module_exports = {"pkg.mod": {"ExportedClass"}}
        record = _entity_record(entity, self._registry([]), module_exports)
        assert (
            record["metadata_json"]
            == '{"fqn": "pkg.mod.ExportedClass", "exported": true}'
        )

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
        sr = SourceRange(
            lineno=5, end_lineno=5, col_offset=0, end_col_offset=10, source_id="mod123"
        )
        module = PythonModule(
            id="mod123",
            fqn="pkg.mod",
            path="pkg/mod.py",
            source="",
            docstring_range=None,
        )
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


class TestEntityRecordMetadata:
    sr = SourceRange(lineno=5, end_lineno=10, col_offset=0, end_col_offset=8)

    def test_docstring_range_stored(self):
        dr = SourceRange(lineno=6, end_lineno=8, col_offset=4, end_col_offset=12)
        entity = PythonFunctionLike(
            id="f1",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=dr,
            signature=None,
            annotations=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["docstring_lineno"] == 6
        assert meta["docstring_end_lineno"] == 8

    def test_signature_ranges_stored(self):
        sig = Signature(
            signature_range=SourceRange(
                lineno=5, end_lineno=5, col_offset=0, end_col_offset=30
            ),
            arguments_range=SourceRange(
                lineno=5, end_lineno=5, col_offset=15, end_col_offset=28
            ),
            return_type_range=SourceRange(
                lineno=5, end_lineno=5, col_offset=32, end_col_offset=38
            ),
        )
        entity = PythonFunctionLike(
            id="f2",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=None,
            signature=sig,
            annotations=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["signature_lineno"] == 5
        assert meta["signature_end_lineno"] == 5
        assert meta["arguments_lineno"] == 5
        assert meta["arguments_end_lineno"] == 5
        assert meta["return_type_lineno"] == 5
        assert meta["return_type_end_lineno"] == 5

    def test_signature_partial_ranges(self):
        sig = Signature(
            signature_range=SourceRange(
                lineno=5, end_lineno=5, col_offset=0, end_col_offset=30
            ),
            arguments_range=None,
            return_type_range=None,
        )
        entity = PythonFunctionLike(
            id="f3",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=None,
            signature=sig,
            annotations=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["signature_lineno"] == 5
        assert "arguments_lineno" not in meta
        assert "return_type_lineno" not in meta

    def test_decorators_stored(self):
        ann = Annotation(
            source_range=SourceRange(
                lineno=4, end_lineno=4, col_offset=0, end_col_offset=5
            ),
            name="staticmethod",
        )
        entity = PythonFunctionLike(
            id="f4",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=None,
            signature=None,
            annotations=[ann],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["decorators"] == ["staticmethod"]

    def test_no_decorators_when_empty(self):
        entity = PythonFunctionLike(
            id="f5",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=None,
            signature=None,
            annotations=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert "decorators" not in meta

    def test_parent_classes_stored(self):
        entity = PythonClass(
            id="c1",
            name="MyClass",
            fqn="mod.MyClass",
            type="CLASS",
            source_range=self.sr,
            docstring_range=None,
            annotations=[],
            inherits=["mod.Base1", "mod.Base2"],
            method_ids=[],
            inner_type_ids=[],
            property_ids=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["parent_classes"] == ["mod.Base1", "mod.Base2"]

    def test_type_definition_ids_stored(self):
        entity = PythonClass(
            id="c2",
            name="MyClass",
            fqn="mod.MyClass",
            type="CLASS",
            source_range=self.sr,
            docstring_range=None,
            annotations=[],
            inherits=[],
            method_ids=["m1", "m2"],
            inner_type_ids=["t1"],
            property_ids=["p1"],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["method_ids"] == ["m1", "m2"]
        assert meta["inner_type_ids"] == ["t1"]
        assert meta["property_ids"] == ["p1"]

    def test_no_docstring_when_none(self):
        entity = PythonFunctionLike(
            id="f6",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=None,
            signature=None,
            annotations=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert "docstring_lineno" not in meta
        assert "docstring_end_lineno" not in meta

    def test_no_signature_when_none(self):
        entity = PythonFunctionLike(
            id="f7",
            name="my_func",
            fqn="mod.my_func",
            type="FUNCTION",
            source_range=self.sr,
            docstring_range=None,
            signature=None,
            annotations=[],
            visibility="public",
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert "signature_lineno" not in meta
        assert "arguments_lineno" not in meta
        assert "return_type_lineno" not in meta

    def test_variable_type_annotation_stored(self):
        ta = SourceRange(lineno=10, end_lineno=10, col_offset=20, end_col_offset=30)
        entity = VariableDeclaration(
            id="v1",
            source_range=self.sr,
            variable_binding=MagicMock(name="var", fqn="mod.var"),
            value_range=None,
            type_annotation=ta,
            modifiers=["export"],
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["type_annotation_lineno"] == 10
        assert meta["type_annotation_end_lineno"] == 10

    def test_variable_modifiers_stored(self):
        entity = VariableDeclaration(
            id="v2",
            source_range=self.sr,
            variable_binding=MagicMock(name="var", fqn="mod.var"),
            value_range=None,
            type_annotation=None,
            modifiers=["export", "global"],
        )
        record = _entity_record(entity, {}, {})
        meta = json.loads(record["metadata_json"])
        assert meta["modifiers"] == ["export", "global"]
