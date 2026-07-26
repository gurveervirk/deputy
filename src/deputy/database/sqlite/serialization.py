import json
from pathlib import Path
from deproc.core.interfaces.parser.models import (
    ControlFlowBlock,
    ControlFlowGroup,
    Entity,
    FunctionLike,
    SourceRange,
    TypeDefinition,
    VariableDeclaration,
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
    "VARIABLE": VariableDeclaration,
    "MODULE": PythonModule,
    "PACKAGE": PythonPackage,
    "NAMESPACE_PACKAGE": PythonNamespacePackage,
    "CONTROL_FLOW_BLOCK": ControlFlowBlock,
    "CONTROL_FLOW_GROUP": ControlFlowGroup,
}

def _module_fqn(full_path: str) -> str | None:
    parts = full_path.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else None

def _get_module_fqn_from_registry(entity_id: str, registry: dict) -> str | None:
    seen: set[str] = set()
    current = entity_id
    while current and current not in seen:
        seen.add(current)
        entity = registry.get(current)
        if entity is None:
            break
        if hasattr(entity, "fqn") and entity.fqn:
            return entity.fqn
        current = getattr(entity, "parent_id", None)
    return None

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
    elif isinstance(entity, VariableDeclaration):
        vb = getattr(entity, "variable_binding", None)
        if not vb:
            return None
        name = vb.name
        full_path = vb.fqn or name
        entity_type = "VARIABLE"
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
    elif isinstance(entity, ControlFlowBlock):
        name = entity.branch
        entity_type = "CONTROL_FLOW_BLOCK"
        module_fqn = None
        if entity.parent_id and registry:
            module_fqn = _get_module_fqn_from_registry(entity.parent_id, registry)
        full_path = f"{module_fqn}.__branch__.{entity.branch}.{entity.source_range.lineno}" if module_fqn else f"__branch__.{entity.branch}.{entity.source_range.lineno}"
    elif isinstance(entity, ControlFlowGroup):
        name = entity.group_type
        entity_type = "CONTROL_FLOW_GROUP"
        module_fqn = None
        if entity.parent_id and registry:
            module_fqn = _get_module_fqn_from_registry(entity.parent_id, registry)
        full_path = f"{module_fqn}.__group__.{entity.group_type}.{entity.source_range.lineno}" if module_fqn else f"__group__.{entity.group_type}.{entity.source_range.lineno}"
    else:
        return None

    if not name or not full_path:
        return None

    metadata = {}
    sr = entity.source_range if hasattr(entity, "source_range") else None
    if sr:
        metadata["lineno"] = sr.lineno
        metadata["end_lineno"] = sr.end_lineno
        metadata["col_offset"] = sr.col_offset
        metadata["end_col_offset"] = sr.end_col_offset
        metadata["source_id"] = sr.source_id
    if isinstance(entity, PythonImportAlias):
        metadata["original_name"] = entity.name
        if entity.alias:
            metadata["alias"] = entity.alias
    if isinstance(entity, PythonImportStatement):
        metadata["import_type"] = entity.type
        metadata["wildcard"] = entity.wildcard
        metadata["name_ids"] = entity.name_ids
    if isinstance(entity, ControlFlowBlock):
        if entity.condition_range:
            cr = entity.condition_range
            metadata["condition_lineno"] = cr.lineno
            metadata["condition_end_lineno"] = cr.end_lineno
            metadata["condition_col_offset"] = cr.col_offset
            metadata["condition_end_col_offset"] = cr.end_col_offset
        metadata["import_stmt_ids"] = entity.import_stmt_ids
        metadata["type_ids"] = entity.type_ids
        metadata["function_ids"] = entity.function_ids
        metadata["variable_ids"] = entity.variable_ids
        metadata["nested_group_ids"] = entity.nested_group_ids
    if isinstance(entity, ControlFlowGroup):
        metadata["block_ids"] = entity.block_ids
    if hasattr(entity, "fqn") and entity.fqn:
        metadata["fqn"] = entity.fqn
    if hasattr(entity, "path"):
        metadata["path"] = entity.path
    if isinstance(entity, PythonModule) and entity.all_exports:
        metadata["all_exports"] = entity.all_exports
    if hasattr(entity, "visibility") and entity.visibility:
        metadata["visibility"] = entity.visibility
    if hasattr(entity, "docstring_range") and entity.docstring_range:
        dr = entity.docstring_range
        metadata["docstring_lineno"] = dr.lineno
        metadata["docstring_end_lineno"] = dr.end_lineno
    if isinstance(entity, FunctionLike):
        if hasattr(entity, "signature") and entity.signature:
            sig = entity.signature
            metadata["signature_lineno"] = sig.signature_range.lineno
            metadata["signature_end_lineno"] = sig.signature_range.end_lineno
            if sig.arguments_range:
                metadata["arguments_lineno"] = sig.arguments_range.lineno
                metadata["arguments_end_lineno"] = sig.arguments_range.end_lineno
            if sig.return_type_range:
                metadata["return_type_lineno"] = sig.return_type_range.lineno
                metadata["return_type_end_lineno"] = sig.return_type_range.end_lineno
        if hasattr(entity, "annotations") and entity.annotations:
            metadata["decorators"] = [a.name for a in entity.annotations]
    if isinstance(entity, TypeDefinition):
        if hasattr(entity, "inherits") and entity.inherits:
            metadata["parent_classes"] = entity.inherits
        if hasattr(entity, "method_ids") and entity.method_ids:
            metadata["method_ids"] = entity.method_ids
        if hasattr(entity, "inner_type_ids") and entity.inner_type_ids:
            metadata["inner_type_ids"] = entity.inner_type_ids
        if hasattr(entity, "property_ids") and entity.property_ids:
            metadata["property_ids"] = entity.property_ids
    if isinstance(entity, VariableDeclaration):
        if hasattr(entity, "type_annotation") and entity.type_annotation:
            ta = entity.type_annotation
            metadata["type_annotation_lineno"] = ta.lineno
            metadata["type_annotation_end_lineno"] = ta.end_lineno
        if hasattr(entity, "modifiers") and entity.modifiers:
            metadata["modifiers"] = entity.modifiers

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
        "parent_id": getattr(entity, "parent_id", None),
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
    parent_id = record.get("parent_id") or meta.get("parent_id")
    if entity_class is PythonImportAlias:
        return PythonImportAlias(
            name=meta.get("original_name", ""),
            alias=meta.get("alias"),
            parent_id=parent_id,
            source_range=sr,
            **common,
        )
    if entity_class is PythonImportStatement:
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
    if entity_class is VariableDeclaration:
        return VariableDeclaration(source_range=sr, **null_fields, **common)
    if entity_class is ControlFlowBlock:
        condition_range = None
        if "condition_lineno" in meta:
            condition_range = SourceRange(
                lineno=meta["condition_lineno"],
                end_lineno=meta["condition_end_lineno"],
                col_offset=meta["condition_col_offset"],
                end_col_offset=meta["condition_end_col_offset"],
            )
        return ControlFlowBlock(
            id=record["id"],
            parent_id=parent_id,
            branch=record["name"],
            source_range=sr,
            condition_range=condition_range,
            import_stmt_ids=meta.get("import_stmt_ids", []),
            type_ids=meta.get("type_ids", []),
            function_ids=meta.get("function_ids", []),
            variable_ids=meta.get("variable_ids", []),
            nested_group_ids=meta.get("nested_group_ids", []),
        )
    if entity_class is ControlFlowGroup:
        return ControlFlowGroup(
            id=record["id"],
            parent_id=parent_id,
            group_type=record["name"],
            source_range=sr,
            block_ids=meta.get("block_ids", []),
        )
    return None
