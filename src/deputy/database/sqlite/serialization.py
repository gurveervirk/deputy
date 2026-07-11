import json
from pathlib import Path
from deproc.core.interfaces.parser.models import (
    Entity,
    FunctionLike,
    SourceRange,
    TypeDefinition,
)
from deproc.plugins.python.parser.models import (
    PythonClass,
    PythonConstant,
    PythonFunctionLike,
    PythonImportAlias,
    PythonImportStatement,
    PythonModule,
    PythonTypeAlias,
)
from deproc.plugins.python.linker.models import (
    PythonNamespacePackage,
    PythonPackage,
)

TYPE_TO_CLASS = {
    "FUNCTION": PythonFunctionLike,
    "METHOD": PythonFunctionLike,
    "CLASS": PythonClass,
    "IMPORT_ALIAS": PythonImportAlias,
    "IMPORT_STATEMENT": PythonImportStatement,
    "CONSTANT": PythonConstant,
    "TYPE_ALIAS": PythonTypeAlias,
    "MODULE": PythonModule,
    "PACKAGE": PythonPackage,
    "NAMESPACE_PACKAGE": PythonNamespacePackage,
}

def _module_fqn(full_path: str) -> str | None:
    parts = full_path.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else None

def entity_to_record(entity, language: str = "python", module_exports: dict[str, set[str]] | None = None, registry=None) -> dict | None:
    if isinstance(entity, PythonImportAlias):
        name = entity.alias or entity.name
        if not entity.fqn:
            return None
        full_path = entity.fqn
        entity_type = "IMPORT_ALIAS"
    elif isinstance(entity, PythonImportStatement):
        name = entity.path
        entity_type = "IMPORT_STATEMENT"
        module_fqn = None
        if entity.parent_id and registry:
            parent = registry.get(entity.parent_id)
            if parent and hasattr(parent, "fqn"):
                module_fqn = parent.fqn
        full_path = f"{module_fqn}.__import__.{entity.id}" if module_fqn else entity.id
    elif isinstance(entity, (PythonConstant, PythonTypeAlias)):
        vb = getattr(entity, "variable_binding", None)
        if not vb:
            return None
        name = vb.name
        full_path = vb.fqn or name
        entity_type = "CONSTANT" if isinstance(entity, PythonConstant) else "TYPE_ALIAS"
    elif isinstance(entity, PythonModule):
        name = entity.fqn.split(".")[-1] if entity.fqn else Path(entity.path).stem
        full_path = entity.fqn or entity.path
        entity_type = "PACKAGE" if isinstance(entity, PythonPackage) else "MODULE"
    elif isinstance(entity, PythonNamespacePackage):
        name = entity.fqn.split(".")[-1] if entity.fqn else Path(entity.path).stem
        full_path = entity.fqn or entity.path
        entity_type = "NAMESPACE_PACKAGE"
    elif isinstance(entity, (FunctionLike, TypeDefinition)):
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
        metadata["col_offset"] = sr.col_offset
        metadata["end_col_offset"] = sr.end_col_offset
    if isinstance(entity, PythonImportAlias):
        metadata["original_name"] = entity.name
        if entity.alias:
            metadata["alias"] = entity.alias
        if entity.parent_id:
            metadata["parent_id"] = entity.parent_id
    if isinstance(entity, PythonImportStatement):
        metadata["import_type"] = entity.type
        metadata["wildcard"] = entity.wildcard
        metadata["name_ids"] = entity.name_ids
        if entity.parent_id:
            metadata["parent_id"] = entity.parent_id
    if hasattr(entity, "fqn") and entity.fqn:
        metadata["fqn"] = entity.fqn
    if hasattr(entity, "path"):
        metadata["path"] = entity.path
    if isinstance(entity, PythonModule) and entity.all_exports:
        metadata["all_exports"] = entity.all_exports
    if hasattr(entity, "visibility") and entity.visibility:
        metadata["visibility"] = entity.visibility

    mfqn = entity.fqn if isinstance(entity, (PythonModule, PythonNamespacePackage)) else _module_fqn(full_path)
    if mfqn and name in (module_exports or {}).get(mfqn, set()):
        metadata["exported"] = True

    return {
        "id": entity.id,
        "language": language,
        "full_path": full_path,
        "name": name,
        "type": entity_type,
        "metadata_json": json.dumps(metadata, default=str),
    }

def record_to_entity(record: dict) -> Entity | None:
    entity_class = TYPE_TO_CLASS.get(record["type"])
    if entity_class is None:
        return None
    meta = json.loads(record["metadata_json"])
    sr = SourceRange(
        lineno=meta.get("lineno", 0),
        end_lineno=meta.get("end_lineno", 0),
        col_offset=meta.get("col_offset", 0),
        end_col_offset=meta.get("end_col_offset", 0),
    )
    common = {
        "id": record["id"],
        "fqn": meta.get("fqn") or record["full_path"],
    }
    if entity_class is PythonImportAlias:
        return PythonImportAlias(
            name=meta.get("original_name", ""),
            alias=meta.get("alias"),
            parent_id=meta.get("parent_id"),
            source_range=sr,
            **common,
        )
    if entity_class is PythonImportStatement:
        parent_id = meta.get("parent_id")
        return PythonImportStatement(
            path=record["name"],
            type=meta.get("import_type", ""),
            wildcard=meta.get("wildcard", False),
            name_ids=meta.get("name_ids", []),
            parent_id=parent_id,
            source_range=sr,
            id=record["id"],
        )
    if entity_class is PythonFunctionLike:
        return PythonFunctionLike(
            name=record["name"],
            source_range=sr,
            docstring_range=None,
            signature=None,
            type=record["type"],
            annotations=[],
            visibility=meta.get("visibility"),
            **common,
        )
    if entity_class is PythonClass:
        return PythonClass(
            name=record["name"],
            source_range=sr,
            docstring_range=None,
            visibility=meta.get("visibility"),
            **common,
        )
    if entity_class is PythonModule:
        return PythonModule(
            all_exports=meta.get("all_exports"),
            path=meta.get("path", ""),
            source="",
            docstring_range=None,
            **common,
        )
    if entity_class is PythonPackage:
        return PythonPackage(
            submodule_ids=meta.get("submodule_ids", []),
            all_exports=meta.get("all_exports"),
            path=meta.get("path", ""),
            source="",
            docstring_range=None,
            **common,
        )
    if entity_class is PythonNamespacePackage:
        return PythonNamespacePackage(
            path=meta.get("path", ""),
            submodule_ids=meta.get("submodule_ids", []),
            **common,
        )
    null_fields = {"variable_binding": None, "value_range": None, "type_annotation": None, "modifiers": []}
    if entity_class is PythonConstant:
        return PythonConstant(source_range=sr, **null_fields, **common)
    if entity_class is PythonTypeAlias:
        return PythonTypeAlias(source_range=sr, **null_fields, **common)
    return None
