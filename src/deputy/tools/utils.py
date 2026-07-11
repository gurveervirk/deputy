import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from deproc.core.interfaces.parser.models import FunctionLike, TypeDefinition
from deproc.plugins.python.linker.models import PythonModule, PythonNamespacePackage, PythonPackage
from deproc.plugins.python.parser.models import (
    PythonConstant,
    PythonImportAlias,
    PythonTypeAlias,
)
from deputy.database.sqlite import open_database

_CONFIG_FILE = ".deputyconfig"
_DEFAULT_DB = ".deputy.db"

def _resolve_db_path() -> str:
    if os.path.exists(_CONFIG_FILE):
        path = Path(_CONFIG_FILE).read_text().strip()
        if path:
            return path
    if os.path.exists(_DEFAULT_DB):
        return _DEFAULT_DB
    raise FileNotFoundError(
        "No database found. Run 'deputy init' first."
    )

def _open_database() -> sqlite3.Connection:
    return open_database(_resolve_db_path())

def _build_module_exports(registry) -> dict[str, set[str]]:
    exports: dict[str, set[str]] = defaultdict(set)
    for entity in registry.values():
        if isinstance(entity, PythonModule) and hasattr(entity, "all_exports") and entity.all_exports:
            for name in entity.all_exports:
                exports[entity.fqn].add(name)
    return dict(exports)

def _entity_fqn(entity) -> str | None:
    fqn = getattr(entity, "fqn", None)
    if fqn:
        return fqn
    vb = getattr(entity, "variable_binding", None)
    if vb:
        return getattr(vb, "fqn", None)
    return None

def _entity_record(entity, registry, module_exports):
    if isinstance(entity, PythonImportAlias):
        name = entity.alias or entity.name
        if not entity.fqn:
            return None
        full_path = entity.fqn
        entity_type = "IMPORT_ALIAS"
    elif isinstance(entity, PythonConstant):
        if not hasattr(entity, "variable_binding") or not entity.variable_binding:
            return None
        name = entity.variable_binding.name
        full_path = entity.variable_binding.fqn or name
        entity_type = "CONSTANT"
    elif isinstance(entity, PythonTypeAlias):
        if not hasattr(entity, "variable_binding") or not entity.variable_binding:
            return None
        name = entity.variable_binding.name
        full_path = entity.variable_binding.fqn or name
        entity_type = "TYPE_ALIAS"
    elif isinstance(entity, PythonModule):
        name = entity.fqn.split(".")[-1] if entity.fqn else Path(entity.path).stem
        full_path = entity.fqn or entity.path
        entity_type = "PACKAGE" if isinstance(entity, PythonPackage) else "MODULE"
    elif isinstance(entity, PythonNamespacePackage):
        name = entity.fqn.split(".")[-1] if entity.fqn else Path(entity.path).stem
        full_path = entity.fqn or entity.path
        entity_type = "NAMESPACE_PACKAGE"
    elif isinstance(entity, FunctionLike):
        name = entity.name
        full_path = entity.fqn
        entity_type = entity.type
    elif isinstance(entity, TypeDefinition):
        name = entity.name
        full_path = entity.fqn
        entity_type = entity.type
    else:
        return None

    if not name or not full_path:
        return None

    metadata = {}
    if hasattr(entity, "source_range") and entity.source_range:
        sr = entity.source_range
        metadata["lineno"] = sr.lineno
        metadata["end_lineno"] = sr.end_lineno
    if isinstance(entity, PythonImportAlias):
        metadata["original_name"] = entity.name
        if entity.alias:
            metadata["alias"] = entity.alias
    entity_fqn = _entity_fqn(entity)
    if entity_fqn:
        metadata["fqn"] = entity_fqn
    if hasattr(entity, "path"):
        metadata["path"] = entity.path
    if isinstance(entity, PythonModule) and hasattr(entity, "all_exports") and entity.all_exports:
        metadata["all_exports"] = entity.all_exports
    if hasattr(entity, "visibility") and entity.visibility:
        metadata["visibility"] = entity.visibility

    module_fqn = None
    if isinstance(entity, (PythonModule, PythonNamespacePackage)) and entity.fqn:
        module_fqn = entity.fqn
    elif entity_fqn:
        parts = entity_fqn.split(".")
        if len(parts) > 1:
            module_fqn = ".".join(parts[:-1])

    if module_fqn and name in module_exports.get(module_fqn, set()):
        metadata["exported"] = True

    return {
        "id": entity.id,
        "language": "python",
        "full_path": full_path,
        "name": name,
        "type": entity_type,
        "metadata_json": json.dumps(metadata),
    }
