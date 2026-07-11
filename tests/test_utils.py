from deproc.core.interfaces.parser.models import FunctionLike, TypeDefinition
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
